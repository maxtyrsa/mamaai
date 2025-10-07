import re
import logging
from typing import Tuple, List
from datetime import datetime, timedelta
from config import Config

logger = logging.getLogger(__name__)

class RateLimiter:
    def __init__(self, db):
        self.db = db
    
    async def check_limit(self, user_id: int) -> bool:
        now = datetime.now()
        minute_ago = now - timedelta(minutes=1)
        
        cursor = self.db.conn.cursor()
        cursor.execute('''
            SELECT COUNT(*) FROM message_history 
            WHERE user_id = ? AND datetime(timestamp) > datetime(?)
        ''', (user_id, minute_ago.isoformat()))
        
        count = cursor.fetchone()[0]
        return count < Config.USER_RATE_LIMIT
    
    async def record_message(self, user_id: int, message: str):
        cursor = self.db.execute_with_datetime('''
            INSERT INTO message_history (user_id, message_text, timestamp)
            VALUES (?, ?, ?)
        ''', (user_id, message, datetime.now()))
        self.db.conn.commit()

class AdvancedModeration:
    def __init__(self, llm, db):
        self.llm = llm
        self.db = db
        self.spam_patterns = self._load_spam_patterns()
    
    def _load_spam_patterns(self) -> List[Tuple[str, int]]:
        return [
            (r'(http|https|t\.me|@[\w]+)', 2),
            (r'(купить|продам|заказать|цена|стоимость)', 1),
            (r'(казино|ставк|покер|рулетк)', 3),
            (r'(кредит|заём|деньги в долг)', 2),
            (r'(подписывайся|подпишись|канал|группа)', 2),
            (r'(бесплатно|даром|акция|скидка)', 1),
            (r'([💵💰🤑📈👇❤️])', 1),
            (r'(\!{3,})', 1),
        ]
    
    def calculate_spam_score(self, text: str) -> float:
        score = 0
        text_lower = text.lower()
        
        for pattern, weight in self.spam_patterns:
            if re.search(pattern, text_lower, re.IGNORECASE):
                score += weight
        
        if len(text) > 500:
            score += 1
        
        return score
    
    async def advanced_spam_check(self, text: str, user_id: int) -> Tuple[bool, float]:
        spam_score = self.calculate_spam_score(text)
        
        if spam_score >= 5:
            logger.info(f"🛡️ Высокий спам-скор: {spam_score}")
            return True, spam_score
        
        if spam_score >= 3:
            ai_check = await self.ai_spam_check(text)
            return ai_check, spam_score
        
        return False, spam_score
    
    async def ai_spam_check(self, text: str) -> bool:
        prompt = f"""Проанализируй сообщение на предмет спама. Учитывай контекст Telegram-канала.

Сообщение: "{text[:400]}"

Критерии спама:
- Коммерческие предложения (продажи, покупки, услуги)
- Реклама каналов, сайтов, ботов
- Финансовые мошенничества (кредиты, инвестиции)
- Нежелательная массовая рассылка
- Сообщения с большим количеством ссылок или упоминаний

Если сообщение выглядит как нормальный комментарий, обсуждение или вопрос - это НЕ спам.

Твой вердикт (только одно слово): ДА (спам) или НЕТ (не спам)"""
        
        try:
            output = self.llm(
                prompt,
                max_tokens=10,
                temperature=0.1,
                stop=["\n", "."]
            )
            answer = output["choices"][0]["text"].strip().upper()
            logger.info(f"🤖 AI модерация: {answer} для текста: {text[:50]}...")
            return "ДА" in answer or "SPAM" in answer
        except Exception as e:
            logger.error(f"❌ Ошибка AI модерации: {e}")
            return self.calculate_spam_score(text) >= 4
