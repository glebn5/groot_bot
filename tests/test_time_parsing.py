import unittest
from datetime import date, datetime, timedelta
from app.agent.time_parser import (
    parse_date_str,
    parse_time_str,
    parse_datetime_str,
    ensure_tz_aware,
    is_past_datetime
)
from app.utils.timezone import get_now, get_today


class TestTimeParsing(unittest.TestCase):
    def test_relative_dates(self):
        today = date(2026, 9, 10)
        self.assertEqual(parse_date_str("сегодня", today), today)
        self.assertEqual(parse_date_str("завтра", today), date(2026, 9, 11))
        self.assertEqual(parse_date_str("послезавтра", today), date(2026, 9, 12))
        self.assertEqual(parse_date_str("вчера", today), date(2026, 9, 9))
        self.assertEqual(parse_date_str("позавчера", today), date(2026, 9, 8))

    def test_explicit_dates(self):
        self.assertEqual(parse_date_str("2026-09-15"), date(2026, 9, 15))
        self.assertEqual(parse_date_str("15.09.2026"), date(2026, 9, 15))
        today = date(2026, 9, 10)
        self.assertEqual(parse_date_str("15.09", today), date(2026, 9, 15))
        self.assertEqual(parse_date_str("20 сентября", today), date(2026, 9, 20))

    def test_weekdays(self):
        # 2026-09-10 is Thursday (weekday index 3)
        base = date(2026, 9, 10)
        # Friday is tomorrow (weekday index 4)
        self.assertEqual(parse_date_str("в пятницу", base), date(2026, 9, 11))
        # Monday next week is 2026-09-14
        self.assertEqual(parse_date_str("в понедельник", base), date(2026, 9, 14))

    def test_time_parsing(self):
        self.assertEqual(parse_time_str("15:00"), (15, 0))
        self.assertEqual(parse_time_str("15:30"), (15, 30))
        self.assertEqual(parse_time_str("9:15"), (9, 15))
        self.assertEqual(parse_time_str("в 15"), (15, 0))
        self.assertEqual(parse_time_str("8 вечера"), (20, 0))

    def test_datetime_parsing(self):
        now = get_now()
        dt_res = parse_datetime_str("2026-09-11 15:00")
        self.assertIsNotNone(dt_res)
        self.assertEqual(dt_res.year, 2026)
        self.assertEqual(dt_res.month, 9)
        self.assertEqual(dt_res.day, 11)
        self.assertEqual(dt_res.hour, 15)
        self.assertEqual(dt_res.minute, 0)

        # Relative offset
        dt_rel = parse_datetime_str("через 30 минут", now)
        self.assertIsNotNone(dt_rel)
        diff = (dt_rel - now).total_seconds()
        self.assertAlmostEqual(diff, 1800, delta=2)

    def test_past_datetime_detection(self):
        now = get_now()
        past = now - timedelta(hours=2)
        future = now + timedelta(hours=2)
        self.assertTrue(is_past_datetime(past))
        self.assertFalse(is_past_datetime(future))


if __name__ == "__main__":
    unittest.main()
