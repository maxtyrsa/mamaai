import os
import sys
from datetime import time as dt_time

def get_script_directory():
    """Получает путь к папке, где находится скрипт"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    else:
        return os.path.dirname(os.path.abspath(__file__))

SCRIPT_DIR = get_script_directory()
DB_PATH = os.path.join(SCRIPT_DIR, 'bot_data.db')
LOG_PATH = os.path.join(SCRIPT_DIR, 'bot_errors.log')
MODEL_PATH = os.path.join(SCRIPT_DIR, "model", "YandexGPT-5-Lite-8B-instruct.Q4_K_M.gguf")

# Настройки бота
BOT_TOKEN = "8261699857:AAEtqvaGETzjqN2SnZK1q53GEboaOAyV7xA"
CHANNEL_ID = "-1002126028964"

# Расписание автоматических постов
MORNING_POST_TIME = dt_time(9, 0)   # 09:00 утра
EVENING_POST_TIME = dt_time(22, 0)  # 22:00 вечера

class Config:
    MAX_MESSAGE_LENGTH = 1000
    MAX_REPLY_LENGTH = 4000
    USER_RATE_LIMIT = 5
    NEW_USER_RESTRICTION = True
    ENABLE_VOICE_RESPONSES = False
    ENABLE_IMAGE_GENERATION = False
    AUTO_TRANSLATE = False
    NOTIFY_ON_SPAM = True
    NOTIFY_ON_ERRORS = True
    ENABLE_WEEKLY_REPORTS = True
    ENABLE_MORNING_POSTS = True
    ENABLE_EVENING_POSTS = True
