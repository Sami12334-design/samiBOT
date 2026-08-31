import re
import asyncio
import os
import threading
import sqlite3
import tempfile
from io import BytesIO
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, InputMediaVideo, InputFile
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler, ContextTypes
from telethon import TelegramClient, events, functions, types
from telethon.sessions import StringSession
from telethon.errors import (FloodWaitError, ChannelPrivateError, UsernameNotOccupiedError, MessageIdInvalidError, RPCError, SessionPasswordNeededError, PeerIdInvalidError)
import fitz  # PyMuPDF

try:
    from telethon.tl.functions.stories import GetPeerStoriesRequest, GetStoriesByIDRequest
except ImportError:
    GetPeerStoriesRequest = None
    GetStoriesByIDRequest = None

app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is alive!"

BOT_TOKEN = os.environ.get('BOT_TOKEN', '')
API_ID = int(os.environ.get('API_ID', 0))
API_HASH = os.environ.get('API_HASH', '')
STRING_SESSION = os.environ.get('STRING_SESSION', '')
BOT_PASSWORD = os.environ.get("BOT_PASSWORD", "ptss25")

telethon_client = TelegramClient(StringSession(STRING_SESSION), API_ID, API_HASH)

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

def parse_tg_link(text):
    pattern = r'https?://t\.me/([a-zA-Z0-9_]+)/(\d+)'
    match = re.search(pattern, text)
    if match: return match.group(1), int(match.group(2))
    channel_pattern = r'https?://t\.me/([a-zA-Z0-9_]+)'
    match = re.search(channel_pattern, text)
    if match: return match.group(1), None
    return None, None

def get_media_type(event):
    if event.photo: return "📷"
    elif event.video: return "🎬"
    elif event.document: return "🎞️" if event.gif else "📄"
    elif event.audio: return "🎵"
    elif event.voice: return "🎤"
    elif event.sticker: return "🧩"
    else: return "💬"

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
        kb = [[InlineKeyboardButton("↩️ Reply", callback_data=f"reply_{chat_id}"), InlineKeyboardButton("👤 Profile", callback_data="profile")], [InlineKeyboardButton("🔄 Refresh", callback_data=f"refresh_{chat_id}"), InlineKeyboardButton("⬅️ Back", callback_data="inbox")]]
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
        kb = [[InlineKeyboardButton("↩️ Reply", callback_data=f"reply_{chat_id}"), InlineKeyboardButton("👤 Profile", callback_data="profile")], [InlineKeyboardButton("🔄 Refresh", callback_data=f"refresh_{chat_id}"), InlineKeyboardButton("⬅️ Back", callback_data="inbox")]]
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
            [InlineKeyboardButton("📄 PDF Fetch", callback_data="pdf_fetch")],
            [InlineKeyboardButton("⬅️ Back", callback_data="main_menu")]
        ]
        await query.edit_message_text("➕ MORE COMMANDS", reply_markup=InlineKeyboardMarkup(kb))

    elif data == "pdf_fetch":
        await query.edit_message_text("📄 PDF FETCH\n\nPlease upload the PDF file directly to this chat.")
        context.user_data['state'] = 'awaiting_pdf'

    elif data == "search":
        await query.edit_message_text("🔎 SEARCH\n\nEnter any keyword to search across your chats:\nExample: Logic mid")
        context.user_data['state'] = 'search_query'

    elif data == "search_next":
        page = context.user_data.get('search_page', 1) + 1
        await display_search_page(update, context, page)

    elif data == "search_prev":
        page = context.user_data.get('search_page', 1) - 1
        if page < 1: page = 1
        await display_search_page(update, context, page)

    elif data in ["stats", "track", "names", "groups", "messages", "analysis", "channels", "rep", "friends", "reactions", "gifts", "share", "words", "common"]:
        prompts = {
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

    elif data.startswith("posts_"):
        page = int(data.split("_")[1])
        await handle_posts_pagination(update, context, page)
    elif data.startswith("story_"):
        # story_start must FETCH stories first. Previously it jumped directly
        # to display_story(), which could have an empty stories_list.
        if data == "story_start":
            entity = context.user_data.get("story_entity") or context.user_data.get("profile_entity")
            if not entity:
                await query.edit_message_text("❌ No profile selected.")
                return
            await query.answer("Fetching Stories…")
            context.user_data["story_index"] = 0
            context.user_data.pop("story_message_id", None)
            context.user_data["stories_list"] = []
            fake_message = query.message
            # Reuse fetch logic with the callback's message context.
            try:
                if not GetPeerStoriesRequest:
                    await query.edit_message_text(
                        "❌ Telethon does not support stories.getPeerStories in this installation."
                    )
                    return

                response = await telethon_client(
                    GetPeerStoriesRequest(peer=entity)
                )
                stories = list(getattr(response, "stories", []) or [])

                # Expand skipped StoryItems using getStoriesByID.
                if GetStoriesByIDRequest:
                    full_items = []
                    skipped_ids = []
                    for story in stories:
                        if getattr(story, "media", None):
                            full_items.append(story)
                        elif getattr(story, "id", None):
                            skipped_ids.append(story.id)

                    if skipped_ids:
                        try:
                            full = await telethon_client(
                                GetStoriesByIDRequest(peer=entity, id=skipped_ids)
                            )
                            full_items.extend(
                                s for s in (getattr(full, "stories", []) or [])
                                if getattr(s, "media", None)
                            )
                        except Exception:
                            pass
                    stories = full_items

                stories = [
                    s for s in stories
                    if getattr(s, "media", None) and getattr(s, "id", None) is not None
                ]

                if not stories:
                    await query.edit_message_text(
                        "❌ No active downloadable Stories are available for this account."
                    )
                    return

                context.user_data["stories_list"] = stories
                context.user_data["story_entity"] = entity
                context.user_data["story_index"] = 0

                await display_story(update, context)

            except FloodWaitError as e:
                await query.edit_message_text(
                    f"⏳ Telegram rate limit. Please wait {e.seconds} seconds."
                )
            except Exception as e:
                await query.edit_message_text(
                    f"❌ Could not retrieve Stories: {type(e).__name__}: {e}"
                )

        elif data == "story_next":
            context.user_data["story_index"] = context.user_data.get("story_index", 0) + 1
            await display_story(update, context)

        elif data == "story_prev":
            context.user_data["story_index"] = context.user_data.get("story_index", 0) - 1
            await display_story(update, context)

# --- SEARCH WITHOUT the forbidden global-message request ---
# Telegram does not expose an exact "global message search across arbitrary public
# channels" through messages.search without using messages.searchGlobal. Since
# the forbidden global-message request is explicitly forbidden here, this implementation combines:
#   1) messages in dialogs the account already has access to
#   2) public users/channels/groups discovered with contacts.search
#      (these can include public chats the account has NOT joined)
#
# This is intentionally honest: it cannot reproduce Telegram's exact global-search
# index, but it can search accessible dialogs plus discoverable public chats/channels.

def _peer_key(entity):
    return getattr(entity, "id", None), getattr(entity, "access_hash", None)

def _result_link(entity, msg_id):
    username = getattr(entity, "username", None)
    if username:
        return f"https://t.me/{username}/{msg_id}"
    # For private/supergroup IDs Telegram's /c/ form is only useful when the
    # message is actually addressable. Keep it as a best-effort link.
    entity_id = getattr(entity, "id", None)
    if entity_id:
        return f"https://t.me/c/{entity_id}/{msg_id}"
    return ""

def _result_text(msg):
    text = (getattr(msg, "message", None) or "").strip()
    if text:
        return re.sub(r"\s+", " ", text)[:180]
    return f"{get_media_type(msg)} Media message"

def _relevance_score(query, msg, entity):
    q_words = [w.lower() for w in re.findall(r"\w+", query) if len(w) > 1]
    haystack = " ".join([
        getattr(msg, "message", "") or "",
        getattr(entity, "title", "") or "",
        getattr(entity, "username", "") or "",
        getattr(entity, "first_name", "") or "",
        getattr(entity, "last_name", "") or "",
    ]).lower()

    text = (getattr(msg, "message", "") or "").lower()
    score = 0
    for word in q_words:
        if word in text:
            score += 10
        if word in haystack:
            score += 2
    if q_words and all(word in text for word in q_words):
        score += 20
    # Newer messages get a small bonus without overwhelming text relevance.
    try:
        score += max(0, int(msg.date.timestamp() / 10_000_000))
    except Exception:
        pass
    return score

async def _search_one_entity(entity, query, limit=10):
    """Search messages in one specific accessible peer."""
    try:
        messages = await telethon_client.get_messages(
            entity,
            search=query,
            limit=limit
        )
        results = []
        for msg in messages or []:
            if not getattr(msg, "id", None):
                continue
            results.append({
                "entity": entity,
                "message": msg,
                "link": _result_link(entity, msg.id),
                "content": _result_text(msg),
                "score": _relevance_score(query, msg, entity),
            })
        return results
    except (ChannelPrivateError, PeerIdInvalidError, UsernameNotOccupiedError):
        return []
    except FloodWaitError:
        raise
    except Exception:
        return []

async def _discover_public_peers(query, limit=100):
    """
    Discover public Telegram users/chats/channels by name/username using
    contacts.search. This can return public peers that are not in dialogs,
    so the search is not limited to joined chats.
    """
    discovered = []
    try:
        response = await telethon_client(functions.contacts.SearchRequest(
            q=query,
            limit=limit
        ))
        for entity in list(getattr(response, "chats", []) or []) + list(getattr(response, "users", []) or []):
            username = getattr(entity, "username", None)
            # Only use public username-addressable peers here. This avoids
            # pretending that arbitrary private/unjoined chats are searchable.
            if username:
                discovered.append(entity)
    except FloodWaitError:
        raise
    except Exception:
        pass
    return discovered

async def fetch_search(update, context, query):
    query = (query or "").strip()
    if not query:
        await update.message.reply_text("🔎 Please enter a keyword, for example: Logic mid")
        return

    status_msg = await update.message.reply_text(
        f"🔎 Searching accessible chats and discoverable public Telegram chats for: {query}"
    )

    try:
        # A larger dialog window than the old 30-dialog limit.
        dialogs = await telethon_client.get_dialogs(limit=200)

        # Start with the user's accessible dialogs.
        peers = []
        seen = set()

        for dialog in dialogs:
            entity = getattr(dialog, "entity", None)
            if not entity:
                continue
            key = _peer_key(entity)
            if key[0] not in seen:
                seen.add(key[0])
                peers.append(entity)

        # IMPORTANT: discover public chats/channels outside the user's joined
        # dialogs. This is the non-the forbidden global-message request technique.
        public_peers = await _discover_public_peers(query, limit=100)
        for entity in public_peers:
            key = _peer_key(entity)
            if key[0] not in seen:
                seen.add(key[0])
                peers.append(entity)

        all_results = []
        sem = asyncio.Semaphore(8)

        async def worker(entity):
            async with sem:
                return await _search_one_entity(entity, query, limit=10)

        # Search peers concurrently, but cap concurrency.
        batches = await asyncio.gather(
            *(worker(entity) for entity in peers),
            return_exceptions=True
        )

        for batch in batches:
            if isinstance(batch, FloodWaitError):
                raise batch
            if isinstance(batch, list):
                all_results.extend(batch)

        # Remove duplicate message results.
        unique = {}
        for item in all_results:
            entity = item["entity"]
            msg = item["message"]
            key = (getattr(entity, "id", None), getattr(msg, "id", None))
            unique[key] = item

        all_results = list(unique.values())
        all_results.sort(key=lambda x: x["score"], reverse=True)

        if not all_results:
            await status_msg.edit_text(
                f"❌ No results found for '{query}'.\n\n"
                "Tip: Telegram's exact global message index is not used because "
                "the forbidden global-message request is disabled. Public chats/channels that "
                "can be discovered by Telegram are searched individually."
            )
            return

        # Store the full result objects in the user's session.
        context.user_data["search_results"] = all_results
        context.user_data["search_query"] = query
        context.user_data["search_page"] = 1

        await status_msg.delete()
        await display_search_page(update, context, 1)

    except FloodWaitError as e:
        await status_msg.edit_text(
            f"⏳ Telegram rate limit. Please wait {e.seconds} seconds and try again."
        )
    except Exception as e:
        await status_msg.edit_text(f"❌ Search failed: {type(e).__name__}: {e}")

async def display_search_page(update, context, page):
    query = update.callback_query
    if query:
        await query.answer()

    results = context.user_data.get("search_results", [])
    search_query = context.user_data.get("search_query", "")

    if not results:
        text = f"<blockquote><b>Telegram Search</b>\n{search_query}\n\nNo results.</blockquote>"
        kb = [[
            InlineKeyboardButton("🔄 New Search", callback_data="search"),
            InlineKeyboardButton("⬅️ Back", callback_data="more")
        ]]
        if query:
            await query.edit_message_text(
                text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML
            )
        else:
            await update.message.reply_text(
                text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML
            )
        return

    per_page = 10
    total_pages = max(1, (len(results) + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    context.user_data["search_page"] = page

    start = (page - 1) * per_page
    page_items = results[start:start + per_page]

    text = f"<blockquote><b>Telegram Search</b>\n{search_query}\n\n"

    for item in page_items:
        entity = item["entity"]
        msg = item["message"]
        title = (
            getattr(entity, "title", None)
            or getattr(entity, "first_name", None)
            or getattr(entity, "username", None)
            or "Telegram"
        )
        username = getattr(entity, "username", None)
        peer_label = f"@{username}" if username else title
        link = item["link"]

        if link:
            text += (
                f"🔗 <a href='{link}'>{item['content']}</a>\n"
                f"   📌 {peer_label}\n\n"
            )
        else:
            text += f"🔗 {item['content']}\n   📌 {peer_label}\n\n"

    text += f"Page {page}/{total_pages}\n"
    text += "Sort by relevance and activity</blockquote>"

    kb = []
    nav_row = []
    if page > 1:
        nav_row.append(InlineKeyboardButton("⬅️ Previous", callback_data="search_prev"))
    if page < total_pages:
        nav_row.append(InlineKeyboardButton("Next ➡️", callback_data="search_next"))
    if nav_row:
        kb.append(nav_row)

    kb.append([
        InlineKeyboardButton("🔄 New Search", callback_data="search"),
        InlineKeyboardButton("⬅️ Back", callback_data="more")
    ])

    markup = InlineKeyboardMarkup(kb)

    if query:
        await query.edit_message_text(
            text,
            reply_markup=markup,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True
        )
    else:
        await update.message.reply_text(
            text,
            reply_markup=markup,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True
        )

# --- PDF FETCH ---
async def handle_pdf_upload(update, context):
    if context.user_data.get('state') != 'awaiting_pdf': return
    if not update.message.document:
        await update.message.reply_text("❌ Please upload a valid PDF document.")
        return
    if update.message.document.mime_type != "application/pdf":
        await update.message.reply_text("❌ The file you uploaded is not a PDF.")
        return

    status_msg = await update.message.reply_text("⏳ Processing PDF...")
    try:
        file = await context.bot.get_file(update.message.document.file_id)
        pdf_bytes = BytesIO()
        await file.download_to_memory(pdf_bytes)
        pdf_bytes.seek(0)

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        total_pages = len(doc)
        text_pages = []
        image_pages = []
        
        for page_num in range(total_pages):
            page = doc.load_page(page_num)
            page_text = page.get_text("text")
            if page_text.strip(): text_pages.append(page_text)
            images = page.get_images(full=True)
            for img in images:
                base_image = doc.extract_image(img[0])
                img_bytes = BytesIO(base_image["image"])
                img_bytes.seek(0)
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
        await status_msg.edit_text(f"❌ Error: {e}")
        context.user_data['state'] = None

# --- PROFILE, POSTS, STORIES ---
async def fetch_profile(update, context, target):
    try:
        entity = await telethon_client.get_entity(target)

        # Keep the exact entity for the View Story callback.
        context.user_data["profile_entity"] = entity
        context.user_data["post_entity"] = entity
        context.user_data["story_entity"] = entity

        save_user_history(
            entity.id,
            getattr(entity, "username", None),
            getattr(entity, "first_name", ""),
            getattr(entity, "last_name", "")
        )

        first_name = getattr(entity, "first_name", "") or ""
        last_name = getattr(entity, "last_name", "") or ""
        display_name = f"{first_name} {last_name}".strip() or getattr(entity, "title", "Unknown")

        text = (
            f"<blockquote><b>{display_name}</b>\n"
            f"@{getattr(entity, 'username', None) or 'N/A'}\n\n"
            f"{getattr(entity, 'about', 'No bio')}\n\n"
            f"ID: {entity.id}\n"
            f"Verified: {getattr(entity, 'verified', False)}\n"
            f"Premium: {getattr(entity, 'premium', False)}\n"
            f"Bot: {getattr(entity, 'bot', False)}</blockquote>"
        )

        kb = [[
            InlineKeyboardButton("📰 View Posts", callback_data="posts_1"),
            InlineKeyboardButton("👁 View Story", callback_data="story_start")
        ], [
            InlineKeyboardButton("⬅️ Back", callback_data="more")
        ]]

        try:
            photo = await telethon_client.download_profile_photo(entity, file=BytesIO())
            if photo:
                photo.seek(0)
                await update.message.reply_photo(
                    photo=photo,
                    caption=text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup(kb)
                )
            else:
                await update.message.reply_text(
                    text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup(kb)
                )
        except Exception:
            await update.message.reply_text(
                text,
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(kb)
            )

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
    if page > 1:
        kb.append([InlineKeyboardButton("⬅️ Previous", callback_data=f"posts_{page-1}")])
    if end < total_posts:
        kb.append([InlineKeyboardButton("Next ➡️", callback_data=f"posts_{page+1}")])
    kb.append([InlineKeyboardButton("⬅️ Back to Profile", callback_data="profile")])

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(kb)
    )

def _story_media_kind(media):
    if not media:
        return None
    if getattr(media, "photo", None):
        return "photo"
    if getattr(media, "document", None):
        document = media.document
        # Check Telegram document attributes to distinguish video.
        for attr in getattr(document, "attributes", []) or []:
            if hasattr(attr, "supports_streaming") or hasattr(attr, "duration"):
                return "video"
            if attr.__class__.__name__.lower().endswith("video"):
                return "video"
        mime = (getattr(document, "mime_type", "") or "").lower()
        if mime.startswith("video/"):
            return "video"
        return "document"
    return None

async def _refresh_story(entity, story):
    """
    Refresh a StoryItem through stories.getStoriesByID when possible.
    This is important because Story media may need a fresh file reference.
    """
    if not GetStoriesByIDRequest:
        return story

    try:
        response = await telethon_client(GetStoriesByIDRequest(
            peer=entity,
            id=[story.id]
        ))
        refreshed = getattr(response, "stories", None) or []
        if refreshed:
            candidate = refreshed[0]
            # Ignore deleted/skipped constructors if they don't contain media.
            if getattr(candidate, "media", None):
                return candidate
    except FloodWaitError:
        raise
    except Exception:
        pass

    return story

async def _download_story_media(entity, story):
    """
    Fetch a full StoryItem and download its actual MessageMedia.
    We first refresh with getStoriesByID so the media has a current file reference.
    """
    story = await _refresh_story(entity, story)

    media = getattr(story, "media", None)
    if not media:
        return None, None, story

    kind = _story_media_kind(media)
    if kind not in ("photo", "video"):
        return None, None, story

    suffix = ".jpg" if kind == "photo" else ".mp4"

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp_path = tmp.name
    tmp.close()

    try:
        # Telethon's downloader accepts the MessageMedia contained in StoryItem.
        downloaded = await telethon_client.download_media(
            media,
            file=tmp_path
        )

        if not downloaded or not os.path.exists(tmp_path) or os.path.getsize(tmp_path) == 0:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            return None, None, story

        return tmp_path, kind, story

    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        return None, None, story

async def _send_or_edit_story_message(query, story, index, total, entity):
    """
    On story_start, send a new media message because the profile message may be
    text/photo and cannot always be edited into media. On next/previous, edit
    the existing story media message.
    """
    tmp_path, kind, story = await _download_story_media(entity, story)

    if not tmp_path:
        return False, story

    caption = getattr(story, "caption", None) or getattr(story, "message", None) or ""
    caption = f"{caption}\n\n📖 Story {index + 1}/{total}".strip()

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("⬅️ Previous", callback_data="story_prev"),
        InlineKeyboardButton("Next ➡️", callback_data="story_next")
    ], [
        InlineKeyboardButton("⬅️ Back to Profile", callback_data="profile")
    ]])

    try:
        with open(tmp_path, "rb") as f:
            if kind == "photo":
                media = InputMediaPhoto(media=InputFile(f), caption=caption)
            else:
                media = InputMediaVideo(media=InputFile(f), caption=caption)

            # story_message_id is present after the first Story has been sent.
            story_message_id = context_user_message_id = None
            # Caller decides whether this is a new message or edit.
            return media, kb, story

    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

async def display_story(update, context):
    query = update.callback_query
    await query.answer()

    entity = context.user_data.get("story_entity") or context.user_data.get("profile_entity")
    stories = context.user_data.get("stories_list", [])
    index = int(context.user_data.get("story_index", 0))

    if not entity:
        await query.edit_message_text("❌ No Telegram user is selected.")
        return

    if not stories:
        await query.edit_message_text("❌ No accessible active Stories were found.")
        return

    index = max(0, min(index, len(stories) - 1))
    context.user_data["story_index"] = index

    story = stories[index]

    try:
        tmp_path, kind, story = await _download_story_media(entity, story)

        if not tmp_path or not kind:
            await query.edit_message_text(
                "❌ Telegram returned the Story, but its media could not be downloaded.\n\n"
                "The Story may be protected, expired, skipped, or its file reference "
                "may no longer be valid."
            )
            return

        caption = getattr(story, "caption", None) or ""
        caption = f"{caption}\n\n📖 Story {index + 1}/{len(stories)}".strip()

        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("⬅️ Previous", callback_data="story_prev"),
            InlineKeyboardButton("Next ➡️", callback_data="story_next")
        ], [
            InlineKeyboardButton("⬅️ Back to Profile", callback_data="profile")
        ]])

        # If the callback is on an existing media message, edit it.
        # If it is on the profile text/photo message, send a new media message.
        is_story_message = context.user_data.get("story_message_id") == query.message.message_id

        with open(tmp_path, "rb") as f:
            if is_story_message:
                if kind == "photo":
                    media = InputMediaPhoto(
                        media=InputFile(f),
                        caption=caption
                    )
                else:
                    media = InputMediaVideo(
                        media=InputFile(f),
                        caption=caption
                    )
                await query.edit_message_media(
                    media=media,
                    reply_markup=kb
                )
            else:
                if kind == "photo":
                    sent = await query.message.reply_photo(
                        photo=InputFile(f),
                        caption=caption,
                        reply_markup=kb
                    )
                else:
                    sent = await query.message.reply_video(
                        video=InputFile(f),
                        caption=caption,
                        reply_markup=kb
                    )
                context.user_data["story_message_id"] = sent.message_id

        # Keep the refreshed story object so subsequent navigation uses the
        # current file reference.
        stories[index] = story
        context.user_data["stories_list"] = stories

        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    except FloodWaitError as e:
        await query.edit_message_text(
            f"⏳ Telegram rate limit. Please wait {e.seconds} seconds."
        )
    except Exception as e:
        try:
            if "tmp_path" in locals() and tmp_path:
                os.unlink(tmp_path)
        except Exception:
            pass
        await query.edit_message_text(
            f"❌ Could not fetch Story: {type(e).__name__}: {e}"
        )

async def fetch_stories(update, context, target):
    """
    Fetch active Stories for a specific peer using the official
    stories.getPeerStories method.
    """
    try:
        entity = await telethon_client.get_entity(target)
    except Exception as e:
        await update.message.reply_text(
            f"❌ Could not resolve user: {type(e).__name__}: {e}"
        )
        return

    if not GetPeerStoriesRequest:
        await update.message.reply_text(
            "❌ Your installed Telethon version does not expose "
            "stories.getPeerStories. Upgrade Telethon first."
        )
        return

    try:
        response = await telethon_client(
            GetPeerStoriesRequest(peer=entity)
        )

        stories = list(getattr(response, "stories", []) or [])

        # StoryItemSkipped entries contain no media. Refresh those individually.
        if GetStoriesByIDRequest:
            refreshed_stories = []
            skipped_ids = []

            for story in stories:
                if getattr(story, "media", None):
                    refreshed_stories.append(story)
                elif getattr(story, "id", None):
                    skipped_ids.append(story.id)

            if skipped_ids:
                try:
                    full = await telethon_client(
                        GetStoriesByIDRequest(
                            peer=entity,
                            id=skipped_ids
                        )
                    )
                    full_items = list(getattr(full, "stories", []) or [])
                    full_by_id = {
                        getattr(s, "id", None): s
                        for s in full_items
                        if getattr(s, "id", None) is not None
                    }
                    for sid in skipped_ids:
                        if sid in full_by_id:
                            refreshed_stories.append(full_by_id[sid])
                except FloodWaitError:
                    raise
                except Exception:
                    pass

            stories = refreshed_stories

        stories = [
            s for s in stories
            if getattr(s, "media", None)
            and getattr(s, "id", None) is not None
        ]

        if not stories:
            await update.message.reply_text(
                "❌ No downloadable active Stories are available for this account."
            )
            return

        context.user_data["story_entity"] = entity
        context.user_data["profile_entity"] = entity
        context.user_data["stories_list"] = stories
        context.user_data["story_index"] = 0
        context.user_data.pop("story_message_id", None)

        # Send the first story as a fresh message.
        await _send_first_story(update, context)

    except FloodWaitError as e:
        await update.message.reply_text(
            f"⏳ Telegram rate limit. Please wait {e.seconds} seconds."
        )
    except ChannelPrivateError:
        await update.message.reply_text(
            "🔒 This Story is not accessible to the authenticated Telegram account."
        )
    except Exception as e:
        await update.message.reply_text(
            f"❌ Could not retrieve Stories: {type(e).__name__}: {e}"
        )

async def _send_first_story(update, context):
    """Send first Story from a normal message context."""
    entity = context.user_data.get("story_entity")
    stories = context.user_data.get("stories_list", [])
    index = 0

    if not entity or not stories:
        await update.message.reply_text("❌ No Stories available.")
        return

    tmp_path, kind, story = await _download_story_media(entity, stories[index])

    if not tmp_path or not kind:
        await update.message.reply_text(
            "❌ Telegram returned the Story, but its media could not be downloaded."
        )
        return

    caption = getattr(story, "caption", None) or ""
    caption = f"{caption}\n\n📖 Story 1/{len(stories)}".strip()

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("⬅️ Previous", callback_data="story_prev"),
        InlineKeyboardButton("Next ➡️", callback_data="story_next")
    ], [
        InlineKeyboardButton("⬅️ Back to Profile", callback_data="profile")
    ]])

    try:
        with open(tmp_path, "rb") as f:
            if kind == "photo":
                sent = await update.message.reply_photo(
                    photo=InputFile(f),
                    caption=caption,
                    reply_markup=kb
                )
            else:
                sent = await update.message.reply_video(
                    video=InputFile(f),
                    caption=caption,
                    reply_markup=kb
                )

        context.user_data["story_message_id"] = sent.message_id
        stories[index] = story
        context.user_data["stories_list"] = stories

    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

# --- OTHER FEATURES ---
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
                        if word not in word_data: word_data[word] = {'count': 0, 'messages': set()}
                        word_data[word]['count'] += 1
                        word_data[word]['messages'].add(m.id)
        sorted_words = sorted(word_data.items(), key=lambda x: x[1]['count'], reverse=True)[:limit]
        text = f"<blockquote>RIGID M (@{entity.username}) often uses this words:\n"
        for word, data in sorted_words: text += f"|{len(data['messages'])} - {data['count']} {word}\n"
        text += "</blockquote>"
        kb = [[InlineKeyboardButton("⬅️ Less Words", callback_data="words_less"), InlineKeyboardButton("More Words ➡️", callback_data="words_more")], [InlineKeyboardButton("⬅️ Back", callback_data="more")]]
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def fetch_words(update, context, target):
    context.user_data['word_target'] = target
    await perform_words_analysis(update, context, target, 10)

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
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

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
                    text += f"1. @{h[0]} [{h[3][:10]}]\n"
                    seen.add(h[0])
        else: text += "No history yet.\n"
        text += "\nfirst name / last name:\n"
        if history:
            for h in history[:5]: text += f"|{h[3][:10]} -> {h[1]} {h[2]}\n"
        else: text += "No history yet.\n"
        text += "</blockquote>"
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

# --- MESSAGE HANDLER ---
async def handle_link(update, context):
    user_id = update.effective_user.id
    text = update.message.text if update.message.text else ""
    if context.user_data.get('state') == 'awaiting_pdf':
        await handle_pdf_upload(update, context)
        return
    if context.user_data.get('state') == 'awaiting_password':
        if text == BOT_PASSWORD:
            add_authenticated_user(user_id)
            context.user_data['state'] = None
            await update.message.reply_text("✅ Access granted!")
            keyboard = [[InlineKeyboardButton("📥 Inbox", callback_data="inbox"), InlineKeyboardButton("👤 Profile", callback_data="profile")], [InlineKeyboardButton("🔗 Fetch Telegram", callback_data="fetch")], [InlineKeyboardButton("➕ More Commands", callback_data="more")]]
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
        elif state == 'search_query': await fetch_search(update, context, text)
        elif state == 'words': await fetch_words(update, context, text)
        elif state == 'friends': await fetch_friends(update, context, text)
        elif state == 'names': await fetch_names(update, context, text)
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
                    if idx % 5 == 0: await status_msg.edit_text(f"Fetching {idx}/{len(messages)}...")
                    await safe_send(update.message.chat_id, context.bot, msg, from_chat_id=entity.id, message_id=msg.id)
                await status_msg.edit_text(f"✅ Batch complete!")
        except Exception as e:
            await handle_telethon_error(update, e)
    else:
        await update.message.reply_text("👋 Use the menu buttons, or send a Telegram link.")

# --- INBOX LISTENER ---
@telethon_client.on(events.NewMessage(incoming=True))
async def inbox_listener(event):
    if event.is_private and not event.out:
        try:
            sender = await event.get_sender()
            add_inbox_message(event.chat_id, sender.id, getattr(sender, 'first_name', 'Unknown'), getattr(sender, 'username', 'N/A'), event.raw_text if event.raw_text else "", get_media_type(event), str(event.date))
        except Exception as e:
            print(f"Inbox Error: {e}")

# --- MAIN ---
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
    bot_app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_link))
    print("Bot running...")
    await bot_app.initialize()
    await bot_app.start()
    await bot_app.updater.start_polling()
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=10000), daemon=True).start()
    await asyncio.Event().wait()

if __name__ == '__main__':
    asyncio.run(main())
