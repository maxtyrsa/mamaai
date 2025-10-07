import re
import json
import random
import logging
from typing import Dict, Optional, List
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
        if not text:
            return ""
            
        text = text.strip()
        
        if (text.startswith('"') and text.endswith('"')) or (text.startswith("'") and text.endswith("'")):
            text = text[1:-1].strip()
        
        artifacts = [
            r'^ответ[:\-\s]*',
            r'^реплика[:\-\s]*',
            r'^комментарий[:\-\s]*',
            r'^бот[:\-\s]*',
            r'^assistant[:\-\s]*',
        ]
        
        for artifact in artifacts:
            text = re.sub(artifact, '', text, flags=re.IGNORECASE).strip()
        
        text = re.sub(r'^[,\-–—\s\.\!:]+', '', text).strip()
        
        return text
    
    async def generate_motivational_message(self, message_type: str) -> str:
        if message_type == "morning":
            prompt = """Создай оригинальное вдохновляющее утреннее сообщение для Telegram-канала. 

Требования:
- Начни сразу с содержания, без вводных слов
- Не используй шаблонные фразы типа "Доброе утро, дорогие подписчики"
- Будь креативным и уникальным
- Используй современный язык
- Добавь мотивацию на день
- Длина: 150-250 символов
- Используй уместные эмодзи

Пример хорошего начала:
"Новый день - новые возможности! 🌞"

Создай сообщение:"""
            default = "☀️ Просыпайтесь с улыбкой! Сегодняшний день полон возможностей для роста и достижений. Пусть каждое ваше действие приближает к мечте! 💫"
        else:
            prompt = """Создай оригинальное тёплое вечернее сообщение для Telegram-канала.

Требования:
- Начни сразу с содержания, без вводных слов  
- Не используй шаблонные фразы типа "Спокойной ночи, дорогие подписчики"
- Будь креативным и уютным
- Используй расслабляющий тон
- Длина: 150-250 символов
- Используй уместные эмодзи

Пример хорошего начала:
"Вечер - время подводить итоги... 🌙"

Создай сообщение:"""
            default = "🌙 Вечер - время отдохнуть и восстановить силы. Пусть ваш сон будет крепким, а завтрашний день принесёт радостные сюрпризы! 💤"
        
        try:
            output = self.llm(
                prompt,
                max_tokens=120,
                temperature=0.8,
                top_p=0.9
            )
            text = self.clean_generated_text(output["choices"][0]["text"])
            return text or default
        except Exception as e:
            logger.error(f"❌ Ошибка генерации {message_type} сообщения: {e}")
            return default
    
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
            
            # Убираем кавычки если они есть
            if text.startswith('"') and text.endswith('"'):
                text = text[1:-1].strip()
            
            # Дополнительная очистка
            text = self.clean_generated_text(text)
            text = self.clean_post_text(text)
            
            # Если текст слишком короткий, генерируем заново
            if len(text) < 20:
                logger.warning("⚠️ Сгенерирован слишком короткий пост, пробуем снова")
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

    async def generate_content_plan(self, plan_type: str, niche: str, tone: str, posts_per_week: int = 7) -> Dict:
        """Генерация контент-плана"""
        
        if plan_type == "weekly":
            duration = "неделю"
            total_posts = posts_per_week
        else:
            duration = "месяц"
            total_posts = posts_per_week * 4
        
        prompt = f"""Создай контент-план для Telegram-канала на {duration}. 

Ниша канала: {niche}
Тон контента: {tone}
Количество постов: {total_posts}

Создай разнообразный контент-план, который включает:
1. Образовательные посты
2. Развлекательный контент
3. Вовлекающие вопросы
4. Новости и обновления
5. Мотивационные посты

Верни ответ в формате JSON со следующими полями для каждого поста:
- day (для недели) или date (для месяца)
- topic (тема поста)
- main_idea (основная идея)
- post_type (тип поста)
- tone (тон)

Создай контент-план:"""
        
        try:
            output = self.llm(
                prompt,
                max_tokens=2000,
                temperature=0.7,
                top_p=0.9
            )
            
            text = output["choices"][0]["text"].strip()
            
            # Пытаемся извлечь JSON из ответа
            json_match = re.search(r'\{.*\}', text, re.DOTALL)
            if json_match:
                plan_data = json.loads(json_match.group())
                return plan_data
            else:
                return await self.create_fallback_plan(plan_type, niche, tone, total_posts)
                
        except Exception as e:
            logger.error(f"❌ Ошибка генерации контент-плана: {e}")
            return await self.create_fallback_plan(plan_type, niche, tone, total_posts)
    
    async def create_fallback_plan(self, plan_type: str, niche: str, tone: str, total_posts: int) -> Dict:
        """Создание резервного контент-плана"""
        
        post_types = ["Образовательный", "Развлекательный", "Вовлекающий", "Новостной", "Мотивационный"]
        
        plan = {"plan": []}
        
        if plan_type == "weekly":
            days = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
            for i, day in enumerate(days[:total_posts]):
                plan["plan"].append({
                    "day": day,
                    "topic": f"{niche} - {post_types[i % len(post_types)]}",
                    "main_idea": f"Интересная информация о {niche} для {day.lower()}",
                    "post_type": post_types[i % len(post_types)],
                    "tone": tone
                })
        else:
            start_date = datetime.now()
            for i in range(total_posts):
                post_date = start_date + timedelta(days=i)
                plan["plan"].append({
                    "date": post_date.strftime("%d.%m.%Y"),
                    "topic": f"{niche} - {post_types[i % len(post_types)]}",
                    "main_idea": f"Ежедневная порция полезной информации о {niche}",
                    "post_type": post_types[i % len(post_types)],
                    "tone": tone
                })
        
        return plan
