import re
import asyncio
import os
import sys
import json
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
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import yt_dlp
import imageio_ffmpeg
from groq import Groq
import edge_tts

# ============================================================
# QR CODE IMPORTS
# ============================================================
import qrcode
import cv2
import numpy as np

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
    
    # --- Message Manager Table (ADDED) ---
    c.execute('''CREATE TABLE IF NOT EXISTS message_manager_rules (rule_key TEXT PRIMARY KEY, rule_value TEXT)''')
    defaults = {
        "messaging": "on",
        "block_everyone_until": "0",
        "blocked_users": "[]",
        "filter_links_until": "0",
        "filter_videos_until": "0",
        "keyword_rules": "{}"
    }
    for key, value in defaults.items():
        c.execute("INSERT OR IGNORE INTO message_manager_rules (rule_key, rule_value) VALUES (?, ?)", (key, value))
    
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

# ============================================================
# MESSAGE MANAGER (ADDED)
# ============================================================

def mm_get_rule(key, default=None):
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute("SELECT rule_value FROM message_manager_rules WHERE rule_key = ?", (key,))
    row = c.fetchone()
    conn.close()
    if row is None:
        return default
    return row[0]

def mm_set_rule(key, value):
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO message_manager_rules (rule_key, rule_value) VALUES (?, ?)", (key, str(value)))
    conn.commit()
    conn.close()

def mm_get_json_rule(key, default):
    raw = mm_get_rule(key, json.dumps(default))
    try:
        return json.loads(raw)
    except Exception:
        return default

def mm_set_json_rule(key, value):
    mm_set_rule(key, json.dumps(value, ensure_ascii=False))

def mm_is_messaging_on():
    return mm_get_rule("messaging", "on").lower() == "on"

def mm_set_messaging(enabled):
    mm_set_rule("messaging", "on" if enabled else "off")

def mm_parse_duration(text):
    if not text:
        return 0
    value = str(text).strip().lower()
    if value in ("permanent", "forever", "perm", "always", "permanently", "∞"):
        return -1
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(second|seconds|sec|secs|minute|minutes|min|mins|hour|hours|hr|hrs|day|days|week|weeks|month|months)\s*", value)
    if not match:
        return 0
    number = float(match.group(1))
    unit = match.group(2)
    if number <= 0:
        return 0
    if unit in ("second", "seconds", "sec", "secs"):
        return int(number)
    if unit in ("minute", "minutes", "min", "mins"):
        return int(number * 60)
    if unit in ("hour", "hours", "hr", "hrs"):
        return int(number * 60 * 60)
    if unit in ("day", "days"):
        return int(number * 24 * 60 * 60)
    if unit in ("week", "weeks"):
        return int(number * 7 * 24 * 60 * 60)
    if unit in ("month", "months"):
        return int(number * 30 * 24 * 60 * 60)
    return 0

def mm_duration_to_until(duration_seconds):
    if duration_seconds == -1:
        return -1
    if duration_seconds <= 0:
        return 0
    return int(time.time()) + int(duration_seconds)

def mm_is_active_until(until):
    try: until = int(until)
    except: return False
    if until == -1: return True
    if until <= 0: return False
    return time.time() < until

def mm_remaining_seconds(until):
    try: until = int(until)
    except: return 0
    if until == -1: return -1
    if until <= 0: return 0
    return max(0, int(until - time.time()))

def mm_format_duration(seconds):
    try: seconds = int(seconds)
    except: return "Unknown"
    if seconds == -1: return "Permanent"
    if seconds <= 0: return "Inactive"
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, secs = divmod(remainder, 60)
    parts = []
    if days: parts.append(f"{days} day{'s' if days != 1 else ''}")
    if hours: parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if minutes: parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    if secs and len(parts) < 2: parts.append(f"{secs} second{'s' if secs != 1 else ''}")
    if not parts: return "Less than 1 second"
    return " ".join(parts[:2])

def mm_until_text(until):
    if not mm_is_active_until(until):
        return "OFF"
    remaining = mm_remaining_seconds(until)
    if remaining == -1:
        return "PERMANENT"
    return mm_format_duration(remaining)

def mm_get_block_everyone_until():
    try: return int(mm_get_rule("block_everyone_until", "0"))
    except: return 0
def mm_set_block_everyone_until(until):
    mm_set_rule("block_everyone_until", until)
def mm_is_everyone_blocked():
    return mm_is_active_until(mm_get_block_everyone_until())

def mm_get_filter_links_until():
    try: return int(mm_get_rule("filter_links_until", "0"))
    except: return 0
def mm_set_filter_links_until(until):
    mm_set_rule("filter_links_until", until)
def mm_links_are_filtered():
    return mm_is_active_until(mm_get_filter_links_until())

def mm_get_filter_videos_until():
    try: return int(mm_get_rule("filter_videos_until", "0"))
    except: return 0
def mm_set_filter_videos_until(until):
    mm_set_rule("filter_videos_until", until)
def mm_videos_are_filtered():
    return mm_is_active_until(mm_get_filter_videos_until())

def mm_get_blocked_users():
    data = mm_get_json_rule("blocked_users", [])
    if not isinstance(data, list): return []
    cleaned = []
    for item in data:
        if not isinstance(item, dict): continue
        try: cleaned.append({"user_id": int(item.get("user_id")), "until": int(item.get("until", 0))})
        except: continue
    return cleaned

def mm_save_blocked_users(users):
    mm_set_json_rule("blocked_users", users)

def mm_block_specific_user(user_id, until):
    try: user_id = int(user_id)
    except: return False
    users = [x for x in mm_get_blocked_users() if int(x.get("user_id", 0)) != user_id]
    users.append({"user_id": user_id, "until": int(until)})
    mm_save_blocked_users(users)
    return True

def mm_unblock_specific_user(user_id):
    try: user_id = int(user_id)
    except: return False
    users = mm_get_blocked_users()
    new_users = [x for x in users if int(x.get("user_id", 0)) != user_id]
    mm_save_blocked_users(new_users)
    return len(new_users) != len(users)

def mm_is_user_blocked(user_id):
    try: user_id = int(user_id)
    except: return False
    users = mm_get_blocked_users()
    changed = False
    active = False
    new_users = []
    for item in users:
        try:
            uid = int(item["user_id"])
            until = int(item["until"])
        except: continue
        if uid == user_id:
            if mm_is_active_until(until):
                active = True
                new_users.append(item)
            else: changed = True
        else:
            new_users.append(item)
    if changed:
        mm_save_blocked_users(new_users)
    return active

def mm_get_keyword_rules():
    data = mm_get_json_rule("keyword_rules", {})
    if not isinstance(data, dict): return {}
    cleaned = {}
    for keyword, until in data.items():
        try: cleaned[str(keyword).lower()] = int(until)
        except: continue
    return cleaned

def mm_save_keyword_rules(rules):
    mm_set_json_rule("keyword_rules", rules)

def mm_add_keyword(keyword, until):
    keyword = str(keyword).strip().lower()
    if not keyword: return False
    rules = mm_get_keyword_rules()
    rules[keyword] = int(until)
    mm_save_keyword_rules(rules)
    return True

def mm_remove_keyword(keyword):
    keyword = str(keyword).strip().lower()
    rules = mm_get_keyword_rules()
    if keyword not in rules: return False
    del rules[keyword]
    mm_save_keyword_rules(rules)
    return True

def mm_cleanup_keyword_rules():
    rules = mm_get_keyword_rules()
    changed = False
    cleaned = {}
    for keyword, until in rules.items():
        if mm_is_active_until(until): cleaned[keyword] = until
        else: changed = True
    if changed: mm_save_keyword_rules(cleaned)
    return cleaned

def mm_find_keyword(text):
    if not text: return None
    text_lower = text.lower()
    rules = mm_cleanup_keyword_rules()
    for keyword in sorted(rules.keys(), key=len, reverse=True):
        if keyword in text_lower: return keyword
    return None

def mm_get_active_keywords():
    return mm_cleanup_keyword_rules()

def mm_contains_link(event):
    text = getattr(event, "raw_text", "") or ""
    if re.search(r"(https?://|www\.|t\.me/|telegram\.me/)", text, re.IGNORECASE): return True
    try:
        entities = getattr(event.message, "entities", None)
        if entities:
            for entity in entities:
                entity_name = type(entity).__name__.lower()
                if "url" in entity_name or "texturl" in entity_name: return True
    except: pass
    return False

def mm_is_video(event):
    try:
        if getattr(event, "video", None) or getattr(event, "gif", None): return True
        message = getattr(event, "message", None)
        if message:
            media = getattr(message, "media", None)
            if media:
                media_name = type(media).__name__.lower()
                if "document" in media_name:
                    document = getattr(media, "document", None)
                    if document:
                        for attr in getattr(document, "attributes", []):
                            attr_name = type(attr).__name__.lower()
                            if "video" in attr_name or "animated" in attr_name: return True
    except: pass
    return False

def mm_should_block_message(sender_id, event):
    if sender_id in ADMIN_IDS: return False
    if mm_is_everyone_blocked(): return True
    if mm_is_user_blocked(sender_id): return True
    if mm_links_are_filtered() and mm_contains_link(event): return True
    if mm_videos_are_filtered() and mm_is_video(event): return True
    text = getattr(event, "raw_text", "") or ""
    if text and mm_find_keyword(text): return True
    return False

def mm_refusal_text():
    return "🤖 **Message Manager**\n\nSorry, messaging is currently unavailable.\nYour message was not forwarded to the administrator.\n\nPlease try again later. 🙏"

# Message Manager Callback Handler
async def handle_message_manager_callback(query, context):
    user_id = query.from_user.id
    if not is_admin(user_id):
        await query.answer("🔒 Admin only.", show_alert=True)
        return
    data = query.data or ""
    try: await query.answer()
    except: pass

    if data == "message_manager":
        await query.message.reply_text("💬 **MESSAGE MANAGER**\n\nChoose an option below:", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(f"💬 Messaging: {'🟢 ON' if mm_is_messaging_on() else '🔴 OFF'}", callback_data="mm_toggle")],
            [InlineKeyboardButton("🚫 Block Messages", callback_data="mm_block_menu"), InlineKeyboardButton("🔎 Filter Messages", callback_data="mm_filter_menu")],
            [InlineKeyboardButton("📋 Active Rules", callback_data="mm_active_rules")],
            [InlineKeyboardButton("⬅️ Back", callback_data="more")]
        ]), parse_mode=ParseMode.MARKDOWN)
    elif data == "mm_toggle":
        new_status = not mm_is_messaging_on()
        mm_set_messaging(new_status)
        await query.message.reply_text("🟢 **Messaging is ON**" if new_status else "🔴 **Messaging is OFF**", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💬 Message Manager", callback_data="message_manager")]]), parse_mode=ParseMode.MARKDOWN)
    elif data == "mm_block_menu":
        await query.message.reply_text("🚫 **BLOCK MESSAGES**\n\nCurrent: **{}**".format(mm_until_text(mm_get_block_everyone_until())), reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("👥 Block Everyone", callback_data="mm_block_everyone")],
            [InlineKeyboardButton("👤 Block Specific User", callback_data="mm_block_specific")],
            [InlineKeyboardButton("🟢 Turn OFF Block Everyone", callback_data="mm_unblock_everyone")],
            [InlineKeyboardButton("⬅️ Back", callback_data="message_manager")]
        ]), parse_mode=ParseMode.MARKDOWN)
    elif data == "mm_block_everyone":
        await query.message.reply_text("Choose how long to block everyone:", reply_markup=mm_duration_keyboard("block_all"), parse_mode=ParseMode.MARKDOWN)
    elif data == "mm_unblock_everyone":
        mm_set_block_everyone_until(0)
        await query.message.reply_text("🟢 Block Everyone is OFF.")
    elif data == "mm_block_specific":
        context.user_data["state"] = "mm_specific_user"
        await query.message.reply_text("👤 Send the Telegram numeric User ID to block.")
    elif data == "mm_filter_menu":
        await query.message.reply_text("🔎 **FILTER MESSAGES**", reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(f"🔗 Links: {mm_until_text(mm_get_filter_links_until())}", callback_data="mm_filter_links")],
            [InlineKeyboardButton(f"🎥 Videos: {mm_until_text(mm_get_filter_videos_until())}", callback_data="mm_filter_videos")],
            [InlineKeyboardButton("🔤 Keywords", callback_data="mm_filter_keywords")],
            [InlineKeyboardButton("⬅️ Back", callback_data="message_manager")]
        ]), parse_mode=ParseMode.MARKDOWN)
    elif data == "mm_filter_links":
        await query.message.reply_text("Choose how long to filter links:", reply_markup=mm_duration_keyboard("filter_links"))
    elif data == "mm_filter_videos":
        await query.message.reply_text("Choose how long to filter videos:", reply_markup=mm_duration_keyboard("filter_videos"))
    elif data == "mm_filter_keywords":
        await query.message.reply_text("🔤 **KEYWORD FILTERS**", parse_mode=ParseMode.MARKDOWN)
    elif data.startswith("mm_dur|"):
        parts = data.split("|")
        if len(parts) == 3:
            seconds = int(parts[2])
            until = mm_duration_to_until(seconds)
            prefix = parts[1]
            if prefix == "block_all":
                mm_set_block_everyone_until(until)
                await query.message.reply_text(f"✅ Block Everyone updated: {mm_until_text(until)}")
            elif prefix == "filter_links":
                mm_set_filter_links_until(until)
                await query.message.reply_text(f"✅ Link Filter updated: {mm_until_text(until)}")
            elif prefix == "filter_videos":
                mm_set_filter_videos_until(until)
                await query.message.reply_text(f"✅ Video Filter updated: {mm_until_text(until)}")

def mm_duration_keyboard(prefix):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("30 sec", callback_data=f"mm_dur|{prefix}|30"), InlineKeyboardButton("1 min", callback_data=f"mm_dur|{prefix}|60")],
        [InlineKeyboardButton("1 hour", callback_data=f"mm_dur|{prefix}|3600"), InlineKeyboardButton("1 day", callback_data=f"mm_dur|{prefix}|86400")],
        [InlineKeyboardButton("1 month", callback_data=f"mm_dur|{prefix}|2592000"), InlineKeyboardButton("♾ Permanent", callback_data=f"mm_dur|{prefix}|-1")],
        [InlineKeyboardButton("⬅️ Back", callback_data="message_manager")]
    ])

# ============================================================
# QR CODE TOOLS (ADDED)
# ============================================================

def create_qr_image_sync(text):
    qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=12, border=4)
    qr.add_data(text)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    output = BytesIO()
    img.save(output, format="PNG")
    output.seek(0)
    return output

def scan_qr_image_sync(image_bytes):
    image_array = bytearray(image_bytes)
    np_array = np.frombuffer(image_array, dtype=np.uint8)
    image = cv2.imdecode(np_array, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("Could not open the image.")
    detector = cv2.QRCodeDetector()
    results = []
    try:
        retval, decoded_info, points, straight_qrcode = detector.detectAndDecodeMulti(image)
        if retval and decoded_info:
            for value in decoded_info:
                if value and value.strip():
                    value = value.strip()
                    if value not in results:
                        results.append(value)
    except Exception:
        pass
    if not results:
        try:
            data, points, _ = detector.detectAndDecode(image)
            if data and data.strip():
                results.append(data.strip())
        except Exception:
            pass
    return results

def qr_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📷 Scan QR", callback_data="qr_scan"), InlineKeyboardButton("🎨 Create QR", callback_data="qr_create")],
        [InlineKeyboardButton("⬅️ Back", callback_data="converter")]
    ])

async def show_qr_menu(update, context):
    query = update.callback_query
    try: await query.answer()
    except: pass
    context.user_data["qr_mode"] = None
    context.user_data["state"] = None
    await query.message.reply_text("📱 QR CODE TOOLS\n\nChoose what you want to do:", reply_markup=qr_menu_keyboard())

async def start_qr_scan(update, context):
    query = update.callback_query
    try: await query.answer()
    except: pass
    context.user_data["qr_mode"] = "scan"
    context.user_data["state"] = "qr_scan"
    await query.message.reply_text("📷 SCAN QR CODE\n\nSend me a photo containing a QR code.")

async def start_qr_create(update, context):
    query = update.callback_query
    try: await query.answer()
    except: pass
    context.user_data["qr_mode"] = "create"
    context.user_data["state"] = "qr_create"
    await query.message.reply_text("🎨 CREATE QR CODE\n\nSend me the text or link you want to put inside the QR code.\n\nExample:\nhttps://example.com")

async def handle_qr_photo(update, context):
    if context.user_data.get("state") != "qr_scan":
        return False
    if not update.message or not update.message.photo:
        await update.message.reply_text("❌ Please send a QR-code image.")
        return True
    status = await update.message.reply_text("🔍 Scanning QR code...")
    temp_path = None
    try:
        photo = update.message.photo[-1]
        telegram_file = await context.bot.get_file(photo.file_id)
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as temp:
            temp_path = temp.name
        await telegram_file.download_to_drive(custom_path=temp_path)
        with open(temp_path, "rb") as image_file:
            image_bytes = image_file.read()
        results = await asyncio.to_thread(scan_qr_image_sync, image_bytes)
        if not results:
            await status.edit_text("❌ No readable QR code was found.")
            return True
        lines = ["✅ QR CODE FOUND!", ""]
        for index, value in enumerate(results, start=1):
            lines.append(f"📌 QR #{index}")
            lines.append(value)
            lines.append("")
        await status.edit_text("\n".join(lines))
    except Exception as e:
        await status.edit_text(f"❌ QR scanning failed.\n\nError: {str(e)[:1000]}")
    finally:
        if temp_path:
            try: os.remove(temp_path)
            except: pass
        context.user_data["state"] = "qr_scan"
    return True

async def handle_qr_create(update, context):
    if context.user_data.get("state") != "qr_create":
        return False
    if not update.message:
        return True
    text = (update.message.text or update.message.caption or "").strip()
    if not text:
        await update.message.reply_text("❌ Please send text or a link to put inside the QR code.")
        return True
    if len(text) > 4000:
        await update.message.reply_text("❌ The content is too long. Please use 4000 characters or less.")
        return True
    status = await update.message.reply_text("🎨 Creating QR code...")
    try:
        qr_image = await asyncio.to_thread(create_qr_image_sync, text)
        await status.delete()
        await update.message.reply_photo(photo=qr_image, caption="✅ QR code created successfully!\n\n📌 Content:\n" + text[:900], reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📱 QR Code Menu", callback_data="qr_menu")]]))
    except Exception as e:
        await status.edit_text(f"❌ Could not create QR code.\n\nError: {str(e)[:1000]}")
    context.user_data["state"] = "qr_create"
    return True

# ============================================================
# EXISTING menu_callback (modified to add QR menu button)
# ============================================================

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

    # Dynamic search filter buttons.
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

    # ========================================================
    # QR CODE MENU (ADDED)
    # ========================================================
    if data == "qr_menu":
        await show_qr_menu(update, context)
        return
    if data == "qr_scan":
        await start_qr_scan(update, context)
        return
    if data == "qr_create":
        await start_qr_create(update, context)
        return

    # ========================================================
    # MESSAGE MANAGER (ADDED)
    # ========================================================
    if data == "message_manager" or data.startswith("mm_"):
        await handle_message_manager_callback(query, context)
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
            admin_buttons = [[InlineKeyboardButton("🔔 Track", callback_data="track"), InlineKeyboardButton("🔗 Names", callback_data="names")], [InlineKeyboardButton("👥 Groups", callback_data="groups"), InlineKeyboardButton("💬 Messages", callback_data="messages")], [InlineKeyboardButton("🔎 Analysis", callback_data="analysis"), InlineKeyboardButton("📢 Channels", callback_data="channels")], [InlineKeyboardButton("👍 Reputation", callback_data="rep"), InlineKeyboardButton("👥 Friends", callback_data="friends")], [InlineKeyboardButton("🔄 Reactions", callback_data="reactions"), InlineKeyboardButton("🎁 Gifts", callback_data="gifts")], [InlineKeyboardButton("📤 Share", callback_data="share"), InlineKeyboardButton("🔵 Words Frequency", callback_data="words")], [InlineKeyboardButton("👥 Common Groups", callback_data="common")], [InlineKeyboardButton("📢 Broadcast", callback_data="broadcast")], [InlineKeyboardButton("💬 Message Manager", callback_data="message_manager")]]
            kb = admin_buttons + kb
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="main_menu")])
        await query.message.reply_text("➕ MORE COMMANDS", reply_markup=InlineKeyboardMarkup(kb))
    elif data == "converter":
        kb = [[InlineKeyboardButton("📄 PDF to Word", callback_data="pdf_to_word"), InlineKeyboardButton("🖼️ Image to Text", callback_data="image_to_text")], [InlineKeyboardButton("📄 Text to PDF", callback_data="text_to_pdf"), InlineKeyboardButton("🎨 Text to Image", callback_data="text_to_image")], [InlineKeyboardButton("🖼️ Image to PDF", callback_data="image_to_pdf"), InlineKeyboardButton("🖼️ Edit Photo", callback_data="image_edit")], [InlineKeyboardButton("🗣️ Text to Voice (ENG)", callback_data="tts_en"), InlineKeyboardButton("🗣️ Text to Voice (AM)", callback_data="tts_am")], [InlineKeyboardButton("📷 Image Format", callback_data="img_fmt_menu"), InlineKeyboardButton("📚 Document Format", callback_data="doc_fmt_menu")], [InlineKeyboardButton("🎙️ Voice to Text (ENG)", callback_data="voice_en"), InlineKeyboardButton("🎙️ Voice to Text (AM)", callback_data="voice_am")], [InlineKeyboardButton("📱 QR Code", callback_data="qr_menu")], [InlineKeyboardButton("⬅️ Back", callback_data="more")]]
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
        context.user_data[PDF_DRAFT_KEY]=[]; context.user_data["state"]="awaiting_text_pdf"
        await query.message.reply_text("📄 TEXT TO PDF\n\nSend your first text. Add more or press Done.",reply_markup=text_pdf_keyboard())
    elif data == "pdftext_next":
        context.user_data["state"]="awaiting_text_pdf"; await query.message.reply_text("➕ Send the next text.")
    elif data == "pdftext_done":
        await finish_text_to_pdf(update,context)
    elif data == "pdftext_cancel":
        await cancel_text_to_pdf(update,context)
    elif data == "text_to_image":
        context.user_data["state"]="awaiting_text_to_image"; await query.message.reply_text("🎨 TEXT TO IMAGE\n\nDescribe the image you want. Example:\nA realistic lion on a mountain at sunset")
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
    if data == "edit_clear":
        context.user_data.pop("edit_image",None); context.user_data.pop("bg_image",None); context.user_data["state"]=None
        await query.message.reply_text("🗑️ Current photo removed. Send another photo to edit."); return

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
        kb = [[InlineKeyboardButton("🖼️ Original", callback_data="edit_orig"), InlineKeyboardButton("✨ HD 100x", callback_data="edit_hd"), InlineKeyboardButton("🎨 Vivid", callback_data="edit_vivid")], [InlineKeyboardButton("⬛ B&W", callback_data="edit_bw"), InlineKeyboardButton("🟤 Sepia", callback_data="edit_sepia"), InlineKeyboardButton("🔪 Sharpen", callback_data="edit_sharp")], [InlineKeyboardButton("☀️ Brighten", callback_data="edit_bright"), InlineKeyboardButton("🌙 Darken", callback_data="edit_dark"), InlineKeyboardButton("🌫️ Blur", callback_data="edit_blur")], [InlineKeyboardButton("🟥 Pixel", callback_data="edit_pixel"), InlineKeyboardButton("🔄 Invert", callback_data="edit_invert"), InlineKeyboardButton("✏️ Sketch", callback_data="edit_sketch")], [InlineKeyboardButton("🧊 Emboss", callback_data="edit_emboss"), InlineKeyboardButton("🎞️ Poster", callback_data="edit_poster"), InlineKeyboardButton("🔥 Solarize", callback_data="edit_solar")], [InlineKeyboardButton("🗑️ Remove", callback_data="edit_clear"), InlineKeyboardButton("🖼️ Remove BG", callback_data="edit_remove_bg"), InlineKeyboardButton("🖼️ Change BG", callback_data="edit_change_bg")]]
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

# --- TEXT TO PDF ---
PDF_DRAFT_KEY = "text_pdf_items"

def text_pdf_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Enter Next Text", callback_data="pdftext_next"), InlineKeyboardButton("✅ Done", callback_data="pdftext_done")],
        [InlineKeyboardButton("❌ Cancel", callback_data="pdftext_cancel")]
    ])

def get_pdf_unicode_font():
    font_name = "BotUnicodeFont"
    try:
        pdfmetrics.getFont(font_name)
        return font_name
    except KeyError:
        pass
    font_candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
        "/usr/local/share/fonts/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for font_path in font_candidates:
        if os.path.exists(font_path):
            try:
                pdfmetrics.registerFont(TTFont(font_name, font_path))
                print(f"[Text to PDF] Unicode font loaded: {font_path}")
                return font_name
            except Exception:
                continue
    raise RuntimeError("No Unicode font was found on the server. Please install DejaVu Sans in Render.")

async def handle_text_pdf_input(update, context):
    if context.user_data.get("state") != "awaiting_text_pdf":
        return
    text = (update.message.text or "").strip()
    if not text:
        await update.message.reply_text("❌ Please send some text.")
        return
    items = context.user_data.setdefault(PDF_DRAFT_KEY, [])
    items.append(text)
    await update.message.reply_text(
        f"✅ Text {len(items)} added.\n\n➕ Press **Enter Next Text** to add another text.\n✅ Press **Done** when you are finished.",
        reply_markup=text_pdf_keyboard(),
        parse_mode=ParseMode.MARKDOWN
    )

async def finish_text_to_pdf(update, context):
    query = update.callback_query
    try: await query.answer()
    except: pass
    items = context.user_data.get(PDF_DRAFT_KEY, [])
    if not items:
        await query.message.reply_text("❌ No text has been added yet.")
        return
    status = await query.message.reply_text("⏳ Creating PDF...\n\nPlease wait.")
    path = None
    try:
        fd, path = tempfile.mkstemp(prefix="text_pdf_", suffix=".pdf")
        os.close(fd)
        font_name = get_pdf_unicode_font()
        styles = getSampleStyleSheet()
        body = ParagraphStyle("UnicodeBody", parent=styles["BodyText"], fontName=font_name, fontSize=12, leading=19, spaceAfter=14, alignment=0, wordWrap="LTR")
        doc = SimpleDocTemplate(path, pagesize=letter, rightMargin=54, leftMargin=54, topMargin=54, bottomMargin=54, title="Text to PDF", author="Telegram Bot")
        story = []
        for index, item in enumerate(items):
            if not item: continue
            safe_text = html.escape(item)
            safe_text = safe_text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br/>")
            story.append(Paragraph(safe_text, body))
            if index < len(items) - 1:
                story.append(Spacer(1, 8))
        doc.build(story)
        with open(path, "rb") as pdf_file:
            await query.message.reply_document(document=pdf_file, filename="text_document.pdf", caption=f"✅ **PDF created successfully!**\n\n📝 Text messages: {len(items)}\n🌍 English + Amharic supported.", reply_markup=tool_done_kb(), parse_mode=ParseMode.MARKDOWN)
        await status.edit_text("✅ Text → PDF completed successfully!")
    except Exception as e:
        print(f"[Text to PDF ERROR] {type(e).__name__}: {e}")
        try:
            await status.edit_text(f"❌ **Text → PDF failed.**\n\nError:\n{html.escape(str(e)[:1500])}", parse_mode=ParseMode.HTML)
        except: pass
    finally:
        context.user_data.pop(PDF_DRAFT_KEY, None)
        context.user_data["state"] = None
        if path:
            try: os.unlink(path)
            except OSError: pass

async def cancel_text_to_pdf(update, context):
    query = update.callback_query
    try: await query.answer()
    except: pass
    context.user_data.pop(PDF_DRAFT_KEY, None)
    context.user_data["state"] = None
    await query.edit_message_text("❌ Text to PDF cancelled.")

# --- TEXT TO IMAGE ---
HF_TOKEN=os.environ.get("HF_TOKEN","")
TEXT_TO_IMAGE_MODEL=os.environ.get("TEXT_TO_IMAGE_MODEL","black-forest-labs/FLUX.1-schnell")
async def handle_text_to_image(update, context):
    if context.user_data.get("state") != "awaiting_text_to_image": return
    prompt=(update.message.text or "").strip()
    if not prompt: await update.message.reply_text("❌ Please describe the image you want."); return
    if len(prompt)>2000: await update.message.reply_text("❌ Prompt is too long. Keep it under 2000 characters."); return
    status=await update.message.reply_text("🎨 Generating your image…")
    try:
        if not HF_TOKEN: await status.edit_text("❌ Text-to-Image needs HF_TOKEN in Render Environment Variables."); return
        def generate():
            from huggingface_hub import InferenceClient
            return InferenceClient(provider="auto",api_key=HF_TOKEN).text_to_image(prompt=prompt,model=TEXT_TO_IMAGE_MODEL)
        image=await asyncio.to_thread(generate); out=BytesIO(); image.save(out,format="PNG"); out.seek(0)
        await update.message.reply_photo(photo=out,caption=f"🎨 Generated image\n\nPrompt: {prompt[:900]}",reply_markup=tool_done_kb()); await status.edit_text("✅ Image generated successfully!")
    except Exception as e: await status.edit_text("❌ Text-to-Image failed.\n\n"+html.escape(str(e)[:1500]),parse_mode=ParseMode.HTML)
    finally: context.user_data["state"]=None

# --- VOICE TO TEXT: FASTER-WHISPER (NO GROQ) ---
_WHISPER_MODEL=None; _WHISPER_LOCK=threading.Lock()
WHISPER_MODEL_NAME=os.environ.get("WHISPER_MODEL","small")
WHISPER_DEVICE=os.environ.get("WHISPER_DEVICE","cpu")
WHISPER_COMPUTE_TYPE=os.environ.get("WHISPER_COMPUTE_TYPE","int8")
def _get_whisper_model():
    global _WHISPER_MODEL
    if _WHISPER_MODEL is not None: return _WHISPER_MODEL
    with _WHISPER_LOCK:
        if _WHISPER_MODEL is None:
            from faster_whisper import WhisperModel
            _WHISPER_MODEL=WhisperModel(WHISPER_MODEL_NAME,device=WHISPER_DEVICE,compute_type=WHISPER_COMPUTE_TYPE)
    return _WHISPER_MODEL
def _transcribe_whisper_sync(wav_path,language_code):
    model=_get_whisper_model(); segments,info=model.transcribe(wav_path,language=language_code,beam_size=5,vad_filter=True,condition_on_previous_text=True)
    return " ".join(x.text.strip() for x in segments).strip(),getattr(info,"language",language_code)
async def handle_voice_to_text(update, context, language):
    if not update.message.voice and not update.message.audio: await update.message.reply_text("❌ Please send a voice message or audio file."); return
    status=await update.message.reply_text("⏳ Transcribing with Whisper AI… First use may take longer while the model loads."); paths=[]
    try:
        file_id=update.message.voice.file_id if update.message.voice else update.message.audio.file_id
        tg=await context.bot.get_file(file_id); data=BytesIO(); await tg.download_to_memory(data); data.seek(0)
        with tempfile.NamedTemporaryFile(delete=False,suffix=".ogg") as f: f.write(data.read()); ogg=f.name; paths.append(ogg)
        with tempfile.NamedTemporaryFile(delete=False,suffix=".wav") as f: wav=f.name; paths.append(wav)
        ff=imageio_ffmpeg.get_ffmpeg_exe(); subprocess.run([ff,"-y","-i",ogg,"-ar","16000","-ac","1",wav],check=True,capture_output=True)
        text,detected=await asyncio.to_thread(_transcribe_whisper_sync,wav,language.split("-")[0].lower())
        if not text: await status.edit_text("❌ Whisper could not detect understandable speech.")
        else: await update.message.reply_text(f"📝 Transcribed Text (Whisper):\n\n{text}",reply_markup=tool_done_kb()); await status.edit_text(f"✅ Transcription complete! Language: {detected}")
    except Exception as e: await status.edit_text("❌ Whisper transcription failed.\n\n"+html.escape(str(e)[:1500]),parse_mode=ParseMode.HTML)
    finally:
        for x in paths:
            try: os.unlink(x)
            except OSError: pass
        context.user_data["state"]=None

# --- VIDEO DOWNLOADER CODE (KEPT AS IS) ---
# ... [All your existing video downloader functions here] ...

# --- MAIN HANDLER (MODIFIED TO ADD QR + MM inputs) ---
async def handle_link(update, context):
    user_id = update.effective_user.id
    text = update.message.text if update.message.text else ""
    self_ping()
    
    # QR CODE INPUT (ADDED)
    if context.user_data.get("state") == "qr_scan":
        if update.message and update.message.photo:
            handled = await handle_qr_photo(update, context)
            if handled: return
    if context.user_data.get("state") == "qr_create":
        if update.message and (update.message.text or update.message.caption):
            handled = await handle_qr_create(update, context)
            if handled: return
    
    # MESSAGE MANAGER SPECIFIC USER INPUT (ADDED)
    if context.user_data.get("state") == "mm_specific_user":
        if update.message.text and update.message.text.strip().isdigit():
            target_user = int(update.message.text.strip())
            context.user_data["mm_target_user"] = target_user
            context.user_data["state"] = None
            await update.message.reply_text(f"👤 User {target_user} selected. Choose duration:", reply_markup=mm_duration_keyboard("block_user"))
            return
    
    if context.user_data.get('state') == 'awaiting_text_pdf': await handle_text_pdf_input(update, context); return
    if context.user_data.get('state') == 'awaiting_text_to_image': await handle_text_to_image(update, context); return
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
    # Register Message Manager callback handler as a fallback
    bot_app.add_handler(CallbackQueryHandler(handle_message_manager_callback, pattern="^mm_"))
    print("Bot running with upgraded multi-source video downloader...")
    await bot_app.initialize(); await bot_app.start(); await bot_app.updater.start_polling()
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=10000), daemon=True).start()
    await asyncio.Event().wait()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"Bot crashed: {e}")
