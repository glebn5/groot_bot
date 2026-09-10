import re
import logging
from datetime import date, datetime, timedelta, time
from typing import Optional, Tuple
from app.utils.timezone import get_now, get_today, get_tz

logger = logging.getLogger(__name__)

WEEKDAYS_RU = {
    "понедельник": 0, "пн": 0,
    "вторник": 1, "вт": 1,
    "среда": 2, "среду": 2, "ср": 2,
    "четверг": 3, "чт": 3,
    "пятница": 4, "пятницу": 4, "пт": 4,
    "суббота": 5, "субботу": 5, "сб": 5,
    "воскресенье": 6, "вс": 6
}

MONTHS_RU = {
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4,
    "ма": 5, "июн": 6, "июл": 7, "август": 8,
    "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12
}


def ensure_tz_aware(dt: datetime) -> datetime:
    """
    Ensures datetime is timezone-aware with configured application timezone.
    """
    tz = get_tz()
    if dt.tzinfo is None:
        return dt.replace(tzinfo=tz)
    return dt.astimezone(tz)


def is_past_datetime(dt: datetime) -> bool:
    """
    Checks whether given datetime is in the past relative to current time.
    """
    now = get_now()
    aware_dt = ensure_tz_aware(dt)
    return aware_dt <= now


def parse_date_str(val: str, base_date: Optional[date] = None) -> Optional[date]:
    """
    Parses a date string (ISO 'YYYY-MM-DD', 'DD.MM.YYYY', 'DD.MM', or Russian relative word).
    """
    if not val or not isinstance(val, str):
        return None

    clean = val.strip().lower()
    if not base_date:
        base_date = get_today()

    # Relative words
    if clean in ["сегодня", "today"]:
        return base_date
    if clean in ["завтра", "tomorrow"]:
        return base_date + timedelta(days=1)
    if clean in ["послезавтра"]:
        return base_date + timedelta(days=2)
    if clean in ["вчера", "yesterday"]:
        return base_date - timedelta(days=1)
    if clean in ["позавчера"]:
        return base_date - timedelta(days=2)

    # Weekday words
    for day_word, day_idx in WEEKDAYS_RU.items():
        if day_word in clean:
            days_ahead = (day_idx - base_date.weekday()) % 7
            if "следующ" in clean or days_ahead == 0:
                days_ahead = days_ahead if days_ahead > 0 else 7
            return base_date + timedelta(days=days_ahead)

    # ISO YYYY-MM-DD
    iso_match = re.search(r'\b(20\d\d)-([0-1]?\d)-([0-3]?\d)\b', clean)
    if iso_match:
        try:
            return date(int(iso_match.group(1)), int(iso_match.group(2)), int(iso_match.group(3)))
        except ValueError:
            pass

    # DD.MM.YYYY
    dmy_match = re.search(r'\b([0-3]?\d)\.([0-1]?\d)\.(20\d\d)\b', clean)
    if dmy_match:
        try:
            return date(int(dmy_match.group(3)), int(dmy_match.group(2)), int(dmy_match.group(1)))
        except ValueError:
            pass

    # DD.MM (defaults to current year)
    dm_match = re.search(r'\b([0-3]?\d)\.([0-1]?\d)\b', clean)
    if dm_match:
        try:
            return date(base_date.year, int(dm_match.group(2)), int(dm_match.group(1)))
        except ValueError:
            pass

    # Russian text like "15 сентября"
    day_month_match = re.search(r'\b([0-3]?\d)\s+([а-яё]+)\b', clean)
    if day_month_match:
        d_num = int(day_month_match.group(1))
        m_word = day_month_match.group(2)
        for m_prefix, m_idx in MONTHS_RU.items():
            if m_word.startswith(m_prefix):
                try:
                    return date(base_date.year, m_idx, d_num)
                except ValueError:
                    pass

    return None


def parse_time_str(val: str) -> Optional[Tuple[int, int]]:
    """
    Parses time string like '15:30', '15:00', '15', 'в 15', '15-00'.
    Returns (hour, minute) tuple.
    """
    if not val or not isinstance(val, str):
        return None

    clean = val.strip().lower()

    # HH:MM or HH-MM
    match = re.search(r'\b([0-1]?\d|2[0-3])[:\-]([0-5]\d)\b', clean)
    if match:
        return int(match.group(1)), int(match.group(2))

    # Just hour e.g. "в 15", "15:00", "в 9 утра"
    h_match = re.search(r'\b(?:в\s*)?([0-1]?\d|2[0-3])\s*(?:ч|час|часов|:00)?\b', clean)
    if h_match:
        hour = int(h_match.group(1))
        if "вечера" in clean and hour < 12:
            hour += 12
        return hour, 0

    return None


def parse_datetime_str(val: str, base_dt: Optional[datetime] = None) -> Optional[datetime]:
    """
    Parses datetime string into timezone-aware datetime.
    Supports ISO formats, 'YYYY-MM-DD HH:MM:SS', 'YYYY-MM-DDTHH:MM:SS',
    and relative expressions like 'через 30 минут', 'через 2 часа'.
    """
    if not val or not isinstance(val, str):
        return None

    tz = get_tz()
    now = base_dt or get_now()
    clean = val.strip()

    # Relative "через N минут / часов"
    rel_match = re.search(r'через\s+(\d+)\s*(мин|час)', clean.lower())
    if rel_match:
        count = int(rel_match.group(1))
        unit = rel_match.group(2)
        if unit.startswith("мин"):
            return now + timedelta(minutes=count)
        else:
            return now + timedelta(hours=count)

    # ISO formats
    iso_clean = clean.replace(" ", "T")
    try:
        dt = datetime.fromisoformat(iso_clean)
        return ensure_tz_aware(dt)
    except Exception:
        pass

    # Separate date and time parts
    parts = clean.split()
    if len(parts) >= 2:
        parsed_d = parse_date_str(parts[0], now.date())
        parsed_t = parse_time_str(parts[1])
        if parsed_d and parsed_t:
            combined = datetime.combine(parsed_d, time(hour=parsed_t[0], minute=parsed_t[1]))
            return ensure_tz_aware(combined)

    return None
