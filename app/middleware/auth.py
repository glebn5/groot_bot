import logging
from typing import Any, Awaitable, Callable, Dict
from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject, User
from app.config import settings

logger = logging.getLogger(__name__)


class AuthMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        user: User = data.get("event_from_user")
        
        # If allowed user list is specified, strictly check permissions
        if settings.ALLOWED_TELEGRAM_USER_IDS:
            if not user or user.id not in settings.ALLOWED_TELEGRAM_USER_IDS:
                logger.warning(f"Unauthorized access attempt by user_id={user.id if user else 'Unknown'}")
                if isinstance(event, Message):
                    await event.answer("⛔ Доступ ограничен. Вы не авторизованы для использования этого бота.")
                return None
        
        return await handler(event, data)
