import re
import html
from datetime import datetime, timezone
import asyncio
import os
import threading
import sqlite3
import tempfile
from io import BytesIO
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, InputMediaVideo, InputFile
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler, ContextTypes
from telethon import TelegramClient, events, functions, types
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError, ChannelPrivateError, UsernameNotOccupiedError, MessageIdInvalidError
import fitz  # PyMuPDF

# Telegram Stories are retrieved with stories.getPeerStories.
# Use peer-specific Telegram API calls for Stories and message search.

app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is alive!"

BOT_TOKEN = os.environ.get('BOT_TOKEN', '')
API_ID = int(os.environ.get('API_ID', 0))
API_HASH = os.environ.get('API_HASH', '')
STRING_SESSION = os.environ.get('STRING_SESSION', '')
BOT_PASSWORD = os.environ.get("BOT_PASSWORD", "ptss25")

telethon_client = TelegramClient(StringSession(STRING_SESSION), API_ID, API_HASH)

def init_db():
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY)''')
    c.execute('''CREATE TABLE IF NOT EXISTS inbox (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, sender_id INTEGER, name TEXT, username TEXT, text TEXT, media_type TEXT, date TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS tracking (target_id INTEGER PRIMARY KEY, username TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS user_history (user_id INTEGER, username TEXT, first_name TEXT, last_name TEXT, date TEXT)''')
    conn.commit()
    conn.close()

def is_authenticated(user_id):
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    data = c.fetchone()
    conn.close()
    return data is not None

def add_authenticated_user(user_id):
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
    conn.commit()
    conn.close()

def remove_authenticated_user(user_id):
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def add_inbox_message(chat_id, sender_id, name, username, text, media_type, date):
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute("INSERT INTO inbox (chat_id, sender_id, name, username, text, media_type, date) VALUES (?, ?, ?, ?, ?, ?, ?)", (chat_id, sender_id, name, username, text, media_type, date))
    conn.commit()
    conn.close()

def get_inbox_conversations():
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute("SELECT chat_id, sender_id, name, username, text, media_type, date FROM inbox ORDER BY id DESC")
    rows = c.fetchall()
    conn.close()
    return rows

def save_user_history(user_id, username, first_name, last_name):
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute("INSERT INTO user_history (user_id, username, first_name, last_name, date) VALUES (?, ?, ?, ?, ?)", (user_id, username, first_name, last_name, str(__import__('datetime').datetime.now())))
    conn.commit()
    conn.close()

def get_user_history(user_id):
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute("SELECT username, first_name, last_name, date FROM user_history WHERE user_id = ? ORDER BY date DESC", (user_id,))
    rows = c.fetchall()
    conn.close()
    return rows

async def safe_send(chat_id, bot, msg, from_chat_id, message_id):
    text = msg.message
    try:
        if msg.photo or msg.video or msg.document or msg.voice or msg.audio or msg.gif:
            media_bytes = BytesIO()
            await telethon_client.download_media(msg, file=media_bytes)
            media_bytes.seek(0)
            if msg.photo: await bot.send_photo(chat_id, photo=media_bytes, caption=text)
            elif msg.video: await bot.send_video(chat_id, video=media_bytes, caption=text)
            elif msg.document: await bot.send_document(chat_id, document=media_bytes, caption=text)
            elif msg.voice: await bot.send_voice(chat_id, voice=media_bytes, caption=text)
            elif msg.audio: await bot.send_audio(chat_id, audio=media_bytes, caption=text)
            elif msg.gif: await bot.send_animation(chat_id, animation=media_bytes, caption=text)
        elif msg.sticker:
            sticker_bytes = BytesIO()
            await telethon_client.download_media(msg, file=sticker_bytes)
            sticker_bytes.seek(0)
            await bot.send_sticker(chat_id, sticker=sticker_bytes)
        elif text: await bot.send_message(chat_id, text=text)
        else: await bot.send_message(chat_id, "Unsupported message type.")
    except Exception as e:
        if "must forward even restricted" in str(e).lower():
            await bot.send_message(chat_id, "🔒 Restricted media.")
        else:
            await bot.send_message(chat_id, f"Failed to send media: {e}")

async def handle_telethon_error(update, error):
    if isinstance(error, FloodWaitError):
        await update.message.reply_text(f"⚠️ FloodWait: {error.seconds} seconds.")
    elif isinstance(error, UsernameNotOccupiedError):
        await update.message.reply_text("❌ Username not found.")
    else:
        await update.message.reply_text(f"❌ Error: {error}")

def parse_tg_link(text):
    pattern = r'https?://t\.me/([a-zA-Z0-9_]+)/(\d+)'
    match = re.search(pattern, text)
    if match: return match.group(1), int(match.group(2))
    channel_pattern = r'https?://t\.me/([a-zA-Z0-9_]+)'
    match = re.search(channel_pattern, text)
    if match: return match.group(1), None
    return None, None

def get_media_type(event):
    if event.photo: return "📷"
    elif event.video: return "🎬"
    elif event.document: return "🎞️" if event.gif else "📄"
    elif event.audio: return "🎵"
    elif event.voice: return "🎤"
    elif event.sticker: return "🧩"
    else: return "💬"

async def start(update, context):
    user_id = update.effective_user.id
    if not is_authenticated(user_id):
        context.user_data['state'] = 'awaiting_password'
        await update.message.reply_text("🔐 TELEGRAM ASSISTANT\n\nPassword required.\nPlease enter the password to continue.")
        return
    keyboard = [
        [InlineKeyboardButton("📥 Inbox", callback_data="inbox"), InlineKeyboardButton("👤 Profile", callback_data="profile")],
        [InlineKeyboardButton("🔗 Fetch Telegram", callback_data="fetch")],
        [InlineKeyboardButton("➕ More Commands", callback_data="more")]
    ]
    await update.message.reply_text("🤖 TELEGRAM ASSISTANT\n\nChoose an option:", reply_markup=InlineKeyboardMarkup(keyboard))

async def logout(update, context):
    user_id = update.effective_user.id
    remove_authenticated_user(user_id)
    await update.message.reply_text("🔒 Logged out.")

async def menu_callback(update, context):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    if not is_authenticated(user_id):
        await query.edit_message_text("🔐 Password required.")
        return

    data = query.data
    if data == "inbox":
        rows = get_inbox_conversations()
        if not rows:
            text = "📥 INBOX\n\nNo new private messages."
            kb = [[InlineKeyboardButton("⬅️ Back", callback_data="main_menu")]]
        else:
            text = "📥 INBOX\n"
            kb = []
            seen = set()
            for row in rows:
                chat_id, sender_id, name, username, msg_text, media, date = row
                if chat_id not in seen:
                    seen.add(chat_id)
                    content = msg_text if msg_text else f"[{media}]"
                    text += f"\n👤 {name} (@{username})\n\"{content}\"\n"
                    kb.append([InlineKeyboardButton(f"💬 {name}", callback_data=f"conv_{chat_id}")])
            kb.append([InlineKeyboardButton("⬅️ Back", callback_data="main_menu")])
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))

    elif data.startswith("conv_"):
        chat_id = int(data.split("_")[1])
        rows = get_inbox_conversations()
        text = f"💬 CHAT\n\n"
        for row in reversed(rows[-5:]):
            if row[0] == chat_id:
                text += f"{row[3]}:\n{row[4] if row[4] else f'[{row[5]}]'}\n\n"
        kb = [[InlineKeyboardButton("↩️ Reply", callback_data=f"reply_{chat_id}"), InlineKeyboardButton("👤 Profile", callback_data="profile")], [InlineKeyboardButton("🔄 Refresh", callback_data=f"refresh_{chat_id}"), InlineKeyboardButton("⬅️ Back", callback_data="inbox")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))

    elif data.startswith("reply_"):
        chat_id = int(data.split("_")[1])
        context.user_data['reply_to'] = chat_id
        await query.edit_message_text(f"💬 Type your reply to {chat_id}:")

    elif data.startswith("refresh_"):
        chat_id = int(data.split("_")[1])
        rows = get_inbox_conversations()
        text = f"💬 CHAT\n\n"
        for row in reversed(rows[-5:]):
            if row[0] == chat_id:
                text += f"{row[3]}:\n{row[4] if row[4] else f'[{row[5]}]'}\n\n"
        kb = [[InlineKeyboardButton("↩️ Reply", callback_data=f"reply_{chat_id}"), InlineKeyboardButton("👤 Profile", callback_data="profile")], [InlineKeyboardButton("🔄 Refresh", callback_data=f"refresh_{chat_id}"), InlineKeyboardButton("⬅️ Back", callback_data="inbox")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))

    elif data == "profile":
        await query.edit_message_text("👤 PROFILE\n\nEnter a Telegram username or ID to generate the Profile Card:")
        context.user_data['state'] = 'profile_query'
    
    elif data == "fetch":
        await query.edit_message_text("🔗 Fetch Telegram\n\nSend me a link (e.g., t.me/channel/123):")
        context.user_data['state'] = 'fetch_link'

    elif data == "main_menu":
        keyboard = [
            [InlineKeyboardButton("📥 Inbox", callback_data="inbox"), InlineKeyboardButton("👤 Profile", callback_data="profile")],
            [InlineKeyboardButton("🔗 Fetch Telegram", callback_data="fetch")],
            [InlineKeyboardButton("➕ More Commands", callback_data="more")]
        ]
        await query.edit_message_text("🤖 TELEGRAM ASSISTANT", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "more":
        kb = [
            [InlineKeyboardButton("🔎 Search", callback_data="search"), InlineKeyboardButton("📊 Statistics", callback_data="stats")],
            [InlineKeyboardButton("🔔 Track", callback_data="track"), InlineKeyboardButton("🔗 Names", callback_data="names")],
            [InlineKeyboardButton("👥 Groups", callback_data="groups"), InlineKeyboardButton("💬 Messages", callback_data="messages")],
            [InlineKeyboardButton("🔎 Analysis", callback_data="analysis"), InlineKeyboardButton("📢 Channels", callback_data="channels")],
            [InlineKeyboardButton("👍 Reputation", callback_data="rep"), InlineKeyboardButton("👥 Friends", callback_data="friends")],
            [InlineKeyboardButton("🔄 Reactions", callback_data="reactions"), InlineKeyboardButton("🎁 Gifts", callback_data="gifts")],
            [InlineKeyboardButton("📤 Share", callback_data="share"), InlineKeyboardButton("🔵 Words Frequency", callback_data="words")],
            [InlineKeyboardButton("👥 Common Groups", callback_data="common")],
            [InlineKeyboardButton("📄 PDF Fetch", callback_data="pdf_fetch")],
            [InlineKeyboardButton("⬅️ Back", callback_data="main_menu")]
        ]
        await query.edit_message_text("➕ MORE COMMANDS", reply_markup=InlineKeyboardMarkup(kb))

    elif data == "pdf_fetch":
        await query.edit_message_text("📄 PDF FETCH\n\nPlease upload the PDF file directly to this chat.")
        context.user_data['state'] = 'awaiting_pdf'

    elif data == "search":
        await query.edit_message_text("🔎 SEARCH\n\nEnter any keyword to search across your chats:\nExample: Logic mid")
        context.user_data['state'] = 'search_query'

    elif data == "search_next":
        page = context.user_data.get('search_page', 1) + 1
        await display_search_page(update, context, page)

    elif data == "search_prev":
        page = context.user_data.get('search_page', 1) - 1
        if page < 1: page = 1
        await display_search_page(update, context, page)

    elif data in ["stats", "track", "names", "groups", "messages", "analysis", "channels", "rep", "friends", "reactions", "gifts", "share", "words", "common"]:
        prompts = {
            "stats": ("📊 STATISTICS", "Enter target username/ID or chat link:"),
            "track": ("🔔 TRACK", "Enter target username/ID to track:"),
            "names": ("🔗 NAMES", "Enter target username/ID:"),
            "groups": ("👥 GROUPS", "Enter the account ID (usually your own) to list groups:"),
            "messages": ("💬 MESSAGES", "Enter target username/ID or chat link:"),
            "analysis": ("🔎 ANALYSIS", "Enter target username/ID or chat link:"),
            "channels": ("📢 CHANNELS", "Enter the account ID (usually your own) to list channels:"),
            "rep": ("👍 REPUTATION", "Enter target username/ID or chat link:"),
            "friends": ("👥 FRIENDS", "Enter target group username (e.g. @group) AND target user username (e.g. @user) separated by space:\nExample: @mygroup @Ollock"),
            "reactions": ("🔄 REACTIONS", "Enter target username/ID or chat link:"),
            "gifts": ("🎁 GIFTS", "Enter target username/ID:"),
            "share": ("📤 SHARE", "Enter target username/ID or message link:"),
            "words": ("🔵 WORDS FREQUENCY", "Enter target username/ID or chat link:"),
            "common": ("👥 COMMON GROUPS", "Enter target username/ID:")
        }
        title, prompt = prompts[data]
        await query.edit_message_text(f"{title}\n\n{prompt}")
        context.user_data['state'] = data

    elif data.startswith("posts_"):
        page = int(data.split("_")[1])
        await handle_posts_pagination(update, context, page)
    elif data.startswith("story_"):
        if data == "story_start":
            context.user_data['story_index'] = 0
            entity = context.user_data.get('profile_entity')
            if not entity:
                await query.edit_message_text("❌ Profile session expired. Please open the profile again.")
                return
            await fetch_stories_from_entity(update, context, entity)
        elif data == "story_next":
            context.user_data['story_index'] = context.user_data.get('story_index', 0) + 1
            await display_story(update, context, replace_current=True)
        elif data == "story_prev":
            context.user_data['story_index'] = context.user_data.get('story_index', 0) - 1
            await display_story(update, context, replace_current=True)
        elif data == "story_back_profile":
            await restore_profile_from_callback(update, context)

# --- CONCURRENT GLOBAL SCANNER (FAST) ---
async def _search_one_dialog(dialog, query, limit=10):
    """Search INSIDE one already-accessible dialog.

    This intentionally uses Telethon's peer-specific search path through
    get_messages(entity, search=...). It does NOT perform a global
    Telegram global message search.
    """
    try:
        msgs = await asyncio.wait_for(
            telethon_client.get_messages(dialog.entity, search=query, limit=limit),
            timeout=12,
        )
        results = []
        for msg in msgs:
            if not msg or not msg.id:
                continue
            text = (msg.message or "").strip()
            if not text and not msg.media:
                continue

            chat = dialog.entity
            username = getattr(chat, "username", None)
            title = (
                getattr(chat, "title", None)
                or " ".join(filter(None, [getattr(chat, "first_name", None), getattr(chat, "last_name", None)]))
                or username
                or str(getattr(chat, "id", "Unknown"))
            )

            if username:
                link = f"https://t.me/{username}/{msg.id}"
            else:
                chat_id = getattr(chat, "id", 0)
                if str(chat_id).startswith("-100"):
                    internal_id = str(chat_id)[4:]
                    link = f"https://t.me/c/{internal_id}/{msg.id}"
                else:
                    # Private users/chats do not always have public web links.
                    # Telegram's tg:// link opens the message for an account
                    # that has access to it.
                    if isinstance(chat, types.User):
                        link = f"tg://openmessage?user_id={chat.id}&message_id={msg.id}"
                    else:
                        link = f"tg://openmessage?chat_id={chat_id}&message_id={msg.id}"

            results.append({
                "link": link,
                "content": text[:180] if text else f"[{get_media_type(msg)} Media]",
                "chat": str(title),
                "date": msg.date,
                "message_id": msg.id,
            })
        return results
    except FloodWaitError as e:
        # Respect Telegram's limit once for this dialog. No infinite retry.
        await asyncio.sleep(min(e.seconds, 30))
        try:
            msgs = await telethon_client.get_messages(dialog.entity, search=query, limit=limit)
            results = []
            chat = dialog.entity
            username = getattr(chat, "username", None)
            title = (
                getattr(chat, "title", None)
                or " ".join(filter(None, [getattr(chat, "first_name", None), getattr(chat, "last_name", None)]))
                or username
                or str(getattr(chat, "id", "Unknown"))
            )
            for msg in msgs:
                if not msg or not msg.id:
                    continue
                text = (msg.message or "").strip()
                if not text and not msg.media:
                    continue
                if username:
                    link = f"https://t.me/{username}/{msg.id}"
                else:
                    chat_id = getattr(chat, "id", 0)
                    if str(chat_id).startswith("-100"):
                        link = f"https://t.me/c/{str(chat_id)[4:]}/{msg.id}"
                    else:
                        if isinstance(chat, types.User):
                            link = f"tg://openmessage?user_id={chat.id}&message_id={msg.id}"
                        else:
                            link = f"tg://openmessage?chat_id={chat_id}&message_id={msg.id}"
                results.append({
                    "link": link,
                    "content": text[:180] if text else f"[{get_media_type(msg)} Media]",
                    "chat": str(title),
                    "date": msg.date,
                    "message_id": msg.id,
                })
            return results
        except Exception:
            return []
    except Exception:
        return []


def _search_score(result, query):
    """Rank results approximately like relevance + activity."""
    text = result["content"].lower()
    q = query.strip().lower()
    words = [w for w in re.findall(r"\w+", q) if len(w) > 1]

    score = 0.0
    if q and q in text:
        score += 100.0
    if words:
        matched = sum(text.count(word) for word in words)
        score += matched * 12.0
        if all(word in text for word in words):
            score += 35.0

    dt = result.get("date")
    if dt:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age_days = max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0)
        score += max(0.0, 30.0 - min(age_days, 30.0))
        result["timestamp"] = dt.timestamp()
    else:
        result["timestamp"] = 0
    return score


async def fetch_search(update, context, query):
    """Search accessible dialogs without using the global message-search request.

    Telegram's global-search request is deliberately avoided. Instead, the
    authenticated user session searches each accessible dialog separately
    using peer-specific message search, then results are merged and ranked.
    """
    query = (query or "").strip()
    if not query:
        await update.message.reply_text("❌ Please enter a search keyword.")
        return

    status_msg = await update.message.reply_text(
        f"🔍 Searching your accessible chats for: {query}\n"
        "⏳ Searching chats separately for reliable results..."
    )

    try:
        # Search more than the old 30-dialog limit. Tune with an environment
        # variable if the account has a very large number of dialogs.
        max_dialogs = int(os.environ.get("SEARCH_MAX_DIALOGS", "300"))
        dialogs = await telethon_client.get_dialogs(limit=max_dialogs)

        # Avoid excessive concurrent RPCs. This is intentionally not a single
        # global search request.
        sem = asyncio.Semaphore(4)

        async def worker(dialog):
            async with sem:
                return await _search_one_dialog(dialog, query, limit=10)

        chunks = await asyncio.gather(*(worker(d) for d in dialogs), return_exceptions=True)

        merged = []
        seen = set()
        for chunk in chunks:
            if isinstance(chunk, Exception):
                continue
            for result in chunk:
                key = (result["chat"], result["message_id"])
                if key in seen:
                    continue
                seen.add(key)
                result["score"] = _search_score(result, query)
                merged.append(result)

        merged.sort(key=lambda r: (r.get("score", 0), r.get("timestamp", 0)), reverse=True)

        if not merged:
            await status_msg.edit_text(
                f"❌ No results found for '{query}'.\n\n"
                "The search checks the dialogs accessible to your authenticated Telegram account."
            )
            return

        context.user_data["search_results"] = merged
        context.user_data["search_query"] = query
        context.user_data["search_page"] = 1

        await status_msg.delete()
        await display_search_page(update, context, 1)

    except FloodWaitError as e:
        await status_msg.edit_text(
            f"⏳ Telegram rate-limited the search. Please wait {e.seconds} seconds and try again."
        )
    except Exception as e:
        await status_msg.edit_text(f"❌ Search failed: {html.escape(str(e))}")


async def display_search_page(update, context, page):
    callback = update.callback_query
    if callback:
        await callback.answer()

    results = context.user_data.get("search_results", [])
    search_query = context.user_data.get("search_query", "")
    if not results:
        text = f"🔎 No results for <b>{html.escape(search_query)}</b>"
        if callback:
            await callback.edit_message_text(text, parse_mode=ParseMode.HTML)
        else:
            await update.message.reply_text(text, parse_mode=ParseMode.HTML)
        return

    per_page = 10
    total_pages = max(1, (len(results) + per_page - 1) // per_page)
    page = max(1, min(int(page), total_pages))
    context.user_data["search_page"] = page

    start = (page - 1) * per_page
    page_items = results[start:start + per_page]

    # Keep the visual style close to the screenshot: title, keyword, then
    # clickable search results and pagination information.
    lines = [
        "<blockquote>",
        "<b>🔎 Telegram Search</b>",
        html.escape(search_query),
        "",
    ]

    for item in page_items:
        content = html.escape(item["content"].replace("\n", " "))
        chat = html.escape(item["chat"])
        link = html.escape(item["link"], quote=True)
        lines.append(f'🔗 <a href="{link}">{content}</a>')
        lines.append(f"   📁 {chat}")

    lines.extend([
        "",
        f"Page {page}/{total_pages}",
        "Sort by relevance and activity",
        "</blockquote>",
    ])
    text = "\n".join(lines)

    kb = []
    nav_row = []
    if page > 1:
        nav_row.append(InlineKeyboardButton("⬅️ Previous", callback_data="search_prev"))
    if page < total_pages:
        nav_row.append(InlineKeyboardButton("Next ➡️", callback_data="search_next"))
    if nav_row:
        kb.append(nav_row)
    kb.append([
        InlineKeyboardButton("🔄 New Search", callback_data="search"),
        InlineKeyboardButton("⬅️ Back", callback_data="more"),
    ])

    markup = InlineKeyboardMarkup(kb)
    if callback:
        await callback.edit_message_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)


# --- PDF FETCH ---
async def handle_pdf_upload(update, context):
    if context.user_data.get('state') != 'awaiting_pdf': return
    if not update.message.document:
        await update.message.reply_text("❌ Please upload a valid PDF document.")
        return
    if update.message.document.mime_type != "application/pdf":
        await update.message.reply_text("❌ The file you uploaded is not a PDF.")
        return

    status_msg = await update.message.reply_text("⏳ Processing PDF...")
    try:
        file = await context.bot.get_file(update.message.document.file_id)
        pdf_bytes = BytesIO()
        await file.download_to_memory(pdf_bytes)
        pdf_bytes.seek(0)

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        total_pages = len(doc)
        text_pages = []
        image_pages = []
        
        for page_num in range(total_pages):
            page = doc.load_page(page_num)
            page_text = page.get_text("text")
            if page_text.strip(): text_pages.append(page_text)
            images = page.get_images(full=True)
            for img in images:
                base_image = doc.extract_image(img[0])
                img_bytes = BytesIO(base_image["image"])
                img_bytes.seek(0)
                image_pages.append(img_bytes)
        
        doc.close()
        await status_msg.edit_text("✅ PDF processed. Sending results...")

        if image_pages:
            await update.message.reply_text(f"🖼 Found {len(image_pages)} images.")
            for i, img_bytes in enumerate(image_pages, 1):
                await update.message.reply_photo(photo=img_bytes, caption=f"Page Image {i}")
        if text_pages:
            full_text = "\n\n".join(text_pages)
            await update.message.reply_text(f"📄 Text from {len(text_pages)} pages.")
            for i in range(0, len(full_text), 4000):
                await update.message.reply_text(full_text[i:i+4000])
        if not image_pages and not text_pages:
            await status_msg.edit_text("❌ No text or images found.")
        context.user_data['state'] = None
    except Exception as e:
        await status_msg.edit_text(f"❌ Error: {e}")
        context.user_data['state'] = None

# --- PROFILE, POSTS, STORIES ---
async def fetch_profile(update, context, target):
    try:
        entity = await telethon_client.get_entity(target)
        context.user_data['profile_entity'] = entity
        context.user_data['post_entity'] = entity
        save_user_history(entity.id, getattr(entity, 'username', None), getattr(entity, 'first_name', ''), getattr(entity, 'last_name', ''))

        first_name = getattr(entity, 'first_name', '') or ''
        last_name = getattr(entity, 'last_name', '') or ''
        display_name = f"{first_name} {last_name}".strip() or getattr(entity, 'title', None) or "Unknown"
        username = getattr(entity, 'username', None)
        verified = getattr(entity, 'verified', False)
        premium = getattr(entity, 'premium', False)
        is_bot = getattr(entity, 'bot', False)
        about = getattr(entity, 'about', None) or 'No bio'

        text = (
            f"<blockquote><b>{html.escape(display_name)}</b>\n"
            f"@{html.escape(username or 'N/A')}\n\n"
            f"{html.escape(about)}\n\n"
            f"ID: {entity.id}\n"
            f"Verified: {verified}\n"
            f"Premium: {premium}\n"
            f"Bot: {is_bot}</blockquote>"
        )
        kb = [
            [
                InlineKeyboardButton("📰 View Posts", callback_data="posts_1"),
                InlineKeyboardButton("👁 View Story", callback_data="story_start"),
            ],
            [InlineKeyboardButton("⬅️ Back", callback_data="more")],
        ]

        try:
            photo = await telethon_client.download_profile_photo(entity, file=BytesIO())
            if photo:
                photo.seek(0)
                await update.message.reply_photo(
                    photo=photo,
                    caption=text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup(kb),
                )
            else:
                await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))
        except Exception:
            await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {html.escape(str(e))}", parse_mode=ParseMode.HTML)


async def handle_posts_pagination(update, context, page):
    query = update.callback_query
    await query.answer()
    entity = context.user_data.get('post_entity')
    if not entity:
        await query.edit_message_text("No posts found.")
        return
    per_page = 5
    messages = await telethon_client.get_messages(entity, limit=(page * per_page))
    total_posts = len(messages)
    start = (page - 1) * per_page
    end = min(start + per_page, total_posts)
    page_items = messages[start:end]
    if not page_items:
        await query.edit_message_text("No more posts to show.")
        return
    text = f"📰 POSTS OF {entity.title or entity.first_name}\nPage {page}\n\n"
    for m in page_items:
        content = m.message[:50] if m.message else f"[{get_media_type(m)}]"
        text += f"• {content}\n"
    kb = []
    if page > 1: kb.append([InlineKeyboardButton("⬅️ Previous", callback_data=f"posts_{page-1}")])
    if end < total_posts: kb.append([InlineKeyboardButton("Next ➡️", callback_data=f"posts_{page+1}")])
    kb.append([InlineKeyboardButton("⬅️ Back to Profile", callback_data="profile")])
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))

async def _get_accessible_stories(entity):
    """Fetch active stories for one peer using stories.getPeerStories."""
    response = await telethon_client(functions.stories.GetPeerStoriesRequest(peer=entity))
    stories = list(getattr(getattr(response, "stories", None), "stories", None) or [])
    # Some Telethon versions/objects may expose the vector directly.
    if not stories:
        raw = getattr(response, "stories", None)
        if isinstance(raw, (list, tuple)):
            stories = list(raw)
    return [s for s in stories if getattr(s, "id", None) and not isinstance(s, types.StoryItemDeleted)]


async def fetch_stories_from_entity(update, context, entity):
    """Fetch real active Stories for the already selected profile."""
    query = update.callback_query
    try:
        if not entity:
            await query.edit_message_text("❌ No Telegram user is selected.")
            return

        stories = await _get_accessible_stories(entity)
        if not stories:
            await query.answer("No active accessible Stories.", show_alert=True)
            await query.edit_message_text(
                "❌ No active accessible Story was returned by Telegram.\n\n"
                "The account may have no active Story, or the Story may not be available to the authenticated account."
            )
            return

        context.user_data['story_entity'] = entity
        context.user_data['stories_list'] = stories
        context.user_data['story_index'] = 0
        await query.answer("Story found")
        await display_story(update, context, replace_current=False)

    except FloodWaitError as e:
        await query.answer()
        await query.edit_message_text(f"⏳ Telegram rate limit. Please wait {e.seconds} seconds and try again.")
    except (ChannelPrivateError, UsernameNotOccupiedError) as e:
        await query.answer()
        await query.edit_message_text("❌ This Story cannot be accessed by the authenticated Telegram account.")
    except Exception as e:
        await query.answer()
        await query.edit_message_text(f"❌ Could not retrieve Stories: {html.escape(str(e))}", parse_mode=ParseMode.HTML)


async def display_story(update, context, replace_current=False):
    query = update.callback_query
    await query.answer()

    stories = context.user_data.get('stories_list') or []
    entity = context.user_data.get('story_entity')
    index = context.user_data.get('story_index', 0)

    if not stories:
        await query.edit_message_text("❌ No accessible Stories found.")
        return

    if index < 0:
        index = len(stories) - 1
    if index >= len(stories):
        index = 0
    context.user_data['story_index'] = index
    story = stories[index]

    if replace_current:
        # On next/previous, remove the old story message so only one story
        # remains visible at a time.
        try:
            await query.message.delete()
        except Exception:
            pass

    media = getattr(story, 'media', None)
    if not media:
        await query.message.reply_text("❌ This Story has no downloadable media.")
        return

    is_photo = isinstance(media, types.MessageMediaPhoto)
    is_document = isinstance(media, types.MessageMediaDocument)
    document = getattr(media, 'document', None)
    mime = (getattr(document, 'mime_type', '') or '').lower()
    is_video = is_document and (mime.startswith('video/') or getattr(document, 'attributes', None))

    if not (is_photo or is_document):
        await query.message.reply_text("❌ Unsupported Story media type.")
        return

    suffix = ".jpg" if is_photo else ".mp4" if mime.startswith('video/') else ".bin"
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp_path = tmp.name

        downloaded = await telethon_client.download_media(story, file=tmp_path)
        if not downloaded or not os.path.exists(tmp_path) or os.path.getsize(tmp_path) == 0:
            raise RuntimeError("Telegram did not return downloadable Story media.")

        caption_text = (getattr(story, 'message', None) or '').strip()
        base = caption_text if caption_text else "📖 Telegram Story"
        caption = f"{base}\n\n📖 Story {index + 1}/{len(stories)}"

        kb = []
        nav = []
        if len(stories) > 1:
            nav.append(InlineKeyboardButton("⬅️ Previous", callback_data="story_prev"))
            nav.append(InlineKeyboardButton("Next ➡️", callback_data="story_next"))
        if nav:
            kb.append(nav)
        kb.append([InlineKeyboardButton("⬅️ Back to Profile", callback_data="story_back_profile")])
        markup = InlineKeyboardMarkup(kb)

        with open(tmp_path, 'rb') as f:
            if is_photo:
                sent = await context.bot.send_photo(
                    chat_id=query.message.chat_id,
                    photo=f,
                    caption=caption[:1024],
                    reply_markup=markup,
                )
            elif mime.startswith('video/'):
                sent = await context.bot.send_video(
                    chat_id=query.message.chat_id,
                    video=f,
                    caption=caption[:1024],
                    supports_streaming=True,
                    reply_markup=markup,
                )
            else:
                sent = await context.bot.send_document(
                    chat_id=query.message.chat_id,
                    document=f,
                    caption=caption[:1024],
                    reply_markup=markup,
                )

        context.user_data['story_message_id'] = sent.message_id

        if not replace_current:
            # The original profile message remains in place. The Story is sent
            # as a separate real media message with navigation buttons.
            return

    except FloodWaitError as e:
        await context.bot.send_message(query.message.chat_id, f"⏳ Telegram rate limit. Wait {e.seconds} seconds and try again.")
    except Exception as e:
        await context.bot.send_message(query.message.chat_id, f"❌ Could not fetch Story: {html.escape(str(e))}", parse_mode=ParseMode.HTML)
    finally:
        if tmp_path:
            try:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
            except Exception:
                pass


async def restore_profile_from_callback(update, context):
    """Restore the selected profile after leaving Story view."""
    query = update.callback_query
    await query.answer()
    entity = context.user_data.get('profile_entity') or context.user_data.get('story_entity')
    if not entity:
        await query.edit_message_text("❌ Profile session expired. Please search for the user again.")
        return

    try:
        try:
            await query.message.delete()
        except Exception:
            pass

        first_name = getattr(entity, 'first_name', '') or ''
        last_name = getattr(entity, 'last_name', '') or ''
        display_name = f"{first_name} {last_name}".strip() or getattr(entity, 'title', None) or 'Unknown'
        username = getattr(entity, 'username', None)
        about = getattr(entity, 'about', None) or 'No bio'
        text = (
            f"<blockquote><b>{html.escape(display_name)}</b>\n"
            f"@{html.escape(username or 'N/A')}\n\n"
            f"{html.escape(about)}\n\n"
            f"ID: {entity.id}\n"
            f"Verified: {getattr(entity, 'verified', False)}\n"
            f"Premium: {getattr(entity, 'premium', False)}\n"
            f"Bot: {getattr(entity, 'bot', False)}</blockquote>"
        )
        kb = [[
            InlineKeyboardButton("📰 View Posts", callback_data="posts_1"),
            InlineKeyboardButton("👁 View Story", callback_data="story_start"),
        ], [InlineKeyboardButton("⬅️ Back", callback_data="more")]]
        photo = await telethon_client.download_profile_photo(entity, file=BytesIO())
        if photo:
            photo.seek(0)
            await context.bot.send_photo(query.message.chat_id, photo=photo, caption=text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))
        else:
            await context.bot.send_message(query.message.chat_id, text=text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))
    except Exception as e:
        await context.bot.send_message(query.message.chat_id, f"❌ Could not restore profile: {html.escape(str(e))}", parse_mode=ParseMode.HTML)


async def fetch_stories(update, context, target):
    """Compatibility entry point for any existing code that calls fetch_stories."""
    try:
        entity = await telethon_client.get_entity(target)
    except Exception as e:
        await update.message.reply_text(f"❌ Could not resolve user: {html.escape(str(e))}", parse_mode=ParseMode.HTML)
        return

    try:
        stories = await _get_accessible_stories(entity)
        if not stories:
            await update.message.reply_text("❌ No active accessible Story available for this user.")
            return
        context.user_data['profile_entity'] = entity
        context.user_data['story_entity'] = entity
        context.user_data['stories_list'] = stories
        context.user_data['story_index'] = 0
        await update.message.reply_text(f"📖 Found {len(stories)} accessible Story/Stories. Use the Story navigation buttons from the profile.")
    except FloodWaitError as e:
        await update.message.reply_text(f"⏳ Telegram rate limit. Please wait {e.seconds} seconds and try again.")
    except Exception as e:
        await update.message.reply_text(f"❌ Could not retrieve Stories: {html.escape(str(e))}", parse_mode=ParseMode.HTML)


# --- OTHER FEATURES ---
async def perform_words_analysis(update, context, target, limit):
    try:
        entity = await telethon_client.get_entity(target)
        messages = await telethon_client.get_messages(entity, limit=100)
        stop_words = set(["the", "a", "an", "is", "are", "was", "were", "to", "of", "in", "on", "for", "and", "or", "but", "with", "at", "by", "from", "up", "about", "into", "through", "during", "before", "after", "above", "below", "can", "will", "just", "not", "you", "your", "i", "me", "my", "it", "its", "this", "that", "these", "those", "we", "our", "they", "them", "their", "be", "been", "being", "do", "does", "did", "doing", "have", "has", "had", "having", "he", "she", "his", "her", "him", "so", "if", "then", "than", "too", "very", "am", "as", "at", "but", "by", "for", "from", "in", "into", "of", "on", "or", "to", "with", "www", "http", "https", "com"])
        word_data = {}
        for m in messages:
            if m.message:
                for word in re.findall(r'\w+', m.message.lower()):
                    if word not in stop_words and len(word) > 2:
                        if word not in word_data: word_data[word] = {'count': 0, 'messages': set()}
                        word_data[word]['count'] += 1
                        word_data[word]['messages'].add(m.id)
        sorted_words = sorted(word_data.items(), key=lambda x: x[1]['count'], reverse=True)[:limit]
        text = f"<blockquote>RIGID M (@{entity.username}) often uses this words:\n"
        for word, data in sorted_words: text += f"|{len(data['messages'])} - {data['count']} {word}\n"
        text += "</blockquote>"
        kb = [[InlineKeyboardButton("⬅️ Less Words", callback_data="words_less"), InlineKeyboardButton("More Words ➡️", callback_data="words_more")], [InlineKeyboardButton("⬅️ Back", callback_data="more")]]
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def fetch_words(update, context, target):
    context.user_data['word_target'] = target
    await perform_words_analysis(update, context, target, 10)

async def fetch_friends(update, context, text):
    parts = text.split(" ")
    if len(parts) < 2:
        await update.message.reply_text("⚠️ Use: @group @user")
        return
    try:
        group_entity = await telethon_client.get_entity(parts[0])
        target_user = await telethon_client.get_entity(parts[1])
        messages = await telethon_client.get_messages(group_entity, limit=500)
        reply_data = {}
        for m in messages:
            if m.sender_id == target_user.id and m.reply_to_msg_id:
                try:
                    reply_to_msg = await telethon_client.get_messages(group_entity, ids=m.reply_to_msg_id)
                    if reply_to_msg and reply_to_msg.sender_id:
                        sender_id = reply_to_msg.sender_id
                        if sender_id not in reply_data:
                            reply_data[sender_id] = {'count': 0, 'date': str(m.date)}
                            try:
                                sender_entity = await telethon_client.get_entity(sender_id)
                                reply_data[sender_id]['name'] = f"{sender_entity.first_name} {getattr(sender_entity, 'last_name', '')}"
                            except: reply_data[sender_id]['name'] = "Unknown"
                        reply_data[sender_id]['count'] += 1
                except: pass
        sorted_replies = sorted(reply_data.items(), key=lambda x: x[1]['count'], reverse=True)[:10]
        text = f"<blockquote>Replies them in the groups:\nwhen - to whom (total times)\n"
        for sid, data in sorted_replies: text += f"|{data['date'][:5]} - {data['name']} ({data['count']})\n"
        text += "</blockquote>"
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def fetch_names(update, context, target):
    try:
        entity = await telethon_client.get_entity(target)
        save_user_history(entity.id, entity.username, getattr(entity, 'first_name', ''), getattr(entity, 'last_name', ''))
        history = get_user_history(entity.id)
        text = f"<blockquote>Names history {entity.first_name} (@{entity.username}):\n\nusernames:\n"
        if history:
            seen = set()
            for h in history:
                if h[0] and h[0] not in seen:
                    text += f"1. @{h[0]} [{h[3][:10]}]\n"
                    seen.add(h[0])
        else: text += "No history yet.\n"
        text += "\nfirst name / last name:\n"
        if history:
            for h in history[:5]: text += f"|{h[3][:10]} -> {h[1]} {h[2]}\n"
        else: text += "No history yet.\n"
        text += "</blockquote>"
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

# --- MESSAGE HANDLER ---
async def handle_link(update, context):
    user_id = update.effective_user.id
    text = update.message.text if update.message.text else ""
    if context.user_data.get('state') == 'awaiting_pdf':
        await handle_pdf_upload(update, context)
        return
    if context.user_data.get('state') == 'awaiting_password':
        if text == BOT_PASSWORD:
            add_authenticated_user(user_id)
            context.user_data['state'] = None
            await update.message.reply_text("✅ Access granted!")
            keyboard = [[InlineKeyboardButton("📥 Inbox", callback_data="inbox"), InlineKeyboardButton("👤 Profile", callback_data="profile")], [InlineKeyboardButton("🔗 Fetch Telegram", callback_data="fetch")], [InlineKeyboardButton("➕ More Commands", callback_data="more")]]
            await update.message.reply_text("🤖 TELEGRAM ASSISTANT", reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await update.message.reply_text("❌ Incorrect password. Please try again.")
        return
    if not is_authenticated(user_id):
        await update.message.reply_text("🔐 Password required. Please run /start and authenticate first.")
        return
    if context.user_data.get('reply_to'):
        target_chat = context.user_data['reply_to']
        try:
            await telethon_client.send_message(target_chat, text)
            context.user_data['reply_to'] = None
            await update.message.reply_text("✅ Reply sent!")
        except Exception as e:
            await update.message.reply_text(f"❌ Failed: {e}")
        return

    state = context.user_data.get('state')
    if state:
        context.user_data['state'] = None
        if state == 'profile_query': await fetch_profile(update, context, text)
        elif state == 'search_query': await fetch_search(update, context, text)
        elif state == 'words': await fetch_words(update, context, text)
        elif state == 'friends': await fetch_friends(update, context, text)
        elif state == 'names': await fetch_names(update, context, text)
        return

    if "t.me" in text:
        username, msg_id = parse_tg_link(text)
        if not username:
            await update.message.reply_text("Invalid link format.")
            return
        try:
            entity = await telethon_client.get_entity(username)
            if msg_id:
                msg = await telethon_client.get_messages(entity, ids=msg_id)
                await safe_send(update.message.chat_id, context.bot, msg, from_chat_id=entity.id, message_id=msg.id)
            else:
                status_msg = await update.message.reply_text("Fetching batch (max 20)...")
                messages = await telethon_client.get_messages(entity, limit=20)
                if not messages:
                    await status_msg.edit_text("No messages found.")
                    return
                for idx, msg in enumerate(messages, 1):
                    if idx % 5 == 0: await status_msg.edit_text(f"Fetching {idx}/{len(messages)}...")
                    await safe_send(update.message.chat_id, context.bot, msg, from_chat_id=entity.id, message_id=msg.id)
                await status_msg.edit_text(f"✅ Batch complete!")
        except Exception as e:
            await handle_telethon_error(update, e)
    else:
        await update.message.reply_text("👋 Use the menu buttons, or send a Telegram link.")

# --- INBOX LISTENER ---
@telethon_client.on(events.NewMessage(incoming=True))
async def inbox_listener(event):
    if event.is_private and not event.out:
        try:
            sender = await event.get_sender()
            add_inbox_message(event.chat_id, sender.id, getattr(sender, 'first_name', 'Unknown'), getattr(sender, 'username', 'N/A'), event.raw_text if event.raw_text else "", get_media_type(event), str(event.date))
        except Exception as e:
            print(f"Inbox Error: {e}")

# --- MAIN ---
async def main():
    init_db()
    try:
        await telethon_client.start()
        print("Telethon connected!")
    except Exception as e:
        print(f"Telethon fail: {e}")
        return
    bot_app = Application.builder().token(BOT_TOKEN).build()
    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(CommandHandler("logout", logout))
    bot_app.add_handler(CallbackQueryHandler(menu_callback))
    bot_app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_link))
    print("Bot running...")
    await bot_app.initialize()
    await bot_app.start()
    await bot_app.updater.start_polling()
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=10000), daemon=True).start()
    await asyncio.Event().wait()

if __name__ == '__main__':
    asyncio.run(main())
