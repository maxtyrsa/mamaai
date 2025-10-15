import logging
import asyncio
from datetime import datetime, timedelta
from typing import List, Dict
from telegram.error import Forbidden, NetworkError

logger = logging.getLogger(__name__)

class MessageRecoverySystem:
    def __init__(self, app, db, moderation, response_generator):
        self.app = app
        self.db = db
        self.moderation = moderation
        self.response_generator = response_generator
        self.is_recovering = False
        self.recovery_task = None
        self.monitoring_task = None
    
    async def start_recovery_check(self):
        """Запуск проверки пропущенных сообщений при старте"""
        if self.is_recovering:
            logger.info("⏳ Восстановление уже выполняется...")
            return
        
        self.is_recovering = True
        
        try:
            # Проверяем соединение с Telegram
            await self.app.bot.get_me()
            logger.info("✅ Соединение с Telegram восстановлено")
            
            # Запускаем восстановление
            await self._recover_messages()
            
        except Exception as e:
            logger.error(f"❌ Ошибка при проверке соединения: {e}")
            # Логируем ошибку восстановления
            self.db.log_recovery(
                recovery_type="auto_recovery",
                processed_count=0,
                spam_count=0,
                total_messages=0,
                success=False,
                error_message=f"Ошибка соединения: {e}"
            )
        finally:
            self.is_recovering = False
    
    async def _recover_messages(self, hours_back: int = 24):
        """Восстановление пропущенных сообщений"""
        start_time = datetime.now()
        
        try:
            # Получаем непроцессированные сообщения
            unprocessed_messages = self.db.get_unprocessed_messages(hours_back)
            
            if not unprocessed_messages:
                logger.info("✅ Нет пропущенных сообщений для обработки")
                return
            
            logger.info(f"🔄 Найдено {len(unprocessed_messages)} пропущенных сообщений за последние {hours_back} часов")
            
            # Обрабатываем сообщения по порядку
            processed_count = 0
            spam_count = 0
            error_count = 0
            skipped_count = 0
            
            for i, msg in enumerate(unprocessed_messages):
                try:
                    # Безопасное извлечение данных из сообщения
                    msg_id = msg.get('id')
                    user_id = msg.get('user_id')
                    text = msg.get('message_text', '')
                    timestamp = msg.get('timestamp')
                    username = msg.get('username')
                    is_spam = msg.get('is_spam')
                    response_text = msg.get('response_text')
                    spam_score = msg.get('spam_score')
                    
                    logger.info(f"🔍 Анализ сообщения {i+1}/{len(unprocessed_messages)}: ID={msg_id}, user={user_id}, текст='{text[:50]}...'")
                    
                    # Проверяем обязательные поля
                    if not msg_id:
                        logger.warning(f"⚠️ Пропущено сообщение без ID: {msg}")
                        skipped_count += 1
                        continue
                    
                    if not user_id:
                        logger.warning(f"⚠️ Пропущено сообщение без user_id: {msg}")
                        skipped_count += 1
                        continue
                    
                    if not text or len(text.strip()) < 2:
                        logger.warning(f"⚠️ Пропущено сообщение с пустым текстом: ID={msg_id}")
                        skipped_count += 1
                        continue
                    
                    # Пропускаем уже обработанные
                    if is_spam is not None:
                        logger.info(f"⏩ Пропущено уже обработанное сообщение {msg_id} (is_spam={is_spam})")
                        skipped_count += 1
                        continue
                    
                    if response_text is not None:
                        logger.info(f"⏩ Пропущено сообщение с ответом {msg_id}")
                        skipped_count += 1
                        continue
                    
                    logger.info(f"🔁 Обработка пропущенного сообщения {msg_id} от пользователя {user_id}")
                    
                    # Проверка на спам
                    logger.info(f"🛡️ Проверка на спам сообщения {msg_id}...")
                    is_spam_detected, spam_score_value = await self.moderation.advanced_spam_check(text, user_id)
                    logger.info(f"🛡️ Результат проверки спама для {msg_id}: {is_spam_detected} (score: {spam_score_value:.1f})")
                    
                    if is_spam_detected:
                        # Помечаем как спам
                        logger.info(f"🚫 Сообщение {msg_id} помечено как спам")
                        self.db.mark_message_processed(
                            msg_id, 
                            is_spam=True, 
                            response_text=f"Обнаружен при восстановлении (score: {spam_score_value:.1f})",
                            spam_score=spam_score_value
                        )
                        spam_count += 1
                        logger.info(f"🛡️ Обнаружен спам в пропущенном сообщении {msg_id}")
                        
                    else:
                        # Генерируем ответ
                        display_name = username or f"user_{user_id}"
                        logger.info(f"🤖 Генерация ответа для сообщения {msg_id}...")
                        reply_text = await self.response_generator.generate_context_aware_reply(
                            text, user_id, display_name
                        )
                        
                        if not reply_text or len(reply_text.strip()) < 3:
                            logger.warning(f"⚠️ Сгенерирован пустой ответ для сообщения {msg_id}")
                            reply_text = "Спасибо за ваше сообщение! 😊"
                        
                        # Помечаем как обработанное
                        logger.info(f"💾 Сохранение ответа для сообщения {msg_id}")
                        self.db.mark_message_processed(
                            msg_id, 
                            is_spam=False, 
                            response_text=reply_text,
                            spam_score=spam_score_value
                        )
                        
                        # Пытаемся отправить ответ (если пользователь не заблокировал бота)
                        logger.info(f"📤 Отправка ответа пользователю {user_id}...")
                        send_success = await self._try_send_reply(user_id, text, reply_text, msg_id)
                        
                        if send_success:
                            processed_count += 1
                            logger.info(f"✅ Ответ на пропущенное сообщение {msg_id} отправлен")
                        else:
                            error_count += 1
                            logger.warning(f"⚠️ Не удалось отправить ответ на сообщение {msg_id}")
                    
                    # Небольшая задержка между обработкой сообщений
                    await asyncio.sleep(0.5)
                    
                except Exception as e:
                    logger.error(f"❌ Ошибка обработки пропущенного сообщения {msg.get('id', 'unknown')}: {e}")
                    error_count += 1
                    continue
            
            # Рассчитываем длительность
            duration = (datetime.now() - start_time).total_seconds()
            
            # Логируем результат восстановления
            self.db.log_recovery(
                recovery_type="auto_recovery",
                processed_count=processed_count,
                spam_count=spam_count,
                total_messages=len(unprocessed_messages),
                success=True,
                duration_seconds=int(duration)
            )
            
            logger.info(f"✅ Восстановление завершено: "
                       f"{processed_count} ответов отправлено, "
                       f"{spam_count} спама обнаружено, "
                       f"{error_count} ошибок, "
                       f"{skipped_count} пропущено, "
                       f"длительность: {duration:.1f} сек.")
            
            # Уведомляем администраторов о результатах
            if processed_count > 0 or spam_count > 0:
                await self._notify_admins_recovery(processed_count, spam_count, error_count, len(unprocessed_messages))
            
        except Exception as e:
            logger.error(f"❌ Критическая ошибка при восстановлении сообщений: {e}")
            # Логируем ошибку восстановления
            self.db.log_recovery(
                recovery_type="auto_recovery",
                processed_count=0,
                spam_count=0,
                total_messages=0,
                success=False,
                error_message=str(e)
            )
    
    async def _try_send_reply(self, user_id: int, original_text: str, reply_text: str, msg_id: int) -> bool:
        """Попытка отправить ответ пользователю"""
        try:
            # Обрезаем текст если слишком длинный
            original_preview = original_text[:100] + ('...' if len(original_text) > 100 else '')
            
            # Форматируем сообщение для восстановления
            formatted_message = (
                f"💬 **Ответ на ваше сообщение:**\n\n"
                f"_{original_preview}_\n\n"
                f"────────────────────\n"
                f"{reply_text}\n\n"
                f"_📅 Автоматический ответ при восстановлении работы бота_"
            )
            
            logger.info(f"📨 Отправка сообщения пользователю {user_id}: {formatted_message[:100]}...")
            
            await self.app.bot.send_message(
                user_id,
                formatted_message,
                parse_mode='Markdown'
            )
            logger.info(f"✅ Ответ на пропущенное сообщение {msg_id} отправлен пользователю {user_id}")
            return True
            
        except Forbidden as e:
            error_msg = str(e).lower()
            if "bot was blocked" in error_msg:
                logger.warning(f"⚠️ Пользователь {user_id} заблокировал бота, ответ не отправлен")
            elif "chat not found" in error_msg:
                logger.warning(f"⚠️ Чат с пользователем {user_id} не найден")
            else:
                logger.warning(f"⚠️ Нет прав для отправки сообщения пользователю {user_id}: {e}")
            return False
            
        except Exception as e:
            logger.error(f"❌ Ошибка отправки ответа пользователю {user_id}: {e}")
            return False
    
    async def _notify_admins_recovery(self, processed_count: int, spam_count: int, error_count: int, total_messages: int):
        """Уведомление администраторов о результатах восстановления"""
        try:
            notification_system = self.app.bot_data.get('notification_system')
            if notification_system:
                success_rate = (processed_count / total_messages * 100) if total_messages > 0 else 0
                
                message = (
                    "🔄 **Восстановление работы бота завершено**\n\n"
                    f"📊 **Общая статистика:**\n"
                    f"• Всего сообщений: {total_messages}\n"
                    f"• Успешно обработано: {processed_count}\n"
                    f"• Обнаружено спама: {spam_count}\n"
                    f"• Ошибок обработки: {error_count}\n"
                    f"• Успешность: {success_rate:.1f}%\n\n"
                    f"⏰ **Время:** {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}\n\n"
                    f"✅ Бот полностью восстановил работу!"
                )
                await notification_system.notify_admins(message)
        except Exception as e:
            logger.error(f"❌ Ошибка уведомления администраторов: {e}")
    
    async def schedule_periodic_recovery_check(self, interval_hours: int = 6):
        """Периодическая проверка пропущенных сообщений"""
        logger.info(f"⏰ Запуск периодических проверок восстановления каждые {interval_hours} часов")
        
        while True:
            try:
                await asyncio.sleep(interval_hours * 3600)  # Ждем указанное количество часов
                
                if not self.is_recovering:
                    logger.info("🔍 Запуск периодической проверки восстановления...")
                    await self._recover_messages(interval_hours)
                else:
                    logger.info("⏳ Пропускаем проверку - восстановление уже выполняется")
                    
            except asyncio.CancelledError:
                logger.info("🛑 Периодические проверки восстановления остановлены")
                break
            except Exception as e:
                logger.error(f"❌ Ошибка в периодической проверке восстановления: {e}")
                await asyncio.sleep(3600)  # Ждем 1 час при ошибке
    
    async def start_periodic_checks(self):
        """Запуск периодических проверок"""
        try:
            self.recovery_task = asyncio.create_task(self.schedule_periodic_recovery_check(6))
            logger.info("✅ Запущены периодические проверки пропущенных сообщений (каждые 6 часов)")
        except Exception as e:
            logger.error(f"❌ Ошибка запуска периодических проверок: {e}")
    
    async def force_recovery(self, hours_back: int = 24):
        """Принудительное восстановление сообщений"""
        start_time = datetime.now()
        
        try:
            if self.is_recovering:
                return {"success": False, "message": "Восстановление уже выполняется"}
            
            self.is_recovering = True
            logger.info(f"🔧 Принудительное восстановление сообщений за последние {hours_back} часов")
            
            # Получаем непроцессированные сообщения
            unprocessed_messages = self.db.get_unprocessed_messages(hours_back)
            
            if not unprocessed_messages:
                result = {"success": True, "message": "Нет пропущенных сообщений для обработки"}
                self.is_recovering = False
                return result
            
            logger.info(f"🔄 Найдено {len(unprocessed_messages)} сообщений для принудительного восстановления")
            
            # Обрабатываем сообщения
            processed_count = 0
            spam_count = 0
            error_count = 0
            skipped_count = 0
            
            for i, msg in enumerate(unprocessed_messages):
                try:
                    # Безопасное извлечение данных
                    msg_id = msg.get('id')
                    user_id = msg.get('user_id')
                    text = msg.get('message_text', '')
                    username = msg.get('username')
                    is_spam = msg.get('is_spam')
                    response_text = msg.get('response_text')
                    
                    logger.info(f"🔍 [{i+1}/{len(unprocessed_messages)}] Анализ сообщения ID={msg_id}, user={user_id}")
                    
                    # Пропускаем уже обработанные
                    if is_spam is not None:
                        logger.info(f"⏩ Пропущено уже обработанное сообщение {msg_id} (is_spam={is_spam})")
                        skipped_count += 1
                        continue
                    
                    if response_text is not None:
                        logger.info(f"⏩ Пропущено сообщение с ответом {msg_id}")
                        skipped_count += 1
                        continue
                    
                    # Проверка на спам
                    logger.info(f"🛡️ Проверка на спам сообщения {msg_id}...")
                    is_spam_detected, spam_score = await self.moderation.advanced_spam_check(text, user_id)
                    logger.info(f"🛡️ Результат для {msg_id}: спам={is_spam_detected}, score={spam_score:.1f}")
                    
                    if is_spam_detected:
                        self.db.mark_message_processed(
                            msg_id, 
                            is_spam=True, 
                            response_text=f"Обнаружен при восстановлении (score: {spam_score:.1f})",
                            spam_score=spam_score
                        )
                        spam_count += 1
                        logger.info(f"🚫 Сообщение {msg_id} помечено как спам")
                    else:
                        display_name = username or f"user_{user_id}"
                        logger.info(f"🤖 Генерация ответа для {msg_id}...")
                        reply_text = await self.response_generator.generate_context_aware_reply(
                            text, user_id, display_name
                        )
                        
                        if not reply_text or len(reply_text.strip()) < 3:
                            logger.warning(f"⚠️ Сгенерирован пустой ответ для {msg_id}, используем fallback")
                            reply_text = "Спасибо за ваше сообщение! 😊"
                        
                        self.db.mark_message_processed(
                            msg_id, 
                            is_spam=False, 
                            response_text=reply_text,
                            spam_score=spam_score
                        )
                        
                        logger.info(f"📤 Отправка ответа пользователю {user_id}...")
                        send_success = await self._try_send_reply(user_id, text, reply_text, msg_id)
                        if send_success:
                            processed_count += 1
                            logger.info(f"✅ Ответ на сообщение {msg_id} отправлен")
                        else:
                            error_count += 1
                            logger.warning(f"⚠️ Не удалось отправить ответ на сообщение {msg_id}")
                    
                    await asyncio.sleep(0.3)  # Уменьшенная задержка для принудительного восстановления
                    
                except Exception as e:
                    logger.error(f"❌ Ошибка принудительного восстановления сообщения {msg.get('id', 'unknown')}: {e}")
                    error_count += 1
                    continue
            
            # Рассчитываем длительность
            duration = (datetime.now() - start_time).total_seconds()
            
            # Логируем результат
            self.db.log_recovery(
                recovery_type="forced_recovery",
                processed_count=processed_count,
                spam_count=spam_count,
                total_messages=len(unprocessed_messages),
                success=True,
                duration_seconds=int(duration)
            )
            
            result = {
                "success": True,
                "message": f"Принудительное восстановление завершено",
                "stats": {
                    "total_messages": len(unprocessed_messages),
                    "processed": processed_count,
                    "spam_detected": spam_count,
                    "errors": error_count,
                    "skipped": skipped_count,
                    "success_rate": (processed_count / len(unprocessed_messages) * 100) if unprocessed_messages else 0,
                    "duration_seconds": int(duration)
                }
            }
            
            logger.info(f"✅ Принудительное восстановление завершено: {processed_count} ответов, {spam_count} спама, {skipped_count} пропущено")
            
            # Уведомляем администраторов
            if processed_count > 0 or spam_count > 0:
                await self._notify_admins_recovery(processed_count, spam_count, error_count, len(unprocessed_messages))
            
            return result
            
        except Exception as e:
            logger.error(f"❌ Ошибка принудительного восстановления: {e}")
            self.db.log_recovery(
                recovery_type="forced_recovery",
                processed_count=0,
                spam_count=0,
                total_messages=0,
                success=False,
                error_message=str(e)
            )
            return {"success": False, "message": f"Ошибка восстановления: {e}"}
        finally:
            self.is_recovering = False
    
    async def get_recovery_status(self):
        """Получение статуса системы восстановления"""
        try:
            # Получаем статистику восстановления за последние 7 дней
            recovery_stats = self.db.get_recovery_stats(7)
            
            # Получаем количество непроцессированных сообщений
            unprocessed_messages = self.db.get_unprocessed_messages(24)
            
            status = {
                "is_recovering": self.is_recovering,
                "unprocessed_messages": len(unprocessed_messages),
                "recovery_stats": recovery_stats,
                "last_check": datetime.now().isoformat(),
                "periodic_checks_active": self.recovery_task is not None and not self.recovery_task.done()
            }
            
            return status
            
        except Exception as e:
            logger.error(f"❌ Ошибка получения статуса восстановления: {e}")
            return {
                "is_recovering": self.is_recovering,
                "error": str(e)
            }
    
    async def stop(self):
        """Остановка системы восстановления"""
        try:
            if self.recovery_task and not self.recovery_task.done():
                self.recovery_task.cancel()
                try:
                    await self.recovery_task
                except asyncio.CancelledError:
                    pass
                logger.info("🛑 Периодические проверки восстановления остановлены")
            
            self.is_recovering = False
            logger.info("✅ Система восстановления остановлена")
            
        except Exception as e:
            logger.error(f"❌ Ошибка остановки системы восстановления: {e}")