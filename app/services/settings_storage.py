import os
import sqlite3
import logging
from app.config import settings

logger = logging.getLogger(__name__)


def init_settings_table():
    db_path = settings.DATABASE_PATH
    db_dir = os.path.dirname(db_path)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)
    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS app_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)
            conn.commit()
    except Exception as e:
        logger.error(f"Failed to init app_settings table in SQLite: {e}", exc_info=True)


def load_settings_from_db():
    """
    Loads persisted settings from SQLite on startup to prevent overrides on code reload.
    """
    init_settings_table()
    try:
        with sqlite3.connect(settings.DATABASE_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT key, value FROM app_settings")
            rows = cursor.fetchall()
            for key, val in rows:
                if hasattr(settings, key):
                    current_val = getattr(settings, key)
                    if isinstance(current_val, bool):
                        setattr(settings, key, str(val).lower() in ("true", "1", "yes"))
                    elif isinstance(current_val, int):
                        try:
                            setattr(settings, key, int(val))
                        except ValueError:
                            pass
                    else:
                        setattr(settings, key, val)
                    logger.info(f"Loaded persistent setting from DB: {key}={getattr(settings, key)}")
    except Exception as e:
        logger.error(f"Failed to load persistent settings from DB: {e}", exc_info=True)


def save_setting_to_db(key: str, value: str):
    """
    Saves a setting to SQLite table app_settings so it survives container recreation and code updates.
    """
    init_settings_table()
    try:
        with sqlite3.connect(settings.DATABASE_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, str(value))
            )
            conn.commit()
            logger.info(f"Saved persistent setting to DB: {key}={value}")
    except Exception as e:
        logger.error(f"Failed to save setting {key} to SQLite DB: {e}", exc_info=True)
