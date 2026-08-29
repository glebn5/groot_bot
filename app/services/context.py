import logging
from datetime import date, datetime, timedelta
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


class UserContext:
    def __init__(self):
        # Memory storage: user_id -> {'last_date': date, 'updated_at': datetime}
        self._storage: Dict[int, Dict[str, Any]] = {}

    def set_last_date(self, user_id: int, target_date: date):
        self._storage[user_id] = {
            "last_date": target_date,
            "updated_at": datetime.now()
        }
        logger.info(f"Updated context for user {user_id}: last_date={target_date}")

    def get_last_date(self, user_id: int, max_age_seconds: int = 600) -> Optional[date]:
        data = self._storage.get(user_id)
        if not data:
            return None

        # Check if context is fresh (within max_age_seconds)
        if datetime.now() - data["updated_at"] > timedelta(seconds=max_age_seconds):
            logger.info(f"Context for user {user_id} expired.")
            return None

        return data["last_date"]

    def clear_context(self, user_id: int):
        self._storage.pop(user_id, None)


context_service = UserContext()
