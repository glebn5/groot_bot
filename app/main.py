import asyncio
import logging
import sys
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from app.config import settings
from app.middleware.auth import AuthMiddleware
from app.services.scheduler import scheduler_service
from app.handlers import common, text, voice, media, settings as settings_handler, notes as notes_handler

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("app.main")


async def main():
    logger.info("Initializing Groot's Telegram Bot...")

    bot = Bot(
        token=settings.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN)
    )
    dp = Dispatcher()

    # Register Auth Middleware
    dp.update.outer_middleware(AuthMiddleware())

    # Register Router handlers
    dp.include_router(common.router)
    dp.include_router(settings_handler.router)
    dp.include_router(notes_handler.router)
    dp.include_router(text.router)
    dp.include_router(voice.router)
    dp.include_router(media.router)

    # Set Telegram bot menu commands
    from aiogram.types import BotCommand
    await bot.set_my_commands([
        BotCommand(command="start", description="🚀 Запустить бота"),
        BotCommand(command="notes", description="📝 Мои заметки"),
        BotCommand(command="settings", description="⚙️ Настройки и ключи"),
        BotCommand(command="help", description="💡 Инструкция по работе")
    ])

    # Start APScheduler
    scheduler_service.start()

    logger.info("Bot setup completed. Starting long polling...")
    try:
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
