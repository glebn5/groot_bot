import os
import sqlite3
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional
from app.config import settings
from app.utils.timezone import get_now

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
                    CREATE TABLE IF NOT EXISTS user_note_folders (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL,
                        name TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS user_notes (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL,
                        content TEXT NOT NULL,
                        folder_id INTEGER DEFAULT NULL,
                        created_at TEXT NOT NULL
                    )
                """)
                
                # Migration check for existing user_notes table without folder_id
                cursor.execute("PRAGMA table_info(user_notes)")
                columns = [row[1] for row in cursor.fetchall()]
                if "folder_id" not in columns:
                    cursor.execute("ALTER TABLE user_notes ADD COLUMN folder_id INTEGER DEFAULT NULL")
                    logger.info("Added folder_id column to user_notes table.")

                conn.commit()
                logger.info("Notes and Folders tables initialized successfully in SQLite.")
        except Exception as e:
            logger.error(f"Failed to initialize notes tables: {e}", exc_info=True)

    # --- FOLDER / SECTION METHODS ---

    async def create_folder(self, user_id: int, name: str) -> int:
        """
        Creates a new folder/section for user_id and returns the new folder ID.
        """
        now_str = get_now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO user_note_folders (user_id, name, created_at) VALUES (?, ?, ?)",
                    (user_id, name.strip(), now_str)
                )
                conn.commit()
                folder_id = cursor.lastrowid
                logger.info(f"Created folder #{folder_id} '{name}' for user_id={user_id}")
                return folder_id
        except Exception as e:
            logger.error(f"Error creating folder: {e}", exc_info=True)
            return 0

    async def get_folders(self, user_id: int) -> List[Dict[str, Any]]:
        """
        Retrieves all folders for user_id with note count for each folder.
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT f.id, f.name, f.created_at, COUNT(n.id) AS note_count
                    FROM user_note_folders f
                    LEFT JOIN user_notes n ON f.id = n.folder_id AND n.user_id = f.user_id
                    WHERE f.user_id = ?
                    GROUP BY f.id, f.name, f.created_at
                    ORDER BY f.id ASC
                """, (user_id,))
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Error fetching folders for user_id={user_id}: {e}", exc_info=True)
            return []

    async def get_folder_by_id(self, folder_id: int, user_id: int) -> Optional[Dict[str, Any]]:
        """
        Retrieves a single folder by ID for user_id.
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT f.id, f.name, f.created_at, COUNT(n.id) AS note_count FROM user_note_folders f LEFT JOIN user_notes n ON f.id = n.folder_id WHERE f.id = ? AND f.user_id = ? GROUP BY f.id",
                    (folder_id, user_id)
                )
                row = cursor.fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.error(f"Error fetching folder #{folder_id}: {e}", exc_info=True)
            return None

    async def delete_folder(self, folder_id: int, user_id: int, delete_contained_notes: bool = False) -> bool:
        """
        Deletes a folder for user_id.
        If delete_contained_notes is True, deletes all notes inside this folder.
        If False, updates notes to folder_id = NULL (Unsorted).
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                if delete_contained_notes:
                    cursor.execute("DELETE FROM user_notes WHERE folder_id = ? AND user_id = ?", (folder_id, user_id))
                else:
                    cursor.execute("UPDATE user_notes SET folder_id = NULL WHERE folder_id = ? AND user_id = ?", (folder_id, user_id))
                
                cursor.execute("DELETE FROM user_note_folders WHERE id = ? AND user_id = ?", (folder_id, user_id))
                conn.commit()
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Error deleting folder #{folder_id}: {e}", exc_info=True)
            return False

    async def get_unsorted_notes_count(self, user_id: int) -> int:
        """
        Returns count of unsorted notes (folder_id IS NULL).
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM user_notes WHERE user_id = ? AND folder_id IS NULL", (user_id,))
                return cursor.fetchone()[0]
        except Exception as e:
            logger.error(f"Error counting unsorted notes: {e}", exc_info=True)
            return 0

    # --- NOTE METHODS ---

    async def add_note(self, user_id: int, content: str, folder_id: Optional[int] = None) -> int:
        """
        Saves a new note for user_id and returns the created note ID.
        """
        now_str = get_now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO user_notes (user_id, content, folder_id, created_at) VALUES (?, ?, ?, ?)",
                    (user_id, content.strip(), folder_id, now_str)
                )
                conn.commit()
                note_id = cursor.lastrowid
                logger.info(f"Saved note #{note_id} (folder={folder_id}) for user_id={user_id}")
                return note_id
        except Exception as e:
            logger.error(f"Error saving note: {e}", exc_info=True)
            raise RuntimeError(f"Failed to save note: {str(e)}")

    async def get_notes(self, user_id: int, folder_id: Optional[int] = None, unsorted_only: bool = False) -> List[Dict[str, Any]]:
        """
        Retrieves notes for user_id.
        If unsorted_only is True, retrieves notes with folder_id IS NULL.
        If folder_id is provided, retrieves notes for that specific folder.
        If neither, retrieves all notes for user_id.
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                if unsorted_only:
                    cursor.execute(
                        "SELECT id, content, folder_id, created_at FROM user_notes WHERE user_id = ? AND folder_id IS NULL ORDER BY id DESC",
                        (user_id,)
                    )
                elif folder_id is not None:
                    cursor.execute(
                        "SELECT id, content, folder_id, created_at FROM user_notes WHERE user_id = ? AND folder_id = ? ORDER BY id DESC",
                        (user_id, folder_id)
                    )
                else:
                    cursor.execute(
                        "SELECT id, content, folder_id, created_at FROM user_notes WHERE user_id = ? ORDER BY id DESC",
                        (user_id,)
                    )
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Error fetching notes for user_id={user_id}: {e}", exc_info=True)
            return []

    async def get_note_by_id(self, note_id: int, user_id: int) -> Optional[Dict[str, Any]]:
        """
        Retrieves a single note by ID for user_id.
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT id, content, folder_id, created_at FROM user_notes WHERE id = ? AND user_id = ?",
                    (note_id, user_id)
                )
                row = cursor.fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.error(f"Error fetching note #{note_id} for user_id={user_id}: {e}", exc_info=True)
            return None

    async def update_note(self, note_id: int, user_id: int, new_content: str) -> bool:
        """
        Updates the content of a note by ID for user_id.
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE user_notes SET content = ? WHERE id = ? AND user_id = ?",
                    (new_content.strip(), note_id, user_id)
                )
                conn.commit()
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Error updating note #{note_id}: {e}", exc_info=True)
            return False

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

    async def clear_notes(self, user_id: int, folder_id: Optional[int] = None, unsorted_only: bool = False) -> bool:
        """
        Deletes notes for user_id (all notes, or for a specific folder, or unsorted).
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                if unsorted_only:
                    cursor.execute("DELETE FROM user_notes WHERE user_id = ? AND folder_id IS NULL", (user_id,))
                elif folder_id is not None:
                    cursor.execute("DELETE FROM user_notes WHERE user_id = ? AND folder_id = ?", (user_id, folder_id))
                else:
                    cursor.execute("DELETE FROM user_notes WHERE user_id = ?", (user_id,))
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Error clearing notes for user_id={user_id}: {e}", exc_info=True)
            return False


notes_service = NotesService()
