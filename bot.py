import re
import asyncio
import os
import threading
import sqlite3
from io import BytesIO
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler, ContextTypes
from telethon import TelegramClient, events, functions, types
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError, ChannelPrivateError, UsernameNotOccupiedError, MessageIdInvalidError

# --- FLASK SETUP (Keeps Render awake) ---
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

telethon_client = TelegramClient(StringSession(STRING_SESSION), API_ID, API_HASH)

# --- SQLITE DATABASE SETUP ---
def init_db():
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY)''')
    c.execute('''CREATE TABLE IF NOT EXISTS inbox (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, sender_id INTEGER, name TEXT, username TEXT, text TEXT, media_type TEXT, date TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS tracking (target_id INTEGER PRIMARY KEY, username TEXT)''')
    conn.commit()
    conn.close()

# --- DATABASE HELPERS ---
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

def add_track(target_id, username):
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO tracking (target_id, username) VALUES (?, ?)", (target_id, username))
    conn.commit()
    conn.close()

def remove_track(target_id):
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute("DELETE FROM tracking WHERE target_id = ?", (target_id,))
    conn.commit()
    conn.close()

def get_tracked_users():
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute("SELECT target_id, username FROM tracking")
    rows = c.fetchall()
    conn.close()
    return rows

# --- EXISTING FETCHER (PRESERVED) ---
async def safe_send(chat_id, bot, msg, from_chat_id, message_id):
    text = msg.message
    try:
        if msg.photo or msg.video or msg.document or msg.voice or msg.audio or msg.gif:
            media_bytes = BytesIO()
            await telethon_client.download_media(msg, file=media_bytes)
            media_bytes.seek(0)
            if msg.photo:
                await bot.send_photo(chat_id, photo=media_bytes, caption=text)
            elif msg.video:
                await bot.send_video(chat_id, video=media_bytes, caption=text)
            elif msg.document:
                await bot.send_document(chat_id, document=media_bytes, caption=text)
            elif msg.voice:
                await bot.send_voice(chat_id, voice=media_bytes, caption=text)
            elif msg.audio:
                await bot.send_audio(chat_id, audio=media_bytes, caption=text)
            elif msg.gif:
                await bot.send_animation(chat_id, animation=media_bytes, caption=text)
        elif msg.sticker:
            sticker_bytes = BytesIO()
            await telethon_client.download_media(msg, file=sticker_bytes)
            sticker_bytes.seek(0)
            await bot.send_sticker(chat_id, sticker=sticker_bytes)
        elif text:
            await bot.send_message(chat_id, text=text)
        else:
            await bot.send_message(chat_id, "Unsupported message type.")
    except Exception as e:
        error_str = str(e)
        if "must forward even restricted" in error_str.lower() or "cannot be reused" in error_str.lower():
            await bot.send_message(chat_id, "🔒 Telegram is blocking the download of this media. You must forward it manually.")
        else:
            await bot.send_message(chat_id, f"Failed to send media: {error_str}")

async def handle_telethon_error(update, error):
    error_str = str(error)
    if isinstance(error, FloodWaitError):
        await update.message.reply_text(f"⚠️ Too many requests! Please wait {error.seconds} seconds.")
    elif isinstance(error, ChannelPrivateError):
        await update.message.reply_text("🔒 This channel is private.")
    elif isinstance(error, UsernameNotOccupiedError):
        await update.message.reply_text("❌ Username not found.")
    elif isinstance(error, MessageIdInvalidError):
        await update.message.reply_text("❌ Message ID invalid.")
    else:
        await update.message.reply_text(f"❌ Error: {error_str}")

def parse_tg_link(text):
    pattern = r'https?://t\.me/([a-zA-Z0-9_]+)/(\d+)'
    match = re.search(pattern, text)
    if match:
        return match.group(1), int(match.group(2))
    channel_pattern = r'https?://t\.me/([a-zA-Z0-9_]+)'
    match = re.search(channel_pattern, text)
    if match:
        return match.group(1), None
    return None, None

def get_media_type(event):
    if event.photo:
        return "📷 Photo"
    elif event.video:
        return "🎬 Video"
    elif event.document:
        return "🎞️ GIF" if event.gif else "📄 Document"
    elif event.audio:
        return "🎵 Audio"
    elif event.voice:
        return "🎤 Voice"
    elif event.sticker:
        return "🧩 Sticker"
    else:
        return "💬 Text"

# --- COMMANDS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

async def logout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    remove_authenticated_user(user_id)
    await update.message.reply_text("🔒 You have been logged out. Please run /start again.")

# --- CALLBACK HANDLER ---
async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    if not is_authenticated(user_id):
        await query.edit_message_text("🔐 Password required. Please run /start and authenticate first.")
        return

    data = query.data

    if data == "inbox":
        rows = get_inbox_conversations()
        if not rows:
            text = "📥 INBOX\n\nNo new private messages detected."
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
        kb = [
            [InlineKeyboardButton("↩️ Reply", callback_data=f"reply_{chat_id}"), InlineKeyboardButton("👤 Profile", callback_data="profile")],
            [InlineKeyboardButton("🔄 Refresh", callback_data=f"refresh_{chat_id}"), InlineKeyboardButton("⬅️ Back", callback_data="inbox")]
        ]
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
        kb = [
            [InlineKeyboardButton("↩️ Reply", callback_data=f"reply_{chat_id}"), InlineKeyboardButton("👤 Profile", callback_data="profile")],
            [InlineKeyboardButton("🔄 Refresh", callback_data=f"refresh_{chat_id}"), InlineKeyboardButton("⬅️ Back", callback_data="inbox")]
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))

    elif data == "profile":
        await query.edit_message_text("👤 PROFILE\n\nEnter a Telegram username or ID:")
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
            "search": ("🔎 SEARCH", "Enter a username, name, keyword, or search query:"),
            "stats": ("📊 STATISTICS", "Enter target username/ID or chat link:"),
            "track": ("🔔 TRACK", "Enter target username/ID to track:"),
            "names": ("🔗 NAMES", "Enter target username/ID:"),
            "groups": ("👥 GROUPS", "Enter target username/ID:"),
            "messages": ("💬 MESSAGES", "Enter target username/ID or chat link:"),
            "analysis": ("🔎 ANALYSIS", "Enter target username/ID or chat link:"),
            "channels": ("📢 CHANNELS", "Enter target username/ID or chat link:"),
            "rep": ("👍 REPUTATION", "Enter target username/ID or chat link:"),
            "friends": ("👥 FRIENDS", "Enter target username/ID:"),
            "reactions": ("🔄 REACTIONS", "Enter target username/ID or chat link:"),
            "gifts": ("🎁 GIFTS", "Enter target username/ID:"),
            "share": ("📤 SHARE", "Enter target username/ID or message link:"),
            "words": ("🔵 WORDS FREQUENCY", "Enter target username/ID or chat link:"),
            "common": ("👥 COMMON GROUPS", "Enter target username/ID:")
        }
        title, prompt = prompts[data]
        await query.edit_message_text(f"{title}\n\n{prompt}")
        context.user_data['state'] = data

# --- FEATURE IMPLEMENTATIONS ---
async def fetch_profile(update, context, target):
    try:
        entity = await telethon_client.get_entity(target)
        text = f"👤 PROFILE\n\n"
        text += f"Name: {getattr(entity, 'first_name', '')} {getattr(entity, 'last_name', '')}\n"
        text += f"Username: @{getattr(entity, 'username', 'N/A')}\n"
        text += f"ID: {entity.id}\n"
        text += f"Bio: {getattr(entity, 'about', 'Not available')}\n"
        text += f"Verified: {getattr(entity, 'verified', False)}\n"
        text += f"Premium: {getattr(entity, 'premium', False)}\n"
        text += f"Bot: {getattr(entity, 'bot', False)}\n"
        text += f"Scam: {getattr(entity, 'scam', False)}\n"
        text += f"Fake: {getattr(entity, 'fake', False)}\n"
        text += f"Restricted: {getattr(entity, 'restricted', False)}\n"
        text += f"Phone: {getattr(entity, 'phone', 'Not available')}\n"
        text += f"Language: {getattr(entity, 'lang_code', 'Not available')}\n"
        text += f"DC ID: {getattr(entity, 'dc_id', 'Not available')}\n"
        try:
            photo = await telethon_client.download_profile_photo(entity, file=BytesIO())
            photo.seek(0)
            await update.message.reply_photo(photo=photo, caption=text)
        except Exception:
            await update.message.reply_text(text)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def fetch_search(update, context, query):
    try:
        result = await telethon_client(functions.contacts.SearchRequest(q=query, limit=10))
        text = f"🔎 SEARCH RESULTS\n\nQuery: {query}\n"
        index = 1
        for user in result.users:
            name = f"{getattr(user, 'first_name', '')} {getattr(user, 'last_name', '')}"
            username = f"@{user.username}" if user.username else "No username"
            text += f"{index}. 👤 {name}\n   Type: User\n   Username: {username}\n   ID: {user.id}\n\n"
            index += 1
        for chat in result.chats:
            title = getattr(chat, 'title', chat.username)
            username = f"@{chat.username}" if chat.username else "No username"
            type_name = type(chat).__name__.replace("Channel", "Channel" if chat.broadcast else "Group").replace("Chat", "Group")
            text += f"{index}. 📢 {title}\n   Type: {type_name}\n   Username: {username}\n   ID: {chat.id}\n\n"
            index += 1
        if index == 1:
            await update.message.reply_text("No results found.")
        else:
            await update.message.reply_text(text)
    except Exception as e:
        await update.message.reply_text(f"❌ Search failed: {e}")

async def fetch_stats(update, context, target):
    try:
        entity = await telethon_client.get_entity(target)
        messages = await telethon_client.get_messages(entity, limit=100)
        photos = sum(1 for m in messages if m.photo)
        videos = sum(1 for m in messages if m.video)
        docs = sum(1 for m in messages if m.document)
        voice = sum(1 for m in messages if m.voice)
        audio = sum(1 for m in messages if m.audio)
        text = f"📊 STATISTICS\n\nAnalyzed Messages: {len(messages)}\nPhotos: {photos}\nVideos: {videos}\nDocs: {docs}\nVoice: {voice}\nAudio: {audio}\n"
        await update.message.reply_text(text)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def fetch_track(update, context, target):
    try:
        entity = await telethon_client.get_entity(target)
        add_track(entity.id, entity.username)
        await update.message.reply_text(f"🔔 TRACK\n\nTarget: @{entity.username}\nStatus: 🟢 Tracking")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def fetch_names(update, context, target):
    try:
        entity = await telethon_client.get_entity(target)
        text = f"🔗 NAMES\n\nCurrent Name: {entity.first_name} {getattr(entity, 'last_name', '')}\nCurrent Username: @{entity.username}\n"
        text += "\nHistorical names/usernames are not exposed via the Telegram API."
        await update.message.reply_text(text)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def fetch_groups(update, context, target):
    try:
        entity = await telethon_client.get_entity(target)
        text = f"👥 GROUPS\n\nTarget: @{entity.username}\n"
        text += "\nMutual groups are not exposed via the Telegram API for arbitrary users."
        await update.message.reply_text(text)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def fetch_messages(update, context, target):
    try:
        entity = await telethon_client.get_entity(target)
        msgs = await telethon_client.get_messages(entity, limit=10)
        text = f"💬 MESSAGES\n\nTarget: @{entity.username}\n\n"
        for i, m in enumerate(msgs, 1):
            content = m.message if m.message else "[Media]"
            text += f"{i}. {content[:50]}...\n📅 {m.date}\n\n"
        await update.message.reply_text(text if len(text) > 20 else "No messages found.")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def fetch_analysis(update, context, target):
    try:
        entity = await telethon_client.get_entity(target)
        msgs = await telethon_client.get_messages(entity, limit=100)
        photos = sum(1 for m in msgs if m.photo)
        videos = sum(1 for m in msgs if m.video)
        docs = sum(1 for m in msgs if m.document)
        links = sum(1 for m in msgs if m.message and "http" in m.message)
        text = f"🔎 ANALYSIS\n\nMessages analyzed: {len(msgs)}\nPhotos: {photos}\nVideos: {videos}\nDocs: {docs}\nLinks: {links}\n"
        await update.message.reply_text(text)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def fetch_channels(update, context, target):
    try:
        entity = await telethon_client.get_entity(target)
        if not hasattr(entity, 'broadcast') or not entity.broadcast:
            await update.message.reply_text("📢 CHANNELS\n\nTarget is not a channel.")
            return
        text = f"📢 CHANNELS\n\nTitle: {entity.title}\nUsername: @{entity.username}\nID: {entity.id}\nAbout: {getattr(entity, 'about', 'N/A')}\nVerified: {entity.verified}\nScam: {entity.scam}\nFake: {entity.fake}\n"
        await update.message.reply_text(text)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def fetch_rep(update, context, target):
    try:
        entity = await telethon_client.get_entity(target)
        msgs = await telethon_client.get_messages(entity, limit=20)
        total_messages = len(msgs)
        media_count = sum(1 for m in msgs if m.media)
        # Heuristic: messages + media*2 - fake/scam penalties
        score = (total_messages + media_count * 2) - (100 if entity.fake else 0) - (50 if entity.scam else 0)
        text = f"👍 REPUTATION\n\nBot-calculated heuristic score: {score}\n\nCalculated from:\n- Messages: {total_messages}\n- Media: {media_count}\n- Fake penalty: {entity.fake}\n- Scam penalty: {entity.scam}\n"
        await update.message.reply_text(text)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def fetch_friends(update, context, target):
    try:
        entity = await telethon_client.get_entity(target)
        text = f"👥 FRIENDS\n\nTarget: @{entity.username}\n"
        text += "\nMutual contacts are not exposed via the Telegram API for arbitrary users."
        await update.message.reply_text(text)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def fetch_reactions(update, context, target):
    try:
        entity = await telethon_client.get_entity(target)
        msgs = await telethon_client.get_messages(entity, limit=20)
        reaction_counts = {}
        for m in msgs:
            if m.reactions:
                for r in m.reactions.results:
                    if hasattr(r.reaction, 'emoticon'):
                        emoji = r.reaction.emoticon
                    else:
                        emoji = "🔷"
                    reaction_counts[emoji] = reaction_counts.get(emoji, 0) + r.count
        text = "🔄 REACTIONS\n\n"
        if not reaction_counts:
            text += "No reactions found."
        else:
            for k, v in reaction_counts.items():
                text += f"{k} {v}\n"
        await update.message.reply_text(text)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def fetch_gifts(update, context, target):
    try:
        entity = await telethon_client.get_entity(target)
        text = f"🎁 GIFTS\n\nTarget: @{entity.username}\n\n"
        text += "Telegram does not expose gift information through the available API."
        await update.message.reply_text(text)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def fetch_share(update, context, target):
    try:
        entity = await telethon_client.get_entity(target)
        link = f"https://t.me/{entity.username}"
        await update.message.reply_text(f"📤 SHARE\n\nShare this link:\n{link}")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def fetch_words(update, context, target):
    try:
        entity = await telethon_client.get_entity(target)
        msgs = await telethon_client.get_messages(entity, limit=100)
        word_count = {}
        for m in msgs:
            if m.message:
                for word in re.findall(r'\w+', m.message.lower()):
                    word_count[word] = word_count.get(word, 0) + 1
        top = sorted(word_count.items(), key=lambda x: x[1], reverse=True)[:5]
        text = "🔵 WORDS FREQUENCY\n\n"
        if not top:
            text += "No text found."
        else:
            for i, (w, c) in enumerate(top, 1):
                text += f"{i}. {w} — {c}\n"
        await update.message.reply_text(text)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def fetch_common(update, context, target):
    try:
        entity = await telethon_client.get_entity(target)
        text = f"👥 COMMON GROUPS\n\nTarget: @{entity.username}\n"
        text += "\nCommon group detection is not exposed via the Telegram API for arbitrary users."
        await update.message.reply_text(text)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

# --- MESSAGE HANDLER ---
async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text

    # Password input
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

    # Reply State
    if context.user_data.get('reply_to'):
        target_chat = context.user_data['reply_to']
        try:
            await telethon_client.send_message(target_chat, text)
            context.user_data['reply_to'] = None
            await update.message.reply_text("✅ Reply sent!")
        except Exception as e:
            await update.message.reply_text(f"❌ Failed: {e}")
        return

    # Feature input states
    state = context.user_data.get('state')
    if state:
        context.user_data['state'] = None
        if state == 'profile_query':
            await fetch_profile(update, context, text)
        elif state == 'search':
            await fetch_search(update, context, text)
        elif state == 'stats':
            await fetch_stats(update, context, text)
        elif state == 'track':
            await fetch_track(update, context, text)
        elif state == 'names':
            await fetch_names(update, context, text)
        elif state == 'groups':
            await fetch_groups(update, context, text)
        elif state == 'messages':
            await fetch_messages(update, context, text)
        elif state == 'analysis':
            await fetch_analysis(update, context, text)
        elif state == 'channels':
            await fetch_channels(update, context, text)
        elif state == 'rep':
            await fetch_rep(update, context, text)
        elif state == 'friends':
            await fetch_friends(update, context, text)
        elif state == 'reactions':
            await fetch_reactions(update, context, text)
        elif state == 'gifts':
            await fetch_gifts(update, context, text)
        elif state == 'share':
            await fetch_share(update, context, text)
        elif state == 'words':
            await fetch_words(update, context, text)
        elif state == 'common':
            await fetch_common(update, context, text)
        return

    # Existing Fetcher
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
                    if idx % 5 == 0:
                        await status_msg.edit_text(f"Fetching {idx}/{len(messages)}...")
                    await safe_send(update.message.chat_id, context.bot, msg, from_chat_id=entity.id, message_id=msg.id)
                await status_msg.edit_text(f"✅ Batch complete! Fetched {len(messages)} messages.")
        except Exception as e:
            await handle_telethon_error(update, e)
    else:
        await update.message.reply_text("👋 Please use the menu buttons, or send a Telegram link to fetch.")

# --- TELETHON LISTENER ---
@telethon_client.on(events.NewMessage(incoming=True))
async def inbox_listener(event):
    if event.is_private and not event.out:
        try:
            sender = await event.get_sender()
            add_inbox_message(
                chat_id=event.chat_id,
                sender_id=sender.id,
                name=getattr(sender, 'first_name', 'Unknown'),
                username=getattr(sender, 'username', 'N/A'),
                text=event.raw_text if event.raw_text else "",
                media_type=get_media_type(event),
                date=str(event.date)
            )
            # Check tracking
            conn = sqlite3.connect('bot_data.db')
            c = conn.cursor()
            c.execute("SELECT * FROM tracking WHERE target_id = ?", (sender.id,))
            data = c.fetchone()
            conn.close()
            if data:
                print(f"Tracked user {sender.id} sent a message.")
        except Exception as e:
            print(f"Inbox Error: {e}")

# --- MAIN EXECUTION ---
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
    await bot_app.updater.start_polling()
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=10000), daemon=True).start()
    await asyncio.Event().wait()

if __name__ == '__main__':
    asyncio.run(main())
