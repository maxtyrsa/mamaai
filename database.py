import sqlite3
import logging
from datetime import datetime
from config import DB_PATH

logger = logging.getLogger(__name__)

class Database:
    def __init__(self):
        self.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        self.conn.execute("PRAGMA foreign_keys = ON")
        self._init_db()
    
    def _init_db(self):
        cursor = self.conn.cursor()
        
        tables = [
            '''CREATE TABLE IF NOT EXISTS stats (
                date TEXT PRIMARY KEY,
                messages_processed INTEGER DEFAULT 0,
                spam_blocked INTEGER DEFAULT 0,
                replies_sent INTEGER DEFAULT 0,
                errors INTEGER DEFAULT 0,
                active_users INTEGER DEFAULT 0
            )''',
            '''CREATE TABLE IF NOT EXISTS user_activity (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_seen TEXT,
                last_activity TEXT,
                messages_count INTEGER DEFAULT 0,
                is_trusted BOOLEAN DEFAULT FALSE,
                warnings INTEGER DEFAULT 0
            )''',
            '''CREATE TABLE IF NOT EXISTS message_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                message_text TEXT,
                timestamp TEXT,
                is_spam BOOLEAN,
                response_text TEXT
            )''',
            '''CREATE TABLE IF NOT EXISTS user_preferences (
                user_id INTEGER PRIMARY KEY,
                language TEXT DEFAULT 'ru',
                receive_notifications BOOLEAN DEFAULT TRUE,
                response_style TEXT DEFAULT 'friendly'
            )''',
            '''CREATE TABLE IF NOT EXISTS response_cache (
                message_hash TEXT PRIMARY KEY,
                response_text TEXT,
                created_at TEXT,
                usage_count INTEGER DEFAULT 0
            )''',
            '''CREATE TABLE IF NOT EXISTS scheduled_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                post_text TEXT,
                scheduled_time TEXT,
                status TEXT DEFAULT 'scheduled',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                channel_id TEXT,
                tone TEXT,
                topic TEXT,
                length TEXT,
                main_idea TEXT DEFAULT ''
            )''',
            '''CREATE TABLE IF NOT EXISTS content_plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                plan_name TEXT,
                plan_type TEXT,
                start_date TEXT,
                end_date TEXT,
                plan_data TEXT,
                status TEXT DEFAULT 'active',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )''',
            '''CREATE TABLE IF NOT EXISTS auto_posts_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                post_type TEXT,
                post_text TEXT,
                posted_at TEXT,
                success BOOLEAN DEFAULT TRUE,
                error_message TEXT
            )'''
        ]
        
        for table in tables:
            try:
                cursor.execute(table)
            except Exception as e:
                logger.error(f"❌ Ошибка создания таблицы: {e}")
        
        self.conn.commit()
        logger.info(f"💾 База данных инициализирована: {DB_PATH}")
    
    def _adapt_datetime(self, dt: datetime) -> str:
        return dt.isoformat()
    
    def execute_with_datetime(self, query: str, params: tuple = ()):
        adapted_params = []
        for param in params:
            if isinstance(param, datetime):
                adapted_params.append(self._adapt_datetime(param))
            else:
                adapted_params.append(param)
        
        cursor = self.conn.cursor()
        cursor.execute(query, tuple(adapted_params))
        return cursor
