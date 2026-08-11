from firebase_functions import https_fn, options
from firebase_admin import db
import asyncio
import os
import hmac
import io
import urllib.parse
import base64
from datetime import datetime
import telegram
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")
import pandas as pd
import matplotlib
matplotlib.use('Agg')  # fixes crashing issues on cloud servers
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
from PIL import Image
from typing import Tuple, Optional

from shared import (
    app,
    MAIN_MENU,
    CONTRIBUTION_TEXT,
    PRIVACY_TEXT,
    download_bytes_from_storage,
)
from reminder_tasks import create_reminder


async def send_privacy_notice(bot: telegram.Bot, chat_id: int) -> None:
    """Send the privacy notice without markup parsing user-visible commands."""
    await bot.send_message(chat_id=chat_id, text=PRIVACY_TEXT)


REMINDER_HELP_TEXT = """⏰ Нагадування про внесок

Вкажіть бажаний час за Києвом у 24-годинному форматі.

Щодня:
/remind_daily 14:30

Щотижня:
/remind_weekly 1 14:30

Дні тижня: 1=понеділок, 2=вівторок, 3=середа, 4=четвер, 5=п’ятниця, 6=субота, 7=неділя.

Показати поточне: /reminder
Вимкнути: /reminder_off"""

def generate_history_graph() -> Optional[io.BytesIO]:
    """Makes a progress chart and slaps it onto a nice background banner.

    Pulls history stats from Firebase Realtime DB, cleans up the dates and hours 
    using pandas, draws the actual charts with matplotlib, and overlays everything 
    onto a background image template from Cloud Storage.

    Returns:
        Optional[io.BytesIO]: The finished combined image in a buffer, or None if 
                             there's no data to plot yet.
    """
    ref = db.reference('history')
    history_data = ref.get()
    if not history_data: 
        return None

    # Sort everything nicely by date so the timeline actually makes sense
    df = pd.DataFrame.from_dict(history_data, orient='index')
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()

    # Round everything up to clean whole integers
    df['recorded'] = df['recorded'].round().astype(int)
    df['validated'] = df['validated'].round().astype(int)

    # Set up the matplotlib chart layout and colors
    fig, ax = plt.subplots(figsize=(7, 4.5))
    ax.plot(df.index, df['recorded'], marker='o', color='#0057b7', label='Recorded Hours', linewidth=2.5)
    ax.plot(df.index, df['validated'], marker='s', color='#ffd700', label='Validated Hours', linewidth=2.5)
    ax.grid(True, linestyle='--', alpha=0.3)
    ax.legend(loc='upper left')
    ax.yaxis.set_major_locator(ticker.MaxNLocator(integer=True))
    plt.xticks(rotation=30)
    plt.tight_layout()

    # Save the chart directly into RAM instead of wasting space on the disk
    chart_buf = io.BytesIO()
    plt.savefig(chart_buf, format='png', dpi=120, transparent=True)
    chart_buf.seek(0)
    plt.close()

    try:
        # Grab the background graphic banner from storage
        bg_bytes = download_bytes_from_storage("progress.png")
        if bg_bytes: 
            background = Image.open(io.BytesIO(bg_bytes))
        else: 
            background = Image.new("RGBA", (1000, 800), "white")
            
        # Paste our freshly generated graph on top of the banner image
        chart_img = Image.open(chart_buf)
        background.paste(chart_img, (550, 30), chart_img.convert("RGBA"))
        
        final_buf = io.BytesIO()
        background.save(final_buf, format='PNG')
        final_buf.seek(0)
        return final_buf
    except Exception as e:
        print(f"⚠️ Image compositing failed: {e}")
        chart_buf.seek(0)
        return chart_buf

def show_statistics() -> Tuple[str, Optional[io.BytesIO]]:
    """Gets the text stats and pulls up the cache or makes a new graph.

    Caches the built chart image for the whole day so the server doesn't have 
    to rebuild the graph from scratch every single time someone checks the stats.

    Returns:
        Tuple[str, Optional[io.BytesIO]]: Markdown text of the stats along with 
                                          the image buffer if it worked out.
    """
    try:
        latest_ref = db.reference('latest').get()
        if not latest_ref: 
            return "⚠️ На жаль, статистика наразі недоступна.", None

        # Format the actual message that the bot is going to send out
        msg = (
            f"📊 **Актуальна статистика проєкту:**\n\n"
            f"👥 Читачів: **{latest_ref.get('speakers', 0)}**\n"
            f"🎙 Записано годин: **{latest_ref.get('recorded', 0)} год.**\n"
            f"✅ Перевірено годин: **{latest_ref.get('validated', 0)} год.**\n\n"
            f"Дякуємо за ваш внесок! 🇺🇦"
        )

        # Check the database cache first to see if we already built today's image
        today_str = datetime.now().strftime("%Y-%m-%d")
        cache_ref = db.reference(f'database_image_cache/{today_str}')
        base64_string = cache_ref.get()

        if base64_string:
            image_bytes = base64.b64decode(base64_string)
            graph_buffer = io.BytesIO(image_bytes)
            graph_buffer.seek(0)
            return msg, graph_buffer
        else:
            # Rebuild the graph image because the cache missed or expired
            generated_buffer = generate_history_graph()
            if generated_buffer:
                raw_bytes = generated_buffer.getvalue()
                cache_ref.set(base64.b64encode(raw_bytes).decode('utf-8'))
                generated_buffer.seek(0)
                return msg, generated_buffer
            return msg, None
    except Exception as e:
        print(f"💥 Помилка статистики: {e}")
        return "💥 Не вдалося завантажити статистику через технічну помилку.", None

@https_fn.on_request(
    memory=options.MemoryOption.GB_1,
    timeout_sec=60,
    min_instances=0,
    secrets=["ADMIN_TELEGRAM_ID", "TOKEN", "TELEGRAM_WEBHOOK_SECRET"]
)
def telegram_bot(req: https_fn.Request) -> https_fn.Response:
    """The main webhook function that catches all updates from Telegram.

    Handles message routing, processes button tappings, and opens up 
    admin privileges if the user has the right chat ID.

    Args:
        req (https_fn.Request): The incoming request payload from the Telegram webhook.

    Returns:
        https_fn.Response: A simple status response back to Telegram so it doesn't retry.
    """
    if req.method != "POST":
        return https_fn.Response("Manual access not allowed", status=405)

    expected_secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET")
    supplied_secret = req.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not expected_secret or not hmac.compare_digest(supplied_secret, expected_secret):
        print("Rejected unauthenticated Telegram webhook request.")
        return https_fn.Response("Forbidden", status=403)

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
            if update.message.chat.type != "private":
                print(f"Ignored update from non-private chat {update.message.chat.id}.")
                return

            chat_id = update.message.chat.id
            text = update.message.text
            caption = update.message.caption
            user = update.message.from_user

            if user and text in ("/delete_me", "/stop"):
                db.reference(f'users/{user.id}').delete()
                await bot.send_message(
                    chat_id=chat_id,
                    text="✅ Ваші дані видалено. Ви більше не отримуватимете розсилки. Надішліть /start, щоб приєднатися знову."
                )
                return

            # Log user info into the database so we can track active profiles
            if user:
                user_info = {
                    "first_name": str(user.first_name or "Користувач"),
                    "username": str(user.username or "No Username"),
                    "last_seen": datetime.now().isoformat()
                }
                db.reference(f'users/{user.id}/profile').update(user_info)

            # --- ROUTING RESPONSES BASED ON USER COMMANDS ---
            if text == "/start":
                await bot.send_message(chat_id, CONTRIBUTION_TEXT, reply_markup=MAIN_MENU, parse_mode='Markdown')

            elif text == "/privacy" or (text and text.replace("*", "").strip() == "🔐 Конфіденційність"):
                await send_privacy_notice(bot, chat_id)

            elif text == "⏰ Нагадування":
                await bot.send_message(chat_id, REMINDER_HELP_TEXT)

            elif text and text.startswith("/remind_daily"):
                parts = text.split()
                if len(parts) != 2:
                    await bot.send_message(chat_id, "⚠️ Формат: /remind_daily ГГ:ХХ, наприклад /remind_daily 14:30")
                else:
                    try:
                        reminder = create_reminder(user.id, "daily", parts[1])
                        await bot.send_message(chat_id, f"✅ Щоденне нагадування встановлено на {reminder['time']} за Києвом.")
                    except ValueError:
                        await bot.send_message(chat_id, "⚠️ Вкажіть правильний час у форматі ГГ:ХХ, наприклад 14:30.")
                    except Exception as exc:
                        print(f"Could not schedule daily reminder for {user.id}: {exc}")
                        await bot.send_message(chat_id, "⚠️ Не вдалося запланувати нагадування. Спробуйте пізніше.")

            elif text and text.startswith("/remind_weekly"):
                parts = text.split()
                try:
                    weekday = int(parts[1]) if len(parts) == 3 else 0
                except ValueError:
                    weekday = 0
                if weekday not in range(1, 8):
                    await bot.send_message(chat_id, "⚠️ Формат: /remind_weekly ДЕНЬ ГГ:ХХ, наприклад /remind_weekly 1 14:30")
                else:
                    try:
                        reminder = create_reminder(user.id, "weekly", parts[2], weekday)
                        weekday_names = ('', 'понеділок', 'вівторок', 'середу', 'четвер', 'п’ятницю', 'суботу', 'неділю')
                        await bot.send_message(chat_id, f"✅ Щотижневе нагадування встановлено на {weekday_names[weekday]}, {reminder['time']} за Києвом.")
                    except ValueError:
                        await bot.send_message(chat_id, "⚠️ Вкажіть правильний день і час, наприклад /remind_weekly 1 14:30.")
                    except Exception as exc:
                        print(f"Could not schedule weekly reminder for {user.id}: {exc}")
                        await bot.send_message(chat_id, "⚠️ Не вдалося запланувати нагадування. Спробуйте пізніше.")

            elif text == "/reminder":
                reminder = db.reference(f'users/{user.id}/reminder').get()
                if not reminder or not reminder.get('enabled'):
                    await bot.send_message(chat_id, "⏰ Нагадування не встановлено.")
                elif reminder.get('frequency') == 'daily':
                    await bot.send_message(chat_id, f"⏰ Щодня о {reminder.get('time')} за Києвом.")
                else:
                    await bot.send_message(chat_id, f"⏰ Щотижня: день {reminder.get('weekday')}, {reminder.get('time')} за Києвом.")

            elif text == "/reminder_off":
                db.reference(f'users/{user.id}/reminder').delete()
                await bot.send_message(chat_id, "✅ Нагадування вимкнено.")
                
            elif text in ["/статистика", "📊 Статистика"]:
                msg, graph_buf = show_statistics()
                if graph_buf:
                    graph_buf.seek(0)
                    await bot.send_photo(chat_id=chat_id, photo=graph_buf, caption=msg, parse_mode='Markdown')
                else:
                    await bot.send_message(chat_id=chat_id, text=msg, parse_mode='Markdown')
                    
            elif text in ["/долучитись", "🌐 Долучитись до проєкту"]:
                markup = InlineKeyboardMarkup([[InlineKeyboardButton(text="Відкрити Common Voice 🇺🇦", url="https://commonvoice.mozilla.org/uk/listen")]])
                await bot.send_message(chat_id=chat_id, text=CONTRIBUTION_TEXT, reply_markup=markup, parse_mode='Markdown')

            elif text == "👥 Поділитись з другом":
                bot_username = os.environ.get("BOT_USERNAME", "").lstrip("@")
                share_target = (
                    f"https://t.me/{bot_username}"
                    if bot_username
                    else "https://commonvoice.mozilla.org/uk/listen"
                )
                native_share_url = "https://t.me/share/url?" + urllib.parse.urlencode({
                    "url": share_target,
                    "text": (
                        "Привіт! Долучайся до проєкту Mozilla Common Voice. "
                        "Давай разом навчимо штучний інтелект розуміти "
                        "українську мову бездоганно! 🇺🇦"
                    ),
                })
                
                share_markup = InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        text="🚀 Переслати другу", 
                        url=native_share_url
                    )
                ]])
                
                await bot.send_message(
                    chat_id=chat_id, 
                    text="✨ **Поділіться проєктом з друзями!**\n\nНатисніть кнопку нижче, щоб вибрати друга або групу, куди ви хочете миттєво надіслати чисте текстове запрошення.", 
                    reply_markup=share_markup, 
                    parse_mode='Markdown'
                )

            # --- PRIVILEGED COMMANDS FOR ADMIN ONLY ---
            env_admin_id = os.environ.get("ADMIN_TELEGRAM_ID")
            TELEGRAM_ID = int(env_admin_id) if env_admin_id else 0

            if chat_id == TELEGRAM_ID:
                # Broadcast engine: sends media updates to everyone registered in database
                if caption and caption.startswith("/send_all"):
                    try:
                        raw_input = caption.split(maxsplit=1)
                        caption_text = raw_input[1].strip() if len(raw_input) > 1 else ""
                        photo_id = update.message.photo[-1].file_id if update.message.photo else None
                        video_id = update.message.video.file_id if update.message.video else None
                        if not photo_id and not video_id: 
                            return

                        queued = db.reference('broadcast_queue').push({
                            'text': caption_text,
                            'image_url': photo_id or video_id,
                            'media_source': 'telegram_file_id',
                            'media_type': 'photo' if photo_id else 'video',
                            'status': 'pending',
                            'processed': False,
                            'created_at': datetime.now().isoformat(),
                        })
                        await bot.send_message(
                            chat_id=chat_id,
                            text=f"✅ Broadcast queued: `{queued.key}`",
                            parse_mode='Markdown'
                        )
                    except Exception as e: 
                        await bot.send_message(chat_id=chat_id, text=f"⚠️ **Error:** {e}")
                        
        except Exception as e: 
            print(f"Update Error: {e}")

    asyncio.run(process_update())
    return https_fn.Response("OK")
