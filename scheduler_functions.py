from firebase_functions import scheduler_fn, options
from firebase_admin import db
import requests
import io
import os
import zoneinfo
from datetime import datetime, timedelta

from shared import app, execute_telegram_broadcast, download_bytes_from_storage
from bot_function import show_statistics

@scheduler_fn.on_schedule(schedule="0 0 * * *", memory=options.MemoryOption.MB_512)
def collect_voice_stats(event):
    """Hits the Mozilla Common Voice API daily at midnight to scrape fresh metrics."""
    url = "https://commonvoice.mozilla.org/api/v1/stats/languages"
    try:
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        
        # Loop through the JSON array to fish out the Ukrainian ('uk') localized entry
        uk = next((l for l in response.json() if l.get('locale') == 'uk'), None)
        if uk:
            stats = {
                "recorded": int(round(uk.get('recordedHours', 0))),
                "validated": int(round(uk.get('validatedHours', 0))),
                "speakers": int(uk.get('speakersCount', 0)),
                "timestamp": datetime.now().isoformat()
            }
            # Log it twice: save a snapshot for historical charting and overwrite the absolute latest stats
            today = datetime.now().strftime("%Y-%m-%d")
            db.reference(f'history/{today}').set(stats)
            db.reference('latest').set(stats)
    except Exception as e: 
        print(f"Collection failed: {e}")


@scheduler_fn.on_schedule(schedule="0 11 * * 6", timezone="Europe/Kyiv", timeout_sec=300, secrets=["TOKEN"], memory=options.MemoryOption.GB_1)
def weekly_sunday_growth_broadcast(event: scheduler_fn.ScheduledEvent) -> None:
    """Calculates weekly net gains and pushes a celebratory progress update to everyone."""
    token = os.environ.get("TOKEN")
    today = datetime.now()
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
    bg_bytes = download_bytes_from_storage("gs://commonvoice-ua.firebasestorage.app/excited.png")
    if not bg_bytes: 
        return
    final_buf = io.BytesIO(bg_bytes)

    # Compile markdown text mapping out the positive net deltas
    message_lines = ["🎉 **Неймовірні новини за цей тиждень! Ось наш прогрес:**\n"]
    if growth_delta > 0: 
        message_lines.append(f"👥 До спільноти приєдналося: **+{growth_delta}** нових читачів (Разом: **{today_ref.get('speakers')}**)")
    if recorded_delta > 0: 
        message_lines.append(f"🎙 Записано нових годин: **+{recorded_delta} god.** (Разом: **{today_ref.get('recorded')} god.**)")
    if validated_delta > 0: 
        message_lines.append(f"✅ Перевірено нових годин: **+{validated_delta} god.** (Разом: **{today_ref.get('validated')} god.**)")
    message_lines.append("\nДякуємо кожному за внесок у розвиток українського мовлення. Продовжуємо в тому ж дусі! 🇺🇦")
    
    execute_telegram_broadcast(bot_token=token, text="\n".join(message_lines), media=final_buf, media_type="photo")


@scheduler_fn.on_schedule(schedule="0 12 * * 0", timezone="Europe/Kyiv", timeout_sec=300, secrets=["TOKEN"], memory=options.MemoryOption.GB_1)
def weekly_hours_growth_broadcast(event: scheduler_fn.ScheduledEvent) -> None:
    """Regular Sunday job that builds and broadcasts the visualization chart to the channel."""
    token = os.environ.get("TOKEN")
    broadcast_caption, graph_buf = show_statistics()
    
    # Safety bail out if something broke down in the chart builder logic
    if "статистика наразі недоступна" in broadcast_caption and graph_buf is None: 
        return
    execute_telegram_broadcast(bot_token=token, text=broadcast_caption, media=graph_buf, media_type="photo" if graph_buf else "text")


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


@scheduler_fn.on_schedule(schedule="*/5 * * * *", timezone="Europe/Kyiv", timeout_sec=300, secrets=["TOKEN"], memory=options.MemoryOption.GB_1)
def process_broadcast_queue(event: scheduler_fn.ScheduledEvent) -> None:
    """Runs a fast polling loop every 5 minutes to dump out pending broadcast queue rows."""
    token = os.environ.get("TOKEN")
    queue_data = db.reference('broadcast_queue').get()
    if not queue_data: 
        return
    items = queue_data.items() if isinstance(queue_data, dict) else enumerate(queue_data)

    for item_id, item_content in items:
        # Ignore dead cells or tasks that are already flagged as processed
        if not item_content or item_content.get('processed') is True: 
            continue
        msg_text = item_content.get('text')
        img_url = item_content.get('image_url')
        if not msg_text: 
            continue

        # Fire off the update payload and instantly seal the record row to prevent blast multi-runs
        execute_telegram_broadcast(bot_token=token, text=msg_text, media=img_url, media_type="photo" if img_url else "text")
        db.reference('broadcast_queue').child(str(item_id)).update({'processed': True})