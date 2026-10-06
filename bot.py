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
import uuid
import shutil
from io import BytesIO
import json
import hashlib
import base64
import speech_recognition as sr
import numpy as np
import cv2
from datetime import datetime, timedelta, timezone
from flask import Flask
from PIL import Image, ImageEnhance, ImageFilter, ImageOps, ImageChops
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile, InlineQueryResultArticle, InputTextMessageContent
from telegram.constants import ParseMode
from telegram.error import Conflict
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler, ContextTypes, InlineQueryHandler
from telethon import TelegramClient, events, functions, types
from telethon.sessions import StringSession
from telethon.utils import get_peer_id
from datetime import datetime, timedelta
from telethon import TelegramClient, events, functions, types
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
from PIL import ImageDraw
import yt_dlp
import imageio_ffmpeg
import qrcode
from ai_media_detector import analyze_image_bytes, analyze_audio_bytes, format_detection_result, DetectorError

try:
    from telethon.tl.functions.stories import GetPeerStoriesRequest, GetStoriesByIDRequest
except ImportError:
    GetPeerStoriesRequest = None
    GetStoriesByIDRequest = None

try:
    from telethon.tl.functions.stories import GetPinnedStoriesRequest
except ImportError:
    GetPinnedStoriesRequest = None

try:
    from telethon.tl.functions.stories import GetStoryViewsListRequest, GetStoriesArchiveRequest
except ImportError:
    GetStoryViewsListRequest = None
    GetStoriesArchiveRequest = None

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
    c.execute('''CREATE TABLE IF NOT EXISTS profile_visits (
        target_id INTEGER NOT NULL,
        visitor_id INTEGER NOT NULL,
        username TEXT,
        first_name TEXT,
        last_name TEXT,
        first_seen TEXT NOT NULL,
        last_seen TEXT NOT NULL,
        visit_count INTEGER NOT NULL DEFAULT 1,
        PRIMARY KEY (target_id, visitor_id)
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS message_manager_rules (id INTEGER PRIMARY KEY CHECK (id=1), messaging INTEGER DEFAULT 1, block_everyone_until REAL DEFAULT 0, blocked_users TEXT DEFAULT '{}', filter_links_until REAL DEFAULT 0, filter_videos_until REAL DEFAULT 0, keyword_rules TEXT DEFAULT '{}')''')
    c.execute("INSERT OR IGNORE INTO message_manager_rules (id) VALUES (1)")
    c.execute("DROP TABLE IF EXISTS chat_locks")
    c.execute('''CREATE TABLE IF NOT EXISTS auto_responder (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trigger_text TEXT NOT NULL,
        response_text TEXT NOT NULL,
        match_type TEXT NOT NULL DEFAULT 'exact',
        enabled INTEGER NOT NULL DEFAULT 1,
        created_by INTEGER,
        created_at TEXT
    )''')
    conn.commit()
    conn.close()


# --- CONTENT SAFETY FILTER ---
SAFETY_MAX_TERMS = 100

def safety_config():
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS safety_settings (id INTEGER PRIMARY KEY CHECK(id=1), enabled INTEGER NOT NULL DEFAULT 0)")
    c.execute("INSERT OR IGNORE INTO safety_settings (id, enabled) VALUES (1, 0)")
    c.execute("CREATE TABLE IF NOT EXISTS safety_terms (term TEXT PRIMARY KEY COLLATE NOCASE)")
    conn.commit()
    c.execute("SELECT enabled FROM safety_settings WHERE id=1")
    enabled = bool(c.fetchone()[0])
    c.execute("SELECT term FROM safety_terms ORDER BY term COLLATE NOCASE")
    terms = [row[0] for row in c.fetchall()]
    conn.close()
    return enabled, terms

async def safety_command(update, context):
    msg = update.effective_message
    user = update.effective_user
    if not user or not is_admin(user.id):
        await msg.reply_text("🔒 Admin only.")
        return
    args = context.args or []
    action = args[0].lower() if args else "help"
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS safety_settings (id INTEGER PRIMARY KEY CHECK(id=1), enabled INTEGER NOT NULL DEFAULT 0)")
    c.execute("INSERT OR IGNORE INTO safety_settings (id, enabled) VALUES (1, 0)")
    c.execute("CREATE TABLE IF NOT EXISTS safety_terms (term TEXT PRIMARY KEY COLLATE NOCASE)")
    if action == "add" and len(args) > 1:
        term = " ".join(args[1:]).strip()[:100]
        c.execute("SELECT COUNT(*) FROM safety_terms")
        if c.fetchone()[0] >= SAFETY_MAX_TERMS:
            await msg.reply_text("The saved filter list is full (100 terms).")
        else:
            c.execute("INSERT OR IGNORE INTO safety_terms(term) VALUES (?)", (term,))
            conn.commit()
            await msg.reply_text("✅ Saved filter phrase: " + html.escape(term))
    elif action == "remove" and len(args) > 1:
        term = " ".join(args[1:]).strip()
        c.execute("DELETE FROM safety_terms WHERE term=? COLLATE NOCASE", (term,))
        conn.commit()
        await msg.reply_text("✅ Removed phrase if it was on the list.")
    elif action in ("on", "off"):
        c.execute("UPDATE safety_settings SET enabled=? WHERE id=1", (1 if action == "on" else 0,))
        conn.commit()
        await msg.reply_text("🛡️ Group safety filter is now " + action.upper() + ".")
    elif action == "list":
        c.execute("SELECT term FROM safety_terms ORDER BY term COLLATE NOCASE")
        terms = [row[0] for row in c.fetchall()]
        c.execute("SELECT enabled FROM safety_settings WHERE id=1")
        enabled = bool(c.fetchone()[0])
        await msg.reply_text("🛡️ Filter: " + ("ON" if enabled else "OFF") + "\\n" + ("\\n".join("• " + html.escape(x) for x in terms) if terms else "No saved phrases."))
    else:
        await msg.reply_text("Safety controls (admin):\\n/safety add <word or phrase>\\n/safety remove <phrase>\\n/safety list\\n/safety on\\n/safety off\\n\\nWhen enabled, a matching group message is deleted if possible and the bot leaves that group. Start with specific phrases to reduce false matches.")
    conn.close()

async def safety_group_monitor(update, context):
    msg = update.effective_message
    chat = update.effective_chat
    if not msg or not chat or chat.type not in ("group", "supergroup"):
        return
    enabled, terms = safety_config()
    if not enabled or not terms:
        return
    body = (msg.text or msg.caption or "").casefold()
    if not body or not any(term.casefold() in body for term in terms):
        return
    try:
        await msg.delete()
    except Exception:
        pass
    try:
        await context.bot.leave_chat(chat_id=chat.id)
    except Exception:
        pass

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
    """Record a profile snapshot only when the user's visible name/username changes."""
    username = username or None
    first_name = first_name or ""
    last_name = last_name or ""

    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute(
        "SELECT username, first_name, last_name FROM user_history "
        "WHERE user_id = ? ORDER BY date DESC LIMIT 1",
        (int(user_id),)
    )
    previous = c.fetchone()

    current = (username, first_name, last_name)
    if previous != current:
        c.execute(
            "INSERT INTO user_history "
            "(user_id, username, first_name, last_name, date) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                int(user_id),
                username,
                first_name,
                last_name,
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
            ),
        )

    conn.commit()
    conn.close()

def get_user_history(user_id):
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute("SELECT username, first_name, last_name, date FROM user_history WHERE user_id = ? ORDER BY date DESC", (user_id,))
    rows = c.fetchall()
    conn.close()
    return rows

def record_profile_visit(target_id, visitor):
    """Record a profile visit made through this bot's Profile lookup flow."""
    if visitor is None or getattr(visitor, "id", None) is None:
        return
    now = datetime.now().isoformat(timespec="seconds")
    visitor_id = int(visitor.id)
    username = getattr(visitor, "username", None)
    first_name = getattr(visitor, "first_name", None) or ""
    last_name = getattr(visitor, "last_name", None) or ""
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute(
        """INSERT INTO profile_visits
           (target_id, visitor_id, username, first_name, last_name, first_seen, last_seen, visit_count)
           VALUES (?, ?, ?, ?, ?, ?, ?, 1)
           ON CONFLICT(target_id, visitor_id) DO UPDATE SET
             username=excluded.username,
             first_name=excluded.first_name,
             last_name=excluded.last_name,
             last_seen=excluded.last_seen,
             visit_count=profile_visits.visit_count + 1""",
        (int(target_id), visitor_id, username, first_name, last_name, now, now)
    )
    conn.commit()
    conn.close()

def get_profile_visits(target_id):
    """Return only lookup records for the exact Telegram account whose profile is being viewed."""
    try:
        owner_id = int(target_id)
    except (TypeError, ValueError):
        return []
    conn = sqlite3.connect('bot_data.db')
    try:
        c = conn.cursor()
        c.execute(
            """SELECT visitor_id, username, first_name, last_name, first_seen, last_seen, visit_count
               FROM profile_visits WHERE target_id = ? ORDER BY last_seen DESC""",
            (owner_id,)
        )
        return c.fetchall()
    finally:
        conn.close()
# ─────────────────────────────────────────────────────────────
# AUTO-RESPONDER  (all authenticated users)
# ─────────────────────────────────────────────────────────────
AR_MAX_RULES = 200

def ar_add_rule(trigger, response, match_type, user_id):
    trigger = (trigger or "").strip()
    response = (response or "").strip()
    if not trigger or not response:
        return None
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM auto_responder")
    if c.fetchone()[0] >= AR_MAX_RULES:
        conn.close()
        return None
    c.execute(
        "INSERT INTO auto_responder (trigger_text, response_text, match_type, enabled, created_by, created_at) "
        "VALUES (?, ?, ?, 1, ?, ?)",
        (trigger, response, match_type, user_id, str(datetime.now()))
    )
    rule_id = c.lastrowid
    conn.commit()
    conn.close()
    return rule_id

def ar_get_rules():
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute("SELECT id, trigger_text, response_text, match_type, enabled, created_by, created_at "
              "FROM auto_responder ORDER BY id DESC")
    rows = c.fetchall()
    conn.close()
    return rows

def ar_delete_rule(rule_id):
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute("DELETE FROM auto_responder WHERE id=?", (rule_id,))
    conn.commit()
    conn.close()

def ar_find_reply(text):
    text = (text or "").strip()
    if not text:
        return None
    text_l = text.lower()
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute("SELECT trigger_text, response_text, match_type FROM auto_responder WHERE enabled=1")
    rows = c.fetchall()
    conn.close()
    for trigger, response, match_type in rows:
        t = (trigger or "").strip().lower()
        if not t:
            continue
        if match_type == "exact" and text_l == t:
            return response
        if match_type == "contains" and t in text_l:
            return response
        if match_type == "startswith" and text_l.startswith(t):
            return response
    return None

def stats_counts():
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    out = {}
    for label, q in (
        ("users", "SELECT COUNT(*) FROM users"),
        ("started", "SELECT COUNT(*) FROM started_users"),
        ("inbox", "SELECT COUNT(*) FROM inbox"),
        ("history", "SELECT COUNT(*) FROM user_history"),
        ("ar_rules", "SELECT COUNT(*) FROM auto_responder"),
    ):
        try:
            c.execute(q)
            out[label] = c.fetchone()[0]
        except Exception:
            out[label] = 0
    conn.close()
    return out
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


def encode_multipart_formdata(fields, files):
    boundary = uuid.uuid4().hex
    body = b""
    for key, value in fields.items():
        body += f"--{boundary}\r\n".encode()
        body += f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode()
        body += f"{value}\r\n".encode()
    for key, file_data in files.items():
        body += f"--{boundary}\r\n".encode()
        body += f'Content-Disposition: form-data; name="{key}"; filename="image.jpg"\r\n'.encode()
        body += b"Content-Type: image/jpeg\r\n\r\n"
        body += file_data
        body += b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    return body, f"multipart/form-data; boundary={boundary}"

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
    conn = sqlite3.connect('bot_data.db')
    conn.execute("CREATE TABLE IF NOT EXISTS started_users (user_id INTEGER PRIMARY KEY, first_started TEXT NOT NULL)")
    conn.execute("INSERT OR IGNORE INTO started_users (user_id, first_started) VALUES (?, ?)",
                 (user_id, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()
    self_ping()
    if not is_authenticated(user_id):
        context.user_data['state'] = 'awaiting_password'
        await update.message.reply_text("🔐 TELEGRAM ASSISTANT\n\nPassword required.\nPlease enter the password to continue.")
        return
    if is_admin(user_id):
        keyboard = [
            [InlineKeyboardButton("📥 Inbox", callback_data="inbox"), InlineKeyboardButton("💬 Message Control", callback_data="message_manager")],
            [InlineKeyboardButton("👤 Profile", callback_data="profile")],
            [InlineKeyboardButton("👀 Profile Visitors", callback_data="profile_visitors"), InlineKeyboardButton("📖 Story Viewers", callback_data="story_viewers")],
            [InlineKeyboardButton("🔗 Fetch Telegram", callback_data="fetch")],
            [InlineKeyboardButton("🌍 Public Search", callback_data="search")],
            [InlineKeyboardButton("🤖 AI Content Check", callback_data="ai_content_check"), InlineKeyboardButton("🖼️ AI Image Check", callback_data="ai_image_check")],
            [InlineKeyboardButton("🎵 YouTube Video Audio Downloader", callback_data="youtube_audio_downloader")],
            [InlineKeyboardButton("➕ More Commands", callback_data="more")]
        ]
    else:
        keyboard = [
            [InlineKeyboardButton("👤 Profile", callback_data="profile")],
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
async def show_statistics(update, context):
    q = update.callback_query
    await q.answer()
    s = stats_counts()
    text = (
        "📊 <b>BOT STATISTICS</b>\n\n"
        f"👥 Total authenticated bot users: <b>{s['users']}</b>\n"
        f"📥 Inbox messages stored: <b>{s['inbox']}</b>\n"
        f"🗂️ Profile history entries: <b>{s['history']}</b>\n"
        f"🤖 Auto-responder rules: <b>{s['ar_rules']}</b>\n"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Refresh", callback_data="stats")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")],
    ])
    try:
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
    except Exception:
        await q.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
def ar_main_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Rule", callback_data="ar_add")],
        [InlineKeyboardButton("📋 View Rules", callback_data="ar_list")],
        [InlineKeyboardButton("⬅️ Back", callback_data="more")],
    ])

async def ar_show_menu(update, context):
    q = update.callback_query
    await q.answer()
    text = (
        "🤖 <b>AUTO RESPONDER</b>\n\n"
        "Set up automatic replies for private messages received by the connected "
        "Telegram account.\n\n"
        "<b>Example:</b>\n"
        "• Trigger: <code>hi</code>\n"
        "• Response: <code>Hello! How can I help you?</code>\n\n"
        "Only admins can create, view, or delete auto-response rules."
    )
    try:
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=ar_main_kb())
    except Exception:
        await q.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=ar_main_kb())

async def ar_start_add(update, context):
    q = update.callback_query
    await q.answer()
    for k in ("ar_trigger", "ar_response"):
        context.user_data.pop(k, None)
    context.user_data['state'] = 'ar_awaiting_trigger'
    await q.edit_message_text(
        "➕ <b>ADD AUTO-RESPONSE RULE</b>\n\n"
        "Step 1/3 — Send the <b>trigger</b> (the message that someone will send).\n\n"
        "Example: <code>hi</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="ar_cancel")]]),
    )

async def ar_handle_trigger(update, context):
    if context.user_data.get('state') != 'ar_awaiting_trigger':
        return
    trigger = (update.message.text or "").strip()
    if not trigger or len(trigger) > 200:
        await update.message.reply_text("❌ Trigger must be 1–200 characters.")
        return
    context.user_data['ar_trigger'] = trigger
    context.user_data['state'] = 'ar_awaiting_response'
    await update.message.reply_text(
        f"✅ Trigger saved: <code>{html.escape(trigger)}</code>\n\n"
        "Step 2/3 — Send the <b>response</b> that the bot will reply with.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="ar_cancel")]]),
    )

async def ar_handle_response(update, context):
    if context.user_data.get('state') != 'ar_awaiting_response':
        return
    response = (update.message.text or "").strip()
    if not response or len(response) > 3000:
        await update.message.reply_text("❌ Response must be 1–3000 characters.")
        return
    context.user_data['ar_response'] = response
    context.user_data['state'] = 'ar_awaiting_match_type'
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎯 Exact match", callback_data="ar_match|exact")],
        [InlineKeyboardButton("🔍 Contains", callback_data="ar_match|contains")],
        [InlineKeyboardButton("▶️ Starts with", callback_data="ar_match|startswith")],
        [InlineKeyboardButton("❌ Cancel", callback_data="ar_cancel")],
    ])
    await update.message.reply_text(
        "Step 3/3 — Choose how the trigger should match incoming messages.\n\n"
        "• <b>Exact</b> — message equals the trigger\n"
        "• <b>Contains</b> — message contains the trigger anywhere\n"
        "• <b>Starts with</b> — message begins with the trigger",
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
    )

async def ar_save_rule(update, context, match_type):
    q = update.callback_query
    await q.answer()
    trigger = context.user_data.get('ar_trigger')
    response = context.user_data.get('ar_response')
    if not trigger or not response:
        await q.edit_message_text(
            "❌ Session expired. Please start over.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="auto_responder")]]),
        )
        return
    rule_id = ar_add_rule(trigger, response, match_type, update.effective_user.id)
    context.user_data.pop('ar_trigger', None)
    context.user_data.pop('ar_response', None)
    context.user_data['state'] = None
    if rule_id is None:
        await q.edit_message_text(
            "❌ Could not save the rule (rule limit reached or invalid input).",
            reply_markup=ar_main_kb(),
        )
        return
    await q.edit_message_text(
        f"✅ <b>Rule #{rule_id} saved!</b>\n\n"
        f"Trigger: <code>{html.escape(trigger)}</code>\n"
        f"Match: <b>{match_type}</b>\n"
        f"Response: {html.escape(response[:500])}",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Add Another", callback_data="ar_add")],
            [InlineKeyboardButton("📋 View Rules", callback_data="ar_list")],
            [InlineKeyboardButton("⬅️ Back", callback_data="auto_responder")],
        ]),
    )

async def ar_show_list(update, context):
    q = update.callback_query
    await q.answer()
    rules = ar_get_rules()
    if not rules:
        await q.edit_message_text(
            "📋 <b>AUTO-RESPONSE RULES</b>\n\nNo rules saved yet.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Add Rule", callback_data="ar_add")],
                [InlineKeyboardButton("⬅️ Back", callback_data="auto_responder")],
            ]),
        )
        return
    lines = ["📋 <b>AUTO-RESPONSE RULES</b>", ""]
    kb = []
    for rid, trigger, response, match_type, enabled, _uid, _ts in rules[:30]:
        mark = "🟢" if enabled else "🔴"
        lines.append(
            f"{mark} <b>#{rid}</b> [{match_type}]\n"
            f"Trigger: <code>{html.escape(trigger)}</code>\n"
            f"Response: {html.escape(response[:120])}\n"
        )
        kb.append([InlineKeyboardButton(f"🗑️ Delete #{rid}", callback_data=f"ar_delete|{rid}")])
    kb.append([InlineKeyboardButton("➕ Add Rule", callback_data="ar_add")])
    kb.append([InlineKeyboardButton("⬅️ Back", callback_data="auto_responder")])
    await q.edit_message_text("\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))

async def ar_delete(update, context, rule_id):
    q = update.callback_query
    await q.answer("Deleted")
    ar_delete_rule(rule_id)
    await ar_show_list(update, context)

async def ar_cancel(update, context):
    q = update.callback_query
    await q.answer()
    context.user_data.pop('ar_trigger', None)
    context.user_data.pop('ar_response', None)
    context.user_data['state'] = None
    await ar_show_menu(update, context)


async def whisper_inline_query(update, context):
    """Create a recipient-locked whisper inline result usable in any chat.

    Syntax after the bot username: <message> <recipient @username/ID>
    """
    query = update.inline_query
    if not query:
        return
    raw = (query.query or "").strip()
    parts = raw.split()
    if len(parts) < 2:
        await query.answer([], cache_time=0, is_personal=True)
        return

    # Match the common format: message text followed by recipient ID/username.
    target = parts[-1]
    secret = " ".join(parts[:-1]).strip()
    if not secret:
        await query.answer([], cache_time=0, is_personal=True)
        return
    try:
        if target.lstrip("@").isdigit():
            recipient_id = int(target.lstrip("@"))
            if recipient_id <= 0:
                raise ValueError("Invalid user ID")
            recipient_name = "the selected user"
        else:
            resolved = await context.bot.get_chat(target if target.startswith("@") else "@" + target)
            if getattr(resolved, "type", None) != "private":
                raise ValueError("Target is not a private user")
            recipient_id = resolved.id
            recipient_name = getattr(resolved, "first_name", None) or "the selected user"
    except Exception:
        await query.answer([], cache_time=0, is_personal=True)
        return

    token = uuid.uuid4().hex[:12]
    context.bot_data.setdefault("whisper_messages", {})[token] = {
        "recipient_id": recipient_id,
        "text": secret[:3500],
        "expires": time.time() + 86400
    }
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("👁️ Read content", callback_data="whisper|" + token)
    ]])
    result = InlineQueryResultArticle(
        id=token,
        title="💌 Send a whisper",
        description="Only " + recipient_name + " can reveal the content",
        input_message_content=InputTextMessageContent(
            "🔒 Whisper for " + recipient_name + ". Only they can read the content."
        ),
        reply_markup=keyboard,
    )
    await query.answer([result], cache_time=0, is_personal=True)

async def whisper_command(update, context):
    """Post a recipient-locked whisper using the connected Telegram user account.

    Usage: /whisper <group @username/ID> <recipient @username/ID> <message>
    Run this command in the bot's private chat.
    """
    user = update.effective_user
    message = update.effective_message
    if not user or not is_admin(user.id):
        await message.reply_text("⛔ Whisper is currently available to admins only.")
        return
    args = list(context.args or [])
    if message.chat.type != "private" or len(args) < 3:
        await message.reply_text(
            "Usage:\n/whisper <group @username/ID> <recipient @username/ID> <message>\n\n"
            "Example: /whisper @mygroup @sam Hello, this is for you."
        )
        return

    group_ref, recipient_ref = args[0], args[1]
    secret = " ".join(args[2:]).strip()
    if not secret:
        await message.reply_text("Add the whisper text after the group and recipient.")
        return

    status = await message.reply_text("🔎 Checking the group and recipient…")
    try:
        group = await telethon_client.get_entity(group_ref)
        recipient = await telethon_client.get_entity(recipient_ref)
        if not isinstance(group, (types.Chat, types.Channel)):
            raise ValueError("That identifier is not a group or channel.")
        if isinstance(group, types.Channel) and not getattr(group, "megagroup", False):
            raise ValueError("Whispers can only be posted in groups, not broadcast channels.")
        recipient_id = int(recipient.id)
        group_id = int(get_peer_id(group))
        token = uuid.uuid4().hex[:12]
        context.bot_data.setdefault("whisper_messages", {})[token] = {
            "recipient_id": recipient_id,
            "text": secret[:3500],
            "expires": time.time() + 86400
        }

        # The connected user account posts the visible placeholder.
        # The connected user account posts the placeholder first. The bot then
        # attaches its recipient-locked button as a reply, keeping the two
        # messages visually grouped in the chat.
        recipient_label = getattr(recipient, "first_name", None) or getattr(recipient, "title", None) or "the intended recipient"
        posted = await telethon_client.send_message(
            group,
            "🔒 Whisper for " + recipient_label + ". Only they can read the content."
        )
        # The bot supplies the recipient-locked reveal button as a reply.
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("👁️ Read content", callback_data="whisper|" + token)
        ]])
        await context.bot.send_message(
            chat_id=group_id,
            text="",
            reply_to_message_id=posted.id,
            reply_markup=keyboard
        )
        await status.edit_text("✅ Whisper posted. The visible placeholder was sent by your connected Telegram account.")
    except Exception as exc:
        await status.edit_text(
            "❌ Could not post the whisper. Check that the connected account can access the group "
            "and that the bot is a member of it.\n" + html.escape(f"{type(exc).__name__}: {str(exc)[:500]}")
        )

async def handle_whisper_callback(update, context, token):
    query = update.callback_query
    item = context.bot_data.get("whisper_messages", {}).get(token)
    if not item or item["expires"] < time.time():
        await query.answer("This whisper has expired.", show_alert=True)
        return
    if query.from_user.id != item["recipient_id"]:
        await query.answer("This whisper is only for its intended recipient.", show_alert=True)
        return
    await query.answer(item["text"], show_alert=True)

async def handle_word_frequency(update, context, raw_input):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🔒 Admin only.")
        return

    value = (raw_input or "").strip()
    if not value:
        await update.message.reply_text("❌ Send a Telegram @username or numeric user ID.")
        return

    try:
        target = await telethon_client.get_entity(value)
        if not isinstance(target, types.User):
            await update.message.reply_text("❌ That identifier is not a Telegram user.")
            return
    except Exception:
        await update.message.reply_text("❌ User not found. Try @username or numeric ID.")
        return

    status = await update.message.reply_text("⏳ Searching accessible Telegram chats for this user's messages…")
    counts = {}
    message_count = 0
    chat_count = 0
    max_dialogs = 100
    max_messages_per_chat = 500
    stop_words = {
        "the","and","for","that","this","with","you","your","are","was","were","have","has",
        "from","but","not","they","their","what","when","where","how","why","can","will",
        "all","about","into","just","than","then","there","here","its","it's","our","out",
        "who","which","would","could","should","been","being","his","her","him","she","them",
        "a","an","to","of","in","on","at","is","it","i","we","he","as","or","if","be","do",
        "did","so","no","yes","my","me","us"
    }

    try:
        async for dialog in telethon_client.iter_dialogs(limit=max_dialogs):
            entity = dialog.entity
            if not getattr(entity, "id", None):
                continue
            chat_count += 1
            try:
                async for msg in telethon_client.iter_messages(
                    entity,
                    limit=max_messages_per_chat,
                    from_user=target,
                ):
                    text_value = (getattr(msg, "message", None) or "").casefold()
                    if not text_value:
                        continue
                    message_count += 1
                    for word in re.findall(r"[\w']{2,}", text_value, flags=re.UNICODE):
                        word = word.strip("_'")
                        if not word or word in stop_words or word.isdigit():
                            continue
                        counts[word] = counts.get(word, 0) + 1
            except Exception:
                continue

        if not counts:
            await status.edit_text(
                "ℹ️ No readable text messages from that user were found in the chats "
                "available to the connected Telegram account."
            )
            return

        top = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:20]
        label = getattr(target, "username", None) or str(target.id)
        lines = [f"🔵 <b>WORD FREQUENCY</b>", f"👤 User: <code>{html.escape(label)}</code>",
                 f"💬 Messages analyzed: <b>{message_count}</b>", "", "📊 <b>Top words:</b>"]
        for index, (word, count) in enumerate(top, 1):
            lines.append(f"{index}. <code>{html.escape(word)}</code> — <b>{count}</b>")
        lines.append("")
        lines.append("ℹ️ Only messages visible to the connected Telegram account were analyzed.")
        await status.edit_text("\n".join(lines), parse_mode=ParseMode.HTML)
    except Exception as exc:
        await status.edit_text("❌ Word frequency search failed: " + html.escape(f"{type(exc).__name__}: {str(exc)[:500]}"),
                               parse_mode=ParseMode.HTML)


async def handle_channel_lookup(update, context, raw_input):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🔒 Admin only.")
        return

    value = (raw_input or "").strip()
    if not value:
        await update.message.reply_text("❌ Send a Telegram @username or numeric user ID.")
        return

    try:
        target = await telethon_client.get_entity(value)
        if not isinstance(target, types.User):
            await update.message.reply_text("❌ That identifier is not a Telegram user.")
            return
    except Exception:
        await update.message.reply_text("❌ User not found. Try @username or numeric ID.")
        return

    try:
        me = await telethon_client.get_me()
        if int(target.id) != int(me.id):
            await update.message.reply_text(
                "⚠️ Telegram does not provide a complete API for listing every channel "
                "another user has joined.\n\n"
                "For privacy and API limitations, this feature can list broadcast channels "
                "only for the connected Telegram account."
            )
            return

        status = await update.message.reply_text("⏳ Reading the connected Telegram account's channel list…")
        channels = []
        async for dialog in telethon_client.iter_dialogs():
            entity = dialog.entity
            if getattr(dialog, "is_channel", False) and not getattr(entity, "megagroup", False):
                channels.append(entity)

        if not channels:
            await status.edit_text("ℹ️ No broadcast channels were found in the connected account's dialogs.")
            return

        lines = [
            "📢 <b>CHANNELS JOINED / SUBSCRIBED</b>",
            f"👤 <code>{html.escape(getattr(me, 'username', None) or str(me.id))}</code>",
            f"📊 Total: <b>{len(channels)}</b>",
            ""
        ]
        for index, channel in enumerate(channels, 1):
            username = getattr(channel, "username", None)
            title = getattr(channel, "title", "Untitled")
            link = f"https://t.me/{username}" if username else "Private channel"
            lines.append(f"{index}. <b>{html.escape(title)}</b> — {html.escape(link)}")
        await status.edit_text("\n".join(lines), parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    except Exception as exc:
        await update.message.reply_text(
            "❌ Channel lookup failed: " + html.escape(f"{type(exc).__name__}: {str(exc)[:500]}"),
            parse_mode=ParseMode.HTML
        )


async def handle_name_history(update, context, raw_input):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🔒 Admin only.")
        return

    value = (raw_input or "").strip()
    try:
        target = await telethon_client.get_entity(value)
        if not isinstance(target, types.User):
            await update.message.reply_text("❌ That identifier is not a Telegram user.")
            return
    except Exception:
        await update.message.reply_text("❌ User not found. Try @username or numeric ID.")
        return

    status = await update.message.reply_text("⏳ Fetching Telegram profile history…")
    try:
        current_username = getattr(target, "username", None)
        current_first = getattr(target, "first_name", None) or ""
        current_last = getattr(target, "last_name", None) or ""

        # Telegram does not expose a complete historical username/name-change
        # timeline through the public API. We report what is actually available.
        country = "Unknown"
        phone = getattr(target, "phone", None)
        if phone:
            try:
                import phonenumbers
                parsed = phonenumbers.parse("+" + str(phone), None)
                country = phonenumbers.region_code_for_number(parsed) or "Unknown"
            except Exception:
                country = "Unknown"

        lines = [
            "🔗 <b>NAME / USERNAME HISTORY</b>",
            f"👤 User ID: <code>{target.id}</code>",
            f"🌍 Country: <b>{html.escape(country)}</b>",
            "",
            f"🟢 Current name: <b>{html.escape((current_first + ' ' + current_last).strip() or 'Unknown')}</b>",
            f"🔵 Current username: <b>@{html.escape(current_username) if current_username else 'None'}</b>",
            "",
            "📅 Account creation/join date: <b>Not available through Telegram's public API</b>",
            "",
            "⚠️ Telegram's public API does not provide a complete historical list of every "
            "previous first name, last name, or username a user has used. "
            "This bot will not invent historical data.",
        ]
        await status.edit_text("\n".join(lines), parse_mode=ParseMode.HTML)
    except Exception as exc:
        await status.edit_text(
            "❌ Name history lookup failed: " +
            html.escape(f"{type(exc).__name__}: {str(exc)[:500]}"),
            parse_mode=ParseMode.HTML
        )


async def menu_callback(update, context):
    query = update.callback_query
    user_id = update.effective_user.id
    if query.data and query.data.startswith("whisper|"):
        await handle_whisper_callback(update, context, query.data.split("|", 1)[1])
        return
    await query.answer()
    if not is_authenticated(user_id):
        await query.edit_message_text("🔐 Password required.")
        return

    data = query.data
    # Manager text-entry state must never leak into unrelated buttons.
    # Otherwise a later normal message is incorrectly interpreted as a user ID.
    if not (data.startswith("mm_") or data == "message_manager"):
        for _key in ("mm_input", "mm_duration_kind", "mm_reply_to", "mm_keyword_tokens"):
            context.user_data.pop(_key, None)
    if not data.startswith("ar_") and data != "auto_responder":
        if context.user_data.get('state') in ('ar_awaiting_trigger', 'ar_awaiting_response', 'ar_awaiting_match_type'):
            context.user_data['state'] = None
            context.user_data.pop('ar_trigger', None)
            context.user_data.pop('ar_response', None)
    if data.startswith("mm_") or data in {"message_manager"}:
        if await handle_mm_callback(update, context, data): return
    if data.startswith("vd_"):
        await handle_video_callback(update, context, data)
        return
    if data == "auto_responder":
        if not is_admin(user_id):
            await query.answer("🔒 Admin only", show_alert=True); return
        await ar_show_menu(update, context); return
    if data == "ar_add":
        if not is_admin(user_id):
            await query.answer("🔒 Admin only", show_alert=True); return
        await ar_start_add(update, context); return
    if data == "ar_list":
        if not is_admin(user_id):
            await query.answer("🔒 Admin only", show_alert=True); return
        await ar_show_list(update, context); return
    if data == "ar_cancel":
        if not is_admin(user_id):
            await query.answer("🔒 Admin only", show_alert=True); return
        await ar_cancel(update, context); return
    if data.startswith("ar_match|"):
        if not is_admin(user_id):
            await query.answer("🔒 Admin only", show_alert=True); return
        await ar_save_rule(update, context, data.split("|", 1)[1]); return
    if data.startswith("ar_delete|"):
        if not is_admin(user_id):
            await query.answer("🔒 Admin only", show_alert=True); return
        try:
            rid = int(data.split("|", 1)[1])
        except ValueError:
            await query.answer("Invalid rule", show_alert=True); return
        await ar_delete(update, context, rid); return
    if data == "words":
        if not is_admin(user_id):
            await query.answer("🔒 Admin only", show_alert=True)
            return
        context.user_data["state"] = "word_frequency"
        await query.message.reply_text(
            "🔵 <b>WORDS FREQUENCY</b>\n\n"
            "Send the user's <b>@username</b> or numeric <b>ID</b>.\n"
            "I will count the most frequent words in messages from that user "
            "that the connected Telegram account can access.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="more")]])
        )
        return
    if data == "channels":
        if not is_admin(user_id):
            await query.answer("🔒 Admin only", show_alert=True)
            return
        context.user_data["state"] = "channel_lookup"
        await query.message.reply_text(
            "📢 <b>CHANNELS</b>\n\n"
            "Send a Telegram <b>@username</b> or numeric <b>ID</b>.\n"
            "For privacy/API limitations, the full joined-channel list is available "
            "only for the connected Telegram account.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="more")]])
        )
        return
    if data == "names":
        if not is_admin(user_id):
            await query.answer("🔒 Admin only", show_alert=True)
            return
        context.user_data["state"] = "names"
        await query.message.reply_text(
            "🔗 <b>NAMES</b>\n\n"
            "Send the user's <b>@username</b> or numeric <b>ID</b>.\n"
            "I will show the country information available from the account, "
            "current profile name/username, and clearly identify Telegram history "
            "that the public API does not expose.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="more")]])
        )
        return
    if data == "youtube_audio_downloader":
        if not is_admin(user_id):
            await query.answer("🔒 Admin only", show_alert=True)
            return
        context.user_data["state"] = "admin_youtube_audio"
        await query.message.reply_text(
            "🎵 <b>YOUTUBE VIDEO AUDIO DOWNLOADER</b>\n\n"
            "Send the YouTube video link.\n"
            "I will download the audio and send it to you as an MP3 file.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="more")]])
        )
        return
    if data == "ai_image_check":
        context.user_data["state"] = "ai_image_check"
        await query.message.reply_text(
            "🖼️ <b>AI IMAGE CHECK</b>\n\n"
            "Send an image. I will check EXIF/XMP/C2PA metadata and run a free local AI-image model.\n\n"
            "No paid API or API key is used.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="more")]])
        )
        return
    if data == "ai_content_check":
        context.user_data["state"] = "ai_content_check"
        await query.message.reply_text(
            "🤖 <b>AI CONTENT CHECK</b>\n\n"
            "Send an image, voice message, audio file, or an image document.\n"
            "I’ll run the improved multi-stage detector and report AI likelihood, confidence, evidence, and warnings.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="more")]])
        )
        return
    if data == "stats":
        if not is_admin(user_id):
            await query.answer("🔒 Admin only", show_alert=True); return
        await show_statistics(update, context); return
    if data == "profile_visitors":
        await show_profile_visitors(update, context); return
    if data == "common":
        if not is_admin(user_id):
            await query.answer("🔒 Admin only", show_alert=True)
            return
        context.user_data.pop("common_groups_results", None)
        context.user_data.pop("common_groups_user", None)
        context.user_data["state"] = "common_query"
        await query.answer()
        await query.message.reply_text(
            "👥 COMMON GROUPS\n\n"
            "Send the Telegram @username or numeric user ID.\n\n"
            "Example: @username\n"
            "Example: 123456789"
        )
        return
    if data.startswith("common_page_"):
        try:
            page = max(0, int(data.removeprefix("common_page_")))
        except ValueError:
            page = 0
        await show_common_groups(update, context, page)
        return
    if data == "story_viewers":
        context.user_data.pop("admin_story_posts", None)
        await show_story_viewers(update, context, 0); return
    if data == "story_viewers_refresh":
        context.user_data.pop("admin_story_posts", None)
        await show_story_viewers(update, context, context.user_data.get("admin_story_index", 0)); return
    if data.startswith("admin_story_"):
        try:
            story_index = int(data.removeprefix("admin_story_"))
        except ValueError:
            await query.answer("Invalid story.", show_alert=True); return
        await show_story_viewers(update, context, story_index); return
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
        if is_admin(user_id):       
            keyboard = [[InlineKeyboardButton("📥 Inbox", callback_data="inbox"), InlineKeyboardButton("👤 Profile", callback_data="profile")], [InlineKeyboardButton("👀 Profile Visitors", callback_data="profile_visitors"), InlineKeyboardButton("📖 Story Viewers", callback_data="story_viewers")], [InlineKeyboardButton("🔗 Fetch Telegram", callback_data="fetch")], [InlineKeyboardButton("🌍 Public Search", callback_data="search")], [InlineKeyboardButton("➕ More Commands", callback_data="more")]]
        else:
            keyboard = [[InlineKeyboardButton("👤 Profile", callback_data="profile")], [InlineKeyboardButton("🔗 Fetch Telegram", callback_data="fetch")], [InlineKeyboardButton("🌍 Public Search", callback_data="search")], [InlineKeyboardButton("➕ More Commands", callback_data="more")]]
        await query.message.reply_text("🤖 TELEGRAM ASSISTANT", reply_markup=InlineKeyboardMarkup(keyboard))
    elif data == "more":
        kb = [
            [InlineKeyboardButton("🔎 Search", callback_data="search")],
            [InlineKeyboardButton("📄 PDF Fetch", callback_data="pdf_fetch")],
            [InlineKeyboardButton("🔄 Converter", callback_data="converter")],
            [InlineKeyboardButton("🎬 Video Downloader", callback_data="video_downloader")],
            [InlineKeyboardButton("🤖 AI Content Check", callback_data="ai_content_check")],
        ]
        if is_admin(user_id):
            admin_buttons = [
                [InlineKeyboardButton("🤖 Auto Responder", callback_data="auto_responder")],
                [InlineKeyboardButton("💬 Message Manager", callback_data="message_manager")],
                [InlineKeyboardButton("🕰️ Message Time Machine", callback_data="time_machine")],
                [InlineKeyboardButton("📊 Statistics", callback_data="stats")],
                [InlineKeyboardButton("👀 Profile Visitors", callback_data="profile_visitors"), InlineKeyboardButton("📖 Story Viewers", callback_data="story_viewers")],
                [InlineKeyboardButton("🔔 Track", callback_data="track"), InlineKeyboardButton("🔗 Names", callback_data="names")],
                [InlineKeyboardButton("👥 Groups", callback_data="groups"), InlineKeyboardButton("💬 Messages", callback_data="messages")],
                [InlineKeyboardButton("🔎 Analysis", callback_data="analysis"), InlineKeyboardButton("📢 Channels", callback_data="channels")],
                [InlineKeyboardButton("👍 Reputation", callback_data="rep"), InlineKeyboardButton("👥 Friends", callback_data="friends")],
                [InlineKeyboardButton("🔄 Reactions", callback_data="reactions"), InlineKeyboardButton("🎁 Gifts", callback_data="gifts")],
                [InlineKeyboardButton("📤 Share", callback_data="share"), InlineKeyboardButton("🔵 Words Frequency", callback_data="words")],
                [InlineKeyboardButton("👥 Common Groups", callback_data="common")],
                [InlineKeyboardButton("📢 Broadcast", callback_data="broadcast")],
            ]
            kb = admin_buttons + kb
        kb.append([InlineKeyboardButton("⬅️ Back", callback_data="main_menu")])
        await query.message.reply_text("➕ MORE COMMANDS", reply_markup=InlineKeyboardMarkup(kb))
    elif data == "converter":
        kb = [[InlineKeyboardButton("📄 PDF to Word", callback_data="pdf_to_word"), InlineKeyboardButton("🖼️ Image to Text", callback_data="image_to_text")], [InlineKeyboardButton("📄 Text to PDF", callback_data="text_to_pdf"), InlineKeyboardButton("🎨 Text to Image", callback_data="text_to_image")], [InlineKeyboardButton("🖼️ Image to PDF", callback_data="image_to_pdf"), InlineKeyboardButton("🖼️ Edit Photo", callback_data="image_edit")], [InlineKeyboardButton("🗣️ Text to Voice (ENG)", callback_data="tts_en"), InlineKeyboardButton("🗣️ Text to Voice (AM)", callback_data="tts_am")], [InlineKeyboardButton("📷 Image Format", callback_data="img_fmt_menu"), InlineKeyboardButton("📚 Document Format", callback_data="doc_fmt_menu")], [InlineKeyboardButton("🎙️ Voice to Text (ENG)", callback_data="voice_en"), InlineKeyboardButton("🎙️ Voice to Text (AM)", callback_data="voice_am")], [InlineKeyboardButton("🎵 Audio Tools", callback_data="audio_tools")], [InlineKeyboardButton("📱 QR Code", callback_data="qr_menu")], [InlineKeyboardButton("⬅️ Back", callback_data="more")]]
        await query.message.reply_text("🔄 MEDIA CONVERTER & AI TOOLS\n\nChoose an option:", reply_markup=InlineKeyboardMarkup(kb))
    elif data == "audio_tools":
        kb = [[InlineKeyboardButton("🎬 Video → Audio", callback_data="video_to_audio"),
               InlineKeyboardButton("✂️ Cut Audio", callback_data="cut_audio")],
              [InlineKeyboardButton("⬅️ Back", callback_data="converter")]]
        await query.message.reply_text("🎵 AUDIO TOOLS\n\nChoose a tool:", reply_markup=InlineKeyboardMarkup(kb))
    elif data == "video_to_audio":
        context.user_data["state"] = "audio_video_to_audio"
        await query.message.reply_text("🎬 VIDEO → AUDIO\n\nSend a video and I’ll extract its audio as MP3.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="audio_tools")]]))
    elif data == "cut_audio":
        context.user_data["audio_cut_start"] = None
        context.user_data["audio_cut_end"] = None
        context.user_data["state"] = "audio_cut_waiting_start"
        await query.message.reply_text(
            "✂️ <b>CUT AUDIO</b>\n\n"
            "⏱️ <b>Step 1 of 3 — Starting point</b>\n\n"
            "Where should the audio start?\n"
            "Enter <b>minutes:seconds</b>.\n\n"
            "Examples: <code>0:30</code> • <code>2:15</code> • <code>10:00</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="audio_tools")]]),
        )
    elif data == "qr_menu":
        kb=[[InlineKeyboardButton("📷 Scan / Read QR",callback_data="qr_scan"),InlineKeyboardButton("➕ Create QR",callback_data="qr_create")],[InlineKeyboardButton("⬅️ Back",callback_data="converter")]]
        await query.message.reply_text("📱 QR CODE\n\nScan any readable QR image, or create a QR code from text/link.",reply_markup=InlineKeyboardMarkup(kb))
    elif data == "qr_scan":
        # Explicit scan action was previously missing from the callback router.
        # That made the button appear to do nothing.
        context.user_data.pop("mm_input", None)
        context.user_data.pop("mm_duration_kind", None)
        context.user_data.pop("mm_reply_to", None)
        context.user_data['state']='qr_scan'
        await query.answer("Send a QR image")
        await query.message.reply_text(
            "📷 SCAN / READ QR\n\n"
            "Send a photo containing a QR code.\n"
            "You can also send the QR image as an image document."
        )
    elif data == "qr_create":
        context.user_data.pop("mm_input", None)
        context.user_data.pop("mm_duration_kind", None)
        context.user_data.pop("mm_reply_to", None)
        context.user_data['state']='qr_create'; await query.message.reply_text("➕ CREATE QR\n\nSend any text or link to turn it into a QR code.")
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
    elif data == "names":
        if not is_admin(user_id):
            await query.answer("🔒 Admin only", show_alert=True)
            return
        context.user_data['state'] = 'names'
        await query.message.reply_text("🔗 NAMES & USERNAME HISTORY\n\nEnter a Telegram username or numeric user ID:")
    elif data == "time_machine":
        if not is_admin(user_id):
            await query.answer("🔒 Admin only", show_alert=True); return
        context.user_data["state"] = "tm_group"
        await query.message.reply_text("🕰️ MESSAGE TIME MACHINE\\n\\n1/3 Send the group or channel @username, public link, or numeric chat ID. The connected Telegram account must have access.")
    elif data == "profile":
        await query.message.reply_text("👤 PROFILE\n\nEnter a Telegram username or ID to generate the Profile Card:")
        context.user_data['state'] = 'profile_query'
    elif data == "fetch":
        await query.message.reply_text("🔗 Fetch Telegram\n\nSend me a link (e.g., t.me/channel/123 or t.me/channel/123-130):")
        context.user_data['state'] = 'fetch_link'
    elif data == "search":
        await query.message.reply_text("🔎 PUBLIC SEARCH\n\nEnter a keyword, hashtag, @username, or public channel name.\n\n🌐 Public groups/channels only — no private dialogs, no admin-only history.\n⚡ Parallel discovery + direct public-history search + relevance ranking.\nExample: Logic mid")
        context.user_data['state'] = 'search_query'
    elif data == "pdf_fetch":
        await query.message.reply_text("📄 PDF FETCH\n\nPlease upload the PDF file directly to this chat.")
        context.user_data['state'] = 'awaiting_pdf'
    elif data.startswith("posts_"):
        try: page=max(1,int(data.split("_",1)[1]))
        except Exception: page=1
        await handle_posts_pagination(update, context, page)
        return
    # Story controls: navigation must be checked BEFORE the generic story action.
    elif data == "story_start":
        await handle_story_view(update, context)
        return
    elif data.startswith("story_nav_"):
        try:
            idx = int(data.split("_", 2)[2])
            lock=context.user_data.get("story_lock")
            if lock is None:
                lock=asyncio.Lock()
                context.user_data["story_lock"]=lock
            async with lock:
                await send_story_at_index(update, context, idx)
        except (ValueError, IndexError):
            await query.answer("Invalid story navigation.", show_alert=True)
        return
    elif data == "story_refresh":
        await handle_story_view(update, context, refresh=True)
        return
    elif data == "story_back":
        await restore_story_profile(update, context)
        return
# --- PHOTO EDITING ---
def build_edit_keyboard(page=1):
    """Returns an InlineKeyboardMarkup for the given page."""
    if page == 1:
        buttons = [
            [("🖼️ Original", "edit_orig"), ("✨ HD Pro", "edit_hd"), ("🎨 Vivid", "edit_vivid")],
            [("⬛ B&W", "edit_bw"), ("🟤 Sepia", "edit_sepia"), ("🔪 Sharpen", "edit_sharp")],
            [("☀️ Brighten", "edit_bright"), ("🌙 Darken", "edit_dark"), ("🌫️ Blur", "edit_blur")],
            [("🟥 Pixel", "edit_pixel"), ("🔄 Invert", "edit_invert"), ("✏️ Sketch", "edit_sketch")],
            [("🧊 Emboss", "edit_emboss"), ("🎞️ Poster", "edit_poster"), ("🔥 Solarize", "edit_solar")],
            [("✂️ Crop Photo", "edit_crop"), ("🪄 Remove Background", "edit_remove_bg")],
            [("➡️ More Effects", "edit_page2"), ("🗑️ Remove Photo", "edit_clear")],
        ]
    else:  # page 2
        buttons = [
            [("🎭 Cartoon", "edit_cartoon"), ("🖌️ Oil Paint", "edit_oil"), ("💧 Watercolor", "edit_watercolor")],
            [("✨ Glow", "edit_glow"), ("💡 Neon", "edit_neon"), ("📻 Vintage", "edit_vintage")],
            [("🪞 Mirror", "edit_mirror"), ("↕️ Flip", "edit_flip"), ("🔄 Rotate 90°", "edit_rotate")],
            [("🌑 Vignette", "edit_vignette"), ("📈 High Contrast", "edit_contrast"), ("🎨 Desaturate", "edit_saturation")],
            [("⬅️ Back", "edit_page1"), ("🗑️ Remove Photo", "edit_clear")],
        ]
    
    keyboard = [
        [InlineKeyboardButton(text, callback_data=cb) for text, cb in row]
        for row in buttons
    ]
    return InlineKeyboardMarkup(keyboard)


async def handle_photo_edit_selection(update, context, data):
    query = update.callback_query
    await query.answer()

    if not context.user_data.get('edit_image'):
        await query.message.reply_text("❌ No image found. Please send a photo first.")
        return

    img = context.user_data['edit_image']

    # Handle navigation and special actions
    if data == "edit_clear":
        context.user_data.pop("edit_image", None)
        context.user_data.pop("bg_image", None)
        context.user_data["state"] = None
        await query.message.reply_text("🗑️ Current photo removed. Send another photo to edit.")
        return

    if data == "edit_crop":
        context.user_data["state"] = "awaiting_crop_width"
        await query.message.reply_text("✂️ CROP PHOTO\n\nSend the target width in pixels (1–5000). The image will be center-cropped to the exact dimensions.")
        return

    if data == "edit_remove_bg":
        status = await query.message.reply_text("🪄 Removing background… This may take a little while on the first run.")
        try:
            def remove_background_sync():
                from rembg import remove, new_session
                session = new_session("u2netp")
                return remove(img.convert("RGBA"), session=session)
            result = await asyncio.to_thread(remove_background_sync)
            if not isinstance(result, Image.Image):
                result = Image.open(BytesIO(result))
            out = BytesIO()
            result.save(out, format="PNG")
            out.seek(0)
            await query.message.reply_document(
                document=out, filename="background_removed.png",
                caption="✅ Background removed. Transparent areas are preserved.",
                reply_markup=tool_done_kb()
            )
            await status.edit_text("✅ Background removal complete.")
        except Exception as e:
            await status.edit_text("❌ Background removal failed.\n\n" + html.escape(str(e)[:1200]), parse_mode=ParseMode.HTML)
        return

    if data == "edit_change_bg":
        await query.message.reply_text("ℹ️ Background changing is disabled in this version.")
        return

    if data == "edit_remove_bg_legacy":
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
            await query.message.reply_document(
                document=out_bytes,
                filename="no_bg.png",
                caption="✅ Background removed!",
                reply_markup=tool_done_kb()
            )
        except Exception as e:
            await query.message.reply_text(f"❌ Background removal failed: {e}")
        return

    # Page navigation
    if data in ("edit_page1", "edit_page2"):
        page = 1 if data == "edit_page1" else 2
        kb = build_edit_keyboard(page=page)
        await query.edit_message_text("🎨 Choose an editing effect:", reply_markup=kb)
        return

    # Normalise image size
    max_side = 4096
    if img.width >= img.height:
        new_width = max_side
        new_height = int(img.height * (max_side / img.width))
    else:
        new_height = max_side
        new_width = int(img.width * (max_side / img.height))
    img = img.resize((new_width, new_height), Image.LANCZOS)

    filter_name = "Original"
    description = ""

    # ─── Apply selected effect ───
    if data == "edit_orig":
        filter_name = "Original"
        description = "No changes applied."
    elif data == "edit_hd":
        # High-clarity enhancement. Upscale first, then clean luminance noise,
        # enhance local contrast, and sharpen fine detail while limiting halos.
        src = img.convert("RGB")
        max_dim = 5000
        scale = min(2.0, max_dim / max(src.size))
        nw = max(1, int(round(src.width * scale)))
        nh = max(1, int(round(src.height * scale)))
        if (nw, nh) != src.size:
            src = src.resize((nw, nh), Image.Resampling.LANCZOS)

        try:
            arr = np.asarray(src)
            ycrcb = cv2.cvtColor(arr, cv2.COLOR_RGB2YCrCb)
            y, cr, cb = cv2.split(ycrcb)
            # Noise reduction before contrast/sharpening prevents grain amplification.
            y = cv2.fastNlMeansDenoising(
                y, None, h=6, templateWindowSize=7, searchWindowSize=21
            )
            # Moderate adaptive contrast improves detail in shadows and highlights.
            clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
            y = clahe.apply(y)
            clean = cv2.cvtColor(cv2.merge((y, cr, cb)), cv2.COLOR_YCrCb2RGB)
            img = Image.fromarray(clean)
        except (ImportError, AttributeError, cv2.error, ValueError):
            img = ImageOps.autocontrast(src, cutoff=0.4)

        # Two-pass, restrained sharpening: micro-contrast plus edge definition.
        img = ImageEnhance.Color(img).enhance(1.04)
        img = ImageEnhance.Contrast(img).enhance(1.05)
        img = img.filter(ImageFilter.UnsharpMask(radius=1.2, percent=145, threshold=2))
        img = img.filter(ImageFilter.UnsharpMask(radius=2.2, percent=65, threshold=4))
        filter_name = f"HD Pro • {nw}×{nh}"
        description = "High-clarity upscale with luminance denoising, adaptive local contrast, and two-pass detail sharpening."

    elif data == "edit_vivid":
        img = ImageEnhance.Contrast(img).enhance(1.3)
        img = ImageEnhance.Color(img).enhance(1.5)
        filter_name = "Vivid"
        description = "Colours pop with extra saturation."

    elif data == "edit_bw":
        img = ImageOps.grayscale(img).convert("RGB")
        filter_name = "B&W"
        description = "Classic black & white."

    elif data == "edit_sepia":
        sepia = ImageOps.colorize(ImageOps.grayscale(img), black="#704214", white="#C0A080")
        img = sepia.convert("RGB")
        filter_name = "Sepia"
        description = "Warm, nostalgic tone."

    elif data == "edit_sharp":
        img = img.filter(ImageFilter.SHARPEN)
        filter_name = "Sharpen"
        description = "Crisp, defined edges."
    elif data == "edit_bright":
        img = ImageEnhance.Brightness(img).enhance(1.5)
        filter_name = "Brighten"
        description = "Luminous and airy."

    elif data == "edit_dark":
        img = ImageEnhance.Brightness(img).enhance(0.5)
        filter_name = "Darken"
        description = "Moody and dramatic."

    elif data == "edit_blur":
        img = img.filter(ImageFilter.GaussianBlur(radius=5))
        filter_name = "Blur"
        description = "Soft, dreamy focus."

    elif data == "edit_pixel":
        small = img.resize((max(1, img.width // 10), max(1, img.height // 10)), Image.NEAREST)
        img = small.resize((img.width, img.height), Image.NEAREST)
        filter_name = "Pixel"
        description = "Retro 8‑bit look."

    elif data == "edit_invert":
        img = ImageChops.invert(img.convert("RGB"))
        filter_name = "Invert"
        description = "Negative colours."

    elif data == "edit_sketch":
        gray = ImageOps.grayscale(img)
        edges = gray.filter(ImageFilter.FIND_EDGES)
        img = edges.convert("RGB")
        filter_name = "Sketch"
        description = "Pencil outline effect."

    elif data == "edit_emboss":
        img = img.filter(ImageFilter.EMBOSS)
        filter_name = "Emboss"
        description = "Raised, 3D‑like texture."

    elif data == "edit_poster":
        img = img.filter(ImageFilter.CONTOUR)
        filter_name = "Poster"
        description = "Bold, graphic lines."

    elif data == "edit_solar":
        img = ImageOps.solarize(img, threshold=128)
        filter_name = "Solarize"
        description = "Surreal colour inversion."

    # ─── NEW EFFECTS (added for 100× attraction) ───
    elif data == "edit_cartoon":
        try:
            import cv2
            arr = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
            arr = cv2.bilateralFilter(arr, 9, 75, 75)
            gray = cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY)
            edges = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                                          cv2.THRESH_BINARY, 9, 10)
            edges = cv2.cvtColor(edges, cv2.COLOR_GRAY2BGR)
            cartoon = cv2.bitwise_and(arr, edges)
            img = Image.fromarray(cv2.cvtColor(cartoon, cv2.COLOR_BGR2RGB))
            filter_name = "Cartoon"
            description = "Flat colours with bold outlines."
        except ImportError:
            img = ImageOps.posterize(img, 4)
            img = img.filter(ImageFilter.CONTOUR)
            filter_name = "Cartoon (lite)"
            description = "Simplified colour posterisation."

    elif data == "edit_oil":
        img = Image.effect_noise(img.size, 50).convert("RGB")
        img = img.filter(ImageFilter.SMOOTH_MORE)
        img = ImageChops.multiply(img, ImageEnhance.Contrast(img).enhance(1.2))
        filter_name = "Oil Paint"
        description = "Brush‑stroke aesthetic."

    elif data == "edit_watercolor":
        img = ImageOps.autocontrast(img, cutoff=2)
        img = img.filter(ImageFilter.ModeFilter(size=5))
        img = ImageEnhance.Color(img).enhance(0.8)
        filter_name = "Watercolor"
        description = "Soft, washed‑out artistic touch."

    elif data == "edit_glow":
        blurred = img.filter(ImageFilter.GaussianBlur(radius=8))
        img = ImageChops.screen(img, blurred)
        filter_name = "Glow"
        description = "Ethereal halo effect."

    elif data == "edit_neon":
        img = ImageEnhance.Color(img).enhance(2.0)
        img = ImageChops.invert(img)
        img = ImageFilter.GaussianBlur(radius=2).filter(img)
        filter_name = "Neon"
        description = "Electric, glowing colours."

    elif data == "edit_vintage":
        img = ImageEnhance.Color(img).enhance(0.5)
        img = ImageEnhance.Contrast(img).enhance(0.9)
        img = ImageChops.overlay(img, Image.new("RGB", img.size, (255, 220, 180)))
        filter_name = "Vintage"
        description = "Retro film looks."

    elif data == "edit_mirror":
        img = img.transpose(Image.FLIP_LEFT_RIGHT)
        filter_name = "Mirror"
        description = "Horizontal reflection."

    elif data == "edit_flip":
        img = img.transpose(Image.FLIP_TOP_BOTTOM)
        filter_name = "Flip"
        description = "Vertical reflection."

    elif data == "edit_rotate":
        img = img.rotate(90, expand=True)
        filter_name = "Rotate 90°"
        description = "Turned sideways."

    elif data == "edit_vignette":
        width, height = img.size
        mask = Image.new("L", (width, height), 0)
        draw = ImageDraw.Draw(mask)
        draw.ellipse((width/4, height/4, 3*width/4, 3*height/4), fill=255)
        img = Image.composite(img, Image.new("RGB", (width, height), (0,0,0)), mask)
        filter_name = "Vignette"
        description = "Darkened corners."

    elif data == "edit_contrast":
        img = ImageEnhance.Contrast(img).enhance(1.5)
        filter_name = "High Contrast"
        description = "Punchy definition."

    elif data == "edit_saturation":
        img = ImageEnhance.Color(img).enhance(0.3)
        filter_name = "Desaturate"
        description = "Muted, subdued tones."

    else:
        await query.message.reply_text("❌ Unknown edit option.")
        return

    # Send the edited image
    out_bytes = BytesIO()
    img.save(out_bytes, format='JPEG', quality=95)
    out_bytes.seek(0)
    await query.message.reply_photo(
        photo=out_bytes,
        caption=f"✨ **{filter_name}**\n{description}",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=tool_done_kb()
    )


async def handle_edit_photo(update, context):
    if context.user_data.get('state') != 'awaiting_edit_photo':
        return
    if not update.message.photo:
        await update.message.reply_text("❌ Please upload an image.")
        return
    status_msg = await update.message.reply_text("⏳ Processing image...")
    try:
        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        img_bytes = BytesIO()
        await file.download_to_memory(img_bytes)
        img_bytes.seek(0)
        img = Image.open(img_bytes)
        context.user_data['edit_image'] = img
        kb = build_edit_keyboard(page=1)
        await status_msg.edit_text("✅ Image loaded!\n\nChoose an editing feature below:", reply_markup=kb)
        context.user_data['state'] = None
    except Exception as e:
        await status_msg.edit_text(f"❌ Processing failed: {e}")
        context.user_data['state'] = None


async def handle_bg_upload(update, context):
    if context.user_data.get('state') != 'awaiting_bg_upload':
        return
    if not update.message.photo:
        await update.message.reply_text("❌ Please upload the **background image**.")
        return
    try:
        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        img_bytes = BytesIO()
        await file.download_to_memory(img_bytes)
        img_bytes.seek(0)
        context.user_data['bg_image'] = Image.open(img_bytes)
        await update.message.reply_text("✅ Background image received!\n\nStep 2/2: Please upload the **front image**.")
        context.user_data['state'] = 'awaiting_front_upload'
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")


async def handle_front_upload(update, context):
    if context.user_data.get('state') != 'awaiting_front_upload':
        return
    if not update.message.photo:
        await update.message.reply_text("❌ Please upload the **front image**.")
        return
    status_msg = await update.message.reply_text("⏳ Changing background... (May take up to 30 seconds)")
    try:
        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        img_bytes = BytesIO()
        await file.download_to_memory(img_bytes)
        img_bytes.seek(0)
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
        out_bytes = BytesIO()
        new_img.save(out_bytes, format='JPEG', quality=95)
        out_bytes.seek(0)
        await update.message.reply_photo(photo=out_bytes, caption="✅ Background changed!", reply_markup=tool_done_kb())
        await status_msg.edit_text("✅ Background change complete!")
        context.user_data.pop('bg_image', None)
        context.user_data['state'] = None
    except Exception as e:
        await status_msg.edit_text(f"❌ Background change failed: {e}")
        context.user_data.pop('bg_image', None)
        context.user_data['state'] = None
# --- TEXT TO VOICE: LOCAL OFFLINE eSpeak (NO AI API) ---
# Uses shutil.which to find the binary anywhere on the server PATH.
ESPEAK_BIN = os.environ.get("ESPEAK_BIN", None) or shutil.which("espeak-ng") or shutil.which("espeak")
# --- TEXT TO VOICE (RENDER FREE TIER COMPATIBLE) ---
TTS_MAX_CHARS = 4000

_VOICE_TYPE_VARIANTS = {
    "male": "m3",
    "female": "f3",
    "old": "m2",
    "child": "f4",
}

def _tts_espeak_sync(text, lang, voice_type, out_path):
    # Render Free Tier does not allow apt-get, so we use gTTS instead of eSpeak
    try:
        from gtts import gTTS
        # Map lang codes to Google TTS codes
        tts_lang = 'en' if lang.startswith('en') else 'am'
        tts = gTTS(text=text, lang=tts_lang, slow=False)
        tts.save(out_path)
        return "gTTS (free API)", True
    except ImportError:
        raise RuntimeError("gTTS is not installed. Add 'gTTS' to requirements.txt")
    except Exception as e:
        raise RuntimeError(f"TTS failed: {e}")

async def handle_tts(update, context, lang):
    text = (update.message.text or "").strip()
    if not text:
        await update.message.reply_text("❌ Please send the text you want to convert.")
        return
    if len(text) > TTS_MAX_CHARS:
        await update.message.reply_text(
            f"❌ Text is too long ({len(text)} chars). Please send up to {TTS_MAX_CHARS} characters."
        )
        return
    context.user_data['tts_text'] = text
    context.user_data['tts_lang'] = lang
    kb = [
        [InlineKeyboardButton("🧑 Male", callback_data=f"tts_voice_{lang}_male"),
         InlineKeyboardButton("👩 Female", callback_data=f"tts_voice_{lang}_female")],
        [InlineKeyboardButton("👴 Old", callback_data=f"tts_voice_{lang}_old"),
         InlineKeyboardButton("👶 Child", callback_data=f"tts_voice_{lang}_child")],
        [InlineKeyboardButton("⬅️ Cancel", callback_data="converter")],
    ]
    await update.message.reply_text("🔊 TEXT TO VOICE\n\nChoose a voice style:", reply_markup=InlineKeyboardMarkup(kb))
    context.user_data['state'] = None

async def handle_tts_voice_selection(update, context, data):
    q = update.callback_query
    await q.answer()
    parts = data.split("_")
    lang = parts[2] if len(parts) > 2 else "en"
    voice_type = parts[3] if len(parts) > 3 else "male"
    text = context.user_data.get('tts_text')
    if not text:
        await q.message.reply_text("❌ Text session expired. Open Text to Voice and send the text again.")
        return

    status = await q.message.reply_text("🔊 Converting to speech…")
    path = None
    try:
        fd, path = tempfile.mkstemp(prefix="tts_", suffix=".mp3")
        os.close(fd)
        used_voice, matched_requested_lang = await asyncio.to_thread(
            _tts_espeak_sync, text, lang, voice_type, path
        )
        with open(path, "rb") as audio:
            await q.message.reply_audio(
                audio=audio,
                title=f"Text to Voice ({lang})",
                reply_markup=tool_done_kb()
            )
        await status.edit_text("✅ Text to voice complete!")
    except Exception as e:
        await status.edit_text(
            "❌ Local text-to-voice failed.\n\n" + html.escape(str(e)[:1200]),
            parse_mode=ParseMode.HTML
        )
    finally:
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass
        context.user_data.pop('tts_text', None)
        context.user_data.pop('tts_lang', None)
        context.user_data['state'] = None
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
# --- TEXT TO PDF: UNICODE/FALLBACK FONT ENGINE ---
PDF_DRAFT_KEY = "text_pdf_items"

def text_pdf_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Enter Next Text", callback_data="pdftext_next"),
         InlineKeyboardButton("✅ Done", callback_data="pdftext_done")],
        [InlineKeyboardButton("❌ Cancel", callback_data="pdftext_cancel")]
    ])

# --- Bundled fonts take priority over system fonts ---
# Put actual .ttf files in a "fonts/" folder next to this script and commit
# them to your repo. This guarantees the PDF engine works identically on
# Render, Railway, a VPS, or your laptop — it never depends on what the
# host OS happens to have installed.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BUNDLED_FONT_DIR = os.path.join(BASE_DIR, "fonts")

def _bundled(*names):
    return [os.path.join(BUNDLED_FONT_DIR, n) for n in names]

FONT_CANDIDATES = _bundled("NotoSans-Regular.ttf", "DejaVuSans.ttf") + [
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/opentype/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]
ETHIOPIC_CANDIDATES = _bundled("NotoSansEthiopic-Regular.ttf") + [
    "/usr/share/fonts/truetype/noto/NotoSansEthiopic-Regular.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansEthiopic-Regular.ttf",
]
ARABIC_CANDIDATES = _bundled("NotoSansArabic-Regular.ttf") + [
    "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansArabic/NotoSansArabic-Regular.ttf",
]
# Fixed: your original list had the SAME path twice, so it never found an
# alternate. Now covers both common install locations.
CJK_CANDIDATES = _bundled("NotoSansCJK-Regular.ttc") + [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto-cjk/NotoSansCJK-Regular.ttc",
]
# Extra scripts added for broader "any language" coverage — Hindi/Marathi/etc.
# (Devanagari), Thai, and Hebrew. Safe no-ops if the files aren't present.
DEVANAGARI_CANDIDATES = _bundled("NotoSansDevanagari-Regular.ttf") + [
    "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
]
THAI_CANDIDATES = _bundled("NotoSansThai-Regular.ttf") + [
    "/usr/share/fonts/truetype/noto/NotoSansThai-Regular.ttf",
]
HEBREW_CANDIDATES = _bundled("NotoSansHebrew-Regular.ttf") + [
    "/usr/share/fonts/truetype/noto/NotoSansHebrew-Regular.ttf",
]

# Unicode block ranges used only to decide whether Arabic reshaping is needed.
_ARABIC_RANGE = re.compile(r'[\u0600-\u06FF\u0750-\u077F]')


def _first_existing(paths):
    for p in paths:
        if p and os.path.exists(p):
            return p
    return None


def _maybe_reshape_arabic(text):
    """
    Arabic (and similar cursive-joining scripts) must be reshaped and
    bidi-reordered before rendering, or letters appear disconnected/reversed.
    Ethiopic, CJK, Devanagari, etc. don't need this — only applied when
    Arabic-range characters are actually present, and it degrades gracefully
    (falls back to plain text) if the optional libraries aren't installed.
    """
    if not _ARABIC_RANGE.search(text):
        return text
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
        reshaped = arabic_reshaper.reshape(text)
        return get_display(reshaped)
    except ImportError:
        # Libraries not installed — text will still render with the right
        # glyphs via the fallback font, just without proper joining/order.
        return text


def _make_unicode_pdf(texts, path):
    # fpdf2 handles Unicode text directly and can use fallback fonts.
    from fpdf import FPDF
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=16)
    pdf.set_margins(15, 15, 15)

    main_font = _first_existing(FONT_CANDIDATES)
    if not main_font:
        raise RuntimeError(
            "No Unicode font found. Add a font file (e.g. NotoSans-Regular.ttf) "
            f"to '{BUNDLED_FONT_DIR}' in your repo, or install Noto/DejaVu system-wide."
        )
    pdf.add_font("Main", fname=main_font)

    fallback = []
    missing = []
    script_fonts = (
        ("Ethiopic", ETHIOPIC_CANDIDATES),
        ("Arabic", ARABIC_CANDIDATES),
        ("CJK", CJK_CANDIDATES),
        ("Devanagari", DEVANAGARI_CANDIDATES),
        ("Thai", THAI_CANDIDATES),
        ("Hebrew", HEBREW_CANDIDATES),
    )
    for name, candidates in script_fonts:
        font_path = _first_existing(candidates)
        if font_path:
            try:
                pdf.add_font(name, fname=font_path)
                fallback.append(name)
            except Exception:
                missing.append(name)
        else:
            missing.append(name)

    if fallback:
        try:
            pdf.set_fallback_fonts(fallback)
        except Exception:
            pass

    pdf.add_page()
    pdf.set_font("Main", size=12)
    pdf.set_title("Text Document")
    for i, t in enumerate(texts):
        if i:
            pdf.ln(4)
        pdf.multi_cell(0, 8, _maybe_reshape_arabic(t))
    pdf.output(path)

    return fallback, missing


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
        f"✅ Text {len(items)} added.\n\nUnicode PDF mode supports multilingual text. Add another or press Done.",
        reply_markup=text_pdf_keyboard()
    )


async def finish_text_to_pdf(update, context):
    q = update.callback_query
    await q.answer()
    items = context.user_data.get(PDF_DRAFT_KEY, [])
    if not items:
        await q.message.reply_text("❌ No text has been added yet.")
        return

    status = await q.message.reply_text("⏳ Creating multilingual Unicode PDF...")
    path = None
    try:
        fd, path = tempfile.mkstemp(prefix="text_pdf_", suffix=".pdf")
        os.close(fd)
        fallback, missing = await asyncio.to_thread(_make_unicode_pdf, items, path)

        caption = f"✅ Unicode PDF created from {len(items)} text message(s)."
        with open(path, "rb") as f:
            await q.message.reply_document(
                document=f, filename="multilingual_text.pdf",
                caption=caption, reply_markup=tool_done_kb()
            )

        if missing:
            # Tell the admin/user exactly which scripts won't render correctly
            # yet, instead of leaving it a silent mystery.
            await status.edit_text(
                "✅ Text to PDF complete.\n"
                f"⚠️ Fonts not found for: {', '.join(missing)} — those scripts "
                "may show as boxes. Add the matching .ttf to the fonts/ folder to fix."
            )
        else:
            await status.edit_text("✅ Text to PDF complete — Unicode font fallback enabled.")
    except Exception as e:
        await status.edit_text("❌ Text to PDF failed.\n\n" + html.escape(str(e)[:1500]), parse_mode=ParseMode.HTML)
    finally:
        context.user_data.pop(PDF_DRAFT_KEY, None)
        context.user_data["state"] = None
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass


async def cancel_text_to_pdf(update, context):
    q = update.callback_query
    await q.answer()
    context.user_data.pop(PDF_DRAFT_KEY, None)
    context.user_data["state"] = None
    await q.edit_message_text("❌ Text to PDF cancelled.")
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
        
def _format_audio_time(seconds):
    seconds = max(0.0, float(seconds))
    minutes = int(seconds // 60)
    secs = int(round(seconds - minutes * 60))
    if secs >= 60:
        minutes += 1
        secs = 0
    return f"{minutes}:{secs:02d}"


# --- AUDIO TOOLS ---
def _parse_audio_time(value):
    value = str(value or "").strip().replace(",", ".")
    if not value:
        raise ValueError("missing time")
    if ":" in value:
        parts = value.split(":")
        if len(parts) > 3:
            raise ValueError("invalid time")
        try:
            nums = [float(x) for x in parts]
        except ValueError:
            raise ValueError("invalid time")
        seconds = 0.0
        for n in nums:
            seconds = seconds * 60 + n
        return seconds
    return float(value)

async def _download_message_media(message, context, suffix):
    media = message.video or message.audio or message.voice or message.document
    if not media:
        return None
    file_id = media.file_id
    tg_file = await context.bot.get_file(file_id)
    data = BytesIO()
    await tg_file.download_to_memory(data)
    data.seek(0)
    fd, path = tempfile.mkstemp(prefix="sami_audio_", suffix=suffix)
    os.close(fd)
    with open(path, "wb") as f:
        f.write(data.read())
    return path

async def handle_video_to_audio(update, context):
    if context.user_data.get("state") != "audio_video_to_audio":
        return
    message = update.message
    if not message or not (message.video or (message.document and (message.document.mime_type or "").startswith("video/"))):
        await message.reply_text("🎬 Please send a video file.")
        return
    status = await message.reply_text("⏳ Extracting audio from the video…")
    src = None
    out = None
    try:
        suffix = os.path.splitext(getattr(message.video, "file_name", "") or ".mp4")[1] or ".mp4"
        src = await _download_message_media(message, context, suffix)
        fd, out = tempfile.mkstemp(prefix="sami_video_audio_", suffix=".mp3")
        os.close(fd)
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        proc = await asyncio.to_thread(
            subprocess.run,
            [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", src,
             "-vn", "-codec:a", "libmp3lame", "-q:a", "2", out],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180, check=False,
        )
        if proc.returncode != 0 or not os.path.exists(out) or os.path.getsize(out) == 0:
            raise RuntimeError(proc.stderr.decode(errors="ignore")[:500] or "FFmpeg failed")
        with open(out, "rb") as f:
            await message.reply_audio(audio=f, filename="audio.mp3",
                                      caption="🎵 Audio extracted successfully.",
                                      reply_markup=tool_done_kb())
        await status.edit_text("✅ Video → Audio complete.")
    except Exception as e:
        await status.edit_text("❌ Video → Audio failed.\n\n" + html.escape(str(e)[:800]), parse_mode=ParseMode.HTML)
    finally:
        for p in (src, out):
            if p:
                try: os.unlink(p)
                except OSError: pass
        context.user_data["state"] = None

async def handle_audio_cut(update, context):
    """Friendly step-by-step audio cutter: start -> end -> media."""
    state = context.user_data.get("state")
    message = update.effective_message

    if state == "audio_cut_waiting_start":
        raw = (message.text or "").strip()
        try:
            start = _parse_audio_time(raw)
            if start < 0:
                raise ValueError
        except Exception:
            await message.reply_text(
                "❌ I couldn't understand that time.\n\n"
                "Please enter the <b>starting point</b> as minutes:seconds.\n"
                "Example: <code>0:30</code> or <code>2:15</code>",
                parse_mode=ParseMode.HTML,
            )
            return
        context.user_data["audio_cut_start"] = start
        context.user_data["state"] = "audio_cut_waiting_end"
        await message.reply_text(
            f"✅ Start point: <b>{html.escape(raw)}</b>\n\n"
            "🏁 Now enter the <b>ending point</b>.\n"
            "Example: <code>2:45</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="audio_tools")]]),
        )
        return

    if state == "audio_cut_waiting_end":
        raw = (message.text or "").strip()
        try:
            end = _parse_audio_time(raw)
            start = float(context.user_data.get("audio_cut_start", 0))
            if end <= start:
                raise ValueError
            if end - start > 3600:
                raise ValueError
        except Exception:
            await message.reply_text(
                "❌ Invalid ending point. It must be <b>after the starting point</b>.\n\n"
                "Please enter the ending time, for example <code>2:45</code>.",
                parse_mode=ParseMode.HTML,
            )
            return
        context.user_data["audio_cut_end"] = end
        context.user_data["state"] = "audio_cut_waiting_file"
        await message.reply_text(
            f"✅ Start: <b>{_format_audio_time(start)}</b>\n"
            f"✅ End: <b>{_format_audio_time(end)}</b>\n"
            f"⏱️ Length: <b>{_format_audio_time(end - start)}</b>\n\n"
            "🎵 Perfect! Now <b>send the audio or voice message</b> you want to cut.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="audio_tools")]]),
        )
        return

    if state != "audio_cut_waiting_file":
        return
    if not (message.audio or message.voice or (message.document and str(getattr(message.document, "mime_type", "")).lower().startswith("audio/"))):
        await message.reply_text("🎵 Please send an audio file or voice message.")
        return

    status = await message.reply_text("✂️ <b>Cutting your audio…</b>", parse_mode=ParseMode.HTML)
    src = out = None
    try:
        suffix = ".ogg" if message.voice else ".mp3"
        src = await _download_message_media(message, context, suffix)
        fd, out = tempfile.mkstemp(prefix="sami_cut_audio_", suffix=".mp3")
        os.close(fd)
        start = float(context.user_data["audio_cut_start"])
        end = float(context.user_data["audio_cut_end"])
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        proc = await asyncio.to_thread(
            subprocess.run,
            [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
             "-ss", str(start), "-to", str(end), "-i", src,
             "-vn", "-codec:a", "libmp3lame", "-q:a", "2", out],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180, check=False,
        )
        if proc.returncode != 0 or not os.path.exists(out) or os.path.getsize(out) == 0:
            raise RuntimeError(proc.stderr.decode(errors="ignore")[:500] or "FFmpeg failed")
        with open(out, "rb") as f:
            await message.reply_audio(
                audio=f,
                filename="cut_audio.mp3",
                caption=f"✂️ Cut: {_format_audio_time(start)} → {_format_audio_time(end)} • {_format_audio_time(end - start)}",
                reply_markup=tool_done_kb(),
            )
        await status.edit_text("✅ <b>Audio cut complete!</b>", parse_mode=ParseMode.HTML)
    except Exception as e:
        await status.edit_text("❌ <b>Audio cut failed.</b>\n\n" + html.escape(str(e)[:800]), parse_mode=ParseMode.HTML)
    finally:
        for p in (src, out):
            if p:
                try: os.unlink(p)
                except OSError: pass
        for key in ("audio_cut_start", "audio_cut_end"):
            context.user_data.pop(key, None)
        context.user_data["state"] = None

# --- VOICE TO TEXT (GROQ + GOOGLE FALLBACK) ---
async def handle_voice_to_text(update, context, language):
    if not update.message.voice and not update.message.audio:
        await update.message.reply_text("❌ Please send a voice message OR an audio file.")
        return

    status_msg = await update.message.reply_text("⏳ Transcribing voice/audio...")

    try:
        if update.message.voice:
            file_id = update.message.voice.file_id
        else:
            file_id = update.message.audio.file_id

        file = await context.bot.get_file(file_id)
        voice_bytes = BytesIO()
        await file.download_to_memory(voice_bytes)
        voice_bytes.seek(0)

        with tempfile.NamedTemporaryFile(delete=False, suffix=".ogg") as tmp_ogg:
            tmp_ogg.write(voice_bytes.read())
            tmp_ogg_path = tmp_ogg.name

        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp_wav:
            tmp_wav_path = tmp_wav.name

        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        cmd = [ffmpeg_exe, "-i", tmp_ogg_path, "-ar", "16000", "-ac", "1", tmp_wav_path, "-y"]
        subprocess.run(cmd, check=True, capture_output=True)

        # 1. TRY GROQ (Best for Amharic, Fastest, Free)
        if GROQ_API_KEY:
            try:
                client = Groq(api_key=GROQ_API_KEY)
                with open(tmp_wav_path, "rb") as f:
                    transcription = client.audio.transcriptions.create(
                        file=(tmp_wav_path, f),
                        model="whisper-large-v3",
                        language=language.split('-')[0],
                        response_format="text"
                    )
                await update.message.reply_text(
                    f"📝 **Transcribed Text (Groq Whisper v3):**\n\n{transcription}",
                    reply_markup=tool_done_kb()
                )
                await status_msg.edit_text("✅ Transcription complete!")
                os.unlink(tmp_ogg_path); os.unlink(tmp_wav_path)
                context.user_data['state'] = None
                return
            except Exception:
                pass  # If Groq fails, fall back to Google

        # 2. FALLBACK TO GOOGLE (Free, but weak for Amharic)
        recognizer = sr.Recognizer()
        with sr.AudioFile(tmp_wav_path) as source:
            audio_data = recognizer.record(source)

        try:
            text = recognizer.recognize_google(audio_data, language=language)
            await update.message.reply_text(
                f"📝 **Transcribed Text (Google):**\n\n{text}",
                reply_markup=tool_done_kb()
            )
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
# Supported input: YouTube, TikTok, Instagram, Facebook.
# The bot first uses yt-dlp. For YouTube/TikTok failures, it can use public
# fallback services that return stream URLs. No CAPTCHA or anti-bot bypass code
# is used. Optional authenticated cookies can be provided by the owner through
# YTDLP_COOKIES_FILE if the service requires login.

VIDEO_MAX_UPLOAD = 50 * 1024 * 1024
VIDEO_QUALITIES = [1080, 720, 480, 360, 240]
PIPED_APIS = [
    "https://pipedapi.kavin.rocks", "https://pipedapi.tokhmi.xyz",
    "https://pipedapi.moomoo.me", "https://pipedapi.syncpundit.io",
    "https://api-piped.mha.fi", "https://piped-api.garudalinux.org",
    "https://pipedapi.rivo.lol", "https://pipedapi.leptons.xyz",
    "https://piped-api.lunar.icu", "https://pipedapi.colinslegacy.com",
    "https://yapi.vyper.me", "https://piped-api.cfe.re",
    "https://pipedapi.r4fo.com", "https://piped-api.privacy.com.de",
    "https://pipedapi.adminforge.de", "https://api.piped.yt",
]
PIPED_SSL_CONTEXT = __import__("ssl")._create_unverified_context()

# Public instances from the maintained Invidious instance list.
INVIDIOUS_APIS = [
    "https://inv.nadeko.net", "https://invidious.nerdvpn.de",
    "https://yt.chocolatemoo53.com", "https://invidious.tiekoetter.com",
    "https://yewtu.be", "https://yt.artemislena.eu",
    "https://invidious.flokinet.to", "https://invidious.privacydev.net",
    "https://iv.melmac.space", "https://inv.tux.pizza",
    "https://invidious.protokolla.fi", "https://invidious.private.coffee",
    "https://yt.drgnz.club", "https://iv.datura.network",
]
TIKWM_API = "https://www.tikwm.com/api/"


def video_platform(url: str) -> str:
    host = urllib.parse.urlparse(url).netloc.lower().split(":", 1)[0]
    if host.startswith("www."):
        host = host[4:]
    if "youtube.com" in host or host == "youtu.be":
        return "youtube"
    if "tiktok.com" in host:
        return "tiktok"
    if "instagram.com" in host:
        return "instagram"
    if "facebook.com" in host or host == "fb.watch":
        return "facebook"
    return "unknown"


def normalize_public_url(url: str) -> str:
    """Follow ordinary HTTP redirects (especially short TikTok links)."""
    url = (url or "").strip()
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/147.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.8",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            final_url = response.geturl()
            return final_url or url
    except Exception:
        return url


def youtube_video_id(url: str):
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower().split(":", 1)[0]
    if host == "youtu.be":
        value = parsed.path.strip("/").split("/")
        return value[0] if value and value[0] else None
    if "youtube.com" in host:
        qs = urllib.parse.parse_qs(parsed.query)
        if qs.get("v"):
            return qs["v"][0]
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) >= 2 and parts[0] in {"shorts", "embed", "live"}:
            return parts[1]
    return None


def ytdlp_base_options():
    options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 25,
        "retries": 3,
        "fragment_retries": 3,
        "file_access_retries": 3,
        "concurrent_fragment_downloads": 4,
        "ffmpeg_location": imageio_ffmpeg.get_ffmpeg_exe(),
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/147.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.8",
        },
        # Current yt-dlp uses EJS to solve YouTube's JavaScript challenges.
        # GitHub is the last-resort source for the EJS scripts when the Python
        # package does not already contain a compatible copy.
        "remote_components": {"ejs:github"},
    }
    # Render/PaaS images often do not have a JS runtime on PATH. The
    # official "deno" Python package ships the Deno binary, so use it when
    # available before falling back to system runtimes.
    runtime_path = shutil.which("deno")
    if not runtime_path:
        try:
            import deno as deno_pkg
            runtime_path = deno_pkg.find_deno_bin()
        except Exception:
            runtime_path = None
    if runtime_path:
        # yt-dlp expects each runtime value to be a configuration dict.
        options["js_runtimes"] = {"deno": {"path": runtime_path}}
    else:
        for runtime_name in ("node", "qjs", "bun"):
            runtime_path = shutil.which(runtime_name)
            if runtime_name == "node" and not runtime_path:
                runtime_path = shutil.which("nodejs")
            if runtime_path:
                options["js_runtimes"] = {runtime_name: {"path": runtime_path}}
                break

    # IMPORTANT: Do not force a hard-coded YouTube player client here.
    # Current yt-dlp has its own adaptive client strategy (currently starting
    # with visionos/web) and may add fallbacks based on the video's state.
    # Forcing old clients was the direct cause of the repeated
    # "Failed to extract any player response" errors in this bot.
    # Client-specific fallbacks are handled in extract_ytdlp_info/ytdlp_download
    # only after yt-dlp's native default strategy has had a chance.
    options["extractor_retries"] = 1

    cookies = os.environ.get("YTDLP_COOKIES_FILE", "").strip()
    if cookies and os.path.isfile(cookies):
        options["cookiefile"] = cookies
    # Do NOT set `impersonate='chrome'` blindly. That was the source of the
    # user's "Impersonate target chrome is not available" error.
    return options


def extract_ytdlp_info(url: str):
    # First let the installed yt-dlp use its CURRENT native YouTube strategy.
    # This is important: recent yt-dlp versions dynamically choose clients
    # (currently visionos/web) and add fallbacks based on playability.
    #
    # Only if that native strategy fails do we try a small set of known
    # no-PO-token / fallback clients. android_vr is intentionally NOT forced:
    # current yt-dlp marks its GVS formats as PO-token-sensitive.
    clients = ["default", "tv", "web_embedded", "tv_simply"]
    errors = []

    for client in clients:
        opts = ytdlp_base_options()
        if client != "default":
            opts["extractor_args"] = {"youtube": {"player_client": [client]}}
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
            if info:
                info["__sami_youtube_client"] = client
                return info
        except Exception as exc:
            errors.append(f"{client}: {exc}")

    raise RuntimeError("YouTube yt-dlp clients failed: " + " | ".join(errors[-4:]))


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


def ytdlp_download(url: str, output_dir: str, *, height=None, audio=False, title_hint="video"):
    platform = video_platform(url)
    # Native/default strategy first. The old implementation started with
    # android_vr/web_embedded/web_safari, which is exactly the pattern that is
    # now failing on the user's Render deployment.
    clients = ["default", "tv", "web_embedded", "tv_simply"] if platform == "youtube" else [None]
    errors = []
    for client in clients:
        try:
            opts = ytdlp_base_options()
            if client and client != "default":
                opts["extractor_args"] = {"youtube": {"player_client": [client]}}
            opts.update({
                "format": build_ytdlp_format(height, audio, platform),
                "outtmpl": os.path.join(output_dir, "%(id)s.%(ext)s"),
                "merge_output_format": "mp4",
                "overwrites": True,
            })
            if audio:
                opts["postprocessors"] = [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }]
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                filepath = ydl.prepare_filename(info)
                if audio:
                    base, _ = os.path.splitext(filepath)
                    mp3_path = base + ".mp3"
                    if os.path.exists(mp3_path):
                        filepath = mp3_path
                elif not os.path.exists(filepath):
                    stem = os.path.splitext(filepath)[0]
                    for ext in (".mp4", ".mkv", ".webm", ".mov"):
                        if os.path.exists(stem + ext):
                            filepath = stem + ext
                            break
                if not os.path.isfile(filepath):
                    raise FileNotFoundError(f"Downloaded file was not created: {title_hint}")
                return {"path": filepath, "title": info.get("title") or title_hint, "info": info}
        except Exception as exc:
            errors.append(f"{client or 'default'}: {exc}")
            # Remove partial output before trying the next YouTube client.
            for name in os.listdir(output_dir):
                path = os.path.join(output_dir, name)
                if os.path.isfile(path):
                    try:
                        os.unlink(path)
                    except Exception:
                        pass
    raise RuntimeError("All YouTube yt-dlp clients failed: " + " | ".join(errors[-5:]))


def piped_get_json(url: str, timeout=8):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json,text/plain,*/*",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout, context=PIPED_SSL_CONTEXT) as response:
        return __import__("json").load(response)


def _parallel_first_json(urls, loader, label):
    """Try independent public YouTube APIs concurrently without waiting on losers."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    errors = []
    # Keep concurrency deliberately small: this is a fallback, not a flooder.
    pool = ThreadPoolExecutor(max_workers=min(4, len(urls)))
    futures = {pool.submit(loader, url): url for url in urls}
    try:
        for future in as_completed(futures):
            api = futures[future]
            try:
                data = future.result()
                if data:
                    # Do not wait for broken/slow fallback hosts after we have
                    # a usable response. Their worker threads are daemonized by
                    # the executor implementation and can finish in the background.
                    for pending in futures:
                        if not pending.done():
                            pending.cancel()
                    pool.shutdown(wait=False, cancel_futures=True)
                    return data, api
                errors.append(f"{api}: empty response")
            except Exception as exc:
                errors.append(f"{api}: {exc}")
    finally:
        # At this point either all tasks finished or the successful path has
        # already shut the pool down. Avoid blocking the bot on dead hosts.
        try:
            pool.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass

    raise RuntimeError(
        f"All {label} fallback services failed: " + " | ".join(errors[:8])
    )


def piped_metadata(video_id: str):
    urls = [
        f"{api}/streams/{urllib.parse.quote(video_id)}"
        for api in PIPED_APIS[:8]
    ]

    def load(url):
        data = piped_get_json(url, timeout=8)
        if not (data.get("videoStreams") or data.get("audioStreams")):
            raise RuntimeError("no streams")
        return data

    data, _api = _parallel_first_json(urls, load, "Piped")
    return data


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


def download_url_to_file(url: str, path: str, headers=None, ssl_context=None):
    request = urllib.request.Request(
        url,
        headers=headers or {
            "User-Agent": "Mozilla/5.0",
            "Accept": "*/*",
        },
    )
    opener = (urllib.request.urlopen(request, timeout=30, context=ssl_context) if ssl_context is not None else urllib.request.urlopen(request, timeout=30))
    with opener as response, open(path, "wb") as dst:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            dst.write(chunk)


def ffmpeg_merge(video_path: str, audio_path: str, out_path: str):
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [
        ffmpeg, "-y",
        "-i", video_path,
        "-i", audio_path,
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-c:v", "copy",
        "-c:a", "aac",
        "-movflags", "+faststart",
        out_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "FFmpeg merge failed")[-1500:])
    return out_path


def download_from_piped(meta, output_dir: str, *, height=None, audio=False):
    title = meta.get("title") or "YouTube video"
    if audio:
        stream = choose_piped_audio_stream(meta.get("audioStreams") or [])
        if not stream:
            raise RuntimeError("YouTube fallback has no audio stream")
        raw = os.path.join(output_dir, "audio.m4a")
        mp3 = os.path.join(output_dir, "audio.mp3")
        download_url_to_file(stream["url"], raw, ssl_context=PIPED_SSL_CONTEXT)
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        result = subprocess.run([
            ffmpeg, "-y", "-i", raw,
            "-vn", "-c:a", "libmp3lame", "-b:a", "192k", mp3,
        ], capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError((result.stderr or "Audio conversion failed")[-1200:])
        return {"path": mp3, "title": title}

    target = height or 720
    stream = choose_piped_video_stream(meta.get("videoStreams") or [], target)
    if not stream:
        raise RuntimeError("YouTube fallback has no video stream")
    video_raw = os.path.join(output_dir, "video.bin")
    download_url_to_file(stream["url"], video_raw, ssl_context=PIPED_SSL_CONTEXT)
    mime = (stream.get("mimeType") or "").lower()
    has_audio = not bool(stream.get("videoOnly")) or mime.startswith("video/mp4") and not stream.get("videoOnly")
    if has_audio:
        final = os.path.join(output_dir, "video.mp4")
        if mime == "video/mp4":
            os.replace(video_raw, final)
        else:
            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
            result = subprocess.run([
                ffmpeg, "-y", "-i", video_raw, "-c:v", "copy", "-c:a", "aac", "-movflags", "+faststart", final,
            ], capture_output=True, text=True)
            if result.returncode != 0:
                raise RuntimeError((result.stderr or "Video conversion failed")[-1200:])
            os.unlink(video_raw)
        return {"path": final, "title": title}

    audio_stream = choose_piped_audio_stream(meta.get("audioStreams") or [])
    if not audio_stream:
        raise RuntimeError("YouTube fallback video is video-only and no audio stream was found")
    audio_raw = os.path.join(output_dir, "audio.bin")
    final = os.path.join(output_dir, "video.mp4")
    download_url_to_file(audio_stream["url"], audio_raw, ssl_context=PIPED_SSL_CONTEXT)
    ffmpeg_merge(video_raw, audio_raw, final)
    os.unlink(video_raw)
    os.unlink(audio_raw)
    return {"path": final, "title": title}


def invidious_get_json(url: str, timeout=8):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json,*/*"},
    )
    with urllib.request.urlopen(request, timeout=timeout, context=PIPED_SSL_CONTEXT) as response:
        return __import__("json").load(response)


def invidious_metadata(video_id: str):
    urls = [
        f"{api}/api/v1/videos/{urllib.parse.quote(video_id)}?hl=en"
        for api in INVIDIOUS_APIS[:8]
    ]

    def load(url):
        data = invidious_get_json(url, timeout=8)
        if not (data.get("videoId") and (data.get("formatStreams") or data.get("adaptiveFormats"))):
            raise RuntimeError("no streams")
        return data

    return _parallel_first_json(urls, load, "Invidious")


def invidious_to_piped_meta(meta):
    videos, audios = [], []
    for s in (meta.get("formatStreams") or []):
        q = s.get("qualityLabel") or ""
        m = re.search(r"(\d+)", str(q))
        if s.get("url") and m:
            videos.append({"url": s["url"], "height": int(m.group(1)), "videoOnly": False, "mimeType": s.get("type") or ""})
    for s in (meta.get("adaptiveFormats") or []):
        typ = (s.get("type") or "").lower()
        q = s.get("qualityLabel") or s.get("resolution") or ""
        m = re.search(r"(\d+)", str(q))
        if s.get("url") and "video/" in typ and m:
            videos.append({"url": s["url"], "height": int(m.group(1)), "videoOnly": True, "mimeType": typ})
        elif s.get("url") and "audio/" in typ:
            audios.append({"url": s["url"], "bitrate": s.get("bitrate") or 0, "mimeType": typ})
    return {"title": meta.get("title") or "YouTube video", "videoStreams": videos, "audioStreams": audios}


def tikwm_get_data(url: str):
    query = urllib.parse.urlencode({"url": url})
    endpoint = TIKWM_API + "?" + query
    request = urllib.request.Request(endpoint, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json,*/*"})
    with urllib.request.urlopen(request, timeout=25) as response:
        data = __import__("json").load(response)
    if int(data.get("code", -1)) != 0 or not data.get("data"):
        raise RuntimeError(data.get("msg") or "TikTok fallback service returned no data")
    return data["data"]


def tikwm_download(url: str, output_dir: str, *, quality="hd", audio=False):
    data = tikwm_get_data(url)
    title = data.get("title") or data.get("desc") or "TikTok video"
    if audio:
        media_url = data.get("music")
        if not media_url:
            raise RuntimeError("TikTok fallback has no audio URL")
        out = os.path.join(output_dir, "tiktok_audio.mp3")
        raw = os.path.join(output_dir, "tiktok_audio.bin")
        download_url_to_file(media_url, raw)
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        result = subprocess.run([ffmpeg, "-y", "-i", raw, "-vn", "-c:a", "libmp3lame", "-b:a", "192k", out], capture_output=True, text=True)
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


def cobalt_prepare(url: str, *, audio=False):
    """Use Cobalt's independent public API for YouTube without yt-dlp extraction."""
    endpoint = os.environ.get("COBALT_API_URL", "https://api.cobalt.tools/").strip()
    payload = {
        "url": url,
        "downloadMode": "audio" if audio else "auto",
        "audioFormat": "mp3",
        "videoQuality": "720",
        "filenameStyle": "pretty",
    }
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "User-Agent": "SamiBOT/1.0",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Origin": "https://cobalt.tools",
            "Referer": "https://cobalt.tools/",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=35) as response:
        data = json.load(response)
    if data.get("status") not in {"tunnel", "redirect"} or not data.get("url"):
        raise RuntimeError(data.get("text") or data.get("error") or "Cobalt returned no downloadable media")
    return data


def extract_download_options(url: str):
    platform = video_platform(url)
    normalized = normalize_public_url(url) if platform == "tiktok" else url
    # YouTube: try an independent hosted download API first, not yt-dlp clients.
    if platform == "youtube":
        try:
            cobalt = cobalt_prepare(normalized)
            return {"source": "cobalt", "platform": platform, "url": normalized,
                    "title": cobalt.get("filename") or "YouTube video",
                    "qualities": [720], "audio": False, "cobalt": cobalt}
        except Exception:
            pass
    # Keep existing extractor path for other platforms and as a YouTube fallback.
    try:
        info = extract_ytdlp_info(normalized)
        title = info.get("title") or "Video"
        if platform == "youtube":
            available = set()
            for f in info.get("formats") or []:
                if f.get("vcodec") != "none" and f.get("height"):
                    available.add(int(f["height"]))
            qualities = [q for q in VIDEO_QUALITIES if any(h >= q for h in available)]
            if not qualities and available:
                qualities = [max(available)]
            return {"source": "ytdlp", "platform": platform, "url": normalized, "title": title, "qualities": qualities or [720], "audio": True}
        # For other sites, expose the qualities yt-dlp actually knows about.
        heights = sorted({int(f["height"]) for f in info.get("formats") or [] if f.get("vcodec") != "none" and f.get("height")}, reverse=True)
        qualities = [q for q in VIDEO_QUALITIES if any(h >= q for h in heights)]
        return {"source": "ytdlp", "platform": platform, "url": normalized, "title": title, "qualities": qualities or [720], "audio": True}
    except Exception as first_error:
        if platform == "youtube":
            vid = youtube_video_id(normalized)
            if not vid:
                raise RuntimeError(f"YouTube extraction failed: {first_error}")
            fallback_errors = []
            try:
                piped = piped_metadata(vid)
                source = "piped"
                fallback_meta = piped
            except Exception as piped_error:
                fallback_errors.append(str(piped_error))
                try:
                    inv, inv_api = invidious_metadata(vid)
                    source = "invidious"
                    fallback_meta = invidious_to_piped_meta(inv)
                    fallback_meta["_invidious_api"] = inv_api
                except Exception as invidious_error:
                    fallback_errors.append(str(invidious_error))
                    raise RuntimeError(
                        "YouTube extraction failed. "
                        f"yt-dlp: {first_error}; "
                        f"fallbacks: {' | '.join(fallback_errors)}"
                    )
            heights = sorted({int(s.get("height")) for s in fallback_meta.get("videoStreams") or [] if s.get("height")}, reverse=True)
            qualities = [q for q in VIDEO_QUALITIES if any(h >= q for h in heights)]
            if not qualities and heights:
                qualities = [max(heights)]
            return {"source": source, "platform": platform, "url": normalized, "title": fallback_meta.get("title") or "YouTube video", "qualities": qualities or [720], "audio": bool(fallback_meta.get("audioStreams")), "piped": fallback_meta}
        if platform == "tiktok":
            data = tikwm_get_data(normalized)
            qualities = []
            if data.get("hdplay"):
                qualities.append("hd")
            if data.get("play"):
                qualities.append("sd")
            if not qualities and data.get("wmplay"):
                qualities.append("sd")
            if not qualities:
                raise RuntimeError(f"TikTok extraction failed: {first_error}")
            return {"source": "tikwm", "platform": platform, "url": normalized, "title": data.get("title") or data.get("desc") or "TikTok video", "qualities": qualities, "audio": bool(data.get("music")), "tikwm": data}
        raise RuntimeError(f"Extraction failed: {first_error}")


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


async def handle_admin_youtube_audio(update, context):
    if context.user_data.get("state") != "admin_youtube_audio":
        return
    user_id = update.effective_user.id
    if not is_admin(user_id):
        context.user_data["state"] = None
        await update.message.reply_text("🔒 Admin only.")
        return

    raw_url = (update.message.text or "").strip()
    if not re.match(r"^https?://(www\\.)?(youtube\\.com|youtu\\.be)/", raw_url, re.I):
        await update.message.reply_text("❌ Please send a valid YouTube video link.")
        return

    status_msg = await update.message.reply_text("⏳ Downloading YouTube audio…")
    output_dir = tempfile.mkdtemp(prefix="tg_yt_audio_")
    try:
        def download_audio():
            # Use Cobalt first so this admin tool does not depend on YouTube
            # authentication/cookies on the Render server.
            try:
                cobalt_data = cobalt_prepare(raw_url, audio=True)
                mp3_path = os.path.join(output_dir, "youtube_audio.mp3")
                download_url_to_file(
                    cobalt_data["url"],
                    mp3_path,
                    headers={"User-Agent": "Mozilla/5.0", "Accept": "*/*"},
                )
                if os.path.isfile(mp3_path) and os.path.getsize(mp3_path) > 0:
                    return mp3_path, cobalt_data.get("filename") or "YouTube Audio"
            except Exception as cobalt_error:
                cobalt_error_text = str(cobalt_error)
            else:
                cobalt_error_text = "Cobalt returned an empty audio file."

            # If Cobalt is unavailable, use the bot's existing public Piped
            # fallback and convert its best audio stream to 192 kbps MP3.
            video_id = youtube_video_id(raw_url)
            if not video_id:
                raise RuntimeError("Could not read the YouTube video ID.")
            try:
                meta = piped_metadata(video_id)
                result = download_from_piped(meta, output_dir, audio=True)
                return result["path"], result.get("title") or "YouTube Audio"
            except Exception as piped_error:
                raise RuntimeError(
                    "Alternative download services failed. "
                    f"Cobalt: {cobalt_error_text[:400]} | "
                    f"Piped: {str(piped_error)[:400]}"
                )

        filepath, title = await asyncio.to_thread(download_audio)
        if os.path.getsize(filepath) > VIDEO_MAX_UPLOAD:
            raise RuntimeError(
                f"The audio file is {format_bytes(os.path.getsize(filepath))}, "
                "which is above Telegram's current 50 MB bot upload limit."
            )

        with open(filepath, "rb") as audio_file:
            await update.message.reply_audio(
                audio=audio_file,
                filename=os.path.basename(filepath),
                title=title[:64],
                caption=f"🎵 {title[:900]}",
                reply_markup=tool_done_kb(),
            )
        await status_msg.edit_text("✅ YouTube audio downloaded successfully.")
    except Exception as e:
        await status_msg.edit_text(f"❌ Audio download failed.\\n\\n{str(e)[:1000]}")
    finally:
        context.user_data["state"] = None
        shutil.rmtree(output_dir, ignore_errors=True)

async def handle_video_download(update, context):
    if context.user_data.get("state") != "awaiting_video_link":
        return
    raw_url = (update.message.text or "").strip()
    if not re.match(r"^https?://", raw_url, re.I):
        await update.message.reply_text("❌ Please send a valid YouTube, TikTok, Instagram, or Facebook URL.")
        return

    status_msg = await update.message.reply_text("🔎 Checking the video and available qualities…")
    try:
        job = await asyncio.to_thread(extract_download_options, raw_url)
        job["chat_id"] = update.effective_chat.id
        context.user_data["video_job"] = job
        context.user_data["state"] = None
        title = job.get("title") or "Video"
        platform = job["platform"].title()
        safe_title = html.escape(title[:120])
        await status_msg.edit_text(
            f"🎬 <b>{html.escape(platform)} Downloader</b>\n\n"
            f"<b>{safe_title}</b>\n\n"
            "Choose the quality you want:",
            reply_markup=video_quality_keyboard(job),
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        context.user_data["state"] = None
        msg = str(e)
        if "Sign in to confirm" in msg or "LOGIN_REQUIRED" in msg:
            msg = (
                "YouTube is currently requiring authentication from this server. "
                "The bot already tried its normal extractor and the public fallback. "
                "For videos that remain protected, the supported option is to provide "
                "a fresh cookies file through YTDLP_COOKIES_FILE."
            )
        await status_msg.edit_text(f"❌ Could not prepare download.\n\n{msg[:900]}")


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
    filepath = None
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
            if job["source"] == "cobalt":
                ext = ".mp3" if audio else ".mp4"
                out = os.path.join(output_dir, "cobalt_media" + ext)
                cobalt_data = cobalt_prepare(job["url"], audio=audio) if audio else job.get("cobalt", {})
                download_url_to_file(cobalt_data["url"], out, headers={"User-Agent": "Mozilla/5.0", "Accept": "*/*"})
                return {"path": out, "title": cobalt_data.get("filename") or job.get("title", "YouTube video")}
            if job["source"] == "ytdlp":
                return ytdlp_download(job["url"], output_dir, height=quality, audio=audio, title_hint=job.get("title", "video"))
            if job["source"] in {"piped", "invidious"}:
                return download_from_piped(job["piped"], output_dir, height=quality, audio=audio)
            if job["source"] == "tikwm":
                return tikwm_download(job["url"], output_dir, quality=quality, audio=audio)
            raise RuntimeError("Unknown download source")

        result = await asyncio.to_thread(do_download)
        filepath = result["path"]
        if not os.path.isfile(filepath):
            raise FileNotFoundError("Downloaded file was not created")

        file_size = os.path.getsize(filepath)
        if file_size > VIDEO_MAX_UPLOAD:
            # Automatic quality fallback for videos: retry one step lower.
            if not audio and job.get("platform") == "youtube":
                lower = [q for q in VIDEO_QUALITIES if q < (quality or 999)]
                if lower:
                    await status_msg.edit_text("📦 That quality is over Telegram's 50 MB bot upload limit. Trying the next lower quality…")
                    for q in lower:
                        try:
                            for name in os.listdir(output_dir):
                                path = os.path.join(output_dir, name)
                                if os.path.isfile(path):
                                    os.unlink(path)
                            result = await asyncio.to_thread(do_download_for_quality, job, output_dir, q)
                            filepath = result["path"]
                            if os.path.getsize(filepath) <= VIDEO_MAX_UPLOAD:
                                quality = q
                                file_size = os.path.getsize(filepath)
                                break
                        except Exception:
                            continue
                
            if file_size > VIDEO_MAX_UPLOAD:
                raise RuntimeError(
                    f"The selected file is {format_bytes(file_size)}, above Telegram's current 50 MB bot upload limit."
                )

        caption = f"✅ {job.get('title', 'Video')[:900]}"
        with open(filepath, "rb") as media:
            if audio:
                await query.message.reply_audio(audio=media, filename=os.path.basename(filepath), caption=caption, reply_markup=tool_done_kb())
            else:
                await query.message.reply_video(video=media, caption=caption, supports_streaming=True, reply_markup=tool_done_kb())
        await status_msg.edit_text("✅ Download completed successfully!")
    except Exception as e:
        await status_msg.edit_text(f"❌ Download failed.\n\n{str(e)[:1000]}")
    finally:
        context.user_data.pop("video_job", None)
        try:
            import shutil
            shutil.rmtree(output_dir, ignore_errors=True)
        except Exception:
            pass


def do_download_for_quality(job, output_dir, quality):
    if job["source"] == "ytdlp":
        return ytdlp_download(job["url"], output_dir, height=quality, audio=False, title_hint=job.get("title", "video"))
    if job["source"] in {"piped", "invidious"}:
        return download_from_piped(job["piped"], output_dir, height=quality, audio=False)
    if job["source"] == "tikwm":
        return tikwm_download(job["url"], output_dir, quality="hd" if quality >= 720 else "sd", audio=False)
    raise RuntimeError("Unknown download source")
    
# --- PDF, WORD, IMAGE COLLECT ---
async def handle_pdf_upload(update, context):
    if context.user_data.get('state') != 'awaiting_pdf':
        return
    if not update.message.document:
        await update.message.reply_text("❌ Please upload a valid PDF document.")
        return
    if update.message.document.mime_type != "application/pdf":
        await update.message.reply_text("❌ The file you uploaded is not a PDF.")
        return

    # Download and store PDF bytes for later processing
    file = await context.bot.get_file(update.message.document.file_id)
    pdf_bytes = BytesIO()
    await file.download_to_memory(pdf_bytes)
    pdf_bytes.seek(0)
    context.user_data['pdf_bytes'] = pdf_bytes

    # Ask user how to process the PDF
    await update.message.reply_text(
        "✅ PDF uploaded successfully!\n\n"
        "How do you want to process it?\n"
        "• Send a page number (e.g., `3`)\n"
        "• Send a range (e.g., `1-5`)\n"
        "• Send `all` to process the entire PDF"
    )
    context.user_data['state'] = 'awaiting_pdf_pages'
async def handle_pdf_pages(update, context):
    if context.user_data.get('state') != 'awaiting_pdf_pages':
        return

    pdf_bytes = context.user_data.get('pdf_bytes')
    if not pdf_bytes:
        await update.message.reply_text("❌ PDF session expired. Please upload the PDF again.")
        context.user_data['state'] = None
        return

    user_input = (update.message.text or "").strip().lower()
    if not user_input:
        await update.message.reply_text("❌ Please send a valid page number, range, or `all`.")
        return

    if user_input == "all":
        pages_to_process = None
    else:
        match = re.fullmatch(r'(\d+)(?:\s*-\s*(\d+))?', user_input)
        if not match:
            await update.message.reply_text("❌ Invalid input. Use a page number, a range like `1-5`, or `all`.")
            return
        start_page = int(match.group(1))
        end_page = int(match.group(2)) if match.group(2) else start_page
        if end_page < start_page:
            await update.message.reply_text("❌ The end page must be greater than or equal to the start page.")
            return
        pages_to_process = (start_page, end_page)

    status_msg = await update.message.reply_text("⏳ Processing PDF...")
    try:
        pdf_bytes.seek(0)
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        total_pages = len(doc)

        if pages_to_process:
            start, end = pages_to_process
            if start < 1 or end > total_pages:
                await status_msg.edit_text(f"❌ Invalid page range. The PDF has only {total_pages} pages.")
                context.user_data['state'] = None
                context.user_data.pop('pdf_bytes', None)
                return
            page_numbers = list(range(start - 1, end))
        else:
            page_numbers = list(range(total_pages))

        text_pages = []
        image_pages = []

        for page_num in page_numbers:
            page = doc.load_page(page_num)
            page_text = page.get_text("text")
            if page_text.strip():
                text_pages.append(page_text)
            images = page.get_images(full=True)
            for img in images:
                base_image = doc.extract_image(img[0])
                img_bytes = BytesIO(base_image["image"])
                img_bytes.seek(0)
                image_pages.append(img_bytes)

        doc.close()

        await status_msg.edit_text("✅ PDF processed. Sending results...")

        if image_pages:
            await update.message.reply_text(f"🖼 Found {len(image_pages)} image(s).")
            for i, img_bytes in enumerate(image_pages, 1):
                await update.message.reply_photo(photo=img_bytes, caption=f"Page Image {i}")

        if text_pages:
            full_text = "\n\n".join(text_pages)
            await update.message.reply_text(f"📄 Text from {len(text_pages)} page(s).")
            for i in range(0, len(full_text), 4000):
                await update.message.reply_text(full_text[i:i+4000])

        if not image_pages and not text_pages:
            await status_msg.edit_text("❌ No text or images found in the selected pages.")

        context.user_data['state'] = None
        context.user_data.pop('pdf_bytes', None)

    except Exception as e:
        await status_msg.edit_text(f"❌ Error: {e}")
        context.user_data['state'] = None
        context.user_data.pop('pdf_bytes', None)
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
    if context.user_data.get('state') != 'awaiting_image_to_text':
        return
    if not update.message.photo:
        await update.message.reply_text("❌ Please upload an image.")
        return

    status_msg = await update.message.reply_text("⏳ please wait...")

    try:
        # 1. Get the photo
        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        img_bytes = BytesIO()
        await file.download_to_memory(img_bytes)
        img_bytes.seek(0)

        # 2. Check if API Key is set
        api_key = os.environ.get('OCR_API_KEY', '')
        if not api_key:
            await status_msg.edit_text("❌ OCR_API_KEY is missing! Please add it to your Render Environment Variables.")
            context.user_data['state'] = None
            return

        # 3. Define the OCR.space API call
        def ocr_api_request():
            import urllib.request
            import json
            
            # Prepare multipart form data
            fields = {
                "apikey": api_key,
                "language": "eng",  # Change to "amh" if you have premium and want Amharic
                "isOverlayRequired": "false",
                "OCREngine": "2"
            }
            files = {"file": img_bytes.getvalue()}
            body, content_type = encode_multipart_formdata(fields, files)

            req = urllib.request.Request(
                "https://api.ocr.space/parse/image",
                data=body,
                headers={"Content-Type": content_type, "User-Agent": "Mozilla/5.0"},
                method="POST"
            )
            
            with urllib.request.urlopen(req, timeout=60) as response:
                result = json.load(response)
            return result

        # 4. Run in a thread so bot doesn't freeze
        result = await asyncio.to_thread(ocr_api_request)

        # 5. Parse the result
        if result.get("IsErroredOnProcessing"):
            error_msg = result.get("ErrorMessage", "Unknown API error")
            await status_msg.edit_text(f"❌ OCR API error: {str(error_msg)[:200]}")
            context.user_data['state'] = None
            return

        extracted_text = result["ParsedResults"][0]["ParsedText"].strip()

        if not extracted_text:
            await status_msg.edit_text("❌ No text found in the image.")
            context.user_data['state'] = None
            return

        # 6. Send the result
        await update.message.reply_text(
            f"📝 **Extracted Text:**\n\n{extracted_text}",
            reply_markup=tool_done_kb()
        )
        await status_msg.edit_text("✅ Text extraction complete!")
        context.user_data['state'] = None

    except ImportError:
        await status_msg.edit_text("❌ `uuid` module is missing! Please check your imports.")
        context.user_data['state'] = None

    except Exception as e:
        await status_msg.edit_text(f"❌ OCR failed: {type(e).__name__}: {str(e)[:500]}")
        context.user_data['state'] = None
async def handle_image_collect(update, context):
    if context.user_data.get('state') != 'awaiting_image_to_pdf':
        return
    if not update.message.photo:
        await update.message.reply_text("❌ Please send a photo.")
        return
    try:
        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        img_bytes = BytesIO()
        await file.download_to_memory(img_bytes)
        img_bytes.seek(0)
        context.user_data.setdefault('pdf_images', []).append(img_bytes)
        count = len(context.user_data['pdf_images'])
        if count >= 10:
            await update.message.reply_text("✅ Maximum 10 images reached. Click 'Done' to create PDF.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Done", callback_data="img_pdf_done")]]))
        else:
            await update.message.reply_text(f"✅ Image {count}/10 added. Send more or click 'Done'.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Done", callback_data="img_pdf_done")]]))
    except Exception as e:
        await update.message.reply_text(f"❌ Error saving image: {e}")

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
async def last_seen_shortcut(update, context):
    """Quick aliases for the existing profile/status lookup."""
    args = context.args or []
    if not args:
        context.user_data["state"] = "profile_query"
        await update.effective_message.reply_text(
            "Send a Telegram @username or numeric ID to check its visible last-seen status.\\n"
            "Shortcuts: /lastseen, /ls, /seen, /online, /status, /last"
        )
        return
    await fetch_profile(update, context, " ".join(args))


async def fetch_profile(update, context, target):
    try:
        entity = await telethon_client.get_entity(target)
        context.user_data["profile_entity"] = entity
        context.user_data["post_entity"] = entity
        context.user_data["story_entity"] = entity

        # Track lookups for any target, not only configured admins. This is
        # a bot-level lookup metric, not a Telegram profile-view API.
        record_profile_visit(entity.id, update.effective_user)

        save_user_history(entity.id, getattr(entity, "username", None), getattr(entity, "first_name", ""), getattr(entity, "last_name", ""))

        first_name = getattr(entity, "first_name", "") or ""
        last_name = getattr(entity, "last_name", "") or ""
        display_name = f"{first_name} {last_name}".strip() or getattr(entity, "title", "Unknown")

        # ---- Fetch online status with exact timestamp when Telegram exposes it ----
        status_text = "⚪ Unknown status"
        try:
            status = entity.status
            if isinstance(status, types.UserStatusOnline):
                status_text = "🟢 Online now"
                until = getattr(status, "expires", None)
                if until:
                    status_text += f"\\n⏳ Online status expires: {until.strftime('%Y-%m-%d %H:%M:%S %Z')}"
            elif isinstance(status, types.UserStatusOffline):
                was_online = getattr(status, "was_online", None)
                if was_online:
                    if was_online.tzinfo:
                        local_seen = was_online.astimezone()
                        now = datetime.now(was_online.tzinfo)
                    else:
                        local_seen = was_online
                        now = datetime.now()
                    seconds = max(0, (now - was_online).total_seconds())
                    exact = local_seen.strftime("%Y-%m-%d %H:%M:%S %Z").strip()
                    ago = str(timedelta(seconds=int(seconds)))
                    status_text = f"⚪ Last seen: {exact}\\n🕒 {ago} ago"
                else:
                    status_text = "⚪ Last seen a long time ago"
            elif isinstance(status, types.UserStatusRecently):
                status_text = "🟡 Last seen recently (exact time hidden by Telegram)"
            elif isinstance(status, types.UserStatusLastWeek):
                status_text = "🟡 Last seen within this week (exact time hidden by Telegram)"
            elif isinstance(status, types.UserStatusLastMonth):
                status_text = "🟡 Last seen within this month (exact time hidden by Telegram)"
            elif isinstance(status, types.UserStatusEmpty):
                status_text = "⚪ Last seen a long time ago"
        except Exception as e:
            print(f"Status fetch error: {e}")

        # ---- Build the full profile text ----
        text = (
            f"<blockquote><b>{display_name}</b>\n"
            f"@{getattr(entity, 'username', None) or 'N/A'}\n\n"
            f"{getattr(entity, 'about', 'No bio')}\n\n"
            f"ID: {entity.id}\n"
            f"Verified: {getattr(entity, 'verified', False)}\n"
            f"Premium: {getattr(entity, 'premium', False)}\n"
            f"Bot: {getattr(entity, 'bot', False)}\n"
            f"Status: {status_text}</blockquote>"
        )

        # ---- Only "View Story" + "Back" buttons ----
        kb = [
            [InlineKeyboardButton("👁 View Story", callback_data="story_start")],
            [InlineKeyboardButton("⬅️ Back", callback_data="more")]
        ]

        # Send profile photo if available
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



async def show_profile_visitors(update, context):
    query = update.callback_query
    user = update.effective_user
    user_id = int(user.id) if user else 0
    if not user or not is_authenticated(user_id):
        await query.answer("🔐 Please authenticate first.", show_alert=True)
        return
    # Visitor records are private to the account whose ID is the target.
    # Never display them in a group chat where other members could see them.
    if not query.message or query.message.chat.type != "private" or query.message.chat.id != user_id:
        await query.answer("🔒 Open Profile Visitors in your private chat with the bot.", show_alert=True)
        return
    await query.answer("Loading your profile lookup visitors…")

    # Remove the previous visitor report so refresh does not stack copies.
    for message_id in context.user_data.pop("profile_visitor_message_ids", []):
        try:
            await context.bot.delete_message(query.message.chat_id, message_id)
        except Exception:
            pass

    rows = get_profile_visits(user_id)
    if not rows:
        chunks = [(
            "👀 <b>WHO VISITED MY PROFILE</b>\n\n"
            "No visits have been recorded through the bot yet.\n\n"
            "<i>This tracks users who open your Telegram profile through this bot's Profile lookup. "
            "Telegram itself does not provide a general profile-viewer list.</i>"
        )]
    else:
        header = [
            "👀 <b>WHO VISITED MY PROFILE</b>",
            "",
            f"Total unique visitors: <b>{len(rows)}</b>",
            "",
        ]
        entries = []
        for idx, (visitor_id, username, first_name, last_name, first_seen, last_seen, count) in enumerate(rows, 1):
            name = f"{first_name or ''} {last_name or ''}".strip() or "Unknown user"
            handle = f"@{username}" if username else f"ID {visitor_id}"
            entries.append(
                f"<b>{idx}.</b> {html.escape(name)} — {html.escape(handle)}\n"
                f"🕒 Last visit: {html.escape(str(last_seen))} • Visits: {count}"
            )

        chunks = []
        current = "\n".join(header)
        for entry in entries:
            candidate = current + ("\n\n" if current else "") + entry
            if len(candidate) > 3900 and current.strip():
                chunks.append(current)
                current = entry
            else:
                current = candidate
        if current.strip():
            chunks.append(current)
        chunks[-1] += "\n\n<i>Only visits made through this bot's Profile lookup are recorded.</i>"

    sent_ids = []
    for chunk in chunks:
        sent = await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=chunk,
            parse_mode=ParseMode.HTML,
        )
        sent_ids.append(sent.message_id)

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Refresh", callback_data="profile_visitors")],
        [InlineKeyboardButton("📖 Who Viewed My Stories", callback_data="story_viewers")],
        [InlineKeyboardButton("⬅️ Back", callback_data="more")],
    ])
    controls = await context.bot.send_message(
        chat_id=query.message.chat_id,
        text="⬆️ <b>Profile visitor controls</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
    )
    sent_ids.append(controls.message_id)
    context.user_data["profile_visitor_message_ids"] = sent_ids



def _story_reaction_text(reaction):
    if reaction is None:
        return ""
    emoticon = getattr(reaction, "emoticon", None)
    if emoticon:
        return str(emoticon)
    document_id = getattr(reaction, "document_id", None)
    if document_id:
        return f"custom emoji {document_id}"
    return "reaction"


def _story_view_user_name(user):
    if user is None:
        return "Unknown user"
    first = getattr(user, "first_name", None) or ""
    last = getattr(user, "last_name", None) or ""
    name = f"{first} {last}".strip()
    username = getattr(user, "username", None)
    if username:
        return f"{name or 'User'} (@{username})"
    return name or f"User {getattr(user, 'id', 'unknown')}"


def _story_view_time(value):
    if value is None:
        return "Unknown time"
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value).strftime("%d %b %Y • %H:%M:%S")
        return value.strftime("%d %b %Y • %H:%M:%S")
    except Exception:
        return str(value)


async def _load_owned_story_posts():
    """Load the connected Telegram account's active + archived stories."""
    me = await telethon_client.get_me()
    if me is None:
        return []

    stories = []
    if GetPeerStoriesRequest is not None:
        try:
            active = await telethon_client(GetPeerStoriesRequest(peer=me))
            stories.extend(_story_items_from_result(active))
        except Exception as e:
            print(f"[Story viewers] Active story fetch skipped: {type(e).__name__}: {e}")

    if GetStoriesArchiveRequest is not None:
        offset_id = 0
        for _ in range(20):
            try:
                archived = await telethon_client(
                    GetStoriesArchiveRequest(peer=me, offset_id=offset_id, limit=100)
                )
            except Exception as e:
                print(f"[Story viewers] Archive fetch stopped: {type(e).__name__}: {e}")
                break
            batch = _story_items_from_result(archived)
            if not batch:
                break
            stories.extend(batch)
            ids = [int(getattr(item, "id", 0) or 0) for item in batch]
            oldest = min([x for x in ids if x > 0], default=0)
            if not oldest or oldest == offset_id:
                break
            offset_id = oldest

    unique = {}
    for story in stories:
        story_id = getattr(story, "id", None)
        if story_id is None or getattr(story, "deleted", False):
            continue
        unique[int(story_id)] = story
    return sorted(unique.values(), key=_story_sort_key, reverse=True)


async def _load_all_story_viewers(peer, story_id):
    """Fetch every available viewer/reaction page for one owned story."""
    if GetStoryViewsListRequest is None:
        raise RuntimeError("This Telethon installation has no story viewer-list support.")

    viewers = []
    users_by_id = {}
    offset = ""
    last_offset = None

    for _ in range(100):
        result = await telethon_client(
            GetStoryViewsListRequest(
                peer=peer,
                id=int(story_id),
                offset=offset,
                limit=100,
            )
        )
        for user in list(getattr(result, "users", []) or []):
            uid = getattr(user, "id", None)
            if uid is not None:
                users_by_id[int(uid)] = user

        batch = list(getattr(result, "views", []) or [])
        viewers.extend(batch)
        next_offset = getattr(result, "next_offset", None)
        if not next_offset or next_offset == last_offset or not batch:
            break
        last_offset = next_offset
        offset = str(next_offset)

    # Keep one record per user, preferring the newest interaction.
    merged = {}
    for view in viewers:
        uid = getattr(view, "user_id", None)
        if uid is None:
            continue
        key = int(uid)
        existing = merged.get(key)
        current_date = getattr(view, "date", 0) or 0
        existing_date = getattr(existing, "date", 0) or 0 if existing else 0
        if existing is None or current_date >= existing_date:
            merged[key] = view

    ordered = list(merged.values())
    ordered.sort(key=lambda item: getattr(item, "date", 0) or 0, reverse=True)
    return ordered, users_by_id


def _story_viewer_text(story, index, total, viewers, users_by_id):
    story_date = getattr(story, "date", None)
    expire_date = getattr(story, "expire_date", None)
    header = f"📖 <b>STORY {index + 1}/{total}</b> • ID {getattr(story, 'id', '?')}"
    if story_date:
        header += f"\n🕒 Posted: {_story_view_time(story_date)}"
    if expire_date:
        header += f"\n⌛ Expires: {_story_view_time(expire_date)}"
    caption = str(getattr(story, "caption", None) or "").strip()
    if caption:
        header += f"\n\n📝 {html.escape(caption[:800])}"

    if not viewers:
        return header + "\n\n👁 <b>Viewers: 0</b>\n\nNo viewer records are currently available for this story."

    lines = [header, "", f"👁 <b>Viewers: {len(viewers)}</b>", ""]
    for idx, view in enumerate(viewers, 1):
        user = users_by_id.get(int(getattr(view, "user_id", 0) or 0))
        name = html.escape(_story_view_user_name(user))
        when = html.escape(_story_view_time(getattr(view, "date", None)))
        reaction = _story_reaction_text(getattr(view, "reaction", None))
        reaction_text = f" • ❤️ {html.escape(reaction)}" if reaction else ""
        lines.append(f"<b>{idx}.</b> {name}\n🕒 {when}{reaction_text}")
    return "\n".join(lines)


async def _delete_story_viewer_messages(context, chat_id):
    message_ids = context.user_data.pop("story_viewer_message_ids", [])
    for message_id in message_ids:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
        except Exception:
            pass


async def show_story_viewers(update, context, index=0):
    query = update.callback_query
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await query.answer("🔒 Admin only", show_alert=True)
        return

    await query.answer("Loading story viewers…")
    lock = context.user_data.get("story_viewers_lock")
    if lock is None:
        lock = asyncio.Lock()
        context.user_data["story_viewers_lock"] = lock

    async with lock:
        try:
            for message_id in context.user_data.pop("profile_visitor_message_ids", []):
                try:
                    await context.bot.delete_message(query.message.chat_id, message_id)
                except Exception:
                    pass

            stories = context.user_data.get("admin_story_posts")
            if stories is None:
                stories = await _load_owned_story_posts()
                context.user_data["admin_story_posts"] = stories

            if not stories:
                await query.edit_message_text(
                    "📖 <b>WHO VIEWED MY STORIES</b>\n\n"
                    "No active or archived stories are available for the connected Telegram account.",
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔄 Refresh", callback_data="story_viewers_refresh")],
                        [InlineKeyboardButton("⬅️ Back", callback_data="profile_visitors")],
                    ]),
                )
                return

            index = max(0, min(int(index), len(stories) - 1))
            story = stories[index]
            peer = await telethon_client.get_input_entity(await telethon_client.get_me())
            viewers, users_by_id = await _load_all_story_viewers(peer, getattr(story, "id", 0))

            await _delete_story_viewer_messages(context, query.message.chat_id)

            # The media is sent separately so the viewer list can contain the
            # full result set without hitting media-caption limits.
            sent_ids = []
            media_path = None
            try:
                media = getattr(story, "media", None)
                if media is not None:
                    media_doc = getattr(media, "document", None)
                    mime = str(getattr(media_doc, "mime_type", "") or "").lower()
                    attrs = list(getattr(media_doc, "attributes", []) or [])
                    is_photo = getattr(media, "photo", None) is not None
                    is_video = ("video" in mime or getattr(media, "video", None) is not None
                                or any(type(attr).__name__.lower().endswith("videoattribute") for attr in attrs))
                    is_gif = ("gif" in mime or getattr(media, "gif", None) is not None
                              or any(type(attr).__name__.lower().endswith("animatedattribute") for attr in attrs))
                    suffix = ".jpg" if is_photo else (".mp4" if is_video else (".gif" if is_gif else ".dat"))
                    media_path = tempfile.NamedTemporaryFile(delete=False, suffix=suffix).name
                    await telethon_client.download_media(media, file=media_path)
                    if os.path.exists(media_path) and os.path.getsize(media_path) > 0:
                        caption = f"📖 Story {index + 1}/{len(stories)} • ID {getattr(story, 'id', '?')}"
                        with open(media_path, "rb") as media_file:
                            if is_photo:
                                sent = await context.bot.send_photo(query.message.chat_id, photo=media_file, caption=caption)
                            elif is_video:
                                sent = await context.bot.send_video(query.message.chat_id, video=media_file, caption=caption, supports_streaming=True)
                            elif is_gif:
                                sent = await context.bot.send_animation(query.message.chat_id, animation=media_file, caption=caption)
                            else:
                                sent = await context.bot.send_document(
                                    query.message.chat_id,
                                    document=InputFile(media_file, filename=f"story_{getattr(story, 'id', 'media')}{suffix}"),
                                    caption=caption
                                )
                        sent_ids.append(sent.message_id)
            except Exception as media_error:
                print(f"[Story viewers] Media send failed: {type(media_error).__name__}: {media_error}")
            finally:
                if media_path:
                    try:
                        os.remove(media_path)
                    except Exception:
                        pass

            full_text = _story_viewer_text(story, index, len(stories), viewers, users_by_id)
            # Telegram text messages are limited to 4096 characters, so split
            # the complete viewer list into chunks while keeping navigation on
            # the final chunk.
            chunks = []
            while len(full_text) > 4096:
                cut = full_text.rfind("\n", 0, 3900)
                if cut < 100:
                    cut = 3900
                chunks.append(full_text[:cut])
                full_text = full_text[cut:].lstrip()
            chunks.append(full_text)

            for chunk in chunks:
                sent = await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=chunk,
                    parse_mode=ParseMode.HTML,
                )
                sent_ids.append(sent.message_id)

            controls = []
            nav = []
            if index > 0:
                nav.append(InlineKeyboardButton("⬅️ Back Story", callback_data=f"admin_story_{index - 1}"))
            if index < len(stories) - 1:
                nav.append(InlineKeyboardButton("Next Story ➡️", callback_data=f"admin_story_{index + 1}"))
            if nav:
                controls.append(nav)
            controls.append([
                InlineKeyboardButton("🔄 Refresh Story", callback_data="story_viewers_refresh"),
                InlineKeyboardButton("👀 Profile Visitors", callback_data="profile_visitors"),
            ])
            controls.append([InlineKeyboardButton("⬅️ Admin Menu", callback_data="more")])
            control_msg = await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="⬆️ <b>Story viewer controls</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(controls),
            )
            sent_ids.append(control_msg.message_id)
            context.user_data["story_viewer_message_ids"] = sent_ids
            context.user_data["admin_story_index"] = index

        except FloodWaitError as e:
            await query.message.reply_text(f"⏳ Telegram rate limit. Please try again in {e.seconds} seconds.")
        except Exception as e:
            print(f"[Story viewers] Failed: {type(e).__name__}: {e}")
            await query.message.reply_text(
                f"❌ Could not load story viewers: {type(e).__name__}",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="profile_visitors")]])
            )


def _story_items_from_result(result):
    """Safely unwrap Telethon story responses into a real Python list.

    Telethon can return stories.PeerStories, whose .stories field contains
    the actual StoryItem vector.  Never call len() on the response wrapper.
    """
    if result is None:
        return []

    # Normal response: stories.PeerStories -> .stories -> StoryItem vector.
    container = getattr(result, "stories", None)
    if container is None:
        return []

    # Some Telethon response wrappers add another .stories layer. Unwrap
    # only a few layers defensively so this stays safe across versions.
    for _ in range(3):
        if isinstance(container, (list, tuple)):
            try:
                return list(container)
            except Exception:
                return []

        nested = getattr(container, "stories", None)
        if nested is None or nested is container:
            break
        container = nested

    if isinstance(container, (list, tuple)):
        try:
            return list(container)
        except Exception:
            return []

    # Telethon vectors are iterable, but avoid len() entirely because
    # PeerStories itself is not a sized collection.
    try:
        return [item for item in container]
    except (TypeError, AttributeError):
        return []

def _story_keyboard(index, total):
    """Build compact, predictable story navigation controls."""
    row=[]
    if index>0: row.append(InlineKeyboardButton("⬅️ Back",callback_data=f"story_nav_{index-1}"))
    if index<total-1: row.append(InlineKeyboardButton("Next ➡️",callback_data=f"story_nav_{index+1}"))
    controls=[row] if row else []
    controls.append([InlineKeyboardButton("🔄 Refresh",callback_data="story_refresh"),InlineKeyboardButton("👤 Profile",callback_data="story_back")])
    return InlineKeyboardMarkup(controls)

def _story_is_active(story):
    if getattr(story,"deleted",False) or getattr(story,"expired",False): return False
    expire_date=getattr(story,"expire_date",None)
    if expire_date:
        try:
            now=datetime.now(expire_date.tzinfo) if getattr(expire_date,"tzinfo",None) else datetime.now()
            if expire_date<=now: return False
        except Exception: pass
    return True

def _story_sort_key(story):
    story_date=getattr(story,"date",None)
    try: return story_date.timestamp() if story_date else float(getattr(story,"id",0))
    except Exception: return float(getattr(story,"id",0))

def _story_caption(story,index,total):
    raw=getattr(story,"caption",None)
    if raw is None: raw=getattr(story,"message",None)
    raw=str(raw or "").strip()
    story_date=getattr(story,"date",None)
    date_text=""
    if story_date:
        try: date_text=story_date.strftime("%d %b %Y • %H:%M")
        except Exception: date_text=str(story_date)
    header=f"📖 Story {index+1}/{total}"
    if date_text: header+=f" • {date_text}"
    if raw: return f"{header}\n\n{raw}",len(raw)>850
    return header,False

def _story_is_video(media):
    document=getattr(media,"document",None)
    mime=str(getattr(document,"mime_type","") or "").lower()
    return "video" in mime or getattr(media,"video",None) is not None

def _compress_story_video_sync(source_path):
    """Best-effort fallback for videos larger than the Bot API upload limit."""
    target_path=source_path + ".compressed.mp4"
    try:
        ffmpeg=imageio_ffmpeg.get_ffmpeg_exe()
        # Two-pass is unnecessary here: use a conservative CRF and cap the
        # bitrate so common large stories can be brought under the limit.
        cmd=[
            ffmpeg,"-y","-i",source_path,
            "-vf","scale=min(640,iw):-2",
            "-c:v","libx264","-preset","veryfast","-crf","30",
            "-maxrate","900k","-bufsize","1800k",
            "-c:a","aac","-b:a","96k","-movflags","+faststart",
            target_path
        ]
        subprocess.run(cmd,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=900,check=True)
        if os.path.exists(target_path) and os.path.getsize(target_path)>0:
            return target_path
    except Exception as e:
        print(f"Story video compression failed: {type(e).__name__}: {e}")
    try:
        if os.path.exists(target_path): os.remove(target_path)
    except Exception: pass
    return None

def _story_media_input(media,file_obj,caption):
    document=getattr(media,"document",None)
    mime=str(getattr(document,"mime_type","") or "").lower()
    if getattr(media,"photo",None) is not None:
        return InputMediaPhoto(media=file_obj,caption=caption)
    if "video" in mime or getattr(media,"video",None) is not None:
        return InputMediaVideo(media=file_obj,caption=caption,supports_streaming=True)
    if "gif" in mime or getattr(media,"gif",None) is not None:
        return InputMediaAnimation(media=file_obj,caption=caption)
    return InputMediaDocument(media=file_obj,caption=caption)

async def restore_story_profile(update,context):
    query=update.callback_query
    entity=context.user_data.get("story_entity")
    if not entity:
        await query.answer("Profile expired. Please search again.",show_alert=True); return
    try:
        first=getattr(entity,"first_name","") or ""; last=getattr(entity,"last_name","") or ""
        name=f"{first} {last}".strip() or getattr(entity,"title","Unknown")
        username=getattr(entity,"username",None); about=getattr(entity,"about",None) or "No bio"
        text=(f"<blockquote><b>{html.escape(name)}</b>\n"
              f"@{html.escape(username or 'N/A')}\n\n{html.escape(about)}\n\nID: {entity.id}</blockquote>")
        kb=InlineKeyboardMarkup([[InlineKeyboardButton("👁 View Story",callback_data="story_start")],
                                 [InlineKeyboardButton("⬅️ Back",callback_data="more")]])
        try:
            await query.edit_message_caption(caption=text,parse_mode=ParseMode.HTML,reply_markup=kb)
        except Exception:
            await query.edit_message_text("👤 <b>Profile</b>\n\nUse <b>View Story</b> to load the latest stories again.",
                                          parse_mode=ParseMode.HTML,reply_markup=kb)
    except Exception as e:
        await query.answer(f"Could not return to profile: {type(e).__name__}",show_alert=True); return
    context.user_data.pop("stories_list",None); context.user_data.pop("story_index",None)
    await query.answer()

async def handle_story_view(update,context,refresh=False):
    query=update.callback_query
    await query.answer("Refreshing stories…" if refresh else "Loading stories…")
    entity=context.user_data.get("story_entity")
    if not entity:
        await query.edit_message_text("❌ No profile selected. Please search a user again."); return
    if GetPeerStoriesRequest is None:
        await query.edit_message_text("❌ Telegram Stories support is unavailable in this Telethon installation."); return

    # Serialize refresh/open operations per user so rapid taps cannot create
    # multiple viewer messages at the same time.
    lock=context.user_data.get("story_lock")
    if lock is None:
        lock=asyncio.Lock()
        context.user_data["story_lock"]=lock

    async with lock:
        try:
            active_result=await telethon_client(GetPeerStoriesRequest(peer=entity))
            active_items=_story_items_from_result(active_result)
            stories=[story for story in active_items if _story_is_active(story)]

            # Expired stories are normally private to their owner, but stories
            # explicitly pinned to a profile are viewable by profile visitors.
            # Include pinned stories so the viewer can continue from recent
            # active stories into older profile-pinned stories.
            if GetPinnedStoriesRequest is not None:
                try:
                    pinned_result=await telethon_client(
                        GetPinnedStoriesRequest(peer=entity,offset_id=0,limit=100)
                    )
                    pinned_items=_story_items_from_result(pinned_result)
                    stories.extend(pinned_items)
                except Exception as pinned_error:
                    print(f"Story pinned fetch skipped: {type(pinned_error).__name__}: {pinned_error}")

            # Deduplicate active/pinned overlap by Telegram story ID.
            unique={}
            for story in stories:
                story_id=getattr(story,"id",None)
                key=("id",int(story_id)) if story_id is not None else (
                    "fallback",id(story)
                )
                unique[key]=story
            stories=list(unique.values())
            stories.sort(key=_story_sort_key,reverse=True)

            if not stories:
                kb=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Refresh",callback_data="story_refresh")],
                                         [InlineKeyboardButton("👤 Profile",callback_data="story_back")]])
                try:
                    await query.edit_message_caption(caption="📭 <b>No viewable stories</b>\n\n"
                                                              "This profile has no active or pinned stories available to this account.",
                                                      parse_mode=ParseMode.HTML,reply_markup=kb)
                except Exception:
                    await query.edit_message_text("📭 <b>No viewable stories</b>\n\n"
                                                   "This profile has no active or pinned stories available to this account.",
                                                   parse_mode=ParseMode.HTML,reply_markup=kb)
                return

            context.user_data["stories_list"]=stories
            context.user_data["story_index"]=0
            await send_story_at_index(update,context,0)
        except FloodWaitError as e:
            await query.edit_message_text(f"⏳ Telegram temporarily rate-limited story loading.\n\nPlease try again in about {e.seconds} seconds.")
        except (ChannelPrivateError,UsernameNotOccupiedError):
            await query.edit_message_text("🔒 Stories are unavailable for this profile.")
        except Exception as e:
            print(f"Story load error: {type(e).__name__}: {e}")
            await query.edit_message_text("❌ Could not load stories right now.\n\n"
                                          "The profile may have restricted stories, the story may have expired, "
                                          "or Telegram may have temporarily rejected the request.",
                                          reply_markup=InlineKeyboardMarkup([
                                              [InlineKeyboardButton("🔄 Refresh",callback_data="story_refresh")],
                                              [InlineKeyboardButton("👤 Profile",callback_data="story_back")]]))

async def send_story_at_index(update,context,index):
    query=update.callback_query
    stories=context.user_data.get("stories_list",[])
    if not isinstance(stories, list):
        try:
            stories=list(stories)
        except TypeError:
            stories=[]
        context.user_data["stories_list"]=stories
    if not stories:
        await query.answer("Stories expired. Refreshing…")
        await handle_story_view(update,context,refresh=True); return
    if index<0 or index>=len(stories):
        await query.answer("No more stories.",show_alert=True); return

    story=stories[index]; total=len(stories)
    context.user_data["story_index"]=index
    caption,long_caption=_story_caption(story,index,total)
    media_caption=caption[:1024]
    media=getattr(story,"media",None)

    if media is None:
        await query.message.edit_text(caption,reply_markup=_story_keyboard(index,total)); return

    tmp=tempfile.NamedTemporaryFile(prefix="story_",delete=False)
    tmp_path=tmp.name; tmp.close()
    try:
        downloaded=await telethon_client.download_media(media,file=tmp_path)
        if not downloaded or not os.path.exists(tmp_path) or os.path.getsize(tmp_path)==0:
            raise RuntimeError("Telegram returned an empty story file")
        upload_path=tmp_path
        compressed_path=None
        file_size=os.path.getsize(tmp_path)
        if file_size>50*1024*1024 and _story_is_video(media):
            status=await asyncio.to_thread(_compress_story_video_sync,tmp_path)
            if status:
                compressed_path=status
                upload_path=status
                file_size=os.path.getsize(status)

        if file_size>50*1024*1024:
            raise ValueError("This story is too large for Telegram's bot upload limit. The automatic video compression could not reduce it enough.")

        keyboard=_story_keyboard(index,total)
        edited=False
        with open(upload_path,"rb") as media_file:
            try:
                await query.message.edit_media(media=_story_media_input(media,media_file,media_caption),
                                               reply_markup=keyboard)
                edited=True
            except Exception as edit_error:
                print(f"Story edit fallback: {type(edit_error).__name__}: {edit_error}")

        if not edited:
            previous_story_message_id=context.user_data.get("story_message_id")
            if previous_story_message_id and previous_story_message_id != query.message.message_id:
                try:
                    await context.bot.delete_message(query.message.chat_id,previous_story_message_id)
                except Exception:
                    pass
            with open(upload_path,"rb") as media_file:
                document=getattr(media,"document",None)
                mime=str(getattr(document,"mime_type","") or "").lower()
                if getattr(media,"photo",None) is not None:
                    sent=await query.message.reply_photo(photo=media_file,caption=media_caption,reply_markup=keyboard)
                elif "video" in mime:
                    try:
                        sent=await query.message.reply_video(video=media_file,caption=media_caption,supports_streaming=True,reply_markup=keyboard)
                    except Exception:
                        media_file.seek(0)
                        sent=await query.message.reply_document(document=media_file,caption=media_caption,reply_markup=keyboard)
                elif "gif" in mime:
                    sent=await query.message.reply_animation(animation=media_file,caption=media_caption,reply_markup=keyboard)
                else:
                    sent=await query.message.reply_document(document=media_file,caption=media_caption,reply_markup=keyboard)
            try: await query.message.delete()
            except Exception: pass
            context.user_data["story_message_id"]=sent.message_id

        old_caption_id=context.user_data.pop("story_caption_message_id",None)
        if old_caption_id:
            try: await context.bot.delete_message(query.message.chat_id,old_caption_id)
            except Exception: pass
        if long_caption:
            full_caption=str(getattr(story,"caption",None) or getattr(story,"message",None) or "").strip()
            if len(full_caption)>850:
                extra=await context.bot.send_message(chat_id=query.message.chat_id,text=f"📝 Full caption:\n\n{full_caption}")
                context.user_data["story_caption_message_id"]=extra.message_id
    except ValueError as e:
        await query.answer(str(e)[:190],show_alert=True)
    except FloodWaitError as e:
        await query.answer(f"Telegram asks us to wait {e.seconds}s.",show_alert=True)
    except Exception as e:
        print(f"Story media error: {type(e).__name__}: {e}")
        await query.answer("This story could not be displayed.",show_alert=True)
        try: await query.message.edit_reply_markup(reply_markup=_story_keyboard(index,total))
        except Exception: pass
    finally:
        try: os.remove(tmp_path)
        except Exception: pass
        try:
            if "compressed_path" in locals() and compressed_path and os.path.exists(compressed_path):
                os.remove(compressed_path)
        except Exception: pass

# --- GLOBAL PUBLIC SEARCH V2 ---
#
# IMPORTANT:
# The bot owns one Telethon user session, so it must NEVER use that session's
# dialogs/history as the definition of "global search". V2 discovers only
# public Telegram peers, then searches those public peers directly. A channel
# does not need to be joined by the account for its public history to be read.
#
# The search cache is intentionally global because only public data is cached.
# The user's result state remains in context.user_data, so filters/pagination
# can never leak between users.

SEARCH_PAGE_SIZE = 8
SEARCH_MAX_QUERY = 80
SEARCH_MAX_RESULTS = 160
SEARCH_PUBLIC_PEER_LIMIT = 50
SEARCH_MESSAGES_PER_PEER = 12
SEARCH_GLOBAL_MESSAGE_LIMIT = 120
SEARCH_CONCURRENCY = 8
SEARCH_TIMEOUT = 7.5
SEARCH_CACHE_TTL = 45.0
SEARCH_DIRECT_PEER_LIMIT = 12

_SEARCH_CACHE = {}
_SEARCH_CACHE_LOCK = asyncio.Lock()


def _search_peer_marked_id(entity):
    try:
        return int(get_peer_id(entity))
    except Exception:
        value = getattr(entity, "id", None)
        return int(value) if value is not None else None


def _search_peer_kind(entity):
    if entity is None:
        return "unknown"
    if getattr(entity, "bot", False):
        return "bot"
    if hasattr(entity, "broadcast"):
        return "channel" if getattr(entity, "broadcast", False) else "group"
    if entity.__class__.__name__ in {"Chat", "ChatForbidden"}:
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
    return f"{first} {last}".strip() or "Unknown"


def _search_peer_link(entity):
    username = (getattr(entity, "username", None) or "").strip().lstrip("@")
    return f"https://t.me/{username}" if username else None


def _search_message_link(entity, message_id):
    username = (getattr(entity, "username", None) or "").strip().lstrip("@")
    return f"https://t.me/{username}/{message_id}" if username and message_id else None


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
    text = (getattr(message, "message", None) or "").strip()
    if re.search(r"https?://|t\.me/|www\.", text, re.I):
        return "link"
    return "text"


def _search_content(message):
    text = (getattr(message, "message", None) or "").strip()
    if text:
        return re.sub(r"\s+", " ", text)[:220]
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
    return labels.get(_search_message_type(message), "💬 Message")


def _search_public_entity(entity):
    """Strict public gate: username + public group/channel only."""
    username = (getattr(entity, "username", None) or "").strip().lstrip("@")
    return bool(username) and len(username) >= 4 and _search_peer_kind(entity) in {"channel", "group"}


def _search_terms(query):
    normalized = re.sub(r"\s+", " ", (query or "").strip().lower())
    raw_tokens = re.findall(r"[\w@#]+", normalized, flags=re.UNICODE)
    tokens = []
    for token in raw_tokens:
        token = token.lstrip("@#").strip()
        if len(token) >= 2 and token not in tokens:
            tokens.append(token)
    return normalized, tokens


def _search_relevance_score(query, entity, message):
    normalized, tokens = _search_terms(query)
    content = re.sub(
        r"\s+",
        " ",
        (getattr(message, "message", None) or "").strip().lower(),
    )
    title = _search_peer_name(entity).lower()
    username = (getattr(entity, "username", None) or "").lower().lstrip("@")

    score = 0.0
    if normalized:
        if normalized in content:
            score += 1000
        if normalized in title:
            score += 850
        if normalized in username:
            score += 900

    matched = 0
    for token in tokens:
        if token in content:
            matched += 1
            score += 180
            if re.search(r"\b" + re.escape(token) + r"\b", content):
                score += 100
        if token in title:
            score += 220
            if title.startswith(token):
                score += 90
        if token in username:
            score += 260
            if username.startswith(token):
                score += 120

    if tokens and matched == len(tokens):
        score += 500
    elif matched:
        score += 80 * matched

    message_date = getattr(message, "date", None)
    if message_date:
        try:
            now = datetime.now(message_date.tzinfo) if getattr(message_date, "tzinfo", None) else datetime.now()
            age_days = max(0.0, (now - message_date).total_seconds() / 86400)
            score += max(0.0, 25.0 - min(age_days, 25.0))
        except Exception:
            pass
    return score


def _search_cache_key(query):
    normalized, _ = _search_terms(query)
    return normalized


async def _search_discover_public_peers(query):
    """
    Discover public Telegram groups/channels by name/username.

    This deliberately does NOT inspect dialogs, participants, private chats,
    or admin-only history. The result is a public peer directory, not a copy
    of the operator's account.
    """
    found = {}
    q = (query or "").strip()
    if not q:
        return found

    # Telegram's global contact search is useful for finding public usernames.
    # Only the returned public group/channel peers are retained.
    try:
        result = await asyncio.wait_for(
            telethon_client(functions.contacts.SearchRequest(q=q, limit=SEARCH_PUBLIC_PEER_LIMIT, hash=0)),
            timeout=SEARCH_TIMEOUT,
        )
        for entity in list(getattr(result, "chats", []) or []) + list(getattr(result, "users", []) or []):
            if _search_public_entity(entity):
                marked = _search_peer_marked_id(entity)
                if marked is not None:
                    found[marked] = entity
    except Exception as exc:
        print(f"[SearchV2] public peer discovery skipped: {type(exc).__name__}: {exc}")

    # A direct @username is deterministic and avoids depending on discovery
    # ranking. This also makes public channel lookup feel instant.
    direct = q.split()[0] if q.startswith("@") else None
    if direct:
        try:
            entity = await asyncio.wait_for(
                telethon_client.get_entity(direct),
                timeout=SEARCH_TIMEOUT,
            )
            if _search_public_entity(entity):
                marked = _search_peer_marked_id(entity)
                if marked is not None:
                    found[marked] = entity
        except Exception:
            pass

    return found


async def _search_public_peer(entity, query, semaphore):
    """Search one public peer without requiring membership."""
    async with semaphore:
        try:
            messages = []
            async for message in telethon_client.iter_messages(
                entity,
                search=query,
                limit=SEARCH_MESSAGES_PER_PEER,
            ):
                messages.append(message)
                if len(messages) >= SEARCH_MESSAGES_PER_PEER:
                    break
            return entity, messages, None
        except Exception as exc:
            return entity, [], exc


def _search_result_from_message(entity, message, query, rank):
    marked = _search_peer_marked_id(entity)
    message_id = getattr(message, "id", None)
    if marked is None or not message_id:
        return None

    return {
        "kind": "message",
        "type": _search_message_type(message),
        "peer_type": _search_peer_kind(entity),
        "peer_id": marked,
        "entity": entity,
        "message": message,
        "link": _search_message_link(entity, message_id),
        "content": _search_content(message),
        "title": _search_peer_name(entity),
        "username": getattr(entity, "username", None),
        "date": getattr(message, "date", None),
        "score": _search_relevance_score(query, entity, message),
        "server_rank": rank,
    }


async def _search_public_index(query):
    """
    Search Telegram's global message index first, then keep only public
    groups/channels. A small public-peer fallback remains for channel/group
    name and @username lookups.
    """
    cache_key = _search_cache_key(query)
    now = time.monotonic()

    async with _SEARCH_CACHE_LOCK:
        cached = _SEARCH_CACHE.get(cache_key)
        if cached and now - cached[0] < SEARCH_CACHE_TTL:
            return list(cached[1]), 0.0, True

    started = time.monotonic()
    results = []
    seen = set()

    # 1) TRUE GLOBAL PUBLIC MESSAGE SEARCH.
    #
    # Telegram exposes separate global search modes for broadcast channels and
    # groups. This avoids using the operator's dialogs as the search universe.
    # We still apply _search_public_entity() because global group/channel
    # results can include private peers too.
    async def _global_public_request(*, broadcasts_only=False, groups_only=False):
        request = functions.messages.SearchGlobalRequest(
            broadcasts_only=broadcasts_only,
            groups_only=groups_only,
            q=query,
            filter=types.InputMessagesFilterEmpty(),
            min_date=0,
            max_date=0,
            offset_rate=0,
            offset_peer=types.InputPeerEmpty(),
            offset_id=0,
            limit=SEARCH_GLOBAL_MESSAGE_LIMIT,
        )
        return await asyncio.wait_for(
            telethon_client(request),
            timeout=SEARCH_TIMEOUT,
        )

    async def _collect_global(result):
        chat_map = {}
        for entity in list(getattr(result, "chats", []) or []):
            if not _search_public_entity(entity):
                continue
            marked = _search_peer_marked_id(entity)
            if marked is not None:
                chat_map[marked] = entity

        for message in list(getattr(result, "messages", []) or []):
            entity = None

            peer_id = getattr(message, "peer_id", None)
            if peer_id is not None:
                try:
                    entity = chat_map.get(int(get_peer_id(peer_id)))
                except Exception:
                    entity = None

            if entity is None:
                candidate = getattr(message, "chat", None)
                if candidate is not None and _search_public_entity(candidate):
                    entity = candidate

            if entity is None or not _search_public_entity(entity):
                continue

            item = _search_result_from_message(
                entity,
                message,
                query,
                len(results) + 1,
            )
            if not item:
                continue

            key = (item["peer_id"], getattr(message, "id", None))
            if key in seen:
                continue
            seen.add(key)
            results.append(item)

    try:
        channel_result, group_result = await asyncio.gather(
            _global_public_request(broadcasts_only=True),
            _global_public_request(groups_only=True),
            return_exceptions=True,
        )
        for label, result in (("channels", channel_result), ("groups", group_result)):
            if isinstance(result, Exception):
                print(f"[SearchV2] global {label} search failed: {type(result).__name__}: {result}")
                continue
            await _collect_global(result)
    except Exception as exc:
        print(f"[SearchV2] global public search failed: {type(exc).__name__}: {exc}")

    results = results[:SEARCH_MAX_RESULTS]
    # 2) PUBLIC PEER DISCOVERY FALLBACK.
    # Only run this when the global index did not give us enough results, or
    # when the user explicitly searched a username/channel handle.
    peers = {}
    if query.startswith("@") or len(results) < SEARCH_PAGE_SIZE:
        peers = await _search_discover_public_peers(query)

    if peers and len(results) < SEARCH_MAX_RESULTS:
        semaphore = asyncio.Semaphore(SEARCH_CONCURRENCY)
        fallback_peers = list(peers.values())[:SEARCH_DIRECT_PEER_LIMIT]
        tasks = [
            asyncio.create_task(_search_public_peer(entity, query, semaphore))
            for entity in fallback_peers
        ]

        try:
            deadline = time.monotonic() + max(1.5, SEARCH_TIMEOUT / 2)
            pending = set(tasks)
            while pending and time.monotonic() < deadline and len(results) < SEARCH_MAX_RESULTS:
                remaining = max(0.1, deadline - time.monotonic())
                done, pending = await asyncio.wait(
                    pending,
                    timeout=remaining,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in done:
                    entity, messages, error = await task
                    if error:
                        continue
                    for message in messages:
                        item = _search_result_from_message(
                            entity,
                            message,
                            query,
                            len(results) + 1,
                        )
                        if not item:
                            continue
                        key = (item["peer_id"], getattr(message, "id", None))
                        if key in seen:
                            continue
                        seen.add(key)
                        results.append(item)
                        if len(results) >= SEARCH_MAX_RESULTS:
                            break
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

    # 3) Add matching public peers even when no message was returned.
    _, tokens = _search_terms(query)
    for entity in peers.values():
        if len(results) >= SEARCH_MAX_RESULTS:
            break

        marked = _search_peer_marked_id(entity)
        if marked is None:
            continue

        name = _search_peer_name(entity)
        username = (getattr(entity, "username", None) or "").lower().lstrip("@")
        haystack = f"{name.lower()} {username}"

        if not (
            query.lower() in haystack
            or any(token in haystack for token in tokens)
        ):
            continue

        if any(item["peer_id"] == marked for item in results):
            continue

        kind = _search_peer_kind(entity)
        results.append({
            "kind": "peer",
            "type": kind,
            "peer_type": kind,
            "peer_id": marked,
            "entity": entity,
            "message": None,
            "link": _search_peer_link(entity),
            "content": (
                f"{'📢' if kind == 'channel' else '👥'} {name}"
                + (
                    f"  @{getattr(entity, 'username', None)}"
                    if getattr(entity, "username", None)
                    else ""
                )
            ),
            "title": name,
            "username": getattr(entity, "username", None),
            "date": None,
            "score": _search_relevance_score(query, entity, None),
            "server_rank": len(results),
        })

    def sort_key(item):
        result_date = item.get("date")
        try:
            date_score = result_date.timestamp() if result_date else 0.0
        except Exception:
            date_score = 0.0
        return (
            float(item.get("score", 0)),
            date_score,
            -int(item.get("server_rank", 0)),
        )

    results.sort(key=sort_key, reverse=True)
    results = results[:SEARCH_MAX_RESULTS]

    elapsed = time.monotonic() - started

    async with _SEARCH_CACHE_LOCK:
        _SEARCH_CACHE[cache_key] = (time.monotonic(), list(results))
        if len(_SEARCH_CACHE) > 128:
            oldest = min(
                _SEARCH_CACHE.items(),
                key=lambda pair: pair[1][0],
            )[0]
            _SEARCH_CACHE.pop(oldest, None)

    return results, elapsed, False

async def fetch_search(update, context, query):
    user_id = update.effective_user.id
    if not is_authenticated(user_id):
        await update.message.reply_text("🔐 Password required.")
        return

    query = re.sub(r"\s+", " ", (query or "").strip())
    query = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", query)
    if len(query) < 2:
        await update.message.reply_text("🔎 Enter at least 2 characters to search public Telegram.")
        return
    if len(query) > SEARCH_MAX_QUERY:
        await update.message.reply_text(f"🔎 Search is limited to {SEARCH_MAX_QUERY} characters for speed.")
        return

    # Every search gets a new user-local session. No shared result list,
    # page number, filter, or search query is ever reused between users.
    search_id = uuid.uuid4().hex[:10]
    for key in (
        "search_results", "search_query", "search_filter", "search_page",
        "search_elapsed", "search_public_only", "search_id",
    ):
        context.user_data.pop(key, None)
    context.user_data["search_id"] = search_id

    status_msg = await update.message.reply_text(
        f"🔎 Searching <b>{html.escape(query)}</b>…",
        parse_mode=ParseMode.HTML,
    )

    try:
        results, elapsed, from_cache = await _search_public_index(query)
        context.user_data["search_results"] = results
        context.user_data["search_query"] = query
        context.user_data["search_filter"] = "all"
        context.user_data["search_page"] = 1
        context.user_data["search_elapsed"] = round(elapsed, 2)
        context.user_data["search_public_only"] = True

        if not results:
            await status_msg.edit_text(
                f"❌ No public results for <b>{html.escape(query)}</b>.\n"
                "Try @username, #hashtag, or another keyword.",
                parse_mode=ParseMode.HTML,
            )
            return

        source = "⚡ cached" if from_cache else f"⚡ {elapsed:.2f}s"
        await status_msg.edit_text(
            f"✅ Found <b>{len(results)}</b> public result(s) • {source}\n"
            "Opening ranked results…",
            parse_mode=ParseMode.HTML,
        )
        await asyncio.sleep(0.15)
        await status_msg.delete()
        await display_search_page(update, context, 1, "all")

    except FloodWaitError as exc:
        await status_msg.edit_text(
            f"⏳ Telegram asked the search worker to slow down for {int(exc.seconds)}s. "
            "No retry storm was started."
        )
    except Exception as exc:
        print(f"[SearchV2] failed: {type(exc).__name__}: {exc}")
        await status_msg.edit_text(
            "❌ Public search failed safely. Please try the query again in a moment."
        )

def _search_exact_match(item, query):
    normalized = " ".join((query or "").strip().lower().split())
    if not normalized:
        return False
    message = item.get("message")
    content = " ".join((getattr(message, "message", None) or "").lower().split()) if message is not None else ""
    title = (item.get("title") or "").lower()
    username = (item.get("username") or "").lower().lstrip("@")
    return normalized in content or normalized in title or normalized in username


def _search_filter_match(item, filter_type):
    if filter_type == "all":
        return True
    if filter_type == "exact":
        return True
    if filter_type == "messages":
        return item.get("kind") == "message"
    if filter_type == "photos":
        return item.get("type") == "photo"
    if filter_type == "videos":
        return item.get("type") == "video"
    if filter_type == "files":
        return item.get("type") == "document"
    if filter_type == "gifs":
        return item.get("type") == "gif"
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
    if filter_type == "users":
        return item.get("peer_type") == "user"
    return True


def _search_filter_buttons(results, active, query):
    """Create only filters that actually have results. This is dynamic."""
    definitions = [
        ("all", "🔎 All"),
        ("exact", "🎯 Exact"),
        ("messages", "💬 Messages"),
        ("photos", "📷 Photos"),
        ("videos", "🎬 Videos"),
        ("files", "📄 Files"),
        ("gifs", "🎞️ GIFs"),
        ("audio", "🎵 Audio"),
        ("links", "🔗 Links"),
        ("channels", "📢 Channels"),
        ("groups", "👥 Groups"),
        ("bots", "🤖 Bots"),
        ("users", "👤 Users"),
    ]

    counts = {}
    for filter_type, _ in definitions:
        if filter_type == "exact":
            counts[filter_type] = sum(1 for item in results if _search_exact_match(item, query))
        else:
            counts[filter_type] = sum(1 for item in results if _search_filter_match(item, filter_type))

    available = []
    for filter_type, label in definitions:
        if filter_type == "all" or counts[filter_type] > 0:
            shown = f"{label} ({counts[filter_type]})"
            if filter_type == active:
                shown = f"✅ {shown}"
            available.append((filter_type, shown))

    rows = []
    for i in range(0, len(available), 3):
        rows.append([
            InlineKeyboardButton(label, callback_data=f"sf_{filter_type}")
            for filter_type, label in available[i:i + 3]
        ])
    return rows, counts


def _search_highlight_html(message, query, limit=240):
    raw = " ".join((getattr(message, "message", None) or "").split()).strip()
    if not raw:
        raw = _search_content(message)
    if not raw:
        return ""
    normalized = " ".join((query or "").strip().lower().split())
    low = raw.lower()
    pos = low.find(normalized) if normalized else -1
    match_text = normalized
    if pos < 0:
        for token in normalized.split():
            p = low.find(token)
            if p >= 0:
                pos = p
                match_text = token
                break
    if len(raw) > limit:
        if pos > 0:
            start = max(0, pos - 80)
            raw = ("…" if start > 0 else "") + raw[start:start + limit] + ("…" if start + limit < len(raw) else "")
        else:
            raw = raw[:limit] + "…"
        low = raw.lower()
        pos = low.find(match_text) if match_text else -1
    if pos < 0:
        return html.escape(raw)
    end = pos + len(match_text)
    return html.escape(raw[:pos]) + "<b>" + html.escape(raw[pos:end]) + "</b>" + html.escape(raw[end:])


async def display_search_page(update, context, page, filter_type=None):
    callback = update.callback_query
    if callback:
        await callback.answer()

    # Per-user search state prevents cross-user result mixing.
    if not context.user_data.get("search_results"):
        target = callback.message if callback else update.message
        if target:
            await target.reply_text("❌ Search session expired. Start a new search.")
        return

    results = context.user_data.get("search_results", [])
    search_query = context.user_data.get("search_query", "")
    active = filter_type or context.user_data.get("search_filter", "all")

    filtered = ([item for item in results if _search_exact_match(item, search_query)]
                if active == "exact" else
                [item for item in results if _search_filter_match(item, active)])
    per_page = SEARCH_PAGE_SIZE
    total_results = len(filtered)
    total_pages = max(1, (total_results + per_page - 1) // per_page)
    page = max(1, min(int(page), total_pages))
    context.user_data["search_page"] = page
    context.user_data["search_filter"] = active

    start = (page - 1) * per_page
    page_items = filtered[start:start + per_page]

    elapsed = context.user_data.get("search_elapsed", 0)
    lines = [
        "<b>🌍 TELEGRAM PUBLIC SEARCH</b>",
        f"🔎 <code>{html.escape(search_query)}</code>",
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
                "channel": "📢", "group": "👥", "user": "👤", "text": "💬",
            }.get(item.get("type"), "🔹")

            if item.get("link"):
                title_html = f'<a href="{html.escape(item["link"], quote=True)}"><b>{peer_name}{username_text}</b></a>'
            else:
                title_html = f"<b>{peer_name}{username_text}</b>"
            lines.append(f"<b>{index}.</b> {icon} {title_html}")
            if item.get("kind") == "message":
                snippet = _search_highlight_html(item.get("message"), search_query)
                if not snippet:
                    snippet = html.escape(item.get("content") or "")
                if snippet:
                    lines.append(f"   {snippet}")
            elif item.get("content"):
                lines.append(f"   {html.escape(item['content'])}")
            lines.append("")

    elapsed_text = f"{elapsed:.2f}s" if elapsed else "—"
    lines.append(f"Page {page}/{total_pages} • {total_results} results • ⚡ {elapsed_text}")
    lines.append("<i>Public groups/channels only • no private dialogs or admin-only chats</i>")
    text = "\n".join(lines)

    kb, counts = _search_filter_buttons(results, active, search_query)

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
            if "message is not modified" not in str(exc).lower():
                raise
    else:
        await update.message.reply_text(
            text,
            reply_markup=markup,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
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
    """Generate the SamiBOT Names assignment report from authorized observations.

    Telegram does not expose an arbitrary user's complete historical username/name
    timeline or exact account creation date. SamiBOT therefore records profile
    snapshots it legitimately observes and never fabricates missing history.
    """
    try:
        target = (target or "").strip()
        if not target:
            await update.message.reply_text("Send a Telegram @username or numeric user ID.")
            return

        entity = await telethon_client.get_entity(target)
        if not isinstance(entity, types.User):
            await update.message.reply_text("❌ That identifier does not resolve to a Telegram user.")
            return

        user_id = int(entity.id)
        username = getattr(entity, "username", None)
        first_name = getattr(entity, "first_name", "") or ""
        last_name = getattr(entity, "last_name", "") or ""

        # Record the current public profile observation. Duplicate snapshots are ignored.
        save_user_history(user_id, username, first_name, last_name)
        history = list(get_user_history(user_id))  # newest -> oldest

        display_name = (" ".join(p for p in (first_name, last_name) if p).strip()
                        or "Unknown")
        current_tag = f" (@{username})" if username else ""

        # Country: only use a phone number when the connected authorized Telegram
        # account is actually allowed to see it. Never infer country from username/ID.
        country = "Unknown / unavailable"
        phone = getattr(entity, "phone", None)
        if phone:
            try:
                import phonenumbers
                parsed = phonenumbers.parse("+" + str(phone), None)
                region = phonenumbers.region_code_for_number(parsed)
                if region:
                    country = region
            except Exception:
                country = "Unknown / unavailable"

        # A single snapshot is the current observation, not historical evidence.
        distinct_snapshots = len(history)
        history_points = 40 if distinct_snapshots >= 2 else 0
        country_points = 30 if country != "Unknown / unavailable" else 0

        # Telegram's public API does not provide exact account creation timestamps.
        # Keep this at zero unless an authorized external dataset is integrated later.
        creation_date = "Unknown / unavailable"
        creation_points = 0
        total_points = history_points + country_points + creation_points

        lines = [
            "<blockquote>🔎 <b>NAMES ASSIGNMENT REPORT</b>",
            "",
            f"👤 <b>Current name:</b> {html.escape(display_name)}{html.escape(current_tag)}",
            f"🆔 <b>User ID:</b> <code>{user_id}</code>",
            "",
            "📜 <b>USERNAME / NAME HISTORY (newest → oldest)</b>",
        ]

        seen_profiles = set()
        number = 0
        for old_username, old_first, old_last, date in history:
            old_name = " ".join(
                p for p in (old_first or "", old_last or "") if p
            ).strip() or "Unknown"
            profile_key = (
                (old_username or "").casefold(),
                old_name.casefold()
            )
            if profile_key in seen_profiles:
                continue
            seen_profiles.add(profile_key)
            number += 1
            shown_username = f"@{old_username}" if old_username else "No username"
            shown_date = str(date)[:19].replace("T", " ")
            lines.append(
                f"{number}. <b>{html.escape(shown_username)}</b> — "
                f"{html.escape(old_name)} "
                f"<i>[observed {html.escape(shown_date)} UTC]</i>"
            )

        if number == 0:
            lines.append("No recorded profile observations.")

        lines.extend([
            "",
            f"🌍 <b>Country:</b> {html.escape(country)}",
            "   Source: visible phone-number region only; no username/ID guessing.",
            "",
            f"📅 <b>Account creation:</b> {html.escape(creation_date)}",
            "   Telegram does not expose an exact creation timestamp for arbitrary users.",
            "",
            "📊 <b>ASSIGNMENT SCORE</b>",
            f"• Name/username history: <b>{history_points}/40</b>",
            f"• Country evidence: <b>{country_points}/30</b>",
            f"• Account creation date: <b>{creation_points}/30</b>",
            f"• <b>TOTAL: {total_points}/100</b>",
            "",
            "ℹ️ Historical entries are snapshots SamiBOT legitimately observed and stored.",
            "It cannot recover changes that happened before the first observation.",
            "</blockquote>",
        ])

        await update.message.reply_text(
            "\n".join(lines),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except Exception as e:
        await update.message.reply_text(
            "❌ Could not generate the Names report: " +
            html.escape(str(e)[:1000]),
            parse_mode=ParseMode.HTML,
        )

async def process_auto_responder(event):
    if not event.is_private or event.out:
        return
    text = event.raw_text or ""
    if not text.strip():
        return
    try:
        sender = await event.get_sender()
        if sender is None:
            return
        if getattr(sender, 'bot', False):
            return
        me = await telethon_client.get_me()
        if sender.id == me.id:
            return
    except Exception:
        return

    try:
        reply = ar_find_reply(text)
    except Exception as e:
        print(f"Auto-responder lookup error: {e}")
        return
    if not reply:
        return
    try:
        await event.reply(reply)
    except Exception as e:
        print(f"Auto-responder reply error: {e}")


@telethon_client.on(events.NewMessage(incoming=True))
async def inbox_listener(event):
    if event.is_private and not event.out:
        try:
            sender = await event.get_sender()
            add_inbox_message(
                event.chat_id, sender.id,
                getattr(sender, 'first_name', 'Unknown'),
                getattr(sender, 'username', 'N/A'),
                event.raw_text if event.raw_text else "",
                get_media_type(event), str(event.date)
            )
        except Exception as e:
            print(f"Inbox Error: {e}")

        try:
            await process_message_manager_incoming(event)
        except Exception as e:
            print(f"Message Manager Error: {e}")

        try:
            await process_auto_responder(event)
        except Exception as e:
            print(f"Auto-Responder Error: {e}")
# MESSAGE MANAGER 
# The manager is a gatekeeper for private messages received by the connected
# Telethon user account. It does not change Telegram's native block list.
PTB_BOT = None
MM_DB_ID = 1

# --- FIX 1: Guarantee the table + default row exist. ---
def mm_init_db():
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS message_manager_rules (
        id INTEGER PRIMARY KEY,
        messaging INTEGER NOT NULL DEFAULT 1,
        block_everyone_until REAL NOT NULL DEFAULT 0,
        blocked_users TEXT NOT NULL DEFAULT '{}',
        filter_links_until REAL NOT NULL DEFAULT 0,
        filter_videos_until REAL NOT NULL DEFAULT 0,
        keyword_rules TEXT NOT NULL DEFAULT '{}'
    )''')
    c.execute('''INSERT OR IGNORE INTO message_manager_rules
        (id, messaging, block_everyone_until, blocked_users, filter_links_until, filter_videos_until, keyword_rules)
        VALUES (1, 1, 0, '{}', 0, 0, '{}')''')
    conn.commit()
    conn.close()

mm_init_db()

def mm_get_rules():
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute('SELECT messaging, block_everyone_until, blocked_users, filter_links_until, filter_videos_until, keyword_rules FROM message_manager_rules WHERE id=1')
    row = c.fetchone()
    conn.close()
    if not row:
        return {'messaging': 1, 'block_everyone_until': 0, 'blocked_users': {}, 'filter_links_until': 0, 'filter_videos_until': 0, 'keyword_rules': {}}
    try: blocked = json.loads(row[2] or '{}')
    except Exception: blocked = {}
    try: keywords = json.loads(row[5] or '{}')
    except Exception: keywords = {}
    return {'messaging': int(row[0] or 0), 'block_everyone_until': float(row[1] or 0), 'blocked_users': blocked if isinstance(blocked, dict) else {}, 'filter_links_until': float(row[3] or 0), 'filter_videos_until': float(row[4] or 0), 'keyword_rules': keywords if isinstance(keywords, dict) else {}}

# --- FIX 2: Use INSERT OR REPLACE (solves "Block Everyone doesn't persist"). ---
def mm_save(r):
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute('''INSERT OR REPLACE INTO message_manager_rules
        (id, messaging, block_everyone_until, blocked_users, filter_links_until, filter_videos_until, keyword_rules)
        VALUES (1, ?, ?, ?, ?, ?, ?)''',
        (int(r.get('messaging',1)), float(r.get('block_everyone_until',0)), json.dumps(r.get('blocked_users',{}), ensure_ascii=False), float(r.get('filter_links_until',0)), float(r.get('filter_videos_until',0)), json.dumps(r.get('keyword_rules',{}), ensure_ascii=False)))
    conn.commit()
    conn.close()
    check = mm_get_rules()
    if mm_active(check['block_everyone_until']) != mm_active(float(r.get('block_everyone_until', 0))):
        print(f"⚠️ mm_save verification mismatch: wrote block_everyone_until={r.get('block_everyone_until')} but read back {check['block_everyone_until']}")

def mm_active(until):
    return until == -1 or until > time.time()

def mm_left(until):
    if until == -1: return 'Permanent'
    if until <= time.time(): return 'Expired'
    sec=int(until-time.time())
    if sec < 60: return f'{sec}s'
    if sec < 3600: return f'{sec//60}m {sec%60}s'
    if sec < 86400: return f'{sec//3600}h {(sec%3600)//60}m'
    return f'{sec//86400}d {(sec%86400)//3600}h'

def mm_duration(value):
    v=(value or '').strip().lower()
    if v in {'permanent','perm','forever'}: return -1
    m=re.fullmatch(r'(\d+)\s*(seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h|days?|d|months?|mos?|mo)', v)
    if not m: return None
    n=int(m.group(1)); u=m.group(2)
    if n <= 0: return None
    if u.startswith('s'): sec=n
    elif u.startswith('m') and not u.startswith('mo'): sec=n*60
    elif u.startswith('h'): sec=n*3600
    elif u.startswith('d'): sec=n*86400
    else: sec=n*30*86400
    return time.time()+sec

def mm_duration_kb(prefix):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('30 seconds', callback_data=f'{prefix}|30s'), InlineKeyboardButton('1 minute', callback_data=f'{prefix}|1m')],
        [InlineKeyboardButton('1 hour', callback_data=f'{prefix}|1h'), InlineKeyboardButton('1 day', callback_data=f'{prefix}|1d')],
        [InlineKeyboardButton('1 month', callback_data=f'{prefix}|1mo'), InlineKeyboardButton('♾️ Permanent', callback_data=f'{prefix}|perm')],
        [InlineKeyboardButton('✏️ Custom duration', callback_data=f'{prefix}|custom'), InlineKeyboardButton('⬅️ Back', callback_data='message_manager')]
    ])

def mm_main_kb():
    r=mm_get_rules(); state='🟢 ON' if r['messaging'] else '🔴 OFF'
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f'💬 Messaging: {state}', callback_data='mm_toggle')],
        [InlineKeyboardButton('🚫 Block Messages', callback_data='mm_block'), InlineKeyboardButton('🔎 Filter Messages', callback_data='mm_filter')],
        [InlineKeyboardButton('📋 Active Rules', callback_data='mm_rules'), InlineKeyboardButton('👥 Blocked Users', callback_data='mm_blocked')],
        [InlineKeyboardButton('📩 Forwarding', callback_data='mm_forwarding')],
        [InlineKeyboardButton('⬅️ Back', callback_data='more')]
    ])

async def mm_show(update, context, text=None):
    q=update.callback_query
    msg=q.message if q else update.message
    if q:
        try: await q.edit_message_text(text or '💬 MESSAGE MANAGER\n\nControl incoming private messages received by the connected Telegram account.', reply_markup=mm_main_kb())
        except Exception: await msg.reply_text(text or '💬 MESSAGE MANAGER', reply_markup=mm_main_kb())
    else: await msg.reply_text(text or '💬 MESSAGE MANAGER', reply_markup=mm_main_kb())

async def mm_block_menu(update, context):
    r=mm_get_rules(); status='🟢 Active: '+mm_left(r['block_everyone_until']) if mm_active(r['block_everyone_until']) else '⚪ Not active'
    kb=InlineKeyboardMarkup([
        [InlineKeyboardButton('👥 Block Everyone', callback_data='mm_block_everyone'), InlineKeyboardButton('👤 Block Specific User', callback_data='mm_block_user')],
        [InlineKeyboardButton('🟢 Turn OFF Block Everyone', callback_data='mm_unblock_everyone')],
        [InlineKeyboardButton('📋 Blocked Users', callback_data='mm_blocked')],
        [InlineKeyboardButton('⬅️ Back', callback_data='message_manager')]
    ])
    await update.callback_query.edit_message_text('🚫 BLOCK MESSAGES\n\n'+status+'\n\nBlock rules stop forwarding and silently delete incoming messages.', reply_markup=kb)

async def mm_filter_menu(update, context):
    r=mm_get_rules()
    def st(x): return mm_left(x) if mm_active(x) else 'OFF'
    kb=InlineKeyboardMarkup([
        [InlineKeyboardButton(f'🔗 Links ({st(r["filter_links_until"])})', callback_data='mm_filter_links'), InlineKeyboardButton(f'🎥 Videos ({st(r["filter_videos_until"])})', callback_data='mm_filter_videos')],
        [InlineKeyboardButton('🔤 Keywords', callback_data='mm_filter_keywords')],
        [InlineKeyboardButton('📋 Active Filters', callback_data='mm_filters')],
        [InlineKeyboardButton('⬅️ Back', callback_data='message_manager')]
    ])
    await update.callback_query.edit_message_text('🔎 FILTER MESSAGES\n\nLinks = messages containing URLs.\nVideos = incoming video messages.\nKeywords = messages containing a configured word/phrase.', reply_markup=kb)

def mm_has_link(text):
    return bool(re.search(r'(https?://|www\.|t\.me/|telegram\.me/|www\.)', text or '', re.I))

def mm_is_video(event):
    return bool(getattr(event,'video',None)) or (getattr(event,'document',None) is not None and str(getattr(getattr(event,'document',None),'mime_type','')).startswith('video/'))

def mm_reason(event, user_id, text):
    r=mm_get_rules(); now=time.time()
    # Owner/admins are never blocked by manager rules.
    if user_id in ADMIN_IDS: return None
    # Never block the bot itself (prevents loop)
    try:
        me = telethon_client.loop.run_until_complete(telethon_client.get_me())
        if user_id == me.id:
            return None
    except Exception:
        pass
    # 🛑 NEW: Explicitly ignore the bot's own ID (PTSS: 8958561939)
    if user_id == 8958561939: return None
    until=float(r.get('block_everyone_until',0))
    if mm_active(until): return f'Block everyone ({mm_left(until)})'
    item=r.get('blocked_users',{}).get(str(user_id))
    if item is not None:
        try: until=float(item)
        except Exception: until=0
        if mm_active(until): return f'User block ({mm_left(until)})'
    if mm_active(float(r.get('filter_links_until',0))) and mm_has_link(text): return f'Link filter ({mm_left(r["filter_links_until"])})'
    if mm_active(float(r.get('filter_videos_until',0))) and mm_is_video(event): return f'Video filter ({mm_left(r["filter_videos_until"])})'
    low=(text or '').lower()
    for k,v in r.get('keyword_rules',{}).items():
        try: active=mm_active(float(v))
        except Exception: active=False
        if active and k.lower() in low: return f'Keyword filter: {k} ({mm_left(float(v))})'
    if not r.get('messaging',1): return 'Messaging is OFF'
    return None

# --- FIX 4: Silently delete the message, NO REPLY at all. ---
async def mm_refuse(event, reason):
    try:
        await event.delete(revoke=True)
    except Exception as e:
        print('Manager revoke error:', e)
    # NOTE: No response message is sent.

def mm_message_preview(event):
    text=(getattr(event,'raw_text','') or '').strip()
    if text: return text[:1500]
    if getattr(event,'photo',None): return '🖼️ Photo'
    if getattr(event,'video',None): return '🎥 Video'
    if getattr(event,'voice',None): return '🎤 Voice message'
    if getattr(event,'audio',None): return '🎵 Audio'
    if getattr(event,'document',None): return '📄 Document'
    return '📎 Media message'

# --- FIX 5: Exclude bot's own ID from forwarding and deduplicate admin list. ---
async def mm_forward(event):
    if not ADMIN_IDS: print('Message Manager: ADMIN_IDS is empty; forwarding skipped.'); return
    try:
        bot_me = await telethon_client.get_me()
        bot_id = bot_me.id
    except Exception:
        bot_id = None

    admin_list = list(set(ADMIN_IDS))
    if bot_id:
        admin_list = [aid for aid in admin_list if aid != bot_id]
    if not admin_list:
        print('Message Manager: No valid admin recipients after filtering.')
        return

    sender=await event.get_sender()
    name=' '.join(x for x in [getattr(sender,'first_name',''),getattr(sender,'last_name','')] if x).strip() or 'Unknown'
    username=getattr(sender,'username',None)
    header=f'📩 NEW MESSAGE\n\n👤 From: {name}\n🆔 User ID: {sender.id}\n🔗 Username: @{username}' if username else f'📩 NEW MESSAGE\n\n👤 From: {name}\n🆔 User ID: {sender.id}\n🔗 Username: N/A'
    preview=mm_message_preview(event)
    for admin_id in admin_list:
        try:
            if PTB_BOT:
                kb=InlineKeyboardMarkup([[InlineKeyboardButton('💬 Reply',callback_data=f'mm_reply|{sender.id}'), InlineKeyboardButton('🚫 Block User',callback_data=f'mm_block_from_message|{sender.id}')]])
                await PTB_BOT.send_message(admin_id, header+'\n\n'+preview[:3500], reply_markup=kb)
            else:
                await telethon_client.send_message(admin_id, header+'\n\n👇 Original message:\n'+preview)
            if getattr(event,'media',None):
                await telethon_client.send_file(admin_id, event.media, caption='📎 Original media')
        except Exception as e:
            print(f'Message Manager forwarding error to {admin_id}: {e}')

# --- FIX 6: Ignore events sent by the bot itself (kills infinite loops). ---
async def process_message_manager_incoming(event):
    if not event.is_private or event.out: return False
    try:
        me = await telethon_client.get_me()
        if event.sender_id == me.id:
            return False
    except Exception:
        pass
    try:
        sender=await event.get_sender(); 
        # 🛑 NEW: Ignore any bot messages (prevents spam from bot itself)
        if getattr(sender, 'bot', False):
            return False
        
        uid=sender.id; text=event.raw_text or ''
        reason=mm_reason(event,uid,text)
        if reason:
            await mm_refuse(event,reason)
            return True
        await mm_forward(event)
        return True
    except Exception as e:
        print('Message Manager incoming error:',e)
        return False
async def mm_active_rules(update, context):
    r=mm_get_rules(); lines=['📋 ACTIVE RULES','']
    lines.append('💬 Messaging: '+('🟢 ON' if r['messaging'] else '🔴 OFF'))
    if mm_active(r['block_everyone_until']): lines.append('🚫 Everyone blocked: '+mm_left(r['block_everyone_until']))
    for uid,u in list(r['blocked_users'].items()):
        try:
            if mm_active(float(u)): lines.append(f'👤 User {uid}: {mm_left(float(u))}')
        except: pass
    if mm_active(r['filter_links_until']): lines.append('🔗 Links: '+mm_left(r['filter_links_until']))
    if mm_active(r['filter_videos_until']): lines.append('🎥 Videos: '+mm_left(r['filter_videos_until']))
    for k,u in r['keyword_rules'].items():
        try:
            if mm_active(float(u)): lines.append(f'🔤 {k}: {mm_left(float(u))}')
        except: pass
    await update.callback_query.edit_message_text('\n'.join(lines), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ Back',callback_data='message_manager')]]))

async def mm_show_blocked(update, context):
    r=mm_get_rules(); rows=[]
    for uid,u in list(r['blocked_users'].items()):
        try:
            if mm_active(float(u)):
                rows.append([InlineKeyboardButton(f'👤 {uid} • {mm_left(float(u))}', callback_data=f'mm_edit_user|{uid}')])
        except: pass
    rows.append([InlineKeyboardButton('➕ Block Specific User',callback_data='mm_block_user')])
    rows.append([InlineKeyboardButton('⬅️ Back',callback_data='mm_block')])
    await update.callback_query.edit_message_text('👥 BLOCKED USERS\n\nTap a user to edit duration or unblock.',reply_markup=InlineKeyboardMarkup(rows))

async def mm_show_filters(update, context):
    r=mm_get_rules(); rows=[]
    if mm_active(r['filter_links_until']): rows.append([InlineKeyboardButton('🔗 Links • '+mm_left(r['filter_links_until']),callback_data='mm_filter_links')])
    if mm_active(r['filter_videos_until']): rows.append([InlineKeyboardButton('🎥 Videos • '+mm_left(r['filter_videos_until']),callback_data='mm_filter_videos')])
    for k,u in r['keyword_rules'].items():
        try:
            if mm_active(float(u)):
                token=hashlib.sha1(k.encode('utf-8')).hexdigest()[:10]
                rows.append([InlineKeyboardButton(f'🔤 {k[:25]} • {mm_left(float(u))}',callback_data=f'mm_kw_edit|{token}'), InlineKeyboardButton('🗑️',callback_data=f'mm_kw_remove|{token}')])
        except: pass
    rows.append([InlineKeyboardButton('➕ Add Keyword',callback_data='mm_filter_keywords')])
    rows.append([InlineKeyboardButton('⬅️ Back',callback_data='mm_filter')])
    await update.callback_query.edit_message_text('📋 ACTIVE FILTERS\n\nChoose a rule to edit it, or remove a keyword.',reply_markup=InlineKeyboardMarkup(rows))

async def mm_set_rule_duration(update, context, kind, value):
    q=update.callback_query; r=mm_get_rules(); until=mm_duration(value)
    if until is None:
        await q.answer('Invalid duration',show_alert=True); return
    if kind=='everyone': r['block_everyone_until']=until
    elif kind=='links': r['filter_links_until']=until
    elif kind=='videos': r['filter_videos_until']=until
    elif kind.startswith('user:'): r['blocked_users'][kind.split(':',1)[1]]=until
    elif kind.startswith('keyword:'):
        token=kind.split(':',1)[1]; keyword=context.user_data.get('mm_keyword_tokens',{}).get(token)
        if keyword is None:
            for k in r['keyword_rules']:
                if hashlib.sha1(k.encode('utf-8')).hexdigest()[:10]==token: keyword=k; break
        if keyword: r['keyword_rules'][keyword]=until
        else: await q.answer('Keyword no longer exists',show_alert=True); return
    mm_save(r)
    for _key in ('mm_input','mm_duration_kind','mm_reply_to'): context.user_data.pop(_key,None)
    await q.answer('Saved')
    if kind=='everyone': await mm_block_menu(update,context)
    elif kind in {'links','videos'}: await mm_filter_menu(update,context)
    elif kind.startswith('user:'): await mm_show_blocked(update,context)
    else: await mm_show_filters(update,context)

async def handle_mm_callback(update, context, data):
    q=update.callback_query; uid=update.effective_user.id
    if not is_authenticated(uid) or not is_admin(uid): await q.answer('Admin only',show_alert=True); return True
    if data=='message_manager':
        for _key in ('mm_input','mm_duration_kind','mm_reply_to'): context.user_data.pop(_key,None)
        await q.answer(); await mm_show(update,context); return True
    if data=='mm_toggle':
        r=mm_get_rules(); r['messaging']=0 if r['messaging'] else 1; mm_save(r); await q.answer('Messaging '+('ON' if r['messaging'] else 'OFF')); await mm_show(update,context); return True
    if data=='mm_block': await q.answer(); await mm_block_menu(update,context); return True
    if data=='mm_filter': await q.answer(); await mm_filter_menu(update,context); return True
    if data=='mm_rules': await q.answer(); await mm_active_rules(update,context); return True
    if data=='mm_blocked': await q.answer(); await mm_show_blocked(update,context); return True
    if data=='mm_filters': await q.answer(); await mm_show_filters(update,context); return True
    if data=='mm_forwarding':
        await q.answer(); admins=', '.join(map(str,ADMIN_IDS)) or 'NONE'
        await q.edit_message_text('📩 FORWARDING\n\nIncoming private messages are forwarded to these ADMIN_IDS:\n'+admins+'\n\nThe connected Telethon account must be able to message those IDs, and each admin should start this bot to receive Bot API controls.',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ Back',callback_data='message_manager')]])); return True
    if data in {'mm_block_everyone','mm_filter_links','mm_filter_videos'}:
        await q.answer(); prefix={'mm_block_everyone':'mm_dur|everyone','mm_filter_links':'mm_dur|links','mm_filter_videos':'mm_dur|videos'}[data]
        await q.edit_message_text('⏱️ Choose duration:',reply_markup=mm_duration_kb(prefix)); return True
    if data=='mm_unblock_everyone':
        r=mm_get_rules(); r['block_everyone_until']=0; mm_save(r); await q.answer('Block everyone disabled'); await mm_block_menu(update,context); return True
    if data=='mm_block_user':
        context.user_data['mm_input']='block_user'; await q.answer(); await q.edit_message_text('👤 BLOCK SPECIFIC USER\n\nSend the Telegram numeric user ID, for example: 123456789'); return True
    if data=='mm_filter_keywords':
        context.user_data['mm_input']='keyword'; await q.answer(); await q.edit_message_text('🔤 ADD KEYWORD\n\nSend the keyword or phrase to filter. Example: spam') ; return True
    if data.startswith('mm_dur|'):
        _,kind,value=data.split('|',2)
        if value=='custom': context.user_data['mm_duration_kind']=kind; context.user_data['mm_input']='duration'; await q.answer(); await q.edit_message_text('✏️ Send duration, e.g. `30 seconds`, `5 minutes`, `2 hours`, `3 days`, `1 month`, or `permanent`.'); return True
        await mm_set_rule_duration(update,context,kind,value); return True
    if data.startswith('mm_edit_user|'):
        target=data.split('|',1)[1]; context.user_data['mm_duration_kind']='user:'+target; await q.answer(); await q.edit_message_text(f'👤 User {target}\n\nChoose a new duration:',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('⏱️ Edit duration',callback_data='mm_user_duration|'+target)],[InlineKeyboardButton('🟢 Unblock',callback_data='mm_unblock_user|'+target)],[InlineKeyboardButton('⬅️ Back',callback_data='mm_blocked')]])); return True
    if data.startswith('mm_user_duration|'):
        target=data.split('|',1)[1]; context.user_data['mm_duration_kind']='user:'+target; await q.answer(); await q.edit_message_text(f'👤 User {target}\n\nChoose a new duration:',reply_markup=mm_duration_kb('mm_dur|user:'+target)); return True
    if data.startswith('mm_unblock_user|'):
        target=data.split('|',1)[1]; r=mm_get_rules(); r['blocked_users'].pop(target,None); mm_save(r); await q.answer('User unblocked'); await mm_show_blocked(update,context); return True
    if data.startswith('mm_kw_edit|'):
        token=data.split('|',1)[1]; context.user_data['mm_duration_kind']='keyword:'+token; await q.answer(); await q.edit_message_text('🔤 Choose a new duration:',reply_markup=mm_duration_kb('mm_dur|keyword:'+token)); return True
    if data.startswith('mm_kw_remove|'):
        token=data.split('|',1)[1]; r=mm_get_rules(); key=next((k for k in r['keyword_rules'] if hashlib.sha1(k.encode()).hexdigest()[:10]==token),None)
        if key: r['keyword_rules'].pop(key,None); mm_save(r)
        await q.answer('Keyword removed'); await mm_show_filters(update,context); return True
    if data=='mm_block_from_message':
        target=data.split('|',1)[1] if '|' in data else ''
    if data.startswith('mm_block_from_message|'):
        target=data.split('|',1)[1]; context.user_data['mm_duration_kind']='user:'+target; await q.answer(); await q.edit_message_text(f'🚫 Block user {target}\n\nChoose duration:',reply_markup=mm_duration_kb('mm_dur|user:'+target)); return True
    if data.startswith('mm_reply|'):
        target=data.split('|',1)[1]; context.user_data['mm_reply_to']=int(target); context.user_data['mm_input']='reply'; await q.answer(); await q.message.reply_text(f'💬 Reply mode enabled for user {target}. Send your reply text.'); return True
    return False

async def handle_mm_input(update, context):
    mode=context.user_data.get('mm_input'); text=(update.message.text or '').strip()
    if not mode: return False
    if mode=='block_user':
        if not text.isdigit(): await update.message.reply_text('❌ Send only the numeric Telegram user ID.'); return True
        context.user_data['mm_duration_kind']='user:'+text; context.user_data['mm_input']='duration'; await update.message.reply_text(f'⏱️ Block user {text}. Choose duration:',reply_markup=mm_duration_kb('mm_dur|user:'+text)); return True
    if mode=='keyword':
        if not text or len(text)>100: await update.message.reply_text('❌ Keyword must be 1–100 characters.'); return True
        token=hashlib.sha1(text.lower().encode('utf-8')).hexdigest()[:10]; context.user_data.setdefault('mm_keyword_tokens',{})[token]=text.lower(); context.user_data['mm_duration_kind']='keyword:'+token; context.user_data['mm_input']='duration'; await update.message.reply_text(f'🔤 Keyword: {text}\n\nChoose duration:',reply_markup=mm_duration_kb('mm_dur|keyword:'+token)); return True
    if mode=='duration':
        kind=context.user_data.get('mm_duration_kind'); until=mm_duration(text)
        if until is None: await update.message.reply_text('❌ Invalid duration. Example: `30 seconds` or `2 hours` or `permanent`.'); return True
        r=mm_get_rules()
        if kind=='everyone': r['block_everyone_until']=until
        elif kind=='links': r['filter_links_until']=until
        elif kind=='videos': r['filter_videos_until']=until
        elif kind and kind.startswith('user:'): r['blocked_users'][kind.split(':',1)[1]]=until
        elif kind and kind.startswith('keyword:'):
            token=kind.split(':',1)[1]; key=context.user_data.get('mm_keyword_tokens',{}).get(token)
            if not key:
                key=next((k for k in r['keyword_rules'] if hashlib.sha1(k.encode()).hexdigest()[:10]==token),None)
            if key: r['keyword_rules'][key]=until
        mm_save(r); context.user_data.pop('mm_input',None); await update.message.reply_text('✅ Manager rule saved.'); return True
    if mode=='reply':
        target=context.user_data.get('mm_reply_to')
        if not target: context.user_data.pop('mm_input',None); return True
        try:
            await telethon_client.send_message(int(target),text); await update.message.reply_text('✅ Reply sent to the user.')
        except Exception as e: await update.message.reply_text(f'❌ Reply failed: {e}')
        context.user_data.pop('mm_input',None); context.user_data.pop('mm_reply_to',None); return True
    return False
    
# QR CODE
def create_qr_image_sync(text):
    qr=qrcode.QRCode(version=None,error_correction=qrcode.constants.ERROR_CORRECT_M,box_size=10,border=4)
    qr.add_data(text); qr.make(fit=True)
    return qr.make_image(fill_color='black',back_color='white').convert('RGB')

def scan_qr_image_sync(data):
    """Read one or multiple QR codes locally with OpenCV.
    Uses several preprocessing passes because Telegram images can be
    compressed, rotated, dark, or low-contrast.
    """
    arr=np.frombuffer(data,dtype=np.uint8)
    original=cv2.imdecode(arr,cv2.IMREAD_COLOR)
    if original is None:
        return []

    # Keep processing bounded on Render while improving small QR detection.
    h,w=original.shape[:2]
    scale=1.0
    if max(h,w) < 1600:
        scale=min(2.0,1600/max(h,w))
    elif max(h,w) > 3000:
        scale=3000/max(h,w)
    if scale != 1.0:
        base=cv2.resize(original,None,fx=scale,fy=scale,interpolation=cv2.INTER_CUBIC)
    else:
        base=original

    detector=cv2.QRCodeDetector()
    results=[]

    def add(value):
        value=(value or '').strip()
        if value and value not in results:
            results.append(value)

    def scan(img):
        try:
            ok, decoded, _, _ = detector.detectAndDecodeMulti(img)
            if ok and decoded:
                for value in decoded:
                    add(value)
        except Exception as e:
            print('QR multi pass:',e)
        try:
            value, _, _ = detector.detectAndDecode(img)
            add(value)
        except Exception as e:
            print('QR single pass:',e)

    gray=cv2.cvtColor(base,cv2.COLOR_BGR2GRAY)
    variants=[base,gray]
    try:
        variants.append(cv2.equalizeHist(gray))
    except Exception:
        pass
    try:
        variants.append(cv2.adaptiveThreshold(gray,255,cv2.ADAPTIVE_THRESH_GAUSSIAN_C,cv2.THRESH_BINARY,31,5))
    except Exception:
        pass

    for img in variants:
        scan(img)
        if results:
            break

    return results


async def handle_qr_photo(update, context):
    if context.user_data.get('state')!='qr_scan': return False
    try:
        photo=update.message.photo[-1]
        f=await context.bot.get_file(photo.file_id)
        b=BytesIO()
        await f.download_to_memory(b)
        data=b.getvalue()
        status=await update.message.reply_text('🔍 Scanning QR code…')
        vals=await asyncio.to_thread(scan_qr_image_sync,data)
        if vals:
            await status.edit_text('✅ QR code detected:\n\n'+'\n\n'.join(vals),reply_markup=tool_done_kb())
            context.user_data['state']=None
        else:
            await status.edit_text('❌ No readable QR code was found.\n\nTry a clearer, closer image with the whole QR code visible.')
            # Keep scan mode active so the user can immediately send another image.
    except Exception as e:
        await update.message.reply_text('❌ QR scan failed: '+str(e)[:1000])
    return True


async def handle_qr_create(update, context):
    if context.user_data.get('state')!='qr_create': return False
    text=(update.message.text or '').strip()
    if not text: await update.message.reply_text('❌ Send text or a link.'); return True
    status=await update.message.reply_text('⚙️ Creating QR code…')
    try:
        img=await asyncio.to_thread(create_qr_image_sync,text); out=BytesIO(); img.save(out,'PNG'); out.seek(0)
        await update.message.reply_document(document=out,filename='qr_code.png',caption='✅ QR code created.\n\nContent: '+text[:1000],reply_markup=tool_done_kb()); await status.edit_text('✅ QR code created successfully!')
    except Exception as e: await status.edit_text('❌ QR creation failed: '+str(e)[:1000])
    context.user_data['state']=None; return True


# --- COMMON GROUPS / USER TELEGRAM DISCOVERY -------------------------------
COMMON_GROUPS_PAGE_SIZE = 10
COMMON_GROUPS_MAX = 300
COMMON_GROUPS_TIMEOUT = 18
PUBLIC_DISCOVERY_MAX = 300
PUBLIC_DISCOVERY_PAGES = 4


def _common_group_marked_id(chat):
    try:
        return int(get_peer_id(chat))
    except Exception:
        chat_id = getattr(chat, "id", None)
        return int(chat_id) if chat_id is not None else None


def _common_group_is_group(chat):
    """Match Telegram's group concept, excluding broadcast-only channels."""
    if chat is None:
        return False
    if chat.__class__.__name__ == "Chat":
        return True
    return bool(getattr(chat, "megagroup", False))


def _common_group_is_public(chat):
    return bool((getattr(chat, "username", None) or "").strip())


def _common_group_name(chat):
    return (getattr(chat, "title", None) or "Unnamed group").strip()


def _common_group_link(chat):
    """Return an HTTP link for public chats; private chats get no fake URL."""
    username = (getattr(chat, "username", None) or "").strip().lstrip("@")
    if username:
        return f"https://t.me/{username}"
    return None


def _common_group_message_link(chat, message_id):
    username = (getattr(chat, "username", None) or "").strip().lstrip("@")
    try:
        message_id = int(message_id or 0)
    except Exception:
        message_id = 0
    if username and message_id:
        return f"https://t.me/{username}/{message_id}"
    return _common_group_link(chat)


async def _resolve_common_user(raw_query):
    """
    Resolve a target user using several independent layers.

    Numeric IDs are only directly useful when this MTProto session has
    previously learned the user's access hash. A username is preferred.
    """
    raw = re.sub(r"\s+", " ", (raw_query or "").strip())
    if not raw:
        raise ValueError("Please enter a Telegram username or numeric user ID.")

    clean = raw
    if clean.startswith("https://t.me/") or clean.startswith("http://t.me/"):
        clean = clean.split("t.me/", 1)[1].split("/", 1)[0]
    clean = clean.strip().lstrip("@")

    if clean and not clean.lstrip("-").isdigit():
        try:
            entity = await asyncio.wait_for(
                telethon_client.get_entity(clean),
                timeout=COMMON_GROUPS_TIMEOUT,
            )
            if getattr(entity, "bot", False):
                raise ValueError("The supplied account is a bot, not a user.")
            return entity, "direct username"
        except Exception:
            pass

        try:
            resolved = await asyncio.wait_for(
                telethon_client(
                    functions.contacts.ResolveUsernameRequest(username=clean)
                ),
                timeout=COMMON_GROUPS_TIMEOUT,
            )
            users = list(getattr(resolved, "users", []) or [])
            user = next((u for u in users if not getattr(u, "bot", False)), None)
            if user is not None:
                return user, "contacts.resolveUsername"
        except Exception:
            pass

        if len(clean) >= 2:
            try:
                found = await asyncio.wait_for(
                    telethon_client(
                        functions.contacts.SearchRequest(q=clean, limit=20)
                    ),
                    timeout=COMMON_GROUPS_TIMEOUT,
                )
                users = list(getattr(found, "users", []) or [])
                exact = [
                    u for u in users
                    if (getattr(u, "username", "") or "").lower() == clean.lower()
                    and not getattr(u, "bot", False)
                ]
                if exact:
                    return exact[0], "contacts.search"
                if len(users) == 1 and not getattr(users[0], "bot", False):
                    return users[0], "contacts.search"
            except Exception:
                pass

    if clean.lstrip("-").isdigit():
        try:
            entity = await asyncio.wait_for(
                telethon_client.get_input_entity(int(clean)),
                timeout=COMMON_GROUPS_TIMEOUT,
            )
            entity = await asyncio.wait_for(
                telethon_client.get_entity(entity),
                timeout=COMMON_GROUPS_TIMEOUT,
            )
            if getattr(entity, "bot", False):
                raise ValueError("The supplied account is a bot, not a user.")
            return entity, "cached numeric ID"
        except Exception:
            pass

    try:
        target_id = int(clean) if clean.lstrip("-").isdigit() else None
        async for dialog in telethon_client.iter_dialogs(limit=200):
            entity = getattr(dialog, "entity", None)
            if entity is None or getattr(entity, "bot", False):
                continue
            if target_id is not None and int(getattr(entity, "id", -1)) == abs(target_id):
                return entity, "dialog cache"
            username = (getattr(entity, "username", "") or "").lower().lstrip("@")
            if clean and username == clean.lower():
                return entity, "dialog username cache"
    except Exception:
        pass

    if clean.lstrip("-").isdigit():
        try:
            conn = sqlite3.connect("bot_data.db")
            row = conn.execute(
                "SELECT user_id, username FROM user_history WHERE user_id=? "
                "ORDER BY date DESC LIMIT 1",
                (abs(int(clean)),),
            ).fetchone()
            conn.close()
            if row and row[1]:
                entity = await asyncio.wait_for(
                    telethon_client.get_entity(str(row[1]).lstrip("@")),
                    timeout=COMMON_GROUPS_TIMEOUT,
                )
                if int(getattr(entity, "id", -1)) == abs(int(clean)):
                    return entity, "bot history + username"
        except Exception:
            pass

    raise ValueError(
        "Telegram could not resolve that user from this account. "
        "Try the person's @username. A numeric ID works when this Telethon "
        "session has previously encountered that user."
    )


async def _fetch_common_groups_for_user(user_entity):
    """
    Confirmed membership/shared-group pass.

    Telegram's official getCommonChats method is the authoritative way to
    reproduce the native 'Groups in common' view.
    """
    errors = []

    try:
        all_chats = []
        max_id = 0
        seen_ids = set()

        for _ in range(10):
            result = await asyncio.wait_for(
                telethon_client(
                    functions.messages.GetCommonChatsRequest(
                        user_id=user_entity,
                        max_id=max_id,
                        limit=100,
                    )
                ),
                timeout=COMMON_GROUPS_TIMEOUT,
            )
            batch = [
                chat for chat in (getattr(result, "chats", []) or [])
                if _common_group_is_group(chat)
            ]
            if not batch:
                break

            for chat in batch:
                marked = _common_group_marked_id(chat)
                if marked is not None and marked not in seen_ids:
                    seen_ids.add(marked)
                    all_chats.append(chat)

            if len(all_chats) >= COMMON_GROUPS_MAX:
                break

            ids = [
                int(getattr(chat, "id", 0) or 0)
                for chat in batch
                if getattr(chat, "id", None) is not None
            ]
            next_max = min(ids) if ids else 0
            if not next_max or next_max == max_id:
                break
            max_id = next_max

        return all_chats[:COMMON_GROUPS_MAX], errors
    except Exception as exc:
        errors.append(f"getCommonChats: {type(exc).__name__}: {exc}")

    # Fallback: inspect groups already available to the connected account.
    try:
        username = (getattr(user_entity, "username", None) or "").strip()
        target_id = int(getattr(user_entity, "id", 0) or 0)
        found = []
        async for dialog in telethon_client.iter_dialogs(limit=150):
            chat = getattr(dialog, "entity", None)
            if not _common_group_is_group(chat):
                continue
            try:
                if username:
                    members = await asyncio.wait_for(
                        telethon_client.get_participants(
                            chat, search=username, limit=20
                        ),
                        timeout=5,
                    )
                    if any(
                        int(getattr(m, "id", -1)) == target_id
                        for m in members
                    ):
                        found.append(chat)
                        continue

                if chat.__class__.__name__ == "Chat":
                    members = await asyncio.wait_for(
                        telethon_client.get_participants(chat, limit=200),
                        timeout=5,
                    )
                    if any(
                        int(getattr(m, "id", -1)) == target_id
                        for m in members
                    ):
                        found.append(chat)
            except Exception:
                continue

        return found[:COMMON_GROUPS_MAX], errors
    except Exception as exc:
        errors.append(f"dialog scan: {type(exc).__name__}: {exc}")

    raise RuntimeError(
        "Common-group lookup failed: " + " | ".join(errors[-3:])
    )


def _public_discovery_queries(user_entity):
    """Build conservative public-search terms from the target profile."""
    values = []
    username = (getattr(user_entity, "username", None) or "").strip().lstrip("@")
    first = (getattr(user_entity, "first_name", None) or "").strip()
    last = (getattr(user_entity, "last_name", None) or "").strip()

    if username and len(username) >= 2:
        values.append(username)
        values.append("@" + username)

    full_name = " ".join(x for x in (first, last) if x).strip()
    if len(full_name) >= 3:
        values.append(full_name)

    if len(first) >= 4:
        values.append(first)

    seen = set()
    out = []
    for value in values:
        value = re.sub(r"\s+", " ", value).strip()
        key = value.lower()
        if value and key not in seen:
            seen.add(key)
            out.append(value)
    return out[:4]


async def _search_global_public_chats(query, kind):
    """
    Search public Telegram groups/channels connected to a query.

    This is intentionally an evidence/discovery pass, not a claim that the
    target is a member. It is useful for reproducing reports like the example
    where public message links and public chat links are shown.
    """
    if not query:
        return []

    if kind == "group":
        flags = {"groups_only": True}
    else:
        flags = {"broadcasts_only": True}

    found = []
    seen_messages = set()
    peer_map = {}
    offset_rate = 0
    offset_id = 0
    offset_peer = types.InputPeerEmpty()

    for _ in range(PUBLIC_DISCOVERY_PAGES):
        try:
            result = await asyncio.wait_for(
                telethon_client(
                    functions.messages.SearchGlobalRequest(
                        broadcasts_only=flags.get("broadcasts_only", False),
                        groups_only=flags.get("groups_only", False),
                        users_only=False,
                        q=query,
                        filter=types.InputMessagesFilterEmpty(),
                        min_date=0,
                        max_date=0,
                        offset_rate=offset_rate,
                        offset_peer=offset_peer,
                        offset_id=offset_id,
                        limit=50,
                    )
                ),
                timeout=COMMON_GROUPS_TIMEOUT,
            )
        except Exception:
            break

        for entity in list(getattr(result, "chats", []) or []):
            marked = _common_group_marked_id(entity)
            if marked is not None:
                peer_map[marked] = entity

        messages = list(getattr(result, "messages", []) or [])
        if not messages:
            break

        for message in messages:
            peer = getattr(message, "peer_id", None)
            marked = _common_group_marked_id(peer)
            entity = peer_map.get(marked)
            if entity is None:
                continue
            if not _common_group_is_public(entity):
                continue
            if kind == "group":
                if not _common_group_is_group(entity):
                    continue
            else:
                if _common_group_is_group(entity):
                    continue
                if not getattr(entity, "broadcast", False):
                    continue

            message_id = int(getattr(message, "id", 0) or 0)
            if not message_id:
                continue

            key = (marked, message_id)
            if key in seen_messages:
                continue
            seen_messages.add(key)

            found.append({
                "chat": entity,
                "message": message,
                "message_link": _common_group_message_link(entity, message_id),
                "chat_link": _common_group_link(entity),
                "query": query,
                "kind": kind,
            })

        next_rate = getattr(result, "next_rate", None)
        last_message = messages[-1]
        last_message_id = int(getattr(last_message, "id", 0) or 0)
        last_marked = _common_group_marked_id(
            getattr(last_message, "peer_id", None)
        )
        last_entity = peer_map.get(last_marked)
        if not last_message_id or last_entity is None:
            break

        if next_rate is None:
            last_date = getattr(last_message, "date", None)
            if last_date is None:
                break
            try:
                next_rate = int(last_date.timestamp())
            except Exception:
                break

        try:
            next_peer = await telethon_client.get_input_entity(last_entity)
        except Exception:
            break

        if (
            int(next_rate) == int(offset_rate)
            and last_message_id == int(offset_id)
        ):
            break

        offset_rate = int(next_rate)
        offset_id = last_message_id
        offset_peer = next_peer

        if len(found) >= PUBLIC_DISCOVERY_MAX:
            break

    return found[:PUBLIC_DISCOVERY_MAX]


async def _discover_user_telegram_places(user_entity):
    """
    Multi-pass report used by the admin Common Groups button.

    Passes:
      1) official common-chat API
      2) connected-dialog participant fallback
      3) public group search by username
      4) public channel search by username
      5) public group search by display name
      6) public channel search by display name

    The first two are membership evidence for the connected account.
    Public-search passes are explicitly labeled as discovered public traces.
    They must never be presented as proof that the target joined a chat.
    """
    errors = []

    # Passes 1 + 2: confirmed common groups.
    try:
        common_groups, common_errors = await _fetch_common_groups_for_user(user_entity)
        errors.extend(common_errors)
    except Exception as exc:
        common_groups = []
        errors.append(f"common groups: {type(exc).__name__}: {exc}")

    records = []
    seen_chat_ids = set()

    for chat in common_groups:
        marked = _common_group_marked_id(chat)
        if marked is None or marked in seen_chat_ids:
            continue
        seen_chat_ids.add(marked)
        records.append({
            "chat": chat,
            "message": None,
            "message_link": None,
            "chat_link": _common_group_link(chat),
            "kind": "confirmed_common_group",
            "source": "Telegram common chats",
        })

    # Passes 3-6: public evidence/discovery.
    queries = _public_discovery_queries(user_entity)
    for query in queries:
        for kind in ("group", "channel"):
            try:
                public_hits = await _search_global_public_chats(query, kind)
            except Exception as exc:
                errors.append(
                    f"public {kind} search {query!r}: "
                    f"{type(exc).__name__}: {exc}"
                )
                continue

            for hit in public_hits:
                chat = hit["chat"]
                marked = _common_group_marked_id(chat)
                if marked is None:
                    continue

                # Keep the confirmed group record if the public search also
                # discovers it. For other chats, retain the best message hit.
                existing = next(
                    (
                        item for item in records
                        if _common_group_marked_id(item["chat"]) == marked
                    ),
                    None,
                )
                if existing and existing["kind"] == "confirmed_common_group":
                    continue

                if existing:
                    old_date = getattr(existing.get("message"), "date", None)
                    new_date = getattr(hit.get("message"), "date", None)
                    if old_date and new_date and new_date <= old_date:
                        continue
                    records.remove(existing)

                records.append({
                    "chat": chat,
                    "message": hit.get("message"),
                    "message_link": hit.get("message_link"),
                    "chat_link": hit.get("chat_link"),
                    "kind": "public_discovery",
                    "source": f"public search: {query}",
                })

                if len(records) >= COMMON_GROUPS_MAX:
                    break

            if len(records) >= COMMON_GROUPS_MAX:
                break
        if len(records) >= COMMON_GROUPS_MAX:
            break

    # Stable ordering: confirmed common groups first, then public evidence by
    # newest message date where available.
    def _record_sort_key(item):
        confirmed = 1 if item.get("kind") == "confirmed_common_group" else 0
        message = item.get("message")
        date = getattr(message, "date", None) if message else None
        try:
            stamp = date.timestamp() if date else 0.0
        except Exception:
            stamp = 0.0
        return (confirmed, stamp)

    records.sort(key=_record_sort_key, reverse=True)
    return records[:COMMON_GROUPS_MAX], errors


def _common_groups_keyboard(page, total):
    rows = []
    if page > 0:
        rows.append([
            InlineKeyboardButton(
                "⬅️ Previous",
                callback_data=f"common_page_{page-1}",
            )
        ])
    if (page + 1) * COMMON_GROUPS_PAGE_SIZE < total:
        rows.append([
            InlineKeyboardButton(
                "Next ➡️",
                callback_data=f"common_page_{page+1}",
            )
        ])
    rows.append([
        InlineKeyboardButton("⬅️ More Commands", callback_data="more")
    ])
    return InlineKeyboardMarkup(rows)


async def show_common_groups(update, context, page=0):
    query = update.callback_query
    if not is_admin(update.effective_user.id):
        if query:
            await query.answer("🔒 Admin only", show_alert=True)
        return

    if query:
        await query.answer()

    user_entity = context.user_data.get("common_groups_user")
    records = context.user_data.get("common_groups_results") or []
    total = len(records)
    start = page * COMMON_GROUPS_PAGE_SIZE
    end = min(start + COMMON_GROUPS_PAGE_SIZE, total)
    shown = records[start:end]

    name = (
        f"{getattr(user_entity, 'first_name', '') or ''} "
        f"{getattr(user_entity, 'last_name', '') or ''}".strip()
        or getattr(user_entity, "username", None)
        or str(getattr(user_entity, "id", "user"))
    )

    confirmed_total = sum(
        1 for item in records
        if item.get("kind") == "confirmed_common_group"
    )
    discovered_total = sum(
        1 for item in records
        if item.get("kind") == "public_discovery"
    )

    lines = [
        "👥 <b>TELEGRAM GROUPS & CHANNEL DISCOVERY</b>",
        f"👤 <b>{html.escape(name)}</b>",
        f"✅ Confirmed groups in common: <b>{confirmed_total}</b>",
        f"🔎 Public groups/channels discovered: <b>{discovered_total}</b>",
        "",
        "<i>Public discoveries are evidence of a public Telegram trace, "
        "not proof of membership. Telegram does not expose a universal "
        "API that lists every private group another user joined.</i>",
        "",
    ]

    if not shown:
        lines.append(
            "ℹ️ Nothing was found from the available Telegram account/API paths."
        )
    else:
        for index, item in enumerate(shown, start=start + 1):
            chat = item.get("chat")
            title = html.escape(_common_group_name(chat))
            kind = item.get("kind")
            chat_link = item.get("chat_link")
            message_link = item.get("message_link")
            if kind == "confirmed_common_group":
                prefix = "✅"
            else:
                prefix = "🔎"

            if message_link and chat_link:
                lines.append(
                    f'{index}. {prefix} <a href="{html.escape(message_link, quote=True)}">'
                    f'💬 {title}</a> → '
                    f'<a href="{html.escape(chat_link, quote=True)}">'
                    f'{title}</a>'
                )
            elif chat_link:
                lines.append(
                    f'{index}. {prefix} '
                    f'<a href="{html.escape(chat_link, quote=True)}">👥 {title}</a>'
                )
            else:
                lines.append(f"{index}. {prefix} 👥 {title}")

    text = "\n".join(lines)
    markup = _common_groups_keyboard(page, total)

    if query:
        try:
            await query.edit_message_text(
                text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
                reply_markup=markup,
            )
        except Exception as exc:
            if "message is not modified" not in str(exc).lower():
                raise
    else:
        await update.message.reply_text(
            text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=markup,
        )


async def handle_common_groups_query(update, context, text):
    if not is_admin(update.effective_user.id):
        context.user_data["state"] = None
        await update.message.reply_text("🔒 Admin only.")
        return

    status = None
    try:
        user_entity, resolve_method = await _resolve_common_user(text)
        context.user_data["common_groups_user"] = user_entity

        status = await update.message.reply_text(
            "🔎 Resolving user…\n"
            "1/6 checking confirmed common groups\n"
            "2/6 checking the connected account's group cache\n"
            "3-6/6 searching public Telegram group/channel traces"
        )

        records, errors = await _discover_user_telegram_places(user_entity)
        context.user_data["common_groups_results"] = records
        context.user_data["common_groups_resolve_method"] = resolve_method
        context.user_data["common_groups_errors"] = errors
        context.user_data["state"] = None

        if status:
            try:
                await status.delete()
            except Exception:
                pass

        await show_common_groups(update, context, 0)

    except FloodWaitError as exc:
        context.user_data["state"] = None
        if status:
            try:
                await status.edit_text(
                    f"⏳ Telegram rate limit: wait {int(exc.seconds)} seconds. "
                    "The bot stopped instead of hammering the account."
                )
                return
            except Exception:
                pass
        await update.message.reply_text(
            f"⏳ Telegram rate limit: wait {int(exc.seconds)} seconds."
        )
    except Exception as exc:
        context.user_data["state"] = None
        if status:
            try:
                await status.edit_text(
                    "❌ Telegram discovery failed safely.\n\n"
                    f"{type(exc).__name__}: {str(exc)[:900]}"
                )
                return
            except Exception:
                pass
        await update.message.reply_text(
            "❌ Telegram discovery failed safely.\n\n"
            f"{type(exc).__name__}: {str(exc)[:900]}"
        )


# --- MAIN HANDLER ---
async def time_machine_fetch(update, context, date_text):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("🔒 Admin only."); return
    target_text = (date_text or "").strip().lower()
    start_dt = end_dt = None
    if target_text not in ("all", "all time", "*"):
        try:
            if ".." in target_text:
                left, right = [x.strip() for x in target_text.split("..", 1)]
                start_dt = datetime.strptime(left, "%Y-%m-%d")
                end_dt = datetime.strptime(right, "%Y-%m-%d") + timedelta(days=1)
            else:
                start_dt = datetime.strptime(target_text, "%Y-%m-%d")
                end_dt = start_dt + timedelta(days=1)
            if end_dt <= start_dt: raise ValueError("end before start")
        except ValueError:
            await update.message.reply_text("Use all, YYYY-MM-DD, or YYYY-MM-DD..YYYY-MM-DD (inclusive)."); return
    group_ref = context.user_data.pop("tm_group", None)
    user_ref = context.user_data.pop("tm_user", None)
    if not group_ref or not user_ref:
        context.user_data["state"] = None
        await update.message.reply_text("Search session expired. Open Message Time Machine again."); return
    status = await update.message.reply_text("🔎 Fetching matching messages…")
    try:
        chat = await telethon_client.get_entity(group_ref)
        person = await telethon_client.get_entity(user_ref)
        matches = []
        async for item in telethon_client.iter_messages(chat, from_user=person, limit=5000):
            dt = getattr(item, "date", None)
            if dt and dt.tzinfo:
                dt = dt.replace(tzinfo=None)
            if start_dt and dt and dt < start_dt: break
            if end_dt and dt and dt >= end_dt: continue
            body = (getattr(item, "message", None) or getattr(item, "text", None) or "").strip()
            if not body:
                body = "[media/attachment]" if getattr(item, "media", None) else "[empty message]"
            stamp = dt.strftime("%Y-%m-%d %H:%M") if dt else "unknown time"
            link = ""
            uname = getattr(chat, "username", None)
            if uname: link = f" https://t.me/{uname}/{item.id}"
            matches.append(f"• {stamp} | msg {item.id}\\n{body[:1200]}{link}")
            if len(matches) >= 300: break
        context.user_data["state"] = None
        if not matches:
            await status.edit_text("No matching messages found in the accessible history."); return
        await status.edit_text(f"✅ Found {len(matches)} message(s). Sending results in batches. (Scan capped at 5,000 history items / 300 matches.)")
        for offset in range(0, len(matches), 8):
            chunk = matches[offset:offset+8]
            text = f"🕰️ Results {offset+1}–{offset+len(chunk)} of {len(matches)}\\n\\n" + "\\n\\n".join(chunk)
            await update.message.reply_text(text[:3900], disable_web_page_preview=True)
    except Exception as exc:
        context.user_data["state"] = None
        await status.edit_text("❌ Could not search this chat: " + html.escape(f"{type(exc).__name__}: {str(exc)[:500]}"))


# --- LOCAL AI IMAGE CHECKER (NO PAID API) ---
AI_IMAGE_MODEL = os.environ.get("AI_IMAGE_MODEL", "umm-maybe/AI-image-detector")
_ai_image_pipeline = None
AI_GENERATOR_TERMS = (
    "midjourney", "dall-e", "dalle", "stable diffusion", "sdxl",
    "comfyui", "automatic1111", "ai generated", "ai-generated",
    "generated by ai", "generative ai", "artificial intelligence",
)

def _metadata_text(image_path):
    """Collect readable EXIF/XMP/C2PA metadata without any network API."""
    found = []
    try:
        import exifread
        with open(image_path, "rb") as fh:
            tags = exifread.process_file(fh, details=False)
        for key, value in tags.items():
            found.append(f"{key}: {value}")
    except Exception:
        pass
    try:
        with Image.open(image_path) as img:
            for key, value in (img.info or {}).items():
                found.append(f"{key}: {value}")
            for key, value in img.getexif().items():
                found.append(f"EXIF:{key}: {value}")
    except Exception:
        pass
    try:
        with open(image_path, "rb") as fh:
            raw = fh.read(8 * 1024 * 1024)
        embedded = raw.decode("utf-8", errors="ignore").lower()
        for term in AI_GENERATOR_TERMS:
            if term in embedded:
                found.append(f"Embedded metadata: {term}")
    except Exception:
        pass
    try:
        from c2pa import Reader
        with Reader(image_path) as reader:
            manifest_json = reader.json()
        if manifest_json:
            found.append("C2PA manifest: " + manifest_json[:20000])
    except Exception:
        pass
    return "\n".join(found)

def _load_ai_image_pipeline():
    global _ai_image_pipeline
    if _ai_image_pipeline is None:
        from transformers import pipeline
        _ai_image_pipeline = pipeline(
            "image-classification",
            model=AI_IMAGE_MODEL,
            device=-1,
        )
    return _ai_image_pipeline

def _model_ai_score(image_path):
    """Return AI probability 0..1, best label, and raw model results."""
    pipe = _load_ai_image_pipeline()
    results = pipe(image_path, top_k=10)
    ai_score = None
    best_label = ""
    for item in results:
        label = str(item.get("label", "")).strip()
        score = float(item.get("score", 0.0))
        low = label.lower()
        if any(term in low for term in ("artificial", "ai", "synthetic", "generated", "fake")):
            ai_score = score if ai_score is None else max(ai_score, score)
            best_label = label
        elif any(term in low for term in ("human", "natural", "real")):
            candidate = 1.0 - score
            ai_score = candidate if ai_score is None else ai_score
            if not best_label:
                best_label = label
    if ai_score is None:
        top = results[0]
        ai_score = float(top.get("score", 0.0))
        best_label = str(top.get("label", "unknown"))
    return max(0.0, min(1.0, ai_score)), best_label, results

def check_ai_image(image_path):
    """
    Analyze one image locally and return a structured result.
    No paid API, API key, or hosted inference service is used.
    """
    if not image_path or not os.path.isfile(image_path):
        raise FileNotFoundError("Image file was not found.")
    metadata = _metadata_text(image_path)
    metadata_lower = metadata.lower()
    metadata_hits = [term for term in AI_GENERATOR_TERMS if term in metadata_lower]
    model_score, model_label, _raw = _model_ai_score(image_path)

    # Explicit generator metadata is strong provenance evidence.
    combined = (model_score * 0.35 + 0.65) if metadata_hits else model_score
    if combined >= 0.75:
        result = "Likely AI"
    elif combined <= 0.25:
        result = "Likely Real"
    else:
        result = "Uncertain"
    confidence = abs(combined - 0.5) * 200.0
    if metadata_hits:
        reason = "Generator metadata found: " + ", ".join(metadata_hits[:4])
    else:
        reason = f"Local model classified it as {model_label or 'unknown'} with {model_score * 100:.1f}% AI probability."
    return {
        "result": result,
        "confidence": round(confidence, 1),
        "reason": reason,
        "metadata_hits": metadata_hits,
        "model_score": round(model_score, 4),
        "model_label": model_label,
    }

async def handle_ai_image_check(update, context):
    """Crash-safe image provenance check using only PIL/metadata in this workflow."""
    if context.user_data.get("state") != "ai_image_check":
        return False
    msg = update.effective_message
    if not msg:
        return True

    photo = getattr(msg, "photo", None)
    document = getattr(msg, "document", None)
    mime = str(getattr(document, "mime_type", "") or "").lower()
    if not photo and not (document and mime.startswith("image/")):
        await msg.reply_text("🖼️ Please send an image.")
        return True

    status = await msg.reply_text("🔎 Checking image safely…")
    try:
        file_id = photo[-1].file_id if photo else document.file_id
        filename = "telegram_photo.jpg"
        if document and getattr(document, "file_name", None):
            filename = document.file_name

        tg_file = await context.bot.get_file(file_id)
        buf = BytesIO()
        await tg_file.download_to_memory(buf)
        data = buf.getvalue()

        if not data:
            raise ValueError("Telegram returned an empty image.")

        # Validate the image without loading PyTorch, Transformers, ONNX,
        # OpenCV, SciPy, or any other heavy detector.
        with Image.open(BytesIO(data)) as img:
            img.verify()

        lower = data[:8 * 1024 * 1024].decode("utf-8", errors="ignore").lower()
        hits = [term for term in AI_GENERATOR_TERMS if term in lower]

        try:
            with Image.open(BytesIO(data)) as img:
                for key, value in (img.info or {}).items():
                    field = f"{key}: {value}"
                    lower_field = field.lower()
                    if any(term in lower_field for term in AI_GENERATOR_TERMS):
                        hits.extend(term for term in AI_GENERATOR_TERMS if term in lower_field)
        except Exception:
            pass

        hits = list(dict.fromkeys(hits))

        if hits:
            result_text = (
                "🤖 <b>AI IMAGE CHECK</b>\n\n"
                "📌 Result: <b>Likely AI</b>\n"
                "🎯 Confidence: <b>High</b>\n"
                "🏷️ AI-generator evidence: <b>"
                + html.escape(", ".join(hits[:6]))
                + "</b>\n\n"
                "💡 A known AI-generator signature was found in the image data.\n"
                "⚠️ Metadata can be removed or changed, so this is not absolute proof."
            )
        else:
            result_text = (
                "🖼️ <b>AI IMAGE CHECK</b>\n\n"
                "📌 Result: <b>Uncertain</b>\n"
                "🎯 Confidence: <b>Low</b>\n"
                "🏷️ AI-generator metadata: <b>Not found</b>\n\n"
                "💡 No known AI-generator signature was found. "
                "This does <b>not</b> prove that the image is human-made.\n\n"
                "🛡️ Crash-safe mode is being used to keep the bot online."
            )

        await status.edit_text(
            result_text,
            parse_mode=ParseMode.HTML,
            reply_markup=tool_done_kb(),
        )
    except Exception as exc:
        await status.edit_text(
            "❌ AI image check could not process this image safely.\n\n"
            + html.escape(f"{type(exc).__name__}: {str(exc)[:700]}"),
            parse_mode=ParseMode.HTML,
        )
    finally:
        context.user_data["state"] = None

    return True

async def _run_ai_media_check(message, context, media_kind=None):
    """Download and analyze one Telegram image/audio attachment safely."""
    if not message:
        return False
    try:
        if getattr(message, "photo", None):
            file = await context.bot.get_file(message.photo[-1].file_id)
            buf = BytesIO(); await file.download_to_memory(buf)
            result = await analyze_image_bytes(buf.getvalue(), "telegram_photo.jpg")
            await message.reply_text(format_detection_result(result), parse_mode=ParseMode.HTML, reply_markup=tool_done_kb())
            return True

        if getattr(message, "voice", None):
            file = await context.bot.get_file(message.voice.file_id)
            buf = BytesIO(); await file.download_to_memory(buf)
            result = await analyze_audio_bytes(buf.getvalue(), "telegram_voice.ogg")
            await message.reply_text(format_detection_result(result), parse_mode=ParseMode.HTML, reply_markup=tool_done_kb())
            return True

        if getattr(message, "audio", None):
            file = await context.bot.get_file(message.audio.file_id)
            buf = BytesIO(); await file.download_to_memory(buf)
            result = await analyze_audio_bytes(buf.getvalue(), getattr(message.audio, "file_name", None) or "telegram_audio")
            await message.reply_text(format_detection_result(result), parse_mode=ParseMode.HTML, reply_markup=tool_done_kb())
            return True

        doc = getattr(message, "document", None)
        mime = str(getattr(doc, "mime_type", "") or "").lower()
        if doc and (mime.startswith("image/") or mime.startswith("audio/")):
            file = await context.bot.get_file(doc.file_id)
            buf = BytesIO(); await file.download_to_memory(buf)
            filename = getattr(doc, "file_name", None) or "telegram_media"
            if mime.startswith("image/"):
                result = await analyze_image_bytes(buf.getvalue(), filename)
            else:
                result = await analyze_audio_bytes(buf.getvalue(), filename)
            await message.reply_text(format_detection_result(result), parse_mode=ParseMode.HTML, reply_markup=tool_done_kb())
            return True
    except DetectorError as exc:
        await message.reply_text("❌ AI content check failed: " + html.escape(str(exc)[:700]), parse_mode=ParseMode.HTML)
        return True
    except Exception as exc:
        await message.reply_text("❌ AI content check failed safely: " + html.escape(f"{type(exc).__name__}: {str(exc)[:500]}"), parse_mode=ParseMode.HTML)
        return True
    return False

async def _auto_detect_uploaded_media(update, context):
    """Automatically screen supported media only when no tool workflow is active."""
    if context.user_data.get("state"):
        return False
    msg = update.effective_message
    if not msg:
        return False
    # Keep automatic screening lightweight and explicit: text/messages are never
    # classified as AI text, while images and audio attachments are checked.
    if getattr(msg, "photo", None) or getattr(msg, "voice", None) or getattr(msg, "audio", None):
        status = await msg.reply_text("🔎 Checking media for AI-generation signals…")
        try:
            checked = await _run_ai_media_check(msg, context)
            if checked:
                try: await status.delete()
                except Exception: pass
                return True
        except Exception:
            try: await status.delete()
            except Exception: pass
            return True
    doc = getattr(msg, "document", None)
    mime = str(getattr(doc, "mime_type", "") or "").lower()
    if doc and (mime.startswith("image/") or mime.startswith("audio/")):
        status = await msg.reply_text("🔎 Checking media for AI-generation signals…")
        try:
            checked = await _run_ai_media_check(msg, context)
            if checked:
                try: await status.delete()
                except Exception: pass
                return True
        except Exception:
            try: await status.delete()
            except Exception: pass
            return True
    return False

async def handle_ai_content_check(update, context):
    if context.user_data.get("state") != "ai_content_check":
        return False
    msg = update.effective_message
    if not msg or not (msg.photo or msg.voice or msg.audio or msg.document):
        await msg.reply_text("🤖 Please send an image, voice message, audio file, or image/audio document.")
        return True
    status = await msg.reply_text("🔎 Running multi-stage AI content analysis…")
    try:
        checked = await _run_ai_media_check(msg, context)
        if not checked:
            await status.edit_text("❌ Unsupported content. Send an image or audio file.")
        else:
            try: await status.delete()
            except Exception: pass
    finally:
        context.user_data["state"] = None
    return True

async def handle_link(update, context):
    user_id = update.effective_user.id
    text = update.message.text if update.message.text else ""
    if await _auto_detect_uploaded_media(update, context): return
    self_ping()
    state = context.user_data.get("state")
    if state in ("awaiting_crop_width", "awaiting_crop_height"):
        raw = (text or "").strip()
        if not raw.isdigit():
            await update.message.reply_text("❌ Please send a whole number of pixels.")
            return
        value = int(raw)
        if not 1 <= value <= 5000:
            await update.message.reply_text("❌ Use a dimension from 1 to 5000 pixels.")
            return
        if state == "awaiting_crop_width":
            context.user_data["crop_width"] = value
            context.user_data["state"] = "awaiting_crop_height"
            await update.message.reply_text("Now send the target height in pixels (1–5000).")
            return
        width = int(context.user_data.pop("crop_width", 0))
        height = value
        context.user_data["state"] = None
        if width * height > 20000000:
            context.user_data["state"] = "awaiting_crop_width"
            await update.message.reply_text("❌ That output is too large (maximum 20 million pixels). Send a smaller width.")
            return
        source = context.user_data.get("edit_image")
        if source is None:
            await update.message.reply_text("❌ The photo is no longer available. Upload it again.")
            return
        status = await update.message.reply_text("✂️ Cropping photo…")
        try:
            cropped = await asyncio.to_thread(lambda: ImageOps.fit(source.convert("RGB"), (width, height), method=Image.Resampling.LANCZOS, centering=(0.5, 0.5)))
            out = BytesIO()
            cropped.save(out, format="JPEG", quality=95)
            out.seek(0)
            await update.message.reply_photo(photo=out, caption=f"✅ Cropped to {width} × {height} px.", reply_markup=tool_done_kb())
            await status.edit_text("✅ Crop complete.")
        except Exception as e:
            await status.edit_text("❌ Crop failed: " + html.escape(str(e)[:1000]), parse_mode=ParseMode.HTML)
        return
    if await handle_mm_input(update, context): return
    if context.user_data.get('state') == 'qr_create': await handle_qr_create(update, context); return
    if context.user_data.get('state') == 'qr_scan' and getattr(update.message, 'document', None) and str(getattr(update.message.document, 'mime_type', '')).startswith('image/'):
        f=await context.bot.get_file(update.message.document.file_id); b=BytesIO(); await f.download_to_memory(b)
        status=await update.message.reply_text('🔍 Scanning QR code…')
        try:
            vals=await asyncio.to_thread(scan_qr_image_sync,b.getvalue())
            if vals: await status.edit_text('✅ QR code detected:\n\n'+'\n\n'.join(vals),reply_markup=tool_done_kb())
            else: await status.edit_text('❌ No readable QR code was found in this image.')
        except Exception as e: await status.edit_text('❌ QR scan failed: '+str(e)[:1000])
        context.user_data['state']=None; return
    if context.user_data.get('state') == 'awaiting_text_pdf': await handle_text_pdf_input(update, context); return
    if context.user_data.get('state') == 'awaiting_text_to_image': await handle_text_to_image(update, context); return
    if context.user_data.get('state') == 'awaiting_image_to_pdf': await handle_image_collect(update, context); return
    if context.user_data.get('state') == 'awaiting_pdf': await handle_pdf_upload(update, context); return
    if context.user_data.get('state') == 'awaiting_pdf_pages': await handle_pdf_pages(update, context); return
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
    if context.user_data.get('state') == 'admin_youtube_audio':
        await handle_admin_youtube_audio(update, context)
        return
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
    if context.user_data.get('state') == 'ar_awaiting_trigger':
        if not is_admin(user_id):
            context.user_data['state'] = None
            await update.message.reply_text("🔒 Admin only.")
            return
        await ar_handle_trigger(update, context); return
    if context.user_data.get('state') == 'ar_awaiting_response':
        if not is_admin(user_id):
            context.user_data['state'] = None
            await update.message.reply_text("🔒 Admin only.")
            return
        await ar_handle_response(update, context); return
    if context.user_data.get('reply_to'):
        target_chat = context.user_data['reply_to']
        try:
            await telethon_client.send_message(target_chat, text); context.user_data['reply_to'] = None
            await update.message.reply_text("✅ Reply sent!")
        except Exception as e:
            await update.message.reply_text(f"❌ Failed: {e}")
        return
    state = context.user_data.get("state")
    if state in ("tm_group", "tm_user", "tm_date"):
        if not is_admin(user_id):
            context.user_data["state"] = None
            await update.message.reply_text("🔒 Admin only."); return
        value = (text or "").strip()
        if state == "tm_group":
            try:
                entity = await telethon_client.get_entity(value)
                context.user_data["tm_group"] = entity
                context.user_data["state"] = "tm_user"
                await update.message.reply_text("2/3 Send the target user's @username or numeric ID.")
            except Exception as exc:
                await update.message.reply_text("Could not access that group/channel. Check the identifier and account access, then try again.")
            return
        if state == "tm_user":
            try:
                entity = await telethon_client.get_entity(value)
                context.user_data["tm_user"] = entity
                context.user_data["state"] = "tm_date"
                await update.message.reply_text("3/3 Choose time: all, one day (YYYY-MM-DD), or inclusive range (YYYY-MM-DD..YYYY-MM-DD).")
            except Exception:
                await update.message.reply_text("Could not resolve that user. Try @username or numeric ID."); return
            return
        context.user_data["state"] = None
        await time_machine_fetch(update, context, value)
        return
    state = context.user_data.get('state')
    if state == "name_history":
        context.user_data["state"] = None
        await handle_name_history(update, context, text)
        return
    if state == "word_frequency":
        context.user_data["state"] = None
        await handle_word_frequency(update, context, text)
        return
    if state == "channel_lookup":
        context.user_data["state"] = None
        await handle_channel_lookup(update, context, text)
        return
    if state in ("audio_cut_waiting_start", "audio_cut_waiting_end", "audio_cut_waiting_file"):
        await handle_audio_cut(update, context)
        return
    if state == "audio_video_to_audio":
        await handle_video_to_audio(update, context)
        return
    if state == "ai_image_check":
        await handle_ai_image_check(update, context)
        return
    if state == "ai_content_check":
        await handle_ai_content_check(update, context)
        return
    if state:
        context.user_data['state'] = None
        if state == 'profile_query': await fetch_profile(update, context, text)
        elif state == 'common_query': await handle_common_groups_query(update, context, text)
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
            if msg_id and comment_id and "-" in text:
                # Range fetch (e.g., t.me/channel/7-11)
                status_msg = await update.message.reply_text(f"⏳ Fetching {msg_id} to {comment_id}...")
                messages = await telethon_client.get_messages(entity, min_id=msg_id, max_id=comment_id + 1)
                if not messages:
                    await status_msg.edit_text("❌ No messages in that range."); return
                for idx, msg in enumerate(messages, 1):
                    if idx % 5 == 0: await status_msg.edit_text(f"Fetching {idx}/{len(messages)}...")
                    await safe_send(update.message.chat_id, context.bot, msg, from_chat_id=entity.id, message_id=msg.id)
                    await asyncio.sleep(1)
                await status_msg.edit_text(f"✅ Range complete! Fetched {len(messages)} messages."); return
            elif msg_id and comment_id:
                # Comment fetch (e.g., t.me/channel/123/456)
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
            elif msg_id:
                # Single message fetch (e.g., t.me/channel/123)
                msg = await telethon_client.get_messages(entity, ids=msg_id)
                if not msg:
                    await update.message.reply_text("❌ Message not found."); return
                await safe_send(update.message.chat_id, context.bot, msg, from_chat_id=entity.id, message_id=msg_id)
            else:
                # Batch fetch (channel link, fetch last 10 messages)
                status_msg = await update.message.reply_text("Fetching batch (max 10)...")
                messages = await telethon_client.get_messages(entity, limit=10)
                if not messages:
                    await status_msg.edit_text("No messages found."); return
                for idx, msg in enumerate(messages, 1):
                    if idx % 5 == 0: await status_msg.edit_text(f"Fetching {idx}/{len(messages)}...")
                    await safe_send(update.message.chat_id, context.bot, msg, from_chat_id=entity.id, message_id=msg.id)
                    await asyncio.sleep(1)
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
    global PTB_BOT
    PTB_BOT = bot_app.bot
    bot_app.add_handler(CommandHandler("start", start))
    for shortcut in ("lastseen", "ls", "seen", "online", "status", "last"):
        bot_app.add_handler(CommandHandler(shortcut, last_seen_shortcut))
    bot_app.add_handler(CommandHandler("safety", safety_command))
    bot_app.add_handler(CommandHandler("whisper", whisper_command))
    bot_app.add_handler(InlineQueryHandler(whisper_inline_query))
    bot_app.add_handler(CommandHandler("logout", logout))
    bot_app.add_handler(CommandHandler("broadcast", broadcast_command))
    bot_app.add_handler(CommandHandler("setname", set_bot_name))
    bot_app.add_handler(CommandHandler("setdesc", set_bot_description))
    bot_app.add_handler(CommandHandler("setphoto", set_bot_photo))
    bot_app.add_handler(CommandHandler("restart", restart_command))
    bot_app.add_handler(CallbackQueryHandler(menu_callback))
    bot_app.add_handler(MessageHandler(filters.ChatType.GROUPS & ~filters.COMMAND, safety_group_monitor), group=-1)
    async def photo_router(update, context):
        if context.user_data.get('state') == 'qr_scan':
            await handle_qr_photo(update, context); return
        if await _auto_detect_uploaded_media(update, context):
            return
        await handle_link(update, context)
    bot_app.add_handler(MessageHandler(filters.PHOTO, photo_router))
    bot_app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND & ~filters.PHOTO, handle_link))
    print("Bot running with upgraded multi-source video downloader...")
    await bot_app.initialize()
    # SamiBOT uses long polling. Remove any stale webhook before getUpdates.
    # Telegram does not allow webhook delivery and getUpdates polling at the same time.
    try:
        webhook_info = await bot_app.bot.get_webhook_info()
        if webhook_info.url:
            print(f"Removing stale Telegram webhook: {webhook_info.url}")
        await bot_app.bot.delete_webhook(drop_pending_updates=False)
    except Exception as exc:
        print(f"Webhook cleanup warning: {exc}")

    await bot_app.start()
    try:
        # Fail fast on a second polling instance instead of retrying forever.
        await bot_app.updater.start_polling(bootstrap_retries=0)
    except Conflict as exc:
        print("FATAL: Telegram rejected polling because another SamiBOT instance is already polling this bot token.")
        print("Stop the other deployment/process (Render replica, old service, local/VPS copy) and redeploy this service.")
        try:
            await bot_app.stop()
            await bot_app.shutdown()
        finally:
            raise RuntimeError("Telegram polling conflict: another bot instance is running") from exc
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=10000), daemon=True).start()
    await asyncio.Event().wait()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"Bot crashed: {e}")