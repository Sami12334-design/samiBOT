import re
import asyncio
import os
import sys
import time
import threading
import sqlite3
import tempfile
import subprocess
import urllib.request
import urllib.parse
import shutil
import html
import base64
import json
from io import BytesIO
from flask import Flask
from PIL import Image, ImageEnhance, ImageFilter, ImageOps, ImageChops
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler, ContextTypes
from telethon import TelegramClient, events, functions, types
from telethon.sessions import StringSession
from telethon.errors import (FloodWaitError, ChannelPrivateError, UsernameNotOccupiedError, MessageIdInvalidError)
import pymupdf
import img2pdf
from pdf2docx import Converter
from pptx import Presentation
from pptx.util import Inches
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
import yt_dlp
import speech_recognition as sr
import imageio_ffmpeg
from groq import Groq
from gtts import gTTS

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
GROQ_API_KEY = os.environ.get('GROQ_API_KEY', '')
YOUTUBE_COOKIES_FILE = os.environ.get('YOUTUBE_COOKIES_FILE', '').strip()
YOUTUBE_COOKIES_B64 = os.environ.get('YOUTUBE_COOKIES_B64', '').strip()
YOUTUBE_COOKIES_RAW = os.environ.get('YOUTUBE_COOKIES', '').strip()

ADMIN_IDS = []
admin_ids_str = os.environ.get('ADMIN_IDS', '')
if admin_ids_str:
    ADMIN_IDS = [int(x.strip()) for x in admin_ids_str.split(',') if x.strip().isdigit()]

telethon_client = TelegramClient(StringSession(STRING_SESSION), API_ID, API_HASH)

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

def tool_done_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Continue", callback_data="converter"), InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
    ])

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
    try:
        app = context.application
        await app.stop()
        await app.shutdown()
    except Exception:
        pass
    os.execv(sys.executable, [sys.executable] + sys.argv)

async def _menu_callback_impl(update, context):
    query = update.callback_query
    data = query.data or ""
    # These handlers answer their own callback queries because they may perform
    # longer-running work or update the message immediately.
    if not (data.startswith("vd_") or data.startswith("edit_") or
            data.startswith("tts_voice_") or data.startswith("posts_") or
            data.startswith("story_")):
        try:
            await query.answer()
        except Exception:
            pass
    user_id = update.effective_user.id
    if not is_authenticated(user_id):
        await query.edit_message_text("🔐 Password required.")
        return

    data = query.data
    if data.startswith("vd_"):
        await handle_video_callback(update, context, data)
        return
    if data == "main_menu":
        keyboard = [[InlineKeyboardButton("📥 Inbox", callback_data="inbox"), InlineKeyboardButton("👤 Profile", callback_data="profile")], [InlineKeyboardButton("🔗 Fetch Telegram", callback_data="fetch")], [InlineKeyboardButton("➕ More Commands", callback_data="more")]]
        await query.message.reply_text("🤖 TELEGRAM ASSISTANT", reply_markup=InlineKeyboardMarkup(keyboard))
    elif data == "more":
        kb = [[InlineKeyboardButton("🔎 Search", callback_data="search"), InlineKeyboardButton("📊 Statistics", callback_data="stats")], [InlineKeyboardButton("📄 PDF Fetch", callback_data="pdf_fetch")], [InlineKeyboardButton("🔄 Converter", callback_data="converter")], [InlineKeyboardButton("🎬 Video Downloader", callback_data="video_downloader")]]
        if is_admin(user_id):
            admin_buttons = [[InlineKeyboardButton("🔔 Track", callback_data="track"), InlineKeyboardButton("🔗 Names", callback_data="names")], [InlineKeyboardButton("👥 Groups", callback_data="groups"), InlineKeyboardButton("💬 Messages", callback_data="messages")], [InlineKeyboardButton("🔎 Analysis", callback_data="analysis"), InlineKeyboardButton("📢 Channels", callback_data="channels")], [InlineKeyboardButton("👍 Reputation", callback_data="rep"), InlineKeyboardButton("👥 Friends", callback_data="friends")], [InlineKeyboardButton("🔄 Reactions", callback_data="reactions"), InlineKeyboardButton("🎁 Gifts", callback_data="gifts")], [InlineKeyboardButton("📤 Share", callback_data="share"), InlineKeyboardButton("🔵 Words Frequency", callback_data="words")], [InlineKeyboardButton("👥 Common Groups", callback_data="common")], [InlineKeyboardButton("📢 Broadcast", callback_data="broadcast")]]
            kb = admin_buttons + kb
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="main_menu")])
        await query.message.reply_text("➕ MORE COMMANDS", reply_markup=InlineKeyboardMarkup(kb))
    elif data == "converter":
        kb = [[InlineKeyboardButton("📄 PDF to Word", callback_data="pdf_to_word"), InlineKeyboardButton("🖼️ Image to Text", callback_data="image_to_text")], [InlineKeyboardButton("📄 Text to PDF", callback_data="text_to_pdf")], [InlineKeyboardButton("🖼️ Image to PDF", callback_data="image_to_pdf"), InlineKeyboardButton("🖼️ Edit Photo", callback_data="image_edit")], [InlineKeyboardButton("🗣️ Text to Voice (ENG)", callback_data="tts_en"), InlineKeyboardButton("🗣️ Text to Voice (AM)", callback_data="tts_am")], [InlineKeyboardButton("📷 Image Format", callback_data="img_fmt_menu"), InlineKeyboardButton("📚 Document Format", callback_data="doc_fmt_menu")], [InlineKeyboardButton("🎙️ Voice to Text (ENG)", callback_data="voice_en"), InlineKeyboardButton("🎙️ Voice to Text (AM)", callback_data="voice_am")], [InlineKeyboardButton("⬅️ Back", callback_data="more")]]
        await query.message.reply_text("🔄 MEDIA CONVERTER & AI TOOLS\n\nChoose an option:", reply_markup=InlineKeyboardMarkup(kb))
    elif data == "img_fmt_menu":
        kb = [[InlineKeyboardButton("PNG to JPG", callback_data="img_png_jpg"), InlineKeyboardButton("JPG to PNG", callback_data="img_jpg_png")], [InlineKeyboardButton("Image to GIF", callback_data="img_gif"), InlineKeyboardButton("⬅️ Back", callback_data="converter")]]
        await query.message.reply_text("📷 **IMAGE FORMAT CONVERTER**\n\nChoose a conversion:", reply_markup=InlineKeyboardMarkup(kb))
    elif data == "doc_fmt_menu":
        kb = [[InlineKeyboardButton("PDF to PPTX", callback_data="doc_pdf_pptx"), InlineKeyboardButton("PPTX to PDF", callback_data="doc_pptx_pdf")], [InlineKeyboardButton("⬅️ Back", callback_data="converter")]]
        await query.message.reply_text("📚 **DOCUMENT FORMAT CONVERTER**\n\nChoose a conversion:", reply_markup=InlineKeyboardMarkup(kb))
    elif data == "image_edit":
        await query.message.reply_text("🖼️ **PHOTO EDITOR**\n\nSend me a photo, and I will give you a menu with professional editing tools.")
        context.user_data['state'] = 'awaiting_edit_photo'
    elif data.startswith("edit_"):
        await handle_photo_edit_selection(update, context, data)
    elif data == "text_to_pdf":
        context.user_data[PDF_DRAFT_KEY] = []
        context.user_data['state'] = 'awaiting_text_pdf'
        await query.message.reply_text(
            "📄 TEXT TO PDF\n\nSend a text message. After every message I will ask you to enter another or finish.",
            reply_markup=text_pdf_keyboard(),
        )
    elif data == "pdftext_next":
        context.user_data['state'] = 'awaiting_text_pdf'
        await query.message.reply_text("➕ Send the next text message.")
    elif data == "pdftext_done":
        await finish_text_to_pdf(update, context)
    elif data == "pdftext_cancel":
        await cancel_text_to_pdf(update, context)
    elif data == "pdf_to_word":
        await query.message.reply_text("📄 PDF to Word\n\nPlease upload the PDF file.")
        context.user_data['state'] = 'awaiting_pdf_to_word'
    elif data == "image_to_text":
        await query.message.reply_text("🖼️ Image to Text\n\nPlease upload an image.")
        context.user_data['state'] = 'awaiting_image_to_text'
    elif data == "image_to_pdf":
        await query.message.reply_text("🖼️ Image to PDF\n\nSend images (1-10). Click Done when finished.")
        context.user_data['state'] = 'awaiting_image_to_pdf'
        context.user_data['pdf_images'] = []
    elif data == "img_pdf_done":
        await process_image_pdf(update, context)
    elif data == "tts_en":
        await query.message.reply_text("🗣️ **Text to Voice (English)**\n\nSend me the text you want to convert to speech.")
        context.user_data['state'] = 'awaiting_tts_en'
    elif data == "tts_am":
        await query.message.reply_text("🗣️ **Text to Voice (Amharic)**\n\nSend me the text you want to convert to speech.")
        context.user_data['state'] = 'awaiting_tts_am'
    elif data.startswith("tts_voice_"):
        await handle_tts_voice_selection(update, context, data)
    elif data == "img_png_jpg":
        await query.message.reply_text("📷 **PNG to JPG**\n\nSend me a PNG image.")
        context.user_data['state'] = 'awaiting_img_png_jpg'
    elif data == "img_jpg_png":
        await query.message.reply_text("📷 **JPG to PNG**\n\nSend me a JPG image.")
        context.user_data['state'] = 'awaiting_img_jpg_png'
    elif data == "img_gif":
        await query.message.reply_text("📷 **Image to GIF**\n\nSend me an image (it will be converted to a GIF).")
        context.user_data['state'] = 'awaiting_img_gif'
    elif data == "doc_pdf_pptx":
        await query.message.reply_text("📚 **PDF to PPTX**\n\nPlease upload the PDF file.")
        context.user_data['state'] = 'awaiting_doc_pdf_pptx'
    elif data == "doc_pptx_pdf":
        await query.message.reply_text("📚 **PPTX to PDF**\n\nPlease upload the PPTX file.")
        context.user_data['state'] = 'awaiting_doc_pptx_pdf'
    elif data == "voice_en":
        await query.message.reply_text("🎙️ **Voice to Text (English)**\n\nSend a clear voice message or audio file.")
        context.user_data['state'] = 'awaiting_voice_en'
    elif data == "voice_am":
        await query.message.reply_text("🎙️ **Voice to Text (Amharic)**\n\nSend a clear voice message or audio file.")
        context.user_data['state'] = 'awaiting_voice_am'
    elif data == "video_downloader":
        await query.message.reply_text("🎬 VIDEO DOWNLOADER\n\nSend me a YouTube, TikTok, Instagram, or Facebook video link.\n\nI will check the available qualities first, then let you choose 1080p/720p/480p/360p/240p or Audio when available.")
        context.user_data['state'] = 'awaiting_video_link'
    elif data == "broadcast":
        await query.message.reply_text("📢 BROADCAST\n\nUsage: /broadcast <message>")
    elif data == "profile":
        await query.message.reply_text("👤 PROFILE\n\nEnter a Telegram username or ID to generate the Profile Card:")
        context.user_data['state'] = 'profile_query'
    elif data == "fetch":
        await query.message.reply_text("🔗 Fetch Telegram\n\nSend me a link (e.g., t.me/channel/123 or t.me/channel/123-130):")
        context.user_data['state'] = 'fetch_link'
    elif data == "search":
        await query.message.reply_text("🔎 SEARCH\n\nEnter any keyword to search across your chats:\nExample: Logic mid")
        context.user_data['state'] = 'search_query'
    elif data == "pdf_fetch":
        await query.message.reply_text("📄 PDF FETCH\n\nPlease upload the PDF file directly to this chat.")
        context.user_data['state'] = 'awaiting_pdf'
    elif data.startswith("posts_"):
        page = int(data.split("_")[1])
        await handle_posts_pagination(update, context, page)
    elif data.startswith("story_"):
        if data == "story_start":
            context.user_data["story_index"] = 0
            entity = context.user_data.get("story_entity") or context.user_data.get("profile_entity")
            if not entity:
                await query.message.reply_text("❌ No profile selected.")
                return
            await query.answer("Fetching Stories…")
            try:
                if not GetPeerStoriesRequest:
                    await query.message.reply_text("❌ Telethon does not support stories in this installation.")
                    return
                response = await telethon_client(GetPeerStoriesRequest(peer=entity))
                stories = list(getattr(response, "stories", []) or [])
                if not stories:
                    await query.message.reply_text("❌ No active Stories are available.")
                    return
                context.user_data["stories_list"] = stories
                await display_story(update, context)
            except Exception as e:
                await query.message.reply_text(f"❌ Could not retrieve Stories: {e}")
        elif data == "story_next":
            context.user_data["story_index"] = context.user_data.get("story_index", 0) + 1
            await display_story(update, context)
        elif data == "story_prev":
            context.user_data["story_index"] = context.user_data.get("story_index", 0) - 1
            await display_story(update, context)

async def menu_callback(update, context):
    try:
        await _menu_callback_impl(update, context)
    except Exception as exc:
        print(f"Menu callback error: {exc}")
        try:
            q = update.callback_query
            if q:
                await q.answer()
                await q.message.reply_text("⚠️ This button could not complete, but the bot is still running. Please try again.")
        except Exception:
            pass


# --- PHOTO EDITING ---
_REMBG_SESSION = None
_REMBG_LOCK = threading.Lock()


def _get_rembg_session():
    global _REMBG_SESSION
    if _REMBG_SESSION is not None:
        return _REMBG_SESSION
    with _REMBG_LOCK:
        if _REMBG_SESSION is None:
            from rembg import new_session
            # u2netp is much lighter than u2net and is friendlier to small
            # Render/container instances. If unavailable, fall back to default.
            try:
                _REMBG_SESSION = new_session("u2netp")
            except Exception:
                _REMBG_SESSION = new_session()
    return _REMBG_SESSION


def _remove_background_sync(img):
    from rembg import remove
    rgba = img.convert("RGBA").copy()
    rgba.load()
    session = _get_rembg_session()
    try:
        return remove(rgba, session=session)
    except Exception:
        # A second attempt without the cached session protects against stale
        # model/session objects after a worker restart or model load failure.
        return remove(rgba)


def _sketch_sync(img):
    # Reliable pencil-sketch effect using Pillow only (no network/model needed).
    rgb = img.convert("RGB")
    gray = ImageOps.grayscale(rgb)
    inverted = ImageOps.invert(gray)
    blurred = inverted.filter(ImageFilter.GaussianBlur(radius=9))
    sketch = ImageChops.dodge(gray, blurred, scale=2.2)
    sketch = ImageEnhance.Contrast(sketch).enhance(1.35)
    sketch = ImageEnhance.Sharpness(sketch).enhance(1.4)
    return sketch.convert("RGB")


async def handle_photo_edit_selection(update, context, data):
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    if not context.user_data.get("edit_image"):
        await query.message.reply_text("❌ No image found. Please send a photo first.")
        return

    img = context.user_data["edit_image"].copy()

    if data == "edit_remove_bg":
        status = await query.message.reply_text("⏳ Removing background… This may take a little while on the first use.")
        try:
            out_img = await asyncio.to_thread(_remove_background_sync, img)
            out_bytes = BytesIO()
            out_img.save(out_bytes, format="PNG")
            out_bytes.seek(0)
            await query.message.reply_document(
                document=out_bytes,
                filename="background_removed.png",
                caption="✅ Background removed successfully!",
                reply_markup=tool_done_kb(),
            )
            await status.edit_text("✅ Background removal complete!")
        except Exception as e:
            await status.edit_text("❌ Background removal failed.\n\n" + html.escape(str(e)[:1500]), parse_mode=ParseMode.HTML)
        return

    if data == "edit_change_bg":
        await query.message.reply_text("🖼️ CHANGE BACKGROUND\n\nStep 1/2: Upload the background image.")
        context.user_data["state"] = "awaiting_bg_upload"
        return

    status = await query.message.reply_text("⏳ Applying edit…")
    try:
        # Work at a reasonable size so Render does not explode memory on huge photos.
        max_side = 2048
        img.thumbnail((max_side, max_side), Image.LANCZOS)
        filter_name = "Original"

        if data == "edit_orig":
            result = img.convert("RGB")
            filter_name = "Original"
        elif data == "edit_hd":
            result = ImageEnhance.Sharpness(img.convert("RGB")).enhance(2.2)
            result = ImageEnhance.Contrast(result).enhance(1.15)
            result = ImageEnhance.Color(result).enhance(1.08)
            filter_name = "HD Enhanced"
        elif data == "edit_bw":
            result = ImageOps.grayscale(img).convert("RGB")
            filter_name = "Black & White"
        elif data == "edit_sepia":
            rgb = img.convert("RGB")
            sepia_matrix = (0.393, 0.769, 0.189, 0, 0.349, 0.686, 0.168, 0, 0.272, 0.534, 0.131, 0)
            result = rgb.convert("RGB", sepia_matrix)
            filter_name = "Vintage Sepia"
        elif data == "edit_vivid":
            result = ImageEnhance.Color(img.convert("RGB")).enhance(1.5)
            result = ImageEnhance.Contrast(result).enhance(1.18)
            filter_name = "Vivid"
        elif data == "edit_sharp":
            result = img.convert("RGB").filter(ImageFilter.UnsharpMask(radius=2, percent=150, threshold=3))
            filter_name = "Sharpen"
        elif data == "edit_bright":
            result = ImageEnhance.Brightness(img.convert("RGB")).enhance(1.3)
            filter_name = "Brighten"
        elif data == "edit_dark":
            result = ImageEnhance.Brightness(img.convert("RGB")).enhance(0.7)
            filter_name = "Darken"
        elif data == "edit_blur":
            result = img.convert("RGB").filter(ImageFilter.GaussianBlur(radius=2.2))
            filter_name = "Soft Blur"
        elif data == "edit_pixel":
            rgb = img.convert("RGB")
            small_w = max(8, rgb.width // 24)
            small_h = max(8, rgb.height // 24)
            tiny = rgb.resize((small_w, small_h), Image.Resampling.BILINEAR)
            result = tiny.resize(rgb.size, Image.Resampling.NEAREST)
            filter_name = "Pixel Art"
        elif data == "edit_invert":
            result = ImageOps.invert(img.convert("RGB"))
            filter_name = "Invert"
        elif data == "edit_sketch":
            result = await asyncio.to_thread(_sketch_sync, img)
            filter_name = "Pencil Sketch"
        elif data == "edit_emboss":
            result = img.convert("RGB").filter(ImageFilter.EMBOSS)
            filter_name = "Emboss"
        elif data == "edit_poster":
            result = ImageOps.posterize(img.convert("RGB"), bits=4)
            filter_name = "Posterize"
        elif data == "edit_solar":
            result = ImageOps.solarize(img.convert("RGB"), threshold=128)
            filter_name = "Solarize"
        else:
            await status.edit_text("❌ Unknown editing operation.")
            return

        out_bytes = BytesIO()
        result.save(out_bytes, format="JPEG", quality=95, optimize=True)
        out_bytes.seek(0)
        await query.message.reply_photo(
            photo=out_bytes,
            caption=f"✅ Applied: {filter_name}",
            reply_markup=tool_done_kb(),
        )
        await status.edit_text(f"✅ {filter_name} complete!")
    except Exception as e:
        await status.edit_text("❌ Edit failed.\n\n" + html.escape(str(e)[:1500]), parse_mode=ParseMode.HTML)


async def handle_edit_photo(update, context):
    if context.user_data.get("state") != "awaiting_edit_photo":
        return
    if not update.message.photo:
        await update.message.reply_text("❌ Please upload an image.")
        return
    status_msg = await update.message.reply_text("⏳ Loading image…")
    try:
        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        img_bytes = BytesIO()
        await file.download_to_memory(img_bytes)
        img_bytes.seek(0)
        img = Image.open(img_bytes).convert("RGBA")
        img.load()
        context.user_data["edit_image"] = img.copy()
        kb = [
            [InlineKeyboardButton("🖼️ Original", callback_data="edit_orig"), InlineKeyboardButton("✨ HD", callback_data="edit_hd"), InlineKeyboardButton("🎨 Vivid", callback_data="edit_vivid")],
            [InlineKeyboardButton("⬛ B&W", callback_data="edit_bw"), InlineKeyboardButton("🟤 Sepia", callback_data="edit_sepia"), InlineKeyboardButton("🔪 Sharpen", callback_data="edit_sharp")],
            [InlineKeyboardButton("☀️ Brighten", callback_data="edit_bright"), InlineKeyboardButton("🌙 Darken", callback_data="edit_dark"), InlineKeyboardButton("🌫️ Blur", callback_data="edit_blur")],
            [InlineKeyboardButton("🟥 Pixel", callback_data="edit_pixel"), InlineKeyboardButton("🔄 Invert", callback_data="edit_invert"), InlineKeyboardButton("✏️ Sketch", callback_data="edit_sketch")],
            [InlineKeyboardButton("🧊 Emboss", callback_data="edit_emboss"), InlineKeyboardButton("🎞️ Poster", callback_data="edit_poster"), InlineKeyboardButton("🔥 Solarize", callback_data="edit_solar")],
            [InlineKeyboardButton("🖼️ Remove BG", callback_data="edit_remove_bg"), InlineKeyboardButton("🖼️ Change BG", callback_data="edit_change_bg")],
        ]
        await status_msg.edit_text("✅ Image loaded!\n\nChoose an editing feature:", reply_markup=InlineKeyboardMarkup(kb))
        context.user_data["state"] = None
    except Exception as e:
        await status_msg.edit_text("❌ Processing failed.\n\n" + html.escape(str(e)[:1500]), parse_mode=ParseMode.HTML)
        context.user_data["state"] = None


async def handle_bg_upload(update, context):
    if context.user_data.get('state') != 'awaiting_bg_upload': return
    if not update.message.photo:
        await update.message.reply_text("❌ Please upload the **background image**.")
        return
    try:
        photo = update.message.photo[-1]; file = await context.bot.get_file(photo.file_id)
        img_bytes = BytesIO(); await file.download_to_memory(img_bytes); img_bytes.seek(0)
        context.user_data['bg_image'] = Image.open(img_bytes)
        await update.message.reply_text("✅ Background image received!\n\nStep 2/2: Please upload the **front image**.")
        context.user_data['state'] = 'awaiting_front_upload'
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def handle_front_upload(update, context):
    if context.user_data.get('state') != 'awaiting_front_upload': return
    if not update.message.photo:
        await update.message.reply_text("❌ Please upload the **front image**.")
        return
    status_msg = await update.message.reply_text("⏳ Changing background... (May take up to 30 seconds)")
    try:
        photo = update.message.photo[-1]; file = await context.bot.get_file(photo.file_id)
        img_bytes = BytesIO(); await file.download_to_memory(img_bytes); img_bytes.seek(0)
        front_img = Image.open(img_bytes)
        bg_img = context.user_data['bg_image']
        async def run_bg_change():
            def do_work():
                from rembg import remove, new_session
                session = new_session("u2netp")
                front_cutout = remove(front_img, session=session)
                bg_img_resized = bg_img.resize(front_cutout.size)
                bg_img_resized.paste(front_cutout, (0, 0), front_cutout)
                return bg_img_resized
            return await asyncio.to_thread(do_work)
        new_img = await run_bg_change()
        out_bytes = BytesIO(); new_img.save(out_bytes, format='JPEG', quality=95); out_bytes.seek(0)
        await update.message.reply_photo(photo=out_bytes, caption="✅ Background changed!", reply_markup=tool_done_kb())
        await status_msg.edit_text("✅ Background change complete!")
        context.user_data.pop('bg_image', None); context.user_data['state'] = None
    except Exception as e:
        await status_msg.edit_text(f"❌ Background change failed: {e}")
        context.user_data.pop('bg_image', None); context.user_data['state'] = None

# --- TEXT TO VOICE ---
async def handle_tts(update, context, lang):
    if not update.message.text:
        await update.message.reply_text("❌ Please send the text you want to convert.")
        return
    context.user_data['tts_text'] = update.message.text
    context.user_data['tts_lang'] = lang
    kb = [[InlineKeyboardButton("🧑 Male", callback_data=f"tts_voice_{lang}_male"), InlineKeyboardButton("👩 Female", callback_data=f"tts_voice_{lang}_female")], [InlineKeyboardButton("👴 Old", callback_data=f"tts_voice_{lang}_old"), InlineKeyboardButton("👶 Child", callback_data=f"tts_voice_{lang}_child")]]
    await update.message.reply_text("🎙️ **Choose Voice Type:**", reply_markup=InlineKeyboardMarkup(kb))
    context.user_data['state'] = None

async def handle_tts_voice_selection(update, context, data):
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    parts = data.split("_")
    lang = parts[2] if len(parts) > 2 else "en"
    voice_type = parts[3] if len(parts) > 3 else "female"
    text = (context.user_data.get('tts_text') or '').strip()
    if not text:
        await query.message.reply_text("❌ No text found. Please send the text again.")
        return

    # edge-tts currently returns intermittent 403 websocket errors on some
    # datacenter IPs. Use Google's free gTTS HTTP endpoint instead.
    lang_code = 'am' if lang == 'am' else 'en'
    status_msg = await query.message.reply_text("🗣️ Generating voice…")
    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(prefix='tts_', suffix='.mp3')
        os.close(fd)

        def generate():
            tts = gTTS(text=text, lang=lang_code, slow=False)
            tts.save(tmp_path)

        await asyncio.to_thread(generate)
        with open(tmp_path, 'rb') as audio:
            await query.message.reply_audio(
                audio=audio,
                filename=f"voice_{lang_code}.mp3",
                title=f"Voice ({voice_type})",
                caption=f"✅ Text converted to speech ({'Amharic' if lang_code == 'am' else 'English'}).",
                reply_markup=tool_done_kb(),
            )
        await status_msg.edit_text("✅ Voice generated successfully!")
        context.user_data.pop('tts_text', None)
        context.user_data.pop('tts_lang', None)
    except Exception as e:
        await status_msg.edit_text(
            "❌ TTS failed. Google TTS could not be reached.\n\n" + html.escape(str(e)[:1200]),
            parse_mode=ParseMode.HTML,
        )
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


# --- TEXT TO PDF ---
PDF_DRAFT_KEY = 'text_pdf_items'

def text_pdf_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Enter Next Text", callback_data="pdftext_next"),
         InlineKeyboardButton("✅ Done", callback_data="pdftext_done")],
        [InlineKeyboardButton("❌ Cancel", callback_data="pdftext_cancel")],
    ])


async def start_text_to_pdf(update, context):
    context.user_data[PDF_DRAFT_KEY] = []
    context.user_data['state'] = 'awaiting_text_pdf'
    await update.message.reply_text(
        "📄 TEXT TO PDF\n\n"
        "Send your first text. After each text I will ask whether to enter another one or finish.\n\n"
        "You can send the texts as separate messages; they will all be collected for the same PDF.",
    )


async def handle_text_pdf_input(update, context):
    if context.user_data.get('state') != 'awaiting_text_pdf':
        return
    text = (update.message.text or '').strip()
    if not text:
        await update.message.reply_text("❌ Please send some text.")
        return
    items = context.user_data.setdefault(PDF_DRAFT_KEY, [])
    # Preserve each Telegram message as its own paragraph. This naturally
    # supports two or more messages arriving quickly from the same chat.
    items.append(text)
    count = len(items)
    await update.message.reply_text(
        f"✅ Text {count} added.\n\n"
        "Choose **Enter Next Text** to add another message, or **Done** to create the PDF.",
        reply_markup=text_pdf_keyboard(),
        parse_mode=ParseMode.MARKDOWN,
    )


async def finish_text_to_pdf(update, context):
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    items = context.user_data.get(PDF_DRAFT_KEY, [])
    if not items:
        await query.edit_message_text("❌ No text has been added yet.")
        return
    status = await query.message.reply_text("⏳ Creating PDF…")
    path = None
    try:
        fd, path = tempfile.mkstemp(prefix='text_pdf_', suffix='.pdf')
        os.close(fd)
        styles = getSampleStyleSheet()
        body = ParagraphStyle(
            'BodyClean', parent=styles['BodyText'], fontName='Helvetica',
            fontSize=11, leading=16, spaceAfter=12
        )
        doc = SimpleDocTemplate(
            path, pagesize=letter, rightMargin=54, leftMargin=54,
            topMargin=54, bottomMargin=54, title='Text to PDF'
        )
        story = []
        for idx, item in enumerate(items, 1):
            escaped = html.escape(item).replace('\n', '<br/>')
            story.append(Paragraph(escaped, body))
            if idx < len(items):
                story.append(Spacer(1, 4))
        doc.build(story)
        with open(path, 'rb') as f:
            await query.message.reply_document(
                document=f, filename='text_document.pdf',
                caption=f"✅ PDF created from {len(items)} text message(s).",
                reply_markup=tool_done_kb(),
            )
        await status.edit_text("✅ Text to PDF complete!")
        context.user_data.pop(PDF_DRAFT_KEY, None)
        context.user_data['state'] = None
    except Exception as e:
        await status.edit_text("❌ Text to PDF failed.\n\n" + html.escape(str(e)[:1200]), parse_mode=ParseMode.HTML)
    finally:
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass


async def cancel_text_to_pdf(update, context):
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    context.user_data.pop(PDF_DRAFT_KEY, None)
    context.user_data['state'] = None
    await query.edit_message_text("❌ Text to PDF cancelled.")


# --- IMAGE FORMAT CONVERSION ---
async def handle_image_convert(update, context, fmt):
    if not update.message.photo:
        await update.message.reply_text("❌ Please upload an image.")
        return
    status_msg = await update.message.reply_text("⏳ Converting image...")
    try:
        photo = update.message.photo[-1]; file = await context.bot.get_file(photo.file_id)
        img_bytes = BytesIO(); await file.download_to_memory(img_bytes); img_bytes.seek(0)
        img = Image.open(img_bytes)
        out_bytes = BytesIO()
        if fmt == 'png_jpg':
            if img.mode in ('RGBA', 'LA', 'P'):
                img = img.convert('RGB')
            img.save(out_bytes, format='JPEG', quality=95)
            filename = "converted.jpg"
        elif fmt == 'jpg_png':
            img.save(out_bytes, format='PNG')
            filename = "converted.png"
        elif fmt == 'gif':
            img.save(out_bytes, format='GIF')
            filename = "converted.gif"
        out_bytes.seek(0)
        await update.message.reply_document(document=out_bytes, filename=filename, caption=f"✅ Converted to {fmt.replace('_', '.').upper()}!", reply_markup=tool_done_kb())
        await status_msg.edit_text("✅ Image conversion complete!")
        context.user_data['state'] = None
    except Exception as e:
        await status_msg.edit_text(f"❌ Conversion failed: {e}")
        context.user_data['state'] = None

# --- DOCUMENT CONVERSION ---
async def handle_pdf_to_pptx(update, context):
    if context.user_data.get('state') != 'awaiting_doc_pdf_pptx': return
    if not update.message.document:
        await update.message.reply_text("❌ Please upload a PDF file.")
        return
    if update.message.document.mime_type != "application/pdf":
        await update.message.reply_text("❌ Please upload a PDF file.")
        return
    status_msg = await update.message.reply_text("⏳ Converting PDF to PPTX...")
    try:
        file = await context.bot.get_file(update.message.document.file_id)
        pdf_bytes = BytesIO(); await file.download_to_memory(pdf_bytes); pdf_bytes.seek(0)
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        prs = Presentation()
        blank_slide_layout = prs.slide_layouts[6]
        for page in doc:
            slide = prs.slides.add_slide(blank_slide_layout)
            pix = page.get_pixmap(matrix=pymupdf.Matrix(2, 2))
            img_bytes = BytesIO(pix.tobytes("png"))
            slide.shapes.add_picture(img_bytes, Inches(0), Inches(0), width=Inches(10), height=Inches(5.63))
        doc.close()
        pptx_bytes = BytesIO()
        prs.save(pptx_bytes)
        pptx_bytes.seek(0)
        await update.message.reply_document(document=pptx_bytes, filename="converted.pptx", caption="✅ PDF converted to PPTX!", reply_markup=tool_done_kb())
        await status_msg.edit_text("✅ PDF to PPTX complete!")
        context.user_data['state'] = None
    except Exception as e:
        await status_msg.edit_text(f"❌ Conversion failed: {e}")
        context.user_data['state'] = None

async def handle_pptx_to_pdf(update, context):
    if context.user_data.get('state') != 'awaiting_doc_pptx_pdf': return
    if not update.message.document:
        await update.message.reply_text("❌ Please upload a PPTX file.")
        return
    status_msg = await update.message.reply_text("⏳ Converting PPTX to PDF...")
    try:
        file = await context.bot.get_file(update.message.document.file_id)
        pptx_bytes = BytesIO(); await file.download_to_memory(pptx_bytes); pptx_bytes.seek(0)
        prs = Presentation(pptx_bytes)
        pdf_bytes = BytesIO()
        c = canvas.Canvas(pdf_bytes, pagesize=letter)
        width, height = letter
        for slide in prs.slides:
            c.setFont("Helvetica", 12)
            y = height - 40
            for shape in slide.shapes:
                if shape.has_text_frame:
                    for paragraph in shape.text_frame.paragraphs:
                        text = paragraph.text
                        if text:
                            c.drawString(40, y, text)
                            y -= 20
            c.showPage()
        c.save()
        pdf_bytes.seek(0)
        await update.message.reply_document(document=pdf_bytes, filename="converted.pdf", caption="✅ PPTX converted to PDF!", reply_markup=tool_done_kb())
        await status_msg.edit_text("✅ PPTX to PDF complete!")
        context.user_data['state'] = None
    except Exception as e:
        await status_msg.edit_text(f"❌ Conversion failed: {e}")
        context.user_data['state'] = None

# --- VOICE TO TEXT ---
async def handle_voice_to_text(update, context, language):
    if not update.message.voice and not update.message.audio:
        await update.message.reply_text("❌ Please send a voice message OR an audio file.")
        return
    status_msg = await update.message.reply_text("⏳ Transcribing voice/audio...")
    try:
        if update.message.voice: file_id = update.message.voice.file_id
        else: file_id = update.message.audio.file_id
        file = await context.bot.get_file(file_id)
        voice_bytes = BytesIO(); await file.download_to_memory(voice_bytes); voice_bytes.seek(0)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".ogg") as tmp_ogg:
            tmp_ogg.write(voice_bytes.read()); tmp_ogg_path = tmp_ogg.name
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp_wav:
            tmp_wav_path = tmp_wav.name
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        cmd = [ffmpeg_exe, "-i", tmp_ogg_path, "-ar", "16000", "-ac", "1", tmp_wav_path, "-y"]
        subprocess.run(cmd, check=True, capture_output=True)
        if GROQ_API_KEY:
            try:
                client = Groq(api_key=GROQ_API_KEY)
                with open(tmp_wav_path, "rb") as f:
                    transcription = client.audio.transcriptions.create(file=(tmp_wav_path, f), model="whisper-large-v3", language=language.split('-')[0], response_format="text")
                await update.message.reply_text(f"📝 **Transcribed Text (Groq):**\n\n{transcription}", reply_markup=tool_done_kb())
                await status_msg.edit_text("✅ Transcription complete!")
                os.unlink(tmp_ogg_path); os.unlink(tmp_wav_path)
                context.user_data['state'] = None
                return
            except Exception: pass
        recognizer = sr.Recognizer()
        with sr.AudioFile(tmp_wav_path) as source:
            audio_data = recognizer.record(source)
        try:
            text = recognizer.recognize_google(audio_data, language=language)
            await update.message.reply_text(f"📝 **Transcribed Text (Google):**\n\n{text}", reply_markup=tool_done_kb())
            await status_msg.edit_text("✅ Transcription complete!")
        except sr.UnknownValueError:
            await status_msg.edit_text("❌ Could not understand the audio.")
        except sr.RequestError:
            await status_msg.edit_text("❌ Speech recognition service is unavailable.")
        os.unlink(tmp_ogg_path); os.unlink(tmp_wav_path)
        context.user_data['state'] = None
    except Exception as e:
        await status_msg.edit_text(f"❌ Transcription failed: {e}")
        context.user_data['state'] = None

# --- VIDEO DOWNLOADER ---
# Build: free-youtube-cookie-2026-09-02-v10
# YouTube: direct yt-dlp only. No paid API, no Piped, no Cobalt.
# For server-side YouTube bot checks, use a legitimate exported YouTube cookie
# file via YOUTUBE_COOKIES_FILE or YOUTUBE_COOKIES_B64.
VIDEO_MAX_UPLOAD = 50 * 1024 * 1024
VIDEO_QUALITIES = [1080, 720, 480, 360, 240]
TIKWM_API = "https://www.tikwm.com/api/"
BUILD_ID = "free-youtube-cookie-2026-09-02-v10"


def video_platform(url: str) -> str:
    host = urllib.parse.urlparse(url).netloc.lower().split(":", 1)[0]
    host = host[4:] if host.startswith("www.") else host
    if host in {"youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"} or host.endswith(".youtube.com"):
        return "youtube"
    if "tiktok.com" in host:
        return "tiktok"
    if "instagram.com" in host:
        return "instagram"
    if host == "fb.watch" or "facebook.com" in host:
        return "facebook"
    return "unknown"


def normalize_public_url(url: str) -> str:
    return (url or "").strip()


def _youtube_cookie_file():
    # File path has highest priority. Raw/base64 environment values are useful
    # on Render where committing cookies to GitHub would be unsafe.
    if YOUTUBE_COOKIES_FILE and os.path.isfile(YOUTUBE_COOKIES_FILE):
        return YOUTUBE_COOKIES_FILE
    if YOUTUBE_COOKIES_B64 or YOUTUBE_COOKIES_RAW:
        cache_dir = os.path.join(tempfile.gettempdir(), 'youtube_auth')
        os.makedirs(cache_dir, exist_ok=True)
        path = os.path.join(cache_dir, 'cookies.txt')
        try:
            if YOUTUBE_COOKIES_B64:
                raw = base64.b64decode(YOUTUBE_COOKIES_B64).decode('utf-8')
            else:
                raw = YOUTUBE_COOKIES_RAW
            # Basic validation: cookie files should be Netscape/Mozilla format.
            if '# Netscape HTTP Cookie File' not in raw and '# HTTP Cookie File' not in raw:
                raise ValueError('Cookie data is not in Netscape cookies.txt format')
            with open(path, 'w', encoding='utf-8', newline='\n') as f:
                f.write(raw)
            os.chmod(path, 0o600)
            return path
        except Exception as exc:
            print(f"YouTube cookies could not be prepared: {exc}")
    return None


def ytdlp_base_options(*, youtube_client=None):
    options = {
        "quiet": True,
        "no_warnings": False,
        "noplaylist": True,
        "socket_timeout": 30,
        "retries": 5,
        "fragment_retries": 5,
        "file_access_retries": 5,
        "concurrent_fragment_downloads": 2,
        "ffmpeg_location": imageio_ffmpeg.get_ffmpeg_exe(),
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/139.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.8",
        },
        "remote_components": {"ejs:github"},
    }
    cookie_file = _youtube_cookie_file()
    if cookie_file:
        options['cookiefile'] = cookie_file
    if youtube_client:
        options['extractor_args'] = {'youtube': {'player_client': [youtube_client]}}
    return options


def _find_media_file(output_dir: str, preferred=None):
    if preferred and os.path.isfile(preferred):
        return preferred
    allowed = {'.mp4', '.mkv', '.webm', '.mov', '.m4a', '.mp3', '.aac', '.opus'}
    files = []
    for name in os.listdir(output_dir):
        path = os.path.join(output_dir, name)
        if os.path.isfile(path) and os.path.splitext(name)[1].lower() in allowed:
            files.append(path)
    return max(files, key=os.path.getmtime) if files else None


def _clean_dir(output_dir):
    for name in os.listdir(output_dir):
        path = os.path.join(output_dir, name)
        if os.path.isfile(path):
            try: os.unlink(path)
            except OSError: pass


def ytdlp_download(url, output_dir, *, height=None, audio=False, title_hint='video', youtube_client=None):
    platform = video_platform(url)
    opts = ytdlp_base_options(youtube_client=youtube_client if platform == 'youtube' else None)
    if audio:
        opts['format'] = 'bestaudio/best'
        opts['postprocessors'] = [{
            'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'
        }]
    else:
        h = int(height) if height else 720
        # Prefer H.264/AAC MP4 when available, then fall back to the best
        # source under the requested height. This avoids WebM-only responses.
        opts['format'] = (
            f"bestvideo[height<={h}][ext=mp4]+bestaudio[ext=m4a]/"
            f"best[height<={h}][ext=mp4]/best[height<={h}]/best"
        )
        opts['merge_output_format'] = 'mp4'
    opts['outtmpl'] = os.path.join(output_dir, '%(id)s.%(ext)s')
    opts['overwrites'] = True
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        prepared = ydl.prepare_filename(info)
        base, _ = os.path.splitext(prepared)
        expected = base + '.mp3' if audio else base + '.mp4'
        path = _find_media_file(output_dir, expected)
        if not path:
            raise FileNotFoundError(f'Downloaded file was not created: {title_hint}')
        return {'path': path, 'title': info.get('title') or title_hint, 'info': info, 'source': 'ytdlp'}


def youtube_extract_info(url):
    # Try free clients in a controlled order. If the server asks for login,
    # all clients will normally show the same error; then the cookie setup is
    # the correct next step instead of cycling paid/public proxy services.
    clients = ['android_vr', 'web_safari', 'web_embedded', 'default']
    errors = []
    for client in clients:
        try:
            with yt_dlp.YoutubeDL(ytdlp_base_options(youtube_client=None if client == 'default' else client)) as ydl:
                return ydl.extract_info(url, download=False), client
        except Exception as exc:
            errors.append(f'{client}: {exc}')
    raise RuntimeError('YouTube extraction failed. ' + ' | '.join(errors[-4:]))


def youtube_direct_download(url, output_dir, *, height=None, audio=False, title_hint='YouTube video'):
    clients = ['android_vr', 'web_safari', 'web_embedded', 'default']
    errors = []
    for client in clients:
        try:
            result = ytdlp_download(
                url, output_dir, height=height, audio=audio, title_hint=title_hint,
                youtube_client=None if client == 'default' else client,
            )
            result['youtube_client'] = client
            return result
        except Exception as exc:
            errors.append(f'{client}: {exc}')
            _clean_dir(output_dir)
    cookie = _youtube_cookie_file()
    hint = (
        ' Add YOUTUBE_COOKIES_B64 (Mozilla/Netscape cookies.txt from your own signed-in YouTube browser) to Render.'
        if not cookie else ''
    )
    raise RuntimeError('YouTube download failed. ' + ' | '.join(errors) + hint)


def tikwm_get_data(url):
    payload = urllib.parse.urlencode({"url": url, "hd": 1}).encode()
    req = urllib.request.Request(TIKWM_API, data=payload, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json,*/*"})
    with urllib.request.urlopen(req, timeout=30) as response:
        import json
        data = json.load(response)
    if int(data.get("code", -1)) != 0 or not data.get("data"):
        raise RuntimeError(data.get("msg") or "TikTok fallback service returned no data")
    return data["data"]


def tikwm_download(url, output_dir, *, quality="hd", audio=False, data=None):
    data = data or tikwm_get_data(url)
    title = data.get("title") or data.get("desc") or "TikTok video"
    if audio:
        media_url = data.get("music")
        if not media_url:
            raise RuntimeError("TikTok audio URL was not returned")
        path = os.path.join(output_dir, "audio.mp3")
        download_url_to_file(media_url, path)
        return {"path": path, "title": title, "source": "tikwm"}
    media_url = data.get("hdplay") if quality == "hd" else None
    media_url = media_url or data.get("play") or data.get("wmplay")
    if not media_url:
        raise RuntimeError("TikTok video URL was not returned")
    path = os.path.join(output_dir, "video.mp4")
    download_url_to_file(media_url, path)
    return {"path": path, "title": title, "source": "tikwm"}


def _quality_list_from_info(info):
    heights = sorted({
        int(f["height"]) for f in (info.get("formats") or [])
        if f.get("vcodec") not in (None, "none") and f.get("height")
    }, reverse=True)
    return [q for q in VIDEO_QUALITIES if any(h >= q for h in heights)]


def _has_audio(info):
    return any(f.get("acodec") not in (None, "none") for f in (info.get("formats") or []))


def extract_download_options(url: str):
    normalized = normalize_public_url(url)
    platform = video_platform(normalized)
    if platform == "unknown":
        raise RuntimeError("Unsupported URL. Use YouTube, TikTok, Instagram, or Facebook.")

    if platform == "youtube":
        info, client = youtube_extract_info(normalized)
        qualities = _quality_list_from_info(info)
        return {
            "source": "ytdlp",
            "platform": platform,
            "url": normalized,
            "title": info.get("title") or "YouTube video",
            "qualities": qualities or [360],
            "audio": _has_audio(info) or True,
            "youtube_client": client,
        }

    try:
        with yt_dlp.YoutubeDL(ytdlp_base_options()) as ydl:
            info = ydl.extract_info(normalized, download=False)
        qualities = _quality_list_from_info(info)
        return {
            "source": "ytdlp",
            "platform": platform,
            "url": normalized,
            "title": info.get("title") or f"{platform.title()} video",
            "qualities": qualities or [720],
            "audio": True,
        }
    except Exception as first_error:
        if platform == "tiktok":
            data = tikwm_get_data(normalized)
            choices = []
            if data.get("hdplay"):
                choices.append("hd")
            if data.get("play") or data.get("wmplay"):
                choices.append("sd")
            if not choices:
                raise RuntimeError(f"TikTok extraction failed: {first_error}")
            return {
                "source": "tikwm",
                "platform": platform,
                "url": normalized,
                "title": data.get("title") or data.get("desc") or "TikTok video",
                "qualities": choices,
                "audio": bool(data.get("music")),
                "tikwm": data,
            }
        raise RuntimeError(f"Extraction failed: {first_error}")


def format_bytes(value):
    value = float(value)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}"
        value /= 1024


def video_quality_keyboard(job):
    buttons = []
    for q in job.get("qualities", []):
        label = f"🎥 {q}p" if isinstance(q, int) else f"🎥 {str(q).upper()}"
        buttons.append(InlineKeyboardButton(label, callback_data=f"vd_q_{q}"))
    rows = [buttons[i:i+3] for i in range(0, len(buttons), 3)] if buttons else []
    if job.get("audio"):
        rows.append([InlineKeyboardButton("🎵 Audio / MP3", callback_data="vd_q_audio")])
    rows.append([InlineKeyboardButton("❌ Cancel", callback_data="vd_cancel")])
    return InlineKeyboardMarkup(rows)


async def handle_video_download(update, context):
    if context.user_data.get("state") != "awaiting_video_link":
        return
    raw_url = (update.message.text or "").strip()
    if not re.match(r"^https?://", raw_url, re.I):
        await update.message.reply_text("❌ Please send a valid YouTube, TikTok, Instagram, or Facebook URL.")
        return

    status_msg = await update.message.reply_text("🔎 Checking the video…")
    try:
        job = await asyncio.to_thread(extract_download_options, raw_url)
        context.user_data["video_job"] = job
        context.user_data["state"] = None
        await status_msg.edit_text(
            f"🎬 <b>{html.escape(job['platform'].title())} Downloader</b>\n\n"
            f"<b>{html.escape((job.get('title') or 'Video')[:160])}</b>\n\n"
            f"Choose the quality:",
            reply_markup=video_quality_keyboard(job),
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        context.user_data["state"] = None
        await status_msg.edit_text(
            "❌ Could not prepare download.\n\n" + html.escape(str(e)[:3500]),
            parse_mode=ParseMode.HTML,
        )


async def handle_video_callback(update, context, data):
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass

    if data == "vd_cancel":
        context.user_data.pop("video_job", None)
        await query.edit_message_text("❌ Video download cancelled.")
        return

    job = context.user_data.get("video_job")
    if not job:
        await query.edit_message_text("❌ Download session expired. Please send the video link again.")
        return

    selected = data.removeprefix("vd_q_")
    output_dir = tempfile.mkdtemp(prefix="tg_video_")
    status_msg = await query.message.reply_text("⏳ Starting download…")

    try:
        await query.edit_message_reply_markup(reply_markup=None)
        audio = selected == "audio"
        requested_quality = None if audio else int(selected)

        def do_download():
            platform = job["platform"]
            source = job["source"]
            if platform == "youtube":
                return youtube_direct_download(
                    job["url"], output_dir, height=requested_quality, audio=audio,
                    title_hint=job.get("title", "YouTube video"),
                )
            if source == "tikwm":
                q = selected if selected in {"hd", "sd"} else "sd"
                return tikwm_download(job["url"], output_dir, quality=q, audio=audio, data=job.get("tikwm"))
            return ytdlp_download(
                job["url"],
                output_dir,
                audio=audio,
                height=requested_quality,
                title_hint=job.get("title", "video"),
            )

        result = await asyncio.to_thread(do_download)
        filepath = result["path"]
        file_size = os.path.getsize(filepath)

        if file_size > VIDEO_MAX_UPLOAD and not audio and requested_quality:
            lower = [q for q in VIDEO_QUALITIES if q < requested_quality]
            for q in lower:
                _clean_dir(output_dir)
                try:
                    if job["platform"] == "youtube":
                        candidate = await asyncio.to_thread(
                            youtube_direct_download, job["url"], output_dir,
                            height=q, audio=False, title_hint=job.get("title", "video")
                        )
                    elif job["source"] == "tikwm":
                        candidate = await asyncio.to_thread(
                            tikwm_download, job["url"], output_dir, quality=selected,
                            audio=False, data=job.get("tikwm")
                        )
                    else:
                        candidate = await asyncio.to_thread(
                            ytdlp_download, job["url"], output_dir, audio=False,
                            height=q, title_hint=job.get("title", "video")
                        )
                    cpath = candidate["path"]
                    csize = os.path.getsize(cpath)
                    if csize <= VIDEO_MAX_UPLOAD:
                        result, filepath, file_size, requested_quality = candidate, cpath, csize, q
                        break
                except Exception:
                    continue

        if file_size > VIDEO_MAX_UPLOAD:
            raise RuntimeError(
                f"Downloaded file is {format_bytes(file_size)} and exceeds Telegram's 50 MB bot upload limit."
            )

        caption = f"✅ {job.get('title', 'Video')[:900]}"
        with open(filepath, "rb") as media:
            if audio:
                await query.message.reply_audio(
                    audio=media,
                    filename=os.path.basename(filepath),
                    caption=caption,
                    reply_markup=tool_done_kb(),
                )
            else:
                await query.message.reply_video(
                    video=media,
                    caption=caption,
                    supports_streaming=True,
                    reply_markup=tool_done_kb(),
                )
        await status_msg.edit_text("✅ Download completed successfully!")
    except Exception as e:
        await status_msg.edit_text(
            "❌ Download failed.\n\n" + html.escape(str(e)[:3500]),
            parse_mode=ParseMode.HTML,
        )
    finally:
        context.user_data.pop("video_job", None)
        shutil.rmtree(output_dir, ignore_errors=True)


# --- PDF, WORD, IMAGE COLLECT ---
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
            await update.message.reply_document(document=docx_file, filename="converted.docx", reply_markup=tool_done_kb())
        os.unlink(tmp_pdf_path); os.unlink(tmp_docx_path)
        await status_msg.edit_text("✅ PDF converted to Word successfully!")
        context.user_data['state'] = None
    except Exception as e:
        await status_msg.edit_text(f"❌ Conversion failed: {e}"); context.user_data['state'] = None

async def handle_image_to_text(update, context):
    """OCR using Groq vision so the bot stays compatible with Render Python 3.14.
    This removes the incompatible rapidocr-onnxruntime dependency.
    """
    if context.user_data.get('state') != 'awaiting_image_to_text':
        return
    if not update.message.photo:
        await update.message.reply_text("❌ Please upload an image.")
        return

    status_msg = await update.message.reply_text("⏳ Extracting text from image with AI OCR...")
    if not GROQ_API_KEY:
        await status_msg.edit_text(
            "❌ Image-to-Text is not configured. Set GROQ_API_KEY in Render Environment Variables."
        )
        context.user_data['state'] = None
        return

    tmp_path = None
    try:
        photo = update.message.photo[-1]
        tg_file = await context.bot.get_file(photo.file_id)
        img_bytes = BytesIO()
        await tg_file.download_to_memory(img_bytes)
        img_bytes.seek(0)

        # Groq accepts base64 data URLs for local images. Keep the payload
        # comfortably below the documented 20 MB image limit.
        img = Image.open(img_bytes).convert('RGB')
        max_side = 2400
        img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
        encoded = BytesIO()
        img.save(encoded, format='JPEG', quality=88, optimize=True)
        import base64
        data_url = 'data:image/jpeg;base64,' + base64.b64encode(encoded.getvalue()).decode('ascii')

        client = Groq(api_key=GROQ_API_KEY)
        completion = await asyncio.to_thread(
            lambda: client.chat.completions.create(
                model=os.environ.get('GROQ_VISION_MODEL', 'qwen/qwen3.6-27b'),
                messages=[
                    {
                        'role': 'user',
                        'content': [
                            {
                                'type': 'text',
                                'text': (
                                    'Transcribe all readable text in this image exactly. '
                                    'Preserve line breaks as much as possible. Do not summarize. '
                                    'If there is no readable text, return NO_TEXT.'
                                ),
                            },
                            {
                                'type': 'image_url',
                                'image_url': {'url': data_url},
                            },
                        ],
                    }
                ],
                temperature=0,
                max_completion_tokens=4096,
            )
        )

        extracted_text = (completion.choices[0].message.content or '').strip()
        if not extracted_text or extracted_text == 'NO_TEXT':
            await status_msg.edit_text("❌ No readable text found in the image.")
        else:
            # Telegram messages have a finite size, so split long OCR output.
            await status_msg.edit_text("✅ Text extraction complete!")
            for i in range(0, len(extracted_text), 3800):
                await update.message.reply_text(
                    f"📝 **Extracted Text:**\n\n{extracted_text[i:i+3800]}",
                    reply_markup=tool_done_kb() if i + 3800 >= len(extracted_text) else None,
                )
        context.user_data['state'] = None
    except Exception as e:
        await status_msg.edit_text(f"❌ OCR failed: {str(e)[:1200]}")
        context.user_data['state'] = None
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

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
        await update.message.reply_text(f"✅ Image {count}/10 added.\nSend another image, or click Done.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Done", callback_data="img_pdf_done")]]))

async def process_image_pdf(update, context):
    query = update.callback_query
    if query: await query.answer()
    if not context.user_data.get('pdf_images'):
        if query: await query.edit_message_text("❌ No images received.")
        else: await update.message.reply_text("❌ No images received.")
        context.user_data['state'] = None; return
    try:
        images = context.user_data['pdf_images']
        pdf_bytes = img2pdf.convert([img.getvalue() for img in images])
        chat_id = query.message.chat_id if query else update.effective_chat.id
        await context.bot.send_document(chat_id=chat_id, document=BytesIO(pdf_bytes), filename="images.pdf", reply_markup=tool_done_kb())
        if query: await query.edit_message_text("✅ Images converted to PDF!")
        else: await update.message.reply_text("✅ Images converted to PDF!")
        context.user_data['state'] = None; context.user_data['pdf_images'] = []
    except Exception as e:
        error_msg = f"❌ Conversion failed: {e}"
        if query: await query.edit_message_text(error_msg)
        else: await update.message.reply_text(error_msg)
        context.user_data['state'] = None; context.user_data['pdf_images'] = []

# --- OTHER FEATURES (Profile, Search, etc.) ---
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

async def display_story(update, context):
    query = update.callback_query
    await query.answer()
    stories = context.user_data.get("stories_list", [])
    index = context.user_data.get("story_index", 0)
    if not stories:
        await query.edit_message_text("❌ No accessible active Stories found.")
        return
    index = max(0, min(index, len(stories) - 1))
    context.user_data["story_index"] = index
    story = stories[index]
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Previous", callback_data="story_prev"), InlineKeyboardButton("Next ➡️", callback_data="story_next")], [InlineKeyboardButton("⬅️ Back to Profile", callback_data="profile")]])
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg" if story.media.photo else ".mp4") as tmp:
            await telethon_client.download_media(story, file=tmp.name)
            tmp_path = tmp.name
        caption = getattr(story, "caption", None) or ""
        caption = f"{caption}\n\n📖 Story {index + 1}/{len(stories)}".strip()
        with open(tmp_path, "rb") as f:
            if hasattr(story.media, 'photo') and story.media.photo:
                media = InputMediaPhoto(media=InputFile(f), caption=caption)
            else:
                media = InputMediaVideo(media=InputFile(f), caption=caption)
            await query.edit_message_media(media=media, reply_markup=kb)
        os.unlink(tmp_path)
    except Exception as e:
        await query.edit_message_text(f"❌ Could not fetch story: {e}", reply_markup=kb)

@telethon_client.on(events.NewMessage(incoming=True))
async def inbox_listener(event):
    if event.is_private and not event.out:
        try:
            sender = await event.get_sender()
            add_inbox_message(event.chat_id, sender.id, getattr(sender, 'first_name', 'Unknown'), getattr(sender, 'username', 'N/A'), event.raw_text if event.raw_text else "", get_media_type(event), str(event.date))
        except Exception as e:
            print(f"Inbox Error: {e}")

# --- MAIN HANDLER ---
async def _handle_link_impl(update, context):
    user_id = update.effective_user.id
    text = update.message.text if update.message.text else ""
    self_ping()
    if context.user_data.get('state') == 'awaiting_text_pdf': await handle_text_pdf_input(update, context); return
    if context.user_data.get('state') == 'awaiting_image_to_pdf': await handle_image_collect(update, context); return
    if context.user_data.get('state') == 'awaiting_pdf': await handle_pdf_upload(update, context); return
    if context.user_data.get('state') == 'awaiting_pdf_to_word': await handle_pdf_to_word(update, context); return
    if context.user_data.get('state') == 'awaiting_image_to_text': await handle_image_to_text(update, context); return
    if context.user_data.get('state') == 'awaiting_edit_photo': await handle_edit_photo(update, context); return
    if context.user_data.get('state') == 'awaiting_bg_upload': await handle_bg_upload(update, context); return
    if context.user_data.get('state') == 'awaiting_front_upload': await handle_front_upload(update, context); return
    if context.user_data.get('state') == 'awaiting_tts_en': await handle_tts(update, context, 'en'); return
    if context.user_data.get('state') == 'awaiting_tts_am': await handle_tts(update, context, 'am'); return
    if context.user_data.get('state') == 'awaiting_img_png_jpg': await handle_image_convert(update, context, 'png_jpg'); return
    if context.user_data.get('state') == 'awaiting_img_jpg_png': await handle_image_convert(update, context, 'jpg_png'); return
    if context.user_data.get('state') == 'awaiting_img_gif': await handle_image_convert(update, context, 'gif'); return
    if context.user_data.get('state') == 'awaiting_doc_pdf_pptx': await handle_pdf_to_pptx(update, context); return
    if context.user_data.get('state') == 'awaiting_doc_pptx_pdf': await handle_pptx_to_pdf(update, context); return
    if context.user_data.get('state') == 'awaiting_voice_en': await handle_voice_to_text(update, context, 'en-US'); return
    if context.user_data.get('state') == 'awaiting_voice_am': await handle_voice_to_text(update, context, 'am-ET'); return
    if context.user_data.get('state') == 'awaiting_video_link': await handle_video_download(update, context); return
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

async def handle_link(update, context):
    try:
        await _handle_link_impl(update, context)
    except Exception as exc:
        print(f"Message handler error: {exc}")
        try:
            if update.message:
                await update.message.reply_text("⚠️ This request failed, but the bot is still running. Please try again.")
        except Exception:
            pass


# --- MAIN EXECUTION ---
async def main():
    print(f"Starting bot build: {BUILD_ID}")
    init_db()
    def keep_alive():
        while True:
            self_ping()
            time.sleep(600)
    threading.Thread(target=keep_alive, daemon=True).start()
    try:
        await telethon_client.start(); print("Telethon connected!")
    except Exception as e:
        print(f"Telethon fail: {e}"); return
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
    print("Bot running with upgraded multi-source video downloader...")
    await bot_app.initialize(); await bot_app.start(); await bot_app.updater.start_polling()
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=10000), daemon=True).start()
    await asyncio.Event().wait()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"Bot crashed: {e}")
