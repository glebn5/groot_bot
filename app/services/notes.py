import os
import sqlite3
import logging
from datetime import datetime
from typing import List, Dict, Any
from app.config import settings

logger = logging.getLogger(__name__)


class NotesService:
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
                    CREATE TABLE IF NOT EXISTS user_notes (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL,
                        content TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )
                """)
                conn.commit()
                logger.info("Notes table initialized successfully in SQLite.")
        except Exception as e:
            logger.error(f"Failed to initialize notes table: {e}", exc_info=True)

    async def add_note(self, user_id: int, content: str) -> int:
        """
        Saves a new note for user_id and returns the created note ID.
        """
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO user_notes (user_id, content, created_at) VALUES (?, ?, ?)",
                    (user_id, content.strip(), now_str)
                )
                conn.commit()
                note_id = cursor.lastrowid
                logger.info(f"Saved note #{note_id} for user_id={user_id}")
                return note_id
        except Exception as e:
            logger.error(f"Error saving note: {e}", exc_info=True)
            raise RuntimeError(f"Failed to save note: {str(e)}")

    async def get_notes(self, user_id: int) -> List[Dict[str, Any]]:
        """
        Retrieves all active notes for user_id ordered by created_at DESC.
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT id, content, created_at FROM user_notes WHERE user_id = ? ORDER BY id DESC",
                    (user_id,)
                )
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Error fetching notes for user_id={user_id}: {e}", exc_info=True)
            return []

    async def delete_note(self, note_id: int, user_id: int) -> bool:
        """
        Deletes a specific note by ID for user_id.
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "DELETE FROM user_notes WHERE id = ? AND user_id = ?",
                    (note_id, user_id)
                )
                conn.commit()
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Error deleting note #{note_id}: {e}", exc_info=True)
            return False

    async def clear_notes(self, user_id: int) -> bool:
        """
        Deletes all notes for user_id.
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM user_notes WHERE user_id = ?", (user_id,))
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Error clearing notes for user_id={user_id}: {e}", exc_info=True)
            return False


notes_service = NotesService()
