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
    download_bytes_from_storage,
)
from common_voice import contribution_url
from i18n import main_menu, t
from reminder_tasks import create_reminder


def generate_history_graph(contribution_locale: str, locale: str) -> Optional[io.BytesIO]:
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
    ax.plot(df.index, df['recorded'], marker='o', color='#0057b7', label=t(locale, "graph.recorded"), linewidth=2.5)
    ax.plot(df.index, df['validated'], marker='s', color='#ffd700', label=t(locale, "graph.validated"), linewidth=2.5)
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

def show_statistics(contribution_locale: str, language_name: str, locale: str) -> Tuple[str, Optional[io.BytesIO]]:
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
            return t(locale, "statistics.unavailable", language=language_name), None

        # Format the actual message that the bot is going to send out
        msg = t(locale, "statistics.title", language=language_name) + "\n\n" + t(
            locale,
            "statistics.body",
            speakers=latest_ref.get('speakers', 0),
            recorded=latest_ref.get('recorded', 0),
            validated=latest_ref.get('validated', 0),
        )

        # Check the database cache first to see if we already built today's image
        today_str = datetime.now().strftime("%Y-%m-%d")
        cache_ref = db.reference('database_image_cache').child(today_str)
        base64_string = cache_ref.get()

        if base64_string:
            image_bytes = base64.b64decode(base64_string)
            graph_buffer = io.BytesIO(image_bytes)
            graph_buffer.seek(0)
            return msg, graph_buffer
        else:
            # Rebuild the graph image because the cache missed or expired
            generated_buffer = generate_history_graph(contribution_locale, locale)
            if generated_buffer:
                raw_bytes = generated_buffer.getvalue()
                cache_ref.set(base64.b64encode(raw_bytes).decode('utf-8'))
                generated_buffer.seek(0)
                return msg, generated_buffer
            return msg, None
    except Exception as e:
        print(f"Statistics error: {e}")
        return t(locale, "statistics.error"), None

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
            if not update:
                return

            query = update.callback_query
            message = update.message
            if query:
                await query.answer(t("uk", "callback.obsolete"), show_alert=True)
                return

            if not message or (not message.text and not message.caption):
                return
            if message.chat.type != "private":
                print(f"Ignored update from non-private chat {message.chat.id}.")
                return

            chat_id = message.chat.id
            text = message.text
            caption = message.caption
            user = message.from_user
            contribution_locale = "uk"
            locale = "uk"

            if user and text in ("/delete_me", "/stop"):
                db.reference(f'users/{user.id}').delete()
                await bot.send_message(chat_id=chat_id, text=t(locale, "delete.confirmed"))
                return

            # Log user info into the database so we can track active profiles
            if user:
                user_info = {
                    "first_name": str(user.first_name or ""),
                    "username": str(user.username or ""),
                    "last_seen": datetime.now().isoformat()
                }
                db.reference(f'users/{user.id}/profile').update(user_info)

            language_name = t(locale, "language.name")

            # --- ROUTING RESPONSES BASED ON USER COMMANDS ---
            if text == "/start":
                await bot.send_message(
                    chat_id,
                    t(locale, "welcome", language=language_name),
                    reply_markup=main_menu(locale),
                    parse_mode='Markdown',
                )

            elif text == "/privacy" or text == t(locale, "menu.privacy"):
                await bot.send_message(chat_id, t(locale, "privacy"))

            elif text == t(locale, "menu.reminders"):
                await bot.send_message(chat_id, t(locale, "reminder.help"))

            elif text and text.startswith("/remind_daily"):
                parts = text.split()
                if len(parts) != 2:
                    await bot.send_message(chat_id, t(locale, "reminder.daily_format"))
                else:
                    try:
                        reminder = create_reminder(user.id, "daily", parts[1])
                        await bot.send_message(chat_id, t(locale, "reminder.daily_set", time=reminder['time']))
                    except ValueError:
                        await bot.send_message(chat_id, t(locale, "reminder.invalid_time"))
                    except Exception as exc:
                        print(f"Could not schedule daily reminder for {user.id}: {exc}")
                        await bot.send_message(chat_id, t(locale, "reminder.schedule_error"))

            elif text and text.startswith("/remind_weekly"):
                parts = text.split()
                try:
                    weekday = int(parts[1]) if len(parts) == 3 else 0
                except ValueError:
                    weekday = 0
                if weekday not in range(1, 8):
                    await bot.send_message(chat_id, t(locale, "reminder.weekly_format"))
                else:
                    try:
                        reminder = create_reminder(user.id, "weekly", parts[2], weekday)
                        await bot.send_message(chat_id, t(
                            locale,
                            "reminder.weekly_set",
                            weekday=t(locale, f"weekday.{weekday}"),
                            time=reminder['time'],
                        ))
                    except ValueError:
                        await bot.send_message(chat_id, t(locale, "reminder.invalid_weekly"))
                    except Exception as exc:
                        print(f"Could not schedule weekly reminder for {user.id}: {exc}")
                        await bot.send_message(chat_id, t(locale, "reminder.schedule_error"))

            elif text == "/reminder":
                reminder = db.reference(f'users/{user.id}/reminder').get()
                if not reminder or not reminder.get('enabled'):
                    await bot.send_message(chat_id, t(locale, "reminder.none"))
                elif reminder.get('frequency') == 'daily':
                    await bot.send_message(chat_id, t(locale, "reminder.daily_current", time=reminder.get('time')))
                else:
                    weekday = reminder.get('weekday')
                    await bot.send_message(chat_id, t(
                        locale,
                        "reminder.weekly_current",
                        weekday=t(locale, f"weekday.{weekday}"),
                        time=reminder.get('time'),
                    ))

            elif text == "/reminder_off":
                db.reference(f'users/{user.id}/reminder').delete()
                await bot.send_message(chat_id, t(locale, "reminder.disabled"))

            elif text in ["/statistics", "/статистика", t(locale, "menu.statistics")]:
                msg, graph_buf = show_statistics(contribution_locale, language_name, locale)
                if graph_buf:
                    graph_buf.seek(0)
                    await bot.send_photo(chat_id=chat_id, photo=graph_buf, caption=msg, parse_mode='Markdown')
                else:
                    await bot.send_message(chat_id=chat_id, text=msg, parse_mode='Markdown')

            elif text in ["/contribute", "/долучитись", t(locale, "menu.contribute")]:
                markup = InlineKeyboardMarkup([[
                    InlineKeyboardButton(text=t(locale, "contribute.button"), url=contribution_url(contribution_locale))
                ]])
                await bot.send_message(
                    chat_id=chat_id,
                    text=t(locale, "welcome", language=language_name),
                    reply_markup=markup,
                    parse_mode='Markdown',
                )

            elif text == t(locale, "menu.share"):
                bot_username = os.environ.get("BOT_USERNAME", "").lstrip("@")
                share_target = (
                    f"https://t.me/{bot_username}"
                    if bot_username
                    else contribution_url(contribution_locale)
                )
                native_share_url = "https://t.me/share/url?" + urllib.parse.urlencode({
                    "url": share_target,
                    "text": t(locale, "share.text"),
                })

                share_markup = InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        text=t(locale, "share.button"),
                        url=native_share_url
                    )
                ]])

                await bot.send_message(
                    chat_id=chat_id,
                    text=t(locale, "share.prompt"),
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
                            text=t(locale, "admin.broadcast_queued", queue_id=queued.key),
                            parse_mode='Markdown'
                        )
                    except Exception as e:
                        await bot.send_message(chat_id=chat_id, text=t(locale, "admin.error", error=e))

        except Exception as e:
            print(f"Update Error: {e}")

    asyncio.run(process_update())
    return https_fn.Response("OK")
