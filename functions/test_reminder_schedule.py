import unittest
from datetime import datetime, timezone

from reminder_schedule import next_occurrence, validate_time


class ReminderScheduleTests(unittest.TestCase):
    def test_time_validation(self):
        self.assertEqual(validate_time("09:05"), "09:05")
        for invalid in ("9:05", "24:00", "12:60", "noon"):
            with self.assertRaises(ValueError):
                validate_time(invalid)

    def test_daily_later_today(self):
        reminder = {"frequency": "daily", "time": "14:30", "timezone": "Europe/Kyiv"}
        now = datetime(2026, 8, 11, 10, 0, tzinfo=timezone.utc)  # 13:00 Kyiv
        self.assertEqual(next_occurrence(reminder, now), datetime(2026, 8, 11, 11, 30, tzinfo=timezone.utc))

    def test_daily_time_that_passed_moves_to_tomorrow(self):
        reminder = {"frequency": "daily", "time": "12:00", "timezone": "Europe/Kyiv"}
        now = datetime(2026, 8, 11, 10, 0, tzinfo=timezone.utc)  # 13:00 Kyiv
        self.assertEqual(next_occurrence(reminder, now), datetime(2026, 8, 12, 9, 0, tzinfo=timezone.utc))

    def test_weekly_uses_requested_weekday(self):
        reminder = {"frequency": "weekly", "weekday": 1, "time": "09:30", "timezone": "Europe/Kyiv"}
        now = datetime(2026, 8, 11, 10, 0, tzinfo=timezone.utc)  # Tuesday
        self.assertEqual(next_occurrence(reminder, now), datetime(2026, 8, 17, 6, 30, tzinfo=timezone.utc))

    def test_timezone_offset_changes_across_dst(self):
        reminder = {"frequency": "daily", "time": "09:00", "timezone": "Europe/Kyiv"}
        winter_now = datetime(2026, 1, 10, 0, 0, tzinfo=timezone.utc)
        summer_now = datetime(2026, 7, 10, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(next_occurrence(reminder, winter_now).hour, 7)
        self.assertEqual(next_occurrence(reminder, summer_now).hour, 6)


if __name__ == "__main__":
    unittest.main()
