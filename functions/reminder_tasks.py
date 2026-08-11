"""Cloud Tasks-backed contribution reminder creation and delivery."""

import asyncio
from datetime import datetime, timezone
import hashlib
from functools import lru_cache
import os
import time
import uuid

from firebase_admin import db, functions
from firebase_functions import options, tasks_fn
from firebase_functions.options import RateLimits, RetryConfig
import google.auth
from google.auth.transport.requests import AuthorizedSession
import telegram

from reminder_schedule import DEFAULT_TIMEZONE, next_occurrence, validate_time
from shared import app


TASK_FUNCTION_NAME = "deliver_reminder"
TASK_REGION = "us-central1"
REMINDER_TEXT = (
    "⏰ Час зробити внесок у Mozilla Common Voice! 🇺🇦\n\n"
    "Навіть 5–10 хвилин допоможуть технологіям краще розуміти українську мову.\n"
    "https://commonvoice.mozilla.org/uk/listen"
)


@lru_cache(maxsize=1)
def _function_url() -> str:
    """Resolve the deployed gen-2 service URI and cache it per warm instance."""
    credentials, discovered_project_id = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    project_id = (
        discovered_project_id
        or app.project_id
        or os.environ.get("GOOGLE_CLOUD_PROJECT")
        or os.environ.get("GCLOUD_PROJECT")
    )
    if not project_id:
        raise RuntimeError("Could not determine the Google Cloud project ID.")
    metadata_url = (
        "https://cloudfunctions.googleapis.com/v2beta/"
        f"projects/{project_id}/locations/{TASK_REGION}/functions/{TASK_FUNCTION_NAME}"
    )
    response = AuthorizedSession(credentials).get(metadata_url, timeout=10)
    response.raise_for_status()
    service_uri = response.json().get("serviceConfig", {}).get("uri")
    if not service_uri:
        raise RuntimeError(f"Could not resolve URL for {TASK_FUNCTION_NAME}.")
    return service_uri


def _task_id(user_id: str, version: str, scheduled_for: str) -> str:
    digest = hashlib.sha256(f"{user_id}|{version}|{scheduled_for}".encode()).hexdigest()
    return f"reminder-{digest}"


def _already_exists(exc: Exception) -> bool:
    code = getattr(exc, "code", "")
    return "already-exists" in str(code).lower() or "already exists" in str(exc).lower()


def enqueue_reminder(user_id: str, version: str, schedule_time: datetime) -> None:
    """Enqueue one versioned delivery task at an absolute UTC time."""
    schedule_time = schedule_time.astimezone(timezone.utc)
    scheduled_for = schedule_time.isoformat()
    body = {
        "data": {
            "user_id": str(user_id),
            "reminder_version": version,
            "scheduled_for": scheduled_for,
        }
    }
    task_options = functions.TaskOptions(
        schedule_time=schedule_time,
        dispatch_deadline_seconds=60,
        task_id=_task_id(str(user_id), version, scheduled_for),
        uri=_function_url(),
    )
    try:
        functions.task_queue(TASK_FUNCTION_NAME).enqueue(body, task_options)
    except Exception as exc:
        # A retry after enqueueing must be safe even if the previous invocation
        # crashed before recording next_run_at in Realtime Database.
        if not _already_exists(exc):
            raise


def create_reminder(user_id: int, frequency: str, time_value: str, weekday: int | None = None) -> dict:
    """Persist a new reminder version and schedule its first task."""
    normalized_time = validate_time(time_value)
    reminder = {
        "enabled": True,
        "frequency": frequency,
        "time": normalized_time,
        "timezone": DEFAULT_TIMEZONE,
        "version": uuid.uuid4().hex,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if frequency == "weekly":
        if weekday not in range(1, 8):
            raise ValueError("Weekly reminders require a weekday from 1 to 7.")
        reminder["weekday"] = weekday
    elif frequency != "daily":
        raise ValueError("Reminder frequency must be daily or weekly.")

    run_at = next_occurrence(reminder)
    reminder["next_run_at"] = run_at.isoformat()
    reminder_ref = db.reference("users").child(str(user_id)).child("reminder")
    previous = reminder_ref.get()
    reminder_ref.set(reminder)

    try:
        enqueue_reminder(str(user_id), reminder["version"], run_at)
    except Exception:
        def restore_previous(current):
            if not isinstance(current, dict) or current.get("version") != reminder["version"]:
                return current
            return previous

        reminder_ref.transaction(restore_previous)
        raise

    return reminder


def _clear_claim(reminder_ref, version: str, claim_id: str) -> None:
    def clear(current):
        if not isinstance(current, dict) or current.get("version") != version:
            return current
        claim = current.get("delivery_claim")
        if not isinstance(claim, dict) or claim.get("id") != claim_id:
            return current
        updated = dict(current)
        updated.pop("delivery_claim", None)
        return updated

    reminder_ref.transaction(clear)


@tasks_fn.on_task_dispatched(
    region=TASK_REGION,
    secrets=["TOKEN"],
    timeout_sec=60,
    memory=options.MemoryOption.MB_256,
    min_instances=0,
    retry_config=RetryConfig(max_attempts=5, min_backoff_seconds=30),
    rate_limits=RateLimits(max_concurrent_dispatches=20),
)
def deliver_reminder(request: tasks_fn.CallableRequest) -> dict:
    """Validate, claim, deliver, and reschedule one contribution reminder."""
    payload = request.data
    if not isinstance(payload, dict):
        return {"status": "invalid"}

    user_id = str(payload.get("user_id", ""))
    version = str(payload.get("reminder_version", ""))
    scheduled_for = str(payload.get("scheduled_for", ""))
    if not user_id.isdigit() or not version or not scheduled_for:
        return {"status": "invalid"}

    reminder_ref = db.reference("users").child(user_id).child("reminder")
    claim_id = uuid.uuid4().hex
    now_epoch = int(time.time())

    def claim_delivery(current):
        if not isinstance(current, dict) or not current.get("enabled"):
            return current
        if current.get("version") != version or current.get("last_sent_for") == scheduled_for:
            return current
        existing_claim = current.get("delivery_claim")
        if isinstance(existing_claim, dict) and existing_claim.get("lease_until", 0) > now_epoch:
            return current
        claimed = dict(current)
        claimed["delivery_claim"] = {
            "id": claim_id,
            "scheduled_for": scheduled_for,
            "lease_until": now_epoch + 120,
        }
        return claimed

    reminder = reminder_ref.transaction(claim_delivery)
    if not isinstance(reminder, dict) or reminder.get("version") != version:
        return {"status": "obsolete"}

    claim = reminder.get("delivery_claim")
    already_sent = reminder.get("last_sent_for") == scheduled_for
    owns_claim = isinstance(claim, dict) and claim.get("id") == claim_id
    if not owns_claim and not already_sent:
        return {"status": "already_claimed"}

    if owns_claim:
        token = os.environ.get("TOKEN")
        if not token:
            _clear_claim(reminder_ref, version, claim_id)
            raise RuntimeError("Reminder delivery cancelled: bot token is missing.")

        async def dispatch() -> None:
            bot = telegram.Bot(token=token)
            async with bot:
                await bot.send_message(chat_id=int(user_id), text=REMINDER_TEXT)

        try:
            asyncio.run(dispatch())
        except Exception:
            _clear_claim(reminder_ref, version, claim_id)
            raise

        def finish_delivery(current):
            if not isinstance(current, dict) or current.get("version") != version:
                return current
            updated = dict(current)
            updated["last_sent_for"] = scheduled_for
            current_claim = updated.get("delivery_claim")
            if isinstance(current_claim, dict) and current_claim.get("id") == claim_id:
                updated.pop("delivery_claim", None)
            return updated

        reminder = reminder_ref.transaction(finish_delivery)

    # If the reminder changed during delivery, its replacement already owns a
    # separate task. Never extend the obsolete schedule.
    if not isinstance(reminder, dict) or reminder.get("version") != version or not reminder.get("enabled"):
        return {"status": "sent_obsolete" if owns_claim else "obsolete"}

    if reminder.get("next_scheduled_after") == scheduled_for:
        return {"status": "sent" if owns_claim else "already_sent"}

    next_run = next_occurrence(reminder)
    enqueue_reminder(user_id, version, next_run)

    def record_next(current):
        if not isinstance(current, dict) or current.get("version") != version:
            return current
        updated = dict(current)
        updated["next_run_at"] = next_run.isoformat()
        updated["next_scheduled_after"] = scheduled_for
        return updated

    reminder_ref.transaction(record_next)
    return {"status": "sent" if owns_claim else "rescheduled"}
