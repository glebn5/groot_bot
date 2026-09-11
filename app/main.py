import asyncio
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from app.config import settings

# Configure structured logging (Console + Rotating File)
log_dir = os.path.dirname(settings.LOG_FILE_PATH)
if log_dir:
    os.makedirs(log_dir, exist_ok=True)

log_formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(log_formatter)

file_handler = RotatingFileHandler(
    settings.LOG_FILE_PATH,
    maxBytes=settings.LOG_MAX_BYTES,
    backupCount=settings.LOG_BACKUP_COUNT,
    encoding="utf-8"
)
file_handler.setFormatter(log_formatter)

logging.basicConfig(
    level=logging.INFO,
    handlers=[console_handler, file_handler]
)
logger = logging.getLogger("app.main")

from app.utils.timezone import init_timezone
from app.middleware.auth import AuthMiddleware
from app.services.scheduler import scheduler_service
from app.handlers import common, text, voice, media, settings as settings_handler, notes as notes_handler, goals as goals_handler, habits as habits_handler



async def main():
    init_timezone()
    from app.services.settings_storage import load_settings_from_db
    load_settings_from_db()
    logger.info("Initializing Groot's Telegram Bot...")

    bot = Bot(
        token=settings.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN)
    )
    dp = Dispatcher()

    # Register Auth Middleware
    dp.update.outer_middleware(AuthMiddleware())

    # Register Router handlers
    dp.include_router(settings_handler.router)
    dp.include_router(notes_handler.router)
    dp.include_router(goals_handler.router)
    dp.include_router(habits_handler.router)
    dp.include_router(text.router)
    dp.include_router(voice.router)
    dp.include_router(media.router)
    dp.include_router(common.router)

    # Set Telegram bot menu commands
    from aiogram.types import BotCommand
    await bot.set_my_commands([
        BotCommand(command="start", description="🚀 Запустить бота"),
        BotCommand(command="today", description="📅 Планы на сегодня"),
        BotCommand(command="goals", description="🎯 Цели на месяц"),
        BotCommand(command="habits", description="🔁 Привычки и повторы"),
        BotCommand(command="notes", description="📝 Мои заметки"),
        BotCommand(command="settings", description="⚙️ Настройки и ключи"),
        BotCommand(command="cancel", description="❌ Отменить текущее действие"),
        BotCommand(command="help", description="💡 Инструкция по работе")
    ])

    # Start APScheduler & load active recurring tasks
    scheduler_service.start()
    await scheduler_service.load_all_recurring_tasks()

    logger.info("Bot setup completed. Starting long polling...")
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        logger.info("Shutting down bot and scheduler...")
        scheduler_service.stop()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped by user.")
