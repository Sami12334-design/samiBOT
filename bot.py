import re
import asyncio
import os
import threading
import sqlite3
import html
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
        filter_key = data.split("_", 1)[1]
        context.user_data["search_filter_key"] = filter_key
        query_text = context.user_data.get("search_query", "")
        if query_text:
            await fetch_search(update, context, query_text, is_callback=True)
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

# ============================================================
# GLOBAL SEARCH - PUBLIC CHANNELS + PUBLIC GROUPS
# ============================================================


def _chat_map_from_result(result):
    """Build a reliable lookup map from Telegram's returned chats."""
    chat_map = {}
    for chat in getattr(result, "chats", []) or []:
        cid = getattr(chat, "id", None)
        if cid is not None:
            try:
                cid = int(cid)
                chat_map[cid] = chat
                chat_map[str(cid)] = chat
                chat_map[abs(cid)] = chat
            except Exception:
                pass
        username = getattr(chat, "username", None)
        if username:
            chat_map[username.lower()] = chat
    return chat_map


def _message_chat_id(msg):
    """Get the peer/chat id represented by a Telethon Message."""
    direct = getattr(msg, "chat_id", None)
    if direct is not None:
        try:
            return int(direct)
        except Exception:
            pass

    peer = getattr(msg, "peer_id", None) or getattr(msg, "to_id", None)
    if isinstance(peer, PeerChannel):
        return int(peer.channel_id)
    if isinstance(peer, PeerChat):
        return int(peer.chat_id)
    if isinstance(peer, PeerUser):
        return int(peer.user_id)

    for attr in ("channel_id", "chat_id", "user_id"):
        value = getattr(peer, attr, None)
        if value is not None:
            try:
                return int(value)
            except Exception:
                pass
    return None


def _public_link(chat, message_id):
    """Return a public t.me message URL, or empty string."""
    if chat is None:
        return ""
    username = getattr(chat, "username", None)
    if username:
        return f"https://t.me/{username}/{message_id}"
    return ""


def _source_name(chat):
    if chat is None:
        return "Unknown source"
    title = getattr(chat, "title", None)
    username = getattr(chat, "username", None)
    if title and username:
        return f"{title} (@{username})"
    if title:
        return str(title)
    if username:
        return f"@{username}"
    return "Unknown source"


def _message_preview(msg):
    text = getattr(msg, "message", None)
    if not text:
        return "[Media]"
    text = " ".join(text.split())
    return text[:110] + ("..." if len(text) > 110 else "")


def _matches_client_filter(msg, filter_key):
    """Apply filters that channels.searchPosts does not expose."""
    if filter_key in (None, "all", "posts"):
        return True
    if filter_key == "photos":
        return bool(getattr(msg, "photo", None))
    if filter_key == "videos":
        return bool(getattr(msg, "video", None))
    if filter_key == "docs":
        return bool(getattr(msg, "document", None))
    if filter_key == "audio":
        return bool(getattr(msg, "audio", None))
    if filter_key == "voice":
        return bool(getattr(msg, "voice", None))
    if filter_key == "links":
        text = getattr(msg, "message", None) or ""
        return bool(re.search(r"(?:https?://|t\.me/|www\.)", text, re.I))
    if filter_key == "media":
        return any([
            getattr(msg, "photo", None),
            getattr(msg, "video", None),
            getattr(msg, "document", None),
            getattr(msg, "audio", None),
            getattr(msg, "voice", None),
            getattr(msg, "sticker", None),
            getattr(msg, "gif", None),
        ])
    return True


async def _search_public_channels(query_text):
    """Global public-channel post search, including unjoined channels."""
    result = await telethon_client(
        functions.channels.SearchPostsRequest(
            hashtag=None,
            query=query_text,
            offset_rate=0,
            offset_peer=types.InputPeerEmpty(),
            offset_id=0,
            limit=100,
        )
    )

    chat_map = _chat_map_from_result(result)
    items = []

    for msg in getattr(result, "messages", []) or []:
        chat_id = _message_chat_id(msg)
        chat = None
        if chat_id is not None:
            chat = (
                chat_map.get(chat_id)
                or chat_map.get(str(chat_id))
                or chat_map.get(abs(chat_id))
            )

        if chat is None:
            sender_chat = getattr(msg, "sender_chat", None)
            if sender_chat is not None:
                chat = sender_chat

        # A public channel must have a username so the result can
        # be opened by ANY Telegram user without joining first.
        link = _public_link(chat, msg.id)
        if not link:
            continue

        items.append({
            "message": msg,
            "chat": chat,
            "link": link,
            "source": _source_name(chat),
            "kind": "channel",
        })

    return items, chat_map


async def _search_public_groups(query_text, filter_type):
    """Global group search. Only public supergroups are shown."""
    result = await telethon_client(
        functions.messages.SearchGlobalRequest(
            broadcasts_only=False,
            groups_only=True,
            users_only=False,
            folder_id=None,
            q=query_text,
            filter=filter_type,
            min_date=None,
            max_date=None,
            offset_rate=0,
            offset_peer=types.InputPeerEmpty(),
            offset_id=0,
            limit=100,
        )
    )

    chat_map = _chat_map_from_result(result)
    items = []

    for msg in getattr(result, "messages", []) or []:
        chat_id = _message_chat_id(msg)
        chat = None
        if chat_id is not None:
            chat = (
                chat_map.get(chat_id)
                or chat_map.get(str(chat_id))
                or chat_map.get(abs(chat_id))
            )

        if chat is None:
            continue

        username = getattr(chat, "username", None)
        is_supergroup = (
            isinstance(chat, types.Channel)
            and bool(getattr(chat, "megagroup", False))
        )

        if not username or not is_supergroup:
            continue

        link = _public_link(chat, msg.id)
        if not link:
            continue

        items.append({
            "message": msg,
            "chat": chat,
            "link": link,
            "source": _source_name(chat),
            "kind": "group",
        })

    return items, chat_map


async def perform_global_search(query_text, filter_key="all"):
    """Search public channels and public supergroups."""
    filter_map = {
        "all": types.InputMessagesFilterEmpty,
        "posts": types.InputMessagesFilterEmpty,
        "media": types.InputMessagesFilterEmpty,
        "photos": getattr(types, "InputMessagesFilterPhotos", types.InputMessagesFilterEmpty),
        "videos": getattr(types, "InputMessagesFilterVideo", types.InputMessagesFilterEmpty),
        "docs": getattr(types, "InputMessagesFilterDocument", types.InputMessagesFilterEmpty),
        "audio": getattr(types, "InputMessagesFilterAudio", types.InputMessagesFilterEmpty),
        "voice": getattr(types, "InputMessagesFilterVoice", types.InputMessagesFilterEmpty),
        "links": getattr(types, "InputMessagesFilterUrl", types.InputMessagesFilterEmpty),
    }
    filter_class = filter_map.get(filter_key, types.InputMessagesFilterEmpty)
    filter_type = filter_class()

    all_items = []
    combined_chat_map = {}
    errors = []

    # Public channels: this is the API specifically designed to find
    # posts in channels the user has NOT joined.
    try:
        channel_items, channel_map = await _search_public_channels(query_text)
        all_items.extend(channel_items)
        combined_chat_map.update(channel_map)
    except Exception as e:
        print("Public channel search error:", repr(e))
        errors.append(f"channels.searchPosts: {e}")

    # Public supergroups: global group search.
    try:
        group_items, group_map = await _search_public_groups(query_text, filter_type)
        all_items.extend(group_items)
        combined_chat_map.update(group_map)
    except Exception as e:
        print("Public group search error:", repr(e))
        errors.append(f"messages.searchGlobal: {e}")

    # channels.searchPosts has no MessagesFilter parameter, so apply
    # media/link filtering locally to channel results.
    all_items = [
        item for item in all_items
        if _matches_client_filter(item["message"], filter_key)
    ]

    # Remove duplicate (source, message_id) pairs.
    unique = {}
    for item in all_items:
        chat = item["chat"]
        username = (getattr(chat, "username", "") or "").lower()
        key = (username, int(item["message"].id))
        unique[key] = item

    all_items = list(unique.values())
    all_items.sort(
        key=lambda item: getattr(item["message"], "date", None) or 0,
        reverse=True,
    )

    return all_items, combined_chat_map, errors


async def fetch_search(update, context, text, filter_type=None, is_callback=False):
    """Run the real global search and display clickable result buttons."""
    text = (text or "").strip()
    if not text:
        message = "❌ Search keyword cannot be empty."
        if is_callback:
            await update.callback_query.edit_message_text(message)
        else:
            await update.effective_message.reply_text(message)
        return

    # Convert Telegram filter object back to our stable filter key.
    filter_key = context.user_data.get("search_filter_key", "all")
    if filter_type is not None:
        name = filter_type.__class__.__name__
        reverse = {
            "InputMessagesFilterEmpty": "all",
            "InputMessagesFilterPhotos": "photos",
            "InputMessagesFilterVideo": "videos",
            "InputMessagesFilterDocument": "docs",
            "InputMessagesFilterAudio": "audio",
            "InputMessagesFilterVoice": "voice",
            "InputMessagesFilterUrl": "links",
        }
        filter_key = reverse.get(name, filter_key)

    status = (
        "🔎 <b>Searching Telegram globally...</b>\n\n"
        f"Query: <code>{html.escape(text)}</code>\n\n"
        "🌍 Public channels + public groups\n"
        "📡 Looking beyond your joined chats..."
    )

    if is_callback:
        try:
            await update.callback_query.edit_message_text(
                status,
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
    else:
        await update.effective_message.reply_text(
            status,
            parse_mode=ParseMode.HTML,
        )

    try:
        items, chat_map, errors = await perform_global_search(
            text,
            filter_key,
        )

        context.user_data["search_items"] = items
        context.user_data["search_query"] = text
        context.user_data["search_filter_key"] = filter_key
        context.user_data["last_search_chats"] = chat_map

        await render_search_page(
            update,
            context,
            1,
            is_callback=is_callback,
        )

    except FloodWaitError as e:
        message = f"⏳ Telegram rate limit. Try again in {e.seconds} seconds."
        if is_callback:
            await update.callback_query.edit_message_text(message)
        else:
            await update.effective_message.reply_text(message)
    except Exception as e:
        print("Search failed:", repr(e))
        message = (
            "❌ <b>Search failed</b>\n\n"
            f"<code>{html.escape(str(e))}</code>"
        )
        if is_callback:
            await update.callback_query.edit_message_text(
                message,
                parse_mode=ParseMode.HTML,
            )
        else:
            await update.effective_message.reply_text(
                message,
                parse_mode=ParseMode.HTML,
            )


async def render_search_page(update, context, page, is_callback=True):
    """Render search results as REAL URL buttons."""
    items = context.user_data.get("search_items") or []
    query_text = context.user_data.get("search_query", "")
    filter_key = context.user_data.get("search_filter_key", "all")

    per_page = 5
    total = len(items)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(int(page), total_pages))
    start = (page - 1) * per_page
    page_items = items[start:start + per_page]

    if not page_items:
        text = (
            "🔎 <b>TELEGRAM GLOBAL SEARCH</b>\n\n"
            f"Query: <code>{html.escape(query_text)}</code>\n\n"
            "❌ No public results found.\n\n"
            "Note: Telegram only exposes public/unrestricted results here."
        )
        keyboard = [[
            InlineKeyboardButton("🔄 New Search", callback_data="search"),
            InlineKeyboardButton("⬅️ Back", callback_data="more"),
        ]]
    else:
        text = (
            "🔎 <b>TELEGRAM GLOBAL SEARCH</b>\n\n"
            f"🔍 Query: <code>{html.escape(query_text)}</code>\n"
            f"📊 Results: <b>{total}</b>\n"
            f"📄 Page: <b>{page}/{total_pages}</b>\n"
            f"🎯 Filter: <b>{html.escape(filter_key)}</b>\n\n"
            "👇 <b>Tap a result to open the exact Telegram message:</b>"
        )
        keyboard = []

        for number, item in enumerate(page_items, start=start + 1):
            msg = item["message"]
            source = item["source"]
            link = item["link"]
            kind_icon = "📢" if item["kind"] == "channel" else "👥"
            media_icon = get_media_type(msg)
            preview = _message_preview(msg)
            button_text = f"{number}. {kind_icon} {source} {media_icon} {preview}"
            if len(button_text) > 95:
                button_text = button_text[:92] + "..."

            # IMPORTANT: url= opens the exact Telegram message.
            # callback_data is NOT used for result buttons.
            keyboard.append([
                InlineKeyboardButton(
                    button_text,
                    url=link,
                )
            ])

        keyboard.extend([
            [
                InlineKeyboardButton("🔎 All", callback_data="filter_all"),
                InlineKeyboardButton("📷 Photos", callback_data="filter_photos"),
                InlineKeyboardButton("🎬 Videos", callback_data="filter_videos"),
            ],
            [
                InlineKeyboardButton("📄 Docs", callback_data="filter_docs"),
                InlineKeyboardButton("🎵 Audio", callback_data="filter_audio"),
                InlineKeyboardButton("🎤 Voice", callback_data="filter_voice"),
            ],
            [InlineKeyboardButton("🔗 Links", callback_data="filter_links")],
        ])

        nav = []
        if page > 1:
            nav.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"search_{page - 1}"))
        if page < total_pages:
            nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"search_{page + 1}"))
        if nav:
            keyboard.append(nav)

        keyboard.append([
            InlineKeyboardButton("🔄 New Search", callback_data="search"),
            InlineKeyboardButton("⬅️ Back", callback_data="more"),
        ])

    markup = InlineKeyboardMarkup(keyboard)

    if is_callback:
        await update.callback_query.edit_message_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=markup,
            disable_web_page_preview=True,
        )
    else:
        await update.effective_message.reply_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=markup,
            disable_web_page_preview=True,
        )


async def handle_search_pagination(update, context, page):
    query = update.callback_query
    await query.answer()
    if not context.user_data.get("search_items"):
        await query.edit_message_text(
            "❌ Search results expired. Please start a new search."
        )
        return
    await render_search_page(update, context, page, is_callback=True)


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
            context.user_data['search_filter_key'] = 'all'
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
        me = await telethon_client.get_me()
        print("Telethon connected!")
        print("Telegram USER account:", getattr(me, "username", None), "ID:", getattr(me, "id", None), "BOT:", getattr(me, "bot", False))
        if getattr(me, "bot", False):
            raise RuntimeError("STRING_SESSION belongs to a BOT account. Use a normal Telegram USER account session.")
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
