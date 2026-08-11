"""Pure scheduling helpers for contribution reminders."""

from datetime import datetime, timedelta, timezone
import re
import zoneinfo


DEFAULT_TIMEZONE = "Europe/Kyiv"
TIME_PATTERN = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


def validate_time(value: str) -> str:
    """Return a normalized HH:MM value or raise ValueError."""
    candidate = str(value or "").strip()
    if not TIME_PATTERN.fullmatch(candidate):
        raise ValueError("Time must use 24-hour HH:MM format.")
    return candidate


def next_occurrence(reminder: dict, now: datetime | None = None) -> datetime:
    """Calculate the next reminder occurrence and return it in UTC."""
    if not isinstance(reminder, dict):
        raise ValueError("Reminder must be a dictionary.")

    time_value = validate_time(reminder.get("time", ""))
    try:
        reminder_zone = zoneinfo.ZoneInfo(reminder.get("timezone", DEFAULT_TIMEZONE))
    except (TypeError, zoneinfo.ZoneInfoNotFoundError) as exc:
        raise ValueError("Unknown reminder timezone.") from exc

    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    local_now = now.astimezone(reminder_zone)
    hour, minute = (int(part) for part in time_value.split(":"))
    frequency = reminder.get("frequency")

    if frequency == "daily":
        days_ahead = 0
    elif frequency == "weekly":
        weekday = reminder.get("weekday")
        if not isinstance(weekday, int) or weekday not in range(1, 8):
            raise ValueError("Weekly reminders require a weekday from 1 to 7.")
        days_ahead = (weekday - local_now.isoweekday()) % 7
    else:
        raise ValueError("Reminder frequency must be daily or weekly.")

    candidate_date = local_now.date() + timedelta(days=days_ahead)
    candidate = datetime(
        candidate_date.year,
        candidate_date.month,
        candidate_date.day,
        hour,
        minute,
        tzinfo=reminder_zone,
    )

    if candidate <= local_now:
        candidate += timedelta(days=1 if frequency == "daily" else 7)

    # Conversion through UTC applies the timezone's daylight-saving rules.
    return candidate.astimezone(timezone.utc)
