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
from datetime import datetime, timedelta
from flask import Flask
from PIL import Image, ImageEnhance, ImageFilter, ImageOps, ImageChops
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler, ContextTypes
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
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute(
        """SELECT visitor_id, username, first_name, last_name, first_seen, last_seen, visit_count
           FROM profile_visits WHERE target_id = ? ORDER BY last_seen DESC""",
        (int(target_id),)
    )
    rows = c.fetchall()
    conn.close()
    return rows
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
    self_ping()
    if not is_authenticated(user_id):
        context.user_data['state'] = 'awaiting_password'
        await update.message.reply_text("🔐 TELEGRAM ASSISTANT\n\nPassword required.\nPlease enter the password to continue.")
        return
    if is_admin(user_id):
        keyboard = [
            [InlineKeyboardButton("📥 Inbox", callback_data="inbox"), InlineKeyboardButton("👤 Profile", callback_data="profile")],
            [InlineKeyboardButton("👀 Profile Visitors", callback_data="profile_visitors"), InlineKeyboardButton("📖 Story Viewers", callback_data="story_viewers")],
            [InlineKeyboardButton("🔗 Fetch Telegram", callback_data="fetch")],
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
        "Every authenticated user of this bot can add their own rules."
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

async def menu_callback(update, context):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
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
        await ar_show_menu(update, context); return
    if data == "ar_add":
        await ar_start_add(update, context); return
    if data == "ar_list":
        await ar_show_list(update, context); return
    if data == "ar_cancel":
        await ar_cancel(update, context); return
    if data.startswith("ar_match|"):
        await ar_save_rule(update, context, data.split("|", 1)[1]); return
    if data.startswith("ar_delete|"):
        try:
            rid = int(data.split("|", 1)[1])
        except ValueError:
            await query.answer("Invalid rule", show_alert=True); return
        await ar_delete(update, context, rid); return
    if data == "stats":
        if not is_admin(user_id):
            await query.answer("🔒 Admin only", show_alert=True); return
        await show_statistics(update, context); return
    if data == "profile_visitors":
        await show_profile_visitors(update, context); return
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
            keyboard = [[InlineKeyboardButton("📥 Inbox", callback_data="inbox"), InlineKeyboardButton("👤 Profile", callback_data="profile")], [InlineKeyboardButton("👀 Profile Visitors", callback_data="profile_visitors"), InlineKeyboardButton("📖 Story Viewers", callback_data="story_viewers")], [InlineKeyboardButton("🔗 Fetch Telegram", callback_data="fetch")], [InlineKeyboardButton("➕ More Commands", callback_data="more")]]
        else:
            keyboard = [[InlineKeyboardButton("👤 Profile", callback_data="profile")], [InlineKeyboardButton("🔗 Fetch Telegram", callback_data="fetch")], [InlineKeyboardButton("➕ More Commands", callback_data="more")]]
        await query.message.reply_text("🤖 TELEGRAM ASSISTANT", reply_markup=InlineKeyboardMarkup(keyboard))
    elif data == "more":
        kb = [
            [InlineKeyboardButton("🔎 Search", callback_data="search"),
             InlineKeyboardButton("🤖 Auto Responder", callback_data="auto_responder")],
            [InlineKeyboardButton("📄 PDF Fetch", callback_data="pdf_fetch")],
            [InlineKeyboardButton("🔄 Converter", callback_data="converter")],
            [InlineKeyboardButton("🎬 Video Downloader", callback_data="video_downloader")],
        ]
        if is_admin(user_id):
            admin_buttons = [
                [InlineKeyboardButton("💬 Message Manager", callback_data="message_manager")],
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
        kb = [[InlineKeyboardButton("📄 PDF to Word", callback_data="pdf_to_word"), InlineKeyboardButton("🖼️ Image to Text", callback_data="image_to_text")], [InlineKeyboardButton("📄 Text to PDF", callback_data="text_to_pdf"), InlineKeyboardButton("🎨 Text to Image", callback_data="text_to_image")], [InlineKeyboardButton("🖼️ Image to PDF", callback_data="image_to_pdf"), InlineKeyboardButton("🖼️ Edit Photo", callback_data="image_edit")], [InlineKeyboardButton("🗣️ Text to Voice (ENG)", callback_data="tts_en"), InlineKeyboardButton("🗣️ Text to Voice (AM)", callback_data="tts_am")], [InlineKeyboardButton("📷 Image Format", callback_data="img_fmt_menu"), InlineKeyboardButton("📚 Document Format", callback_data="doc_fmt_menu")], [InlineKeyboardButton("🎙️ Voice to Text (ENG)", callback_data="voice_en"), InlineKeyboardButton("🎙️ Voice to Text (AM)", callback_data="voice_am")], [InlineKeyboardButton("📱 QR Code", callback_data="qr_menu")], [InlineKeyboardButton("⬅️ Back", callback_data="more")]]
        await query.message.reply_text("🔄 MEDIA CONVERTER & AI TOOLS\n\nChoose an option:", reply_markup=InlineKeyboardMarkup(kb))
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
    elif data == "profile":
        await query.message.reply_text("👤 PROFILE\n\nEnter a Telegram username or ID to generate the Profile Card:")
        context.user_data['state'] = 'profile_query'
    elif data == "fetch":
        await query.message.reply_text("🔗 Fetch Telegram\n\nSend me a link (e.g., t.me/channel/123 or t.me/channel/123-130):")
        context.user_data['state'] = 'fetch_link'
    elif data == "search":
        await query.message.reply_text("🔎 SEARCH\n\nEnter a keyword to search Telegram globally.\n\nPublic results only — private/admin-only chats are excluded, and results are ranked by relevance.\nExample: Logic mid")
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

    if data == "edit_remove_bg":
        await query.message.reply_text("ℹ️ Background removal is disabled in this version.")
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
        # HD Pro enhancement
        src = img.convert("RGB")
        target_min = 2000
        scale = max(4.0, target_min / max(1, min(src.size)))
        scale = min(scale, 8.0)
        nw = max(2000, int(round(src.width * scale)))
        nh = max(2000, int(round(src.height * scale)))
        max_dim = 5000
        if max(nw, nh) > max_dim:
            ratio = max_dim / max(nw, nh)
            nw, nh = int(nw * ratio), int(nh * ratio)
        img = src.resize((nw, nh), Image.Resampling.LANCZOS)
        img = img.filter(ImageFilter.MedianFilter(size=3))
        try:
            arr = np.array(img)
            lab = cv2.cvtColor(arr, cv2.COLOR_RGB2LAB)
            l, a, b = cv2.split(lab)
            clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
            l = clahe.apply(l)
            lab = cv2.merge((l, a, b))
            arr = cv2.cvtColor(lab, cv2.COLOR_LAB2RGB)
            img = Image.fromarray(arr)
        except ImportError:
            img = ImageOps.autocontrast(img, cutoff=1)
        img = ImageEnhance.Contrast(img).enhance(1.15)
        img = ImageEnhance.Color(img).enhance(1.20)
        img = ImageEnhance.Brightness(img).enhance(1.03)
        img = img.filter(ImageFilter.UnsharpMask(radius=3.0, percent=120, threshold=2))
        img = img.filter(ImageFilter.UnsharpMask(radius=1.2, percent=160, threshold=2))
        img = ImageEnhance.Sharpness(img).enhance(1.3)
        filter_name = f"HD Pro • {nw}×{nh}"
        description = "Upscaled, denoised, sharpened, and colour-boosted."

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
    # Keep several independently hosted instances because public Piped
    # instances can temporarily fail or be rate-limited.
    "https://pipedapi.kavin.rocks",
    "https://pipedapi.tokhmi.xyz",
    "https://pipedapi.moomoo.me",
    "https://pipedapi.syncpundit.io",
    "https://api-piped.mha.fi",
    "https://piped-api.garudalinux.org",
    "https://pipedapi.rivo.lol",
    "https://pipedapi.leptons.xyz",
    "https://piped-api.lunar.icu",
    "https://pipedapi.colinslegacy.com",
    "https://yapi.vyper.me",
    "https://piped-api.cfe.re",
    "https://pipedapi.r4fo.com",
    "https://piped-api.privacy.com.de",
    "https://pipedapi.adminforge.de",
    "https://api.piped.yt",
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
    # PaaS images may not ship Deno, yt-dlp's default JS runtime. Explicitly
    # enable whichever supported runtime is actually installed.
    for runtime_name in ("deno", "node", "qjs", "bun"):
        runtime_path = shutil.which(runtime_name)
        if runtime_name == "node" and not runtime_path:
            runtime_path = shutil.which("nodejs")
        if runtime_path:
            options["js_runtimes"] = {runtime_name: runtime_path}
            break

    # YouTube is rotating which player clients expose downloadable formats.
    # Keep normal defaults, plus clients that can still expose formats without
    # requiring a separate PO-token provider when available.
    options["extractor_args"] = {
        "youtube": {
            "player_client": ["android_vr", "tv_simply", "default", "web_safari"]
        }
    }

    cookies = os.environ.get("YTDLP_COOKIES_FILE", "").strip()
    if cookies and os.path.isfile(cookies):
        options["cookiefile"] = cookies
    # Do NOT set `impersonate='chrome'` blindly. That was the source of the
    # user's "Impersonate target chrome is not available" error.
    return options


def extract_ytdlp_info(url: str):
    opts = ytdlp_base_options()
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)


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
    opts = ytdlp_base_options()
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
        else:
            # yt-dlp can change extension after merge/postprocessing.
            if not os.path.exists(filepath):
                stem = os.path.splitext(filepath)[0]
                for ext in (".mp4", ".mkv", ".webm", ".mov"):
                    if os.path.exists(stem + ext):
                        filepath = stem + ext
                        break
        if not os.path.isfile(filepath):
            raise FileNotFoundError(f"Downloaded file was not created: {title_hint}")
        return {"path": filepath, "title": info.get("title") or title_hint, "info": info}


def piped_get_json(url: str):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json,text/plain,*/*",
        },
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return __import__("json").load(response)


def piped_metadata(video_id: str):
    errors = []
    for api in PIPED_APIS:
        try:
            data = piped_get_json(f"{api}/streams/{urllib.parse.quote(video_id)}")
            if data.get("videoStreams") or data.get("audioStreams"):
                return data
            errors.append(f"{api}: no streams")
        except Exception as exc:
            errors.append(f"{api}: {exc}")
    raise RuntimeError("All YouTube fallback services failed: " + " | ".join(errors[:3]))


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
        headers=headers or {
            "User-Agent": "Mozilla/5.0",
            "Accept": "*/*",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response, open(path, "wb") as dst:
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
        download_url_to_file(stream["url"], raw)
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
    download_url_to_file(stream["url"], video_raw)
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
    download_url_to_file(audio_stream["url"], audio_raw)
    ffmpeg_merge(video_raw, audio_raw, final)
    os.unlink(video_raw)
    os.unlink(audio_raw)
    return {"path": final, "title": title}


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


def extract_download_options(url: str):
    platform = video_platform(url)
    normalized = normalize_public_url(url) if platform == "tiktok" else url
    # Direct yt-dlp path first.
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
            piped = piped_metadata(vid)
            heights = sorted({int(s.get("height")) for s in piped.get("videoStreams") or [] if s.get("height")}, reverse=True)
            qualities = [q for q in VIDEO_QUALITIES if any(h >= q for h in heights)]
            if not qualities and heights:
                qualities = [max(heights)]
            return {"source": "piped", "platform": platform, "url": normalized, "title": piped.get("title") or "YouTube video", "qualities": qualities or [720], "audio": bool(piped.get("audioStreams")), "piped": piped}
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
    if job["source"] == "piped":
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
async def fetch_profile(update, context, target):
    try:
        entity = await telethon_client.get_entity(target)
        context.user_data["profile_entity"] = entity
        context.user_data["post_entity"] = entity
        context.user_data["story_entity"] = entity

        # Profile visits are tracked only when the lookup target is one of the
        # configured admins. This is a bot-level visit metric, not a Telegram
        # profile-view API (Telegram does not expose arbitrary profile viewers).
        if is_admin(int(getattr(entity, "id", 0) or 0)):
            record_profile_visit(entity.id, update.effective_user)

        save_user_history(entity.id, getattr(entity, "username", None), getattr(entity, "first_name", ""), getattr(entity, "last_name", ""))

        first_name = getattr(entity, "first_name", "") or ""
        last_name = getattr(entity, "last_name", "") or ""
        display_name = f"{first_name} {last_name}".strip() or getattr(entity, "title", "Unknown")

        # ---- Fetch online status (READ DIRECTLY from entity) ----
        status_text = "⚪ Unknown status"
        try:
            status = entity.status  # No extra API call, works perfectly

            if status is not None:
                if isinstance(status, types.UserStatusOnline):
                    status_text = "🟢 Online now"
                elif isinstance(status, types.UserStatusOffline):
                    if status.was_online:
                        delta = datetime.now(status.was_online.tzinfo) - status.was_online
                        seconds = delta.total_seconds()
                        if seconds < 60:
                            status_text = "🟢 Last seen just now"
                        elif seconds < 3600:
                            minutes = int(seconds // 60)
                            status_text = f"🟢 Last seen {minutes} minute{'s' if minutes != 1 else ''} ago"
                        elif seconds < 86400:
                            hours = int(seconds // 3600)
                            status_text = f"🟢 Last seen {hours} hour{'s' if hours != 1 else ''} ago"
                        elif seconds < 604800:
                            days = int(seconds // 86400)
                            status_text = f"🟢 Last seen {days} day{'s' if days != 1 else ''} ago"
                        else:
                            weeks = int(seconds // 604800)
                            status_text = f"🟢 Last seen {weeks} week{'s' if weeks != 1 else ''} ago"
                    else:
                        status_text = "⚪ Last seen a long time ago"
                elif isinstance(status, types.UserStatusRecently):
                    status_text = "🟢 Last seen recently"
                elif isinstance(status, types.UserStatusLastWeek):
                    status_text = "🟢 Last seen within this week"
                elif isinstance(status, types.UserStatusLastMonth):
                    status_text = "🟢 Last seen within this month"
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
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await query.answer("🔒 Admin only", show_alert=True)
        return
    await query.answer("Loading profile visitors…")

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
                    media_path = tempfile.NamedTemporaryFile(delete=False, suffix=".bin").name
                    await telethon_client.download_media(media, file=media_path)
                    if os.path.exists(media_path) and os.path.getsize(media_path) > 0:
                        media_doc = getattr(media, "document", None)
                        mime = str(getattr(media_doc, "mime_type", "") or "").lower()
                        caption = f"📖 Story {index + 1}/{len(stories)} • {getattr(story, 'id', '?')}"
                        with open(media_path, "rb") as media_file:
                            if getattr(media, "photo", None) is not None:
                                sent = await context.bot.send_photo(query.message.chat_id, photo=media_file, caption=caption)
                            elif "video" in mime or getattr(media, "video", None) is not None:
                                sent = await context.bot.send_video(query.message.chat_id, video=media_file, caption=caption, supports_streaming=True)
                            elif "gif" in mime or getattr(media, "gif", None) is not None:
                                sent = await context.bot.send_animation(query.message.chat_id, animation=media_file, caption=caption)
                            else:
                                sent = await context.bot.send_document(query.message.chat_id, document=media_file, caption=caption)
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

# --- GLOBAL TELEGRAM SEARCH ---
#
# Search is deliberately public-only.  The bot runs one Telethon user session,
# so using that session's dialogs/private history would make search depend on
# the admin account.  We only keep peers with a public username and rank the
# returned public messages locally for relevance.

SEARCH_PAGE_SIZE = 8
SEARCH_GLOBAL_LIMIT = 100
SEARCH_MAX_FETCH = 500


def _search_peer_marked_id(entity):
    """Return Telegram's marked peer ID safely."""
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
    if hasattr(entity, "broadcast"):
        return "channel" if getattr(entity, "broadcast", False) else "group"
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
    return f"https://t.me/{username}" if username else None


def _search_message_link(entity, message_id):
    username = getattr(entity, "username", None)
    if username:
        return f"https://t.me/{username}/{message_id}"
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


def _search_public_entity(entity):
    """Only allow peers that are publicly addressable by username.

    This is the important isolation rule: private dialogs, private groups,
    and admin-only peers are never exposed by the bot's global search.
    """
    username = (getattr(entity, "username", None) or "").strip()
    if not username:
        return False
    kind = _search_peer_kind(entity)
    return kind in {"channel", "group", "bot", "user"}


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
    """Score a Telegram result using exact phrase/token/title matches.

    Telegram performs the server-side fuzzy search. This second pass makes the
    display order much more useful for multi-word queries instead of simply
    showing whatever happened to arrive first from the server.
    """
    normalized, tokens = _search_terms(query)
    content = re.sub(r"\s+", " ", (getattr(message, "message", None) or "").strip().lower())
    title = _search_peer_name(entity).lower()
    username = (getattr(entity, "username", None) or "").lower().lstrip("@")

    score = 0.0
    if normalized and normalized in content:
        score += 1000
    if normalized and normalized in title:
        score += 850
    if normalized and normalized in username:
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

    # A small freshness tie-breaker; relevance remains dominant.
    message_date = getattr(message, "date", None)
    if message_date:
        try:
            age_days = max(0.0, (datetime.now(message_date.tzinfo) - message_date).total_seconds() / 86400) if getattr(message_date, "tzinfo", None) else max(0.0, (datetime.now() - message_date).total_seconds() / 86400)
            score += max(0.0, 25.0 - min(age_days, 25.0))
        except Exception:
            pass
    return score


async def _search_global_batch(query, offset_rate, offset_peer, offset_id):
    """Fetch one server-ranked global-search page."""
    return await telethon_client(
        functions.messages.SearchGlobalRequest(
            q=query,
            filter=types.InputMessagesFilterEmpty(),
            min_date=0,
            max_date=0,
            offset_rate=offset_rate,
            offset_peer=offset_peer,
            offset_id=offset_id,
            limit=SEARCH_GLOBAL_LIMIT,
        )
    )


async def _search_public_posts(query):
    """Search public channel posts globally when Telegram permits the query."""
    request_cls = getattr(functions.channels, "SearchPostsRequest", None)
    if request_cls is None:
        return [], {}, "unsupported"
    normalized = (query or "").strip().lower()
    is_hashtag = normalized.startswith("#") and " " not in normalized
    posts = []
    peer_map = {}
    offset_rate = 0
    offset_peer = types.InputPeerEmpty()
    offset_id = 0
    last_state = None
    for _ in range(3):
        kwargs = {"offset_rate": int(offset_rate or 0), "offset_peer": offset_peer, "offset_id": int(offset_id or 0), "limit": 100}
        kwargs["hashtag" if is_hashtag else "query"] = normalized[1:] if is_hashtag else normalized
        try:
            result = await telethon_client(request_cls(**kwargs))
        except Exception as e:
            print(f"[Search] Public-post search skipped: {type(e).__name__}: {e}")
            return posts, peer_map, type(e).__name__
        for entity in list(getattr(result, "chats", []) or []) + list(getattr(result, "users", []) or []):
            marked = _search_peer_marked_id(entity)
            if marked is not None:
                peer_map[marked] = entity
        batch = list(getattr(result, "messages", []) or [])
        posts.extend(batch)
        if not batch:
            break
        next_rate = getattr(result, "next_rate", None)
        last = batch[-1]
        last_id = int(getattr(last, "id", 0) or 0)
        last_entity = peer_map.get(_search_peer_marked_id(getattr(last, "peer_id", None)))
        if not last_id or last_entity is None:
            break
        if next_rate is None:
            last_date = getattr(last, "date", None)
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
        state = (int(next_rate), last_id, repr(next_peer))
        if state == last_state:
            break
        last_state = state
        offset_rate = int(next_rate)
        offset_id = last_id
        offset_peer = next_peer
    return posts, peer_map, None


async def fetch_search(update, context, query):
    query = re.sub(r"\s+", " ", (query or "").strip())
    if not query:
        await update.message.reply_text("🔎 Please enter a keyword, for example: Logic mid")
        return

    search_id = uuid.uuid4().hex[:8]
    for key in ("search_results", "search_query", "search_filter", "search_page"):
        context.user_data.pop(key, None)
    context.user_data["search_id"] = search_id

    status_msg = await update.message.reply_text(
        f"🔎 Searching Telegram globally for: <b>{html.escape(query)}</b>\n"
        "🌐 Public results only • private/admin-only chats are excluded\n"
        "⚡ Ranking results by query relevance",
        parse_mode=ParseMode.HTML,
    )

    started = time.monotonic()
    try:
        results = []
        peer_map = {}
        seen_messages = set()
        seen_peers = set()
        fetched = 0
        offset_rate = 0
        offset_peer = types.InputPeerEmpty()
        offset_id = 0
        page_count = 0
        last_state = None

        while fetched < SEARCH_MAX_FETCH:
            global_result = await _search_global_batch(
                query, offset_rate, offset_peer, offset_id
            )
            page_count += 1

            entities = list(getattr(global_result, "chats", []) or []) + list(getattr(global_result, "users", []) or [])
            for entity in entities:
                marked = _search_peer_marked_id(entity)
                if marked is not None:
                    peer_map[marked] = entity

            batch_messages = list(getattr(global_result, "messages", []) or [])
            if not batch_messages:
                break

            for message in batch_messages:
                marked_peer = _search_peer_marked_id(getattr(message, "peer_id", None))
                entity = peer_map.get(marked_peer)
                if entity is None or not _search_public_entity(entity):
                    continue

                # Message results are restricted to public channels/groups.
                # A public username on a user/bot does NOT make private DMs
                # searchable; those must never leak from the admin session.
                peer_kind = _search_peer_kind(entity)
                if peer_kind not in {"channel", "group"}:
                    continue

                message_id = getattr(message, "id", None)
                if not message_id:
                    continue
                key = (marked_peer, int(message_id))
                if key in seen_messages:
                    continue
                seen_messages.add(key)

                media_type = _search_message_type(message)
                results.append({
                    "kind": "message",
                    "type": media_type,
                    "peer_type": _search_peer_kind(entity),
                    "peer_id": marked_peer,
                    "entity": entity,
                    "message": message,
                    "link": _search_message_link(entity, message_id),
                    "content": _search_content(message),
                    "title": _search_peer_name(entity),
                    "username": getattr(entity, "username", None),
                    "date": getattr(message, "date", None),
                    "score": _search_relevance_score(query, entity, message),
                    "server_rank": len(results),
                })

            fetched += len(batch_messages)
            if fetched >= SEARCH_MAX_FETCH:
                break

            next_rate = getattr(global_result, "next_rate", None)
            last_message = batch_messages[-1]
            last_message_id = int(getattr(last_message, "id", 0) or 0)
            last_entity = peer_map.get(_search_peer_marked_id(getattr(last_message, "peer_id", None)))
            if not last_message_id or last_entity is None:
                break

            # Telegram documents that offset_rate should use next_rate when
            # present, otherwise the date of the last returned message.
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

            new_state = (int(next_rate), last_message_id, repr(next_peer))
            if new_state == last_state:
                break
            last_state = new_state
            offset_rate = int(next_rate)
            offset_id = last_message_id
            offset_peer = next_peer

            if page_count >= 10:
                break

        # Also search Telegram's dedicated global public-channel post index.
        # This is separate from the connected account's joined chats.
        post_messages, post_peers, post_error = await _search_public_posts(query)
        peer_map.update(post_peers)
        for message in post_messages:
            marked_peer = _search_peer_marked_id(getattr(message, "peer_id", None))
            entity = peer_map.get(marked_peer)
            if entity is None or not _search_public_entity(entity):
                continue
            if _search_peer_kind(entity) != "channel":
                continue
            message_id = getattr(message, "id", None)
            if not message_id:
                continue
            key = (marked_peer, int(message_id))
            if key in seen_messages:
                continue
            seen_messages.add(key)
            results.append({
                "kind": "message", "type": _search_message_type(message),
                "peer_type": "channel", "peer_id": marked_peer,
                "entity": entity, "message": message,
                "link": _search_message_link(entity, message_id),
                "content": _search_content(message),
                "title": _search_peer_name(entity),
                "username": getattr(entity, "username", None),
                "date": getattr(message, "date", None),
                "score": _search_relevance_score(query, entity, message) + 250,
                "server_rank": len(results),
            })

        # Add public peers returned by Telegram as secondary results, but only
        # when the peer itself matches the query and has not produced a message.
        message_peer_ids = {item["peer_id"] for item in results}
        for entity in peer_map.values():
            if not _search_public_entity(entity):
                continue
            marked = _search_peer_marked_id(entity)
            if marked is None or marked in message_peer_ids or marked in seen_peers:
                continue
            name = _search_peer_name(entity)
            username = (getattr(entity, "username", None) or "").lower()
            normalized, tokens = _search_terms(query)
            haystack = f"{name.lower()} {username}"
            if normalized not in haystack and not any(token in haystack for token in tokens):
                continue
            seen_peers.add(marked)
            score = _search_relevance_score(query, entity, None)
            results.append({
                "kind": "peer",
                "type": _search_peer_kind(entity),
                "peer_type": _search_peer_kind(entity),
                "peer_id": marked,
                "entity": entity,
                "message": None,
                "link": _search_peer_link(entity),
                "content": f"{('🤖' if _search_peer_kind(entity) == 'bot' else '📢' if _search_peer_kind(entity) == 'channel' else '👥' if _search_peer_kind(entity) == 'group' else '👤')} {name}"
                           + (f"  @{getattr(entity, 'username', None)}" if getattr(entity, 'username', None) else ""),
                "title": name,
                "username": getattr(entity, "username", None),
                "date": None,
                "score": score,
                "server_rank": len(results),
            })

        if not results:
            await status_msg.edit_text(
                f"❌ No public Telegram results found for <b>{html.escape(query)}</b>.\n\n"
                "Private/admin-only chats are intentionally excluded from global search.",
                parse_mode=ParseMode.HTML,
            )
            return

        # Relevance first, server order/date only break ties.
        def _search_sort_key(item):
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

        results.sort(key=_search_sort_key, reverse=True)

        context.user_data["search_results"] = results[:500]
        context.user_data["search_query"] = query
        context.user_data["search_filter"] = "all"
        context.user_data["search_page"] = 1
        context.user_data["search_joined_hidden"] = 0
        context.user_data["search_elapsed"] = round(time.monotonic() - started, 2)

        await status_msg.delete()
        await display_search_page(update, context, 1, "all")

    except FloodWaitError as e:
        await status_msg.edit_text(
            f"⏳ Telegram rate limit. Please wait {e.seconds} seconds and try again."
        )
    except Exception as e:
        print(f"[Search] Global public search failed: {type(e).__name__}: {e}")
        await status_msg.edit_text(
            f"❌ Search failed: <code>{html.escape(type(e).__name__)}</code>\n\n"
            f"{html.escape(str(e)[:700])}",
            parse_mode=ParseMode.HTML,
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

    # Guard against race conditions from other searches
    if not context.user_data.get("search_results"):
        await update.message.reply_text("❌ Search session expired. Please search again.")
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
    joined_hidden = context.user_data.get("search_joined_hidden", 0)

    lines = [
        "<b>🌍 TELEGRAM GLOBAL SEARCH</b>",
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

    lines.append(f"Page {page}/{total_pages} • {total_results} results")
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

# --- MAIN HANDLER ---
async def handle_link(update, context):
    user_id = update.effective_user.id
    text = update.message.text if update.message.text else ""
    self_ping()
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
        if not is_authenticated(user_id):
            await update.message.reply_text("🔐 Password required.")
            return
        await ar_handle_trigger(update, context); return
    if context.user_data.get('state') == 'ar_awaiting_response':
        if not is_authenticated(user_id):
            await update.message.reply_text("🔐 Password required.")
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
    bot_app.add_handler(CommandHandler("logout", logout))
    bot_app.add_handler(CommandHandler("broadcast", broadcast_command))
    bot_app.add_handler(CommandHandler("setname", set_bot_name))
    bot_app.add_handler(CommandHandler("setdesc", set_bot_description))
    bot_app.add_handler(CommandHandler("setphoto", set_bot_photo))
    bot_app.add_handler(CommandHandler("restart", restart_command))
    bot_app.add_handler(CallbackQueryHandler(menu_callback))
    async def photo_router(update, context):
        if context.user_data.get('state') == 'qr_scan':
            await handle_qr_photo(update, context); return
        await handle_link(update, context)
    bot_app.add_handler(MessageHandler(filters.PHOTO, photo_router))
    bot_app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND & ~filters.PHOTO, handle_link))
    print("Bot running with upgraded multi-source video downloader...")
    await bot_app.initialize(); await bot_app.start(); await bot_app.updater.start_polling()
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=10000), daemon=True).start()
    await asyncio.Event().wait()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"Bot crashed: {e}")