import re
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from telegram.error import Forbidden, NetworkError, TimedOut, BadRequest

from config import CHANNEL_ID, Config
from utils import send_message_with_fallback, check_bot_permissions
from keyboards import (
    get_main_menu_keyboard,
    get_tone_keyboard,
    get_length_keyboard,
    get_content_plan_type_keyboard
)

logger = logging.getLogger(__name__)

class NotificationSystem:
    def __init__(self, app, db):
        self.app = app
        self.db = db
    
    async def notify_admins(self, message: str, include_buttons: bool = False):
        admins = await self.get_channel_admins()
        
        for admin_id in admins:
            try:
                if include_buttons:
                    keyboard = [
                        [InlineKeyboardButton("📊 Статистика", callback_data="stats")],
                        [InlineKeyboardButton("🛑 Стоп уведомления", callback_data="mute_notifications")]
                    ]
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    await self.app.bot.send_message(
                        admin_id, message, reply_markup=reply_markup
                    )
                else:
                    await self.app.bot.send_message(admin_id, message)
            except Exception as e:
                logger.error(f"❌ Не удалось отправить уведомление администратору {admin_id}: {e}")
    
    async def get_channel_admins(self) -> list:
        try:
            administrators = await self.app.bot.get_chat_administrators(CHANNEL_ID)
            return [
                admin.user.id for admin in administrators 
                if admin.user and not admin.user.is_bot
            ]
        except Exception as e:
            logger.error(f"❌ Ошибка получения администраторов: {e}")
            return []

class PostCreator:
    def __init__(self, response_generator, db):
        self.response_generator = response_generator
        self.db = db
    
    async def handle_post_creation(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.user_data.get('creating_post'):
            return
        
        user = update.effective_user
        message = update.effective_message
        
        stage = context.user_data.get('post_stage')
        logger.info(f"📝 Обработка создания поста, стадия: {stage}")
        
        if stage == 'topic':
            context.user_data['post_topic'] = message.text
            context.user_data['post_stage'] = 'tone'
            
            reply_markup = get_tone_keyboard()
            
            await message.reply_text(
                "🎭 **Шаг 2 из 5:** Выберите тон поста:",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        
        elif stage == 'main_idea':
            context.user_data['post_main_idea'] = message.text
            context.user_data['post_stage'] = 'length'
            
            reply_markup = get_length_keyboard()
            
            await message.reply_text(
                "📏 **Шаг 4 из 5:** Выберите длину поста:",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        
        elif stage == 'schedule_time':
            try:
                schedule_time = self.parse_schedule_time(message.text)
                if schedule_time:
                    logger.info(f"✅ Распознано время: {schedule_time}")
                    await self.schedule_post(update, context, schedule_time)
                else:
                    await message.reply_text(
                        "❌ Не удалось распознать время. Используйте: 'сейчас', 'через 2 часа', 'завтра 09:00'",
                        parse_mode='Markdown'
                    )
            except Exception as e:
                logger.error(f"❌ Ошибка планирования поста: {e}")
                await message.reply_text("❌ Ошибка при планировании поста")

    def parse_schedule_time(self, text: str):
        text = text.lower().strip()
        now = datetime.now()
        
        if text in ['сейчас', 'немедленно', 'now', 'сразу']:
            return now
        
        match = re.search(r'через\s*(\d+)\s*(час|часа|часов|минут|минуты|минуту)', text)
        if match:
            amount = int(match.group(1))
            unit = match.group(2)
            
            if unit in ['час', 'часа', 'часов']:
                return now + timedelta(hours=amount)
            elif unit in ['минут', 'минуты', 'минуту']:
                return now + timedelta(minutes=amount)
        
        match = re.search(r'завтра\s*в\s*(\d{1,2})[:\s]?(\d{2})?', text)
        if match:
            hour = int(match.group(1))
            minute = int(match.group(2) or 0)
            tomorrow = now + timedelta(days=1)
            try:
                return tomorrow.replace(hour=hour, minute=minute, second=0, microsecond=0)
            except ValueError:
                return None
        
        return None

    async def schedule_post(self, update: Update, context: ContextTypes.DEFAULT_TYPE, schedule_time: datetime):
        user = update.effective_user
        post_data = context.user_data
        
        required_data = ['generated_post', 'post_tone', 'post_topic', 'post_length', 'post_main_idea']
        missing_data = [key for key in required_data if key not in post_data or not post_data[key]]
        
        if missing_data:
            logger.error(f"❌ Отсутствуют данные: {missing_data}")
            await update.effective_message.reply_text("❌ Ошибка: отсутствуют данные поста")
            context.user_data.clear()
            return
        
        topic = post_data['post_topic']
        tone = post_data['post_tone']
        main_idea = post_data['post_main_idea']
        length = post_data['post_length']
        generated_post = post_data['generated_post']
        
        try:
            cursor = self.db.execute_with_datetime('''
                INSERT INTO scheduled_posts 
                (user_id, post_text, scheduled_time, channel_id, tone, topic, length, main_idea, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                user.id,
                generated_post,
                schedule_time,
                CHANNEL_ID,
                tone,
                topic,
                length,
                main_idea,
                'scheduled'
            ))
            self.db.conn.commit()
            
            post_id = cursor.lastrowid
            logger.info(f"✅ Пост сохранен: ID={post_id}, тема='{topic}', время='{schedule_time}'")
            
        except Exception as e:
            logger.error(f"❌ Ошибка сохранения поста: {e}")
            await update.effective_message.reply_text("❌ Ошибка при сохранении поста")
            return
        
        context.user_data.clear()
        
        # Проверяем, нужно ли опубликовать немедленно
        time_diff = (schedule_time - datetime.now()).total_seconds()
        
        if time_diff <= 60:  # Если менее 60 секунд до публикации
            try:
                success = await send_message_with_fallback(context.application, CHANNEL_ID, generated_post)
                
                if success:
                    # Обновляем статус в базе
                    cursor = self.db.execute_with_datetime('''
                        UPDATE scheduled_posts 
                        SET status = 'published'
                        WHERE id = ?
                    ''', (post_id,))
                    self.db.conn.commit()
                    
                    status = "✅ Пост опубликован!"
                    logger.info(f"✅ Пост {post_id} опубликован в канале")
                else:
                    status = "❌ Ошибка при публикации поста"
                    logger.error(f"❌ Не удалось опубликовать пост {post_id}")
                
            except Forbidden as e:
                if "bot is not a member" in str(e):
                    status = "❌ Бот не добавлен в канал. Добавьте бота как администратора."
                    logger.error(f"❌ {status}")
                else:
                    status = "❌ Ошибка при публикации поста"
                    logger.error(f"❌ Ошибка публикации: {e}")
            except Exception as e:
                logger.error(f"❌ Ошибка публикации: {e}")
                status = "❌ Ошибка при публикации поста"
        else:
            status = f"✅ Пост запланирован на {schedule_time.strftime('%d.%m.%Y %H:%M')}"
            logger.info(f"✅ Пост {post_id} запланирован на {schedule_time}")
        
        await update.effective_message.reply_text(
            f"{status}\n\n"
            f"📝 Тема: {topic}\n"
            f"🎭 Тон: {tone}\n"
            f"💡 Идея: {main_idea}\n"
            f"🆔 ID поста: {post_id}",
            parse_mode='Markdown'
        )

class ContentPlanManager:
    def __init__(self, response_generator, db):
        self.response_generator = response_generator
        self.db = db
    
    async def create_content_plan(self, update: Update, context: ContextTypes.DEFAULT_TYPE, plan_type: str):
        user = update.effective_user
        
        context.user_data['content_plan_type'] = plan_type
        context.user_data['content_plan_stage'] = 'niche'
        
        await update.callback_query.edit_message_text(
            f"�� **Создание {plan_type} контент-плана**\n\n"
            "🎯 **Шаг 1 из 3:** Введите нишу вашего канала:",
            parse_mode='Markdown'
        )
    
    async def handle_content_plan_creation(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        message = update.effective_message
        
        if not context.user_data.get('content_plan_stage'):
            return
        
        stage = context.user_data.get('content_plan_stage')
        logger.info(f"📅 Обработка контент-плана, стадия: {stage}")
        
        if stage == 'niche':
            context.user_data['content_plan_niche'] = message.text
            context.user_data['content_plan_stage'] = 'tone'
            
            reply_markup = get_tone_keyboard("plan_tone")
            
            await message.reply_text(
                "🎭 **Шаг 2 из 3:** Выберите тон контента:",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        
        elif stage == 'posts_count':
            try:
                posts_count = int(message.text)
                if posts_count < 1 or posts_count > 50:
                    await message.reply_text("❌ Введите число от 1 до 50")
                    return
                
                context.user_data['content_plan_posts_count'] = posts_count
                await self.generate_content_plan(update, context)
                
            except ValueError:
                await message.reply_text("❌ Пожалуйста, введите число")

    async def generate_content_plan(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        plan_data = context.user_data
        
        await update.effective_message.reply_text("🤖 Генерирую контент-план... ⏳")
        
        try:
            content_plan = await self.response_generator.generate_content_plan(
                plan_type=plan_data['content_plan_type'],
                niche=plan_data['content_plan_niche'],
                tone=plan_data['content_plan_tone'],
                posts_per_week=plan_data.get('content_plan_posts_count', 7)
            )
            
            plan_name = f"{plan_data['content_plan_type']} план - {plan_data['content_plan_niche']}"
            
            cursor = self.db.execute_with_datetime('''
                INSERT INTO content_plans 
                (user_id, plan_name, plan_type, start_date, end_date, plan_data)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (
                user.id,
                plan_name,
                plan_data['content_plan_type'],
                datetime.now().date().isoformat(),
                (datetime.now() + timedelta(days=30 if plan_data['content_plan_type'] == 'monthly' else 7)).date().isoformat(),
                json.dumps(content_plan, ensure_ascii=False)
            ))
            self.db.conn.commit()
            
            await self.show_content_plan(update, context, content_plan, plan_name)
            context.user_data.clear()
            
        except Exception as e:
            logger.error(f"❌ Ошибка создания контент-плана: {e}")
            await update.effective_message.reply_text("❌ Ошибка при создании контент-плана")

    async def show_content_plan(self, update: Update, context: ContextTypes.DEFAULT_TYPE, content_plan: dict, plan_name: str):
        plan_text = f"📅 **{plan_name}**\n\n"
        
        for i, post in enumerate(content_plan.get('plan', [])[:5]):
            if 'day' in post:
                plan_text += f"**{post['day']}**\n"
            elif 'date' in post:
                plan_text += f"**{post['date']}**\n"
            
            plan_text += f"🎯 Тема: {post.get('topic', 'Без темы')}\n"
            plan_text += f"💡 Идея: {post.get('main_idea', 'Без описания')}\n\n"
        
        plan_text += "✅ Контент-план сохранен!"
        
        keyboard = [
            [InlineKeyboardButton("📋 Мои контент-планы", callback_data="my_content_plans")],
            [InlineKeyboardButton("↩️ Главное меню", callback_data="main_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.effective_message.reply_text(
            plan_text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

# === ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ===
async def get_user_display_name(user, message) -> str:
    """Получение отображаемого имени пользователя"""
    try:
        # Пробуем получить полное имя
        if user.first_name and user.first_name.lower() != "telegram":
            if user.last_name:
                return f"{user.first_name} {user.last_name}"
            return user.first_name
        
        # Пробуем username
        if user.username:
            return f"@{user.username}"
        
        # Для анонимных пользователей в каналах
        if hasattr(message, 'sender_chat') and message.sender_chat:
            return message.sender_chat.title or "участник канала"
            
        return "друг"
    except Exception:
        return "пользователь"

async def safe_reply_to_message(message, reply_text: str, username: str):
    """Безопасная отправка ответа на сообщение"""
    try:
        # Проверяем, существует ли сообщение для ответа
        if message and message.message_id:
            await message.reply_text(reply_text)
            logger.info(f"💬 Ответ отправлен {username}: {reply_text[:50]}...")
        else:
            # Если сообщение недоступно, отправляем в тот же чат
            await message.chat.send_message(reply_text)
            logger.info(f"💬 Ответ отправлен в чат для {username}: {reply_text[:50]}...")
            
    except Exception as e:
        logger.error(f"❌ Не удалось отправить ответ для {username}: {e}")
        # Пробуем отправить без reply
        try:
            await message.chat.send_message(reply_text)
            logger.info(f"💬 Ответ отправлен без reply для {username}")
        except Exception as e2:
            logger.error(f"❌ Критическая ошибка отправки для {username}: {e2}")

async def save_user_activity(db, user, username: str):
    """Сохранение активности пользователя"""
    try:
        db_username = user.username or user.first_name or "anonymous"
        cursor = db.execute_with_datetime('''
            INSERT OR REPLACE INTO user_activity 
            (user_id, username, first_seen, last_activity, messages_count)
            VALUES (?, ?, COALESCE((SELECT first_seen FROM user_activity WHERE user_id = ?), ?), ?, 
            COALESCE((SELECT messages_count FROM user_activity WHERE user_id = ?), 0) + 1)
        ''', (user.id, db_username, user.id, datetime.now().date().isoformat(), 
              datetime.now().date().isoformat(), user.id))
        db.conn.commit()
    except Exception as e:
        logger.error(f"❌ Ошибка сохранения активности пользователя {user.id}: {e}")

async def handle_message_error(message, user, error):
    """Обработка ошибок при отправке сообщений"""
    error_msg = str(error).lower()
    
    if "message to be replied not found" in error_msg:
        logger.warning(f"⚠️ Сообщение для ответа не найдено для пользователя {user.id}")
        return
    
    if "bot was blocked" in error_msg:
        logger.warning(f"⚠️ Бот заблокирован пользователем {user.id}")
        return
        
    # Для других ошибок пробуем отправить уведомление
    try:
        if user.first_name and user.first_name.lower() != "telegram":
            await message.reply_text(f"Спасибо, {user.first_name}! 😊")
        else:
            await message.reply_text("Спасибо за ваш комментарий! 🌟")
    except Exception:
        pass

def clean_message_text(text: str) -> str:
    """Очистка текста сообщения от артефактов"""
    if not text:
        return ""
    
    # Удаляем лишние символы в начале и конце
    text = text.strip()
    
    # Удаляем артефакты типа ___ в начале
    text = re.sub(r'^_+\s*', '', text)
    
    # Удаляем лишние кавычки
    text = re.sub(r'^["\']+|\s*["\']+$', '', text)
    
    # Удаляем лишние пробелы
    text = re.sub(r'\s+', ' ', text)
    
    return text.strip()

# === ОБРАБОТЧИКИ СООБЩЕНИЙ ===
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if not message or not message.text:
        return

    user = message.from_user
    text = clean_message_text(message.text)
    
    logger.info(f"💬 Сообщение от {user.first_name or user.id}: {text[:50]}...")

    if str(message.chat.id) == CHANNEL_ID:
        await handle_channel_comment(update, context)
        return

    if not await context.application.context_data['rate_limiter'].check_limit(user.id):
        try:
            await message.reply_text("⚠️ Слишком много сообщений. Подождите немного.")
            return
        except:
            pass
        return

    await context.application.context_data['rate_limiter'].record_message(user.id, text)

    is_spam, spam_score = await context.application.context_data['moderation'].advanced_spam_check(text, user.id)
    
    if is_spam:
        await handle_spam(message, user, spam_score)
    else:
        await handle_legitimate_message(message, user, text, context)

async def handle_channel_comment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    user = message.from_user
    text = clean_message_text(message.text)
    
    if not user or not text:
        return
        
    logger.info(f"💬 Комментарий в канале от {user.first_name or 'анонимного пользователя'}: {text[:50]}...")

    is_spam, spam_score = await context.application.context_data['moderation'].advanced_spam_check(text, user.id)
    
    if is_spam:
        await handle_spam(message, user, spam_score)
        return

    if not await context.application.context_data['rate_limiter'].check_limit(user.id):
        logger.info(f"⏰ Rate limit для комментария от {user.first_name or 'анонимного пользователя'}")
        return

    await context.application.context_data['rate_limiter'].record_message(user.id, text)

    try:
        username = await get_user_display_name(user, message)
        
        logger.info(f"🤖 Генерация ответа на комментарий от {username}")
        
        reply_text = await context.application.context_data['response_generator'].generate_context_aware_reply(
            text, user.id, username
        )
        
        # Безопасная отправка ответа
        await safe_reply_to_message(message, reply_text, username)
        
        # Сохраняем активность
        await save_user_activity(context.application.context_data['db'], user, username)
        
    except Exception as e:
        logger.error(f"❌ Ошибка отправки ответа на комментарий: {e}")

async def handle_spam(message, user, spam_score):
    try:
        await message.delete()
        logger.warning(f"🛡️ Удален спам от {user.first_name or user.id} (score: {spam_score})")
        
        if spam_score >= 5 and Config.NOTIFY_ON_SPAM:
            notification_system = message._bot.context_data.get('notification_system')
            if notification_system:
                notification = f"🚨 Высокий уровень спама (score: {spam_score})\nОт: {user.first_name or user.id}\nТекст: {message.text[:100]}..."
                await notification_system.notify_admins(notification)
            
    except Exception as e:
        logger.error(f"❌ Не удалось удалить спам: {e}")

async def handle_legitimate_message(message, user, text, context):
    """Обработка легитимных сообщений с улучшенной обработкой ошибок"""
    try:
        username = await get_user_display_name(user, message)
        
        logger.info(f"🤖 Генерация ответа для {username} на сообщение: {text[:50]}...")
        
        reply_text = await context.application.context_data['response_generator'].generate_context_aware_reply(
            text, user.id, username
        )
        
        # Безопасная отправка ответа
        await safe_reply_to_message(message, reply_text, username)
        
        # Сохраняем в базу
        await save_user_activity(context.application.context_data['db'], user, username)
        
    except Exception as e:
        logger.error(f"❌ Ошибка обработки сообщения от {user.id}: {e}")
        await handle_message_error(message, user, e)

# === ОБРАБОТЧИК ВСЕХ СООБЩЕНИЙ ===
async def handle_all_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Главный обработчик всех сообщений"""
    
    # Обработка контент-планов
    if context.user_data.get('content_plan_stage'):
        stage = context.user_data.get('content_plan_stage')
        if stage in ['niche', 'posts_count']:
            await context.application.context_data['content_plan_manager'].handle_content_plan_creation(update, context)
            return
    
    # Обработка создания постов
    if context.user_data.get('creating_post'):
        stage = context.user_data.get('post_stage')
        if stage in ['topic', 'main_idea', 'schedule_time']:
            await context.application.context_data['post_creator'].handle_post_creation(update, context)
            return
    
    # Обработка обычных сообщений
    await handle_message(update, context)

# === КОМАНДЫ БОТА ===
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = """🚀 **Добро пожаловать в MamaAI Бота!**

🤖 Я помогаю автоматизировать модерацию и взаимодействие в Telegram-каналах.

**Что я умею:**
🛡️ Автоматически находить и удалять спам
💬 Отвечать на комментарии участников
📢 Публиковать ежедневные посты
📊 Собирать статистику активности
🤖 Генерировать посты с помощью ИИ
📅 Создавать контент-планы

Выберите действие из меню ниже:"""
    
    reply_markup = get_main_menu_keyboard()
    
    if update.message:
        await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode='Markdown')
    elif update.callback_query:
        await update.callback_query.edit_message_text(welcome_text, reply_markup=reply_markup, parse_mode='Markdown')

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        db = context.application.context_data['db']
        cache = context.application.context_data['cache']
        
        cursor = db.conn.cursor()
        
        cursor.execute('SELECT COUNT(*) FROM message_history')
        total_messages = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM message_history WHERE is_spam = TRUE')
        spam_blocked = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(DISTINCT user_id) FROM user_activity')
        unique_users = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM scheduled_posts WHERE status = "scheduled"')
        scheduled_posts = cursor.fetchone()[0]
        
        cache_stats = cache.get_stats()
        
        stats_text = f"""📊 **Статистика бота**

• 💬 Всего сообщений: {total_messages}
• 🛡️ Заблокировано спама: {spam_blocked}
• 👥 Уникальных пользователей: {unique_users}
• 📅 Запланировано постов: {scheduled_posts}
• 💾 Эффективность кэша: {cache_stats['hit_rate']:.1%}
• ⏰ Авто-посты: активны

Всё работает отлично! ✅"""
        
        if update.message:
            await update.message.reply_text(stats_text, parse_mode='Markdown')
        elif update.callback_query:
            await update.callback_query.edit_message_text(stats_text, parse_mode='Markdown')
            
    except Exception as e:
        logger.error(f"❌ Ошибка получения статистики: {e}")
        error_text = "❌ Ошибка при получении статистики"
        if update.message:
            await update.message.reply_text(error_text)
        elif update.callback_query:
            await update.callback_query.edit_message_text(error_text)

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from config import MORNING_POST_TIME, EVENING_POST_TIME
    
    status_text = f"""🖥 **Статус системы**

• 🤖 Модель ИИ: Загружена
• 💾 База данных: Активна
• 📢 Авто-посты: Работают
• 🛡️ Модерация: Включена
• 🌐 Сеть: Стабильная

⏰ Расписание постов:
• Утренние: {MORNING_POST_TIME.strftime('%H:%M')}
• Вечерние: {EVENING_POST_TIME.strftime('%H:%M')}

🕐 Время сервера: {datetime.now().strftime('%H:%M:%S')}"""
    
    if update.message:
        await update.message.reply_text(status_text, parse_mode='Markdown')
    elif update.callback_query:
        await update.callback_query.edit_message_text(status_text, parse_mode='Markdown')

async def test_post_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        post_text = f"🧪 **Тестовый пост**\n\nОпубликован в {datetime.now().strftime('%H:%M:%S')}\n\nБот работает корректно! ✅"
        
        success = await send_message_with_fallback(context.application, CHANNEL_ID, post_text)
        
        if success:
            await update.message.reply_text("✅ Тестовый пост опубликован в канале!")
            logger.info("📢 Тестовый пост отправлен")
        else:
            await update.message.reply_text("❌ Не удалось опубликовать тестовый пост")
            logger.error("❌ Ошибка отправки тестового поста")
            
    except Forbidden as e:
        if "bot is not a member" in str(e):
            await update.message.reply_text(
                "❌ Бот не добавлен в канал!\n\n"
                "Добавьте бота в канал как администратора:\n"
                "1. Зайдите в настройки канала\n"
                "2. Выберите 'Администраторы'\n"
                "3. Добавьте бота @MamaAIBot\n"
                "4. Дайте права на отправку сообщений"
            )
        else:
            await update.message.reply_text("❌ Ошибка при публикации поста")
        logger.error(f"❌ Ошибка тестового поста: {e}")
    except Exception as e:
        logger.error(f"❌ Ошибка тестового поста: {e}")
        await update.message.reply_text("❌ Ошибка при публикации поста")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from config import MORNING_POST_TIME, EVENING_POST_TIME
    
    help_text = f"""🤖 **Помощь по MamaAI Боту**

**Основные команды:**
/start - главное меню
/stats - статистика бота
/status - статус системы
/test_post - тестовый пост
/create_post - создать пост с ИИ
/content_plan - создать контент-план
/scheduled_posts - запланированные посты
/check_permissions - проверить права бота
/help - эта справка

**Автоматические функции:**
• Модерация спама (AI проверка)
• Ответы на комментарии (AI генерация)
• Утренние посты: {MORNING_POST_TIME.strftime('%H:%M')}
• Вечерние посты: {EVENING_POST_TIME.strftime('%H:%M')}
• Сбор статистики

Бот работает полностью автоматически! 🎯"""
    
    if update.message:
        await update.message.reply_text(help_text, parse_mode='Markdown')
    elif update.callback_query:
        await update.callback_query.edit_message_text(help_text, parse_mode='Markdown')

async def create_post_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['creating_post'] = True
    context.user_data['post_stage'] = 'topic'
    
    if update.message:
        await update.message.reply_text(
            "🤖 **Создание поста с помощью ИИ**\n\n"
            "📝 **Шаг 1 из 5:** Введите тему поста:",
            parse_mode='Markdown'
        )
    elif update.callback_query:
        await update.callback_query.edit_message_text(
            "🤖 **Создание поста с помощью ИИ**\n\n"
            "📝 **Шаг 1 из 5:** Введите тему поста:",
            parse_mode='Markdown'
        )

async def content_plan_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reply_markup = get_content_plan_type_keyboard()
    
    if update.message:
        await update.message.reply_text(
            "📅 **Создание контент-плана**\n\n"
            "Выберите тип контент-плана:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    elif update.callback_query:
        await update.callback_query.edit_message_text(
            "📅 **Создание контент-плана**\n\n"
            "Выберите тип контент-плана:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

async def scheduled_posts_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        stats = await context.application.context_data['post_scheduler'].get_scheduled_posts_stats()
        
        response = "📅 **Запланированные посты**\n\n"
        
        if stats['upcoming_posts']:
            response += "⏰ **Ближайшие посты:**\n"
            for post in stats['upcoming_posts']:
                response += f"• {post['time']}: {post['topic']}\n"
            response += "\n"
        else:
            response += "📭 Нет запланированных постов\n\n"
        
        response += "📊 **Статистика:**\n"
        for status, count in stats['stats'].items():
            status_emoji = {
                'scheduled': '⏰',
                'published': '✅', 
                'error': '❌'
            }.get(status, '📝')
            response += f"• {status_emoji} {status}: {count}\n"
        
        if update.message:
            await update.message.reply_text(response, parse_mode='Markdown')
        elif update.callback_query:
            await update.callback_query.edit_message_text(response, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"❌ Ошибка получения запланированных постов: {e}")
        error_text = "❌ Ошибка при получении запланированных постов"
        if update.message:
            await update.message.reply_text(error_text)
        elif update.callback_query:
            await update.callback_query.edit_message_text(error_text)

async def check_permissions_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Проверка прав бота в канале"""
    try:
        if update.message:
            await update.message.reply_text("🔍 Проверяю права бота в канале...")
        elif update.callback_query:
            await update.callback_query.edit_message_text("🔍 Проверяю права бота в канале...")
        
        has_permissions = await check_bot_permissions(context.application)
        
        if has_permissions:
            response = (
                "✅ **Права бота в порядке!**\n\n"
                "Бот является администратором канала и может публиковать посты."
            )
        else:
            response = (
                "❌ **Проблема с правами бота!**\n\n"
                "Добавьте бота в канал как администратора:\n"
                "1. Зайдите в настройки канала\n"
                "2. Выберите 'Администраторы'\n" 
                "3. Добавьте бота @MamaAIBot\n"
                "4. Дайте права на отправку сообщений\n\n"
                "После этого используйте /check_permissions снова."
            )
        
        if update.message:
            await update.message.reply_text(response, parse_mode='Markdown')
        elif update.callback_query:
            await update.callback_query.edit_message_text(response, parse_mode='Markdown')
            
    except Exception as e:
        logger.error(f"❌ Ошибка проверки прав: {e}")
        error_text = "❌ Ошибка при проверке прав"
        if update.message:
            await update.message.reply_text(error_text)
        elif update.callback_query:
            await update.callback_query.edit_message_text(error_text)

async def force_check_scheduled_posts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Принудительная проверка запланированных постов"""
    try:
        await update.message.reply_text("🔍 Проверяю запланированные посты...")
        await context.application.context_data['post_scheduler']._check_scheduled_posts()
        await update.message.reply_text("✅ Проверка завершена")
    except Exception as e:
        logger.error(f"❌ Ошибка принудительной проверки: {e}")
        await update.message.reply_text("❌ Ошибка при проверке")

async def force_auto_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Принудительная публикация авто-поста"""
    try:
        post_type = context.args[0] if context.args else "morning"
        
        if post_type not in ["morning", "evening"]:
            await update.message.reply_text("❌ Используйте: /force_auto_post morning или /force_auto_post evening")
            return
        
        auto_post_scheduler = context.application.context_data['auto_post_scheduler']
        await auto_post_scheduler._publish_post(post_type)
        await update.message.reply_text(f"✅ {post_type} пост опубликован принудительно")
        
    except Exception as e:
        logger.error(f"❌ Ошибка принудительной публикации: {e}")
        await update.message.reply_text("❌ Ошибка при публикации поста")

# === ОБРАБОТЧИКИ CALLBACK ===
async def handle_all_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user = query.from_user
    data = query.data
    
    logger.info(f"🔔 Callback: {data} от {user.id}")
    
    if data in ["stats", "status", "auto_posts", "create_post", "content_plan", "help", "scheduled_posts", "check_permissions", "main_menu"]:
        await handle_main_menu_callback(update, context)
    elif any(data.startswith(prefix) for prefix in ["tone_", "length_", "emojis_", "publish_now", "schedule_later"]):
        await handle_post_creation_callback(update, context)
    else:
        await handle_content_plan_callback(update, context)

async def handle_main_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    
    try:
        if data == "stats":
            await stats_command(update, context)
        elif data == "status":
            await status_command(update, context)
        elif data == "auto_posts":
            await query.edit_message_text("🤖 Авто-посты работают по расписанию! ✅", parse_mode='Markdown')
        elif data == "create_post":
            await create_post_command(update, context)
        elif data == "content_plan":
            await content_plan_command(update, context)
        elif data == "scheduled_posts":
            await scheduled_posts_command(update, context)
        elif data == "check_permissions":
            await check_permissions_command(update, context)
        elif data == "help":
            await help_command(update, context)
        elif data == "main_menu":
            await start_command(update, context)
    except Exception as e:
        logger.error(f"❌ Ошибка в обработчике меню: {e}")
        await query.edit_message_text("❌ Произошла ошибка при обработке запроса")

async def handle_post_creation_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    
    if data.startswith('tone_'):
        tone = data.split('_')[1]
        context.user_data['post_tone'] = tone
        context.user_data['post_stage'] = 'main_idea'
        
        await query.edit_message_text(
            "💡 **Шаг 3 из 5:** Введите основную мысль поста:",
            parse_mode='Markdown'
        )
    
    elif data.startswith('length_'):
        length = data.split('_')[1]
        context.user_data['post_length'] = length
        context.user_data['post_stage'] = 'emojis'
        
        keyboard = [
            [InlineKeyboardButton("✅ Со смайликами", callback_data="emojis_yes")],
            [InlineKeyboardButton("❌ Без смайликов", callback_data="emojis_no")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "😊 **Шаг 5 из 5:** Использовать смайлики в посте?",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    
    elif data.startswith('emojis_'):
        use_emojis = data.split('_')[1] == 'yes'
        context.user_data['post_emojis'] = use_emojis
        
        await query.edit_message_text("🤖 Генерирую пост... ⏳")
        
        try:
            generated_post = await context.application.context_data['response_generator'].generate_post(
                topic=context.user_data['post_topic'],
                tone=context.user_data['post_tone'],
                main_idea=context.user_data['post_main_idea'],
                use_emojis=use_emojis,
                length=context.user_data['post_length']
            )
            
            context.user_data['generated_post'] = generated_post
            context.user_data['post_stage'] = 'schedule'
            
            keyboard = [
                [InlineKeyboardButton("⏰ Опубликовать сейчас", callback_data="publish_now")],
                [InlineKeyboardButton("📅 Отложить публикацию", callback_data="schedule_later")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                f"📝 **Сгенерированный пост:**\n\n{generated_post}\n\nВыберите действие:",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
            
        except Exception as e:
            logger.error(f"❌ Ошибка генерации поста: {e}")
            await query.edit_message_text("❌ Ошибка при генерации поста")
    
    elif data == 'publish_now':
        try:
            success = await send_message_with_fallback(context.application, CHANNEL_ID, context.user_data['generated_post'])
            
            if success:
                cursor = context.application.context_data['db'].execute_with_datetime('''
                    INSERT INTO scheduled_posts 
                    (user_id, post_text, scheduled_time, channel_id, tone, topic, length, main_idea, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    query.from_user.id,
                    context.user_data['generated_post'],
                    datetime.now(),
                    CHANNEL_ID,
                    context.user_data['post_tone'],
                    context.user_data['post_topic'],
                    context.user_data['post_length'],
                    context.user_data['post_main_idea'],
                    'published'
                ))
                context.application.context_data['db'].conn.commit()
                
                context.user_data.clear()
                await query.edit_message_text("✅ Пост успешно опубликован!", parse_mode='Markdown')
            else:
                await query.edit_message_text("❌ Не удалось опубликовать пост")
            
        except Forbidden as e:
            if "bot is not a member" in str(e):
                await query.edit_message_text(
                    "❌ Бот не добавлен в канал!\n\n"
                    "Добавьте бота в канал как администратора:\n"
                    "1. Зайдите в настройки канала\n"
                    "2. Выберите 'Администраторы'\n"
                    "3. Добавьте бота @MamaAIBot\n"
                    "4. Дайте права на отправку сообщений\n\n"
                    "После этого попробуйте снова."
                )
            else:
                await query.edit_message_text("❌ Ошибка при публикации поста")
            logger.error(f"❌ Ошибка публикации: {e}")
        except Exception as e:
            logger.error(f"❌ Ошибка публикации: {e}")
            await query.edit_message_text("❌ Ошибка при публикации поста")
    
    elif data == 'schedule_later':
        context.user_data['post_stage'] = 'schedule_time'
        await query.edit_message_text(
            "⏰ **Планирование публикации**\n\n"
            "Введите время публикации:\n\n"
            "• **Сейчас** - опубликовать немедленно\n"
            "• **Через 2 часа** - через указанное время\n"
            "• **Завтра 15:30** - конкретное время",
            parse_mode='Markdown'
        )

async def handle_content_plan_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    
    if data == "content_plan_weekly":
        await context.application.context_data['content_plan_manager'].create_content_plan(update, context, "weekly")
    elif data == "content_plan_monthly":
        await context.application.context_data['content_plan_manager'].create_content_plan(update, context, "monthly")
    elif data.startswith('plan_tone_'):
        tone = data.split('_')[2]
        context.user_data['content_plan_tone'] = tone
        context.user_data['content_plan_stage'] = 'posts_count'
        
        await query.edit_message_text(
            "�� **Шаг 3 из 3:** Введите количество постов (1-50):",
            parse_mode='Markdown'
        )

# === ОБРАБОТЧИК ОШИБОК ===
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик ошибок"""
    logger.error(f"❌ Ошибка: {context.error}", exc_info=context.error)
    
    try:
        if update and update.callback_query:
            await update.callback_query.edit_message_text(
                "❌ Произошла ошибка при обработке запроса. Попробуйте еще раз."
            )
        elif update and update.message:
            await update.message.reply_text(
                "❌ Произошла ошибка при обработке запроса. Попробуйте еще раз."
            )
    except Exception as e:
        logger.error(f"❌ Ошибка в обработчике ошибок: {e}")

# === НАСТРОЙКА ОБРАБОТЧИКОВ ===
def setup_handlers(app: Application):
    """Настройка всех обработчиков"""
    
    # Добавляем обработчик ошибок
    app.add_error_handler(error_handler)
    
    # Обработчики команд
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("test_post", test_post_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("create_post", create_post_command))
    app.add_handler(CommandHandler("content_plan", content_plan_command))
    app.add_handler(CommandHandler("scheduled_posts", scheduled_posts_command))
    app.add_handler(CommandHandler("check_permissions", check_permissions_command))
    app.add_handler(CommandHandler("force_check", force_check_scheduled_posts))
    
    # Универсальный обработчик callback
    app.add_handler(CallbackQueryHandler(handle_all_callbacks))
    
    # Обработчик всех текстовых сообщений
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & (
            filters.ChatType.PRIVATE | 
            filters.ChatType.GROUPS | 
            filters.ChatType.SUPERGROUP
        ), 
        handle_all_messages
    ))
