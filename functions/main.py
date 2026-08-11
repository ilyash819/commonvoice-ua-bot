"""Firebase Functions entry point.

Implementations live in focused modules so each function has one source of truth.
Firebase discovers these imported, decorated functions through this module.
"""

from bot_function import telegram_bot
from scheduler_functions import (
    collect_voice_stats,
    weekly_sunday_growth_broadcast,
    daily_recurring_broadcast_checker,
    process_broadcast_queue_item,
    cleanup_expired_data,
)
from reminder_tasks import deliver_reminder

__all__ = [
    "telegram_bot",
    "collect_voice_stats",
    "weekly_sunday_growth_broadcast",
    "daily_recurring_broadcast_checker",
    "process_broadcast_queue_item",
    "cleanup_expired_data",
    "deliver_reminder",
]
