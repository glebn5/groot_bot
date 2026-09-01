import os
import sqlite3
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional
from app.config import settings
from app.utils.timezone import get_now, get_today

logger = logging.getLogger(__name__)


class GoalsService:
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
                    CREATE TABLE IF NOT EXISTS monthly_goals (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL,
                        goal_text TEXT NOT NULL,
                        target_month TEXT NOT NULL,
                        is_completed INTEGER DEFAULT 0,
                        created_at TEXT NOT NULL
                    )
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS user_goal_settings (
                        user_id INTEGER PRIMARY KEY,
                        is_enabled INTEGER DEFAULT 1,
                        days_of_week TEXT DEFAULT 'mon,thu',
                        time_str TEXT DEFAULT '10:00'
                    )
                """)
                conn.commit()
                logger.info("Monthly goals tables initialized successfully in SQLite.")
        except Exception as e:
            logger.error(f"Failed to initialize monthly goals tables: {e}", exc_info=True)

    async def add_goal(self, user_id: int, goal_text: str, target_month: Optional[str] = None) -> int:
        """
        Adds a new monthly goal for user_id for target_month (YYYY-MM).
        """
        if not target_month:
            target_month = get_today().strftime("%Y-%m")

        now_str = get_now().strftime("%Y-%m-%d %H:%M:%S")

        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO monthly_goals (user_id, goal_text, target_month, is_completed, created_at) VALUES (?, ?, ?, 0, ?)",
                    (user_id, goal_text.strip(), target_month, now_str)
                )
                conn.commit()
                goal_id = cursor.lastrowid
                logger.info(f"Added goal #{goal_id} for user_id={user_id} for month {target_month}")
                return goal_id
        except Exception as e:
            logger.error(f"Error adding goal for user {user_id}: {e}", exc_info=True)
            return 0

    async def get_goals(self, user_id: int, target_month: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Returns all goals for user_id for a specific target_month (YYYY-MM).
        If target_month is None, defaults to current month.
        """
        if not target_month:
            target_month = get_today().strftime("%Y-%m")

        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT * FROM monthly_goals WHERE user_id = ? AND target_month = ? ORDER BY is_completed ASC, id ASC",
                    (user_id, target_month)
                )
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Error fetching goals for user {user_id}: {e}", exc_info=True)
            return []

    async def toggle_goal(self, goal_id: int, user_id: int) -> Optional[bool]:
        """
        Toggles is_completed status for goal_id owned by user_id.
        Returns new boolean state or None if goal not found.
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("SELECT is_completed FROM monthly_goals WHERE id = ? AND user_id = ?", (goal_id, user_id))
                row = cursor.fetchone()
                if not row:
                    return None

                new_state = 0 if row["is_completed"] == 1 else 1
                cursor.execute("UPDATE monthly_goals SET is_completed = ? WHERE id = ?", (new_state, goal_id))
                conn.commit()
                return bool(new_state)
        except Exception as e:
            logger.error(f"Error toggling goal {goal_id} for user {user_id}: {e}", exc_info=True)
            return None

    async def delete_goal(self, goal_id: int, user_id: int) -> bool:
        """
        Deletes a goal owned by user_id.
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM monthly_goals WHERE id = ? AND user_id = ?", (goal_id, user_id))
                conn.commit()
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Error deleting goal {goal_id} for user {user_id}: {e}", exc_info=True)
            return False

    async def get_goal_settings(self, user_id: int) -> Dict[str, Any]:
        """
        Retrieves user goal reminder settings or creates default settings if not exists.
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM user_goal_settings WHERE user_id = ?", (user_id,))
                row = cursor.fetchone()
                if row:
                    return dict(row)

                # Insert default settings
                cursor.execute(
                    "INSERT INTO user_goal_settings (user_id, is_enabled, days_of_week, time_str) VALUES (?, 1, 'mon,thu', '10:00')",
                    (user_id,)
                )
                conn.commit()
                return {"user_id": user_id, "is_enabled": 1, "days_of_week": "mon,thu", "time_str": "10:00"}
        except Exception as e:
            logger.error(f"Error getting goal settings for user {user_id}: {e}", exc_info=True)
            return {"user_id": user_id, "is_enabled": 1, "days_of_week": "mon,thu", "time_str": "10:00"}

    async def update_goal_settings(
        self,
        user_id: int,
        is_enabled: Optional[bool] = None,
        days_of_week: Optional[str] = None,
        time_str: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Updates user goal reminder settings.
        """
        current = await self.get_goal_settings(user_id)
        new_is_enabled = current["is_enabled"] if is_enabled is None else (1 if is_enabled else 0)
        new_days = current["days_of_week"] if days_of_week is None else days_of_week
        new_time = current["time_str"] if time_str is None else time_str

        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO user_goal_settings (user_id, is_enabled, days_of_week, time_str)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET
                        is_enabled = excluded.is_enabled,
                        days_of_week = excluded.days_of_week,
                        time_str = excluded.time_str
                """, (user_id, new_is_enabled, new_days, new_time))
                conn.commit()
                return {"user_id": user_id, "is_enabled": new_is_enabled, "days_of_week": new_days, "time_str": new_time}
        except Exception as e:
            logger.error(f"Error updating goal settings for user {user_id}: {e}", exc_info=True)
            return current

    async def get_all_users_with_enabled_reminders(self) -> List[Dict[str, Any]]:
        """
        Returns settings for all users who have goal reminders enabled.
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM user_goal_settings WHERE is_enabled = 1")
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Error getting enabled goal users: {e}", exc_info=True)
            return []


goals_service = GoalsService()
