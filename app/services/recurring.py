import os
import sqlite3
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional
from app.config import settings
from app.utils.timezone import get_now

logger = logging.getLogger(__name__)


class RecurringService:
    def __init__(self):
        self.db_path = settings.DATABASE_PATH
        self._init_db()

    def _init_db(self):
        db_dir = os.path.dirname(self.db_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)

        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS recurring_tasks (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL,
                        title TEXT NOT NULL,
                        repeat_type TEXT NOT NULL,
                        cron_expression TEXT,
                        interval_days INTEGER,
                        target_time TEXT,
                        is_active INTEGER DEFAULT 1,
                        last_triggered_at TEXT,
                        created_at TEXT NOT NULL
                    )
                """)
                conn.commit()
                logger.info("Recurring tasks table initialized successfully in SQLite.")
        except Exception as e:
            logger.error(f"Failed to initialize recurring tasks table: {e}", exc_info=True)

    async def add_recurring_task(
        self,
        user_id: int,
        title: str,
        repeat_type: str,
        cron_expression: Optional[str] = None,
        interval_days: Optional[int] = None,
        target_time: Optional[str] = None
    ) -> int:
        """
        Adds a new recurring task / habit for user_id.
        """
        now_str = get_now().strftime("%Y-%m-%d %H:%M:%S")

        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO recurring_tasks (user_id, title, repeat_type, cron_expression, interval_days, target_time, is_active, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, 1, ?)
                """, (user_id, title.strip(), repeat_type, cron_expression, interval_days, target_time, now_str))
                conn.commit()
                task_id = cursor.lastrowid
                logger.info(f"Added recurring task #{task_id} for user {user_id}: '{title}' ({repeat_type})")
                return task_id
        except Exception as e:
            logger.error(f"Error adding recurring task for user {user_id}: {e}", exc_info=True)
            return 0

    async def get_user_tasks(self, user_id: int) -> List[Dict[str, Any]]:
        """
        Returns all recurring tasks for user_id.
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT * FROM recurring_tasks WHERE user_id = ? ORDER BY is_active DESC, id ASC",
                    (user_id,)
                )
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Error fetching recurring tasks for user {user_id}: {e}", exc_info=True)
            return []

    async def get_task_by_id(self, task_id: int, user_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """
        Returns a recurring task by id.
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                if user_id is not None:
                    cursor.execute("SELECT * FROM recurring_tasks WHERE id = ? AND user_id = ?", (task_id, user_id))
                else:
                    cursor.execute("SELECT * FROM recurring_tasks WHERE id = ?", (task_id,))
                row = cursor.fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.error(f"Error getting recurring task #{task_id}: {e}", exc_info=True)
            return None

    async def toggle_active(self, task_id: int, user_id: int) -> Optional[bool]:
        """
        Toggles is_active state for task_id.
        Returns new boolean state or None if not found.
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("SELECT is_active FROM recurring_tasks WHERE id = ? AND user_id = ?", (task_id, user_id))
                row = cursor.fetchone()
                if not row:
                    return None

                new_state = 0 if row["is_active"] == 1 else 1
                cursor.execute("UPDATE recurring_tasks SET is_active = ? WHERE id = ?", (new_state, task_id))
                conn.commit()
                return bool(new_state)
        except Exception as e:
            logger.error(f"Error toggling recurring task #{task_id} for user {user_id}: {e}", exc_info=True)
            return None

    async def delete_task(self, task_id: int, user_id: int) -> bool:
        """
        Deletes a recurring task.
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM recurring_tasks WHERE id = ? AND user_id = ?", (task_id, user_id))
                conn.commit()
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Error deleting recurring task #{task_id} for user {user_id}: {e}", exc_info=True)
            return False

    async def get_all_active_tasks(self) -> List[Dict[str, Any]]:
        """
        Returns all active (is_active = 1) recurring tasks across all users.
        Used for initializing APScheduler triggers on startup.
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM recurring_tasks WHERE is_active = 1")
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Error getting all active recurring tasks: {e}", exc_info=True)
            return []

    async def update_last_triggered(self, task_id: int) -> bool:
        """
        Updates last_triggered_at timestamp for task_id.
        """
        now_str = get_now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("UPDATE recurring_tasks SET last_triggered_at = ? WHERE id = ?", (now_str, task_id))
                conn.commit()
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Error updating last_triggered_at for recurring task #{task_id}: {e}", exc_info=True)
            return False


recurring_service = RecurringService()
