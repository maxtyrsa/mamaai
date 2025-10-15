import re
import logging
import asyncio
from typing import Tuple, List, Dict
from datetime import datetime, timedelta
from collections import defaultdict
import json
from config import Config

logger = logging.getLogger(__name__)

class AdvancedSpamDetector:
    def __init__(self, db):
        self.db = db
        self.user_behavior = defaultdict(lambda: {
            'message_count': 0,
            'spam_count': 0,
            'last_activity': None,
            'trust_score': 50,  # 0-100, где 100 - полностью доверенный
            'warning_count': 0
        })
        self._load_spam_patterns()
        self._load_whitelist()
    
    def _load_spam_patterns(self):
        """Загрузка шаблонов спама с весами"""
        self.spam_patterns = [
            # Коммерческие предложения
            (r'(купить|продам|продажа|покупка|заказ|цена|стоимость|оплата|доставка)', 2),
            (r'(рубл|₽|р\.|руб|цена|стоим|цена)', 1.5),
            
            # Финансовые схемы
            (r'(казин|ставк|покер|рулетк|букмекер|тотализатор)', 3),
            (r'(кредит|заём|деньги в долг|микрозайм|ипотек)', 2.5),
            (r'(инвест|акци|акция|дивиденд|трейд)', 2),
            # В _load_spam_patterns добавить:
(r'(форекс|forex|трейдинг|трейдер|биржа|актив|инвест)', 2.0),
(r'(бесплатн|деньг|денег|заработок|прибыль|доход)', 1.5),
            
            # Реклама и продвижение
            (r'(подписывайся|подпишись|канал|группа|паблик|telegram)', 2),
            (r'(бесплатно|даром|акция|скидка|распродаж|промокод)', 1.5),
            (r'(реклам|продвижен|раскрутк)', 2),
            
            # Мошенничество
            (r'(выигр|приз|розыгрыш|лотере|подарок)', 2),
            (r'(гаранти|100%|результат|быстро)', 1.5),
            (r'(секс|знакомств|встреч|интим)', 3),
            
            # Ссылки и контакты
            (r'(http|https|t\.me|@[\w]+|www\.|\.[a-z]{2,})', 2),
            (r'([0-9]{10,}|телефон|номер|звон)', 1.5),
            
            # Подозрительные символы
            (r'([💵💰🤑📈👇❤️🔥⭐✨🎁🎉])', 1),
            (r'(\!{3,}|\?{3,})', 0.5),
            (r'([A-Z]{5,})', 1),  # КАПС ЛОК
        ]
        
        # Фразы, которые почти всегда спам
        self.hard_spam_phrases = [
            'заработок без вложений',
            'быстрые деньги',
            'работа на дому',
            'стабильный доход',
            'пассивный заработок',
            'инвестируй и богатей',
            'проверенный способ',
            'секретная методика',
            'только сегодня',
            'успей получить'
        ]
    
    def _load_whitelist(self):
        """Загрузка белого списка безопасных фраз"""
        self.whitelist_phrases = [
            'спасибо', 'благодарю', 'привет', 'здравствуйте',
            'интересно', 'полезно', 'понравилось', 'класс',
            'вопрос', 'помогите', 'подскажите', 'объясните',
            'уточнение', 'дополнение', 'согласен', 'не согласен'
        ]
    
    def calculate_text_metrics(self, text: str) -> Dict:
        """Расчет метрик текста"""
        text_lower = text.lower()
        
        metrics = {
            'length': len(text),
            'word_count': len(text.split()),
            'avg_word_length': sum(len(word) for word in text.split()) / max(1, len(text.split())),
            'caps_ratio': sum(1 for c in text if c.isupper()) / max(1, len(text)),
            'special_chars_ratio': sum(1 for c in text if not c.isalnum() and not c.isspace()) / max(1, len(text)),
            'digit_ratio': sum(1 for c in text if c.isdigit()) / max(1, len(text)),
            'emoji_count': sum(1 for c in text if c in '💵💰🤑📈👇❤️🔥⭐✨🎁🎉'),
            'repetition_score': self._calculate_repetition_score(text),
            'suspicious_words': 0
        }
        
        return metrics
    
    def _calculate_repetition_score(self, text: str) -> float:
        """Расчет оценки повторяемости текста"""
        words = text.lower().split()
        if len(words) < 3:
            return 0
        
        word_freq = defaultdict(int)
        for word in words:
            word_freq[word] += 1
        
        # Оценка на основе максимальной частоты слова
        max_freq = max(word_freq.values()) if word_freq else 0
        return min(1.0, max_freq / len(words) * 3)
    
    def pattern_based_analysis(self, text: str) -> float:
        """Анализ на основе шаблонов"""
        text_lower = text.lower()
        spam_score = 0
        
        # Проверка жестких спам-фраз
        for phrase in self.hard_spam_phrases:
            if phrase in text_lower:
                spam_score += 5
        
        # Проверка по регулярным выражениям
        for pattern, weight in self.spam_patterns:
            if re.search(pattern, text_lower, re.IGNORECASE):
                spam_score += weight
        
        # Проверка белого списка
        for phrase in self.whitelist_phrases:
            if phrase in text_lower:
                spam_score -= 1
        
        return max(0, spam_score)
    
    def behavioral_analysis(self, user_id: int, text: str) -> float:
        """Поведенческий анализ пользователя"""
        user_data = self.user_behavior[user_id]
        current_time = datetime.now()
        
        # Инициализация при первом сообщении
        if not user_data['last_activity']:
            user_data['last_activity'] = current_time
            user_data['message_count'] = 1
            return 0
        
        # Расчет временных интервалов
        time_diff = (current_time - user_data['last_activity']).total_seconds()
        user_data['last_activity'] = current_time
        user_data['message_count'] += 1
        
        # Штраф за слишком частые сообщения
        if time_diff < 10:  # Меньше 10 секунд между сообщениями
            return 2
        elif time_diff < 30:  # Меньше 30 секунд
            return 1
        
        # Бонус за нормальное поведение
        if user_data['trust_score'] > 70:
            return -1
        
        return 0
    
    def update_user_trust_score(self, user_id: int, is_spam: bool):
        """Обновление доверия к пользователю"""
        user_data = self.user_behavior[user_id]
        
        if is_spam:
            user_data['spam_count'] += 1
            user_data['trust_score'] = max(0, user_data['trust_score'] - 20)
            user_data['warning_count'] += 1
        else:
            # Постепенное восстановление доверия
            user_data['trust_score'] = min(100, user_data['trust_score'] + 1)
    
    def get_user_trust_level(self, user_id: int) -> str:
        """Получение уровня доверия пользователя"""
        score = self.user_behavior[user_id]['trust_score']
        if score >= 80:
            return "trusted"
        elif score >= 50:
            return "neutral"
        elif score >= 20:
            return "suspicious"
        else:
            return "banned"

class RateLimiter:
    def __init__(self, db):
        self.db = db
        self.user_limits = defaultdict(list)
    
    async def check_limit(self, user_id: int) -> bool:
        """Проверка лимита сообщений"""
        now = datetime.now()
        minute_ago = now - timedelta(minutes=1)
        
        # Очистка старых записей
        self.user_limits[user_id] = [
            timestamp for timestamp in self.user_limits[user_id] 
            if timestamp > minute_ago
        ]
        
        # Проверка лимита
        return len(self.user_limits[user_id]) < Config.USER_RATE_LIMIT
    
    async def record_message(self, user_id: int, message: str):
        """Запись сообщения пользователя"""
        self.user_limits[user_id].append(datetime.now())
        
        # Сохранение в базу
        cursor = self.db.execute_with_datetime('''
            INSERT INTO message_history (user_id, message_text, timestamp, is_spam)
            VALUES (?, ?, ?, ?)
        ''', (user_id, message, datetime.now(), False))
        self.db.conn.commit()

class AdvancedModeration:
    def __init__(self, llm, db):
        self.llm = llm
        self.db = db
        self.spam_detector = AdvancedSpamDetector(db)
        self.rate_limiter = RateLimiter(db)
        
        # Статистика модерации
        self.stats = {
            'total_checked': 0,
            'spam_detected': 0,
            'false_positives': 0,
            'ai_checks': 0
        }
    
    async def advanced_spam_check(self, text: str, user_id: int) -> Tuple[bool, float]:
        """Расширенная проверка на спам"""
        self.stats['total_checked'] += 1
        
        # Быстрая проверка на пустые сообщения
        if not text or len(text.strip()) < 2:
            return False, 0
        
        # Шаг 1: Анализ метрик текста
        metrics = self.spam_detector.calculate_text_metrics(text)
        
        # Шаг 2: Проверка по шаблонам
        pattern_score = self.spam_detector.pattern_based_analysis(text)
        
        # Шаг 3: Поведенческий анализ
        behavior_score = self.spam_detector.behavioral_analysis(user_id, text)
        
        # Шаг 4: Комбинированная оценка
        total_score = (
            pattern_score * 0.6 +
            behavior_score * 0.3 +
            self._calculate_metrics_score(metrics) * 0.1
        )
        
        logger.info(f"🛡️ Анализ спама для пользователя {user_id}: "
                   f"pattern={pattern_score:.1f}, behavior={behavior_score:.1f}, "
                   f"total={total_score:.1f}")
        
        # Определение порогов
        user_trust = self.spam_detector.get_user_trust_level(user_id)
        thresholds = {
            'trusted': 4.0,
            'neutral': 3.0,
            'suspicious': 2.0,
            'banned': 1.0
        }
        
        threshold = thresholds.get(user_trust, 3.0)
        is_spam = total_score >= threshold
        
        # Если результат пограничный, используем AI проверку
        if 2.0 <= total_score <= 4.0:
            self.stats['ai_checks'] += 1
            ai_result = await self.ai_spam_check(text)
            if ai_result:
                is_spam = True
                total_score = max(total_score, 4.1)
        
        # Обновление статистики пользователя
        self.spam_detector.update_user_trust_score(user_id, is_spam)
        
        if is_spam:
            self.stats['spam_detected'] += 1
        
        return is_spam, total_score
    
    def _calculate_metrics_score(self, metrics: Dict) -> float:
        """Расчет оценки на основе метрик текста"""
        score = 0
        
        # Слишком короткое сообщение
        if metrics['length'] < 10:
            score += 1
        
        # Слишком много заглавных букв
        if metrics['caps_ratio'] > 0.5:
            score += 2
        
        # Слишком много специальных символов
        if metrics['special_chars_ratio'] > 0.3:
            score += 1
        
        # Слишком много цифр
        if metrics['digit_ratio'] > 0.2:
            score += 1
        
        # Слишком много эмодзи
        if metrics['emoji_count'] > 3:
            score += 1
        
        # Высокий уровень повторений
        if metrics['repetition_score'] > 0.5:
            score += 2
        
        return score
    
    async def ai_spam_check(self, text: str) -> bool:
        """AI проверка сомнительных сообщений"""
        prompt = f"""Проанализируй сообщение и определи, является ли оно спамом. Учитывай контекст Telegram-канала.

СООБЩЕНИЕ: "{text[:500]}"

КРИТЕРИИ СПАМА:
✅ НОРМАЛЬНОЕ СООБЩЕНИЕ:
- Вопросы, обсуждения, мнения
- Благодарности, отзывы
- Конструктивная критика
- Запросы помощи или информации
- Естественная беседа

❌ СПАМ:
- Коммерческие предложения (продажи, услуги)
- Реклама каналов, сайтов, ботов
- Финансовые мошенничества
- Массовые рассылки
- Бессмысленный текст
- Навязчивые призывы к действию

Проанализируй намерение и содержание сообщения. Если сомневаешься, считай сообщение нормальным.

ВЕРДИКТ (только одно слово): СПАМ или НОРМА"""
        
        try:
            output = self.llm(
                prompt,
                max_tokens=10,
                temperature=0.1,
                stop=["\n", "."]
            )
            answer = output["choices"][0]["text"].strip().upper()
            
            logger.info(f"🤖 AI модерация: {answer} для текста: {text[:50]}...")
            
            return "СПАМ" in answer
            
        except Exception as e:
            logger.error(f"❌ Ошибка AI модерации: {e}")
            return False
    
    async def check_limit(self, user_id: int) -> bool:
        """Проверка лимита сообщений"""
        return await self.rate_limiter.check_limit(user_id)
    
    async def record_message(self, user_id: int, message: str):
        """Запись сообщения пользователя"""
        await self.rate_limiter.record_message(user_id, message)
    
    def get_moderation_stats(self) -> Dict:
        """Получение статистики модерации"""
        return self.stats.copy()
    
    def get_user_stats(self, user_id: int) -> Dict:
        """Получение статистики пользователя"""
        user_data = self.spam_detector.user_behavior[user_id]
        return {
            'message_count': user_data['message_count'],
            'spam_count': user_data['spam_count'],
            'trust_score': user_data['trust_score'],
            'warning_count': user_data['warning_count'],
            'trust_level': self.spam_detector.get_user_trust_level(user_id)
        }