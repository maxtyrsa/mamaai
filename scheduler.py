import asyncio
import logging
from datetime import datetime, time as dt_time, timedelta
from config import MORNING_POST_TIME, EVENING_POST_TIME, Config, CHANNEL_ID
from utils import send_message_with_fallback

logger = logging.getLogger(__name__)

class AutoPostScheduler:
    def __init__(self, app, response_generator, db):
        self.app = app
        self.response_generator = response_generator
        self.db = db
        self.is_running = False
        self.tasks = []
        self.last_morning_post = None
        self.last_evening_post = None
    
    async def start(self):
        """Запуск системы автоматических постов"""
        if self.is_running:
            return
        
        self.is_running = True
        logger.info("🚀 Запуск системы автоматических постов...")
        
        # Проверяем, были ли сегодня уже посты
        await self._check_today_posts()
        
        self.tasks = [
            asyncio.create_task(self._post_loop("morning", MORNING_POST_TIME)),
            asyncio.create_task(self._post_loop("evening", EVENING_POST_TIME))
        ]
        
        logger.info("✅ Система автоматических постов запущена")
        logger.info(f"⏰ Утренние посты: {MORNING_POST_TIME.strftime('%H:%M')}")
        logger.info(f"⏰ Вечерние посты: {EVENING_POST_TIME.strftime('%H:%M')}")
    
    async def _check_today_posts(self):
        """Проверяем, были ли сегодня уже опубликованы посты"""
        try:
            cursor = self.db.conn.cursor()
            today = datetime.now().date().isoformat()
            
            cursor.execute('''
                SELECT post_type, MAX(posted_at) 
                FROM auto_posts_history 
                WHERE date(posted_at) = date(?) AND success = TRUE
                GROUP BY post_type
            ''', (today,))
            
            results = cursor.fetchall()
            
            for post_type, last_post_time in results:
                if post_type == "morning":
                    self.last_morning_post = datetime.fromisoformat(last_post_time)
                    logger.info(f"📅 Сегодняшний утренний пост уже был в {self.last_morning_post.strftime('%H:%M')}")
                elif post_type == "evening":
                    self.last_evening_post = datetime.fromisoformat(last_post_time)
                    logger.info(f"📅 Сегодняшний вечерний пост уже был в {self.last_evening_post.strftime('%H:%M')}")
                    
        except Exception as e:
            logger.error(f"❌ Ошибка проверки сегодняшних постов: {e}")
    
    async def _post_loop(self, post_type: str, post_time: dt_time):
        """Цикл для публикации постов"""
        type_emoji = "🌅" if post_type == "morning" else "🌙"
        type_name = "утренний" if post_type == "morning" else "вечерний"
        
        while self.is_running:
            try:
                now = datetime.now()
                target_time = datetime.combine(now.date(), post_time)
                
                # Если время уже прошло сегодня, планируем на завтра
                if now >= target_time:
                    target_time += timedelta(days=1)
                    logger.info(f"⏰ Время {post_time.strftime('%H:%M')} уже прошло, планируем на завтра")
                
                wait_seconds = (target_time - now).total_seconds()
                
                # Логируем только если ждем больше 10 минут
                if wait_seconds > 600:
                    hours = wait_seconds / 3600
                    logger.info(f"⏰ Следующий {type_name} пост через {hours:.1f} часов (в {target_time.strftime('%d.%m %H:%M')})")
                
                # Ждем до времени публикации (но не более 1 часа для периодической проверки)
                await asyncio.sleep(min(wait_seconds, 3600))
                
                # Проверяем, нужно ли публиковать пост
                if self.is_running and await self._should_publish_post(post_type):
                    if post_type == "morning" and Config.ENABLE_MORNING_POSTS:
                        await self._publish_post(post_type)
                    elif post_type == "evening" and Config.ENABLE_EVENING_POSTS:
                        await self._publish_post(post_type)
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"❌ Ошибка в цикле {type_name} постов: {e}")
                await asyncio.sleep(300)  # Ждем 5 минут перед повторной попыткой
    
    async def _should_publish_post(self, post_type: str) -> bool:
        """Проверяем, нужно ли публиковать пост"""
        now = datetime.now()
        today = now.date()
        
        # Проверяем, был ли уже пост сегодня этого типа
        if post_type == "morning":
            if self.last_morning_post and self.last_morning_post.date() == today:
                logger.info(f"⏰ Утренний пост уже был сегодня в {self.last_morning_post.strftime('%H:%M')}")
                return False
        else:
            if self.last_evening_post and self.last_evening_post.date() == today:
                logger.info(f"⏰ Вечерний пост уже был сегодня в {self.last_evening_post.strftime('%H:%M')}")
                return False
        
        # Проверяем время - публикуем только если текущее время близко к целевому
        target_time = MORNING_POST_TIME if post_type == "morning" else EVENING_POST_TIME
        current_time = now.time()
        
        # Публикуем если текущее время в пределах 10 минут от целевого
        time_diff = abs((datetime.combine(now.date(), current_time) - 
                        datetime.combine(now.date(), target_time)).total_seconds())
        
        if time_diff > 600:  # 10 минут
            logger.info(f"⏰ Не время для {post_type} поста (разница: {time_diff/60:.1f} мин)")
            return False
            
        return True
    
    async def _publish_post(self, post_type: str):
        """Публикация поста"""
        type_name = "утренний" if post_type == "morning" else "вечерний"
        type_emoji = "🌅" if post_type == "morning" else "🌙"
        
        try:
            logger.info(f"{type_emoji} Генерация {type_name} поста...")
            post_text = await self.response_generator.generate_motivational_message(post_type)
            
            if not post_text or len(post_text.strip()) < 10:
                logger.error(f"❌ Сгенерирован пустой или слишком короткий {type_name} пост")
                post_text = await self._get_fallback_post(post_type)
            
            # Публикуем пост с обработкой ошибок
            success = await send_message_with_fallback(self.app, CHANNEL_ID, post_text)
            
            if success:
                # Обновляем время последнего поста
                post_time = datetime.now()
                if post_type == "morning":
                    self.last_morning_post = post_time
                else:
                    self.last_evening_post = post_time
                
                cursor = self.db.execute_with_datetime('''
                    INSERT INTO auto_posts_history 
                    (post_type, post_text, posted_at, success)
                    VALUES (?, ?, ?, ?)
                ''', (post_type, post_text, post_time, True))
                self.db.conn.commit()
                
                logger.info(f"✅ {type_emoji} {type_name.capitalize()} пост опубликован")
                
                # Уведомляем администраторов
                if Config.NOTIFY_ON_SPAM:
                    notification_system = self.app.context_data.get('notification_system')
                    if notification_system:
                        await notification_system.notify_admins(
                            f"{type_emoji} {type_name.capitalize()} пост опубликован в {post_time.strftime('%H:%M')}\n\n"
                            f"{post_text[:100]}..."
                        )
            else:
                raise Exception("Не удалось опубликовать пост после нескольких попыток")
            
        except Exception as e:
            logger.error(f"❌ Ошибка публикации {type_name} поста: {e}")
            
            cursor = self.db.execute_with_datetime('''
                INSERT INTO auto_posts_history 
                (post_type, post_text, posted_at, success, error_message)
                VALUES (?, ?, ?, ?, ?)
            ''', (post_type, "", datetime.now(), False, str(e)))
            self.db.conn.commit()
    
    async def _get_fallback_post(self, post_type: str) -> str:
        """Резервные посты на случай ошибки генерации"""
        if post_type == "morning":
            return "☀️ Доброе утро! Новый день — новые возможности. Пусть сегодняшний день будет продуктивным и насыщенным позитивными событиями! 💫"
        else:
            return "🌙 Спокойной ночи! Отдыхайте и набирайтесь сил для новых свершений. Пусть ваши сны будут яркими и приятными! 💤"
    
    async def stop(self):
        """Остановка системы автоматических постов"""
        self.is_running = False
        for task in self.tasks:
            if not task.done():
                task.cancel()
        logger.info("🛑 Система автоматических постов остановлена")


class PostScheduler:
    def __init__(self, app, db):
        self.app = app
        self.db = db
        self.is_running = False
        self.scheduler_task = None
    
    async def start(self):
        """Запуск системы планировщика постов"""
        if self.is_running:
            return
        
        self.is_running = True
        logger.info("🚀 Запуск системы планировщика постов...")
        
        self.scheduler_task = asyncio.create_task(self._scheduler_loop())
        logger.info("✅ Система планировщика постов запущена")
    
    async def stop(self):
        """Остановка системы планировщика постов"""
        self.is_running = False
        if self.scheduler_task:
            self.scheduler_task.cancel()
        logger.info("🛑 Система планировщика постов остановлена")
    
    async def _scheduler_loop(self):
        """Основной цикл планировщика"""
        while self.is_running:
            try:
                await self._check_scheduled_posts()
                await asyncio.sleep(30)  # Проверяем каждые 30 секунд
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"❌ Ошибка в цикле планировщика: {e}")
                await asyncio.sleep(60)
    
    async def _check_scheduled_posts(self):
        """Проверка и публикация запланированных постов"""
        try:
            cursor = self.db.conn.cursor()
            cursor.execute('''
                SELECT id, user_id, post_text, scheduled_time, channel_id 
                FROM scheduled_posts 
                WHERE status = 'scheduled' 
                AND datetime(scheduled_time) <= datetime('now', '+10 seconds')
                ORDER BY datetime(scheduled_time) ASC
            ''')
            
            posts_to_publish = cursor.fetchall()
            
            for post in posts_to_publish:
                post_id, user_id, post_text, scheduled_time, channel_id = post
                logger.info(f"📤 Найден пост для публикации: ID {post_id}, время: {scheduled_time}")
                await self._publish_scheduled_post(post_id, user_id, post_text, channel_id)
                
        except Exception as e:
            logger.error(f"❌ Ошибка проверки запланированных постов: {e}")
    
    async def _publish_scheduled_post(self, post_id: int, user_id: int, post_text: str, channel_id: str):
        """Публикация запланированного поста"""
        try:
            logger.info(f"📤 Публикация запланированного поста {post_id} в канал {channel_id}...")
            
            # Проверяем, что канал существует и бот имеет права
            try:
                chat = await self.app.bot.get_chat(channel_id)
                logger.info(f"✅ Канал найден: {chat.title}")
                
                # Проверяем права бота
                bot_info = await self.app.bot.get_me()
                member = await self.app.bot.get_chat_member(channel_id, bot_info.id)
                if member.status not in ['administrator', 'creator']:
                    error_msg = "Бот не является администратором канала"
                    logger.error(f"❌ {error_msg}")
                    await self._mark_post_as_error(post_id, error_msg)
                    return
                    
            except Forbidden as e:
                if "bot is not a member" in str(e):
                    error_msg = "Бот не добавлен в канал"
                    logger.error(f"❌ {error_msg}")
                    await self._mark_post_as_error(post_id, error_msg)
                    return
                else:
                    raise e
            
            # Публикуем пост с обработкой ошибок
            success = await send_message_with_fallback(self.app, channel_id, post_text)
            
            if success:
                # Обновляем статус в базе
                await self._mark_post_as_published(post_id)
                logger.info(f"✅ Пост {post_id} успешно опубликован")
                
                # Уведомляем пользователя
                await self._notify_user(user_id, post_text, post_id)
            else:
                error_msg = "Не удалось опубликовать пост после нескольких попыток"
                await self._mark_post_as_error(post_id, error_msg)
                
        except Forbidden as e:
            if "bot is not a member" in str(e):
                error_msg = "Бот не является участником канала. Добавьте бота в канал как администратора."
                logger.error(f"❌ {error_msg}")
                await self._mark_post_as_error(post_id, error_msg)
            else:
                logger.error(f"❌ Ошибка публикации запланированного поста {post_id}: {e}")
                await self._mark_post_as_error(post_id, str(e))
        except Exception as e:
            logger.error(f"❌ Ошибка публикации запланированного поста {post_id}: {e}")
            await self._mark_post_as_error(post_id, str(e))
    
    async def _mark_post_as_published(self, post_id: int):
        """Отмечаем пост как опубликованный"""
        try:
            cursor = self.db.execute_with_datetime('''
                UPDATE scheduled_posts 
                SET status = 'published'
                WHERE id = ?
            ''', (post_id,))
            self.db.conn.commit()
        except Exception as e:
            logger.error(f"❌ Ошибка обновления статуса поста {post_id}: {e}")
    
    async def _mark_post_as_error(self, post_id: int, error_message: str):
        """Отмечаем пост как ошибочный"""
        try:
            cursor = self.db.execute_with_datetime('''
                UPDATE scheduled_posts 
                SET status = 'error', scheduled_time = ?
                WHERE id = ?
            ''', (datetime.now(), post_id))
            self.db.conn.commit()
            logger.error(f"❌ Пост {post_id} помечен как ошибочный: {error_message}")
        except Exception as e:
            logger.error(f"❌ Ошибка пометки поста {post_id} как ошибочного: {e}")
    
    async def _notify_user(self, user_id: int, post_text: str, post_id: int):
        """Уведомляем пользователя о публикации"""
        try:
            await self.app.bot.send_message(
                user_id,
                f"✅ Ваш запланированный пост опубликован!\n\n"
                f"📝 {post_text[:100]}...\n\n"
                f"🆔 ID поста: {post_id}"
            )
            logger.info(f"✅ Пользователь {user_id} уведомлен о публикации поста {post_id}")
        except Exception as e:
            logger.warning(f"⚠️ Не удалось уведомить пользователя {user_id}: {e}")
    
    async def get_scheduled_posts_stats(self) -> Dict:
        """Получение статистики запланированных постов"""
        cursor = self.db.conn.cursor()
        
        cursor.execute('''
            SELECT status, COUNT(*) 
            FROM scheduled_posts 
            GROUP BY status
        ''')
        
        stats = {}
        for status, count in cursor.fetchall():
            stats[status] = count
        
        # Ближайшие посты
        cursor.execute('''
            SELECT topic, scheduled_time, status 
            FROM scheduled_posts 
            WHERE status = 'scheduled'
            ORDER BY datetime(scheduled_time) 
            LIMIT 5
        ''')
        
        upcoming_posts = []
        for row in cursor.fetchall():
            topic, scheduled_time, status = row
            try:
                time_str = datetime.fromisoformat(scheduled_time).strftime('%d.%m %H:%M')
            except:
                time_str = scheduled_time
                
            upcoming_posts.append({
                'topic': topic,
                'time': time_str,
                'status': status
            })
        
        return {
            'stats': stats,
            'upcoming_posts': upcoming_posts
        }
