from firebase_functions import https_fn, scheduler_fn, options
from firebase_admin import initialize_app, db, storage, _apps
import asyncio
import requests
from datetime import datetime, timedelta
import telegram
from telegram import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
import io
import os
import base64
import pandas as pd
import matplotlib
import zoneinfo
matplotlib.use('Agg') 
import matplotlib.pyplot as plt
from PIL import Image, ImageDraw, ImageFont
import matplotlib.ticker as ticker
from typing import Tuple, Optional

# --- INITIALIZATION ---
if not _apps:
    app = initialize_app(options={
        'databaseURL': 'https://commonvoice-ua-default-rtdb.firebaseio.com/',
        'storageBucket': 'commonvoice-ua.firebasestorage.app'
    })
else:
    app = _apps[0]

# --- GLOBAL STATIC ARCHITECTURE ---
MAIN_MENU = ReplyKeyboardMarkup([
    [KeyboardButton(text='🌐 Долучитись до проєкту', web_app=WebAppInfo(url="https://commonvoice.mozilla.org/uk/listen"))],
    [KeyboardButton(text='📊 Статистика')]  
], resize_keyboard=True)

CONTRIBUTION_TEXT = """👨‍💻 **Навчімо сучасні технології говорити українською!**

Голосові асистенти, автоматичні субтитри та розумні пристрої працюють на основі масштабних мовних моделей. Проєкт Mozilla Common Voice змінює правила гри, створюючи повністю відкриту й доступну для кожного базу голосових даних.

Український датасет активно розвивається, проте для бездоганної роботи алгоритмів розпізнавання мовлення критично бракує різноманітності. Штучному інтелекту потрібні голоси різного віку, з різними тембрами, регіональними акцентами та діалектами.

Прагнете, щоб цифрові технології в усьому світі бездоганно розуміли та відтворювали українську мову?

Приділіть проєкту всього 5–10 хвилин:
• **Начитуйте текст:** записуйте запропоновані фрази своїм природним голосом.
• **Перевіряйте інших:** слухайте аудіозаписи та підтверджуйте точність вимови, поки п'єте ранкову каву.

Ваш голос — це внесок у майбутнє української мови в цифровому просторі.

🔗 Тисніть кнопку нижче і долучайтеся! 🇺🇦"""


# --- HELPER FUNCTIONS (Піднято вгору для безпечного виклику) ---
def normalize_user_ids(all_users) -> list:
    """Safely extracts valid integer user IDs from Firebase list or dict structures."""
    if not all_users:
        return []
    if isinstance(all_users, dict):
        return [int(uid) for uid in all_users.keys() if str(uid).isdigit()]
    if isinstance(all_users, list):
        return [idx for idx, val in enumerate(all_users) if val is not None]
    return []

def download_bytes_from_storage(path_or_url: str) -> bytes or None:
    if not path_or_url:
        return None
    try:
        bucket = storage.bucket()
        clean_path = path_or_url.strip()
        
        if "firebasestorage.googleapis.com" in clean_path:
            parts = clean_path.split("/o/")
            if len(parts) > 1:
                encoded_path = parts[1].split("?")[0]
                from urllib.parse import unquote
                clean_path = unquote(encoded_path)
        elif clean_path.startswith("gs://"):
            path_no_prefix = clean_path.replace("gs://", "", 1)
            clean_path = "/".join(path_no_prefix.split("/")[1:])

        print(f"Fetching raw bytes directly from bucket path: '{clean_path}'")
        blob = bucket.blob(clean_path)
        return blob.download_as_bytes()
    except Exception as e:
        print(f"Failed to fetch asset from Firebase Storage: {e}")
        return None

def generate_history_graph():
    ref = db.reference('history')
    history_data = ref.get()
    if not history_data: 
        return None

    df = pd.DataFrame.from_dict(history_data, orient='index')
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()

    df['recorded'] = df['recorded'].round().astype(int)
    df['validated'] = df['validated'].round().astype(int)

    fig, ax = plt.subplots(figsize=(7, 4.5))
    ax.plot(df.index, df['recorded'], marker='o', color='#0057b7', label='Recorded Hours', linewidth=2.5)
    ax.plot(df.index, df['validated'], marker='s', color='#ffd700', label='Validated Hours', linewidth=2.5)
    
    ax.grid(True, linestyle='--', alpha=0.3)
    ax.legend(loc='upper left')
    ax.yaxis.set_major_locator(ticker.MaxNLocator(integer=True))
    
    plt.xticks(rotation=30)
    plt.tight_layout()

    chart_buf = io.BytesIO()
    plt.savefig(chart_buf, format='png', dpi=120, transparent=True)
    chart_buf.seek(0)
    plt.close()

    try:
        bg_bytes = download_bytes_from_storage("gs://commonvoice-ua.firebasestorage.app/progress.png") 
        if bg_bytes:
            background = Image.open(io.BytesIO(bg_bytes))
        else:
            background = Image.new("RGBA", (1000, 800), "white")
            
        chart_img = Image.open(chart_buf)
        offset = 550, 30
        background.paste(chart_img, offset, chart_img.convert("RGBA"))

        final_buf = io.BytesIO()
        background.save(final_buf, format='PNG')
        final_buf.seek(0)
        return final_buf
    except Exception as e:
        print(f"⚠️ Image compositing failed, returning clean chart instead: {e}")
        chart_buf.seek(0)
        return chart_buf

def show_statistics() -> Tuple[str, Optional[io.BytesIO]]:
    try:
        latest_ref = db.reference('latest').get()
        if not latest_ref:
            return "⚠️ На жаль, статистика наразі недоступна.", None

        current_speakers = latest_ref.get('speakers', 0)
        current_recorded = latest_ref.get('recorded', 0)
        current_validated = latest_ref.get('validated', 0)

        msg = (
            f"📊 **Актуальна статистика проєкту:**\n\n"
            f"👥 Учасників спільноти: **{current_speakers}**\n"
            f"🎙 Записано годин: **{current_recorded} год.**\n"
            f"✅ Перевірено годин: **{current_validated} год.**\n\n"
            f"Дякуємо за ваш внесок! 🇺🇦"
        )

        today_str = datetime.now().strftime("%Y-%m-%d")
        cache_ref = db.reference(f'database_image_cache/{today_str}')
        base64_string = cache_ref.get()

        if base64_string:
            image_bytes = base64.b64decode(base64_string)
            graph_buffer = io.BytesIO(image_bytes)
            graph_buffer.seek(0)
            return msg, graph_buffer
        else:
            generated_buffer = generate_history_graph()
            if generated_buffer:
                raw_bytes = generated_buffer.getvalue()
                encoded_string = base64.b64encode(raw_bytes).decode('utf-8')
                cache_ref.set(encoded_string)
                generated_buffer.seek(0)
                return msg, generated_buffer
            else:
                return msg, None
    except Exception as e:
        print(f"💥 Помилка у методі show_statistics: {e}")
        return "💥 Не вдалося завантажити статистику через технічну помилку.", None

def execute_telegram_broadcast(
    bot_token: str, 
    text: str, 
    media: io.BytesIO | str = None, 
    media_type: str = "photo"
) -> None:
    if not bot_token:
        print("Broadcast cancelled: Bot token is missing.")
        return

    all_users_raw = db.reference('users').get()
    user_ids = normalize_user_ids(all_users_raw)
    if not user_ids:
        print("Broadcast aborted: No recipients found.")
        return

    bot = telegram.Bot(token=bot_token)
    media_type = media_type.lower()

    async def async_dispatch():
        success, fail = 0, 0
        async with bot:
            for uid in user_ids:
                try:
                    if uid <= 0 or uid == 537899542: 
                        continue
                    if not media:
                        await bot.send_message(chat_id=uid, text=text, parse_mode='Markdown')
                    elif media_type == "photo":
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
                    await asyncio.sleep(0.04)
                except telegram.error.RetryAfter as delay:
                    await asyncio.sleep(delay.cooling_time)
                except Exception:
                    fail += 1
        print(f"🏁 Broadcast complete. Success: {success} | Failed: {fail}")

    asyncio.run(async_dispatch())


# --- PLANNER CRON SCHEDULERS ---

@scheduler_fn.on_schedule(schedule="0 0 * * *", memory=options.MemoryOption.MB_512)
def collect_voice_stats(event):
    url = "https://commonvoice.mozilla.org/api/v1/stats/languages"
    try:
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        all_langs = response.json()
        uk = next((l for l in all_langs if l.get('locale') == 'uk'), None)
        if uk:
            stats = {
                "recorded": int(round(uk.get('recordedHours', 0))),
                "validated": int(round(uk.get('validatedHours', 0))),
                "speakers": int(uk.get('speakersCount', 0)),
                "timestamp": datetime.now().isoformat()
            }
            today = datetime.now().strftime("%Y-%m-%d")
            db.reference(f'history/{today}').set(stats)
            db.reference('latest').set(stats)
            print(f"Оновлена статистика за {today}")        
    except Exception as e:
        print(f"Collection failed: {e}")

@scheduler_fn.on_schedule(
    schedule="0 11 * * 6", 
    timezone="Europe/Kyiv",
    timeout_sec=300,
    secrets=["TOKEN"],
    memory=options.MemoryOption.GB_1
)
def weekly_sunday_growth_broadcast(event: scheduler_fn.ScheduledEvent) -> None:
    token = os.environ.get("TOKEN")
    today = datetime.now()
    seven_days_ago = today - timedelta(days=7)
    
    today_str = today.strftime("%Y-%m-%d")
    past_str = seven_days_ago.strftime("%Y-%m-%d")

    today_ref = db.reference(f'history/{today_str}').get()
    past_ref = db.reference(f'history/{past_str}').get()

    if not today_ref:
        today_ref = db.reference('latest').get()

    if not today_ref or not past_ref:
        print("⚠️ Growth metrics unavailable: Missing historical snapshots.")
        return

    current_speakers = int(today_ref.get('speakers', 0))
    previous_speakers = int(past_ref.get('speakers', 0))
    growth_delta = current_speakers - previous_speakers

    current_recorded = int(today_ref.get('recorded', 0))
    current_validated = int(today_ref.get('validated', 0))
    past_recorded = int(past_ref.get('recorded', 0))
    past_validated = int(past_ref.get('validated', 0))

    recorded_delta = current_recorded - past_recorded
    validated_delta = current_validated - past_validated

    if growth_delta <= 0 and recorded_delta <= 0 and validated_delta <= 0:
        print("📉 No statistic updates or growth detected this week. Broadcast skipped.")
        return

    try:
        bg_bytes = download_bytes_from_storage("gs://commonvoice-ua.firebasestorage.app/excited.png")
        if not bg_bytes: return
        final_buf = io.BytesIO(bg_bytes)
        final_buf.seek(0)
    except Exception as err:
        print(f"💥 Failed retrieving static asset: {err}")
        return

    message_lines = ["🎉 **Неймовірні новини за цей тиждень! Ось наш прогрес:**\n"]
    if growth_delta > 0:
        message_lines.append(f"👥 До спільноти приєдналося: **+{growth_delta}** нових читачів (Разом: **{current_speakers}**)")
    if recorded_delta > 0:
        message_lines.append(f"🎙 Записано нових годин: **+{recorded_delta} год.** (Разом: **{current_recorded} год.**)")
    if validated_delta > 0:
        message_lines.append(f"✅ Перевірено нових годин: **+{validated_delta} год.** (Разом: **{current_validated} год.**)")

    message_lines.append("\nДякуємо кожному за внесок у розвиток українського мовлення. Продовжуємо в тому ж дусі! 🇺🇦")
    execute_telegram_broadcast(bot_token=token, text="\n".join(message_lines), media=final_buf, media_type="photo")

@scheduler_fn.on_schedule(
    schedule="0 12 * * 0", 
    timezone="Europe/Kyiv",
    timeout_sec=300,
    secrets=["TOKEN"],
    memory=options.MemoryOption.GB_1
)
def weekly_hours_growth_broadcast(event: scheduler_fn.ScheduledEvent) -> None:
    token = os.environ.get("TOKEN")
    broadcast_caption, graph_buf = show_statistics()

    if "статистика наразі недоступна" in broadcast_caption and graph_buf is None:
        print("Aborting broadcast: show_statistics returned error/empty data.")
        return

    execute_telegram_broadcast(
        bot_token=token,
        text=broadcast_caption,
        media=graph_buf,
        media_type="photo" if graph_buf else "text"
    )

@scheduler_fn.on_schedule(
    schedule="0 10 * * *", 
    timezone="Europe/Kyiv",
    timeout_sec=300,
    secrets=["TOKEN"],
    memory=options.MemoryOption.GB_1
)
def daily_recurring_broadcast_checker(event: scheduler_fn.ScheduledEvent) -> None:
    token = os.environ.get("TOKEN")
    kyiv_tz = zoneinfo.ZoneInfo("Europe/Kyiv")
    today = datetime.now(kyiv_tz)
    current_month, current_day, current_year = today.month, today.day, today.year

    all_broadcasts = db.reference('recurring_broadcasts').get()
    if not all_broadcasts: return

    active_id, broadcast_data = None, None
    for b_id, data in all_broadcasts.items():
        if data.get('month') == current_month and data.get('day') == current_day:
            if data.get('last_run_year') != current_year:
                active_id, broadcast_data = b_id, data
                break

    if not active_id or not broadcast_data: return

    message_text = broadcast_data.get('text')
    image_url = broadcast_data.get('image_url')
    photo_bytes = download_bytes_from_storage(image_url) if image_url else None
    
    payload = io.BytesIO(photo_bytes) if photo_bytes else None
    execute_telegram_broadcast(bot_token=token, text=message_text, media=payload, media_type="photo" if payload else "text")
    db.reference('recurring_broadcasts').child(active_id).update({'last_run_year': current_year})

@scheduler_fn.on_schedule(
    schedule="*/5 * * * *", 
    timezone="Europe/Kyiv",
    timeout_sec=300,
    secrets=["TOKEN"],
    memory=options.MemoryOption.GB_1
)
def process_broadcast_queue(event: scheduler_fn.ScheduledEvent) -> None:
    token = os.environ.get("TOKEN")
    queue_data = db.reference('broadcast_queue').get()
    if not queue_data: return

    items_to_process = queue_data.items() if isinstance(queue_data, dict) else enumerate(queue_data)

    for item_id, item_content in items_to_process:
        if not item_content or item_content.get('processed') is True:
            continue
            
        msg_text = item_content.get('text')
        img_url = item_content.get('image_url')
        if not msg_text: continue

        execute_telegram_broadcast(bot_token=token, text=msg_text, media=img_url, media_type="photo" if img_url else "text")
        db.reference('broadcast_queue').child(str(item_id)).update({'processed': True})


# --- TELEGRAM BOT ENTRYPOINT ---
@https_fn.on_request(memory=options.MemoryOption.GB_1, timeout_sec=60, secrets=["ADMIN_TELEGRAM_ID", "TOKEN"])
def telegram_bot(req: https_fn.Request) -> https_fn.Response:
    token = os.environ.get("TOKEN")
    bot = telegram.Bot(token=token)

    async def process_update():
        try:
            body = req.get_json()
            update = telegram.Update.de_json(body, bot)

            if not update or not update.message:
                return            
            if not update.message.text and not update.message.caption:
                return            

            chat_id = update.message.chat.id
            text = update.message.text
            caption = update.message.caption
            user = update.message.from_user

            if user:
                user_info = {
                    "first_name": str(user.first_name or "Користувач"),
                    "last_name": str(user.last_name or ""),
                    "username": str(user.username or "No Username"),
                    "last_seen": datetime.now().isoformat()
                }
                db.reference(f'users/{chat_id}/profile').update(user_info)

            # --- USER LAND ROUTING (ВИПРАВЛЕНО ДЛЯ КНОПОК МЕНЮ) ---
            if text == "/start":
                await bot.send_message(chat_id, CONTRIBUTION_TEXT, reply_markup=MAIN_MENU, parse_mode='Markdown')

            elif text in ["/статистика", "📊 Статистика"]:
                msg, graph_buf = show_statistics()
                share_markup = None 

                if graph_buf:
                    graph_buf.seek(0)
                    await bot.send_photo(chat_id=chat_id, photo=graph_buf, caption=msg, reply_markup=share_markup, parse_mode='Markdown')
                else:
                    await bot.send_message(chat_id=chat_id, text=msg, reply_markup=share_markup, parse_mode='Markdown')
 
            elif text in ["/долучитись", "🌐 Долучитись до проєкту"]:
                markup = InlineKeyboardMarkup([[
                    InlineKeyboardButton(text="Відкрити Common Voice 🇺🇦", url="https://commonvoice.mozilla.org/uk/listen")
                ]])
                await bot.send_message(chat_id=chat_id, text=CONTRIBUTION_TEXT, reply_markup=markup, parse_mode='Markdown')

            # --- SECURE ADMINISTRATIVE LAYER ---
            env_admin_id = os.environ.get("ADMIN_TELEGRAM_ID")
            TELEGRAM_ID = int(env_admin_id) if env_admin_id else 0

            if chat_id == TELEGRAM_ID:
                all_users_raw = db.reference('users').get()
                admin_user_ids = normalize_user_ids(all_users_raw)

                if caption and caption.startswith("/send_all"):
                    try:
                        raw_input = caption.split(maxsplit=1)
                        caption_text = raw_input[1].strip() if len(raw_input) > 1 else ""

                        photo_id = update.message.photo[-1].file_id if update.message.photo else None
                        video_id = update.message.video.file_id if update.message.video else None

                        if not photo_id and not video_id: return

                        success, fail = 0, 0
                        for target_uid in admin_user_ids:
                            try:
                                if target_uid == chat_id: continue
                                if photo_id:
                                    await bot.send_photo(chat_id=target_uid, photo=photo_id, caption=caption_text, parse_mode='Markdown')
                                elif video_id:
                                    await bot.send_video(chat_id=target_uid, video=video_id, caption=caption_text, parse_mode='Markdown')
                                success += 1
                                await asyncio.sleep(0.05)
                            except Exception:
                                fail += 1

                        await bot.send_message(chat_id=chat_id, text=f"✅ `{success}` | ❌ `{fail}`", parse_mode='Markdown')
                    except Exception as e:
                        await bot.send_message(chat_id=chat_id, text=f"⚠️ **Error:** {e}")

                elif text:
                    if text == "/admin_users":
                        if not all_users_raw:
                            await bot.send_message(chat_id, "No users found in database.")
                            return

                        report = "👥 **Registered Bot Users:**\n━━━━━━━━━━━━━━━\n"
                        items = all_users_raw.items() if isinstance(all_users_raw, dict) else enumerate(all_users_raw)
                        for uid, details in items:
                            if details is None: continue
                            profile = details.get('profile', {}) if isinstance(details, dict) else {}
                            first = profile.get('first_name', 'Unknown')
                            username = profile.get('username', 'No Username')
                            report += f"• {first} (@{username}) — ID: `{uid}`\n" if username != "No Username" else f"• {first} — ID: `{uid}`\n"
                        await bot.send_message(chat_id, report, parse_mode='Markdown')

        except Exception as e:
            print(f"Update Error: {e}")

    if req.method == "POST":
        asyncio.run(process_update())
        return https_fn.Response("OK")
    return https_fn.Response("Manual access not allowed", status=405)