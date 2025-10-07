import asyncio
import logging
from telegram.ext import Application
from config import BOT_TOKEN, CHANNEL_ID
from database import Database
from models import Llama, MockLLM
from moderation import AdvancedModeration
from ai_generator import ResponseGenerator
from scheduler import AutoPostScheduler, PostScheduler
from handlers import setup_handlers
from utils import setup_logging, check_bot_permissions

logger = setup_logging()

async def main():
    logger.info("🚀 Запуск MamaAI Бота...")
    
    try:
        # Инициализация базы данных
        db = Database()
        
        # Инициализация AI модели
        logger.info("🧠 Загрузка модели ИИ...")
        try:
            llm = Llama(
                model_path=MODEL_PATH,
                n_ctx=2048,
                n_threads=4,
                n_gpu_layers=0,
                verbose=False
            )
            logger.info("✅ Модель ИИ загружена!")
        except Exception as e:
            logger.error(f"❌ Ошибка загрузки модели: {e}")
            llm = MockLLM()
            logger.info("🤖 Используется тестовая модель")
        
        # Создание приложения
        app = Application.builder().token(BOT_TOKEN).build()
        
        # Настройка контекста
        context_data = {
            'db': db,
            'llm': llm,
            'channel_id': CHANNEL_ID
        }
        
        # Инициализация систем
        from ai_generator import AdvancedCache
        from moderation import RateLimiter
        from handlers import NotificationSystem, PostCreator, ContentPlanManager
        
        cache = AdvancedCache(db)
        rate_limiter = RateLimiter(db)
        moderation = AdvancedModeration(llm, db)
        response_generator = ResponseGenerator(llm, cache, db)
        notification_system = NotificationSystem(app, db)
        post_creator = PostCreator(response_generator, db)
        content_plan_manager = ContentPlanManager(response_generator, db)
        auto_post_scheduler = AutoPostScheduler(app, response_generator, db)
        post_scheduler = PostScheduler(app, db)
        
        # Сохранение в контекст
        app.context_data = {
            'db': db,
            'cache': cache,
            'rate_limiter': rate_limiter,
            'moderation': moderation,
            'response_generator': response_generator,
            'notification_system': notification_system,
            'post_creator': post_creator,
            'content_plan_manager': content_plan_manager,
            'auto_post_scheduler': auto_post_scheduler,
            'post_scheduler': post_scheduler
        }
        
        # Настройка обработчиков
        setup_handlers(app)
        
        # Проверка прав при запуске
        logger.info("🔍 Проверка прав бота в канале...")
        has_permissions = await check_bot_permissions(app)
        if has_permissions:
            logger.info("✅ Права бота в порядке")
        else:
            logger.error("❌ Бот не имеет прав для публикации в канале!")
        
        # Запуск систем
        await auto_post_scheduler.start()
        await post_scheduler.start()
        
        logger.info("📱 Бот готов к работе!")
        
        # Запуск бота
        await app.run_polling(
            drop_pending_updates=True,
            close_loop=False,
            allowed_updates=Update.ALL_TYPES
        )
        
    except KeyboardInterrupt:
        logger.info("🔴 Бот остановлен пользователем")
    except Exception as e:
        logger.error(f"💥 Критическая ошибка: {e}")
    finally:
        logger.info("🛑 Завершение работы...")
        if 'auto_post_scheduler' in locals():
            await auto_post_scheduler.stop()
        if 'post_scheduler' in locals():
            await post_scheduler.stop()
        if 'db' in locals():
            db.conn.close()
        logger.info("🔴 Бот остановлен")

if __name__ == "__main__":
    asyncio.run(main())
