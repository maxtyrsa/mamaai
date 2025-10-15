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

# Вспомогательные функции для фильтрации
async def is_admin_user(bot, user_id: int, chat_id: str) -> bool:
    """Проверяет, является ли пользователь администратором канала"""
    try:
        administrators = await bot.get_chat_administrators(chat_id)
        admin_ids = [admin.user.id for admin in administrators if admin.user]
        return user_id in admin_ids
    except Exception as e:
        logger.error(f"❌ Ошибка проверки прав администратора: {e}")
        return False

async def is_channel_post(message) -> bool:
    """Проверяет, является ли сообщение постом канала"""
    if not message:
        return False
    
    # Проверяем, что сообщение отправлено от имени канала
    if hasattr(message, 'sender_chat') and message.sender_chat:
        return message.sender_chat.id == int(CHANNEL_ID)
    
    return False

def is_auto_post_message(text: str) -> bool:
    """Улучшенная проверка на авто-посты бота"""
    if not text:
        return False
    
    text_lower = text.lower().strip()
    
    # Только точные совпадения с началом авто-постов
    auto_post_starts = [
        "просыпайтесь с улыбкой",
        "новый день - новые возможности",
        "доброе утро",
        "вечер накрывает город",
        "спокойной ночи", 
        "🧪 тестовый пост",
        "☀️ новое утро — новые возможности",
        "🌙 вечер наступает",
        "✨ день подходит к концу",
        "🌅 утро — время планировать"
    ]
    
    # Проверяем только начало сообщения для точного определения
    for phrase in auto_post_starts:
        if text_lower.startswith(phrase.lower()):
            logger.info(f"⏩ Точное совпадение с авто-постом: {phrase}")
            return True
    
    # Дополнительные проверки с более строгими условиями
    morning_indicators = ['утро', 'утрен', 'доброе утро', 'просыпай', 'новый день', 'солнц', 'рассвет']
    evening_indicators = ['вечер', 'ночь', 'сон', 'отдых', 'спокойной', 'закат', 'луна', 'звезд', 'расслаб', 'восстанов']
    
    # Считаем сообщение авто-постом только если есть несколько индикаторов И текст похож на авто-пост
    morning_count = sum(1 for indicator in morning_indicators if indicator in text_lower)
    evening_count = sum(1 for indicator in evening_indicators if indicator in text_lower)
    
    # Только если есть несколько индикаторов И текст достаточно длинный (как авто-пост)
    if (morning_count >= 2 or evening_count >= 2) and len(text) > 100:
        logger.info(f"⏩ Определен как авто-пост по индикаторам: утро={morning_count}, вечер={evening_count}")
        return True
    
    return False

async def should_process_message(message) -> bool:
    """Улучшенная проверка необходимости обработки сообщения"""
    if not message or not message.text:
        logger.info("⏩ Пропущено пустое сообщение")
        return False
    
    text = message.text.strip()
    if not text:
        logger.info("⏩ Пропущено сообщение с пустым текстом")
        return False
    
    user_info = f"{message.from_user.first_name or 'Аноним'} ({message.from_user.id})"
    
    # Пропускаем посты канала
    if await is_channel_post(message):
        logger.info(f"⏩ Пропущен пост канала от {user_info}: {text[:50]}...")
        return False
    
    # Пропускаем автоматические посты бота (более точная проверка)
    if is_auto_post_message(text):
        logger.info(f"⏩ Пропущен авто-пост от {user_info}: {text[:50]}...")
        return False
    
    # Пропускаем очень короткие сообщения (возможно, артефакты)
    if len(text) < 2:
        logger.info(f"⏩ Пропущено слишком короткое сообщение от {user_info}: '{text}'")
        return False
    
    logger.info(f"✅ Сообщение будет обработано от {user_info}: {text[:50]}...")
    return True

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

    async def generate_post_from_plan(self, update: Update, context: ContextTypes.DEFAULT_TYPE, plan_id: int, post_index: int = 0):
        """Генерация поста из контент-плана"""
        user = update.effective_user
        query = update.callback_query
        
        try:
            # Получаем контент-план из базы
            cursor = self.db.conn.cursor()
            cursor.execute('''
                SELECT plan_data FROM content_plans 
                WHERE id = ? AND user_id = ?
            ''', (plan_id, user.id))
            
            result = cursor.fetchone()
            if not result:
                await query.edit_message_text("❌ Контент-план не найден")
                return
            
            plan_data = json.loads(result[0]) if result[0] else {}
            posts = plan_data.get('plan', [])
            
            if not posts:
                await query.edit_message_text("❌ В контент-плане нет постов")
                return
            
            if post_index >= len(posts):
                await query.edit_message_text("❌ Указанный пост не найден в плане")
                return
            
            post_data = posts[post_index]
            
            await query.edit_message_text("🤖 Генерирую пост из контент-плана... ⏳")
            
            # Генерируем пост на основе данных из контент-плана
            generated_post = await self.response_generator.generate_post_from_plan_data(post_data)
            
            if not generated_post:
                await query.edit_message_text("❌ Не удалось сгенерировать пост")
                return
            
            # Предлагаем действия с постом
            keyboard = [
                [InlineKeyboardButton("⏰ Опубликовать сейчас", callback_data=f"publish_plan_post_{plan_id}_{post_index}")],
                [InlineKeyboardButton("📅 Запланировать", callback_data=f"schedule_plan_post_{plan_id}_{post_index}")],
                [InlineKeyboardButton("🔄 Сгенерировать другой", callback_data=f"regenerate_plan_post_{plan_id}_{post_index}")],
                [InlineKeyboardButton("📋 К контент-плану", callback_data=f"plan_nav_{post_index}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            post_info = f"**Тема:** {post_data.get('topic', 'Без темы')}\n"
            post_info += f"**Тип:** {post_data.get('post_type', 'Не указан')}\n"
            post_info += f"**Тон:** {post_data.get('tone', 'Не указан')}\n\n"
            
            await query.edit_message_text(
                f"📝 **Сгенерированный пост из контент-плана:**\n\n"
                f"{post_info}"
                f"{generated_post}\n\n"
                f"Выберите действие:",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
            
        except Exception as e:
            logger.error(f"❌ Ошибка генерации поста из контент-плана: {e}")
            await query.edit_message_text("❌ Ошибка при генерации поста")

    async def publish_plan_post(self, update: Update, context: ContextTypes.DEFAULT_TYPE, plan_id: int, post_index: int):
        """Публикация поста из контент-плана"""
        user = update.effective_user
        query = update.callback_query
        
        try:
            # Получаем контент-план и данные поста
            cursor = self.db.conn.cursor()
            cursor.execute('''
                SELECT plan_data, plan_name FROM content_plans 
                WHERE id = ? AND user_id = ?
            ''', (plan_id, user.id))
            
            result = cursor.fetchone()
            if not result:
                await query.edit_message_text("❌ Контент-план не найден")
                return
            
            plan_data_json, plan_name = result
            plan_data = json.loads(plan_data_json) if plan_data_json else {}
            posts = plan_data.get('plan', [])
            
            if post_index >= len(posts):
                await query.edit_message_text("❌ Указанный пост не найден в плане")
                return
            
            post_data = posts[post_index]
            
            # Генерируем пост
            generated_post = await self.response_generator.generate_post_from_plan_data(post_data)
            
            if not generated_post:
                await query.edit_message_text("❌ Не удалось сгенерировать пост")
                return
            
            # Публикуем пост
            success = await send_message_with_fallback(context.application, CHANNEL_ID, generated_post)
            
            if success:
                # Сохраняем в базу как опубликованный пост
                cursor = self.db.execute_with_datetime('''
                    INSERT INTO scheduled_posts 
                    (user_id, post_text, scheduled_time, channel_id, tone, topic, length, main_idea, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    user.id,
                    generated_post,
                    datetime.now(),
                    CHANNEL_ID,
                    post_data.get('tone', 'friendly'),
                    post_data.get('topic', 'Без темы'),
                    'medium',
                    post_data.get('main_idea', 'Без описания'),
                    'published'
                ))
                self.db.conn.commit()
                
                post_id = cursor.lastrowid
                
                await query.edit_message_text(
                    f"✅ **Пост опубликован!**\n\n"
                    f"📝 Тема: {post_data.get('topic', 'Без темы')}\n"
                    f"📅 Из плана: {plan_name}\n"
                    f"🆔 ID поста: {post_id}\n\n"
                    f"Пост успешно опубликован в канале! 🎉",
                    parse_mode='Markdown'
                )
                logger.info(f"✅ Пост {post_id} из контент-плана {plan_id} опубликован")
            else:
                await query.edit_message_text("❌ Не удалось опубликовать пост")
                
        except Forbidden as e:
            if "bot is not a member" in str(e):
                await query.edit_message_text(
                    "❌ Бот не добавлен в канал!\n\n"
                    "Добавьте бота в канал как администратора."
                )
            else:
                await query.edit_message_text("❌ Ошибка при публикации поста")
            logger.error(f"❌ Ошибка публикации поста из контент-плана: {e}")
        except Exception as e:
            logger.error(f"❌ Ошибка публикации поста из контент-плана: {e}")
            await query.edit_message_text("❌ Ошибка при публикации поста")

    async def schedule_plan_post(self, update: Update, context: ContextTypes.DEFAULT_TYPE, plan_id: int, post_index: int):
        """Планирование поста из контент-плана"""
        user = update.effective_user
        query = update.callback_query
        
        # Сохраняем данные для следующего шага
        context.user_data['scheduling_plan_post'] = True
        context.user_data['plan_id'] = plan_id
        context.user_data['post_index'] = post_index
        
        await query.edit_message_text(
            "⏰ **Планирование публикации поста из контент-плана**\n\n"
            "Введите время публикации:\n\n"
            "• **Сейчас** - опубликовать немедленно\n"
            "• **Через 2 часа** - через указанное время\n"
            "• **Завтра 15:30** - конкретное время\n"
            "• **01.01.2024 10:00** - конкретная дата и время",
            parse_mode='Markdown'
        )

    async def handle_plan_post_scheduling(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка планирования поста из контент-плана"""
        if not context.user_data.get('scheduling_plan_post'):
            return
        
        user = update.effective_user
        message = update.effective_message
        plan_id = context.user_data.get('plan_id')
        post_index = context.user_data.get('post_index')
        
        try:
            schedule_time = self.parse_schedule_time(message.text)
            if not schedule_time:
                await message.reply_text(
                    "❌ Не удалось распознать время. Используйте: 'сейчас', 'через 2 часа', 'завтра 09:00'",
                    parse_mode='Markdown'
                )
                return
            
            # Получаем контент-план и данные поста
            cursor = self.db.conn.cursor()
            cursor.execute('''
                SELECT plan_data, plan_name FROM content_plans 
                WHERE id = ? AND user_id = ?
            ''', (plan_id, user.id))
            
            result = cursor.fetchone()
            if not result:
                await message.reply_text("❌ Контент-план не найден")
                context.user_data.clear()
                return
            
            plan_data_json, plan_name = result
            plan_data = json.loads(plan_data_json) if plan_data_json else {}
            posts = plan_data.get('plan', [])
            
            if post_index >= len(posts):
                await message.reply_text("❌ Указанный пост не найден в плане")
                context.user_data.clear()
                return
            
            post_data = posts[post_index]
            
            # Генерируем пост
            generated_post = await self.response_generator.generate_post_from_plan_data(post_data)
            
            if not generated_post:
                await message.reply_text("❌ Не удалось сгенерировать пост")
                context.user_data.clear()
                return
            
            # Сохраняем запланированный пост
            cursor = self.db.execute_with_datetime('''
                INSERT INTO scheduled_posts 
                (user_id, post_text, scheduled_time, channel_id, tone, topic, length, main_idea, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                user.id,
                generated_post,
                schedule_time,
                CHANNEL_ID,
                post_data.get('tone', 'friendly'),
                post_data.get('topic', 'Без темы'),
                'medium',
                post_data.get('main_idea', 'Без описания'),
                'scheduled'
            ))
            self.db.conn.commit()
            
            post_id = cursor.lastrowid
            
            context.user_data.clear()
            
            await message.reply_text(
                f"✅ **Пост запланирован!**\n\n"
                f"📝 Тема: {post_data.get('topic', 'Без темы')}\n"
                f"📅 Из плана: {plan_name}\n"
                f"⏰ Время: {schedule_time.strftime('%d.%m.%Y %H:%M')}\n"
                f"🆔 ID поста: {post_id}\n\n"
                f"Пост будет опубликован автоматически в указанное время! 🎉",
                parse_mode='Markdown'
            )
            logger.info(f"✅ Пост {post_id} из контент-плана {plan_id} запланирован на {schedule_time}")
            
        except Exception as e:
            logger.error(f"❌ Ошибка планирования поста из контент-плана: {e}")
            await message.reply_text("❌ Ошибка при планировании поста")
            context.user_data.clear()

class ContentPlanManager:
    def __init__(self, response_generator, db):
        self.response_generator = response_generator
        self.db = db
    
    async def create_content_plan(self, update: Update, context: ContextTypes.DEFAULT_TYPE, plan_type: str):
        user = update.effective_user
        
        context.user_data['content_plan_type'] = plan_type
        context.user_data['content_plan_stage'] = 'niche'
        
        await update.callback_query.edit_message_text(
            f"📅 **Создание {plan_type} контент-плана**\n\n"
            "🎯 **Шаг 1 из 4:** Введите нишу вашего канала:",
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
            context.user_data['content_plan_stage'] = 'audience'
            
            await message.reply_text(
                "👥 **Шаг 2 из 4:** Опишите целевую аудиторию канала:\n\n"
                "Например: 'молодые предприниматели', 'IT-специалисты', 'студенты' и т.д.",
                parse_mode='Markdown'
            )
        
        elif stage == 'audience':
            context.user_data['content_plan_audience'] = message.text
            context.user_data['content_plan_stage'] = 'tone'
            
            reply_markup = get_tone_keyboard("plan_tone")
            
            await message.reply_text(
                "🎭 **Шаг 3 из 4:** Выберите тон контента:",
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
                posts_per_week=plan_data.get('content_plan_posts_count', 7),
                audience=plan_data.get('content_plan_audience', 'подписчики Telegram-канала'),
                goals=plan_data.get('content_plan_goals', 'вовлечение и рост аудитории')
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
            plan_text += f"📝 Тип: {post.get('post_type', 'Не указан')}\n"
            plan_text += f"💡 Идея: {post.get('main_idea', 'Без описания')}\n"
            plan_text += f"🎭 Тон: {post.get('tone', 'Не указан')}\n"
            plan_text += f"🔗 Вовлечение: {post.get('engagement_elements', 'Не указано')}\n"
            plan_text += f"🏷️ Хештеги: {post.get('hashtags', 'Не указаны')}\n\n"
        
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

    async def get_user_content_plans(self, user_id: int) -> List[Dict]:
        """Получение контент-планов пользователя"""
        cursor = self.db.conn.cursor()
        cursor.execute('''
            SELECT id, plan_name, plan_type, start_date, end_date, plan_data, created_at
            FROM content_plans 
            WHERE user_id = ? AND status = 'active'
            ORDER BY created_at DESC
        ''', (user_id,))
        
        plans = []
        for row in cursor.fetchall():
            plan_id, plan_name, plan_type, start_date, end_date, plan_data, created_at = row
            try:
                plan_json = json.loads(plan_data) if plan_data else {}
                plans.append({
                    'id': plan_id,
                    'name': plan_name,
                    'type': plan_type,
                    'start_date': start_date,
                    'end_date': end_date,
                    'plan_data': plan_json,
                    'created_at': created_at
                })
            except Exception as e:
                logger.error(f"❌ Ошибка парсинга контент-плана {plan_id}: {e}")
        
        return plans

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
       
async def check_messages_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Проверка статуса сообщений в БД"""
    try:
        db = context.bot_data['db']
        cursor = db.conn.cursor()
        
        # Получаем статистику по статусам сообщений
        cursor.execute('''
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN is_spam IS NULL AND response_text IS NULL THEN 1 ELSE 0 END) as unprocessed,
                SUM(CASE WHEN is_spam = 1 THEN 1 ELSE 0 END) as spam,
                SUM(CASE WHEN is_spam = 0 THEN 1 ELSE 0 END) as legitimate,
                SUM(CASE WHEN is_spam IS NOT NULL AND response_text IS NULL THEN 1 ELSE 0 END) as processed_no_response,
                SUM(CASE WHEN is_spam IS NULL AND response_text IS NOT NULL THEN 1 ELSE 0 END) as response_no_spam_flag
            FROM message_history 
            WHERE timestamp >= datetime('now', '-24 hours')
        ''')
        
        stats = cursor.fetchone()
        
        response = "📊 **Статус сообщений за последние 24 часа:**\n\n"
        response += f"• Всего сообщений: {stats[0] or 0}\n"
        response += f"• Необработанных: {stats[1] or 0}\n"
        response += f"• Спам: {stats[2] or 0}\n"
        response += f"• Легитимных: {stats[3] or 0}\n"
        response += f"• Обработано без ответа: {stats[4] or 0}\n"
        response += f"• Ответ без флага спама: {stats[5] or 0}\n\n"
        
        # Показываем несколько примеров каждого типа
        cursor.execute('''
            SELECT id, user_id, message_text, is_spam, response_text
            FROM message_history 
            WHERE timestamp >= datetime('now', '-24 hours')
            ORDER BY timestamp DESC
            LIMIT 10
        ''')
        
        recent_messages = cursor.fetchall()
        
        response += "**Последние 10 сообщений:**\n"
        for msg in recent_messages:
            status = "❓ Необработанное"
            if msg[3] == 1:
                status = "🚫 Спам"
            elif msg[3] == 0:
                status = "✅ Легитимное"
            
            response += f"• ID:{msg[0]} - {status}\n"
            response += f"  Текст: {msg[2][:30]}...\n"
            if msg[4]:
                response += f"  Ответ: {msg[4][:30]}...\n"
        
        await update.message.reply_text(response, parse_mode='Markdown')
        
    except Exception as e:
        logger.error(f"❌ Ошибка проверки статуса сообщений: {e}")
        await update.message.reply_text(f"❌ Ошибка: {e}")

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

async def save_unprocessed_message(context, message):
    """Сохранение непроцессированного сообщения при сетевых ошибках"""
    try:
        if message and message.text and message.from_user:
            cursor = context.bot_data['db'].execute_with_datetime('''
                INSERT INTO message_history 
                (user_id, message_text, timestamp, is_spam, response_text)
                VALUES (?, ?, ?, NULL, NULL)
            ''', (message.from_user.id, message.text, datetime.now()))
            context.bot_data['db'].conn.commit()
            logger.info(f"💾 Сохранено непроцессированное сообщение от {message.from_user.id}")
    except Exception as e:
        logger.error(f"❌ Ошибка сохранения непроцессированного сообщения: {e}")

# === ОБРАБОТЧИКИ СООБЩЕНИЙ ===
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if not message or not message.text:
        return

    user = message.from_user
    
    # Проверяем, нужно ли обрабатывать это сообщение
    if not await should_process_message(message):
        return

    text = clean_message_text(message.text)
    
    logger.info(f"💬 Сообщение от {user.first_name or user.id}: {text[:50]}...")

    if str(message.chat.id) == CHANNEL_ID:
        await handle_channel_comment(update, context)
        return

    if not await context.bot_data['rate_limiter'].check_limit(user.id):
        try:
            await message.reply_text("⚠️ Слишком много сообщений. Подождите немного.")
            return
        except:
            pass
        return

    await context.bot_data['rate_limiter'].record_message(user.id, text)

    is_spam, spam_score = await context.bot_data['moderation'].advanced_spam_check(text, user.id)
    
    if is_spam:
        await handle_spam(message, user, spam_score, context)
    else:
        await handle_legitimate_message(message, user, text, context)

async def handle_channel_comment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    user = message.from_user
    
    # Проверяем, нужно ли обрабатывать это сообщение
    if not await should_process_message(message):
        return
    
    # Пропускаем посты канала
    if await is_channel_post(message):
        logger.info("⏩ Пропущен пост канала")
        return
        
    # Пропускаем сообщения администраторов
    if user and await is_admin_user(context.bot, user.id, CHANNEL_ID):
        logger.info(f"⏩ Пропущено сообщение администратора {user.first_name}")
        return
        
    text = clean_message_text(message.text)
    
    if not user or not text:
        return
    
    # Пропускаем ответы на автоматические посты бота (утренние и вечерние)
    if is_auto_post_message(text):
        logger.info(f"⏩ Пропущен комментарий к авто-посту от {user.first_name}")
        return
        
    logger.info(f"💬 Комментарий в канале от {user.first_name or 'анонимного пользователя'}: {text[:50]}...")

    is_spam, spam_score = await context.bot_data['moderation'].advanced_spam_check(text, user.id)
    
    if is_spam:
        await handle_spam(message, user, spam_score, context)
        return

    if not await context.bot_data['rate_limiter'].check_limit(user.id):
        logger.info(f"⏰ Rate limit для комментария от {user.first_name or 'анонимного пользователя'}")
        return

    await context.bot_data['rate_limiter'].record_message(user.id, text)

    try:
        username = await get_user_display_name(user, message)
        
        logger.info(f"🤖 Генерация ответа на комментарий от {username}")
        
        reply_text = await context.bot_data['response_generator'].generate_context_aware_reply(
            text, user.id, username
        )
        
        # Безопасная отправка ответа
        await safe_reply_to_message(message, reply_text, username)
        
        # Сохраняем активность
        await save_user_activity(context.bot_data['db'], user, username)
        
        # Сохраняем в базу данных как обработанное сообщение
        cursor = context.bot_data['db'].execute_with_datetime('''
            INSERT INTO message_history 
            (user_id, message_text, timestamp, is_spam, response_text)
            VALUES (?, ?, ?, ?, ?)
        ''', (
            user.id, 
            text, 
            datetime.now(), 
            False, 
            reply_text[:500]  # Сохраняем только часть ответа
        ))
        context.bot_data['db'].conn.commit()
        
    except Exception as e:
        logger.error(f"❌ Ошибка отправки ответа на комментарий: {e}")
        # Сохраняем как непроцессированное для последующего восстановления
        await save_unprocessed_message(context, message)

async def handle_spam(message, user, spam_score, context):
    try:
        # Сохраняем сообщение в БД КАК СПАМ перед удалением
        try:
            cursor = context.bot_data['db'].execute_with_datetime('''
                INSERT INTO message_history 
                (user_id, message_text, timestamp, is_spam, response_text)
                VALUES (?, ?, ?, ?, ?)
            ''', (
                user.id, 
                message.text if message.text else "Нет текста", 
                datetime.now(), 
                True, 
                f"Удалено как спам (score: {spam_score:.1f})"
            ))
            context.bot_data['db'].conn.commit()
            logger.info(f"💾 Спам сообщение сохранено в БД для пользователя {user.id}")
        except Exception as db_error:
            logger.error(f"❌ Ошибка сохранения спама в БД: {db_error}")

        # Теперь удаляем сообщение
        await message.delete()
        logger.warning(f"🛡️ Удален спам от {user.first_name or user.id} (score: {spam_score:.1f})")
        
        # Обновляем статистику пользователя в системе модерации
        moderation = context.bot_data['moderation']
        user_stats = moderation.get_user_stats(user.id)
        
        # Уведомление администраторов
        if Config.NOTIFY_ON_SPAM:
            notification_system = context.bot_data.get('notification_system')
            if notification_system:
                notification = (
                    f"🚨 Обнаружен спам (score: {spam_score:.1f})\n"
                    f"👤 От: {user.first_name or user.id}\n"
                    f"📊 Уровень доверия: {user_stats['trust_level']}\n"
                    f"📝 Текст: {message.text[:100]}...\n"
                    f"✅ Сообщение удалено и сохранено в статистике"
                )
                await notification_system.notify_admins(notification)
        
        # Автоматический бан при множественных нарушениях
        if user_stats['warning_count'] >= 2:
            try:
                await context.bot.ban_chat_member(
                    chat_id=CHANNEL_ID,
                    user_id=user.id,
                    until_date=datetime.now() + timedelta(days=1)
                )
                logger.warning(f"🔨 Пользователь {user.id} забанен на 1 день")
                
                if notification_system:
                    ban_notification = (
                        f"🔨 Пользователь забанен за спам\n"
                        f"👤 ID: {user.id}\n"
                        f"📛 Имя: {user.first_name or 'Неизвестно'}\n"
                        f"⚠️ Предупреждений: {user_stats['warning_count']}\n"
                        f"📊 Уровень доверия: {user_stats['trust_level']}"
                    )
                    await notification_system.notify_admins(ban_notification)
                    
            except Exception as ban_error:
                logger.error(f"❌ Не удалось забанить пользователя {user.id}: {ban_error}")
            
    except Forbidden as e:
        logger.error(f"❌ Нет прав для удаления сообщения от {user.id}: {e}")
    except Exception as e:
        logger.error(f"❌ Не удалось удалить спам: {e}")

async def handle_legitimate_message(message, user, text, context):
    """Обработка легитимных сообщений с улучшенной обработкой ошибок"""
    try:
        username = await get_user_display_name(user, message)
        
        logger.info(f"🤖 Генерация ответа для {username} на сообщение: {text[:50]}...")
        
        reply_text = await context.bot_data['response_generator'].generate_context_aware_reply(
            text, user.id, username
        )
        
        # Сохраняем сообщение в БД как НЕ спам
        try:
            cursor = context.bot_data['db'].execute_with_datetime('''
                INSERT INTO message_history 
                (user_id, message_text, timestamp, is_spam, response_text)
                VALUES (?, ?, ?, ?, ?)
            ''', (
                user.id, 
                text, 
                datetime.now(), 
                False, 
                reply_text[:500]  # Сохраняем только часть ответа
            ))
            context.bot_data['db'].conn.commit()
        except Exception as db_error:
            logger.error(f"❌ Ошибка сохранения сообщения в БД: {db_error}")
        
        # Безопасная отправка ответа
        await safe_reply_to_message(message, reply_text, username)
        
        # Сохраняем активность
        await save_user_activity(context.bot_data['db'], user, username)
        
    except Exception as e:
        logger.error(f"❌ Ошибка обработки сообщения от {user.id}: {e}")
        await handle_message_error(message, user, e)
        # Сохраняем как непроцессированное для последующего восстановления
        await save_unprocessed_message(context, message)
        
async def update_stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Принудительное обновление статистики"""
    try:
        await update.message.reply_text("🔄 Обновляю статистику...")
        
        # Пересчитываем статистику
        db = context.bot_data['db']
        cursor = db.conn.cursor()
        
        cursor.execute('SELECT COUNT(*) FROM message_history WHERE is_spam = TRUE')
        spam_count = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM message_history')
        total_count = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM message_history WHERE is_spam IS NULL AND response_text IS NULL')
        unprocessed_count = cursor.fetchone()[0]
        
        await update.message.reply_text(
            f"📊 **Обновленная статистика:**\n"
            f"• Всего сообщений в БД: {total_count}\n"
            f"• Спам сообщений в БД: {spam_count}\n"
            f"• Необработанных сообщений: {unprocessed_count}\n"
            f"• Эффективность: {spam_count/max(1, total_count)*100:.1f}%",
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"❌ Ошибка обновления статистики: {e}")
        await update.message.reply_text("❌ Ошибка при обновлении статистики")

# === ОБРАБОТЧИК ВСЕХ СООБЩЕНИЙ ===
async def handle_all_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Главный обработчик всех сообщений"""
    try:
        # Обработка контент-планов
        if context.user_data.get('content_plan_stage'):
            stage = context.user_data.get('content_plan_stage')
            if stage in ['niche', 'audience', 'posts_count']:
                await context.bot_data['content_plan_manager'].handle_content_plan_creation(update, context)
                return
        
        # Обработка создания постов
        if context.user_data.get('creating_post'):
            stage = context.user_data.get('post_stage')
            if stage in ['topic', 'main_idea', 'schedule_time']:
                await context.bot_data['post_creator'].handle_post_creation(update, context)
                return
        
        # Обработка планирования постов из контент-плана
        if context.user_data.get('scheduling_plan_post'):
            await context.bot_data['post_creator'].handle_plan_post_scheduling(update, context)
            return
        
        # Обработка обычных сообщений
        await handle_message(update, context)
        
    except NetworkError as e:
        logger.error(f"🌐 Сетевая ошибка: {e}")
        # Сохраняем сообщение как непроцессированное
        await save_unprocessed_message(context, update.effective_message)
        
    except Exception as e:
        logger.error(f"❌ Неизвестная ошибка: {e}")
        # Сохраняем сообщение как непроцессированное
        await save_unprocessed_message(context, update.effective_message)

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
        db = context.bot_data['db']
        cache = context.bot_data['cache']
        
        cursor = db.conn.cursor()
        
        cursor.execute('SELECT COUNT(*) FROM message_history')
        total_messages = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM message_history WHERE is_spam = TRUE')
        spam_blocked = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(DISTINCT user_id) FROM user_activity')
        unique_users = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM scheduled_posts WHERE status = "scheduled"')
        scheduled_posts = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM content_plans')
        content_plans = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM message_history WHERE is_spam IS NULL AND response_text IS NULL')
        unprocessed_messages = cursor.fetchone()[0]
        
        cache_stats = cache.get_stats()
        
        stats_text = f"""📊 **Статистика бота**

• 💬 Всего сообщений: {total_messages}
• 🛡️ Заблокировано спама: {spam_blocked}
• 👥 Уникальных пользователей: {unique_users}
• 📅 Запланировано постов: {scheduled_posts}
• 📋 Контент-планов: {content_plans}
• ⏳ Необработанных сообщений: {unprocessed_messages}
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
    
    help_text = f"""🤖 Помощь по MamaAI Боту

Основные команды:
/start - главное меню
/stats - статистика бота
/status - статус системы
/test_post - тестовый пост
/create_post - создать пост с ИИ
/content_plan - создать контент-план
/scheduled_posts - запланированные посты
/check_permissions - проверить права бота
/moderation_stats - статистика модерации
/my_trust - мой уровень доверия
/my_content_plans - мои контент-планы
/force_recovery - восстановить пропущенные сообщения
/update_stats - обновить статистику
/help - эта справка

Автоматические функции:
• Модерация спама (AI проверка)
• Ответы на комментарии (AI генерация)
• Утренние посты: {MORNING_POST_TIME.strftime('%H:%M')}
• Вечерние посты: {EVENING_POST_TIME.strftime('%H:%M')}
• Сбор статистики

Бот работает полностью автоматически! 🎯"""
    
    if update.message:
        await update.message.reply_text(help_text)
    elif update.callback_query:
        await update.callback_query.edit_message_text(help_text)

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
        stats = await context.bot_data['post_scheduler'].get_scheduled_posts_stats()
        
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
        
        has_permissions = await check_bot_permissions(context.application.bot, CHANNEL_ID)
        
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
        await context.bot_data['post_scheduler']._check_scheduled_posts()
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
        
        auto_post_scheduler = context.bot_data['auto_post_scheduler']
        await auto_post_scheduler._publish_post(post_type)
        await update.message.reply_text(f"✅ {post_type} пост опубликован принудительно")
        
    except Exception as e:
        logger.error(f"❌ Ошибка принудительной публикации: {e}")
        await update.message.reply_text("❌ Ошибка при публикации поста")

async def moderation_stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Статистика модерации"""
    try:
        moderation = context.bot_data['moderation']
        stats = moderation.get_moderation_stats()
        
        stats_text = f"""🛡️ **Статистика модерации**

• 📊 Всего проверено: {stats['total_checked']}
• 🚨 Обнаружено спама: {stats['spam_detected']}
• 🤖 AI проверок: {stats['ai_checks']}
• ⚠️ Ложных срабатываний: {stats['false_positives']}

**Эффективность:** {stats['spam_detected']/max(1, stats['total_checked'])*100:.1f}%"""
        
        if update.message:
            await update.message.reply_text(stats_text, parse_mode='Markdown')
        elif update.callback_query:
            await update.callback_query.edit_message_text(stats_text, parse_mode='Markdown')
            
    except Exception as e:
        logger.error(f"❌ Ошибка получения статистики модерации: {e}")

async def user_trust_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Проверка доверия пользователя"""
    try:
        user = update.effective_user
        moderation = context.bot_data['moderation']
        user_stats = moderation.get_user_stats(user.id)
        
        trust_emoji = {
            'trusted': '🟢',
            'neutral': '🟡', 
            'suspicious': '🟠',
            'banned': '🔴'
        }
        
        trust_text = f"""👤 **Ваш уровень доверия**

{trust_emoji[user_stats['trust_level']]} **Уровень:** {user_stats['trust_level'].upper()}
📊 **Очков доверия:** {user_stats['trust_score']}/100
💬 **Сообщений:** {user_stats['message_count']}
⚠️ **Предупреждений:** {user_stats['warning_count']}
🚨 **Спам-сообщений:** {user_stats['spam_count']}

**Рекомендации:**
• Отправляйте содержательные сообщения
• Избегайте коммерческих предложений
• Не спамьте ссылками"""
        
        if update.message:
            await update.message.reply_text(trust_text, parse_mode='Markdown')
        elif update.callback_query:
            await update.callback_query.edit_message_text(trust_text, parse_mode='Markdown')
            
    except Exception as e:
        logger.error(f"❌ Ошибка проверки доверия: {e}")

async def my_content_plans_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать контент-планы пользователя"""
    user = update.effective_user
    
    try:
        content_plan_manager = context.bot_data['content_plan_manager']
        plans = await content_plan_manager.get_user_content_plans(user.id)
        
        if not plans:
            if update.message:
                await update.message.reply_text(
                    "📭 У вас пока нет созданных контент-планов.\n\n"
                    "Создайте первый контент-план через меню или командой /content_plan",
                    parse_mode='Markdown'
                )
            elif update.callback_query:
                await update.callback_query.edit_message_text(
                    "📭 У вас пока нет созданных контент-планов.\n\n"
                    "Создайте первый контент-план через меню или командой /content_plan",
                    parse_mode='Markdown'
                )
            return
        
        # Показываем первый план, остальные через кнопки
        await show_content_plan_details(update, context, plans[0], 0, len(plans))
        
    except Exception as e:
        logger.error(f"❌ Ошибка получения контент-планов: {e}")
        error_text = "❌ Ошибка при получении контент-планов"
        if update.message:
            await update.message.reply_text(error_text)
        elif update.callback_query:
            await update.callback_query.edit_message_text(error_text)

async def show_content_plan_details(update: Update, context: ContextTypes.DEFAULT_TYPE, plan: Dict, current_index: int, total_plans: int):
    """Показать детали контент-плана"""
    plan_text = f"📅 **{plan['name']}**\n\n"
    plan_text += f"📊 **Тип:** {plan['type']}\n"
    plan_text += f"📅 **Период:** {plan['start_date']} - {plan['end_date']}\n\n"
    
    # Показываем первые 5 постов из плана
    plan_data = plan.get('plan_data', {})
    posts = plan_data.get('plan', [])
    
    if posts:
        plan_text += "**Посты в плане:**\n\n"
        for i, post in enumerate(posts[:5]):
            day_info = post.get('day', '') or post.get('date', '')
            plan_text += f"**{i+1}. {day_info}**\n"
            plan_text += f"🎯 Тема: {post.get('topic', 'Без темы')}\n"
            plan_text += f"📝 Тип: {post.get('post_type', 'Не указан')}\n"
            plan_text += f"💡 Идея: {post.get('main_idea', 'Без описания')[:50]}...\n\n"
    else:
        plan_text += "📭 В плане пока нет постов\n\n"
    
    # Создаем клавиатуру навигации
    keyboard = []
    if total_plans > 1:
        nav_buttons = []
        if current_index > 0:
            nav_buttons.append(InlineKeyboardButton("⬅️ Назад", callback_data=f"plan_nav_{current_index-1}"))
        nav_buttons.append(InlineKeyboardButton(f"{current_index+1}/{total_plans}", callback_data="plan_info"))
        if current_index < total_plans - 1:
            nav_buttons.append(InlineKeyboardButton("Вперед ➡️", callback_data=f"plan_nav_{current_index+1}"))
        keyboard.append(nav_buttons)
    
    # Кнопки для работы с постами
    if posts:
        keyboard.append([InlineKeyboardButton("🎯 Сгенерировать пост из этого плана", callback_data=f"select_plan_post_{plan['id']}")])
    
    keyboard.extend([
        [InlineKeyboardButton("🗑️ Удалить план", callback_data=f"delete_plan_{plan['id']}")],
        [InlineKeyboardButton("↩️ Назад к списку", callback_data="my_content_plans")]
    ])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.message:
        await update.message.reply_text(plan_text, reply_markup=reply_markup, parse_mode='Markdown')
    elif update.callback_query:
        await update.callback_query.edit_message_text(plan_text, reply_markup=reply_markup, parse_mode='Markdown')

async def select_plan_post(update: Update, context: ContextTypes.DEFAULT_TYPE, plan_id: int):
    """Выбор поста из контент-плана для генерации"""
    user = update.effective_user
    query = update.callback_query
    
    try:
        # Получаем контент-план
        cursor = context.bot_data['db'].conn.cursor()
        cursor.execute('''
            SELECT plan_data, plan_name FROM content_plans 
            WHERE id = ? AND user_id = ?
        ''', (plan_id, user.id))
        
        result = cursor.fetchone()
        if not result:
            await query.edit_message_text("❌ Контент-план не найден")
            return
        
        plan_data_json, plan_name = result
        plan_data = json.loads(plan_data_json) if plan_data_json else {}
        posts = plan_data.get('plan', [])
        
        if not posts:
            await query.edit_message_text("❌ В контент-плане нет постов")
            return
        
        # Создаем клавиатуру с постами
        keyboard = []
        for i, post in enumerate(posts):
            day_info = post.get('day', '') or post.get('date', '')
            post_title = f"{i+1}. {day_info} - {post.get('topic', 'Без темы')[:30]}..."
            keyboard.append([InlineKeyboardButton(post_title, callback_data=f"generate_plan_post_{plan_id}_{i}")])
        
        keyboard.append([InlineKeyboardButton("↩️ Назад к плану", callback_data=f"plan_nav_0")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            f"📝 **Выберите пост для генерации из плана:**\n**{plan_name}**\n\n"
            f"Всего постов в плане: {len(posts)}",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"❌ Ошибка выбора поста из контент-плана: {e}")
        await query.edit_message_text("❌ Ошибка при выборе поста")

async def force_recovery_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Принудительный запуск восстановления пропущенных сообщений"""
    try:
        await update.message.reply_text("🔄 Запуск принудительного восстановления пропущенных сообщений...")
        
        recovery_system = context.bot_data.get('recovery_system')
        if recovery_system:
            # Получаем параметр часов из аргументов команды
            hours_back = 24
            if context.args and context.args[0].isdigit():
                hours_back = min(int(context.args[0]), 168)  # Максимум 7 дней
            
            result = await recovery_system.force_recovery(hours_back)
            
            if result["success"]:
                stats = result.get("stats", {})
                response = (
                    f"✅ **Принудительное восстановление завершено!**\n\n"
                    f"📊 **Результаты:**\n"
                    f"• Всего сообщений: {stats.get('total_messages', 0)}\n"
                    f"• Обработано: {stats.get('processed', 0)}\n"
                    f"• Спама обнаружено: {stats.get('spam_detected', 0)}\n"
                    f"• Ошибок: {stats.get('errors', 0)}\n"
                    f"• Успешность: {stats.get('success_rate', 0):.1f}%\n\n"
                    f"⏰ Период: последние {hours_back} часов"
                )
            else:
                response = f"❌ **Ошибка восстановления:** {result['message']}"
            
            await update.message.reply_text(response, parse_mode='Markdown')
        else:
            await update.message.reply_text("❌ Система восстановления недоступна")
            
    except Exception as e:
        logger.error(f"❌ Ошибка принудительного восстановления: {e}")
        await update.message.reply_text("❌ Ошибка при восстановлении")

# === ОБРАБОТЧИКИ CALLBACK ===
async def handle_all_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user = query.from_user
    data = query.data
    
    logger.info(f"🔔 Callback: {data} от {user.id}")
    
    if data in ["stats", "status", "auto_posts", "create_post", "content_plan", "help", "scheduled_posts", "check_permissions", "main_menu", "my_content_plans"]:
        await handle_main_menu_callback(update, context)
    elif any(data.startswith(prefix) for prefix in ["tone_", "length_", "emojis_", "publish_now", "schedule_later"]):
        await handle_post_creation_callback(update, context)
    elif data.startswith('plan_nav_'):
        # Навигация по контент-планам
        plan_index = int(data.split('_')[2])
        user = query.from_user
        content_plan_manager = context.bot_data['content_plan_manager']
        plans = await content_plan_manager.get_user_content_plans(user.id)
        
        if 0 <= plan_index < len(plans):
            await show_content_plan_details(update, context, plans[plan_index], plan_index, len(plans))
    elif data.startswith('select_plan_post_'):
        # Выбор поста из контент-плана
        plan_id = int(data.split('_')[3])
        await select_plan_post(update, context, plan_id)
    elif data.startswith('generate_plan_post_'):
        # Генерация поста из контент-плана
        parts = data.split('_')
        plan_id = int(parts[3])
        post_index = int(parts[4])
        await context.bot_data['post_creator'].generate_post_from_plan(update, context, plan_id, post_index)
    elif data.startswith('publish_plan_post_'):
        # Публикация поста из контент-плана
        parts = data.split('_')
        plan_id = int(parts[3])
        post_index = int(parts[4])
        await context.bot_data['post_creator'].publish_plan_post(update, context, plan_id, post_index)
    elif data.startswith('schedule_plan_post_'):
        # Планирование поста из контент-плана
        parts = data.split('_')
        plan_id = int(parts[3])
        post_index = int(parts[4])
        await context.bot_data['post_creator'].schedule_plan_post(update, context, plan_id, post_index)
    elif data.startswith('regenerate_plan_post_'):
        # Регенерация поста из контент-плана
        parts = data.split('_')
        plan_id = int(parts[3])
        post_index = int(parts[4])
        await context.bot_data['post_creator'].generate_post_from_plan(update, context, plan_id, post_index)
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
        elif data == "my_content_plans":
            await my_content_plans_command(update, context)
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
            generated_post = await context.bot_data['response_generator'].generate_post(
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
                cursor = context.bot_data['db'].execute_with_datetime('''
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
                context.bot_data['db'].conn.commit()
                
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
        await context.bot_data['content_plan_manager'].create_content_plan(update, context, "weekly")
    elif data == "content_plan_monthly":
        await context.bot_data['content_plan_manager'].create_content_plan(update, context, "monthly")
    elif data.startswith('plan_tone_'):
        tone = data.split('_')[2]
        context.user_data['content_plan_tone'] = tone
        context.user_data['content_plan_stage'] = 'posts_count'
        
        await query.edit_message_text(
            "📊 **Шаг 4 из 4:** Введите количество постов в неделю (1-50):",
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
    app.add_handler(CommandHandler("force_auto_post", force_auto_post))
    app.add_handler(CommandHandler("moderation_stats", moderation_stats_command))
    app.add_handler(CommandHandler("my_trust", user_trust_command))
    app.add_handler(CommandHandler("my_content_plans", my_content_plans_command))
    app.add_handler(CommandHandler("update_stats", update_stats_command))
    app.add_handler(CommandHandler("force_recovery", force_recovery_command))
    app.add_handler(CommandHandler("check_messages", check_messages_status_command))
    
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