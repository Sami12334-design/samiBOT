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
import html
from io import BytesIO
from flask import Flask
from PIL import Image, ImageEnhance, ImageFilter, ImageOps, ImageChops
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler, ContextTypes
from telethon import TelegramClient, events, functions, types
from telethon.sessions import StringSession
from telethon.utils import get_peer_id
from telethon.errors import (FloodWaitError, ChannelPrivateError, UsernameNotOccupiedError, MessageIdInvalidError)
import pymupdf
import img2pdf
from pdf2docx import Converter
from pptx import Presentation
from pptx.util import Inches
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
import yt_dlp
import speech_recognition as sr
import imageio_ffmpeg
from groq import Groq
import edge_tts

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
    """Copy a Telegram message to the bot chat while preserving media + caption."""
    text = (getattr(msg, "message", None) or "").strip()
    try:
        # Telegram Bot API captions are shorter than normal messages. If a caption
        # is too long, send the media first and then the full text separately.
        caption = text if len(text) <= 1024 else None

        if msg.photo or msg.video or msg.document or msg.voice or msg.audio or msg.gif:
            media_bytes = BytesIO()
            await telethon_client.download_media(msg, file=media_bytes)
            media_bytes.seek(0)
            if msg.photo:
                sent = await bot.send_photo(chat_id, photo=media_bytes, caption=caption)
            elif msg.video:
                sent = await bot.send_video(chat_id, video=media_bytes, caption=caption)
            elif msg.gif:
                sent = await bot.send_animation(chat_id, animation=media_bytes, caption=caption)
            elif msg.voice:
                sent = await bot.send_voice(chat_id, voice=media_bytes, caption=caption)
            elif msg.audio:
                sent = await bot.send_audio(chat_id, audio=media_bytes, caption=caption)
            else:
                sent = await bot.send_document(chat_id, document=media_bytes, caption=caption)
            if text and caption is None:
                await bot.send_message(chat_id, text=text)
            return sent

        if msg.sticker:
            sticker_bytes = BytesIO()
            await telethon_client.download_media(msg, file=sticker_bytes)
            sticker_bytes.seek(0)
            return await bot.send_sticker(chat_id, sticker=sticker_bytes)

        if text:
            return await bot.send_message(chat_id, text=text)

        return await bot.send_message(chat_id, text="📎 Unsupported/empty Telegram message.")
    except Exception as e:
        if "must forward even restricted" in str(e).lower() or "protected" in str(e).lower():
            await bot.send_message(chat_id, "🔒 This Telegram message has protected media and cannot be copied.")
        else:
            await bot.send_message(chat_id, f"⚠️ Could not display one message: {type(e).__name__}: {e}")

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

async def menu_callback(update, context):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    if not is_authenticated(user_id):
        await query.edit_message_text("🔐 Password required.")
        return

    data = query.data
    if data.startswith("vd_"):
        await handle_video_callback(update, context, data)
        return

    # Dynamic search filter buttons. They are created from the actual result
    # types, so a filter is shown only when it has matching results.
    if data.startswith("sf_"):
        filter_type = data.removeprefix("sf_")
        context.user_data["search_filter"] = filter_type
        context.user_data["search_page"] = 1
        await display_search_page(update, context, 1, filter_type)
        return

    if data.startswith("search_page_"):
        try:
            page = int(data.removeprefix("search_page_"))
        except ValueError:
            page = 1
        filter_type = context.user_data.get("search_filter", "all")
        await display_search_page(update, context, page, filter_type)
        return

    if data == "inbox":
        await show_inbox(update, context)
        return
    if data.startswith("inbox_open_"):
        try:
            index = int(data.removeprefix("inbox_open_"))
        except ValueError:
            index = -1
        await open_inbox_chat(update, context, index)
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
        kb = [[InlineKeyboardButton("📄 PDF to Word", callback_data="pdf_to_word"), InlineKeyboardButton("🖼️ Image to Text", callback_data="image_to_text")], [InlineKeyboardButton("🖼️ Image to PDF", callback_data="image_to_pdf"), InlineKeyboardButton("🖼️ Edit Photo", callback_data="image_edit")], [InlineKeyboardButton("🗣️ Text to Voice (ENG)", callback_data="tts_en"), InlineKeyboardButton("🗣️ Text to Voice (AM)", callback_data="tts_am")], [InlineKeyboardButton("📷 Image Format", callback_data="img_fmt_menu"), InlineKeyboardButton("📚 Document Format", callback_data="doc_fmt_menu")], [InlineKeyboardButton("🎙️ Voice to Text (ENG)", callback_data="voice_en"), InlineKeyboardButton("🎙️ Voice to Text (AM)", callback_data="voice_am")], [InlineKeyboardButton("⬅️ Back", callback_data="more")]]
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
        await query.message.reply_text("🔎 SEARCH\n\nEnter a keyword to search Telegram globally.\n\nI will prioritize public results outside the chats/channels you already joined.\nExample: Logic mid")
        context.user_data['state'] = 'search_query'
    elif data == "pdf_fetch":
        await query.message.reply_text("📄 PDF FETCH\n\nPlease upload the PDF file directly to this chat.")
        context.user_data['state'] = 'awaiting_pdf'
    elif data.startswith("posts_"):
        await query.answer("Profile posts have been removed.", show_alert=True)
        return
    elif data.startswith("story_"):
        # Stories were intentionally removed from Profile. This also safely
        # handles old/stale Telegram buttons created by an older bot version.
        await query.answer("Stories have been removed from Profile.", show_alert=True)
        return

# --- PHOTO EDITING ---
async def handle_photo_edit_selection(update, context, data):
    query = update.callback_query
    await query.answer()
    if not context.user_data.get('edit_image'):
        await query.message.reply_text("❌ No image found. Please send a photo first.")
        return
    img = context.user_data['edit_image']
    if data == "edit_remove_bg":
        async def run_bg_removal():
            def do_work():
                from rembg import remove, new_session
                session = new_session("u2netp")
                return remove(img, session=session)
            return await asyncio.to_thread(do_work)
        try:
            out_img = await run_bg_removal()
            out_bytes = BytesIO()
            out_img.save(out_bytes, format='PNG')
            out_bytes.seek(0)
            await query.message.reply_document(document=out_bytes, filename="no_bg.png", caption="✅ Background removed!", reply_markup=tool_done_kb())
        except Exception as e:
            await query.message.reply_text(f"❌ Background removal failed: {e}")
        return
    if data == "edit_change_bg":
        await query.message.reply_text("🖼️ **CHANGE BACKGROUND**\n\nStep 1/2: Please upload the **background image** you want to use.")
        context.user_data['state'] = 'awaiting_bg_upload'
        return
    max_side = 4096
    if img.width >= img.height:
        new_width = max_side
        new_height = int(img.height * (max_side / img.width))
    else:
        new_height = max_side
        new_width = int(img.width * (max_side / img.height))
    img = img.resize((new_width, new_height), Image.LANCZOS)
    filter_name = "Original"
    if data == "edit_orig": filter_name = "Original"
    elif data == "edit_hd":
        img = ImageEnhance.Sharpness(img).enhance(2.0)
        img = ImageEnhance.Contrast(img).enhance(1.2)
        img = ImageEnhance.Color(img).enhance(1.1)
        filter_name = "HD Enhanced"
    elif data == "edit_bw":
        img = ImageOps.grayscale(img); filter_name = "Black & White"
    elif data == "edit_sepia":
        sepia_matrix = (0.393, 0.769, 0.189, 0, 0.349, 0.686, 0.168, 0, 0.272, 0.534, 0.131, 0)
        img = img.convert("RGB", sepia_matrix); filter_name = "Vintage Sepia"
    elif data == "edit_vivid":
        img = ImageEnhance.Color(img).enhance(1.5); img = ImageEnhance.Contrast(img).enhance(1.2); filter_name = "Vivid"
    elif data == "edit_sharp":
        img = img.filter(ImageFilter.SHARPEN); filter_name = "Sharpen"
    elif data == "edit_bright":
        img = ImageEnhance.Brightness(img).enhance(1.3); filter_name = "Brighten"
    elif data == "edit_dark":
        img = ImageEnhance.Brightness(img).enhance(0.7); filter_name = "Darken"
    elif data == "edit_blur":
        img = img.filter(ImageFilter.GaussianBlur(radius=2)); filter_name = "Soft Blur"
    elif data == "edit_pixel":
        small = img.resize((64, 64), Image.BILINEAR); img = small.resize((new_width, new_height), Image.NEAREST); filter_name = "Pixel Art"
    elif data == "edit_invert":
        img = ImageOps.invert(img.convert('RGB')); filter_name = "Invert"
    elif data == "edit_sketch":
        gray = img.convert('L'); invert = ImageOps.invert(gray); blur = invert.filter(ImageFilter.GaussianBlur(radius=5)); img = ImageChops.dodge(gray, blur); filter_name = "Sketch"
    elif data == "edit_emboss":
        img = img.filter(ImageFilter.EMBOSS); filter_name = "Emboss"
    elif data == "edit_poster":
        img = ImageOps.posterize(img.convert('RGB'), bits=3); filter_name = "Posterize"
    elif data == "edit_solar":
        img = ImageOps.solarize(img.convert('RGB'), threshold=128); filter_name = "Solarize"
    out_bytes = BytesIO()
    img.save(out_bytes, format='JPEG', quality=95)
    out_bytes.seek(0)
    await query.message.reply_photo(photo=out_bytes, caption=f"✅ Applied: **{filter_name}**", reply_markup=tool_done_kb())

async def handle_edit_photo(update, context):
    if context.user_data.get('state') != 'awaiting_edit_photo': return
    if not update.message.photo:
        await update.message.reply_text("❌ Please upload an image.")
        return
    status_msg = await update.message.reply_text("⏳ Processing image...")
    try:
        photo = update.message.photo[-1]; file = await context.bot.get_file(photo.file_id)
        img_bytes = BytesIO(); await file.download_to_memory(img_bytes); img_bytes.seek(0)
        img = Image.open(img_bytes)
        context.user_data['edit_image'] = img
        kb = [[InlineKeyboardButton("🖼️ Original", callback_data="edit_orig"), InlineKeyboardButton("✨ HD 100x", callback_data="edit_hd"), InlineKeyboardButton("🎨 Vivid", callback_data="edit_vivid")], [InlineKeyboardButton("⬛ B&W", callback_data="edit_bw"), InlineKeyboardButton("🟤 Sepia", callback_data="edit_sepia"), InlineKeyboardButton("🔪 Sharpen", callback_data="edit_sharp")], [InlineKeyboardButton("☀️ Brighten", callback_data="edit_bright"), InlineKeyboardButton("🌙 Darken", callback_data="edit_dark"), InlineKeyboardButton("🌫️ Blur", callback_data="edit_blur")], [InlineKeyboardButton("🟥 Pixel", callback_data="edit_pixel"), InlineKeyboardButton("🔄 Invert", callback_data="edit_invert"), InlineKeyboardButton("✏️ Sketch", callback_data="edit_sketch")], [InlineKeyboardButton("🧊 Emboss", callback_data="edit_emboss"), InlineKeyboardButton("🎞️ Poster", callback_data="edit_poster"), InlineKeyboardButton("🔥 Solarize", callback_data="edit_solar")], [InlineKeyboardButton("🖼️ Remove BG", callback_data="edit_remove_bg"), InlineKeyboardButton("🖼️ Change BG", callback_data="edit_change_bg")]]
        await status_msg.edit_text("✅ Image loaded!\n\nChoose an editing feature below:", reply_markup=InlineKeyboardMarkup(kb))
        context.user_data['state'] = None
    except Exception as e:
        await status_msg.edit_text(f"❌ Processing failed: {e}")
        context.user_data['state'] = None

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
    await query.answer()
    parts = data.split("_")
    lang = parts[2]
    voice_type = parts[3]
    text = context.user_data.get('tts_text')
    if not text:
        await query.message.reply_text("❌ No text found. Please send the text again.")
        return
    if lang == 'en':
        voice_map = {'male': 'en-US-GuyNeural', 'female': 'en-US-JennyNeural', 'old': 'en-US-SteffanNeural', 'child': 'en-US-AnaNeural'}
    else:
        voice_map = {'male': 'am-ET-AmehaNeural', 'female': 'am-ET-MekdesNeural', 'old': 'am-ET-MekdesNeural', 'child': 'am-ET-MekdesNeural'}
    selected_voice = voice_map.get(voice_type, 'en-US-GuyNeural')
    status_msg = await query.message.reply_text("🗣️ Generating voice...")
    try:
        communicate = edge_tts.Communicate(text, selected_voice)
        audio_path = "output.mp3"
        await communicate.save(audio_path)
        with open(audio_path, "rb") as audio:
            await query.message.reply_audio(audio=audio, title=f"Voice ({voice_type})", reply_markup=tool_done_kb())
        os.unlink(audio_path)
        await status_msg.edit_text("✅ Voice generated!")
        context.user_data.pop('tts_text', None); context.user_data.pop('tts_lang', None)
    except Exception as e:
        await status_msg.edit_text(f"❌ TTS failed: {e}")

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

# --- POWERFUL MULTI-SOURCE VIDEO DOWNLOADER ---
# Direct yt-dlp is the PRIMARY YouTube downloader.
# Public Piped instances are ONLY a last-resort metadata/stream fallback.
# The Piped discovery code accepts ROOT API URLs only; paths such as
# /registered/badge are deliberately rejected.

VIDEO_MAX_UPLOAD = 50 * 1024 * 1024
VIDEO_QUALITIES = [1080, 720, 480, 360, 240]
TIKWM_API = "https://www.tikwm.com/api/"

# TeamPiped publishes the public API list. We do not hard-code individual
# instances because public instances frequently disappear or change.
PIPED_DISCOVERY_URLS = (
    "https://raw.githubusercontent.com/TeamPiped/wiki/master/Instances.md",
    "https://raw.githubusercontent.com/TeamPiped/documentation/main/content/docs/public-instances/index.md",
)
PIPED_CACHE_TTL = 60 * 60
PIPED_TIMEOUT = 10
PIPED_MAX_CANDIDATES = 20
PIPED_BAD = {}
PIPED_CACHE = {"time": 0.0, "items": []}
PIPED_LOCK = threading.Lock()

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/139.0.0.0 Safari/537.36"
)


def video_platform(url: str) -> str:
    host = urllib.parse.urlparse(url).netloc.lower().split(":", 1)[0]
    if host.startswith("www."):
        host = host[4:]
    if host == "youtu.be" or host.endswith("youtube.com"):
        return "youtube"
    if host.endswith("tiktok.com"):
        return "tiktok"
    if host.endswith("instagram.com"):
        return "instagram"
    if host == "fb.watch" or host.endswith("facebook.com"):
        return "facebook"
    return "unknown"


def normalize_public_url(url: str) -> str:
    url = (url or "").strip()
    if not re.match(r"^https?://", url, re.I):
        return url
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": BROWSER_UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.8",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.geturl() or url
    except Exception:
        return url


def youtube_video_id(url: str):
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower().split(":", 1)[0]
    if host == "youtu.be":
        parts = [p for p in parsed.path.split("/") if p]
        return parts[0] if parts else None
    if host.endswith("youtube.com"):
        qs = urllib.parse.parse_qs(parsed.query)
        if qs.get("v"):
            return qs["v"][0]
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) >= 2 and parts[0] in {"shorts", "embed", "live"}:
            return parts[1]
    return None


def _find_js_runtime():
    """Find a supported JS runtime, including a Deno installed by build.sh."""
    candidates = []
    local_deno = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".deno", "bin", "deno")
    local_node = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".node", "bin", "node")
    candidates.extend([
        ("deno", local_deno),
        ("node", local_node),
    ])
    for runtime, path in candidates:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return runtime, path
    deno = shutil.which("deno")
    if deno:
        return "deno", deno
    node = shutil.which("node")
    if node:
        return "node", node
    return None, None


def ytdlp_base_options():
    options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 30,
        "retries": 3,
        "fragment_retries": 3,
        "file_access_retries": 3,
        "concurrent_fragment_downloads": 4,
        "ffmpeg_location": imageio_ffmpeg.get_ffmpeg_exe(),
        "http_headers": {
            "User-Agent": BROWSER_UA,
            "Accept-Language": "en-US,en;q=0.8",
        },
        # Current yt-dlp can load the EJS challenge scripts remotely.
        "remote_components": {"ejs:github"},
    }

    cookies = os.environ.get("YTDLP_COOKIES_FILE", "").strip()
    if cookies and os.path.isfile(cookies):
        options["cookiefile"] = cookies

    runtime, runtime_path = _find_js_runtime()
    if runtime == "deno":
        options["js_runtimes"] = {"deno": {"path": runtime_path}}
    elif runtime == "node":
        options["js_runtimes"] = {"node": {"path": runtime_path}}

    # Optional owner-controlled extractor args. Example:
    # YTDLP_EXTRACTOR_ARGS=player_client=web
    # Leave empty by default so current yt-dlp can choose its own clients.
    extractor_args = os.environ.get("YTDLP_EXTRACTOR_ARGS", "").strip()
    if extractor_args:
        parsed = {}
        for item in extractor_args.split(";"):
            if "=" not in item:
                continue
            key, value = item.split("=", 1)
            parsed[key.strip()] = [x.strip() for x in value.split(",") if x.strip()]
        if parsed:
            options["extractor_args"] = {"youtube": parsed}

    return options


def _youtube_extraction_profiles():
    """Return conservative retries for transient YouTube extractor failures."""
    profiles = [None]
    # Only add alternatives if the owner did not explicitly configure one.
    if not os.environ.get("YTDLP_EXTRACTOR_ARGS", "").strip():
        profiles.extend([
            {"youtube": {"player_client": ["web"]}},
            {"youtube": {"player_client": ["mweb"]}},
        ])
    return profiles


def extract_ytdlp_info(url: str):
    errors = []
    for profile in _youtube_extraction_profiles():
        opts = ytdlp_base_options()
        if profile:
            opts["extractor_args"] = profile
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(url, download=False)
        except Exception as exc:
            errors.append(str(exc))
    raise RuntimeError("yt-dlp extraction failed: " + " | ".join(errors[-3:]))


def build_ytdlp_format(height=None, audio=False, platform="unknown"):
    if audio:
        return "bestaudio/best"
    if height:
        if platform == "youtube":
            return (
                f"bestvideo[height<={height}]+bestaudio/"
                f"best[height<={height}]/best"
            )
        return f"best[height<={height}]/best"
    return "bestvideo+bestaudio/best"


def _download_with_ytdlp_options(url, output_dir, opts, title_hint):
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filepath = ydl.prepare_filename(info)
        return info, filepath


def ytdlp_download(url: str, output_dir: str, *, height=None, audio=False, title_hint="video"):
    platform = video_platform(url)
    errors = []
    for profile in _youtube_extraction_profiles() if platform == "youtube" else [None]:
        opts = ytdlp_base_options()
        opts.update({
            "format": build_ytdlp_format(height, audio, platform),
            "outtmpl": os.path.join(output_dir, "%(id)s.%(ext)s"),
            "merge_output_format": "mp4",
            "overwrites": True,
        })
        if profile:
            opts["extractor_args"] = profile
        if audio:
            opts["postprocessors"] = [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }]
        try:
            info, filepath = _download_with_ytdlp_options(url, output_dir, opts, title_hint)
            if audio:
                base, _ = os.path.splitext(filepath)
                candidate = base + ".mp3"
                if os.path.isfile(candidate):
                    filepath = candidate
            if not os.path.isfile(filepath):
                stem = os.path.splitext(filepath)[0]
                for ext in (".mp4", ".mkv", ".webm", ".mov", ".mp3", ".m4a"):
                    if os.path.isfile(stem + ext):
                        filepath = stem + ext
                        break
            if not os.path.isfile(filepath):
                raise FileNotFoundError("yt-dlp finished without creating a media file")
            return {"path": filepath, "title": info.get("title") or title_hint, "info": info}
        except Exception as exc:
            errors.append(str(exc))
            for name in os.listdir(output_dir):
                path = os.path.join(output_dir, name)
                if os.path.isfile(path):
                    try:
                        os.unlink(path)
                    except Exception:
                        pass
    raise RuntimeError("yt-dlp download failed: " + " | ".join(errors[-3:]))


# --------------------------- PIPED LAST RESORT ---------------------------

def _normalize_piped_url(raw):
    """Accept ONLY the Piped API root, never /registered/badge or other paths."""
    value = (raw or "").strip().strip("`<> ")
    value = value.rstrip("/")
    if not value.startswith(("https://", "http://")):
        return None
    try:
        parsed = urllib.parse.urlparse(value)
    except Exception:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    if parsed.path not in {"", "/"} or parsed.params or parsed.query or parsed.fragment:
        return None
    host = parsed.hostname.lower() if parsed.hostname else ""
    if not host or host.startswith("pipedapi.") is False and "piped" not in host:
        # Discovery sources contain some non-Piped URLs. Reject them.
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


def _parse_piped_instances(markdown):
    found = []
    seen = set()
    # Parse markdown table rows. We ONLY inspect the API URL column (column 2).
    for line in markdown.splitlines():
        if "|" not in line:
            continue
        cells = [c.strip() for c in line.split("|")]
        if len(cells) < 3:
            continue
        api_cell = cells[2]
        urls = re.findall(r"https?://[^\s|<>`]+", api_cell)
        for raw in urls:
            raw = raw.rstrip(".,;)]")
            api = _normalize_piped_url(raw)
            if api and api not in seen:
                seen.add(api)
                found.append(api)
    return found


def _discover_piped_instances():
    discovered = []
    for source in PIPED_DISCOVERY_URLS:
        try:
            req = urllib.request.Request(
                source,
                headers={"User-Agent": BROWSER_UA, "Accept": "text/plain,*/*"},
            )
            with urllib.request.urlopen(req, timeout=PIPED_TIMEOUT) as response:
                text = response.read().decode("utf-8", "replace")
            for api in _parse_piped_instances(text):
                if api not in discovered:
                    discovered.append(api)
        except Exception:
            continue
    return discovered[:PIPED_MAX_CANDIDATES]


def _piped_get_json(api, video_id):
    endpoint = f"{api}/streams/{urllib.parse.quote(video_id, safe='')}"
    request = urllib.request.Request(
        endpoint,
        headers={
            "User-Agent": BROWSER_UA,
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.8",
        },
    )
    with urllib.request.urlopen(request, timeout=PIPED_TIMEOUT) as response:
        status = getattr(response, "status", 200)
        raw = response.read()
    if status != 200:
        raise RuntimeError(f"HTTP {status}")
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except Exception:
        preview = raw[:120].decode("utf-8", "replace").replace("\n", " ")
        raise RuntimeError(f"non-JSON response: {preview!r}")
    if not isinstance(data, dict):
        raise RuntimeError("invalid Piped JSON object")
    return data


def _piped_bad(api, minutes):
    PIPED_BAD[api] = time.time() + minutes * 60


def _piped_metadata_one(api, video_id):
    now = time.time()
    if PIPED_BAD.get(api, 0) > now:
        return None, f"{api}: temporarily skipped"
    try:
        return _piped_get_json(api, video_id), None
    except urllib.error.HTTPError as exc:
        code = exc.code
        if code == 526:
            _piped_bad(api, 360)
        elif code == 403:
            # A public instance may temporarily rate-limit/block Render.
            _piped_bad(api, 30)
        elif code in {404, 410}:
            _piped_bad(api, 120)
        elif code in {429, 500, 502, 503, 504}:
            _piped_bad(api, 10)
        else:
            _piped_bad(api, 10)
        return None, f"{api}: HTTP {code}"
    except (socket.gaierror, urllib.error.URLError, TimeoutError) as exc:
        _piped_bad(api, 10)
        return None, f"{api}: network/DNS failure"
    except Exception as exc:
        _piped_bad(api, 10)
        return None, f"{api}: {str(exc)[:120]}"


def piped_metadata(video_id: str):
    with PIPED_LOCK:
        now = time.time()
        if now - PIPED_CACHE["time"] > PIPED_CACHE_TTL or not PIPED_CACHE["items"]:
            PIPED_CACHE["items"] = _discover_piped_instances()
            PIPED_CACHE["time"] = now
        candidates = [x for x in PIPED_CACHE["items"] if PIPED_BAD.get(x, 0) <= now]

    if not candidates:
        candidates = _discover_piped_instances()

    errors = []
    for api in candidates:
        data, error = _piped_metadata_one(api, video_id)
        if data and (data.get("videoStreams") or data.get("audioStreams")):
            return data
        if error:
            errors.append(error)

    raise RuntimeError(
        "No healthy public Piped instance returned YouTube streams. "
        + (" | ".join(errors[:8]) if errors else "No candidates were available.")
    )


def choose_piped_video_stream(streams, target_height):
    valid = [s for s in streams if s.get("url") and s.get("height")]
    if not valid:
        return None
    progressive = [s for s in valid if not s.get("videoOnly")]
    if progressive:
        valid = progressive
    below = [s for s in valid if int(s.get("height", 0)) <= target_height]
    pool = below or valid
    pool.sort(key=lambda s: (abs(int(s.get("height", 0)) - target_height), -int(s.get("height", 0))))
    return pool[0]


def choose_piped_audio_stream(streams):
    valid = [s for s in streams if s.get("url")]
    if not valid:
        return None
    valid.sort(key=lambda s: float(s.get("bitrate") or 0), reverse=True)
    return valid[0]


def download_url_to_file(url: str, path: str, headers=None):
    request = urllib.request.Request(
        url,
        headers=headers or {"User-Agent": BROWSER_UA, "Accept": "*/*"},
    )
    with urllib.request.urlopen(request, timeout=45) as response, open(path, "wb") as dst:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            dst.write(chunk)


def ffmpeg_merge(video_path: str, audio_path: str, out_path: str):
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [ffmpeg, "-y", "-i", video_path, "-i", audio_path,
           "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac",
           "-movflags", "+faststart", out_path]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "FFmpeg merge failed")[-1500:])
    return out_path


def download_from_piped(meta, output_dir: str, *, height=None, audio=False):
    title = meta.get("title") or "YouTube video"
    if audio:
        stream = choose_piped_audio_stream(meta.get("audioStreams") or [])
        if not stream:
            raise RuntimeError("Piped returned no audio stream")
        raw = os.path.join(output_dir, "audio.bin")
        mp3 = os.path.join(output_dir, "audio.mp3")
        download_url_to_file(stream["url"], raw)
        result = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-i", raw,
                                 "-vn", "-c:a", "libmp3lame", "-b:a", "192k", mp3],
                                capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError((result.stderr or "Audio conversion failed")[-1200:])
        return {"path": mp3, "title": title}

    target = height or 720
    stream = choose_piped_video_stream(meta.get("videoStreams") or [], target)
    if not stream:
        raise RuntimeError("Piped returned no video stream")
    video_raw = os.path.join(output_dir, "video.bin")
    download_url_to_file(stream["url"], video_raw)
    mime = (stream.get("mimeType") or "").lower()
    has_audio = not bool(stream.get("videoOnly"))
    if has_audio:
        final = os.path.join(output_dir, "video.mp4")
        if mime.startswith("video/mp4"):
            os.replace(video_raw, final)
        else:
            result = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-i", video_raw,
                                     "-c:v", "copy", "-c:a", "aac", "-movflags", "+faststart", final],
                                    capture_output=True, text=True)
            if result.returncode != 0:
                raise RuntimeError((result.stderr or "Video conversion failed")[-1200:])
            os.unlink(video_raw)
        return {"path": final, "title": title}

    audio_stream = choose_piped_audio_stream(meta.get("audioStreams") or [])
    if not audio_stream:
        raise RuntimeError("Piped returned video-only stream and no audio stream")
    audio_raw = os.path.join(output_dir, "audio.bin")
    final = os.path.join(output_dir, "video.mp4")
    download_url_to_file(audio_stream["url"], audio_raw)
    ffmpeg_merge(video_raw, audio_raw, final)
    os.unlink(video_raw)
    os.unlink(audio_raw)
    return {"path": final, "title": title}


def tikwm_get_data(url: str):
    endpoint = TIKWM_API + "?" + urllib.parse.urlencode({"url": url})
    request = urllib.request.Request(endpoint, headers={"User-Agent": BROWSER_UA, "Accept": "application/json,*/*"})
    with urllib.request.urlopen(request, timeout=25) as response:
        data = json.loads(response.read().decode("utf-8", "replace"))
    if int(data.get("code", -1)) != 0 or not data.get("data"):
        raise RuntimeError(data.get("msg") or "TikTok fallback returned no data")
    return data["data"]


def tikwm_download(url: str, output_dir: str, *, quality="hd", audio=False):
    data = tikwm_get_data(url)
    title = data.get("title") or data.get("desc") or "TikTok video"
    if audio:
        media_url = data.get("music")
        if not media_url:
            raise RuntimeError("TikTok fallback has no audio URL")
        raw = os.path.join(output_dir, "tiktok_audio.bin")
        out = os.path.join(output_dir, "tiktok_audio.mp3")
        download_url_to_file(media_url, raw)
        result = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-i", raw,
                                 "-vn", "-c:a", "libmp3lame", "-b:a", "192k", out],
                                capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError((result.stderr or "TikTok audio conversion failed")[-1200:])
        os.unlink(raw)
        return {"path": out, "title": title}
    media_url = data.get("hdplay") if quality == "hd" else data.get("play")
    media_url = media_url or data.get("play") or data.get("wmplay")
    if not media_url:
        raise RuntimeError("TikTok fallback returned no video URL")
    out = os.path.join(output_dir, "tiktok.mp4")
    download_url_to_file(media_url, out)
    return {"path": out, "title": title}


def _quality_list_from_formats(formats):
    heights = sorted({int(f["height"]) for f in formats or []
                      if f.get("vcodec") != "none" and f.get("height")}, reverse=True)
    qualities = [q for q in VIDEO_QUALITIES if any(h >= q for h in heights)]
    return qualities or ([max(heights)] if heights else [])


def extract_download_options(url: str):
    platform = video_platform(url)
    normalized = normalize_public_url(url) if platform == "tiktok" else url
    first_error = None

    # PRIMARY: direct yt-dlp.
    try:
        info = extract_ytdlp_info(normalized)
        title = info.get("title") or "Video"
        qualities = _quality_list_from_formats(info.get("formats"))
        return {
            "source": "ytdlp",
            "platform": platform,
            "url": normalized,
            "title": title,
            "qualities": qualities or [720],
            "audio": bool(info.get("formats")) or True,
        }
    except Exception as exc:
        first_error = str(exc)

    # LAST RESORT: public Piped for YouTube only.
    if platform == "youtube":
        vid = youtube_video_id(normalized)
        if not vid:
            raise RuntimeError(f"YouTube URL could not be parsed. yt-dlp error: {first_error[:700]}")
        try:
            piped = piped_metadata(vid)
            heights = sorted({int(s.get("height")) for s in piped.get("videoStreams") or [] if s.get("height")}, reverse=True)
            qualities = [q for q in VIDEO_QUALITIES if any(h >= q for h in heights)]
            if not qualities and heights:
                qualities = [max(heights)]
            return {
                "source": "piped",
                "platform": platform,
                "url": normalized,
                "title": piped.get("title") or "YouTube video",
                "qualities": qualities or [720],
                "audio": bool(piped.get("audioStreams")),
                "piped": piped,
            }
        except Exception as piped_error:
            runtime, _ = _find_js_runtime()
            runtime_msg = runtime or "none"
            raise RuntimeError(
                "YouTube could not be extracted directly and no healthy public Piped fallback responded. "
                f"JS runtime: {runtime_msg}. "
                f"yt-dlp: {first_error[:500]} | Piped: {str(piped_error)[:700]}"
            )

    if platform == "tiktok":
        try:
            data = tikwm_get_data(normalized)
            qualities = []
            if data.get("hdplay"):
                qualities.append("hd")
            if data.get("play"):
                qualities.append("sd")
            if not qualities and data.get("wmplay"):
                qualities.append("sd")
            if not qualities:
                raise RuntimeError("TikTok fallback returned no video")
            return {"source": "tikwm", "platform": platform, "url": normalized,
                    "title": data.get("title") or data.get("desc") or "TikTok video",
                    "qualities": qualities, "audio": bool(data.get("music")), "tikwm": data}
        except Exception as fallback_error:
            raise RuntimeError(f"TikTok extraction failed. yt-dlp: {first_error[:400]} | fallback: {str(fallback_error)[:500]}")

    raise RuntimeError(f"Extraction failed: {first_error[:900]}")


def format_bytes(value):
    value = float(value or 0)
    units = ["B", "KB", "MB", "GB"]
    idx = 0
    while value >= 1024 and idx < len(units) - 1:
        value /= 1024
        idx += 1
    return f"{value:.1f} {units[idx]}"


def video_quality_keyboard(job):
    buttons = []
    platform = job["platform"]
    if platform == "tiktok" and job["source"] == "tikwm":
        if "hd" in job["qualities"]:
            buttons.append(InlineKeyboardButton("🎥 HD", callback_data="vd_q_hd"))
        if "sd" in job["qualities"]:
            buttons.append(InlineKeyboardButton("🎥 SD", callback_data="vd_q_sd"))
    else:
        for q in job.get("qualities", []):
            buttons.append(InlineKeyboardButton(f"🎥 {q}p", callback_data=f"vd_q_{q}"))
    rows = [buttons[i:i + 3] for i in range(0, len(buttons), 3)] if buttons else []
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

    status_msg = await update.message.reply_text("🔎 Checking the video with the direct downloader…")
    try:
        job = await asyncio.to_thread(extract_download_options, raw_url)
        job["chat_id"] = update.effective_chat.id
        context.user_data["video_job"] = job
        context.user_data["state"] = None
        title = job.get("title") or "Video"
        platform = job["platform"].title()
        await status_msg.edit_text(
            f"🎬 <b>{html.escape(platform)} Downloader</b>\n\n"
            f"<b>{html.escape(title[:120])}</b>\n\n"
            "Choose the quality you want:",
            reply_markup=video_quality_keyboard(job),
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        context.user_data["state"] = None
        await status_msg.edit_text(f"❌ Could not prepare download.\n\n{str(e)[:1200]}")


async def handle_video_callback(update, context, data):
    query = update.callback_query
    await query.answer()
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
    status_msg = await query.message.reply_text("⏳ Preparing your download…")
    try:
        await query.edit_message_reply_markup(reply_markup=None)
        if selected == "audio":
            quality = None
            audio = True
        elif job["source"] == "tikwm":
            quality = selected if selected in {"hd", "sd"} else "sd"
            audio = False
        else:
            quality = int(selected)
            audio = False

        def do_download():
            if job["source"] == "ytdlp":
                return ytdlp_download(job["url"], output_dir, height=quality, audio=audio, title_hint=job.get("title", "video"))
            if job["source"] == "piped":
                return download_from_piped(job["piped"], output_dir, height=quality, audio=audio)
            if job["source"] == "tikwm":
                return tikwm_download(job["url"], output_dir, quality=quality, audio=audio)
            raise RuntimeError("Unknown download source")

        result = await asyncio.to_thread(do_download)
        filepath = result["path"]
        if not os.path.isfile(filepath):
            raise FileNotFoundError("Downloaded file was not created")

        file_size = os.path.getsize(filepath)
        if file_size > VIDEO_MAX_UPLOAD and not audio and job.get("platform") == "youtube":
            lower = [q for q in VIDEO_QUALITIES if q < (quality or 999)]
            for q in lower:
                try:
                    for name in os.listdir(output_dir):
                        path = os.path.join(output_dir, name)
                        if os.path.isfile(path):
                            os.unlink(path)
                    result = await asyncio.to_thread(do_download_for_quality, job, output_dir, q)
                    filepath = result["path"]
                    file_size = os.path.getsize(filepath)
                    if file_size <= VIDEO_MAX_UPLOAD:
                        quality = q
                        break
                except Exception:
                    continue

        if file_size > VIDEO_MAX_UPLOAD:
            raise RuntimeError(
                f"The selected file is {format_bytes(file_size)}, above Telegram's 50 MB bot upload limit."
            )

        caption = f"✅ {job.get('title', 'Video')[:900]}"
        with open(filepath, "rb") as media:
            if audio:
                await query.message.reply_audio(audio=media, filename=os.path.basename(filepath),
                                                caption=caption, reply_markup=tool_done_kb())
            else:
                await query.message.reply_video(video=media, caption=caption,
                                                supports_streaming=True, reply_markup=tool_done_kb())
        await status_msg.edit_text("✅ Download completed successfully!")
    except Exception as e:
        await status_msg.edit_text(f"❌ Download failed.\n\n{str(e)[:1000]}")
    finally:
        context.user_data.pop("video_job", None)
        shutil.rmtree(output_dir, ignore_errors=True)


def do_download_for_quality(job, output_dir, quality):
    if job["source"] == "ytdlp":
        return ytdlp_download(job["url"], output_dir, height=quality, audio=False, title_hint=job.get("title", "video"))
    if job["source"] == "piped":
        return download_from_piped(job["piped"], output_dir, height=quality, audio=False)
    if job["source"] == "tikwm":
        return tikwm_download(job["url"], output_dir, quality="hd" if quality >= 720 else "sd", audio=False)
    raise RuntimeError("Unknown download source")

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
        await update.message.reply_text(f"📝 **Extracted Text:**\n\n{extracted_text}", reply_markup=tool_done_kb())
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
async def _inbox_title(entity):
    return (
        getattr(entity, "title", None)
        or " ".join(x for x in [getattr(entity, "first_name", ""), getattr(entity, "last_name", "")] if x).strip()
        or getattr(entity, "username", None)
        or "Telegram chat"
    )


def _inbox_preview(message):
    if not message:
        return "No messages yet."
    text = (getattr(message, "message", None) or "").strip().replace("\n", " ")
    if message.photo:
        return "🖼️ Photo" + (f": {text[:55]}" if text else "")
    if message.video:
        return "🎬 Video" + (f": {text[:55]}" if text else "")
    if message.voice:
        return "🎤 Voice message"
    if message.audio:
        return "🎵 Audio" + (f": {text[:55]}" if text else "")
    if message.document:
        return "📄 File" + (f": {text[:55]}" if text else "")
    if message.gif:
        return "🎞️ GIF" + (f": {text[:55]}" if text else "")
    if message.sticker:
        return "🧩 Sticker"
    return text[:90] if text else "💬 Message"


async def show_inbox(update, context):
    """Build the inbox directly from Telegram, not from the SQLite listener cache."""
    query = update.callback_query
    if query:
        await query.answer("Loading inbox…")
        chat_id = query.message.chat_id
        sender_message = query.message
    else:
        chat_id = update.effective_chat.id
        sender_message = update.message

    try:
        dialogs = []
        async for dialog in telethon_client.iter_dialogs(limit=50):
            entity = dialog.entity
            # Inbox = private one-to-one conversations and bots.
            if getattr(entity, "bot", False) or entity.__class__.__name__ in ("User",):
                if not getattr(entity, "deleted", False):
                    dialogs.append(dialog)

        context.user_data["inbox_entities"] = {}
        rows = []
        for idx, dialog in enumerate(dialogs[:30]):
            entity = dialog.entity
            context.user_data["inbox_entities"][str(idx)] = entity
            title = await _inbox_title(entity)
            last = dialog.message
            rows.append((idx, title, _inbox_preview(last), dialog.unread_count or 0))

        if not rows:
            kb = [[InlineKeyboardButton("🔄 Refresh", callback_data="inbox")],
                  [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]]
            text = "📥 INBOX\n\nNo private conversations were found."
        else:
            lines = ["📥 INBOX", "", "Your latest Telegram private conversations:", ""]
            kb = []
            for idx, title, preview, unread in rows:
                badge = f" • {unread} unread" if unread else ""
                lines.append(f"{idx + 1}. <b>{html.escape(title)}</b>{badge}\n   {html.escape(preview)}")
                kb.append([InlineKeyboardButton(f"💬 {title[:28]}", callback_data=f"inbox_open_{idx}")])
            kb.append([InlineKeyboardButton("🔄 Refresh", callback_data="inbox"), InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")])
            text = "\n".join(lines)

        if query:
            try:
                await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))
            except Exception:
                await query.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))
        else:
            await sender_message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))
    except Exception as e:
        msg = f"❌ Could not load Inbox: {type(e).__name__}: {e}"
        if query:
            await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]]))
        else:
            await sender_message.reply_text(msg)


async def open_inbox_chat(update, context, index):
    query = update.callback_query
    await query.answer()
    entity = context.user_data.get("inbox_entities", {}).get(str(index))
    if not entity:
        await query.edit_message_text("❌ This inbox item expired. Please refresh the Inbox.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Inbox", callback_data="inbox")]]))
        return

    try:
        title = await _inbox_title(entity)
        messages = await telethon_client.get_messages(entity, limit=10)
        messages = list(reversed([m for m in messages if m]))
        if not messages:
            await query.edit_message_text(
                f"💬 <b>{html.escape(title)}</b>\n\nNo messages found.",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Inbox", callback_data="inbox")]])
            )
            return

        # Replace the button message with a compact header, then send each real
        # Telegram message. This preserves text, photo+caption, photo, video,
        # documents, audio and voice instead of reducing everything to a preview.
        await query.edit_message_text(
            f"💬 <b>{html.escape(title)}</b>\n\nShowing the latest {len(messages)} messages…",
            parse_mode=ParseMode.HTML
        )
        for msg in messages:
            await safe_send(query.message.chat_id, context.bot, msg, entity, msg.id)

        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=f"📥 <b>{html.escape(title)}</b>\n\nEnd of inbox conversation.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Inbox", callback_data="inbox"), InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
            ])
        )
    except FloodWaitError as e:
        await query.message.reply_text(f"⏳ Telegram asks us to wait {e.seconds} seconds before loading this conversation.")
    except Exception as e:
        await query.message.reply_text(f"❌ Could not open conversation: {type(e).__name__}: {e}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Inbox", callback_data="inbox")]]))


async def fetch_profile(update, context, target):
    try:
        entity = await telethon_client.get_entity(target)
        context.user_data["profile_entity"] = entity
        save_user_history(entity.id, getattr(entity, "username", None), getattr(entity, "first_name", ""), getattr(entity, "last_name", ""))
        first_name = getattr(entity, "first_name", "") or ""
        last_name = getattr(entity, "last_name", "") or ""
        display_name = f"{first_name} {last_name}".strip() or getattr(entity, "title", "Unknown")
        text = f"<blockquote><b>{display_name}</b>\n@{getattr(entity, 'username', None) or 'N/A'}\n\n{getattr(entity, 'about', 'No bio')}\n\nID: {entity.id}\nVerified: {getattr(entity, 'verified', False)}\nPremium: {getattr(entity, 'premium', False)}\nBot: {getattr(entity, 'bot', False)}</blockquote>"
        kb = [[InlineKeyboardButton("⬅️ Back", callback_data="more")]]
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

# --- FAST GLOBAL TELEGRAM SEARCH ---
#
# IMPORTANT:
#   The old implementation opened up to 200 dialogs and then searched each
#   dialog separately. That is why searches could take 1-5 minutes.
#
#   This version uses Telegram's server-side global search once, then filters
#   out chats/groups/channels that are already in the user's joined dialogs.
#   The result is therefore much faster and is not limited to the user's
#   joined-chat list.
#
#   NOTE: Telegram still requires an authenticated user session for this API.
#   "Not from my account" here means "do not search only my joined chats".
#   The server-side global search is used, and joined peers are removed from
#   the displayed results.

SEARCH_PAGE_SIZE = 8
SEARCH_GLOBAL_LIMIT = 100
SEARCH_JOINED_CACHE_SECONDS = 600


def _search_peer_marked_id(entity):
    """Return Telegram's marked peer ID without making a network request."""
    try:
        return int(get_peer_id(entity))
    except Exception:
        value = getattr(entity, "id", None)
        return int(value) if value is not None else None


def _search_peer_kind(entity):
    """Classify a Telegram peer as channel/group/user/bot."""
    if entity is None:
        return "unknown"
    if getattr(entity, "bot", False):
        return "bot"
    # Telethon Channel represents both broadcast channels and supergroups.
    if hasattr(entity, "broadcast"):
        return "channel" if getattr(entity, "broadcast", False) else "group"
    # Basic Telegram groups are represented by Chat.
    if entity.__class__.__name__ == "Chat":
        return "group"
    if hasattr(entity, "first_name") or hasattr(entity, "username"):
        return "user"
    return "unknown"


def _search_peer_name(entity):
    if entity is None:
        return "Unknown"
    title = getattr(entity, "title", None)
    if title:
        return str(title).strip()
    first = (getattr(entity, "first_name", None) or "").strip()
    last = (getattr(entity, "last_name", None) or "").strip()
    name = f"{first} {last}".strip()
    return name or "Unknown"


def _search_peer_link(entity):
    username = getattr(entity, "username", None)
    if username:
        return f"https://t.me/{username}"
    return None


def _search_message_link(entity, message_id):
    username = getattr(entity, "username", None)
    if username:
        return f"https://t.me/{username}/{message_id}"

    # For public channels/groups without a username Telegram may not expose a
    # normal clickable public URL. We keep the result but omit a fake link.
    marked_id = _search_peer_marked_id(entity)
    if marked_id is not None and marked_id < -1000000000000:
        internal_id = str(abs(marked_id))[3:]
        return f"https://t.me/c/{internal_id}/{message_id}"
    return None


def _search_message_type(message):
    if getattr(message, "photo", None):
        return "photo"
    if getattr(message, "video", None):
        return "video"
    if getattr(message, "voice", None):
        return "voice"
    if getattr(message, "audio", None):
        return "audio"
    if getattr(message, "gif", None):
        return "gif"
    if getattr(message, "document", None):
        return "document"

    # Detect URLs without doing another network request.
    text = (getattr(message, "message", None) or "").strip()
    if re.search(r"https?://|t\.me/|www\.", text, re.I):
        return "link"
    return "text"


def _search_content(message):
    text = (getattr(message, "message", None) or "").strip()
    if text:
        return re.sub(r"\s+", " ", text)[:180]
    media_type = _search_message_type(message)
    labels = {
        "photo": "📷 Photo",
        "video": "🎬 Video",
        "voice": "🎤 Voice message",
        "audio": "🎵 Audio",
        "gif": "🎞️ GIF",
        "document": "📄 Document",
        "link": "🔗 Link",
        "text": "💬 Message",
    }
    return labels.get(media_type, "💬 Message")


async def _get_joined_peer_ids(context):
    """Cache joined peer IDs so every search does not call get_dialogs()."""
    now = time.monotonic()
    cached_at = context.application.bot_data.get("search_joined_cache_at", 0.0)
    cached_ids = context.application.bot_data.get("search_joined_ids")
    if cached_ids is not None and now - cached_at < SEARCH_JOINED_CACHE_SECONDS:
        return set(cached_ids)

    joined = set()
    try:
        # This call is only for the exclusion list. The actual search below is
        # still one server-side global search request.
        async for dialog in telethon_client.iter_dialogs(limit=500):
            entity = getattr(dialog, "entity", None)
            if entity is None:
                continue
            kind = _search_peer_kind(entity)
            if kind in {"group", "channel"}:
                marked = _search_peer_marked_id(entity)
                if marked is not None:
                    joined.add(marked)
    except Exception as exc:
        # If the cache cannot be refreshed, keep the previous cache rather than
        # failing the whole global search.
        print(f"[Search] joined-peer cache refresh failed: {exc}")
        if cached_ids is not None:
            return set(cached_ids)

    context.application.bot_data["search_joined_ids"] = joined
    context.application.bot_data["search_joined_cache_at"] = now
    return joined


def _search_peer_from_result(peer_id, peer_map):
    if peer_id is None:
        return None
    try:
        return peer_map.get(int(peer_id))
    except Exception:
        return None


async def fetch_search(update, context, query):
    query = (query or "").strip()
    if not query:
        await update.message.reply_text("🔎 Please enter a keyword, for example: Logic mid")
        return

    status_msg = await update.message.reply_text(
        f"🔎 Searching Telegram globally for: <b>{html.escape(query)}</b>\n"
        "⚡ Server-side search • excluding your joined groups/channels",
        parse_mode=ParseMode.HTML,
    )

    started = time.monotonic()
    try:
        # Refresh the exclusion cache and run the global request concurrently.
        joined_task = asyncio.create_task(_get_joined_peer_ids(context))

        # Telegram's messages.searchGlobal is the server-side global search.
        # It is NOT the old "get_dialogs -> search every dialog" approach.
        global_result = await telethon_client(
            functions.messages.SearchGlobalRequest(
                q=query,
                filter=types.InputMessagesFilterEmpty(),
                min_date=0,
                max_date=0,
                offset_rate=0,
                offset_peer=types.InputPeerEmpty(),
                offset_id=0,
                limit=SEARCH_GLOBAL_LIMIT,
            )
        )
        joined_ids = await joined_task

        # Build the peer map from Telegram's returned auxiliary objects.
        peer_map = {}
        for entity in list(getattr(global_result, "chats", []) or []) + list(getattr(global_result, "users", []) or []):
            marked = _search_peer_marked_id(entity)
            if marked is not None:
                peer_map[marked] = entity

        results = []
        seen = set()
        joined_hidden = 0

        for message in list(getattr(global_result, "messages", []) or []):
            marked_peer = None
            try:
                marked_peer = _search_peer_marked_id(message.peer_id)
            except Exception:
                marked_peer = None

            entity = _search_peer_from_result(marked_peer, peer_map)

            # Some result constructors expose chat_id more conveniently.
            if entity is None:
                chat_id = getattr(message, "chat_id", None)
                if chat_id is not None:
                    for candidate_id, candidate in peer_map.items():
                        if candidate_id == int(chat_id):
                            entity = candidate
                            marked_peer = candidate_id
                            break

            if entity is None:
                # Do not make one get_entity() request per result. That was one
                # of the major causes of the old slow search.
                continue

            peer_kind = _search_peer_kind(entity)

            # Main requirement: hide messages belonging to groups/channels the
            # logged-in Telegram account has already joined.
            if marked_peer in joined_ids and peer_kind in {"group", "channel"}:
                joined_hidden += 1
                continue

            # Do not turn this into a private-chat/message search. The search is
            # intended as public discovery. Bots are kept because they are a
            # requested search category.
            if peer_kind == "user":
                continue

            message_id = getattr(message, "id", None)
            if not message_id:
                continue

            key = (marked_peer, int(message_id))
            if key in seen:
                continue
            seen.add(key)

            media_type = _search_message_type(message)
            link = _search_message_link(entity, message_id)
            results.append({
                "kind": "message",
                "type": media_type,
                "peer_type": peer_kind,
                "peer_id": marked_peer,
                "entity": entity,
                "message": message,
                "link": link,
                "content": _search_content(message),
                "title": _search_peer_name(entity),
                "username": getattr(entity, "username", None),
                "date": getattr(message, "date", None),
                "score": 1000 - len(results),
            })

        # Also expose public peers returned by the global search. This gives
        # users a useful "channel/group/bot" result even when the message list
        # itself is short.
        for entity in list(getattr(global_result, "chats", []) or []) + list(getattr(global_result, "users", []) or []):
            kind = _search_peer_kind(entity)
            marked = _search_peer_marked_id(entity)
            if kind in {"group", "channel"} and marked in joined_ids:
                continue
            if kind not in {"group", "channel", "bot"}:
                continue

            name = _search_peer_name(entity)
            username = getattr(entity, "username", None)
            searchable = f"{name} @{username or ''}".lower()
            if query.lower() not in searchable and not any(
                r["peer_id"] == marked for r in results
            ):
                continue

            peer_link = _search_peer_link(entity)
            peer_key = ("peer", marked)
            if peer_key in seen:
                continue
            seen.add(peer_key)
            results.append({
                "kind": "peer",
                "type": kind,
                "peer_type": kind,
                "peer_id": marked,
                "entity": entity,
                "message": None,
                "link": peer_link,
                "content": f"{('🤖' if kind == 'bot' else '📢' if kind == 'channel' else '👥')} {name}"
                            + (f"  @{username}" if username else ""),
                "title": name,
                "username": username,
                "date": None,
                "score": 2000 - len(results),
            })

        if not results:
            await status_msg.edit_text(
                f"❌ No public results found for <b>{html.escape(query)}</b>.\n\n"
                "The search intentionally hides results from groups/channels you already joined.",
                parse_mode=ParseMode.HTML,
            )
            return

        # Put actual messages first, then peer cards, preserving Telegram's
        # relevance ordering as much as possible.
        results.sort(key=lambda item: item.get("score", 0), reverse=True)

        context.user_data["search_results"] = results
        context.user_data["search_query"] = query
        context.user_data["search_filter"] = "all"
        context.user_data["search_page"] = 1
        context.user_data["search_joined_hidden"] = joined_hidden
        context.user_data["search_elapsed"] = round(time.monotonic() - started, 2)

        await status_msg.delete()
        await display_search_page(update, context, 1, "all")

    except FloodWaitError as e:
        await status_msg.edit_text(
            f"⏳ Telegram rate limit. Please wait {e.seconds} seconds and try again."
        )
    except Exception as e:
        print(f"[Search] Global search failed: {type(e).__name__}: {e}")
        await status_msg.edit_text(
            f"❌ Search failed: <code>{html.escape(type(e).__name__)}</code>\n\n"
            f"{html.escape(str(e)[:700])}",
            parse_mode=ParseMode.HTML,
        )


def _search_filter_match(item, filter_type):
    if filter_type == "all":
        return True
    if filter_type == "messages":
        return item.get("kind") == "message"
    if filter_type == "photos":
        return item.get("type") == "photo"
    if filter_type == "videos":
        return item.get("type") == "video"
    if filter_type == "files":
        return item.get("type") in {"document", "gif"}
    if filter_type == "audio":
        return item.get("type") in {"audio", "voice"}
    if filter_type == "links":
        return item.get("type") == "link"
    if filter_type == "channels":
        return item.get("peer_type") == "channel"
    if filter_type == "groups":
        return item.get("peer_type") == "group"
    if filter_type == "bots":
        return item.get("peer_type") == "bot"
    return True


def _search_filter_buttons(results, active):
    """Create only filters that actually have results. This is dynamic."""
    definitions = [
        ("all", "🔎 All"),
        ("messages", "💬 Messages"),
        ("photos", "📷 Photos"),
        ("videos", "🎬 Videos"),
        ("files", "📄 Files"),
        ("audio", "🎵 Audio"),
        ("links", "🔗 Links"),
        ("channels", "📢 Channels"),
        ("groups", "👥 Groups"),
        ("bots", "🤖 Bots"),
    ]

    counts = {}
    for filter_type, _ in definitions:
        counts[filter_type] = sum(1 for item in results if _search_filter_match(item, filter_type))

    available = []
    for filter_type, label in definitions:
        if filter_type == "all" or counts[filter_type] > 0:
            shown = f"{label} ({counts[filter_type]})"
            if filter_type == active:
                shown = f"✅ {shown}"
            available.append((filter_type, shown))

    rows = []
    # Five compact buttons per row; Telegram clients can render this cleanly.
    for i in range(0, len(available), 3):
        rows.append([
            InlineKeyboardButton(label, callback_data=f"sf_{filter_type}")
            for filter_type, label in available[i:i + 3]
        ])
    return rows, counts


async def display_search_page(update, context, page, filter_type=None):
    callback = update.callback_query
    if callback:
        await callback.answer()

    results = context.user_data.get("search_results", [])
    search_query = context.user_data.get("search_query", "")
    active = filter_type or context.user_data.get("search_filter", "all")

    filtered = [item for item in results if _search_filter_match(item, active)]
    per_page = SEARCH_PAGE_SIZE
    total_results = len(filtered)
    total_pages = max(1, (total_results + per_page - 1) // per_page)
    page = max(1, min(int(page), total_pages))
    context.user_data["search_page"] = page
    context.user_data["search_filter"] = active

    start = (page - 1) * per_page
    page_items = filtered[start:start + per_page]

    elapsed = context.user_data.get("search_elapsed", 0)
    joined_hidden = context.user_data.get("search_joined_hidden", 0)

    lines = [
        "<blockquote>",
        "<b>🌐 GLOBAL TELEGRAM SEARCH</b>",
        f"🔎 <b>{html.escape(search_query)}</b>",
        f"⚡ {elapsed}s • hidden joined results: {joined_hidden}",
        "",
    ]

    if not page_items:
        lines.append("<i>No results in this filter.</i>")
    else:
        for index, item in enumerate(page_items, start=start + 1):
            peer_name = html.escape(item.get("title") or "Unknown")
            username = item.get("username")
            username_text = f" @{html.escape(username)}" if username else ""
            icon = {
                "photo": "📷", "video": "🎬", "document": "📄", "gif": "🎞️",
                "audio": "🎵", "voice": "🎤", "link": "🔗", "bot": "🤖",
                "channel": "📢", "group": "👥", "text": "💬",
            }.get(item.get("type"), "🔹")

            content = html.escape(item.get("content") or "")
            if len(content) > 180:
                content = content[:177] + "..."

            if item.get("link"):
                lines.append(
                    f"<b>{index}.</b> {icon} <a href=\"{html.escape(item['link'], quote=True)}\">"
                    f"{peer_name}{username_text}</a>\n{content}\n"
                )
            else:
                lines.append(
                    f"<b>{index}.</b> {icon} <b>{peer_name}{username_text}</b>\n{content}\n"
                )

    lines.append(f"Page {page}/{total_pages} • {total_results} results")
    lines.append("</blockquote>")
    text = "\n".join(lines)

    kb, counts = _search_filter_buttons(results, active)

    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"search_page_{page - 1}"))
    if page < total_pages:
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"search_page_{page + 1}"))
    if nav:
        kb.append(nav)

    kb.append([
        InlineKeyboardButton("🔄 New Search", callback_data="search"),
        InlineKeyboardButton("⬅️ Back", callback_data="more"),
    ])
    markup = InlineKeyboardMarkup(kb)

    if callback:
        try:
            await callback.edit_message_text(
                text,
                reply_markup=markup,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
        except Exception as exc:
            # Telegram may reject an edit if the content is unchanged.
            if "message is not modified" not in str(exc).lower():
                raise
    else:
        await update.message.reply_text(
            text,
            reply_markup=markup,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )

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

# --- MAIN HANDLER ---
async def handle_link(update, context):
    user_id = update.effective_user.id
    text = update.message.text if update.message.text else ""
    self_ping()
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

# --- MAIN EXECUTION ---
async def main():
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
