import os
import sqlite3
import logging
from datetime import date, datetime
from typing import List, Dict, Any, Optional
from app.config import settings
from app.utils.timezone import get_now, get_today

logger = logging.getLogger(__name__)


class TasksService:
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
                    CREATE TABLE IF NOT EXISTS user_tasks (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL,
                        task_text TEXT NOT NULL,
                        target_date TEXT NOT NULL,
                        is_completed INTEGER DEFAULT 0,
                        created_at TEXT NOT NULL
                    )
                """)
                conn.commit()
                logger.info("Tasks table initialized successfully in SQLite.")
        except Exception as e:
            logger.error(f"Failed to initialize tasks table: {e}", exc_info=True)

    async def add_task(self, user_id: int, task_text: str, target_date: Optional[date] = None) -> int:
        """
        Adds a new daily task for user_id on target_date.
        """
        if not target_date:
            target_date = get_today()

        date_str = target_date.strftime("%Y-%m-%d")
        now_str = get_now().strftime("%Y-%m-%d %H:%M:%S")

        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO user_tasks (user_id, task_text, target_date, is_completed, created_at) VALUES (?, ?, ?, 0, ?)",
                    (user_id, task_text.strip(), date_str, now_str)
                )
                conn.commit()
                task_id = cursor.lastrowid
                logger.info(f"Added task #{task_id} for user_id={user_id} on {date_str}")
                return task_id
        except Exception as e:
            logger.error(f"Error adding task: {e}", exc_info=True)
            raise RuntimeError(f"Failed to add task: {str(e)}")

    async def get_tasks(self, user_id: int, target_date: date) -> List[Dict[str, Any]]:
        """
        Gets all tasks for user_id on target_date.
        """
        return await self.get_tasks_for_date_range(user_id, target_date, target_date)

    async def get_tasks_for_date_range(self, user_id: int, start_date: date, end_date: date) -> List[Dict[str, Any]]:
        """
        Gets all tasks for user_id between start_date and end_date.
        """
        start_str = start_date.strftime("%Y-%m-%d")
        end_str = end_date.strftime("%Y-%m-%d")
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT id, task_text, target_date, is_completed, created_at FROM user_tasks WHERE user_id = ? AND target_date >= ? AND target_date <= ? ORDER BY target_date ASC, id ASC",
                    (user_id, start_str, end_str)
                )
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Error getting tasks for range {start_str} to {end_str}: {e}", exc_info=True)
            return []

    async def move_task(self, user_id: int, query: str, to_date: date, from_date: Optional[date] = None) -> Optional[Dict[str, Any]]:
        """
        Finds a task matching query (and optional from_date) and updates its target_date to to_date.
        """
        to_date_str = to_date.strftime("%Y-%m-%d")
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()

                if from_date:
                    from_date_str = from_date.strftime("%Y-%m-%d")
                    cursor.execute(
                        "SELECT id, task_text, target_date FROM user_tasks WHERE user_id = ? AND target_date = ? AND task_text LIKE ? LIMIT 1",
                        (user_id, from_date_str, f"%{query.strip()}%")
                    )
                else:
                    cursor.execute(
                        "SELECT id, task_text, target_date FROM user_tasks WHERE user_id = ? AND task_text LIKE ? ORDER BY id DESC LIMIT 1",
                        (user_id, f"%{query.strip()}%")
                    )

                row = cursor.fetchone()
                if not row:
                    # Fallback: search for any task of that user on from_date if query is vague
                    if from_date:
                        from_date_str = from_date.strftime("%Y-%m-%d")
                        cursor.execute(
                            "SELECT id, task_text, target_date FROM user_tasks WHERE user_id = ? AND target_date = ? ORDER BY id DESC LIMIT 1",
                            (user_id, from_date_str)
                        )
                        row = cursor.fetchone()

                if not row:
                    logger.warning(f"No task found matching query '{query}' for user_id={user_id}")
                    return None

                task_id = row["id"]
                task_text = row["task_text"]
                old_date_str = row["target_date"]

                cursor.execute(
                    "UPDATE user_tasks SET target_date = ? WHERE id = ?",
                    (to_date_str, task_id)
                )
                conn.commit()
                logger.info(f"Moved task #{task_id} ('{task_text}') from {old_date_str} to {to_date_str}")
                return {
                    "id": task_id,
                    "task_text": task_text,
                    "old_date": old_date_str,
                    "new_date": to_date_str
                }
        except Exception as e:
            logger.error(f"Error moving task matching '{query}': {e}", exc_info=True)
            return None

    async def complete_task(self, user_id: int, task_id: int) -> bool:
        """
        Marks a task as completed.
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE user_tasks SET is_completed = 1 WHERE id = ? AND user_id = ?",
                    (task_id, user_id)
                )
                conn.commit()
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Error completing task #{task_id}: {e}", exc_info=True)
            return False

    async def toggle_task(self, user_id: int, task_id: int) -> bool:
        """
        Toggles completion status of a task (0 <-> 1).
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE user_tasks SET is_completed = 1 - is_completed WHERE id = ? AND user_id = ?",
                    (task_id, user_id)
                )
                conn.commit()
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Error toggling task #{task_id}: {e}", exc_info=True)
            return False

    async def move_task_by_id(self, user_id: int, task_id: int, to_date: date) -> bool:
        """
        Moves a task by ID to a new target_date.
        """
        to_date_str = to_date.strftime("%Y-%m-%d")
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE user_tasks SET target_date = ? WHERE id = ? AND user_id = ?",
                    (to_date_str, task_id, user_id)
                )
                conn.commit()
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Error moving task #{task_id} to {to_date_str}: {e}", exc_info=True)
            return False

    async def delete_task(self, user_id: int, task_id: int) -> bool:
        """
        Deletes a task by ID.
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "DELETE FROM user_tasks WHERE id = ? AND user_id = ?",
                    (task_id, user_id)
                )
                conn.commit()
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Error deleting task #{task_id}: {e}", exc_info=True)
            return False

    async def clear_tasks_for_date(self, user_id: int, target_date: Optional[date] = None) -> int:
        """
        Clears all tasks for user_id on target_date (or all tasks if target_date is None).
        Returns number of deleted tasks.
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                if target_date:
                    date_str = target_date.strftime("%Y-%m-%d")
                    cursor.execute("DELETE FROM user_tasks WHERE user_id = ? AND target_date = ?", (user_id, date_str))
                else:
                    cursor.execute("DELETE FROM user_tasks WHERE user_id = ?", (user_id,))
                conn.commit()
                deleted_count = cursor.rowcount
                logger.info(f"Cleared {deleted_count} tasks for user_id={user_id}")
                return deleted_count
        except Exception as e:
            logger.error(f"Error clearing tasks: {e}", exc_info=True)
            return 0

    async def delete_task_by_query(self, user_id: int, query: str, target_date: Optional[date] = None) -> bool:
        """
        Deletes a single task matching keywords.
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                if target_date:
                    date_str = target_date.strftime("%Y-%m-%d")
                    cursor.execute(
                        "DELETE FROM user_tasks WHERE id IN (SELECT id FROM user_tasks WHERE user_id = ? AND target_date = ? AND task_text LIKE ? LIMIT 1)",
                        (user_id, date_str, f"%{query.strip()}%")
                    )
                else:
                    cursor.execute(
                        "DELETE FROM user_tasks WHERE id IN (SELECT id FROM user_tasks WHERE user_id = ? AND task_text LIKE ? ORDER BY id DESC LIMIT 1)",
                        (user_id, f"%{query.strip()}%")
                    )
                conn.commit()
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Error deleting task matching '{query}': {e}", exc_info=True)
            return False


    async def search_tasks(self, user_id: int, query: str) -> List[Dict[str, Any]]:
        """
        Searches tasks matching query keywords for user_id (Cyrillic case-insensitive).
        """
        stop_words = {"завтра", "сегодня", "вчера", "когда", "где", "во", "сколько", "напомни", "планы", "на", "для", "что", "есть"}
        clean_words = [w.lower().strip("?!.,") for w in query.split() if len(w.strip("?!.,")) >= 2 and w.lower().strip("?!.,") not in stop_words]
        if not clean_words:
            clean_words = [query.lower().strip("?!.,")]

        results = {}
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.create_function("lower", 1, lambda s: s.lower() if isinstance(s, str) else s)
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()

                for w in clean_words:
                    cursor.execute(
                        "SELECT id, task_text, target_date, is_completed, created_at FROM user_tasks WHERE user_id = ? AND lower(task_text) LIKE ? ORDER BY target_date ASC, id ASC",
                        (user_id, f"%{w}%")
                    )
                    for row in cursor.fetchall():
                        d = dict(row)
                        results[d["id"]] = d

                return list(results.values())
        except Exception as e:
            logger.error(f"Error searching tasks matching '{query}': {e}", exc_info=True)
            return []


tasks_service = TasksService()
