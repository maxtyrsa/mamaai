import re
import json
import random
import logging
from typing import Dict, Optional, List, Tuple
from datetime import datetime, timedelta
from config import Config

logger = logging.getLogger(__name__)

class AdvancedCache:
    def __init__(self, db):
        self.db = db
        self.memory_cache = {}
        self.hit_count = 0
        self.miss_count = 0
    
    def get(self, key: str) -> Optional[str]:
        if key in self.memory_cache:
            self.hit_count += 1
            return self.memory_cache[key]
        
        cursor = self.db.conn.cursor()
        cursor.execute(
            'SELECT response_text FROM response_cache WHERE message_hash = ? AND datetime(created_at) > datetime("now", "-1 day")',
            (key,)
        )
        result = cursor.fetchone()
        
        if result:
            self.memory_cache[key] = result[0]
            self.hit_count += 1
            return result[0]
        
        self.miss_count += 1
        return None
    
    def set(self, key: str, value: str):
        self.memory_cache[key] = value
        cursor = self.db.conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO response_cache 
            (message_hash, response_text, created_at, usage_count)
            VALUES (?, ?, datetime("now"), COALESCE((SELECT usage_count FROM response_cache WHERE message_hash = ?), 0) + 1)
        ''', (key, value, key))
        self.db.conn.commit()
    
    def get_stats(self) -> Dict:
        total = self.hit_count + self.miss_count
        return {
            'hit_rate': self.hit_count / total if total > 0 else 0,
            'memory_size': len(self.memory_cache),
            'total_hits': self.hit_count,
            'total_misses': self.miss_count
        }

class ResponseGenerator:
    def __init__(self, llm, cache, db):
        self.llm = llm
        self.cache = cache
        self.db = db
    
    async def generate_context_aware_reply(self, comment: str, user_id: int, username: str) -> str:
        logger.info(f"🤖 Генерация ответа для {username}")
        
        # Получаем контекст предыдущих сообщений пользователя
        context = await self._get_user_context(user_id)
        
        prompt = f"""Ты — дружелюбный AI-ассистент в Telegram-канале. Пользователь оставил комментарий - ответь на него естественно и уместно.

КОММЕНТАРИЙ ПОЛЬЗОВАТЕЛЯ:
"{comment}"

ИНФОРМАЦИЯ О ПОЛЬЗОВАТЕЛЕ:
Имя: {username}
Контекст: {context if context else "Нет предыдущих сообщений"}

ТВОЙ ОТВЕТ ДОЛЖЕН БЫТЬ:
- Кратким (1-2 предложения)
- Естественным и дружелюбным
- Уместным по содержанию
- Обращаться к пользователю по имени
- Без шаблонных фраз типа "Спасибо за сообщение"

ОТВЕТ:"""
        
        # Кэширование
        cache_key = f"reply_{user_id}_{hash(comment[:100])}"
        cached = self.cache.get(cache_key)
        if cached:
            logger.info(f"💾 Использован кэшированный ответ для {username}")
            return cached
        
        try:
            output = self.llm(
                prompt,
                max_tokens=100,
                temperature=0.8,
                stop=["\n\n", "---", "###"]
            )
            
            text = output["choices"][0]["text"].strip()
            text = self.clean_generated_text(text)
            
            if not text or len(text) < 3:
                logger.warning(f"⚠️ Сгенерирован слишком короткий ответ: '{text}'")
                text = await self._generate_fallback_reply(username, comment)
            elif len(text) > Config.MAX_REPLY_LENGTH:
                text = text[:Config.MAX_REPLY_LENGTH - 3] + "..."
            
            self.cache.set(cache_key, text)
            logger.info(f"💬 Ответ для {username}: {text[:50]}...")
            return text
            
        except Exception as e:
            logger.error(f"❌ Ошибка генерации ответа для {username}: {e}")
            return await self._generate_fallback_reply(username, comment)
    
    async def _generate_fallback_reply(self, username: str, comment: str) -> str:
        fallbacks = [
            f"Интересная мысль, {username}! 💫",
            f"{username}, спасибо за участие в обсуждении! 👏",
            f"Хороший вопрос, {username}! 🤔",
            f"{username}, благодарю за комментарий! 🌟",
            f"Замечательно, {username}! 😊",
            f"{username}, ценю ваше мнение! 🙏",
            f"Спасибо, {username}, что поделились! 💖"
        ]
        return random.choice(fallbacks)
    
    async def _get_user_context(self, user_id: int) -> str:
        cursor = self.db.conn.cursor()
        cursor.execute('''
            SELECT message_text FROM message_history 
            WHERE user_id = ? AND is_spam = FALSE 
            ORDER BY datetime(timestamp) DESC LIMIT 3
        ''', (user_id,))
        
        messages = [row[0] for row in cursor.fetchall()]
        return " | ".join(messages[-2:]) if len(messages) > 1 else ""
    
    def clean_generated_text(self, text: str) -> str:
        """Улучшенная очистка сгенерированного текста"""
        if not text:
            return ""
            
        text = text.strip()
        
        # Удаляем кавычки если они есть
        if (text.startswith('"') and text.endswith('"')) or (text.startswith("'") and text.endswith("'")):
            text = text[1:-1].strip()
        
        # Удаляем распространенные артефакты промпта (более полный список)
        artifacts = [
            r'^ответ[:\-\s]*',
            r'^реплика[:\-\s]*', 
            r'^комментарий[:\-\s]*',
            r'^бот[:\-\s]*',
            r'^assistant[:\-\s]*',
            r'^ai[:\-\s]*',
            r'^сообщение[:\-\s]*',
            r'^пост[:\-\s]*',
            r'^текст[:\-\s]*',
            r'^создан[:\-\s]*',
            r'^генерирован[:\-\s]*',
            r'^вот[:\-\s]*',
            r'^пример[:\-\s]*',
            r'^промпт[:\-\s]*',
            r'^пользователь[:\-\s]*',
            r'^канал[:\-\s]*',
            r'^telegram[:\-\s]*',
        ]
        
        for artifact in artifacts:
            text = re.sub(artifact, '', text, flags=re.IGNORECASE).strip()
        
        # Удаляем лишние знаки препинания в начале
        text = re.sub(r'^[,\-–—\s\.\!:]+', '', text).strip()
        
        return text

    def is_quality_text(self, text: str, min_length: int = 20) -> bool:
        """Улучшенная проверка качества сгенерированного текста"""
        if not text or len(text.strip()) < min_length:
            return False
        
        # Проверяем, что текст не состоит в основном из артефактов
        artifact_indicators = [
            'ответ:', 'реплика:', 'комментарий:', 'бот:', 'assistant:', 
            'сообщение:', 'пост:', 'текст:', 'создан', 'генерирован',
            'нейросеть', 'ии', 'ai:'
        ]
        
        text_lower = text.lower()
        artifact_count = sum(1 for artifact in artifact_indicators if artifact in text_lower)
        
        # Если слишком много артефактов, считаем текст некачественным
        if artifact_count > 1:
            return False
        
        # Проверяем, что текст содержит осмысленные слова (не только эмодзи и пунктуацию)
        words = re.findall(r'\b[а-яёa-z]{3,}\b', text_lower)
        if len(words) < 3:  # Минимум 3 осмысленных слова
            return False
        
        # Проверяем, что текст не состоит только из fallback-фраз
        fallback_phrases = [
            'просыпайтесь с улыбкой',
            'сегодняшний день полон возможностей',
            'вечер - время отдохнуть',
            'пусть ваш сон будет крепким',
            'новый день - новые возможности',
            'спокойной ночи',
            'доброе утро'
        ]
        
        if any(phrase in text_lower for phrase in fallback_phrases):
            # Это fallback-текст, считаем его качественным для авто-постов
            return True
        
        return True

    def clean_motivational_text(self, text: str) -> str:
        """Специализированная очистка для мотивационных сообщений"""
        if not text:
            return ""
        
        original_text = text
        text = text.strip()
        
        # Удаляем кавычки
        if (text.startswith('"') and text.endswith('"')) or (text.startswith("'") and text.endswith("'")):
            text = text[1:-1].strip()
        
        # Удаляем подчеркивания и другие артефакты
        text = re.sub(r'^_+\s*', '', text)  # Удаляем подчеркивания в начале
        text = re.sub(r'_+\s*$', '', text)  # Удаляем подчеркивания в конце
        text = re.sub(r'\s*_{2,}\s*', ' ', text)  # Заменяем множественные подчеркивания пробелом
        
        # Удаляем специфичные для мотивационных постов артефакты
        motivational_artifacts = [
            r'^сообщение[:\-\s]*',
            r'^утреннее[:\-\s]*',
            r'^вечернее[:\-\s]*', 
            r'^мотивационное[:\-\s]*',
            r'^вдохновляющее[:\-\s]*',
            r'^пост[:\-\s]*',
            r'^текст[:\-\s]*',
            r'^пример[:\-\s]*',
            r'^сгенерирован[:\-\s]*',
            r'^создан[:\-\s]*',
            r'^бот[:\-\s]*',
            r'^ai[:\-\s]*',
            r'^assistant[:\-\s]*',
            r'^нейросеть[:\-\s]*',
            # Удаляем возможные нумерации
            r'^\d+[\.\)]\s*',
            r'^[\-\*•]\s*',
            # Удаляем возможные метки времени или идентификаторы
            r'\[\d+\]\s*',
            r'\(\d+\)\s*',
        ]
        
        for artifact in motivational_artifacts:
            text = re.sub(artifact, '', text, flags=re.IGNORECASE).strip()
        
        # Удаляем возможные остатки промпта (многострочные)
        prompt_remnants = [
            r'требования.*',
            r'пример.*',
            r'длина.*', 
            r'используй.*',
            r'будь.*',
            r'не используй.*',
            r'начни сразу.*',
            r'создай.*',
            r'содержание.*',
            r'эмодзи.*',
            r'телеграм.*',
            r'канал.*',
        ]
        
        for remnant in prompt_remnants:
            text = re.sub(remnant, '', text, flags=re.IGNORECASE | re.DOTALL)
        
        # Удаляем лишние знаки препинания в начале и конце
        text = re.sub(r'^[,\-–—\s\.\!:\n_]+', '', text)
        text = re.sub(r'[,\-–—\s\.\!:\n_]+$', '', text)
        
        # Удаляем двойные пробелы и лишние переносы
        text = re.sub(r'\s+', ' ', text)
        text = re.sub(r'\n\s*\n', '\n', text)
        
        # Проверяем, не состоит ли текст только из артефактов
        clean_text = re.sub(r'[_\-–—\s\.\!:,]', '', text)
        if len(clean_text.strip()) < 10:  # Если после удаления знаков осталось мало символов
            logger.warning(f"⚠️ Текст состоит в основном из артефактов: '{original_text[:50]}...'")
            return ""  # Возвращаем пустую строку для использования fallback
        
        return text.strip()

    async def generate_motivational_message(self, message_type: str) -> str:
        """Генерация мотивационных сообщений с улучшенным промптом"""
        
        if message_type == "morning":
            prompt = """Создай оригинальное вдохновляющее утреннее сообщение для Telegram-канала.

ВАЖНЫЕ ПРАВИЛА:
1. НАЧИНАЙ НЕМЕДЛЕННО С ТЕКСТА СООБЩЕНИЯ
2. НЕ используй вводные фразы типа "Доброе утро", "Вот сообщение", "Создал пост"
3. НЕ упоминай что это сообщение, пост или результат генерации
4. Будь креативным и уникальным
5. Используй современный естественный язык
6. Добавь мотивацию на день
7. Длина: 150-250 символов
8. Используй уместные эмодзи (1-2 штуки)

Пример ХОРОШЕГО сообщения:
"Новый день - это чистый лист! 🌞 Какие истории напишешь сегодня? Пусть каждая минута будет наполнена смыслом и радостью! 💫"

Пример ПЛОХОГО сообщения:
"Сообщение: Доброе утро, дорогие подписчики! Вот создал для вас мотивационный пост..."

ТВОЙ УТРЕННИЙ ПОСТ:"""
        
        else:
            prompt = """Создай оригинальное тёплое вечернее сообщение для Telegram-канала.

ВАЖНЫЕ ПРАВИЛА:
1. НАЧИНАЙ НЕМЕДЛЕННО С ТЕКСТА СООБЩЕНИЯ  
2. НЕ используй вводные фразы типа "Спокойной ночи", "Вот вечерний пост", "Создал сообщение"
3. НЕ упоминай что это сообщение, пост или результат генерации
4. Будь креативным и уютным
5. Используй расслабляющий тон
6. Длина: 150-250 символов
7. Используй уместные эмодзи (1-2 штуки)

Пример ХОРОШЕГО сообщения:
"Вечер накрывает город уютным покрывалом... 🌙 Самое время отдохнуть, перезагрузиться и помечтать о завтрашних свершениях! 💤"

Пример ПЛОХОГО сообщения:
"Вечернее сообщение: Спокойной ночи, друзья! Вот текст для вас..."

ТВОЙ ВЕЧЕРНИЙ ПОСТ:"""
        
        try:
            output = self.llm(
                prompt,
                max_tokens=200,
                temperature=0.85,
                top_p=0.9,
                stop=["Пример", "Правила:", "Сообщение:", "Пост:"]
            )
            text = output["choices"][0]["text"].strip()
            
            logger.info(f"🤖 Сырой сгенерированный текст ({message_type}): {text[:100]}...")
            
            # Специальная очистка для мотивационных сообщений
            text = self.clean_motivational_text(text)
            
            # Улучшенная проверка качества
            if not text or len(text.strip()) < 20:
                logger.warning(f"⚠️ Сгенерирован слишком короткий {message_type} текст после очистки, используем fallback")
                return await self._get_fallback_post(message_type)
            
            # Проверяем, что текст содержит осмысленные слова
            words = re.findall(r'\b[а-яё]{3,}\b', text.lower())
            if len(words) < 3:  # Минимум 3 осмысленных слова
                logger.warning(f"⚠️ Сгенерирован нечитаемый {message_type} текст (мало слов), используем fallback")
                return await self._get_fallback_post(message_type)
            
            # Проверяем наличие артефактов
            if any(artifact in text.lower() for artifact in ['сообщение', 'пост', 'бот', 'сгенерирован', 'создан']):
                logger.warning(f"⚠️ Сгенерированный {message_type} текст содержит артефакты, используем fallback")
                return await self._get_fallback_post(message_type)
            
            # Дополнительная тематическая проверка
            if message_type == "evening":
                evening_indicators = ['вечер', 'ночь', 'сон', 'отдых', 'спокойной', 'закат', 'луна', 'звезд', 'расслаб', 'восстанов']
                if not any(indicator in text.lower() for indicator in evening_indicators):
                    logger.warning(f"⚠️ Вечерний пост не содержит вечерней тематики, используем fallback")
                    return await self._get_fallback_post(message_type)
            else:
                morning_indicators = ['утро', 'утрен', 'день', 'новый', 'просыпай', 'солнц', 'рассвет', 'начало']
                if not any(indicator in text.lower() for indicator in morning_indicators):
                    logger.warning(f"⚠️ Утренний пост не содержит утренней тематики, используем fallback")
                    return await self._get_fallback_post(message_type)
            
            logger.info(f"✅ Сгенерирован качественный {message_type} пост: {text[:80]}...")
            return text
            
        except Exception as e:
            logger.error(f"❌ Ошибка генерации {message_type} сообщения: {e}")
            return await self._get_fallback_post(message_type)

    async def _get_fallback_post(self, post_type: str) -> str:
        """Улучшенные резервные посты на случай ошибки генерации"""
        fallbacks = {
            "morning": [
                "☀️ Новое утро — новые возможности! Пусть сегодняшний день будет наполнен яркими моментами и продуктивными свершениями! 🌟",
                "🌞 Доброе утро! Откройте глаза навстречу новым возможностям. Сегодня — идеальный день для больших и маленьких побед! 💫",
                "✨ Просыпайтесь с улыбкой! Сегодняшний день приготовил для вас множество приятных сюрпризов и возможностей для роста! 🚀",
                "🌅 Утро — время планировать великие дела! Наполните этот день смыслом, радостью и движением к вашим целям! 💪"
            ],
            "evening": [
                "🌙 Вечер наступает, принося с собой умиротворение... Отдохните, восстановите силы и приготовьтесь к новым свершениям завтра! 💤",
                "✨ День подходит к концу... Благодарите за все уроки и достижения. Завтра — новый шанс стать еще лучше! 🌟",
                "🌜 Спокойной ночи! Пусть ваши сны будут светлыми, а утро принесет свежие силы и вдохновение для новых побед! 💫",
                "🌃 Вечер — время подвести итоги и отпустить переживания. Отдыхайте, завтра вас ждут новые возможности! 💖"
            ]
        }
        
        import random
        return random.choice(fallbacks.get(post_type, ["Отличного дня! 🌟"]))

    async def generate_post(self, topic: str, tone: str, main_idea: str, use_emojis: bool, length: str) -> str:
        """Генерация поста по заданным параметрам"""
        
        length_map = {
            "short": "50-100 символов, очень кратко",
            "medium": "200-300 символов, стандартный пост",
            "long": "400-600 символов, развернутый пост"
        }
        
        tone_map = {
            "serious": "серьёзный, официальный",
            "friendly": "дружелюбный, неформальный", 
            "funny": "юмористический, с шутками",
            "inspirational": "вдохновляющий, мотивационный",
            "professional": "профессиональный, деловой"
        }
        
        emoji_instruction = "Используй уместные смайлики и эмодзи." if use_emojis else "Не используй смайлики и эмодзи."
        
        prompt = f"""Создай пост для Telegram-канала на тему: "{topic}"

Основная мысль: {main_idea}
Тон: {tone_map.get(tone, tone)}
Длина: {length_map.get(length, length)}
{emoji_instruction}

ВАЖНЫЕ ПРАВИЛА:
- Начинай сразу с содержания поста
- Не упоминай что это пост, сообщение или результат генерации
- Не используй вводные фразы типа "Вот пост", "Создал контент", "Генерирую текст"
- Не повторяй условия задания в тексте
- Будь естественным и органичным
- Не дублируй слова и фразы

Текст поста:"""
        
        try:
            output = self.llm(
                prompt,
                max_tokens=400 if length == "long" else 250,
                temperature=0.8,
                top_p=0.9
            )
            
            text = output["choices"][0]["text"].strip()
            
            # Улучшенная очистка текста
            text = self.clean_post_text(text)
            
            # Если текст слишком короткий, генерируем заново
            if len(text) < 20:
                logger.warning("⚠️ Сгенерирован слишком короткий пост, пробуем снова")
                return await self.generate_post_fallback(topic, tone, main_idea, use_emojis, length)
            
            # Проверка качества текста
            if not self.is_quality_text(text):
                logger.warning("⚠️ Сгенерирован некачественный пост, используем fallback")
                return await self.generate_post_fallback(topic, tone, main_idea, use_emojis, length)
            
            # Добавляем форматирование для длинных постов
            if len(text) > 100 and length != "short":
                sentences = re.split(r'[.!?]+', text)
                if len(sentences) > 2:
                    # Объединяем предложения с переносами
                    cleaned_sentences = [s.strip() for s in sentences if s.strip()]
                    if len(cleaned_sentences) > 2:
                        text = '.\n\n'.join(cleaned_sentences) + ('.' if not text.endswith('.') else '')
            
            return text or "Не удалось сгенерировать пост. Попробуйте другие параметры."
            
        except Exception as e:
            logger.error(f"❌ Ошибка генерации поста: {e}")
            return await self.generate_post_fallback(topic, tone, main_idea, use_emojis, length)

    async def generate_post_fallback(self, topic: str, tone: str, main_idea: str, use_emojis: bool, length: str) -> str:
        """Резервная генерация поста"""
        emojis = "🌟✨😊📱💫🎯💡" if use_emojis else ""
        
        base_text = f"{topic}. {main_idea}"
        
        if length == "short":
            return f"{base_text}{emojis}"
        elif length == "medium":
            return f"{base_text}\n\nУзнайте больше в нашем канале!{emojis}"
        else:
            return f"{base_text}\n\nПодробнее расскажем в следующих публикациях. Оставайтесь на связи!{emojis}"

    def clean_post_text(self, text: str) -> str:
        """Дополнительная очистка текста постов"""
        if not text:
            return ""
        
        # Удаляем кавычки в начале и конце
        text = text.strip()
        if (text.startswith('"') and text.endswith('"')) or (text.startswith("'") and text.endswith("'")):
            text = text[1:-1].strip()
        
        # Удаляем номерные списки в начале
        text = re.sub(r'^\d+[\.\)]\s*', '', text)
        
        # Удаляем маркированные списки в начале
        text = re.sub(r'^[\-\*•]\s*', '', text)
        
        # Удаляем распространенные префиксы (только в начале текста)
        prefixes = [
            r'^пост\s*[:\-]?\s*',
            r'^текст\s*[:\-]?\s*', 
            r'^сообщение\s*[:\-]?\s*',
            r'^контент\s*[:\-]?\s*',
            r'^запись\s*[:\-]?\s*',
            r'^результат\s*[:\-]?\s*',
            r'^вот\s*[:\-]?\s*',
            r'^сгенерирован\w*\s*[:\-]?\s*',
            r'^ответ\s*[:\-]?\s*',
            r'^бот\s*[:\-]?\s*',
            r'^ии\s*[:\-]?\s*',
            r'^нейросеть\s*[:\-]?\s*',
        ]
        
        for prefix in prefixes:
            text = re.sub(prefix, '', text, flags=re.IGNORECASE)
        
        # Удаляем повторяющиеся слова (обрабатываем основные случаи)
        words = text.split()
        if len(words) > 1:
            cleaned_words = []
            for i, word in enumerate(words):
                # Пропускаем слова, которые идут подряд и одинаковые
                if i > 0 and word.lower() == words[i-1].lower():
                    continue
                cleaned_words.append(word)
            text = ' '.join(cleaned_words)
        
        # Удаляем артефакты промпта
        artifacts = [
            r'создай\s+пост\s*[:\-]?\s*',
            r'начни\s+сразу\s+с\s+содержания\s*[:\-]?\s*',
            r'не\s+используй\s+фразы\s*[:\-]?\s*',
            r'требования\s+к\s+посту\s*[:\-]?\s*',
            r'пост\s+должен\s+быть\s*[:\-]?\s*',
        ]
        
        for artifact in artifacts:
            text = re.sub(artifact, '', text, flags=re.IGNORECASE)
        
        # Удаляем лишние пробелы и знаки препинания в начале
        text = re.sub(r'^[,\-–—\s\.\!:]+', '', text).strip()
        
        # Удаляем лишние переносы строк
        text = re.sub(r'\n\s*\n\s*\n+', '\n\n', text)
        
        return text

    async def generate_content_plan(self, plan_type: str, niche: str, tone: str, posts_per_week: int = 7, audience: str = "подписчики Telegram-канала", goals: str = "вовлечение и рост аудитории") -> Dict:
        """Генерация контент-плана с учетом особенностей Telegram"""
        
        if plan_type == "weekly":
            duration = "неделю"
            total_posts = min(posts_per_week, 7)
        else:
            duration = "месяц"
            total_posts = min(posts_per_week * 4, 28)
        
        # Форматы контента для Telegram
        content_formats = [
            "📝 Текстовый пост (лонгрид, мнение, аналитика)",
            "📊 Опрос или голосование",
            "📰 Новости и дайджесты", 
            "💬 Обсуждение и дискуссия",
            "📈 Обзор или кейс",
            "❓ Вопрос к аудитории",
            "🎯 Полезные материалы (чек-листы, инструкции)",
            "🎭 Развлекательный контент",
            "🚀 Мотивационный пост",
            "🤝 Советы и рекомендации"
        ]
        
        prompt = f"""Создай контент-план для Telegram-канала на {duration}. 

КЛЮЧЕВЫЕ ПРИНЦИПЫ РАБОТЫ:
1. Учитывай особенности Telegram: длинные посты, форматирование, хештеги, взаимодействие с аудиторией
2. Генерируй контент, который провоцирует обсуждение и вовлеченность  
3. Используй разнообразные форматы контента

ОСНОВНЫЕ ПАРАМЕТРЫ:
- **Тематика**: {niche}
- **Тон коммуникации**: {tone}
- **Период**: {duration}
- **Целевая аудитория**: {audience}
- **Цели**: {goals}
- **Количество постов**: {total_posts}

ФОРМАТЫ КОНТЕНТА ДЛЯ TELEGRAM:
{chr(10).join(f"{i+1}. {format}" for i, format in enumerate(content_formats))}

ТРЕБОВАНИЯ К КОНТЕНТ-ПЛАНУ:
- Создай разнообразный контент, чередуя форматы
- Включай вовлекающие элементы (вопросы, опросы, обсуждения)
- Предлагай темы, которые провоцируют комментарии
- Используй актуальные и интересные темы для целевой аудитории
- Добавляй рекомендации по хештегам и форматированию

СТРУКТУРА КАЖДОГО ПОСТА:
- day (для недели) или date (для месяца)
- topic: конкретная тема поста (емкая и привлекательная)
- main_idea: основная идея и ключевые моменты
- post_type: тип контента из списка форматов
- tone: тон из параметров
- engagement_elements: элементы вовлечения (вопросы, опросы и т.д.)
- hashtags: рекомендуемые хештеги (3-5 штук)
- format_tips: советы по форматированию для Telegram

Верни ответ в формате JSON с полем "plan", содержащим массив постов."""

        try:
            output = self.llm(
                prompt,
                max_tokens=3000,
                temperature=0.8,
                top_p=0.9
            )
            
            text = output["choices"][0]["text"].strip()
            
            # Пытаемся извлечь JSON из ответа
            json_match = re.search(r'\{.*\}', text, re.DOTALL)
            if json_match:
                try:
                    plan_data = json.loads(json_match.group())
                    
                    # Валидация и дополнение данных
                    if 'plan' in plan_data and isinstance(plan_data['plan'], list):
                        for post in plan_data['plan']:
                            # Добавляем обязательные поля если их нет
                            if 'engagement_elements' not in post:
                                post['engagement_elements'] = "Вопрос к аудитории в конце поста"
                            if 'hashtags' not in post:
                                post['hashtags'] = f"#{niche.replace(' ', '')} #контент #обсуждение"
                            if 'format_tips' not in post:
                                post['format_tips'] = "Используй абзацы и эмодзи для лучшей читаемости"
                    
                    logger.info(f"✅ Сгенерирован контент-план с {len(plan_data.get('plan', []))} постами")
                    return plan_data
                    
                except json.JSONDecodeError as e:
                    logger.error(f"❌ Ошибка парсинга JSON контент-плана: {e}")
                    return await self.create_enhanced_fallback_plan(plan_type, niche, tone, total_posts, audience, goals)
            else:
                logger.warning("⚠️ Не найден JSON в ответе ИИ, используем fallback")
                return await self.create_enhanced_fallback_plan(plan_type, niche, tone, total_posts, audience, goals)
                
        except Exception as e:
            logger.error(f"❌ Ошибка генерации контент-плана: {e}")
            return await self.create_enhanced_fallback_plan(plan_type, niche, tone, total_posts, audience, goals)

    async def create_enhanced_fallback_plan(self, plan_type: str, niche: str, tone: str, total_posts: int, audience: str, goals: str) -> Dict:
        """Создание улучшенного резервного контент-плана"""
        
        # Разнообразные типы постов для Telegram
        post_types = [
            {
                "type": "📝 Текстовый пост",
                "engagement": "Вопрос к аудитории в конце",
                "hashtags": f"#{niche.replace(' ', '')} #обсуждение #мнение",
                "format": "Используй абзацы и эмодзи"
            },
            {
                "type": "📊 Опрос", 
                "engagement": "Голосование с вариантами ответов",
                "hashtags": f"#{niche.replace(' ', '')} #опрос #голосование",
                "format": "Четкие варианты ответов"
            },
            {
                "type": "📰 Новости",
                "engagement": "Просим мнение в комментариях",
                "hashtags": f"#{niche.replace(' ', '')} #новости #актуальное", 
                "format": "Кратко и по делу"
            },
            {
                "type": "💬 Дискуссия",
                "engagement": "Открытый вопрос для обсуждения",
                "hashtags": f"#{niche.replace(' ', '')} #дискуссия #мнения",
                "format": "Провокационный заголовок"
            },
            {
                "type": "🎯 Полезные материалы",
                "engagement": "Просим поделиться опытом",
                "hashtags": f"#{niche.replace(' ', '')} #полезное #советы",
                "format": "Структурированный список"
            },
            {
                "type": "❓ Вопрос к аудитории",
                "engagement": "Прямой вопрос для ответов",
                "hashtags": f"#{niche.replace(' ', '')} #вопрос #ответы",
                "format": "Ясная формулировка вопроса"
            },
            {
                "type": "🚀 Мотивационный пост", 
                "engagement": "Призыв к действию",
                "hashtags": f"#{niche.replace(' ', '')} #мотивация #развитие",
                "format": "Вдохновляющий тон"
            }
        ]
        
        plan = {"plan": []}
        
        if plan_type == "weekly":
            days = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
            for i, day in enumerate(days[:total_posts]):
                post_type = post_types[i % len(post_types)]
                plan["plan"].append({
                    "day": day,
                    "topic": f"{niche} - {post_type['type']}",
                    "main_idea": f"Интересный контент о {niche} для {audience}. {goals}",
                    "post_type": post_type['type'],
                    "tone": tone,
                    "engagement_elements": post_type['engagement'],
                    "hashtags": post_type['hashtags'],
                    "format_tips": post_type['format']
                })
        else:
            start_date = datetime.now()
            for i in range(total_posts):
                post_date = start_date + timedelta(days=i)
                post_type = post_types[i % len(post_types)]
                plan["plan"].append({
                    "date": post_date.strftime("%d.%m.%Y"),
                    "topic": f"{niche} - {post_type['type']}",
                    "main_idea": f"Ежедневная порция качественного контента о {niche}",
                    "post_type": post_type['type'],
                    "tone": tone,
                    "engagement_elements": post_type['engagement'],
                    "hashtags": post_type['hashtags'],
                    "format_tips": post_type['format']
                })
        
        logger.info(f"✅ Создан fallback контент-план с {len(plan['plan'])} постами")
        return plan

    async def generate_post_from_plan_data(self, post_data: Dict) -> str:
        """Генерация поста на основе данных из контент-плана"""
        try:
            topic = post_data.get('topic', 'Без темы')
            tone = post_data.get('tone', 'friendly')
            main_idea = post_data.get('main_idea', 'Интересный контент')
            post_type = post_data.get('post_type', 'Текстовый пост')
            
            prompt = f"""Создай пост для Telegram-канала на основе контент-плана.

ТЕМА: {topic}
ТИП ПОСТА: {post_type}
ТОН: {tone}
ОСНОВНАЯ ИДЕЯ: {main_idea}

ДОПОЛНИТЕЛЬНАЯ ИНФОРМАЦИЯ:
- Элементы вовлечения: {post_data.get('engagement_elements', 'Вопрос к аудитории')}
- Хештеги: {post_data.get('hashtags', '#контент #обсуждение')}
- Советы по форматированию: {post_data.get('format_tips', 'Используй абзацы и эмодзи')}

ВАЖНЫЕ ПРАВИЛА:
- Начинай сразу с содержания поста
- Не упоминай что это пост из контент-плана
- Используй естественный и органичный язык
- Учитывай указанный тон и тип поста
- Включи элементы вовлечения
- Добавь рекомендуемые хештеги в конце

ТЕКСТ ПОСТА:"""
            
            output = self.llm(
                prompt,
                max_tokens=350,
                temperature=0.8,
                top_p=0.9
            )
            
            text = output["choices"][0]["text"].strip()
            text = self.clean_post_text(text)
            
            # Добавляем хештеги если их нет
            if 'hashtags' in post_data and not any(hashtag in text for hashtag in post_data['hashtags'].split()):
                text += f"\n\n{post_data['hashtags']}"
            
            return text or f"Пост на тему: {topic}"
            
        except Exception as e:
            logger.error(f"❌ Ошибка генерации поста из контент-плана: {e}")
            return f"Пост на тему: {post_data.get('topic', 'Без темы')}\n\n{post_data.get('main_idea', 'Интересный контент')}"