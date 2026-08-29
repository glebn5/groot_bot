import os
import time
from datetime import datetime, date
from zoneinfo import ZoneInfo
from app.config import settings


def init_timezone():
    """
    Sets the process timezone environment variable to configured TIMEZONE (Europe/Moscow).
    Calls time.tzset() on POSIX platforms to ensure process-wide timezone compliance.
    """
    os.environ["TZ"] = settings.TIMEZONE
    if hasattr(time, "tzset"):
        time.tzset()


def get_tz() -> ZoneInfo:
    """Returns ZoneInfo object for the configured timezone."""
    return ZoneInfo(settings.TIMEZONE)


def get_now() -> datetime:
    """Returns current datetime in configured timezone."""
    return datetime.now(get_tz())


def get_today() -> date:
    """Returns current date in configured timezone."""
    return get_now().date()
