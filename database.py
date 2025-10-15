import sqlite3
import logging
import os
from datetime import datetime
from typing import Optional, List, Dict, Any
from config import DB_PATH

logger = logging.getLogger(__name__)

class Database:
    def __init__(self):
        self.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.row_factory = sqlite3.Row
        self._init_db()
    
    def _init_db(self):
        """Инициализация базы данных и создание таблиц с проверкой существующих колонок"""
        cursor = self.conn.cursor()
        
        # Основные таблицы
        tables = [
            '''CREATE TABLE IF NOT EXISTS stats (
                date TEXT PRIMARY KEY,
                messages_processed INTEGER DEFAULT 0,
                spam_blocked INTEGER DEFAULT 0,
                replies_sent INTEGER DEFAULT 0,
                errors INTEGER DEFAULT 0,
                active_users INTEGER DEFAULT 0,
                unprocessed_messages INTEGER DEFAULT 0
            )''',
            '''CREATE TABLE IF NOT EXISTS user_activity (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_seen TEXT,
                last_activity TEXT,
                messages_count INTEGER DEFAULT 0,
                is_trusted BOOLEAN DEFAULT FALSE,
                warnings INTEGER DEFAULT 0,
                trust_score INTEGER DEFAULT 50
            )''',
            '''CREATE TABLE IF NOT EXISTS message_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                message_text TEXT,
                timestamp TEXT,
                is_spam BOOLEAN,
                response_text TEXT,
                spam_score REAL,
                processed_at TEXT
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
                usage_count INTEGER DEFAULT 0,
                last_used TEXT
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
                main_idea TEXT DEFAULT '',
                published_at TEXT,
                error_message TEXT
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
            '''CREATE TABLE IF NOT EXISTS moderation_stats (
                date TEXT PRIMARY KEY,
                total_checked INTEGER DEFAULT 0,
                spam_detected INTEGER DEFAULT 0,
                ai_checks INTEGER DEFAULT 0,
                false_positives INTEGER DEFAULT 0,
                trust_scores_updated INTEGER DEFAULT 0
            )''',
            '''CREATE TABLE IF NOT EXISTS auto_posts_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                post_type TEXT,
                post_text TEXT,
                posted_at TEXT,
                success BOOLEAN DEFAULT TRUE,
                error_message TEXT,
                retry_count INTEGER DEFAULT 0
            )''',
            '''CREATE TABLE IF NOT EXISTS recovery_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recovery_type TEXT,
                processed_count INTEGER,
                spam_count INTEGER,
                total_messages INTEGER,
                recovery_time TEXT,
                success BOOLEAN DEFAULT TRUE,
                error_message TEXT,
                duration_seconds INTEGER DEFAULT 0
            )''',
            '''CREATE TABLE IF NOT EXISTS system_health (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                check_time TEXT,
                total_messages INTEGER,
                unprocessed_messages INTEGER,
                active_users INTEGER,
                database_size INTEGER,
                last_backup TEXT,
                status TEXT DEFAULT 'healthy'
            )'''
        ]
        
        # Создаем таблицы
        for table in tables:
            try:
                cursor.execute(table)
                logger.debug(f"✅ Таблица создана/проверена: {table.split('(')[0].split()[-1]}")
            except Exception as e:
                logger.error(f"❌ Ошибка создания таблицы: {e}")
        
        # Добавляем отсутствующие колонки в существующие таблицы
        self._add_missing_columns()
        
        # Создаем индексы для улучшения производительности
        indexes = [
            'CREATE INDEX IF NOT EXISTS idx_message_history_timestamp ON message_history(timestamp)',
            'CREATE INDEX IF NOT EXISTS idx_message_history_user_id ON message_history(user_id)',
            'CREATE INDEX IF NOT EXISTS idx_message_history_processed ON message_history(is_spam, response_text)',
            'CREATE INDEX IF NOT EXISTS idx_scheduled_posts_status ON scheduled_posts(status)',
            'CREATE INDEX IF NOT EXISTS idx_scheduled_posts_time ON scheduled_posts(scheduled_time)',
            'CREATE INDEX IF NOT EXISTS idx_user_activity_last ON user_activity(last_activity)',
            'CREATE INDEX IF NOT EXISTS idx_auto_posts_posted ON auto_posts_history(posted_at)',
            'CREATE INDEX IF NOT EXISTS idx_response_cache_created ON response_cache(created_at)'
        ]
        
        for index in indexes:
            try:
                cursor.execute(index)
            except Exception as e:
                logger.error(f"❌ Ошибка создания индекса: {e}")
        
        self.conn.commit()
        logger.info(f"💾 База данных инициализирована: {DB_PATH}")
    
    def _add_missing_columns(self):
        """Добавление отсутствующих колонок в существующие таблицы"""
        cursor = self.conn.cursor()
        
        # Проверяем и добавляем отсутствующие колонки
        column_checks = [
            # message_history table
            ('message_history', 'spam_score', 'REAL'),
            ('message_history', 'processed_at', 'TEXT'),
            
            # user_activity table  
            ('user_activity', 'trust_score', 'INTEGER DEFAULT 50'),
            
            # recovery_log table
            ('recovery_log', 'duration_seconds', 'INTEGER DEFAULT 0'),
            
            # auto_posts_history table
            ('auto_posts_history', 'retry_count', 'INTEGER DEFAULT 0'),
            
            # response_cache table
            ('response_cache', 'last_used', 'TEXT'),
            
            # scheduled_posts table
            ('scheduled_posts', 'published_at', 'TEXT'),
            ('scheduled_posts', 'error_message', 'TEXT'),
            
            # stats table
            ('stats', 'unprocessed_messages', 'INTEGER DEFAULT 0'),
            
            # moderation_stats table
            ('moderation_stats', 'trust_scores_updated', 'INTEGER DEFAULT 0')
        ]
        
        for table, column, column_type in column_checks:
            try:
                # Проверяем существование колонки
                cursor.execute(f"PRAGMA table_info({table})")
                columns = [col[1] for col in cursor.fetchall()]
                
                if column not in columns:
                    # Для колонок с DEFAULT значением используем упрощенный синтаксис
                    if 'DEFAULT' in column_type:
                        simple_type = column_type.split('DEFAULT')[0].strip()
                        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {simple_type}")
                    else:
                        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")
                    logger.info(f"✅ Добавлена колонка {column} в таблицу {table}")
            except Exception as e:
                logger.error(f"❌ Ошибка добавления колонки {column} в {table}: {e}")
        
        self.conn.commit()
    
    def _adapt_datetime(self, dt: datetime) -> str:
        """Конвертация datetime в строку для SQLite"""
        return dt.isoformat()
    
    def execute_with_datetime(self, query: str, params: tuple = ()):
        """Выполнение запроса с автоматической конвертацией datetime"""
        adapted_params = []
        for param in params:
            if isinstance(param, datetime):
                adapted_params.append(self._adapt_datetime(param))
            else:
                adapted_params.append(param)
        
        cursor = self.conn.cursor()
        cursor.execute(query, tuple(adapted_params))
        return cursor
    
    def get_unprocessed_messages(self, hours_back: int = 24) -> List[Dict]:
        """Получение непроцессированных сообщений за указанный период"""
        cursor = self.conn.cursor()
        
        # Проверяем существование колонки spam_score
        cursor.execute("PRAGMA table_info(message_history)")
        columns = [col[1] for col in cursor.fetchall()]
        has_spam_score = 'spam_score' in columns
        
        # ИСПРАВЛЕННЫЙ ЗАПРОС: ищем сообщения где is_spam IS NULL И response_text IS NULL
        if has_spam_score:
            query = '''
                SELECT mh.id, mh.user_id, mh.message_text, mh.timestamp, 
                       ua.username, mh.is_spam, mh.response_text, mh.spam_score
                FROM message_history mh
                LEFT JOIN user_activity ua ON mh.user_id = ua.user_id
                WHERE mh.timestamp >= datetime('now', ?)
                AND mh.is_spam IS NULL 
                AND mh.response_text IS NULL
                ORDER BY mh.timestamp ASC
            '''
        else:
            query = '''
                SELECT mh.id, mh.user_id, mh.message_text, mh.timestamp, 
                       ua.username, mh.is_spam, mh.response_text, NULL as spam_score
                FROM message_history mh
                LEFT JOIN user_activity ua ON mh.user_id = ua.user_id
                WHERE mh.timestamp >= datetime('now', ?)
                AND mh.is_spam IS NULL 
                AND mh.response_text IS NULL
                ORDER BY mh.timestamp ASC
            '''
        
        cursor.execute(query, (f'-{hours_back} hours',))
        results = cursor.fetchall()
        return [dict(row) for row in results]
    
    def get_unprocessed_messages_count(self, hours_back: int = 24) -> int:
        """Получение количества непроцессированных сообщений"""
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT COUNT(*) 
            FROM message_history 
            WHERE timestamp >= datetime('now', ?)
            AND is_spam IS NULL 
            AND response_text IS NULL
        ''', (f'-{hours_back} hours',))
        
        result = cursor.fetchone()
        return result[0] if result else 0
    
    def get_message_status_stats(self, hours_back: int = 24) -> Dict[str, int]:
        """Получение статистики по статусам сообщений"""
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN is_spam IS NULL AND response_text IS NULL THEN 1 ELSE 0 END) as unprocessed,
                SUM(CASE WHEN is_spam = 1 THEN 1 ELSE 0 END) as spam,
                SUM(CASE WHEN is_spam = 0 THEN 1 ELSE 0 END) as legitimate,
                SUM(CASE WHEN is_spam IS NOT NULL AND response_text IS NULL THEN 1 ELSE 0 END) as processed_no_response,
                SUM(CASE WHEN is_spam IS NULL AND response_text IS NOT NULL THEN 1 ELSE 0 END) as response_no_spam_flag
            FROM message_history 
            WHERE timestamp >= datetime('now', ?)
        ''', (f'-{hours_back} hours',))
        
        result = cursor.fetchone()
        return {
            'total': result[0] or 0,
            'unprocessed': result[1] or 0,
            'spam': result[2] or 0,
            'legitimate': result[3] or 0,
            'processed_no_response': result[4] or 0,
            'response_no_spam_flag': result[5] or 0
        }
    
    def mark_message_processed(self, message_id: int, is_spam: bool = False, response_text: str = None, spam_score: float = None):
        """Отметка сообщения как обработанного"""
        cursor = self.conn.cursor()
        
        # Проверяем существование колонки spam_score
        cursor.execute("PRAGMA table_info(message_history)")
        columns = [col[1] for col in cursor.fetchall()]
        has_spam_score = 'spam_score' in columns
        has_processed_at = 'processed_at' in columns
        
        if has_spam_score and has_processed_at:
            cursor.execute('''
                UPDATE message_history 
                SET is_spam = ?, response_text = ?, spam_score = ?, processed_at = datetime('now')
                WHERE id = ?
            ''', (is_spam, response_text, spam_score, message_id))
        elif has_processed_at:
            cursor.execute('''
                UPDATE message_history 
                SET is_spam = ?, response_text = ?, processed_at = datetime('now')
                WHERE id = ?
            ''', (is_spam, response_text, message_id))
        else:
            cursor.execute('''
                UPDATE message_history 
                SET is_spam = ?, response_text = ?
                WHERE id = ?
            ''', (is_spam, response_text, message_id))
            
        self.conn.commit()
        logger.debug(f"💾 Сообщение {message_id} помечено как обработанное (спам: {is_spam})")
    
    def save_message(self, user_id: int, message_text: str, is_spam: bool = None, response_text: str = None, spam_score: float = None) -> int:
        """Сохранение сообщения в базу данных"""
        cursor = self.conn.cursor()
        
        # Проверяем существование колонки spam_score
        cursor.execute("PRAGMA table_info(message_history)")
        columns = [col[1] for col in cursor.fetchall()]
        has_spam_score = 'spam_score' in columns
        
        if has_spam_score:
            cursor = self.execute_with_datetime('''
                INSERT INTO message_history 
                (user_id, message_text, timestamp, is_spam, response_text, spam_score)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (user_id, message_text, datetime.now(), is_spam, response_text, spam_score))
        else:
            cursor = self.execute_with_datetime('''
                INSERT INTO message_history 
                (user_id, message_text, timestamp, is_spam, response_text)
                VALUES (?, ?, ?, ?, ?)
            ''', (user_id, message_text, datetime.now(), is_spam, response_text))
        
        self.conn.commit()
        message_id = cursor.lastrowid
        
        # Обновляем статистику пользователя
        self._update_user_activity(user_id, message_text)
        
        logger.debug(f"💾 Сообщение сохранено: ID={message_id}, пользователь={user_id}")
        return message_id
    
    def _update_user_activity(self, user_id: int, message_text: str = None):
        """Обновление активности пользователя"""
        username = f"user_{user_id}"  # Базовое имя
        
        cursor = self.execute_with_datetime('''
            INSERT OR REPLACE INTO user_activity 
            (user_id, username, first_seen, last_activity, messages_count)
            VALUES (?, ?, 
                COALESCE((SELECT first_seen FROM user_activity WHERE user_id = ?), ?), 
                ?, 
                COALESCE((SELECT messages_count FROM user_activity WHERE user_id = ?), 0) + 1
            )
        ''', (user_id, username, user_id, datetime.now().date().isoformat(), 
              datetime.now().date().isoformat(), user_id))
        
        self.conn.commit()
    
    def update_user_trust_score(self, user_id: int, trust_score: int):
        """Обновление уровня доверия пользователя"""
        cursor = self.conn.cursor()
        
        # Проверяем существование колонки trust_score
        cursor.execute("PRAGMA table_info(user_activity)")
        columns = [col[1] for col in cursor.fetchall()]
        has_trust_score = 'trust_score' in columns
        
        if has_trust_score:
            cursor.execute('''
                UPDATE user_activity 
                SET trust_score = ?
                WHERE user_id = ?
            ''', (trust_score, user_id))
            self.conn.commit()
            logger.debug(f"🔐 Обновлен уровень доверия пользователя {user_id}: {trust_score}")
        else:
            logger.warning(f"⚠️ Колонка trust_score не существует в таблице user_activity")
    
    def get_user_trust_score(self, user_id: int) -> int:
        """Получение уровня доверия пользователя"""
        cursor = self.conn.cursor()
        
        # Проверяем существование колонки trust_score
        cursor.execute("PRAGMA table_info(user_activity)")
        columns = [col[1] for col in cursor.fetchall()]
        has_trust_score = 'trust_score' in columns
        
        if has_trust_score:
            cursor.execute('SELECT trust_score FROM user_activity WHERE user_id = ?', (user_id,))
            result = cursor.fetchone()
            return result[0] if result else 50  # Значение по умолчанию
        else:
            return 50  # Значение по умолчанию
    
    def log_recovery(self, recovery_type: str, processed_count: int, spam_count: int, 
                    total_messages: int, success: bool = True, error_message: str = None, duration_seconds: int = 0):
        """Логирование процесса восстановления"""
        cursor = self.conn.cursor()
        
        # Проверяем существование колонки duration_seconds
        cursor.execute("PRAGMA table_info(recovery_log)")
        columns = [col[1] for col in cursor.fetchall()]
        has_duration = 'duration_seconds' in columns
        
        if has_duration:
            cursor = self.execute_with_datetime('''
                INSERT INTO recovery_log 
                (recovery_type, processed_count, spam_count, total_messages, recovery_time, success, error_message, duration_seconds)
                VALUES (?, ?, ?, ?, datetime('now'), ?, ?, ?)
            ''', (recovery_type, processed_count, spam_count, total_messages, success, error_message, duration_seconds))
        else:
            cursor = self.execute_with_datetime('''
                INSERT INTO recovery_log 
                (recovery_type, processed_count, spam_count, total_messages, recovery_time, success, error_message)
                VALUES (?, ?, ?, ?, datetime('now'), ?, ?)
            ''', (recovery_type, processed_count, spam_count, total_messages, success, error_message))
        
        self.conn.commit()
        logger.info(f"📊 Логирование восстановления: {recovery_type}, обработано: {processed_count}")
    
    def get_recovery_stats(self, days_back: int = 7) -> Dict:
        """Получение статистики восстановления за указанный период"""
        cursor = self.conn.cursor()
        
        # Проверяем существование колонки duration_seconds
        cursor.execute("PRAGMA table_info(recovery_log)")
        columns = [col[1] for col in cursor.fetchall()]
        has_duration = 'duration_seconds' in columns
        
        if has_duration:
            cursor.execute('''
                SELECT 
                    COUNT(*) as total_recoveries,
                    SUM(processed_count) as total_processed,
                    SUM(spam_count) as total_spam,
                    AVG(processed_count) as avg_processed,
                    SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as successful_recoveries,
                    AVG(duration_seconds) as avg_duration
                FROM recovery_log 
                WHERE datetime(recovery_time) >= datetime('now', ?)
            ''', (f'-{days_back} days',))
        else:
            cursor.execute('''
                SELECT 
                    COUNT(*) as total_recoveries,
                    SUM(processed_count) as total_processed,
                    SUM(spam_count) as total_spam,
                    AVG(processed_count) as avg_processed,
                    SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as successful_recoveries,
                    0 as avg_duration
                FROM recovery_log 
                WHERE datetime(recovery_time) >= datetime('now', ?)
            ''', (f'-{days_back} days',))
        
        result = cursor.fetchone()
        return {
            'total_recoveries': result[0] or 0,
            'total_processed': result[1] or 0,
            'total_spam': result[2] or 0,
            'avg_processed': result[3] or 0,
            'successful_recoveries': result[4] or 0,
            'avg_duration_seconds': result[5] or 0
        }
    
    def get_user_stats(self, user_id: int) -> Dict:
        """Получение статистики пользователя"""
        cursor = self.conn.cursor()
        
        # Общее количество сообщений
        cursor.execute('SELECT COUNT(*) FROM message_history WHERE user_id = ?', (user_id,))
        total_messages = cursor.fetchone()[0] or 0
        
        # Количество спама
        cursor.execute('SELECT COUNT(*) FROM message_history WHERE user_id = ? AND is_spam = TRUE', (user_id,))
        spam_messages = cursor.fetchone()[0] or 0
        
        # Последняя активность
        cursor.execute('SELECT MAX(timestamp) FROM message_history WHERE user_id = ?', (user_id,))
        last_activity = cursor.fetchone()[0]
        
        # Уровень доверия
        trust_score = self.get_user_trust_score(user_id)
        
        return {
            'total_messages': total_messages,
            'spam_messages': spam_messages,
            'legitimate_messages': total_messages - spam_messages,
            'last_activity': last_activity,
            'spam_ratio': spam_messages / total_messages if total_messages > 0 else 0,
            'trust_score': trust_score
        }

    def get_system_stats(self) -> Dict:
        """Получение общей статистики системы"""
        cursor = self.conn.cursor()
        
        stats = {}
        
        # Статистика сообщений
        cursor.execute('SELECT COUNT(*) FROM message_history')
        stats['total_messages'] = cursor.fetchone()[0] or 0
        
        cursor.execute('SELECT COUNT(*) FROM message_history WHERE is_spam = TRUE')
        stats['spam_messages'] = cursor.fetchone()[0] or 0
        
        cursor.execute('SELECT COUNT(*) FROM message_history WHERE is_spam = FALSE')
        stats['legitimate_messages'] = cursor.fetchone()[0] or 0
        
        # Используем исправленный метод для непроцессированных сообщений
        stats['unprocessed_messages'] = self.get_unprocessed_messages_count(24)
        
        # Статистика пользователей
        cursor.execute('SELECT COUNT(DISTINCT user_id) FROM user_activity')
        stats['unique_users'] = cursor.fetchone()[0] or 0
        
        cursor.execute('SELECT COUNT(DISTINCT user_id) FROM user_activity WHERE date(last_activity) >= date("now", "-7 days")')
        stats['active_users_7d'] = cursor.fetchone()[0] or 0
        
        # Статистика постов
        cursor.execute('SELECT COUNT(*) FROM scheduled_posts')
        stats['total_scheduled_posts'] = cursor.fetchone()[0] or 0
        
        cursor.execute('SELECT COUNT(*) FROM scheduled_posts WHERE status = "scheduled"')
        stats['pending_posts'] = cursor.fetchone()[0] or 0
        
        cursor.execute('SELECT COUNT(*) FROM scheduled_posts WHERE status = "published"')
        stats['published_posts'] = cursor.fetchone()[0] or 0
        
        cursor.execute('SELECT COUNT(*) FROM content_plans')
        stats['content_plans'] = cursor.fetchone()[0] or 0
        
        # Статистика авто-постов
        cursor.execute('SELECT COUNT(*) FROM auto_posts_history WHERE success = TRUE')
        stats['successful_auto_posts'] = cursor.fetchone()[0] or 0
        
        cursor.execute('SELECT COUNT(*) FROM auto_posts_history WHERE success = FALSE')
        stats['failed_auto_posts'] = cursor.fetchone()[0] or 0
        
        # Статистика кэша
        cursor.execute('SELECT COUNT(*) FROM response_cache')
        stats['cached_responses'] = cursor.fetchone()[0] or 0
        
        cursor.execute('SELECT SUM(usage_count) FROM response_cache')
        stats['total_cache_usage'] = cursor.fetchone()[0] or 0
        
        # Статистика восстановления
        recovery_stats = self.get_recovery_stats(1)  # За последний день
        stats['recovery_stats'] = recovery_stats
        
        # Статистика статусов сообщений
        message_stats = self.get_message_status_stats(24)
        stats['message_status_stats'] = message_stats
        
        return stats

    def cleanup_old_data(self, days_to_keep: int = 30):
        """Очистка старых данных"""
        cursor = self.conn.cursor()
        
        try:
            # Удаляем старые сообщения
            cursor.execute('DELETE FROM message_history WHERE datetime(timestamp) < datetime("now", ?)', 
                         (f'-{days_to_keep} days',))
            messages_deleted = cursor.rowcount
            
            # Удаляем старые записи кэша
            cursor.execute('DELETE FROM response_cache WHERE datetime(created_at) < datetime("now", ?)', 
                         (f'-{days_to_keep} days',))
            cache_deleted = cursor.rowcount
            
            # Удаляем старые логи авто-постов
            cursor.execute('DELETE FROM auto_posts_history WHERE datetime(posted_at) < datetime("now", ?)', 
                         (f'-{days_to_keep} days',))
            auto_posts_deleted = cursor.rowcount
            
            # Удаляем старые логи восстановления
            cursor.execute('DELETE FROM recovery_log WHERE datetime(recovery_time) < datetime("now", ?)', 
                         (f'-{days_to_keep} days',))
            recovery_logs_deleted = cursor.rowcount
            
            # Удаляем неактивных пользователей (без активности более 60 дней)
            cursor.execute('''
                DELETE FROM user_activity 
                WHERE datetime(last_activity) < datetime("now", ?)
                AND messages_count = 0
            ''', (f'-{days_to_keep * 2} days',))
            inactive_users_deleted = cursor.rowcount
            
            self.conn.commit()
            
            logger.info(f"🧹 Очистка данных: сообщений={messages_deleted}, кэша={cache_deleted}, "
                       f"авто-постов={auto_posts_deleted}, логов восстановления={recovery_logs_deleted}, "
                       f"неактивных пользователей={inactive_users_deleted}")
            
            return {
                'messages_deleted': messages_deleted,
                'cache_deleted': cache_deleted,
                'auto_posts_deleted': auto_posts_deleted,
                'recovery_logs_deleted': recovery_logs_deleted,
                'inactive_users_deleted': inactive_users_deleted
            }
            
        except Exception as e:
            logger.error(f"❌ Ошибка очистки данных: {e}")
            return {}

    def backup_database(self, backup_path: str = None):
        """Создание резервной копии базы данных"""
        import shutil
        import os
        from datetime import datetime
        
        if backup_path is None:
            backup_dir = os.path.join(os.path.dirname(DB_PATH), 'backups')
            os.makedirs(backup_dir, exist_ok=True)
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            backup_path = os.path.join(backup_dir, f'bot_backup_{timestamp}.db')
        
        try:
            # Создаем резервную копию
            shutil.copy2(DB_PATH, backup_path)
            logger.info(f"💾 Создана резервная копия: {backup_path}")
            return backup_path
            
        except Exception as e:
            logger.error(f"❌ Ошибка создания резервной копии: {e}")
            return None

    def optimize_database(self):
        """Оптимизация базы данных"""
        cursor = self.conn.cursor()
        
        try:
            # Выполняем VACUUM для оптимизации
            cursor.execute('VACUUM')
            
            # Анализируем таблицы для оптимизации запросов
            cursor.execute('ANALYZE')
            
            self.conn.commit()
            logger.info("⚡ База данных оптимизирована")
            return True
            
        except Exception as e:
            logger.error(f"❌ Ошибка оптимизации базы данных: {e}")
            return False

    def reset_messages_for_testing(self, limit: int = 5):
        """Сброс статуса сообщений для тестирования восстановления"""
        cursor = self.conn.cursor()
        
        try:
            cursor.execute('''
                UPDATE message_history 
                SET is_spam = NULL, response_text = NULL, spam_score = NULL
                WHERE id IN (
                    SELECT id FROM message_history 
                    WHERE timestamp >= datetime('now', '-24 hours')
                    ORDER BY timestamp DESC 
                    LIMIT ?
                )
            ''', (limit,))
            
            reset_count = cursor.rowcount
            self.conn.commit()
            
            logger.info(f"🔄 Сброшено {reset_count} сообщений для тестирования")
            return reset_count
            
        except Exception as e:
            logger.error(f"❌ Ошибка сброса сообщений: {e}")
            return 0

    def get_recent_messages_sample(self, limit: int = 10) -> List[Dict]:
        """Получение примеров последних сообщений"""
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT id, user_id, message_text, timestamp, is_spam, response_text
            FROM message_history 
            WHERE timestamp >= datetime('now', '-24 hours')
            ORDER BY timestamp DESC
            LIMIT ?
        ''', (limit,))
        
        results = cursor.fetchall()
        return [dict(row) for row in results]

    def __del__(self):
        """Деструктор - закрытие соединения с БД"""
        try:
            if hasattr(self, 'conn'):
                self.conn.close()
        except:
            pass