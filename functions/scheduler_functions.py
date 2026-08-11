from firebase_functions import db_fn, scheduler_fn, options
from firebase_admin import db
import requests
import io
import os
import time
import uuid
import zoneinfo
from datetime import datetime, timedelta, timezone

from shared import app, execute_telegram_broadcast, download_bytes_from_storage

USER_RETENTION_DAYS = 365
IMAGE_CACHE_RETENTION_DAYS = 14

@scheduler_fn.on_schedule(
    schedule="0 0 * * *",
    timezone="Europe/Kyiv",
    memory=options.MemoryOption.MB_512,
)
def collect_voice_stats(event):
    """Hits the Mozilla Common Voice API daily at midnight to scrape fresh metrics."""
    url = "https://commonvoice.mozilla.org/api/v1/stats/languages"
    try:
        response = requests.get(url, timeout=15)
        response.raise_for_status()

        # Loop through the JSON array to fish out the Ukrainian ('uk') localized entry
        uk = next((l for l in response.json() if l.get('locale') == 'uk'), None)
        if uk:
            now = datetime.now(zoneinfo.ZoneInfo("Europe/Kyiv"))
            stats = {
                "recorded": int(round(uk.get('recordedHours', 0))),
                "validated": int(round(uk.get('validatedHours', 0))),
                "speakers": int(uk.get('speakersCount', 0)),
                "timestamp": now.isoformat()
            }
            # Log it twice: save a snapshot for historical charting and overwrite the absolute latest stats
            today = now.strftime("%Y-%m-%d")
            db.reference(f'history/{today}').set(stats)
            db.reference('latest').set(stats)
    except Exception as e:
        print(f"Collection failed: {e}")


@scheduler_fn.on_schedule(schedule="0 11 * * 0", timezone="Europe/Kyiv", timeout_sec=300, secrets=["TOKEN"], memory=options.MemoryOption.GB_1)
def weekly_sunday_growth_broadcast(event: scheduler_fn.ScheduledEvent) -> None:
    """Calculates weekly net gains and pushes a celebratory progress update to everyone."""
    token = os.environ.get("TOKEN")
    today = datetime.now(zoneinfo.ZoneInfo("Europe/Kyiv"))
    today_str = today.strftime("%Y-%m-%d")
    past_str = (today - timedelta(days=7)).strftime("%Y-%m-%d")

    # Pull today's data (fall back to the global 'latest' node if needed) and compare it against last week
    today_ref = db.reference(f'history/{today_str}').get() or db.reference('latest').get()
    past_ref = db.reference(f'history/{past_str}').get()
    if not today_ref or not past_ref:
        return

    # Calculate differences between current stats and last week's data points
    growth_delta = int(today_ref.get('speakers', 0)) - int(past_ref.get('speakers', 0))
    recorded_delta = int(today_ref.get('recorded', 0)) - int(past_ref.get('recorded', 0))
    validated_delta = int(today_ref.get('validated', 0)) - int(past_ref.get('validated', 0))

    # If nothing changed at all this week, skip the broadcast so we don't spam users
    if growth_delta <= 0 and recorded_delta <= 0 and validated_delta <= 0:
        return

    # Pull the celebration banner asset from our Cloud Storage bucket
    bg_bytes = download_bytes_from_storage("excited.png")
    if not bg_bytes:
        return
    final_buf = io.BytesIO(bg_bytes)

    # Compile markdown text mapping out the positive net deltas
    message_lines = ["🎉 **Неймовірні новини за цей тиждень! Ось наш прогрес:**\n"]
    if growth_delta > 0:
        message_lines.append(f"👥 До спільноти читачів приєдналося: **+{growth_delta}** нових читачів (Разом: **{today_ref.get('speakers')}**)")
    if recorded_delta > 0:
        message_lines.append(f"🎙 Записано нових годин: **+{recorded_delta} год.** (Разом: **{today_ref.get('recorded')} год.**)")
    if validated_delta > 0:
        message_lines.append(f"✅ Перевірено нових годин: **+{validated_delta} год.** (Разом: **{today_ref.get('validated')} год.**)")
    message_lines.append("\nДякуємо кожному за внесок у розвиток українського мовлення. Продовжуємо в тому ж дусі! 🇺🇦")

    execute_telegram_broadcast(bot_token=token, text="\n".join(message_lines), media=final_buf, media_type="photo")


@scheduler_fn.on_schedule(schedule="0 10 * * *", timezone="Europe/Kyiv", timeout_sec=300, secrets=["TOKEN"], memory=options.MemoryOption.GB_1)
def daily_recurring_broadcast_checker(event: scheduler_fn.ScheduledEvent) -> None:
    """Scans the DB daily to look for special holiday events or recurring anniversary items."""
    token = os.environ.get("TOKEN")
    today = datetime.now(zoneinfo.ZoneInfo("Europe/Kyiv"))
    all_broadcasts = db.reference('recurring_broadcasts').get()
    if not all_broadcasts:
        return

    # Look for matching day/month combinations while making sure we didn't run this script yet this year
    active_id, broadcast_data = None, None
    for b_id, data in all_broadcasts.items():
        if data.get('month') == today.month and data.get('day') == today.day and data.get('last_run_year') != today.year:
            active_id, broadcast_data = b_id, data
            break
    if not active_id:
        return

    # Grab the associated graphic asset link, dispatch the alert, and flip the tracking year flag
    photo_bytes = download_bytes_from_storage(broadcast_data.get('image_url'))
    payload = io.BytesIO(photo_bytes) if photo_bytes else None
    execute_telegram_broadcast(bot_token=token, text=broadcast_data.get('text'), media=payload, media_type="photo" if payload else "text")
    db.reference('recurring_broadcasts').child(active_id).update({'last_run_year': today.year})


@db_fn.on_value_created(
    reference="/broadcast_queue/{item_id}",
    timeout_sec=300,
    secrets=["TOKEN"],
    memory=options.MemoryOption.GB_1,
    min_instances=0,
)
def process_broadcast_queue_item(event: db_fn.Event[dict]) -> None:
    """Immediately deliver a newly created broadcast queue item."""
    token = os.environ.get("TOKEN")
    item_id = event.params.get('item_id')
    if not item_id:
        print("Broadcast queue event is missing item_id.")
        return

    item_ref = db.reference('broadcast_queue').child(str(item_id))
    claim_id = uuid.uuid4().hex
    now = int(time.time())

    def claim_item(current):
        if not isinstance(current, dict) or current.get('processed') is True:
            return current
        if current.get('status') in ('failed', 'invalid'):
            return current
        if current.get('status') == 'processing' and current.get('lease_until', 0) > now:
            return current

        claimed = dict(current)
        claimed.update({
            'status': 'processing',
            'claim_id': claim_id,
            'lease_until': now + 600,
        })
        return claimed

    claimed_item = item_ref.transaction(claim_item)
    if not isinstance(claimed_item, dict) or claimed_item.get('claim_id') != claim_id:
        return

    msg_text = claimed_item.get('text')
    img_url = claimed_item.get('image_url')
    queued_media_type = claimed_item.get('media_type') or ("photo" if img_url else "text")
    if not msg_text:
        item_ref.update({'status': 'invalid', 'lease_until': 0})
        return

    media_payload = img_url
    is_firebase_media = isinstance(img_url, str) and (
        img_url.startswith('gs://') or 'firebasestorage.googleapis.com' in img_url
    )
    if is_firebase_media:
        media_bytes = download_bytes_from_storage(img_url)
        if media_bytes:
            media_payload = io.BytesIO(media_bytes)
        else:
            media_payload = None
            result = {
                'success': 0,
                'failed': 1,
                'error': f'Could not download queued media: {img_url}',
            }

    if not is_firebase_media or media_payload is not None:
        try:
            result = execute_telegram_broadcast(
                bot_token=token,
                text=msg_text,
                media=media_payload,
                media_type=queued_media_type
            )
        except Exception as exc:
            result = {'success': 0, 'failed': 1, 'error': str(exc)}

    def finish_claim(current):
        if not isinstance(current, dict) or current.get('claim_id') != claim_id:
            return current
        finished = dict(current)
        finished['delivery_result'] = result
        finished['lease_until'] = 0
        if result.get('failed', 0) == 0 and result.get('success', 0) > 0:
            finished.update({'processed': True, 'status': 'completed'})
        else:
            finished.update({'processed': False, 'status': 'failed'})
        return finished

    item_ref.transaction(finish_claim)


@scheduler_fn.on_schedule(
    schedule="0 3 * * *",
    timezone="Europe/Kyiv",
    memory=options.MemoryOption.MB_256,
)
def cleanup_expired_data(event: scheduler_fn.ScheduledEvent) -> None:
    """Remove inactive user profiles and obsolete generated-image caches."""
    now = datetime.now(timezone.utc)
    user_cutoff = now - timedelta(days=USER_RETENTION_DAYS)
    users = db.reference('users').get() or {}

    if isinstance(users, dict):
        for uid, details in users.items():
            profile = details.get('profile', {}) if isinstance(details, dict) else {}
            last_seen_raw = profile.get('last_seen')
            if not last_seen_raw:
                continue
            try:
                last_seen = datetime.fromisoformat(str(last_seen_raw))
                if last_seen.tzinfo is None:
                    last_seen = last_seen.replace(tzinfo=timezone.utc)
                if last_seen.astimezone(timezone.utc) < user_cutoff:
                    db.reference('users').child(str(uid)).delete()
            except (TypeError, ValueError):
                print(f"Skipped user {uid}: invalid last_seen value.")

    cache_cutoff = now.date() - timedelta(days=IMAGE_CACHE_RETENTION_DAYS)
    caches = db.reference('database_image_cache').get() or {}
    if isinstance(caches, dict):
        for date_key in caches:
            try:
                if datetime.strptime(str(date_key), "%Y-%m-%d").date() < cache_cutoff:
                    db.reference('database_image_cache').child(str(date_key)).delete()
            except ValueError:
                print(f"Skipped image cache with invalid date key: {date_key}")
