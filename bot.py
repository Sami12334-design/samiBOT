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
from telethon.errors import FloodWaitError, ChannelPrivateError, UsernameNotOccupiedError, MessageIdInvalidError

# --- FIX FOR STORIES IMPORT ---
try:
    from telethon.tl.functions.stories import GetStoriesRequest
except ImportError:
    GetStoriesRequest = None

# --- FLASK SETUP ---
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
    c.execute('''CREATE TABLE IF NOT EXISTS user_history (user_id INTEGER, username TEXT, first_name TEXT, last_name TEXT, date TEXT)''')
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
            await bot.send_message(chat_id, "🔒 Telegram is blocking the download of this media.")
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
    await update.message.reply_text("🔒 You have been logged out.")

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
            "search": ("🔎 SEARCH", "Enter target group/channel username followed by keyword.\nExample: @mygroup exam"),
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

    # SEARCH FILTER CALLBACKS
    elif data.startswith("filt_"):
        parts = data.split("_")
        filt_type = parts[1]
        page = int(parts[2])
        results = context.user_data.get('search_data', {}).get('results', [])
        if filt_type == "all": filtered_results = results
        elif filt_type == "photo": filtered_results = [r for r in results if r[1] == 'photo']
        elif filt_type == "doc": filtered_results = [r for r in results if r[1] == 'doc']
        elif filt_type == "contact": filtered_results = [r for r in results if r[1] == 'contact']
        elif filt_type == "gif": filtered_results = [r for r in results if r[1] == 'gif']
        elif filt_type == "video": filtered_results = [r for r in results if r[1] == 'video']
        elif filt_type == "audio": filtered_results = [r for r in results if r[1] == 'audio']
        per_page = 10
        total_pages = (len(filtered_results) + per_page - 1) // per_page
        page = max(1, min(page, total_pages))
        start = (page - 1) * per_page
        end = start + per_page
        page_results = filtered_results[start:end]
        text = f"🔎 SEARCH RESULTS\n\nPage {page}/{max(1, total_pages)}\n"
        for msg_id, media_type, link in page_results:
            text += f"• <a href='{link}'>Message {msg_id}</a> [{media_type}]\n"
        kb = []
        row = []
        for ft, label in [("all", "All"), ("photo", "📷"), ("doc", "📄"), ("contact", "👤"), ("gif", "🎞️"), ("video", "🎬"), ("audio", "🎵")]:
            row.append(InlineKeyboardButton(label, callback_data=f"filt_{ft}_{page}"))
            if len(row) == 4:
                kb.append(row)
                row = []
        if row: kb.append(row)
        nav_row = []
        if page > 1: nav_row.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"filt_{filt_type}_{page-1}"))
        if page < total_pages: nav_row.append(InlineKeyboardButton("Next ➡️", callback_data=f"filt_{filt_type}_{page+1}"))
        if nav_row: kb.append(nav_row)
        kb.append([InlineKeyboardButton("🔄 New Search", callback_data="search"), InlineKeyboardButton("⬅️ Back", callback_data="more")])
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)

    # WORDS FREQUENCY PAGINATION
    elif data.startswith("words_"):
        word_limit = context.user_data.get('word_limit', 10)
        if data == "words_less" and word_limit > 5: word_limit -= 5
        elif data == "words_more": word_limit += 5
        context.user_data['word_limit'] = word_limit
        target = context.user_data.get('word_target')
        await perform_words_analysis(update, context, target, word_limit)

    # PROFILE STORIES PAGINATION
    elif data.startswith("story_"):
        story_parts = data.split("_")
        if len(story_parts) == 3 and story_parts[1] == "next":
            context.user_data['story_index'] += 1
        elif len(story_parts) == 3 and story_parts[1] == "prev":
            context.user_data['story_index'] -= 1
        elif len(story_parts) == 2 and story_parts[1] == "start":
            context.user_data['story_index'] = 0
        await display_story(update, context)

# --- FEATURE IMPLEMENTATIONS ---
async def fetch_profile(update, context, target):
    try:
        entity = await telethon_client.get_entity(target)
        save_user_history(entity.id, entity.username, getattr(entity, 'first_name', ''), getattr(entity, 'last_name', ''))
        text = f"<blockquote><b>{entity.first_name} {getattr(entity, 'last_name', '')}</b>\n@{entity.username or 'N/A'}\n\n"
        text += f"{getattr(entity, 'about', 'No bio')}\n\n"
        text += f"ID: {entity.id}\n"
        text += f"usernames:\n| @{entity.username or 'N/A'}\n"
        text += f"first name / last name:\n| {entity.first_name or 'N/A'} {getattr(entity, 'last_name', '')}\n"
        text += f"\nVerified: {entity.verified}\nPremium: {getattr(entity, 'premium', False)}\nBot: {entity.bot}</blockquote>"
        kb = [[InlineKeyboardButton("📸 View Stories", callback_data="story_start"), InlineKeyboardButton("🔗 Names", callback_data="names")],
              [InlineKeyboardButton("⬅️ Back", callback_data="more")]]
        try:
            photo = await telethon_client.download_profile_photo(entity, file=BytesIO())
            photo.seek(0)
            await update.message.reply_photo(photo=photo, caption=text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))
        except Exception:
            await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def fetch_search(update, context, text):
    try:
        if not text.startswith("@"):
            await update.message.reply_text("⚠️ Please enter a valid group/channel username starting with @.\nExample: @mygroup exam")
            return
        parts = text.split(" ", 1)
        target = parts[0]
        query = parts[1] if len(parts) > 1 else ""
        entity = await telethon_client.get_entity(target)
        messages = await telethon_client.get_messages(entity, search=query, limit=100)
        results = []
        for m in messages:
            if m.photo: media_type = "photo"
            elif m.document: media_type = "gif" if m.gif else "doc"
            elif m.video: media_type = "video"
            elif m.audio: media_type = "audio"
            elif m.voice: media_type = "audio"
            elif m.contact: media_type = "contact"
            else: media_type = "text"
            link = f"https://t.me/{entity.username}/{m.id}"
            results.append((m.id, media_type, link))
        context.user_data['search_data'] = {'results': results}
        context.user_data['state'] = None
        filtered_results = results
        text = "🔎 SEARCH RESULTS\n\nPage 1/1\n"
        for msg_id, media_type, link in filtered_results[:10]:
            text += f"• <a href='{link}'>Message {msg_id}</a> [{media_type}]\n"
        kb = []
        row = []
        for ft, label in [("all", "All"), ("photo", "📷"), ("doc", "📄"), ("contact", "👤"), ("gif", "🎞️"), ("video", "🎬"), ("audio", "🎵")]:
            row.append(InlineKeyboardButton(label, callback_data=f"filt_{ft}_1"))
            if len(row) == 4:
                kb.append(row)
                row = []
        if row: kb.append(row)
        kb.append([InlineKeyboardButton("🔄 New Search", callback_data="search"), InlineKeyboardButton("⬅️ Back", callback_data="more")])
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

# --- DYNAMIC GROUPS & CHANNELS LOGIC ---
async def fetch_groups(update, context, target):
    try:
        dialogs = await telethon_client.get_dialogs(limit=None)
        groups = []
        no_msg_groups = []
        for dialog in dialogs:
            if dialog.is_group:
                entity = dialog.entity
                status = ""
                if hasattr(entity, 'admin_rights') and entity.admin_rights:
                    if entity.admin_rights.is_creator:
                        status = "👑creator"
                    else:
                        status = "👑admin"
                elif hasattr(entity, 'left') and entity.left:
                    status = "✖left"
                elif hasattr(entity, 'is_private') and entity.is_private:
                    status = "🔒private"
                
                last_msg = dialog.message.message[:30] if dialog.message and dialog.message.message else "No text"
                if dialog.message:
                    groups.append(f"{str(dialog.message.date)[:5]} {last_msg} • {entity.title} (1)")
                else:
                    no_msg_groups.append(entity.title)
        
        if not groups and not no_msg_groups:
            await update.message.reply_text("No groups found for this account.")
            return
            
        text = f"<blockquote>Known groups of account <b>{telethon_client.get_me().id}</b>\n👑admin, 🔒private, ✖left\nLast msg - group (total messages)\n\n"
        for g in groups[:10]:
            text += f"|{g}\n"
        if no_msg_groups:
            text += "\nWithout messages:\n"
            for g in no_msg_groups[:10]:
                text += f"| {g}\n"
        text += "</blockquote>"
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def fetch_channels(update, context, target):
    try:
        dialogs = await telethon_client.get_dialogs(limit=None)
        channels = []
        for dialog in dialogs:
            if dialog.is_channel and not dialog.is_group:
                entity = dialog.entity
                last_msg = dialog.message.message[:30] if dialog.message and dialog.message.message else "No text"
                channels.append(f"{str(dialog.message.date)[:5]} {last_msg} • {entity.title} (1)")
        
        if not channels:
            await update.message.reply_text("No channels found for this account.")
            return
            
        text = f"<blockquote>Known channels of account <b>{telethon_client.get_me().id}</b>\nLast msg - channel (total messages)\n\n"
        for c in channels[:10]:
            text += f"|{c}\n"
        text += "</blockquote>"
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

# --- WORDS FREQUENCY (LEGEND STYLE) ---
async def fetch_words(update, context, target):
    context.user_data['word_target'] = target
    await perform_words_analysis(update, context, target, 10)

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
                        if word not in word_data:
                            word_data[word] = {'count': 0, 'messages': set()}
                        word_data[word]['count'] += 1
                        word_data[word]['messages'].add(m.id)
        sorted_words = sorted(word_data.items(), key=lambda x: x[1]['count'], reverse=True)[:limit]
        text = f"<blockquote>RIGID M (@{entity.username}) often uses this words:\n"
        for word, data in sorted_words:
            text += f"|{len(data['messages'])} - {data['count']} {word}\n"
        text += "</blockquote>"
        kb = [[InlineKeyboardButton("⬅️ Less Words", callback_data="words_less"), InlineKeyboardButton("More Words ➡️", callback_data="words_more")],
              [InlineKeyboardButton("🔄 Refresh", callback_data="words_refresh"), InlineKeyboardButton("⬅️ Back", callback_data="more")]]
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

# --- FRIENDS (LEGEND STYLE) ---
async def fetch_friends(update, context, text):
    parts = text.split(" ")
    if len(parts) < 2:
        await update.message.reply_text("⚠️ Please use the exact format:\n@groupusername @targetuser")
        return
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
                        except:
                            reply_data[sender_id]['name'] = "Unknown"
                    reply_data[sender_id]['count'] += 1
            except:
                pass
    sorted_replies = sorted(reply_data.items(), key=lambda x: x[1]['count'], reverse=True)[:10]
    text = f"<blockquote>Replies them in the groups:\nwhen - to whom (total times)\n"
    for sid, data in sorted_replies:
        date_str = data['date'][:5]
        text += f"|{date_str} - {data['name']} ({data['count']})\n"
    text += "</blockquote>"
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

# --- NAMES HISTORY (LEGEND STYLE) ---
async def fetch_names(update, context, target):
    try:
        entity = await telethon_client.get_entity(target)
        save_user_history(entity.id, entity.username, getattr(entity, 'first_name', ''), getattr(entity, 'last_name', ''))
        history = get_user_history(entity.id)
        text = f"<blockquote>Names history {entity.first_name} (@{entity.username}):\n\nusernames:\n"
        if history:
            seen_usernames = set()
            for h in history:
                if h[0] and h[0] not in seen_usernames:
                    text += f"1. @{h[0]} [{h[3][:10]}]\n"
                    seen_usernames.add(h[0])
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

# --- PROFILE STORIES PAGINATION (FIXED VERSION) ---
async def display_story(update, context):
    query = update.callback_query
    entity = context.user_data.get('story_entity')
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
        if story.media and hasattr(story.media, 'photo'):
            media_file = await telethon_client.download_media(story, file=BytesIO())
            media_file.seek(0)
            await query.edit_message_media(media=media_file, caption=f"Story {index+1}/{len(stories)}", reply_markup=InlineKeyboardMarkup(kb))
        elif story.media and hasattr(story.media, 'document'):
            media_file = await telethon_client.download_media(story, file=BytesIO())
            media_file.seek(0)
            await query.edit_message_media(media=media_file, caption=f"Story {index+1}/{len(stories)}", reply_markup=InlineKeyboardMarkup(kb))
        else:
            await query.edit_message_text(f"Story {index+1}/{len(stories)}\nUnsupported media.", reply_markup=InlineKeyboardMarkup(kb))
    except Exception as e:
        await query.edit_message_text(f"Story {index+1}/{len(stories)}\nCannot download media: {e}", reply_markup=InlineKeyboardMarkup(kb))

async def fetch_stories(update, context, target):
    if GetStoriesRequest is None:
        await update.message.reply_text("❌ Stories API is not supported in your current Telethon version. Please ignore this button.")
        return
    entity = await telethon_client.get_entity(target)
    try:
        stories = await telethon_client(GetStoriesRequest(entity))
        context.user_data['story_entity'] = entity
        context.user_data['stories_list'] = stories.stories
        context.user_data['story_index'] = 0
        await display_story(update, context)
    except Exception as e:
        await update.message.reply_text(f"❌ Could not fetch stories: {e}")

# --- MESSAGE HANDLER ---
async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
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
        elif state == 'search': await fetch_search(update, context, text)
        elif state == 'words': await fetch_words(update, context, text)
        elif state == 'friends': await fetch_friends(update, context, text)
        elif state == 'names': await fetch_names(update, context, text)
        elif state == 'groups': await fetch_groups(update, context, text)
        elif state == 'channels': await fetch_channels(update, context, text)
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
