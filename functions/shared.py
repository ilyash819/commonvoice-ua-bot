from firebase_admin import initialize_app, db, storage, _apps
import io
import json
import os
import telegram

# Check if the app is already running so we don't drop a nasty duplicate initialization error
if not _apps:
    try:
        firebase_config = json.loads(os.environ.get("FIREBASE_CONFIG", "{}"))
    except json.JSONDecodeError:
        firebase_config = {}
    app_options = {}
    database_url = os.environ.get("DATABASE_URL") or firebase_config.get("databaseURL")
    storage_bucket = os.environ.get("STORAGE_BUCKET") or firebase_config.get("storageBucket")
    if database_url:
        app_options["databaseURL"] = database_url
    if storage_bucket:
        app_options["storageBucket"] = storage_bucket
    app = initialize_app(options=app_options or None)
else:
    app = _apps[0]

def normalize_user_ids(all_users) -> list:
    """Safely extracts valid integer user IDs from Firebase list or dict structures."""
    if not all_users:
        return []
    # If firebase formats this as a dict, grab the keys and cast them straight to strings/ints
    if isinstance(all_users, dict):
        return [int(uid) for uid in all_users.keys() if str(uid).isdigit()]
    # If firebase maps this as an indexed list/array, use the list position values as active user IDs
    if isinstance(all_users, list):
        return [idx for idx, val in enumerate(all_users) if val is not None]
    return []

def download_bytes_from_storage(path_or_url: str) -> bytes or None:
    if not path_or_url:
        return None
    try:
        bucket = storage.bucket()
        clean_path = path_or_url.strip()

        # If it's a web token URL, split out the storage path markers and unquote the encoded characters
        if "firebasestorage.googleapis.com" in clean_path:
            parts = clean_path.split("/o/")
            if len(parts) > 1:
                encoded_path = parts[1].split("?")[0]
                from urllib.parse import unquote
                clean_path = unquote(encoded_path)
        # Drop the storage identifier prefix to get the raw asset directory locator path
        elif clean_path.startswith("gs://"):
            path_no_prefix = clean_path.replace("gs://", "", 1)
            clean_path = "/".join(path_no_prefix.split("/")[1:])

        blob = bucket.blob(clean_path)
        return blob.download_as_bytes()
    except Exception as e:
        print(f"Failed to fetch asset from Firebase Storage: {e}")
        return None

def execute_telegram_broadcast(bot_token: str, text: str, media = None, media_type: str = "photo") -> dict:
    if not bot_token:
        print("Broadcast cancelled: Bot token is missing.")
        return {'success': 0, 'failed': 1, 'error': 'Bot token is missing.'}

    # Grab the whole user directory block from database fields
    all_users_raw = db.reference('users').get()
    user_ids = normalize_user_ids(all_users_raw)
    if not user_ids:
        print("Broadcast aborted: No recipients found.")
        return {'success': 0, 'failed': 1, 'error': 'No recipients found.'}

    bot = telegram.Bot(token=bot_token)
    media_type = media_type.lower()

    async def async_dispatch():
        import asyncio
        semaphore = asyncio.Semaphore(8)
        pace_lock = asyncio.Lock()
        next_send_at = 0.0

        async def pace_request():
            nonlocal next_send_at
            async with pace_lock:
                loop = asyncio.get_running_loop()
                now = loop.time()
                if next_send_at > now:
                    await asyncio.sleep(next_send_at - now)
                    now = loop.time()
                next_send_at = max(now, next_send_at) + 0.04

        async def deliver(uid):
            if uid <= 0:
                return None

            for attempt in range(2):
                try:
                    await pace_request()
                    async with semaphore:
                        if not media:
                            await bot.send_message(chat_id=uid, text=text, parse_mode='Markdown')
                        elif media_type == "photo":
                            payload = io.BytesIO(media.getvalue()) if isinstance(media, io.BytesIO) else media
                            await bot.send_photo(chat_id=uid, photo=payload, caption=text, parse_mode='Markdown')
                        elif media_type == "video":
                            payload = io.BytesIO(media.getvalue()) if isinstance(media, io.BytesIO) else media
                            await bot.send_video(chat_id=uid, video=payload, caption=text, parse_mode='Markdown')
                    return True
                except telegram.error.RetryAfter as delay:
                    if attempt == 1:
                        return False
                    retry_after = delay.retry_after
                    wait_seconds = retry_after.total_seconds() if hasattr(retry_after, 'total_seconds') else float(retry_after)
                    await asyncio.sleep(max(0, wait_seconds) + 0.1)
                except Exception as exc:
                    print(f"Broadcast delivery failed for chat {uid}: {exc}")
                    return False
            return False

        async with bot:
            results = await asyncio.gather(*(deliver(uid) for uid in user_ids))
        success = sum(result is True for result in results)
        fail = sum(result is False for result in results)
        print(f"🏁 Broadcast complete. Success: {success} | Failed: {fail}")
        return {'success': success, 'failed': fail}

    import asyncio
    return asyncio.run(async_dispatch())
