import re
import asyncio
import os
import sys
import time
import threading
import sqlite3
import tempfile
import urllib.request
from io import BytesIO
from flask import Flask
from PIL import Image
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, InputMediaVideo, InputFile
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler, ContextTypes
from telethon import TelegramClient, events, functions, types
from telethon.sessions import StringSession
from telethon.errors import (FloodWaitError, ChannelPrivateError, UsernameNotOccupiedError, MessageIdInvalidError)
import pymupdf
import img2pdf
from pdf2docx import Converter
import yt_dlp
import json

try:
    from telethon.tl.functions.stories import GetPeerStoriesRequest, GetStoriesByIDRequest
except ImportError:
    GetPeerStoriesRequest = None
    GetStoriesByIDRequest = None

app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is alive!"

# --- CONFIGURATION ---
BOT_TOKEN = os.environ.get('BOT_TOKEN', '')
API_ID = int(os.environ.get('API_ID', 0))
API_HASH = os.environ.get('API_HASH', '')
STRING_SESSION = os.environ.get('STRING_SESSION', '')
BOT_PASSWORD = os.environ.get("BOT_PASSWORD", "ptss25")
RENDER_URL = os.environ.get('RENDER_URL', 'https://samibot-s1h6.onrender.com')

ADMIN_IDS = []
admin_ids_str = os.environ.get('ADMIN_IDS', '')
if admin_ids_str:
    ADMIN_IDS = [int(x.strip()) for x in admin_ids_str.split(',') if x.strip().isdigit()]

telethon_client = TelegramClient(StringSession(STRING_SESSION), API_ID, API_HASH)

LOCK_FILE = '/tmp/samibot.lock'
def acquire_lock():
    if os.path.exists(LOCK_FILE):
        with open(LOCK_FILE, 'r') as f:
            pid = f.read().strip()
        print(f"❌ Conflict Detected! Another instance (PID: {pid}) may still be running.")
        return False
    with open(LOCK_FILE, 'w') as f:
        f.write(str(os.getpid()))
    return True

def release_lock():
    try:
        if os.path.exists(LOCK_FILE):
            os.unlink(LOCK_FILE)
    except Exception:
        pass

def self_ping():
    try:
        urllib.request.urlopen(RENDER_URL, timeout=5)
    except Exception:
        pass

def is_admin(user_id):
    return user_id in ADMIN_IDS

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

def get_all_authenticated_users():
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute("SELECT user_id FROM users")
    rows = c.fetchall()
    conn.close()
    return [row[0] for row in rows]

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

def parse_tg_link(text):
    comment_pattern = r'https?://t\.me/([a-zA-Z0-9_]+)/(\d+)/(\d+)'
    match = re.search(comment_pattern, text)
    if match:
        return match.group(1), int(match.group(2)), int(match.group(3))
    range_pattern = r'https?://t\.me/([a-zA-Z0-9_]+)/(\d+)-(\d+)'
    match = re.search(range_pattern, text)
    if match:
        return match.group(1), int(match.group(2)), int(match.group(3))
    single_pattern = r'https?://t\.me/([a-zA-Z0-9_]+)/(\d+)'
    match = re.search(single_pattern, text)
    if match:
        return match.group(1), int(match.group(2)), None
    channel_pattern = r'https?://t\.me/([a-zA-Z0-9_]+)'
    match = re.search(channel_pattern, text)
    if match:
        return match.group(1), None, None
    return None, None, None

def get_media_type(event):
    if event.photo: return "📷"
    elif event.video: return "🎬"
    elif event.document: return "🎞️" if event.gif else "📄"
    elif event.audio: return "🎵"
    elif event.voice: return "🎤"
    elif event.sticker: return "🧩"
    else: return "💬"

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

async def start(update, context):
    user_id = update.effective_user.id
    self_ping()
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

async def broadcast_command(update, context):
    user_id = update.effective_user.id
    if not is_authenticated(user_id) or not is_admin(user_id):
        await update.message.reply_text("🔒 Admin only command.")
        return
    if len(context.args) == 0:
        await update.message.reply_text("📢 Usage: /broadcast <message>")
        return
    message_text = " ".join(context.args)
    users = get_all_authenticated_users()
    sent = 0
    failed = 0
    for uid in users:
        try:
            await context.bot.send_message(chat_id=uid, text=message_text)
            sent += 1
        except Exception:
            failed += 1
    await update.message.reply_text(f"✅ Broadcast sent to {sent} users! ❌ Failed: {failed}")

async def set_bot_name(update, context):
    user_id = update.effective_user.id
    if not is_authenticated(user_id) or not is_admin(user_id):
        await update.message.reply_text("🔒 Admin only command.")
        return
    if len(context.args) == 0:
        await update.message.reply_text("🤖 Usage: /setname <New Bot Name>")
        return
    new_name = " ".join(context.args)
    try:
        await context.bot.set_my_name(new_name)
        await update.message.reply_text(f"✅ Bot name updated to: {new_name}")
    except Exception as e:
        await update.message.reply_text(f"❌ Could not update name: {e}")

async def set_bot_description(update, context):
    user_id = update.effective_user.id
    if not is_authenticated(user_id) or not is_admin(user_id):
        await update.message.reply_text("🔒 Admin only command.")
        return
    if len(context.args) == 0:
        await update.message.reply_text("🤖 Usage: /setdesc <New Description>")
        return
    new_desc = " ".join(context.args)
    try:
        await context.bot.set_my_description(new_desc)
        await update.message.reply_text("✅ Bot description updated.")
    except Exception as e:
        await update.message.reply_text(f"❌ Could not update description: {e}")

async def set_bot_photo(update, context):
    user_id = update.effective_user.id
    if not is_authenticated(user_id) or not is_admin(user_id):
        await update.message.reply_text("🔒 Admin only command.")
        return
    if not update.message.photo:
        await update.message.reply_text("🤖 Usage: Send a photo after `/setphoto`")
        return
    try:
        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        photo_bytes = BytesIO()
        await file.download_to_memory(photo_bytes)
        photo_bytes.seek(0)
        await context.bot.set_my_profile_photo(photo_bytes)
        await update.message.reply_text("✅ Bot profile photo updated.")
    except Exception as e:
        await update.message.reply_text(f"❌ Could not update profile photo: {e}")

async def restart_command(update, context):
    user_id = update.effective_user.id
    if not is_authenticated(user_id) or not is_admin(user_id):
        await update.message.reply_text("🔒 Admin only command.")
        return
    await update.message.reply_text("🔄 Restarting bot... Please wait.")
    release_lock()
    try:
        app = context.application
        await app.stop()
        await app.shutdown()
    except Exception:
        pass
    os.execv(sys.executable, [sys.executable] + sys.argv)

async def menu_callback(update, context):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    if not is_authenticated(user_id):
        await query.edit_message_text("🔐 Password required.")
        return

    data = query.data
    if data == "main_menu":
        keyboard = [
            [InlineKeyboardButton("📥 Inbox", callback_data="inbox"), InlineKeyboardButton("👤 Profile", callback_data="profile")],
            [InlineKeyboardButton("🔗 Fetch Telegram", callback_data="fetch")],
            [InlineKeyboardButton("➕ More Commands", callback_data="more")]
        ]
        await query.edit_message_text("🤖 TELEGRAM ASSISTANT", reply_markup=InlineKeyboardMarkup(keyboard))
    elif data == "more":
        kb = [
            [InlineKeyboardButton("🔎 Search", callback_data="search"), InlineKeyboardButton("📊 Statistics", callback_data="stats")],
            [InlineKeyboardButton("📄 PDF Fetch", callback_data="pdf_fetch")],
            [InlineKeyboardButton("🔄 Converter", callback_data="converter")],
            [InlineKeyboardButton("🎬 Video Downloader", callback_data="video_downloader")]
        ]
        if is_admin(user_id):
            admin_buttons = [
                [InlineKeyboardButton("🔔 Track", callback_data="track"), InlineKeyboardButton("🔗 Names", callback_data="names")],
                [InlineKeyboardButton("👥 Groups", callback_data="groups"), InlineKeyboardButton("💬 Messages", callback_data="messages")],
                [InlineKeyboardButton("🔎 Analysis", callback_data="analysis"), InlineKeyboardButton("📢 Channels", callback_data="channels")],
                [InlineKeyboardButton("👍 Reputation", callback_data="rep"), InlineKeyboardButton("👥 Friends", callback_data="friends")],
                [InlineKeyboardButton("🔄 Reactions", callback_data="reactions"), InlineKeyboardButton("🎁 Gifts", callback_data="gifts")],
                [InlineKeyboardButton("📤 Share", callback_data="share"), InlineKeyboardButton("🔵 Words Frequency", callback_data="words")],
                [InlineKeyboardButton("👥 Common Groups", callback_data="common")],
                [InlineKeyboardButton("📢 Broadcast", callback_data="broadcast")]
            ]
            kb = admin_buttons + kb
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="main_menu")])
        await query.edit_message_text("➕ MORE COMMANDS", reply_markup=InlineKeyboardMarkup(kb))
    elif data == "converter":
        kb = [
            [InlineKeyboardButton("📄 PDF to Word", callback_data="pdf_to_word"), InlineKeyboardButton("🖼️ Image to Text", callback_data="image_to_text")],
            [InlineKeyboardButton("🖼️ Image to PDF", callback_data="image_to_pdf")],
            [InlineKeyboardButton("⬅️ Back", callback_data="more")]
        ]
        await query.edit_message_text("🔄 MEDIA CONVERTER\n\nChoose an option:", reply_markup=InlineKeyboardMarkup(kb))
    elif data == "pdf_to_word":
        await query.edit_message_text("📄 PDF to Word\n\nPlease upload the PDF file.")
        context.user_data['state'] = 'awaiting_pdf_to_word'
    elif data == "image_to_text":
        await query.edit_message_text("🖼️ Image to Text\n\nPlease upload an image.")
        context.user_data['state'] = 'awaiting_image_to_text'
    elif data == "image_to_pdf":
        await query.edit_message_text("🖼️ Image to PDF\n\nSend images (1-10). Click Done when finished.")
        context.user_data['state'] = 'awaiting_image_to_pdf'
        context.user_data['pdf_images'] = []
    elif data == "img_pdf_done":
        await process_image_pdf(update, context)
    elif data == "video_downloader":
        await query.edit_message_text("🎬 VIDEO DOWNLOADER\n\nSend me a YouTube, TikTok, Instagram, or Facebook video link.")
        context.user_data['state'] = 'awaiting_video_link'
    elif data == "broadcast":
        await query.edit_message_text("📢 BROADCAST\n\nUsage: /broadcast <message>")
    elif data == "profile":
        await query.edit_message_text("👤 PROFILE\n\nEnter a Telegram username or ID to generate the Profile Card:")
        context.user_data['state'] = 'profile_query'
    elif data == "fetch":
        await query.edit_message_text("🔗 Fetch Telegram\n\nSend me a link (e.g., t.me/channel/123 or t.me/channel/123-130):")
        context.user_data['state'] = 'fetch_link'
    elif data == "search":
        await query.edit_message_text("🔎 SEARCH\n\nEnter any keyword to search across your chats:\nExample: Logic mid")
        context.user_data['state'] = 'search_query'
    elif data == "pdf_fetch":
        await query.edit_message_text("📄 PDF FETCH\n\nPlease upload the PDF file directly to this chat.")
        context.user_data['state'] = 'awaiting_pdf'
    elif data.startswith("posts_"):
        page = int(data.split("_")[1])
        await handle_posts_pagination(update, context, page)
    elif data.startswith("story_"):
        if data == "story_start":
            context.user_data["story_index"] = 0
            entity = context.user_data.get("story_entity") or context.user_data.get("profile_entity")
            if not entity:
                await query.edit_message_text("❌ No profile selected.")
                return
            await query.answer("Fetching Stories…")
            try:
                if not GetPeerStoriesRequest:
                    await query.edit_message_text("❌ Telethon does not support stories in this installation.")
                    return
                response = await telethon_client(GetPeerStoriesRequest(peer=entity))
                stories = list(getattr(response, "stories", []) or [])
                if not stories:
                    await query.edit_message_text("❌ No active Stories are available.")
                    return
                context.user_data["stories_list"] = stories
                await display_story(update, context)
            except Exception as e:
                await query.edit_message_text(f"❌ Could not retrieve Stories: {e}")
        elif data == "story_next":
            context.user_data["story_index"] = context.user_data.get("story_index", 0) + 1
            await display_story(update, context)
        elif data == "story_prev":
            context.user_data["story_index"] = context.user_data.get("story_index", 0) - 1
            await display_story(update, context)

async def fetch_profile(update, context, target):
    try:
        entity = await telethon_client.get_entity(target)
        context.user_data["profile_entity"] = entity
        context.user_data["post_entity"] = entity
        context.user_data["story_entity"] = entity
        save_user_history(entity.id, getattr(entity, "username", None), getattr(entity, "first_name", ""), getattr(entity, "last_name", ""))
        first_name = getattr(entity, "first_name", "") or ""
        last_name = getattr(entity, "last_name", "") or ""
        display_name = f"{first_name} {last_name}".strip() or getattr(entity, "title", "Unknown")
        text = f"<blockquote><b>{display_name}</b>\n@{getattr(entity, 'username', None) or 'N/A'}\n\n{getattr(entity, 'about', 'No bio')}\n\nID: {entity.id}\nVerified: {getattr(entity, 'verified', False)}\nPremium: {getattr(entity, 'premium', False)}\nBot: {getattr(entity, 'bot', False)}</blockquote>"
        kb = [[InlineKeyboardButton("📰 View Posts", callback_data="posts_1"), InlineKeyboardButton("👁 View Story", callback_data="story_start")], [InlineKeyboardButton("⬅️ Back", callback_data="more")]]
        try:
            photo = await telethon_client.download_profile_photo(entity, file=BytesIO())
            if photo:
                photo.seek(0)
                await update.message.reply_photo(photo=photo, caption=text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))
            else:
                await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))
        except Exception:
            await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {type(e).__name__}: {e}")

async def handle_posts_pagination(update, context, page):
    query = update.callback_query
    await query.answer()
    entity = context.user_data.get("post_entity") or context.user_data.get("profile_entity")
    if not entity:
        await query.edit_message_text("❌ No profile selected.")
        return
    per_page = 5
    try:
        messages = await telethon_client.get_messages(entity, limit=(page * per_page))
    except Exception as e:
        await query.edit_message_text(f"❌ Could not fetch posts: {e}")
        return
    total_posts = len(messages)
    start = (page - 1) * per_page
    end = min(start + per_page, total_posts)
    page_items = messages[start:end]
    if not page_items:
        await query.edit_message_text("No more posts to show.")
        return
    title = getattr(entity, "title", None) or getattr(entity, "first_name", "User")
    text = f"📰 POSTS OF {title}\nPage {page}\n\n"
    for m in page_items:
        content = (m.message or "").strip()[:80] if m.message else f"[{get_media_type(m)}]"
        text += f"• {content}\n"
    kb = []
    if page > 1: kb.append([InlineKeyboardButton("⬅️ Previous", callback_data=f"posts_{page-1}")])
    if end < total_posts: kb.append([InlineKeyboardButton("Next ➡️", callback_data=f"posts_{page+1}")])
    kb.append([InlineKeyboardButton("⬅️ Back to Profile", callback_data="profile")])
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))

async def fetch_search(update, context, query):
    status_msg = await update.message.reply_text(f"🔎 Searching accessible chats and public Telegram chats for: {query}")
    try:
        dialogs = await telethon_client.get_dialogs(limit=200)
        peers = []
        seen = set()
        for dialog in dialogs:
            entity = getattr(dialog, "entity", None)
            if not entity: continue
            key = (getattr(entity, "id", None), getattr(entity, "access_hash", None))
            if key[0] not in seen:
                seen.add(key[0]); peers.append(entity)
        all_results = []
        sem = asyncio.Semaphore(8)
        async def worker(entity):
            async with sem:
                try:
                    messages = await telethon_client.get_messages(entity, search=query, limit=3)
                    return [{"entity": entity, "message": m, "link": f"https://t.me/{getattr(entity, 'username', entity.id)}/{m.id}", "content": (m.message or "[Media]")[:80]} for m in messages]
                except Exception:
                    return []
        batches = await asyncio.gather(*(worker(e) for e in peers))
        for batch in batches:
            all_results.extend(batch)
        if not all_results:
            await status_msg.edit_text(f"❌ No results found for '{query}'.")
            return
        context.user_data["search_results"] = all_results
        context.user_data["search_page"] = 1
        await status_msg.delete()
        await display_search_page(update, context, 1)
    except Exception as e:
        await status_msg.edit_text(f"❌ Search failed: {e}")

async def display_search_page(update, context, page):
    query = update.callback_query
    if query: await query.answer()
    results = context.user_data.get("search_results", [])
    query_text = " "
    per_page = 10
    total_pages = max(1, (len(results) + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    start = (page - 1) * per_page
    page_items = results[start:start + per_page]
    text = f"<blockquote><b>Telegram Search</b>\n{query_text}\n\n"
    for item in page_items:
        text += f"🔗 <a href='{item['link']}'>{item['content']}</a>\n\n"
    text += f"Page {page}/{total_pages}\nSort by relevance and activity</blockquote>"
    kb = []
    nav_row = []
    if page > 1: nav_row.append(InlineKeyboardButton("⬅️ Previous", callback_data="search_prev"))
    if page < total_pages: nav_row.append(InlineKeyboardButton("Next ➡️", callback_data="search_next"))
    if nav_row: kb.append(nav_row)
    kb.append([InlineKeyboardButton("🔄 New Search", callback_data="search"), InlineKeyboardButton("⬅️ Back", callback_data="more")])
    if query: await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
    else: await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)

async def handle_pdf_to_word(update, context):
    if context.user_data.get('state') != 'awaiting_pdf_to_word': return
    if not update.message.document:
        await update.message.reply_text("❌ Please upload a PDF document.")
        return
    if update.message.document.mime_type != "application/pdf":
        await update.message.reply_text("❌ The file you uploaded is not a PDF.")
        return
    status_msg = await update.message.reply_text("⏳ Converting PDF to Word...")
    try:
        file = await context.bot.get_file(update.message.document.file_id)
        pdf_bytes = BytesIO(); await file.download_to_memory(pdf_bytes); pdf_bytes.seek(0)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_pdf:
            tmp_pdf.write(pdf_bytes.read()); tmp_pdf_path = tmp_pdf.name
        with tempfile.NamedTemporaryFile(delete=False, suffix=".docx") as tmp_docx:
            tmp_docx_path = tmp_docx.name
        cv = Converter(tmp_pdf_path); cv.convert(tmp_docx_path); cv.close()
        with open(tmp_docx_path, 'rb') as docx_file:
            await update.message.reply_document(document=docx_file, filename="converted.docx")
        os.unlink(tmp_pdf_path); os.unlink(tmp_docx_path)
        await status_msg.edit_text("✅ PDF converted to Word successfully!")
        context.user_data['state'] = None
    except Exception as e:
        await status_msg.edit_text(f"❌ Conversion failed: {e}"); context.user_data['state'] = None

async def handle_image_to_text(update, context):
    if context.user_data.get('state') != 'awaiting_image_to_text': return
    if not update.message.photo:
        await update.message.reply_text("❌ Please upload an image."); return
    status_msg = await update.message.reply_text("⏳ Extracting text from image...")
    try:
        from rapidocr_onnxruntime import RapidOCR
        ocr = RapidOCR()
        photo = update.message.photo[-1]; file = await context.bot.get_file(photo.file_id)
        img_bytes = BytesIO(); await file.download_to_memory(img_bytes); img_bytes.seek(0)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp_img:
            tmp_img.write(img_bytes.read()); tmp_img_path = tmp_img.name
        result, elapse = ocr(tmp_img_path); os.unlink(tmp_img_path)
        if not result:
            await status_msg.edit_text("❌ No text found in the image."); return
        extracted_text = "\n".join([line[1] for line in result])
        await update.message.reply_text(f"📝 **Extracted Text:**\n\n{extracted_text}")
        await status_msg.edit_text("✅ Text extraction complete!"); context.user_data['state'] = None
    except Exception as e:
        await status_msg.edit_text(f"❌ OCR failed: {e}"); context.user_data['state'] = None

async def handle_image_collect(update, context):
    if context.user_data.get('state') != 'awaiting_image_to_pdf': return
    if not update.message.photo:
        await update.message.reply_text("❌ Please upload an image or click Done to finish."); return
    if 'pdf_images' not in context.user_data:
        context.user_data['pdf_images'] = []
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    img_bytes = BytesIO(); await file.download_to_memory(img_bytes); img_bytes.seek(0)
    context.user_data['pdf_images'].append(img_bytes)
    count = len(context.user_data['pdf_images'])
    if count >= 10:
        await process_image_pdf(update, context)
    else:
        await update.message.reply_text(f"✅ Image {count}/10 added.\nSend another image, or click Done to process.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Done", callback_data="img_pdf_done")]]))

async def process_image_pdf(update, context):
    query = update.callback_query
    if query:
        await query.answer()
    if not context.user_data.get('pdf_images'):
        if query:
            await query.edit_message_text("❌ No images received.")
        else:
            await update.message.reply_text("❌ No images received.")
        context.user_data['state'] = None; return

    try:
        images = context.user_data['pdf_images']
        pdf_bytes = img2pdf.convert([img.getvalue() for img in images])

        chat_id = query.message.chat_id if query else update.effective_chat.id
        await context.bot.send_document(chat_id=chat_id, document=BytesIO(pdf_bytes), filename="images.pdf")

        if query:
            await query.edit_message_text("✅ Images converted to PDF!")
        else:
            await update.message.reply_text("✅ Images converted to PDF!")
        context.user_data['state'] = None; context.user_data['pdf_images'] = []
    except Exception as e:
        error_msg = f"❌ Conversion failed: {e}"
        if query:
            await query.edit_message_text(error_msg)
        else:
            await update.message.reply_text(error_msg)
        context.user_data['state'] = None; context.user_data['pdf_images'] = []

# --- SUPER POWERFUL VIDEO DOWNLOADER ---
async def handle_video_download(update, context):
    if context.user_data.get('state') != 'awaiting_video_link': return
    url = update.message.text
    if not url.startswith("http"):
        await update.message.reply_text("❌ Please send a valid video URL (YouTube, TikTok, Instagram, Facebook)."); return
    
    status_msg = await update.message.reply_text("⏳ Downloading video with advanced methods...")
    
    # Define multiple User-Agents to bypass blocks
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
        "Mozilla/5.0 (Linux; Android 13; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.0.0 Mobile Safari/537.36"
    ]
    
    # Extensive extractor configurations
    clients = [
        {'extractor_args': {'youtube': {'player_client': ['android', 'ios', 'tv', 'web_safari', 'mweb']}}},
        {'extractor_args': {'youtube': {'player_client': ['android_vr', 'web_embedded']}}},
        {}
    ]
    
    ydl_opts_list = []
    for client in clients:
        for ua in user_agents:
            ydl_opts_list.append({
                'format': 'best[height<=1080]',
                'outtmpl': os.path.join(tempfile.gettempdir(), '%(title)s.%(ext)s'),
                'quiet': True,
                'no_warnings': True,
                'noplaylist': True,
                'retries': 10,
                'fragment_retries': 10,
                'ignoreerrors': True,
                'http_headers': {'User-Agent': ua},
                **client
            })
    
    # Add fallback for direct URL sending if download fails
    ydl_opts_list.append({
        'format': 'best',
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
        'simulate': True,
        'skip_download': True,
        'http_headers': {'User-Agent': user_agents[0]}
    })
    
    filename = None
    last_error = ""
    for opts in ydl_opts_list:
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=not opts.get('simulate'))
                if opts.get('simulate'):
                    # If simulated, try sending the direct URL
                    direct_url = info.get('url') or info.get('webpage_url') or url
                    await status_msg.edit_text("✅ Video detected! Sending direct link...")
                    await update.message.reply_text(f"🎬 **Direct Video Link:**\n{direct_url}")
                    context.user_data['state'] = None
                    return
                
                filename = ydl.prepare_filename(info)
                if os.path.exists(filename) and os.path.getsize(filename) > 0:
                    break
        except Exception as e:
            last_error = str(e)
            continue
    
    if not filename or not os.path.exists(filename):
        await status_msg.edit_text(
            f"❌ Could not download video despite advanced methods.\n"
            f"Last Error: {last_error[:200]}"
        )
        context.user_data['state'] = None
        return
    
    try:
        with open(filename, 'rb') as video_file:
            await update.message.reply_video(video=video_file, caption="✅ Downloaded successfully!")
        os.unlink(filename)
        await status_msg.edit_text("✅ Video download complete!")
        context.user_data['state'] = None
    except Exception as e:
        await status_msg.edit_text(f"❌ Error sending video: {e}")
        context.user_data['state'] = None

async def handle_link(update, context):
    user_id = update.effective_user.id
    text = update.message.text if update.message.text else ""
    self_ping()

    if context.user_data.get('state') == 'awaiting_image_to_pdf':
        await handle_image_collect(update, context); return
    if context.user_data.get('state') == 'awaiting_pdf':
        await handle_pdf_upload(update, context); return
    if context.user_data.get('state') == 'awaiting_pdf_to_word':
        await handle_pdf_to_word(update, context); return
    if context.user_data.get('state') == 'awaiting_image_to_text':
        await handle_image_to_text(update, context); return
    if context.user_data.get('state') == 'awaiting_video_link':
        await handle_video_download(update, context); return
    if context.user_data.get('state') == 'awaiting_password':
        if text == BOT_PASSWORD:
            add_authenticated_user(user_id); context.user_data['state'] = None
            await update.message.reply_text("✅ Access granted!")
            keyboard = [[InlineKeyboardButton("📥 Inbox", callback_data="inbox"), InlineKeyboardButton("👤 Profile", callback_data="profile")], [InlineKeyboardButton("🔗 Fetch Telegram", callback_data="fetch")], [InlineKeyboardButton("➕ More Commands", callback_data="more")]]
            await update.message.reply_text("🤖 TELEGRAM ASSISTANT", reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await update.message.reply_text("❌ Incorrect password. Please try again.")
        return
    if not is_authenticated(user_id):
        await update.message.reply_text("🔐 Password required. Please run /start and authenticate first."); return
    if context.user_data.get('reply_to'):
        target_chat = context.user_data['reply_to']
        try:
            await telethon_client.send_message(target_chat, text); context.user_data['reply_to'] = None
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
        username, msg_id, comment_id = parse_tg_link(text)
        if not username:
            await update.message.reply_text("Invalid link format."); return
        try:
            entity = await telethon_client.get_entity(username)
            if msg_id and comment_id:
                status_msg = await update.message.reply_text(f"⏳ Fetching comment {comment_id}...")
                try:
                    msg = await telethon_client.get_messages(entity, ids=comment_id)
                    if msg:
                        await safe_send(update.message.chat_id, context.bot, msg, from_chat_id=entity.id, message_id=comment_id)
                        await status_msg.edit_text("✅ Comment fetched!")
                    else:
                        await status_msg.edit_text("❌ Comment ID not found.")
                except Exception as e:
                    await status_msg.edit_text(f"❌ Could not fetch comment: {e}")
            elif msg_id and "-" in text:
                range_pattern = r'https?://t\.me/([a-zA-Z0-9_]+)/(\d+)-(\d+)'
                m = re.search(range_pattern, text)
                if m:
                    msg_id_start = int(m.group(2)); msg_id_end = int(m.group(3))
                    status_msg = await update.message.reply_text(f"⏳ Fetching {msg_id_start} to {msg_id_end}...")
                    messages = await telethon_client.get_messages(entity, min_id=msg_id_start, max_id=msg_id_end + 1)
                    if not messages:
                        await status_msg.edit_text("❌ No messages in that range."); return
                    for idx, msg in enumerate(messages, 1):
                        if idx % 5 == 0: await status_msg.edit_text(f"Fetching {idx}/{len(messages)}...")
                        await safe_send(update.message.chat_id, context.bot, msg, from_chat_id=entity.id, message_id=msg.id)
                    await status_msg.edit_text(f"✅ Range complete! Fetched {len(messages)} messages."); return
            elif msg_id:
                msg = await telethon_client.get_messages(entity, ids=msg_id)
                if not msg:
                    await update.message.reply_text("❌ Message not found."); return
                await safe_send(update.message.chat_id, context.bot, msg, from_chat_id=entity.id, message_id=msg.id)
            else:
                status_msg = await update.message.reply_text("Fetching batch (max 20)...")
                messages = await telethon_client.get_messages(entity, limit=20)
                if not messages:
                    await status_msg.edit_text("No messages found."); return
                for idx, msg in enumerate(messages, 1):
                    if idx % 5 == 0: await status_msg.edit_text(f"Fetching {idx}/{len(messages)}...")
                    await safe_send(update.message.chat_id, context.bot, msg, from_chat_id=entity.id, message_id=msg.id)
                await status_msg.edit_text(f"✅ Batch complete!")
        except Exception as e:
            await handle_telethon_error(update, e)
    else:
        await update.message.reply_text("👋 Use the menu buttons, or send a Telegram link.")

async def handle_pdf_upload(update, context):
    if context.user_data.get('state') != 'awaiting_pdf': return
    if not update.message.document:
        await update.message.reply_text("❌ Please upload a valid PDF document."); return
    if update.message.document.mime_type != "application/pdf":
        await update.message.reply_text("❌ The file you uploaded is not a PDF."); return
    status_msg = await update.message.reply_text("⏳ Processing PDF...")
    try:
        file = await context.bot.get_file(update.message.document.file_id)
        pdf_bytes = BytesIO(); await file.download_to_memory(pdf_bytes); pdf_bytes.seek(0)
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        total_pages = len(doc)
        text_pages = []; image_pages = []
        for page_num in range(total_pages):
            page = doc.load_page(page_num)
            page_text = page.get_text("text")
            if page_text.strip(): text_pages.append(page_text)
            images = page.get_images(full=True)
            for img in images:
                base_image = doc.extract_image(img[0])
                img_bytes = BytesIO(base_image["image"]); img_bytes.seek(0)
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
        await status_msg.edit_text(f"❌ Error: {e}"); context.user_data['state'] = None

async def fetch_words(update, context, target):
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
                        word_data[word]['count'] += 1; word_data[word]['messages'].add(m.id)
        sorted_words = sorted(word_data.items(), key=lambda x: x[1]['count'], reverse=True)[:10]
        text = f"<blockquote>RIGID M (@{entity.username}) often uses this words:\n"
        for word, data in sorted_words: text += f"|{len(data['messages'])} - {data['count']} {word}\n"
        text += "</blockquote>"
        kb = [[InlineKeyboardButton("⬅️ Less Words", callback_data="words_less"), InlineKeyboardButton("More Words ➡️", callback_data="words_more")], [InlineKeyboardButton("⬅️ Back", callback_data="more")]]
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def fetch_friends(update, context, text):
    parts = text.split(" ")
    if len(parts) < 2: await update.message.reply_text("⚠️ Use: @group @user"); return
    try:
        group_entity = await telethon_client.get_entity(parts[0]); target_user = await telethon_client.get_entity(parts[1])
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
    except Exception as e: await update.message.reply_text(f"❌ Error: {e}")

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
                    text += f"1. @{h[0]} [{h[3][:10]}]\n"; seen.add(h[0])
        else: text += "No history yet.\n"
        text += "\nfirst name / last name:\n"
        if history:
            for h in history[:5]: text += f"|{h[3][:10]} -> {h[1]} {h[2]}\n"
        else: text += "No history yet.\n"
        text += "</blockquote>"
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)
    except Exception as e: await update.message.reply_text(f"❌ Error: {e}")

@telethon_client.on(events.NewMessage(incoming=True))
async def inbox_listener(event):
    if event.is_private and not event.out:
        try:
            sender = await event.get_sender()
            add_inbox_message(event.chat_id, sender.id, getattr(sender, 'first_name', 'Unknown'), getattr(sender, 'username', 'N/A'), event.raw_text if event.raw_text else "", get_media_type(event), str(event.date))
        except Exception as e:
            print(f"Inbox Error: {e}")

async def main():
    if not acquire_lock():
        print("❌ Another instance is already running. Exiting...")
        return
    init_db()
    def keep_alive():
        while True:
            self_ping()
            time.sleep(600)
    threading.Thread(target=keep_alive, daemon=True).start()
    try:
        await telethon_client.start(); print("Telethon connected!")
    except Exception as e:
        print(f"Telethon fail: {e}"); release_lock(); return
    bot_app = Application.builder().token(BOT_TOKEN).build()
    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(CommandHandler("logout", logout))
    bot_app.add_handler(CommandHandler("broadcast", broadcast_command))
    bot_app.add_handler(CommandHandler("setname", set_bot_name))
    bot_app.add_handler(CommandHandler("setdesc", set_bot_description))
    bot_app.add_handler(CommandHandler("setphoto", set_bot_photo))
    bot_app.add_handler(CommandHandler("restart", restart_command))
    bot_app.add_handler(CallbackQueryHandler(menu_callback))
    bot_app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_link))
    print("Bot running...")
    await bot_app.initialize(); await bot_app.start(); await bot_app.updater.start_polling()
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=10000), daemon=True).start()
    await asyncio.Event().wait()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    finally:
        release_lock()
