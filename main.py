import asyncio
import logging
import signal
import sys
from telegram import Update
from telegram.ext import Application
from config import BOT_TOKEN, CHANNEL_ID, MODEL_PATH
from database import Database
from models import Llama, MockLLM
from moderation import AdvancedModeration, RateLimiter
from ai_generator import ResponseGenerator, AdvancedCache
from scheduler import AutoPostScheduler, PostScheduler
from handlers import setup_handlers, PostCreator, ContentPlanManager, NotificationSystem
from utils import setup_logging, check_bot_permissions
from recovery_system import MessageRecoverySystem

logger = setup_logging()

class BotRunner:
    def __init__(self):
        self.app = None
        self.db = None
        self.auto_post_scheduler = None
        self.post_scheduler = None
        self.post_creator = None
        self.content_plan_manager = None
        self.notification_system = None
        self.recovery_system = None
        self._stop_event = asyncio.Event()

    async def initialize(self):
        """Инициализация всех компонентов бота"""
        logger.info("🚀 Запуск MamaAI Бота...")
        
        # Инициализация базы данных
        loop = asyncio.get_running_loop()
        self.db = await loop.run_in_executor(None, Database)

        # Инициализация AI модели
        logger.info("🧠 Загрузка модели ИИ...")
        llm = None
        try:
            llm = await loop.run_in_executor(None, lambda: Llama(
                model_path=MODEL_PATH,
                n_ctx=2048,
                n_threads=4,
                n_gpu_layers=0,
                verbose=False
            ))
            logger.info("✅ Модель ИИ загружена!")
        except Exception as e:
            logger.error(f"❌ Ошибка загрузки модели: {e}")
            llm = MockLLM()
            logger.info("🤖 Используется тестовая модель")
        
        # Создание приложения
        self.app = Application.builder().token(BOT_TOKEN).build()
        
        # Инициализация систем
        cache = AdvancedCache(self.db)
        rate_limiter = RateLimiter(self.db)
        moderation = AdvancedModeration(llm, self.db)
        response_generator = ResponseGenerator(llm, cache, self.db)
        
        # Инициализация менеджеров
        self.auto_post_scheduler = AutoPostScheduler(self.app, response_generator, self.db)
        self.post_scheduler = PostScheduler(self.app, self.db)
        self.post_creator = PostCreator(response_generator, self.db)
        self.content_plan_manager = ContentPlanManager(response_generator, self.db)
        self.notification_system = NotificationSystem(self.app, self.db)
        self.recovery_system = MessageRecoverySystem(self.app, self.db, moderation, response_generator)
        
        # Сохранение в bot_data приложения
        self.app.bot_data.update({
            'db': self.db,
            'cache': cache,
            'rate_limiter': rate_limiter,
            'moderation': moderation,
            'response_generator': response_generator,
            'auto_post_scheduler': self.auto_post_scheduler,
            'post_scheduler': self.post_scheduler,
            'post_creator': self.post_creator,
            'content_plan_manager': self.content_plan_manager,
            'notification_system': self.notification_system,
            'recovery_system': self.recovery_system,
            'llm': llm,
            'channel_id': CHANNEL_ID
        })
        
        # Настройка обработчиков
        setup_handlers(self.app)
        
        # Проверка прав при запуске
        logger.info("🔍 Проверка прав бота в канале...")
        try:
            has_permissions = await check_bot_permissions(self.app.bot, CHANNEL_ID)
            if has_permissions:
                logger.info("✅ Права бота в порядке")
            else:
                logger.error("❌ Бот не имеет прав для публикации в канале!")
        except Exception as e:
            logger.error(f"⚠️ Ошибка при проверке прав: {e}")

        # Запуск систем
        await self.auto_post_scheduler.start()
        await self.post_scheduler.start()
        
        # Запуск системы восстановления
        await self.recovery_system.start_recovery_check()
        await self.recovery_system.start_periodic_checks()
        
        # Запуск мониторинга непроцессированных сообщений
        asyncio.create_task(self.monitor_unprocessed_messages())
        
        logger.info("📱 Бот готов к работе!")

    async def monitor_unprocessed_messages(self):
        """Мониторинг непроцессированных сообщений"""
        logger.info("🔍 Запуск мониторинга непроцессированных сообщений...")
        
        while not self._stop_event.is_set():
            try:
                # Проверяем каждые 5 минут
                await asyncio.sleep(300)
                
                # Получаем количество непроцессированных сообщений
                unprocessed = self.db.get_unprocessed_messages(1)  # За последний час
                if unprocessed:
                    logger.warning(f"⚠️ Найдено {len(unprocessed)} непроцессированных сообщений")
                    
                    # Автоматически запускаем восстановление если есть много непроцессированных сообщений
                    if len(unprocessed) >= 5:
                        logger.info("🔄 Автоматический запуск восстановления...")
                        await self.recovery_system.force_recovery(1)
                
            except Exception as e:
                logger.error(f"❌ Ошибка мониторинга непроцессированных сообщений: {e}")
                await asyncio.sleep(60)  # Ждем минуту при ошибке

    async def run(self):
        """Запуск основного цикла бота"""
        try:
            await self.initialize()

            # Вручную запускаем приложение и polling
            await self.app.initialize()
            await self.app.start()
            await self.app.updater.start_polling(
                drop_pending_updates=True,
                allowed_updates=Update.ALL_TYPES
            )

            logger.info("📡 Polling запущен. Бот активен!")
            
            # Ждем сигнала остановки
            await self._stop_event.wait()
            
        except Exception as e:
            logger.exception(f"💥 Ошибка в основном цикле: {e}")
        finally:
            await self.shutdown()

    async def shutdown(self):
        """Корректное завершение работы"""
        logger.info("🛑 Завершение работы...")
        
        # Устанавливаем флаг остановки
        self._stop_event.set()
        
        # Остановка updater (polling)
        try:
            if self.app and self.app.updater and self.app.updater.running:
                await self.app.updater.stop()
        except Exception as e:
            logger.error(f"❌ Ошибка остановки updater: {e}")

        # Остановка планировщиков
        try:
            if self.auto_post_scheduler:
                await self.auto_post_scheduler.stop()
        except Exception as e:
            logger.error(f"❌ Ошибка остановки авто-постов: {e}")
            
        try:
            if self.post_scheduler:
                await self.post_scheduler.stop()
        except Exception as e:
            logger.error(f"❌ Ошибка остановки планировщика: {e}")
            
        # Остановка системы восстановления
        try:
            if self.recovery_system:
                await self.recovery_system.stop()
        except Exception as e:
            logger.error(f"❌ Ошибка остановки системы восстановления: {e}")
            
        # Остановка приложения
        try:
            if self.app:
                await self.app.stop()
                await self.app.shutdown()
        except Exception as e:
            logger.error(f"❌ Ошибка завершения работы приложения: {e}")
            
        # Закрытие БД
        try:
            if self.db:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, self.db.conn.close)
        except Exception as e:
            logger.error(f"❌ Ошибка закрытия БД: {e}")
            
        logger.info("🔴 Бот остановлен")

    def stop(self):
        """Внешний вызов для остановки бота"""
        self._stop_event.set()


def main():
    """Основная функция запуска"""
    runner = BotRunner()
    
    # Обработка сигналов
    def signal_handler(signum, frame):
        logger.info(f"📱 Получен сигнал {signum}, завершаем работу...")
        runner.stop()
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Запуск бота
    try:
        asyncio.run(runner.run())
    except KeyboardInterrupt:
        logger.info("🔴 Бот остановлен через KeyboardInterrupt")
    except Exception as e:
        logger.exception(f"💥 Критическая ошибка при запуске: {e}")


if __name__ == "__main__":
    main()