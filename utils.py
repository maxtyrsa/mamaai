import sys
import os
import logging
import asyncio
from datetime import datetime
from telegram.error import Forbidden, NetworkError, TimedOut, BadRequest, RetryAfter
from config import CHANNEL_ID, LOG_PATH

# === КРАСИВАЯ СИСТЕМА ЛОГГИРОВАНИЯ ===
class BeautifulFormatter(logging.Formatter):
    """Красивый форматтер с эмодзи"""
    
    COLORS = {
        'DEBUG': '\033[36m',      # Cyan
        'INFO': '\033[32m',       # Green
        'WARNING': '\033[33m',    # Yellow
        'ERROR': '\033[31m',      # Red
        'CRITICAL': '\033[41m'    # Red background
    }
    RESET = '\033[0m'
    
    def format(self, record):
        # Пропускаем HTTP логи и DeprecationWarning
        message = record.getMessage()
        if any(ignore in message for ignore in ['HTTP Request', 'DeprecationWarning']):
            return ""
        
        color = self.COLORS.get(record.levelname, '')
        time_str = self.formatTime(record, "%H:%M:%S")
        
        # Эмодзи для разных типов сообщений
        emoji = self._get_emoji(record)
        
        # Для ошибок показываем краткую информацию
        if record.exc_info and record.levelname in ['ERROR', 'CRITICAL']:
            exc_type, exc_value, _ = record.exc_info
            short_error = f"{exc_type.__name__}: {str(exc_value)[:100]}"
            return f"{color}{emoji} [{time_str}] {message} | {short_error}{self.RESET}"
        
        return f"{color}{emoji} [{time_str}] {message}{self.RESET}"
    
    def _get_emoji(self, record):
        """Определяет эмодзи по содержимому сообщения"""
        message = record.getMessage().lower()
        
        if record.levelname == 'ERROR':
            return '❌'
        elif record.levelname == 'WARNING':
            return '⚠️'
        
        if any(word in message for word in ['🚀', 'запуск', 'старт']):
            return '🚀'
        elif any(word in message for word in ['✅', 'успех', 'готов', 'успешно']):
            return '✅'
        elif any(word in message for word in ['🤖', 'ии', 'генерац', 'модель']):
            return '🤖'
        elif any(word in message for word in ['💬', 'сообщен', 'комментар', 'ответ']):
            return '💬'
        elif any(word in message for word in ['🛡️', 'спам', 'модерац']):
            return '🛡️'
        elif any(word in message for word in ['📢', 'пост', 'публикац']):
            return '📢'
        elif any(word in message for word in ['❌', 'ошибка', 'error']):
            return '❌'
        elif any(word in message for word in ['⚠️', 'предупрежден', 'warning']):
            return '⚠️'
        elif any(word in message for word in ['🔔', 'callback', 'кнопк']):
            return '🔔'
        elif any(word in message for word in ['⌨️', 'команда', 'command']):
            return '⌨️'
        elif any(word in message for word in ['💾', 'база', 'database']):
            return '💾'
        elif any(word in message for word in ['⏰', 'планировщ', 'scheduler', 'следующ']):
            return '⏰'
        elif any(word in message for word in ['📊', 'статистик']):
            return '📊'
        elif any(word in message for word in ['🌅', 'утрен']):
            return '🌅'
        elif any(word in message for word in ['🌙', 'вечер']):
            return '🌙'
        elif any(word in message for word in ['🛑', 'остановк', 'stop']):
            return '🛑'
        elif any(word in message for word in ['📱', 'бот', 'работа']):
            return '📱'
        elif any(word in message for word in ['📝', 'создан', 'create']):
            return '📝'
        elif any(word in message for word in ['📅', 'план', 'content']):
            return '📅'
        elif any(word in message for word in ['🎭', 'тон', 'tone']):
            return '🎭'
        elif any(word in message for word in ['💡', 'идея', 'idea']):
            return '💡'
        
        return '📝'

def setup_logging():
    """Настройка красивого логирования"""
    
    # Отключаем ненужные логи
    logging.getLogger("telegram").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    logging.getLogger("telegram.request").setLevel(logging.WARNING)
    logging.getLogger("telegram.bot").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    
    # Основной логгер
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    
    # Удаляем старые обработчики
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
    
    # Консольный обработчик с красивым форматированием
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(BeautifulFormatter())
    
    # Файловый обработчик (полные логи для отладки)
    file_handler = logging.FileHandler(LOG_PATH, encoding='utf-8')
    file_handler.setFormatter(logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    ))
    file_handler.setLevel(logging.WARNING)
    
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    
    return logger

# === ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ДЛЯ РАБОТЫ С КАНАЛОМ ===
async def check_bot_permissions(app):
    """Проверка прав бота в канале"""
    try:
        chat = await app.bot.get_chat(CHANNEL_ID)
        logging.info(f"✅ Канал найден: {chat.title}")
        
        # Получаем информацию о боте в канале
        bot_info = await app.bot.get_me()
        try:
            member = await app.bot.get_chat_member(CHANNEL_ID, bot_info.id)
            if member.status in ['administrator', 'creator']:
                logging.info("✅ Бот является администратором канала")
                return True
            else:
                logging.warning("⚠️ Бот не является администратором канала")
                return False
        except Exception as e:
            logging.error(f"❌ Бот не является участником канала: {e}")
            return False
            
    except Exception as e:
        logging.error(f"❌ Ошибка доступа к каналу: {e}")
        return False

async def send_message_with_fallback(app, chat_id: str, text: str, max_retries: int = 3) -> bool:
    """Отправка сообщения с обработкой ошибок и повторными попытками"""
    for attempt in range(max_retries):
        try:
            await app.bot.send_message(chat_id, text)
            return True
        except Forbidden as e:
            logging.error(f"❌ Ошибка доступа (попытка {attempt + 1}): {e}")
            if "bot is not a member" in str(e):
                # Критическая ошибка - бот не в канале
                raise e
            await asyncio.sleep(2)
        except (NetworkError, TimedOut) as e:
            logging.warning(f"⚠️ Сетевая ошибка (попытка {attempt + 1}): {e}")
            await asyncio.sleep(2)
        except RetryAfter as e:
            # Telegram просит подождать
            wait_time = e.retry_after
            logging.warning(f"⏰ Rate limit, ждем {wait_time} секунд (попытка {attempt + 1})")
            await asyncio.sleep(wait_time)
        except BadRequest as e:
            logging.error(f"❌ Неверный запрос (попытка {attempt + 1}): {e}")
            # Для BadRequest обычно нет смысла повторять
            return False
        except Exception as e:
            logging.error(f"❌ Неизвестная ошибка (попытка {attempt + 1}): {e}")
            await asyncio.sleep(2)
    
    return False

async def send_message_safe(app, chat_id: str, text: str, **kwargs) -> bool:
    """Безопасная отправка сообщения с расширенными параметрами"""
    try:
        await app.bot.send_message(chat_id, text, **kwargs)
        return True
    except Forbidden as e:
        if "bot was blocked" in str(e).lower():
            logging.warning(f"⚠️ Бот заблокирован пользователем/каналом {chat_id}")
        else:
            logging.error(f"❌ Ошибка доступа при отправке сообщения: {e}")
        return False
    except Exception as e:
        logging.error(f"❌ Ошибка отправки сообщения: {e}")
        return False

async def edit_message_safe(app, chat_id: str, message_id: int, text: str, **kwargs) -> bool:
    """Безопасное редактирование сообщения"""
    try:
        await app.bot.edit_message_text(text, chat_id, message_id, **kwargs)
        return True
    except Exception as e:
        logging.error(f"❌ Ошибка редактирования сообщения: {e}")
        return False

async def delete_message_safe(app, chat_id: str, message_id: int) -> bool:
    """Безопасное удаление сообщения"""
    try:
        await app.bot.delete_message(chat_id, message_id)
        return True
    except Exception as e:
        logging.error(f"❌ Ошибка удаления сообщения: {e}")
        return False

# === ФУНКЦИИ ДЛЯ РАБОТЫ С ВРЕМЕНЕМ ===
def format_timedelta(delta) -> str:
    """Форматирование временного интервала в читаемый вид"""
    total_seconds = int(delta.total_seconds())
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    
    if hours > 0:
        return f"{hours}ч {minutes}м"
    elif minutes > 0:
        return f"{minutes}м {seconds}с"
    else:
        return f"{seconds}с"

def get_next_post_time(post_time, current_time=None):
    """Получение времени следующего поста"""
    if current_time is None:
        current_time = datetime.now()
    
    target_time = datetime.combine(current_time.date(), post_time)
    
    # Если время уже прошло сегодня, планируем на завтра
    if current_time >= target_time:
        target_time = datetime.combine(current_time.date(), post_time) + timedelta(days=1)
    
    return target_time

def is_time_close(target_time, tolerance_minutes=10):
    """Проверка, близко ли текущее время к целевому"""
    current_time = datetime.now()
    time_diff = abs((target_time - current_time).total_seconds())
    return time_diff <= tolerance_minutes * 60

# === ФУНКЦИИ ДЛЯ РАБОТЫ С ТЕКСТОМ ===
def truncate_text(text: str, max_length: int, suffix: str = "...") -> str:
    """Обрезка текста до максимальной длины"""
    if len(text) <= max_length:
        return text
    return text[:max_length - len(suffix)] + suffix

def escape_markdown(text: str) -> str:
    """Экранирование символов Markdown"""
    escape_chars = r'\_*[]()~`>#+-=|{}.!'
    return ''.join(['\\' + char if char in escape_chars else char for char in text])

def clean_username(username: str) -> str:
    """Очистка имени пользователя"""
    if not username:
        return "анонимный пользователь"
    
    # Удаляем @ в начале если есть
    if username.startswith('@'):
        username = username[1:]
    
    # Заменяем недопустимые символы
    username = re.sub(r'[^\w]', '_', username)
    
    return username if username else "пользователь"

# === ФУНКЦИИ ДЛЯ РАБОТЫ С БАЗОЙ ДАННЫХ ===
def get_today_date() -> str:
    """Получение сегодняшней даты в формате для БД"""
    return datetime.now().date().isoformat()

def get_current_datetime() -> datetime:
    """Получение текущего datetime"""
    return datetime.now()

def datetime_from_isoformat(iso_string: str) -> datetime:
    """Безопасное создание datetime из ISO строки"""
    try:
        return datetime.fromisoformat(iso_string)
    except (ValueError, TypeError):
        return datetime.now()

# === ФУНКЦИИ ДЛЯ СТАТИСТИКИ ===
async def get_bot_usage_stats(db) -> dict:
    """Получение статистики использования бота"""
    cursor = db.conn.cursor()
    
    try:
        # Общее количество сообщений
        cursor.execute('SELECT COUNT(*) FROM message_history')
        total_messages = cursor.fetchone()[0]
        
        # Сообщения за сегодня
        cursor.execute('SELECT COUNT(*) FROM message_history WHERE date(timestamp) = date("now")')
        today_messages = cursor.fetchone()[0]
        
        # Заблокированный спам
        cursor.execute('SELECT COUNT(*) FROM message_history WHERE is_spam = TRUE')
        spam_blocked = cursor.fetchone()[0]
        
        # Уникальные пользователи
        cursor.execute('SELECT COUNT(DISTINCT user_id) FROM user_activity')
        unique_users = cursor.fetchone()[0]
        
        # Активные пользователи (за последние 7 дней)
        cursor.execute('''
            SELECT COUNT(DISTINCT user_id) FROM user_activity 
            WHERE date(last_activity) >= date("now", "-7 days")
        ''')
        active_users = cursor.fetchone()[0]
        
        return {
            'total_messages': total_messages,
            'today_messages': today_messages,
            'spam_blocked': spam_blocked,
            'unique_users': unique_users,
            'active_users': active_users
        }
        
    except Exception as e:
        logging.error(f"❌ Ошибка получения статистики: {e}")
        return {}

async def get_channel_stats(app, channel_id: str) -> dict:
    """Получение статистики канала"""
    try:
        chat = await app.bot.get_chat(channel_id)
        
        # Получаем количество участников (если доступно)
        try:
            members_count = await app.bot.get_chat_members_count(channel_id)
        except:
            members_count = "недоступно"
        
        return {
            'title': chat.title,
            'description': chat.description,
            'members_count': members_count,
            'username': f"@{chat.username}" if chat.username else "отсутствует"
        }
        
    except Exception as e:
        logging.error(f"❌ Ошибка получения статистики канала: {e}")
        return {}

# === ФУНКЦИИ ДЛЯ ОБРАБОТКИ ОШИБОК ===
def handle_async_error(task: asyncio.Task, context: str = ""):
    """Обработка ошибок в асинхронных задачах"""
    if task.exception():
        exception = task.exception()
        logging.error(f"❌ Ошибка в асинхронной задаче {context}: {exception}")

async def safe_execute(coroutine, context: str = "", default_return=None):
    """Безопасное выполнение корутины с обработкой ошибок"""
    try:
        return await coroutine
    except Exception as e:
        logging.error(f"❌ Ошибка при выполнении {context}: {e}")
        return default_return

# === ФУНКЦИИ ДЛЯ ВАЛИДАЦИИ ===
def is_valid_channel_id(channel_id: str) -> bool:
    """Проверка валидности ID канала"""
    if not channel_id:
        return False
    
    # ID канала обычно начинается с -100
    if channel_id.startswith('-100'):
        try:
            int(channel_id)
            return True
        except ValueError:
            return False
    
    return False

def is_valid_user_id(user_id: int) -> bool:
    """Проверка валидности ID пользователя"""
    return user_id is not None and user_id > 0

def is_valid_text(text: str, min_length: int = 1, max_length: int = 4000) -> bool:
    """Проверка валидности текста"""
    if not text or not isinstance(text, str):
        return False
    
    text = text.strip()
    return min_length <= len(text) <= max_length

# Импорты для функций
import re
from datetime import timedelta

# Инициализация логгера
logger = setup_logging()
