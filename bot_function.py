from firebase_functions import https_fn, options
from firebase_admin import db
import asyncio
import os
import io
import base64
from datetime import datetime
import telegram
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
import pandas as pd
import matplotlib
matplotlib.use('Agg')  # fixes crashing issues on cloud servers
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
from PIL import Image
from typing import Tuple, Optional

from shared import app, MAIN_MENU, CONTRIBUTION_TEXT, normalize_user_ids, download_bytes_from_storage

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
        bg_bytes = download_bytes_from_storage("gs://commonvoice-ua.firebasestorage.app/progress.png") 
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
            f"👥 Учасників спільноти: **{latest_ref.get('speakers', 0)}**\n"
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

@https_fn.on_request(memory=options.MemoryOption.GB_1, timeout_sec=60, secrets=["ADMIN_TELEGRAM_ID", "TOKEN"])
def telegram_bot(req: https_fn.Request) -> https_fn.Response:
    """The main webhook function that catches all updates from Telegram.

    Handles message routing, processes button tappings, and opens up 
    admin privileges if the user has the right chat ID.

    Args:
        req (https_fn.Request): The incoming request payload from the Telegram webhook.

    Returns:
        https_fn.Response: A simple status response back to Telegram so it doesn't retry.
    """
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

            # Log user info into the database so we can track active profiles
            if user:
                user_info = {
                    "first_name": str(user.first_name or "Користувач"),
                    "last_name": str(user.last_name or ""),
                    "username": str(user.username or "No Username"),
                    "last_seen": datetime.now().isoformat()
                }
                db.reference(f'users/{chat_id}/profile').update(user_info)

            # --- ROUTING RESPONSES BASED ON USER COMMANDS ---
            if text == "/start":
                await bot.send_message(chat_id, CONTRIBUTION_TEXT, reply_markup=MAIN_MENU, parse_mode='Markdown')
                
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
                import urllib.parse
                
                share_text = (
                    "Привіт! Долучайся до проєкту Mozilla Common Voice. "
                    "Давай разом навчимо штучний інтелект розуміти українську мову бездоганно! 🇺🇦\n\n"
                    "Переходь у бот проєкту: https://t.me/UkrBot_bot"
                )
                
                # Format string to URL parameters so Telegram client opens the share panel cleanly
                encoded_text = urllib.parse.quote(share_text)
                native_share_url = f"https://t.me/share/url?url={encoded_text}"
                
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
                all_users_raw = db.reference('users').get()
                admin_user_ids = normalize_user_ids(all_users_raw)

                # Broadcast engine: sends media updates to everyone registered in database
                if caption and caption.startswith("/send_all"):
                    try:
                        raw_input = caption.split(maxsplit=1)
                        caption_text = raw_input[1].strip() if len(raw_input) > 1 else ""
                        photo_id = update.message.photo[-1].file_id if update.message.photo else None
                        video_id = update.message.video.file_id if update.message.video else None
                        if not photo_id and not video_id: 
                            return

                        success, fail = 0, 0
                        for target_uid in admin_user_ids:
                            try:
                                if target_uid == chat_id: 
                                    continue
                                if photo_id: 
                                    await bot.send_photo(chat_id=target_uid, photo=photo_id, caption=caption_text, parse_mode='Markdown')
                                elif video_id: 
                                    await bot.send_video(chat_id=target_uid, video=video_id, caption=caption_text, parse_mode='Markdown')
                                success += 1
                                # Slow down iteration slightly so Telegram doesn't rate-limit or ban the bot
                                await asyncio.sleep(0.05)
                            except Exception: 
                                fail += 1
                        await bot.send_message(chat_id=chat_id, text=f"✅ `{success}` | ❌ `{fail}`", parse_mode='Markdown')
                    except Exception as e: 
                        await bot.send_message(chat_id=chat_id, text=f"⚠️ **Error:** {e}")
                        
                elif text == "/admin_users":
                    if not all_users_raw: 
                        return
                    report = "👥 **Registered Bot Users:**\n━━━━━━━━━━━━━━━\n"
                    items = all_users_raw.items() if isinstance(all_users_raw, dict) else enumerate(all_users_raw)
                    for uid, details in items:
                        if details is None: 
                            continue
                        profile = details.get('profile', {}) if isinstance(details, dict) else {}
                        username = profile.get('username', 'No Username')
                        report += f"• {profile.get('first_name', 'Unknown')} (@{username}) — ID: `{uid}`\n" if username != "No Username" else f"• {profile.get('first_name', 'Unknown')} — ID: `{uid}`\n"
                    await bot.send_message(chat_id, report, parse_mode='Markdown')
        except Exception as e: 
            print(f"Update Error: {e}")

    # Make sure we only process post requests coming from actual webhooks
    if req.method == "POST":
        asyncio.run(process_update())
        return https_fn.Response("OK")
    return https_fn.Response("Manual access not allowed", status=405)