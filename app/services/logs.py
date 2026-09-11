import os
import glob
import logging
from typing import Dict, Any, List
from app.config import settings

logger = logging.getLogger(__name__)


def get_log_stats() -> Dict[str, Any]:
    """
    Returns information about the current log file and backups.
    """
    path = settings.LOG_FILE_PATH
    exists = os.path.exists(path)
    size_bytes = os.path.getsize(path) if exists else 0
    
    if size_bytes < 1024:
        size_str = f"{size_bytes} Б"
    elif size_bytes < 1024 * 1024:
        size_str = f"{size_bytes / 1024:.1f} КБ"
    else:
        size_str = f"{size_bytes / (1024 * 1024):.2f} МБ"

    max_mb = settings.LOG_MAX_BYTES / (1024 * 1024)
    
    # Check backups
    backups: List[Dict[str, Any]] = []
    base_dir = os.path.dirname(path)
    base_name = os.path.basename(path)
    if os.path.exists(base_dir):
        pattern = os.path.join(base_dir, f"{base_name}.*")
        for b_path in sorted(glob.glob(pattern)):
            b_size = os.path.getsize(b_path)
            b_name = os.path.basename(b_path)
            backups.append({
                "name": b_name,
                "size_kb": f"{b_size / 1024:.1f} КБ"
            })

    return {
        "path": path,
        "exists": exists,
        "size_bytes": size_bytes,
        "size_str": size_str,
        "max_mb": max_mb,
        "backup_count": settings.LOG_BACKUP_COUNT,
        "backups": backups
    }


def get_log_tail(max_lines: int = 20, errors_only: bool = False, max_chars: int = 3500) -> str:
    """
    Reads the tail of the log file, optionally filtering for ERROR/CRITICAL/WARNING.
    Safely limits total character count for Telegram messages.
    """
    path = settings.LOG_FILE_PATH
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return "ℹ️ Файл логов пока пуст или еще не создан."

    try:
        # Read the last ~100KB to be fast and memory-efficient
        chunk_size = 100 * 1024
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            file_len = f.tell()
            seek_pos = max(0, file_len - chunk_size)
            f.seek(seek_pos)
            content_bytes = f.read()

        content = content_bytes.decode("utf-8", errors="replace")
        lines = content.splitlines()

        # Filter if requested
        if errors_only:
            lines = [line for line in lines if any(lvl in line for lvl in ("ERROR", "CRITICAL", "WARNING"))]
            if not lines:
                return "✅ В последних логах нет ошибок или предупреждений!"

        # Take the last max_lines
        selected_lines = lines[-max_lines:]

        # Ensure total length doesn't exceed max_chars
        result_text = "\n".join(selected_lines)
        if len(result_text) > max_chars:
            result_text = result_text[-max_chars:]
            # Trim to the first complete line if cut in middle
            if "\n" in result_text:
                result_text = "..." + result_text.split("\n", 1)[1]

        return result_text
    except Exception as e:
        logger.error(f"Error reading log file tail: {e}", exc_info=True)
        return f"⚠️ Ошибка при чтении логов: {e}"


def clear_log_file() -> bool:
    """
    Truncates current log file to 0 bytes.
    """
    path = settings.LOG_FILE_PATH
    try:
        if os.path.exists(path):
            with open(path, "w", encoding="utf-8") as f:
                f.truncate(0)
        return True
    except Exception as e:
        logger.error(f"Error clearing log file: {e}", exc_info=True)
        return False
