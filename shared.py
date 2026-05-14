from firebase_admin import initialize_app, db, storage, _apps
import io
import telegram

# Check if the app is already running so we don't drop a nasty duplicate initialization error
if not _apps:
    app = initialize_app(options={
        'databaseURL': 'https://commonvoice-ua-default-rtdb.firebaseio.com/',
        'storageBucket': 'commonvoice-ua.firebasestorage.app'
    })
else:
    app = _apps[0]
MAIN_MENU = telegram.ReplyKeyboardMarkup([
    [telegram.KeyboardButton(text='🌐 Долучитись до проєкту', web_app=telegram.WebAppInfo(url="https://commonvoice.mozilla.org/uk/listen"))],
    [telegram.KeyboardButton(text='👥 Поділитись з другом')],
    [telegram.KeyboardButton(text='📊 Статистика')]

], resize_keyboard=True)


CONTRIBUTION_TEXT = """👨‍💻 **Навчімо сучасні технології говорити українською!**


Ваш голос — це внесок у майбутнє української мови в цифровому просторі.
🔗 Тисніть кнопку нижче і долучайтеся! 🇺🇦"""

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

def execute_telegram_broadcast(bot_token: str, text: str, media = None, media_type: str = "photo") -> None:
    if not bot_token:
        print("Broadcast cancelled: Bot token is missing.")
        return

    # Grab the whole user directory block from database fields
    all_users_raw = db.reference('users').get()
    user_ids = normalize_user_ids(all_users_raw)
    if not user_ids:
        print("Broadcast aborted: No recipients found.")
        return

    bot = telegram.Bot(token=bot_token)
    media_type = media_type.lower()

    async def async_dispatch():
        import asyncio
        success, fail = 0, 0
        async with bot:
            for uid in user_ids:
                try:
                    if uid <= 0: 
                        continue
                    if not media:
                        await bot.send_message(chat_id=uid, text=text, parse_mode='Markdown')
                    elif media_type == "photo":
                        # Clone inside memory context so stream references don't fracture across loops
                        if isinstance(media, io.BytesIO):
                            media.seek(0)
                            payload = io.BytesIO(media.getvalue())
                        else:
                            payload = media
                        await bot.send_photo(chat_id=uid, photo=payload, caption=text, parse_mode='Markdown')
                    elif media_type == "video":
                        if isinstance(media, io.BytesIO):
                            media.seek(0)
                            payload = io.BytesIO(media.getvalue())
                        else:
                            payload = media
                        await bot.send_video(chat_id=uid, video=payload, caption=text, parse_mode='Markdown')
                    success += 1
                    # Small throttle delay so the Telegram server framework doesn't flag us for spamming the gateway
                    await asyncio.sleep(0.04)
                except telegram.error.RetryAfter as delay:
                    # Catch automated rate limits on the fly and wait out the exact cooling off cooldown duration
                    await asyncio.sleep(delay.cooling_time)
                except Exception:
                    fail += 1
        print(f"🏁 Broadcast complete. Success: {success} | Failed: {fail}")

    import asyncio
    asyncio.run(async_dispatch())