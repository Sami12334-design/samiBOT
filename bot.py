#!/usr/bin/env python3
"""
telegram_assistant_resilient_search.py

Features:
- Menu UI, inbox, fetch-by-link, profile card, view posts/stories (preserved)
- Robust global search that returns clickable links for public channels/groups (even if not joined)
  using multiple fallbacks and careful chat-id normalization.
- Profile resolution and "View Posts" resilient: tries get_entity, then uses cached search chat objects
  (with access_hash) to read messages from public channels without joining.

Requirements:
  pip install telethon python-telegram-bot Flask

Environment variables:
  BOT_TOKEN, API_ID, API_HASH, STRING_SESSION
  BOT_PASSWORD (optional, default "ptss25")
"""

import re
import asyncio
import os
import threading
import sqlite3
from io import BytesIO
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler, ContextTypes
from telethon import TelegramClient, events, functions, types
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError, UsernameNotOccupiedError
from telethon.tl.types import PeerChannel, PeerUser, PeerChat, InputPeerChannel

# Flask health endpoint
app = Flask(__name__)
@app.route('/')
def home():
    return "Bot is alive!"

# --- Config ---
BOT_TOKEN = os.environ.get('BOT_TOKEN', '')
API_ID = int(os.environ.get('API_ID', 0))
API_HASH = os.environ.get('API_HASH', '')
STRING_SESSION = os.environ.get('STRING_SESSION', '')
BOT_PASSWORD = os.environ.get("BOT_PASSWORD", "ptss25")

telethon_client = TelegramClient(StringSession(STRING_SESSION), API_ID, API_HASH)
DB_PATH = 'bot_data.db'

# --- SQLite DB helpers (unchanged) ---
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY)''')
    c.execute('''CREATE TABLE IF NOT EXISTS inbox (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, sender_id INTEGER, name TEXT, username TEXT, text TEXT, media_type TEXT, date TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS tracking (target_id INTEGER PRIMARY KEY, username TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS user_history (user_id INTEGER, username TEXT, first_name TEXT, last_name TEXT, date TEXT)''')
    conn.commit()
    conn.close()

def is_authenticated(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    data = c.fetchone()
    conn.close()
    return data is not None

def add_authenticated_user(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
    conn.commit()
    conn.close()

def remove_authenticated_user(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def add_inbox_message(chat_id, sender_id, name, username, text, media_type, date):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO inbox (chat_id, sender_id, name, username, text, media_type, date) VALUES (?, ?, ?, ?, ?, ?, ?)",
              (chat_id, sender_id, name, username, text, media_type, date))
    conn.commit()
    conn.close()

def get_inbox_conversations():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT chat_id, sender_id, name, username, text, media_type, date FROM inbox ORDER BY id DESC")
    rows = c.fetchall()
    conn.close()
    return rows

def save_user_history(user_id, username, first_name, last_name):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO user_history (user_id, username, first_name, last_name, date) VALUES (?, ?, ?, ?, ?)",
              (user_id, username, first_name, last_name, str(__import__('datetime').datetime.now())))
    conn.commit()
    conn.close()

def get_user_history(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT username, first_name, last_name, date FROM user_history WHERE user_id = ? ORDER BY date DESC", (user_id,))
    rows = c.fetchall()
    conn.close()
    return rows

# --- Utilities / normalization / link building ---

def parse_tg_link(text):
    pattern = r'https?://t\.me/([a-zA-Z0-9_]+)/(\d+)'
    match = re.search(pattern, text)
    if match: return match.group(1), int(match.group(2))
    channel_pattern = r'https?://t\.me/([a-zA-Z0-9_]+)'
    match = re.search(channel_pattern, text)
    if match: return match.group(1), None
    return None, None

def get_media_type(event):
    if getattr(event, 'photo', None): return "📷"
    if getattr(event, 'video', None): return "🎬"
    if getattr(event, 'document', None): return "📄"
    if getattr(event, 'audio', None): return "🎵"
    if getattr(event, 'voice', None): return "🎤"
    if getattr(event, 'sticker', None): return "🧩"
    return "💬"

def safe_numeric_for_c(cid):
    """
    Convert various id forms into the numeric segment used by t.me/c/{numeric}/{msg}.
    Telethon channel ids often appear as negative ints like -1001234567890.
    We return the numeric string without -100 or leading '-'.
    """
    if cid is None:
        return None
    try:
        # If it's an object (TL type) with id attribute:
        if hasattr(cid, 'id'):
            cid = getattr(cid, 'id')
        cid_int = int(cid)
        s = str(cid_int)
    except Exception:
        s = str(cid)
    if s.startswith("-100"):
        return s[4:]
    if s.startswith("-"):
        return s[1:]
    return s

def tg_link_from_chat_obj(chat_obj, msg_id):
    """
    Prefer username link when available, else /c/{numeric}/{msg_id}
    We return empty string if we cannot form any link.
    """
    if chat_obj is None:
        return ""
    username = getattr(chat_obj, 'username', None)
    if username:
        return f"https://t.me/{username}/{msg_id}"
    # try id-like attributes:
    cid = getattr(chat_obj, 'id', None) or getattr(chat_obj, 'channel_id', None) or getattr(chat_obj, 'chat_id', None)
    numeric = safe_numeric_for_c(cid)
    if numeric:
        return f"https://t.me/c/{numeric}/{msg_id}"
    return ""

def tg_link_from_chat_id(chat_id, msg_id):
    numeric = safe_numeric_for_c(chat_id)
    if numeric:
        return f"https://t.me/c/{numeric}/{msg_id}"
    return ""

def extract_chat_id_from_msg(msg):
    """
    Robustly extract chat id (int) from a Telethon message object:
    - peer_id.channel_id / peer_id.chat_id / to_id.channel_id / from_id.user_id
    - fallback to msg.chat_id
    Returns int or None.
    """
    # direct attributes
    if hasattr(msg, 'chat_id') and getattr(msg, 'chat_id') is not None:
        try:
            return int(msg.chat_id)
        except Exception:
            pass
    # peer_id / to_id / from_id
    pid = getattr(msg, 'peer_id', None) or getattr(msg, 'to_id', None) or getattr(msg, 'from_id', None)
    if pid is None:
        return None
    try:
        if isinstance(pid, PeerChannel):
            return int(pid.channel_id)
        if isinstance(pid, PeerUser):
            return int(pid.user_id)
        if isinstance(pid, PeerChat):
            return int(pid.chat_id)
    except Exception:
        pass
    # some TL objects might have .channel_id/.chat_id
    cid = getattr(pid, 'channel_id', None) or getattr(pid, 'chat_id', None) or getattr(pid, 'user_id', None)
    if cid is not None:
        try:
            return int(cid)
        except Exception:
            return None
    return None

# --- Safe sending of media/messages (unchanged) ---
async def safe_send(chat_id, bot, msg, from_chat_id, message_id):
    text = getattr(msg, 'message', None)
    try:
        if getattr(msg, 'photo', None) or getattr(msg, 'video', None) or getattr(msg, 'document', None) or getattr(msg, 'voice', None) or getattr(msg, 'audio', None) or getattr(msg, 'gif', None):
            media_bytes = BytesIO()
            await telethon_client.download_media(msg, file=media_bytes)
            media_bytes.seek(0)
            if getattr(msg, 'photo', None): await bot.send_photo(chat_id, photo=media_bytes, caption=text)
            elif getattr(msg, 'video', None): await bot.send_video(chat_id, video=media_bytes, caption=text)
            elif getattr(msg, 'document', None): await bot.send_document(chat_id, document=media_bytes, caption=text)
            elif getattr(msg, 'voice', None): await bot.send_voice(chat_id, voice=media_bytes, caption=text)
            elif getattr(msg, 'audio', None): await bot.send_audio(chat_id, audio=media_bytes, caption=text)
            elif getattr(msg, 'gif', None): await bot.send_animation(chat_id, animation=media_bytes, caption=text)
        elif getattr(msg, 'sticker', None):
            sticker_bytes = BytesIO()
            await telethon_client.download_media(msg, file=sticker_bytes)
            sticker_bytes.seek(0)
            await bot.send_sticker(chat_id, sticker=sticker_bytes)
        elif text:
            await bot.send_message(chat_id, text=text)
        else:
            await bot.send_message(chat_id, "Unsupported message type.")
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

# --- Commands & menu (kept, with updated search and profile resolution) ---
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

# --- Menu callback (mostly same) ---
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
                    uname = username or "N/A"
                    text += f"\n👤 {name} (@{uname})\n\"{content}\"\n"
                    kb.append([InlineKeyboardButton(f"💬 {name}", callback_data=f"conv_{chat_id}")])
            kb.append([InlineKeyboardButton("⬅️ Back", callback_data="main_menu")])
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))

    elif data.startswith("conv_"):
        chat_id = int(data.split("_", 1)[1])
        rows = get_inbox_conversations()
        text = f"💬 CHAT\n\n"
        for row in reversed(rows[-50:]):
            if row[0] == chat_id:
                text += f"{row[3]}:\n{row[4] if row[4] else f'[{row[5]}]'}\n\n"
        kb = [[InlineKeyboardButton("↩️ Reply", callback_data=f"reply_{chat_id}"),
               InlineKeyboardButton("👤 Profile", callback_data="profile")],
              [InlineKeyboardButton("🔄 Refresh", callback_data=f"refresh_{chat_id}"),
               InlineKeyboardButton("⬅️ Back", callback_data="inbox")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))

    elif data.startswith("reply_"):
        chat_id = int(data.split("_", 1)[1])
        context.user_data['reply_to'] = chat_id
        await query.edit_message_text(f"💬 Type your reply to {chat_id}:")

    elif data.startswith("refresh_"):
        chat_id = int(data.split("_", 1)[1])
        rows = get_inbox_conversations()
        text = f"💬 CHAT\n\n"
        for row in reversed(rows[-50:]):
            if row[0] == chat_id:
                text += f"{row[3]}:\n{row[4] if row[4] else f'[{row[5]}]'}\n\n"
        kb = [[InlineKeyboardButton("↩️ Reply", callback_data=f"reply_{chat_id}"),
               InlineKeyboardButton("👤 Profile", callback_data="profile")],
              [InlineKeyboardButton("🔄 Refresh", callback_data=f"refresh_{chat_id}"),
               InlineKeyboardButton("⬅️ Back", callback_data="inbox")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))

    elif data == "profile":
        entity = context.user_data.get('profile_entity')
        if entity:
            text = (f"<blockquote><b>{getattr(entity, 'first_name', '')} {getattr(entity, 'last_name', '')}</b>\n"
                    f"@{getattr(entity, 'username', 'N/A')}\n\n"
                    f"{getattr(entity, 'about', 'No bio')}\n\n"
                    f"ID: {getattr(entity, 'id', 'N/A')}\nVerified: {getattr(entity, 'verified', False)}\n"
                    f"Premium: {getattr(entity, 'premium', False)}\nBot: {getattr(entity, 'bot', False)}</blockquote>")
            kb = [
                [InlineKeyboardButton("📰 View Posts", callback_data="posts_1"),
                 InlineKeyboardButton("📸 View Stories", callback_data="story_start")],
                [InlineKeyboardButton("⬅️ Back", callback_data="more")]
            ]
            await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))
        else:
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
            [InlineKeyboardButton("⬅️ Back", callback_data="main_menu")]
        ]
        await query.edit_message_text("➕ MORE COMMANDS", reply_markup=InlineKeyboardMarkup(kb))

    elif data in ["search", "stats", "track", "names", "groups", "messages", "analysis", "channels", "rep", "friends", "reactions", "gifts", "share", "words", "common"]:
        prompts = {
            "search": ("🔎 SEARCH", "Enter any keyword to search globally across channels/groups:\nExample: python or technology"),
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

    elif data.startswith("search_"):
        page = int(data.split("_", 1)[1])
        await handle_search_pagination(update, context, page)

    elif data.startswith("posts_"):
        page = int(data.split("_", 1)[1])
        await handle_posts_pagination(update, context, page)

    elif data == "story_start":
        entity = context.user_data.get('profile_entity')
        if not entity:
            await query.edit_message_text("No profile loaded. Please view a profile first.")
            return
        await fetch_stories(update, context, entity)

    elif data == "story_prev":
        context.user_data['story_index'] = context.user_data.get('story_index', 0) - 1
        await display_story(update, context)

    elif data == "story_next":
        context.user_data['story_index'] = context.user_data.get('story_index', 0) + 1
        await display_story(update, context)

    elif data.startswith("filter_"):
        def get_filter(name):
            return getattr(types, name, types.InputMessagesFilterEmpty)
        filter_map = {
            "all": types.InputMessagesFilterEmpty,
            "posts": types.InputMessagesFilterEmpty,
            "media": types.InputMessagesFilterEmpty,
            "photos": get_filter('InputMessagesFilterPhotos'),
            "videos": get_filter('InputMessagesFilterVideo'),
            "docs": get_filter('InputMessagesFilterDocument'),
            "audio": get_filter('InputMessagesFilterAudio'),
            "voice": get_filter('InputMessagesFilterVoice'),
            "links": get_filter('InputMessagesFilterUrl'),
        }
        filter_key = data.split("_", 1)[1]
        filter_class = filter_map.get(filter_key, types.InputMessagesFilterEmpty)
        filter_type = filter_class()
        query_text = context.user_data.get('search_query', '')
        if query_text:
            await fetch_search(update, context, query_text, filter_type, is_callback=True)
        else:
            await query.edit_message_text("No search query found. Please search again.")

# --- Profile fetch (resilient) ---
async def resolve_entity_from_target(context, target):
    """
    Try multiple ways to resolve a telethon entity from target string or id:
    1) telethon_client.get_entity(target)
    2) If target is numeric id, try to find in last_search_chats and create InputPeerChannel using access_hash
    3) If last_search_chats contains an object with same username or id use that
    Returns (entity, note) where entity may be a Telethon entity or None and note is a diagnostic.
    """
    # First direct get_entity (works for usernames, phone numbers, or ids we have access to)
    try:
        ent = await telethon_client.get_entity(target)
        return ent, "resolved via get_entity"
    except Exception:
        pass

    # If we have last search chat_map, try to find matching chat
    chat_map = context.user_data.get('last_search_chats') or {}
    # If target looks like '@username' or username
    t = str(target).lstrip('@')
    for k, ch in chat_map.items():
        try:
            if getattr(ch, 'username', None) and getattr(ch, 'username').lower() == t.lower():
                return ch, "from last_search_chats by username"
        except Exception:
            pass

    # If target numeric, try to find chat with matching id / channel_id / chat_id
    try:
        tid = int(target)
    except Exception:
        tid = None
    if tid is not None:
        for k, ch in chat_map.items():
            cid = getattr(ch, 'id', None) or getattr(ch, 'channel_id', None) or getattr(ch, 'chat_id', None)
            try:
                if cid is not None and int(cid) == tid:
                    # If this chat_obj contains access_hash we can build InputPeerChannel
                    access_hash = getattr(ch, 'access_hash', None)
                    if access_hash:
                        try:
                            peer = InputPeerChannel(channel_id=tid, access_hash=int(access_hash))
                            # get_entity may accept InputPeerChannel as well
                            ent = await telethon_client.get_entity(peer)
                            return ent, "resolved via InputPeerChannel using access_hash"
                        except Exception:
                            return ch, "using chat object (no get_entity)"
                    return ch, "from last_search_chats by id"
            except Exception:
                pass

    return None, "not resolved"

async def fetch_profile(update, context, target):
    """
    Fetch a profile entity and store it in context.user_data['profile_entity'].
    This function uses resolve_entity_from_target to attempt multiple fallbacks.
    """
    try:
        ent, note = await resolve_entity_from_target(context, target)
        if ent is None:
            await update.message.reply_text(f"❌ Could not resolve profile for '{target}'. {note}. Try a username like @channel or a numeric id.")
            return
        # Save the entity and fetch recent messages for posts
        context.user_data['profile_entity'] = ent
        # Attempt to fetch messages (telethon get_messages accepts entities or InputPeer)
        try:
            msgs = await telethon_client.get_messages(ent, limit=50)
            context.user_data['post_messages'] = list(msgs)
        except Exception:
            # If get_messages fails for direct entity, but we have chat object in last_search_chats with access_hash,
            # try building InputPeerChannel
            last_map = context.user_data.get('last_search_chats') or {}
            # try to find matching chat_obj
            found = None
            ent_id = getattr(ent, 'id', None)
            for ch in last_map.values():
                cid = getattr(ch, 'id', None) or getattr(ch, 'channel_id', None) or getattr(ch, 'chat_id', None)
                if cid is not None and ent_id is not None and int(cid) == int(ent_id):
                    found = ch
                    break
            if found and getattr(found, 'access_hash', None):
                try:
                    peer = InputPeerChannel(channel_id=int(getattr(found, 'id', found.channel_id)), access_hash=int(found.access_hash))
                    msgs = await telethon_client.get_messages(peer, limit=50)
                    context.user_data['post_messages'] = list(msgs)
                except Exception as e:
                    context.user_data['post_messages'] = []
            else:
                context.user_data['post_messages'] = []

        save_user_history(getattr(ent, 'id', 0), getattr(ent, 'username', ''), getattr(ent, 'first_name', ''), getattr(ent, 'last_name', ''))

        # Build profile text
        text = (f"<blockquote><b>{getattr(ent, 'first_name', '')} {getattr(ent, 'last_name', '')}</b>\n"
                f"@{getattr(ent, 'username', 'N/A')}\n\n"
                f"{getattr(ent, 'about', 'No bio')}\n\n"
                f"ID: {getattr(ent, 'id', 'N/A')}\nVerified: {getattr(ent, 'verified', False)}\n"
                f"Premium: {getattr(ent, 'premium', False)}\nBot: {getattr(ent, 'bot', False)}</blockquote>")
        kb = [
            [InlineKeyboardButton("📰 View Posts", callback_data="posts_1"),
             InlineKeyboardButton("📸 View Stories", callback_data="story_start")],
            [InlineKeyboardButton("⬅️ Back", callback_data="more")]
        ]
        # Try to show profile photo if possible
        try:
            photo = await telethon_client.download_profile_photo(ent, file=BytesIO())
            if photo:
                photo.seek(0)
                await update.message.reply_photo(photo=photo, caption=text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))
                return
        except Exception:
            pass
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))
    except Exception as e:
        await update.message.reply_text(f"❌ Error fetching profile: {e}")

# --- Posts pagination (unchanged but resilient) ---
async def handle_posts_pagination(update, context, page):
    query = update.callback_query
    await query.answer()
    messages = context.user_data.get('post_messages')
    if not messages:
        await query.edit_message_text("No posts found. Please load profile first.")
        return
    per_page = 5
    total = len(messages)
    start = (page - 1) * per_page
    end = min(start + per_page, total)
    page_items = messages[start:end]
    if not page_items:
        await query.edit_message_text("No more posts.")
        return
    text = f"📰 <b>POSTS (Recent → Old)</b>\nPage {page}/{max(1, (total + per_page - 1)//per_page)}\n\n"
    entity = context.user_data.get('profile_entity')
    for msg in page_items:
        media_emoji = get_media_type(msg)
        date_str = msg.date.strftime("%Y-%m-%d %H:%M") if getattr(msg, 'date', None) else ""
        content = (msg.message[:120] + '...') if (getattr(msg, 'message', None) and len(msg.message) > 120) else (msg.message if getattr(msg, 'message', None) else "[Media]")
        # Try to build link via several ways:
        link = ""
        # 1) If entity has username:
        if entity and getattr(entity, 'username', None):
            link = f"https://t.me/{entity.username}/{msg.id}"
        else:
            # 2) If message contains peer info (extract chat id) and we have last_search_chats
            chat_id = extract_chat_id_from_msg(msg)
            last_map = context.user_data.get('last_search_chats') or {}
            chat_obj = None
            if chat_id is not None:
                chat_obj = last_map.get(int(chat_id)) or last_map.get(str(chat_id))
            if chat_obj:
                link = tg_link_from_chat_obj(chat_obj, msg.id)
            elif chat_id is not None:
                link = tg_link_from_chat_id(chat_id, msg.id)
        if link:
            text += f"{media_emoji} <a href='{link}'>{content}</a> - {date_str}\n"
        else:
            text += f"{media_emoji} {content} - {date_str}\n"
    kb = []
    nav_row = []
    if page > 1:
        nav_row.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"posts_{page-1}"))
    if end < total:
        nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"posts_{page+1}"))
    if nav_row:
        kb.append(nav_row)
    kb.append([InlineKeyboardButton("⬅️ Back to Profile", callback_data="profile")])
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML, disable_web_page_preview=True)

# --- Search: robust mapping and multiple fallbacks ---
async def fetch_search(update, context, text, filter_type=types.InputMessagesFilterEmpty(), is_callback=False):
    """
    Use SearchGlobalRequest and build as many valid clickable links as possible using:
     - chat objects returned in result.chats (username or id + access_hash)
     - normalized numeric /c/ links
     - store last_search_chats in context.user_data for later resolution
    """
    try:
        # perform global search
        result = await telethon_client(functions.messages.SearchGlobalRequest(
            q=text,
            filter=filter_type,
            min_date=None,
            max_date=None,
            offset_rate=0,
            offset_peer=types.InputPeerEmpty(),
            offset_id=0,
            limit=50
        ))
        context.user_data['search_results'] = result
        context.user_data['search_query'] = text
        context.user_data['search_filter'] = filter_type

        # Build a robust chat_map from result.chats
        chat_map = {}
        if hasattr(result, 'chats') and result.chats:
            for ch in result.chats:
                # try a few keys
                try:
                    cid = getattr(ch, 'id', None) or getattr(ch, 'channel_id', None) or getattr(ch, 'chat_id', None)
                    if cid is not None:
                        chat_map[int(cid)] = ch
                        chat_map[str(cid)] = ch
                        # also map abs and without -100
                        try:
                            chat_map[abs(int(cid))] = ch
                        except Exception:
                            pass
                except Exception:
                    continue
                # map username if present
                uname = getattr(ch, 'username', None)
                if uname:
                    chat_map[str(uname).lower()] = ch
        # store chat_map for later profile/posts resolution
        context.user_data['last_search_chats'] = chat_map

        # Build result list
        page = 1
        per_page = 5
        all_items = []
        for msg in result.messages:
            msg_id = msg.id
            chat_id = extract_chat_id_from_msg(msg)
            chat_obj = None
            if chat_id is not None:
                chat_obj = chat_map.get(int(chat_id)) or chat_map.get(str(chat_id)) or chat_map.get(abs(int(chat_id))) if isinstance(chat_id, int) else None
            # If still no chat_obj, and message has sender_chat or via_bot_id, attempt other fields:
            if chat_obj is None:
                # message may have .sender_chat in some cases
                sc = getattr(msg, 'sender_chat', None)
                if sc is not None:
                    chat_obj = sc
            # build link using best available info
            link = ""
            if chat_obj:
                link = tg_link_from_chat_obj(chat_obj, msg_id)
            elif chat_id is not None:
                # fallback to numeric /c/ link
                link = tg_link_from_chat_id(chat_id, msg_id)
            else:
                link = ""
            content = (msg.message[:140]) if getattr(msg, 'message', None) else f"[{get_media_type(msg)}]"
            if link:
                all_items.append(f"🔗 <a href='{link}'>{content}</a>")
            else:
                # show origin/source if possible
                src = ""
                if chat_obj:
                    src = getattr(chat_obj, 'title', None) or getattr(chat_obj, 'username', None) or str(getattr(chat_obj, 'id', 'unknown'))
                else:
                    src = str(chat_id or "unknown")
                all_items.append(f"• {content}  — {src}")
        total_items = len(all_items)
        start = (page - 1) * per_page
        end = min(start + per_page, total_items)
        page_items = all_items[start:end]

        if not page_items:
            msg_text = "No search results found globally."
            if is_callback:
                await update.callback_query.edit_message_text(msg_text)
            else:
                await update.effective_message.reply_text(msg_text)
            return

        text_output = f"<blockquote><b>Search: {text}</b>\n\n"
        for item in page_items:
            text_output += f"{item}\n"
        text_output += f"\nPage {page}/{max(1, (total_items + per_page - 1) // per_page)}\n"
        text_output += "Results include public channels/groups (even if not joined). Click links to open in Telegram.</blockquote>"

        filter_buttons = [
            InlineKeyboardButton("All", callback_data="filter_all"),
            InlineKeyboardButton("Posts", callback_data="filter_posts"),
            InlineKeyboardButton("Media", callback_data="filter_media"),
            InlineKeyboardButton("Photos", callback_data="filter_photos"),
            InlineKeyboardButton("Videos", callback_data="filter_videos"),
            InlineKeyboardButton("Docs", callback_data="filter_docs"),
            InlineKeyboardButton("Audio", callback_data="filter_audio"),
            InlineKeyboardButton("Voice", callback_data="filter_voice"),
            InlineKeyboardButton("Links", callback_data="filter_links"),
        ]
        filter_rows = [filter_buttons[i:i+3] for i in range(0, len(filter_buttons), 3)]
        nav_buttons = []
        if page > 1:
            nav_buttons.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"search_{page-1}"))
        if end < total_items:
            nav_buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"search_{page+1}"))
        keyboard = filter_rows + ([nav_buttons] if nav_buttons else []) + [[InlineKeyboardButton("🔄 New Search", callback_data="search"), InlineKeyboardButton("⬅️ Back", callback_data="more")]]

        if is_callback:
            await update.callback_query.edit_message_text(text_output, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        else:
            await update.effective_message.reply_text(text_output, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    except Exception as e:
        msg_text = f"❌ Search failed: {e}"
        if is_callback:
            await update.callback_query.edit_message_text(msg_text)
        else:
            await update.effective_message.reply_text(msg_text)

async def handle_search_pagination(update, context, page):
    query = update.callback_query
    await query.answer()
    result = context.user_data.get('search_results')
    if not result:
        await query.edit_message_text("No results found.")
        return
    # Rebuild chat_map
    chat_map = context.user_data.get('last_search_chats') or {}
    per_page = 5
    all_items = []
    for msg in result.messages:
        msg_id = msg.id
        chat_id = extract_chat_id_from_msg(msg)
        chat_obj = None
        if chat_id is not None:
            chat_obj = chat_map.get(int(chat_id)) or chat_map.get(str(chat_id)) or chat_map.get(abs(int(chat_id))) if isinstance(chat_id, int) else None
        if chat_obj is None:
            sc = getattr(msg, 'sender_chat', None)
            if sc is not None:
                chat_obj = sc
        link = ""
        if chat_obj:
            link = tg_link_from_chat_obj(chat_obj, msg_id)
        elif chat_id is not None:
            link = tg_link_from_chat_id(chat_id, msg_id)
        content = (msg.message[:140]) if getattr(msg, 'message', None) else f"[{get_media_type(msg)}]"
        if link:
            all_items.append(f"🔗 <a href='{link}'>{content}</a>")
        else:
            src = getattr(chat_obj, 'title', None) or getattr(chat_obj, 'username', None) or str(chat_id or "")
            all_items.append(f"• {content}  — {src}")
    total_items = len(all_items)
    start = (page - 1) * per_page
    end = min(start + per_page, total_items)
    page_items = all_items[start:end]
    if not page_items:
        await query.edit_message_text("No more results.")
        return
    text = f"<blockquote><b>Search: {context.user_data.get('search_query', '')}</b>\n\n"
    for item in page_items:
        text += f"{item}\n"
    text += f"\nPage {page}/{max(1, (total_items + per_page - 1) // per_page)}\n"
    text += "Results include public channels/groups (even if not joined)</blockquote>"
    filter_buttons = [
        InlineKeyboardButton("All", callback_data="filter_all"),
        InlineKeyboardButton("Posts", callback_data="filter_posts"),
        InlineKeyboardButton("Media", callback_data="filter_media"),
        InlineKeyboardButton("Photos", callback_data="filter_photos"),
        InlineKeyboardButton("Videos", callback_data="filter_videos"),
        InlineKeyboardButton("Docs", callback_data="filter_docs"),
        InlineKeyboardButton("Audio", callback_data="filter_audio"),
        InlineKeyboardButton("Voice", callback_data="filter_voice"),
        InlineKeyboardButton("Links", callback_data="filter_links"),
    ]
    filter_rows = [filter_buttons[i:i+3] for i in range(0, len(filter_buttons), 3)]
    nav_buttons = []
    if page > 1:
        nav_buttons.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"search_{page-1}"))
    if end < total_items:
        nav_buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"search_{page+1}"))
    keyboard = filter_rows + ([nav_buttons] if nav_buttons else []) + [[InlineKeyboardButton("🔄 New Search", callback_data="search"), InlineKeyboardButton("⬅️ Back", callback_data="more")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML, disable_web_page_preview=True)

# --- Stories (kept) ---
async def display_story(update, context):
    query = update.callback_query
    stories = context.user_data.get('stories_list')
    index = context.user_data.get('story_index', 0)
    if not stories:
        await query.edit_message_text("No stories found.")
        return
    if index < 0: index = 0
    if index >= len(stories): index = len(stories) - 1
    context.user_data['story_index'] = index
    story = stories[index]
    kb = [
        [InlineKeyboardButton("⬅️ Previous", callback_data="story_prev"), InlineKeyboardButton("Next ➡️", callback_data="story_next")],
        [InlineKeyboardButton("⬅️ Back to Profile", callback_data="profile")]
    ]
    try:
        media_file = BytesIO()
        await telethon_client.download_media(story, file=media_file)
        media_file.seek(0)
        try:
            await query.edit_message_text(f"Story {index+1}/{len(stories)} (media file)", reply_markup=InlineKeyboardMarkup(kb))
        except Exception:
            await query.edit_message_text(f"Story {index+1}/{len(stories)} (no media)", reply_markup=InlineKeyboardMarkup(kb))
    except Exception as e:
        await query.edit_message_text(f"Story {index+1}/{len(stories)}\nCannot download media: {e}", reply_markup=InlineKeyboardMarkup(kb))

async def fetch_stories(update, context, entity):
    try:
        stories = None
        if hasattr(telethon_client, 'get_stories'):
            try:
                stories = await telethon_client.get_stories(entity)
            except Exception:
                stories = None
        if (not stories) and (hasattr(types, 'GetStoriesRequest')):
            try:
                stories = await telethon_client(types.GetStoriesRequest(entity))
            except Exception:
                stories = None
        if not stories:
            await update.callback_query.edit_message_text("No stories available for this user.")
            return
        context.user_data['story_entity'] = entity
        context.user_data['stories_list'] = stories
        context.user_data['story_index'] = 0
        await display_story(update, context)
    except Exception as e:
        await update.callback_query.edit_message_text(f"❌ Failed to fetch stories: {e}")

# --- Words / friends / names (kept) ---
async def perform_words_analysis(update, context, target, limit=20):
    try:
        entity = await telethon_client.get_entity(target)
        messages = await telethon_client.get_messages(entity, limit=200)
        if not messages:
            await update.message.reply_text("No messages found for this user.")
            return
        stop_words = {
            "the","a","an","is","are","was","were","to","of","in","on","for","and","or","but","with",
            "at","by","from","up","about","into","through","during","before","after","above","below",
            "can","will","just","not","you","your","i","me","my","it","its","this","that","these","those",
            "we","our","they","them","their","be","been","being","do","does","did","doing","have","has","had",
            "having","he","she","his","her","him","so","if","then","than","too","very","am","as","www","http","https","t.me","telegram"
        }
        word_data = {}
        for msg in messages:
            if getattr(msg, 'message', None):
                words = re.findall(r'\b[a-zA-Z0-9_]+\b', msg.message.lower())
                for word in words:
                    if len(word) > 2 and word not in stop_words:
                        if word not in word_data:
                            word_data[word] = {'count': 0, 'messages': set()}
                        word_data[word]['count'] += 1
                        word_data[word]['messages'].add(msg.id)
        if not word_data:
            await update.message.reply_text("No meaningful words found.")
            return
        sorted_words = sorted(word_data.items(), key=lambda x: x[1]['count'], reverse=True)[:limit]
        text = f"<blockquote>Word frequency for @{getattr(entity, 'username', 'N/A')}:\n"
        for word, data in sorted_words:
            text += f"- {word}: {data['count']}\n"
        text += "</blockquote>"
        kb = [[InlineKeyboardButton("⬅️ Back", callback_data="more")]]
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def fetch_words(update, context, target):
    context.user_data['word_target'] = target
    await perform_words_analysis(update, context, target, 20)

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
            if getattr(m, 'sender_id', None) == target_user.id and getattr(m, 'reply_to_msg_id', None):
                try:
                    reply_to_msg = await telethon_client.get_messages(group_entity, ids=m.reply_to_msg_id)
                    if reply_to_msg and getattr(reply_to_msg, 'sender_id', None):
                        sender_id = reply_to_msg.sender_id
                        if sender_id not in reply_data:
                            reply_data[sender_id] = {'count': 0, 'date': str(m.date)}
                            try:
                                sender_entity = await telethon_client.get_entity(sender_id)
                                reply_data[sender_id]['name'] = f"{sender_entity.first_name} {getattr(sender_entity, 'last_name', '')}"
                            except:
                                reply_data[sender_id]['name'] = "Unknown"
                        reply_data[sender_id]['count'] += 1
                except:
                    pass
        sorted_replies = sorted(reply_data.items(), key=lambda x: x[1]['count'], reverse=True)[:10]
        text = "<blockquote>Replies in group:\n"
        for sid, data in sorted_replies:
            text += f"|{data['date'][:10]} - {data['name']} ({data['count']})\n"
        text += "</blockquote>"
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def fetch_names(update, context, target):
    try:
        ent = await telethon_client.get_entity(target)
        save_user_history(ent.id, getattr(ent, 'username', ''), getattr(ent, 'first_name', ''), getattr(ent, 'last_name', ''))
        history = get_user_history(ent.id)
        text = f"<blockquote>Names history {ent.first_name} (@{getattr(ent, 'username', 'N/A')}):\n\nusernames:\n"
        if history:
            seen = set()
            for h in history:
                if h[0] and h[0] not in seen:
                    text += f"1. @{h[0]} [{h[3][:10]}]\n"
                    seen.add(h[0])
        else:
            text += "No history yet.\n"
        text += "\nfirst name / last name:\n"
        if history:
            for h in history[:5]:
                text += f"|{h[3][:10]} -> {h[1]} {h[2]}\n"
        else:
            text += "No history yet.\n"
        text += "</blockquote>"
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

# --- Message handler (keeps original flows and uses fetch_search/fetch_profile where necessary) ---
async def handle_link(update, context):
    user_id = update.effective_user.id
    text = update.message.text or ""
    # password gating
    if context.user_data.get('state') == 'awaiting_password':
        if text == BOT_PASSWORD:
            add_authenticated_user(user_id)
            context.user_data['state'] = None
            await update.message.reply_text("✅ Access granted!")
            keyboard = [
                [InlineKeyboardButton("📥 Inbox", callback_data="inbox"), InlineKeyboardButton("👤 Profile", callback_data="profile")],
                [InlineKeyboardButton("🔗 Fetch Telegram", callback_data="fetch")],
                [InlineKeyboardButton("➕ More Commands", callback_data="more")]
            ]
            await update.message.reply_text("🤖 TELEGRAM ASSISTANT", reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await update.message.reply_text("❌ Incorrect password. Please try again.")
        return
    if not is_authenticated(user_id):
        await update.message.reply_text("🔐 Password required. Please run /start and authenticate first.")
        return
    # reply flow
    if context.user_data.get('reply_to'):
        target_chat = context.user_data['reply_to']
        try:
            await telethon_client.send_message(target_chat, text)
            context.user_data['reply_to'] = None
            await update.message.reply_text("✅ Reply sent!")
        except Exception as e:
            await update.message.reply_text(f"❌ Failed: {e}")
        return
    # state-driven commands
    state = context.user_data.get('state')
    if state:
        context.user_data['state'] = None
        if state == 'profile_query':
            await fetch_profile(update, context, text)
            return
        elif state == 'search':
            context.user_data['search_query'] = text
            await fetch_search(update, context, text)
            return
        elif state == 'words':
            await fetch_words(update, context, text)
            return
        elif state == 'friends':
            await fetch_friends(update, context, text)
            return
        elif state == 'names':
            await fetch_names(update, context, text)
            return
    # handle t.me links to fetch messages
    if "t.me" in text:
        username, msg_id = parse_tg_link(text)
        if not username:
            await update.message.reply_text("Invalid link format.")
            return
        try:
            entity = await telethon_client.get_entity(username)
            if msg_id:
                msg = await telethon_client.get_messages(entity, ids=msg_id)
                if msg:
                    await safe_send(update.message.chat_id, context.bot, msg, from_chat_id=entity.id, message_id=msg.id)
                else:
                    await update.message.reply_text("Message not found.")
            else:
                status_msg = await update.message.reply_text("Fetching batch (max 20)...")
                messages = await telethon_client.get_messages(entity, limit=20)
                if not messages:
                    await status_msg.edit_text("No messages found.")
                    return
                for idx, msg in enumerate(messages, 1):
                    if idx % 5 == 0:
                        try:
                            await status_msg.edit_text(f"Fetching {idx}/{len(messages)}...")
                        except:
                            pass
                    await safe_send(update.message.chat_id, context.bot, msg, from_chat_id=entity.id, message_id=msg.id)
                await status_msg.edit_text(f"✅ Batch complete!")
        except Exception as e:
            await handle_telethon_error(update, e)
    else:
        await update.message.reply_text("👋 Use the menu buttons, or send a Telegram link.")

# --- Inbox listener (kept) ---
@telethon_client.on(events.NewMessage(incoming=True))
async def inbox_listener(event):
    if event.is_private and not event.out:
        try:
            sender = await event.get_sender()
            add_inbox_message(event.chat_id, sender.id, getattr(sender, 'first_name', 'Unknown'),
                              getattr(sender, 'username', 'N/A'),
                              event.raw_text if getattr(event, 'raw_text', None) else "",
                              get_media_type(event), str(event.date))
        except Exception as e:
            print(f"Inbox Error: {e}")

# --- Main: initialize handlers and start ---
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
    bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))
    print("Bot running...")
    await bot_app.initialize()
    await bot_app.start()
    try:
        await bot_app.updater.start_polling()
    except Exception:
        await bot_app.start_polling()
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=10000), daemon=True).start()
    await asyncio.Event().wait()

if __name__ == '__main__':
    asyncio.run(main())
