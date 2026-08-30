import re
import asyncio
import os
import threading
import sqlite3
from io import BytesIO
from html import escape

from flask import Flask

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from telegram.constants import ParseMode

from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    CallbackQueryHandler,
    ContextTypes,
)

from telethon import TelegramClient, events, functions, types
from telethon.sessions import StringSession
from telethon.errors import (
    FloodWaitError,
    UsernameNotOccupiedError,
)

from telethon.tl.types import (
    PeerChannel,
    PeerUser,
    PeerChat,
    InputPeerChannel,
)


# ============================================================
# FLASK HEALTH ENDPOINT
# ============================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "Bot is alive!"


# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
STRING_SESSION = os.environ.get("STRING_SESSION", "")

BOT_PASSWORD = os.environ.get(
    "BOT_PASSWORD",
    "ptss25"
)


# ============================================================
# TELETHON CLIENT
# ============================================================

telethon_client = TelegramClient(
    StringSession(STRING_SESSION),
    API_ID,
    API_HASH
)


# ============================================================
# DATABASE
# ============================================================

DB_PATH = "bot_data.db"


def init_db():

    conn = sqlite3.connect(DB_PATH)

    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS inbox (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            sender_id INTEGER,
            name TEXT,
            username TEXT,
            text TEXT,
            media_type TEXT,
            date TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS tracking (
            target_id INTEGER PRIMARY KEY,
            username TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS user_history (
            user_id INTEGER,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            date TEXT
        )
    """)

    conn.commit()
    conn.close()


def is_authenticated(user_id):

    conn = sqlite3.connect(DB_PATH)

    c = conn.cursor()

    c.execute(
        "SELECT * FROM users WHERE user_id = ?",
        (user_id,)
    )

    data = c.fetchone()

    conn.close()

    return data is not None


def add_authenticated_user(user_id):

    conn = sqlite3.connect(DB_PATH)

    c = conn.cursor()

    c.execute(
        "INSERT OR IGNORE INTO users (user_id) VALUES (?)",
        (user_id,)
    )

    conn.commit()
    conn.close()


def remove_authenticated_user(user_id):

    conn = sqlite3.connect(DB_PATH)

    c = conn.cursor()

    c.execute(
        "DELETE FROM users WHERE user_id = ?",
        (user_id,)
    )

    conn.commit()
    conn.close()


def add_inbox_message(
    chat_id,
    sender_id,
    name,
    username,
    text,
    media_type,
    date
):

    conn = sqlite3.connect(DB_PATH)

    c = conn.cursor()

    c.execute(
        """
        INSERT INTO inbox
        (
            chat_id,
            sender_id,
            name,
            username,
            text,
            media_type,
            date
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            chat_id,
            sender_id,
            name,
            username,
            text,
            media_type,
            date
        )
    )

    conn.commit()
    conn.close()


def get_inbox_conversations():

    conn = sqlite3.connect(DB_PATH)

    c = conn.cursor()

    c.execute(
        """
        SELECT
            chat_id,
            sender_id,
            name,
            username,
            text,
            media_type,
            date
        FROM inbox
        ORDER BY id DESC
        """
    )

    rows = c.fetchall()

    conn.close()

    return rows


def save_user_history(
    user_id,
    username,
    first_name,
    last_name
):

    conn = sqlite3.connect(DB_PATH)

    c = conn.cursor()

    c.execute(
        """
        INSERT INTO user_history
        (
            user_id,
            username,
            first_name,
            last_name,
            date
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            user_id,
            username,
            first_name,
            last_name,
            str(__import__("datetime").datetime.now())
        )
    )

    conn.commit()
    conn.close()


def get_user_history(user_id):

    conn = sqlite3.connect(DB_PATH)

    c = conn.cursor()

    c.execute(
        """
        SELECT
            username,
            first_name,
            last_name,
            date
        FROM user_history
        WHERE user_id = ?
        ORDER BY date DESC
        """,
        (user_id,)
    )

    rows = c.fetchall()

    conn.close()

    return rows


# ============================================================
# TELEGRAM UTILITIES
# ============================================================

def parse_tg_link(text):

    pattern = r"https?://t\.me/([a-zA-Z0-9_]+)/(\d+)"

    match = re.search(pattern, text)

    if match:
        return match.group(1), int(match.group(2))

    channel_pattern = r"https?://t\.me/([a-zA-Z0-9_]+)"

    match = re.search(channel_pattern, text)

    if match:
        return match.group(1), None

    return None, None


def get_media_type(event):

    if getattr(event, "photo", None):
        return "📷"

    if getattr(event, "video", None):
        return "🎬"

    if getattr(event, "document", None):
        return "📄"

    if getattr(event, "audio", None):
        return "🎵"

    if getattr(event, "voice", None):
        return "🎤"

    if getattr(event, "sticker", None):
        return "🧩"

    return "💬"


def safe_numeric_for_c(cid):

    if cid is None:
        return None

    try:

        if hasattr(cid, "id"):
            cid = getattr(cid, "id")

        cid_int = int(cid)

        s = str(cid_int)

    except Exception:

        s = str(cid)

    if s.startswith("-100"):
        return s[4:]

    if s.startswith("-"):
        return s[1:]

    return s


def tg_link_from_chat_obj(
    chat_obj,
    msg_id
):

    if chat_obj is None:
        return ""

    username = getattr(
        chat_obj,
        "username",
        None
    )

    if username:

        return (
            f"https://t.me/"
            f"{username}/"
            f"{msg_id}"
        )

    cid = (
        getattr(chat_obj, "id", None)
        or getattr(chat_obj, "channel_id", None)
        or getattr(chat_obj, "chat_id", None)
    )

    numeric = safe_numeric_for_c(cid)

    if numeric:

        return (
            f"https://t.me/c/"
            f"{numeric}/"
            f"{msg_id}"
        )

    return ""


def tg_link_from_chat_id(
    chat_id,
    msg_id
):

    numeric = safe_numeric_for_c(chat_id)

    if numeric:

        return (
            f"https://t.me/c/"
            f"{numeric}/"
            f"{msg_id}"
        )

    return ""


def extract_chat_id_from_msg(msg):

    if (
        hasattr(msg, "chat_id")
        and getattr(msg, "chat_id") is not None
    ):

        try:
            return int(msg.chat_id)

        except Exception:
            pass

    pid = (
        getattr(msg, "peer_id", None)
        or getattr(msg, "to_id", None)
        or getattr(msg, "from_id", None)
    )

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

    cid = (
        getattr(pid, "channel_id", None)
        or getattr(pid, "chat_id", None)
        or getattr(pid, "user_id", None)
    )

    if cid is not None:

        try:
            return int(cid)

        except Exception:
            return None

    return None


# ============================================================
# SAFE SEND
# ============================================================

async def safe_send(
    chat_id,
    bot,
    msg,
    from_chat_id,
    message_id
):

    text = getattr(
        msg,
        "message",
        None
    )

    try:

        if (
            getattr(msg, "photo", None)
            or getattr(msg, "video", None)
            or getattr(msg, "document", None)
            or getattr(msg, "voice", None)
            or getattr(msg, "audio", None)
            or getattr(msg, "gif", None)
        ):

            media_bytes = BytesIO()

            await telethon_client.download_media(
                msg,
                file=media_bytes
            )

            media_bytes.seek(0)

            if getattr(msg, "photo", None):

                await bot.send_photo(
                    chat_id,
                    photo=media_bytes,
                    caption=text
                )

            elif getattr(msg, "video", None):

                await bot.send_video(
                    chat_id,
                    video=media_bytes,
                    caption=text
                )

            elif getattr(msg, "document", None):

                await bot.send_document(
                    chat_id,
                    document=media_bytes,
                    caption=text
                )

            elif getattr(msg, "voice", None):

                await bot.send_voice(
                    chat_id,
                    voice=media_bytes,
                    caption=text
                )

            elif getattr(msg, "audio", None):

                await bot.send_audio(
                    chat_id,
                    audio=media_bytes,
                    caption=text
                )

            elif getattr(msg, "gif", None):

                await bot.send_animation(
                    chat_id,
                    animation=media_bytes,
                    caption=text
                )

        elif getattr(msg, "sticker", None):

            sticker_bytes = BytesIO()

            await telethon_client.download_media(
                msg,
                file=sticker_bytes
            )

            sticker_bytes.seek(0)

            await bot.send_sticker(
                chat_id,
                sticker=sticker_bytes
            )

        elif text:

            await bot.send_message(
                chat_id,
                text=text
            )

        else:

            await bot.send_message(
                chat_id,
                text="Unsupported message type."
            )

    except Exception as e:

        if (
            "must forward even restricted"
            in str(e).lower()
        ):

            await bot.send_message(
                chat_id,
                "🔒 Restricted media."
            )

        else:

            await bot.send_message(
                chat_id,
                f"Failed to send media: {e}"
            )


# ============================================================
# TELETHON ERROR
# ============================================================

async def handle_telethon_error(
    update,
    error
):

    if isinstance(error, FloodWaitError):

        await update.message.reply_text(
            f"⚠️ FloodWait: "
            f"{error.seconds} seconds."
        )

    elif isinstance(
        error,
        UsernameNotOccupiedError
    ):

        await update.message.reply_text(
            "❌ Username not found."
        )

    else:

        await update.message.reply_text(
            f"❌ Error: {error}"
        )


# ============================================================
# START
# ============================================================

async def start(
    update,
    context
):

    user_id = update.effective_user.id

    if not is_authenticated(user_id):

        context.user_data[
            "state"
        ] = "awaiting_password"

        await update.message.reply_text(
            "🔐 TELEGRAM ASSISTANT\n\n"
            "Password required.\n"
            "Please enter the password to continue."
        )

        return

    keyboard = [

        [
            InlineKeyboardButton(
                "📥 Inbox",
                callback_data="inbox"
            ),

            InlineKeyboardButton(
                "👤 Profile",
                callback_data="profile"
            )
        ],

        [
            InlineKeyboardButton(
                "🔗 Fetch Telegram",
                callback_data="fetch"
            )
        ],

        [
            InlineKeyboardButton(
                "➕ More Commands",
                callback_data="more"
            )
        ]

    ]

    await update.message.reply_text(
        "🤖 TELEGRAM ASSISTANT\n\n"
        "Choose an option:",
        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )


# ============================================================
# LOGOUT
# ============================================================

async def logout(
    update,
    context
):

    user_id = update.effective_user.id

    remove_authenticated_user(
        user_id
    )

    await update.message.reply_text(
        "🔒 Logged out."
    )


# ============================================================
# MAIN MENU CALLBACK
# ============================================================

async def menu_callback(
    update,
    context
):

    query = update.callback_query

    await query.answer()

    user_id = update.effective_user.id

    if not is_authenticated(user_id):

        await query.edit_message_text(
            "🔐 Password required."
        )

        return

    data = query.data

    # ========================================================
    # INBOX
    # ========================================================

    if data == "inbox":

        rows = get_inbox_conversations()

        if not rows:

            text = (
                "📥 INBOX\n\n"
                "No new private messages."
            )

            kb = [
                [
                    InlineKeyboardButton(
                        "⬅️ Back",
                        callback_data="main_menu"
                    )
                ]
            ]

        else:

            text = "📥 INBOX\n"

            kb = []

            seen = set()

            for row in rows:

                (
                    chat_id,
                    sender_id,
                    name,
                    username,
                    msg_text,
                    media,
                    date
                ) = row

                if chat_id not in seen:

                    seen.add(chat_id)

                    content = (
                        msg_text
                        if msg_text
                        else f"[{media}]"
                    )

                    uname = (
                        username
                        or "N/A"
                    )

                    text += (
                        f"\n👤 {name} "
                        f"(@{uname})\n"
                        f"\"{content}\"\n"
                    )

                    kb.append(
                        [
                            InlineKeyboardButton(
                                f"💬 {name}",
                                callback_data=(
                                    f"conv_{chat_id}"
                                )
                            )
                        ]
                    )

            kb.append(
                [
                    InlineKeyboardButton(
                        "⬅️ Back",
                        callback_data="main_menu"
                    )
                ]
            )

        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(kb)
        )

        return

    # ========================================================
    # CONVERSATION
    # ========================================================

    if data.startswith("conv_"):

        chat_id = int(
            data.split("_", 1)[1]
        )

        rows = get_inbox_conversations()

        text = "💬 CHAT\n\n"

        for row in reversed(
            rows[-50:]
        ):

            if row[0] == chat_id:

                text += (
                    f"{row[3]}:\n"
                    f"{row[4] if row[4] else f'[{row[5]}]'}\n\n"
                )

        kb = [

            [
                InlineKeyboardButton(
                    "↩️ Reply",
                    callback_data=(
                        f"reply_{chat_id}"
                    )
                ),

                InlineKeyboardButton(
                    "👤 Profile",
                    callback_data="profile"
                )
            ],

            [
                InlineKeyboardButton(
                    "🔄 Refresh",
                    callback_data=(
                        f"refresh_{chat_id}"
                    )
                ),

                InlineKeyboardButton(
                    "⬅️ Back",
                    callback_data="inbox"
                )
            ]

        ]

        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(kb)
        )

        return

    # ========================================================
    # REPLY
    # ========================================================

    if data.startswith("reply_"):

        chat_id = int(
            data.split("_", 1)[1]
        )

        context.user_data[
            "reply_to"
        ] = chat_id

        await query.edit_message_text(
            f"💬 Type your reply to {chat_id}:"
        )

        return

    # ========================================================
    # REFRESH
    # ========================================================

    if data.startswith("refresh_"):

        chat_id = int(
            data.split("_", 1)[1]
        )

        rows = get_inbox_conversations()

        text = "💬 CHAT\n\n"

        for row in reversed(
            rows[-50:]
        ):

            if row[0] == chat_id:

                text += (
                    f"{row[3]}:\n"
                    f"{row[4] if row[4] else f'[{row[5]}]'}\n\n"
                )

        kb = [

            [
                InlineKeyboardButton(
                    "↩️ Reply",
                    callback_data=(
                        f"reply_{chat_id}"
                    )
                ),

                InlineKeyboardButton(
                    "👤 Profile",
                    callback_data="profile"
                )
            ],

            [
                InlineKeyboardButton(
                    "🔄 Refresh",
                    callback_data=(
                        f"refresh_{chat_id}"
                    )
                ),

                InlineKeyboardButton(
                    "⬅️ Back",
                    callback_data="inbox"
                )
            ]

        ]

        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(kb)
        )

        return

    # ========================================================
    # PROFILE
    # ========================================================

    if data == "profile":

        entity = context.user_data.get(
            "profile_entity"
        )

        if entity:

            first_name = escape(
                str(
                    getattr(
                        entity,
                        "first_name",
                        ""
                    )
                    or ""
                )
            )

            last_name = escape(
                str(
                    getattr(
                        entity,
                        "last_name",
                        ""
                    )
                    or ""
                )
            )

            username = escape(
                str(
                    getattr(
                        entity,
                        "username",
                        "N/A"
                    )
                    or "N/A"
                )
            )

            about = escape(
                str(
                    getattr(
                        entity,
                        "about",
                        "No bio"
                    )
                    or "No bio"
                )
            )

            text = (
                f"<blockquote>"
                f"<b>{first_name} "
                f"{last_name}</b>\n"
                f"@{username}\n\n"
                f"{about}\n\n"
                f"ID: "
                f"{getattr(entity, 'id', 'N/A')}\n"
                f"Verified: "
                f"{getattr(entity, 'verified', False)}\n"
                f"Premium: "
                f"{getattr(entity, 'premium', False)}\n"
                f"Bot: "
                f"{getattr(entity, 'bot', False)}"
                f"</blockquote>"
            )

            kb = [

                [
                    InlineKeyboardButton(
                        "📰 View Posts",
                        callback_data="posts_1"
                    ),

                    InlineKeyboardButton(
                        "📸 View Stories",
                        callback_data="story_start"
                    )
                ],

                [
                    InlineKeyboardButton(
                        "⬅️ Back",
                        callback_data="more"
                    )
                ]

            ]

            await query.edit_message_text(
                text,
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(kb)
            )

        else:

            await query.edit_message_text(
                "👤 PROFILE\n\n"
                "Enter a Telegram username or ID "
                "to generate the Profile Card:"
            )

            context.user_data[
                "state"
            ] = "profile_query"

        return

    # ========================================================
    # FETCH
    # ========================================================

    if data == "fetch":

        await query.edit_message_text(
            "🔗 Fetch Telegram\n\n"
            "Send me a link:\n\n"
            "Example:\n"
            "https://t.me/channel/123"
        )

        context.user_data[
            "state"
        ] = "fetch_link"

        return

    # ========================================================
    # MAIN MENU
    # ========================================================

    if data == "main_menu":

        keyboard = [

            [
                InlineKeyboardButton(
                    "📥 Inbox",
                    callback_data="inbox"
                ),

                InlineKeyboardButton(
                    "👤 Profile",
                    callback_data="profile"
                )
            ],

            [
                InlineKeyboardButton(
                    "🔗 Fetch Telegram",
                    callback_data="fetch"
                )
            ],

            [
                InlineKeyboardButton(
                    "➕ More Commands",
                    callback_data="more"
                )
            ]

        ]

        await query.edit_message_text(
            "🤖 TELEGRAM ASSISTANT",
            reply_markup=InlineKeyboardMarkup(
                keyboard
            )
        )

        return

    # ========================================================
    # MORE
    # ========================================================

    if data == "more":

        kb = [

            [
                InlineKeyboardButton(
                    "🔎 Search",
                    callback_data="search"
                ),

                InlineKeyboardButton(
                    "📊 Statistics",
                    callback_data="stats"
                )
            ],

            [
                InlineKeyboardButton(
                    "🔔 Track",
                    callback_data="track"
                ),

                InlineKeyboardButton(
                    "🔗 Names",
                    callback_data="names"
                )
            ],

            [
                InlineKeyboardButton(
                    "👥 Groups",
                    callback_data="groups"
                ),

                InlineKeyboardButton(
                    "💬 Messages",
                    callback_data="messages"
                )
            ],

            [
                InlineKeyboardButton(
                    "🔎 Analysis",
                    callback_data="analysis"
                ),

                InlineKeyboardButton(
                    "📢 Channels",
                    callback_data="channels"
                )
            ],

            [
                InlineKeyboardButton(
                    "👍 Reputation",
                    callback_data="rep"
                ),

                InlineKeyboardButton(
                    "👥 Friends",
                    callback_data="friends"
                )
            ],

            [
                InlineKeyboardButton(
                    "🔄 Reactions",
                    callback_data="reactions"
                ),

                InlineKeyboardButton(
                    "🎁 Gifts",
                    callback_data="gifts"
                )
            ],

            [
                InlineKeyboardButton(
                    "📤 Share",
                    callback_data="share"
                ),

                InlineKeyboardButton(
                    "🔵 Words Frequency",
                    callback_data="words"
                )
            ],

            [
                InlineKeyboardButton(
                    "👥 Common Groups",
                    callback_data="common"
                )
            ],

            [
                InlineKeyboardButton(
                    "⬅️ Back",
                    callback_data="main_menu"
                )
            ]

        ]

        await query.edit_message_text(
            "➕ MORE COMMANDS",
            reply_markup=InlineKeyboardMarkup(kb)
        )

        return

    # ========================================================
    # COMMAND PROMPTS
    # ========================================================

    command_states = {
        "search": (
            "🔎 GLOBAL TELEGRAM SEARCH",
            "Enter a keyword or phrase.\n\n"
            "Examples:\n"
            "• BDU final exam\n"
            "• Chemistry final exam\n"
            "• C++ final exam\n"
            "• freshman mathematics\n\n"
            "🌍 Public Telegram content can be "
            "searched globally, including public "
            "channels you have not joined."
        ),

        "stats": (
            "📊 STATISTICS",
            "Enter target username/ID or chat link:"
        ),

        "track": (
            "🔔 TRACK",
            "Enter target username/ID to track:"
        ),

        "names": (
            "🔗 NAMES",
            "Enter target username/ID:"
        ),

        "groups": (
            "👥 GROUPS",
            "Enter the account ID "
            "(usually your own) to list groups:"
        ),

        "messages": (
            "💬 MESSAGES",
            "Enter target username/ID or chat link:"
        ),

        "analysis": (
            "🔎 ANALYSIS",
            "Enter target username/ID or chat link:"
        ),

        "channels": (
            "📢 CHANNELS",
            "Enter the account ID "
            "(usually your own) to list channels:"
        ),

        "rep": (
            "👍 REPUTATION",
            "Enter target username/ID or chat link:"
        ),

        "friends": (
            "👥 FRIENDS",
            "Enter target group username and "
            "target user username separated by space.\n\n"
            "Example:\n"
            "@mygroup @user"
        ),

        "reactions": (
            "🔄 REACTIONS",
            "Enter target username/ID or chat link:"
        ),

        "gifts": (
            "🎁 GIFTS",
            "Enter target username/ID:"
        ),

        "share": (
            "📤 SHARE",
            "Enter target username/ID or message link:"
        ),

        "words": (
            "🔵 WORDS FREQUENCY",
            "Enter target username/ID or chat link:"
        ),

        "common": (
            "👥 COMMON GROUPS",
            "Enter target username/ID:"
        )
    }

    if data in command_states:

        title, prompt = command_states[data]

        await query.edit_message_text(
            f"{title}\n\n{prompt}"
        )

        context.user_data[
            "state"
        ] = data

        return

    # ========================================================
    # SEARCH PAGINATION
    # ========================================================

    if data.startswith("search_"):

        try:

            page = int(
                data.split("_", 1)[1]
            )

            await handle_search_pagination(
                update,
                context,
                page
            )

        except Exception as e:

            await query.edit_message_text(
                f"❌ Pagination error: {e}"
            )

        return

    # ========================================================
    # POSTS PAGINATION
    # ========================================================

    if data.startswith("posts_"):

        try:

            page = int(
                data.split("_", 1)[1]
            )

            await handle_posts_pagination(
                update,
                context,
                page
            )

        except Exception as e:

            await query.edit_message_text(
                f"❌ Pagination error: {e}"
            )

        return

    # ========================================================
    # STORIES
    # ========================================================

    if data == "story_start":

        entity = context.user_data.get(
            "profile_entity"
        )

        if not entity:

            await query.edit_message_text(
                "No profile loaded. "
                "Please view a profile first."
            )

            return

        await fetch_stories(
            update,
            context,
            entity
        )

        return

    if data == "story_prev":

        context.user_data[
            "story_index"
        ] = (
            context.user_data.get(
                "story_index",
                0
            ) - 1
        )

        await display_story(
            update,
            context
        )

        return

    if data == "story_next":

        context.user_data[
            "story_index"
        ] = (
            context.user_data.get(
                "story_index",
                0
            ) + 1
        )

        await display_story(
            update,
            context
        )

        return

    # ========================================================
    # SEARCH FILTERS
    # ========================================================

    if data.startswith("filter_"):

        filter_key = data.split(
            "_",
            1
        )[1]

        filter_map = {

            "all":
                types.InputMessagesFilterEmpty,

            "photos":
                getattr(
                    types,
                    "InputMessagesFilterPhotos",
                    types.InputMessagesFilterEmpty
                ),

            "videos":
                getattr(
                    types,
                    "InputMessagesFilterVideo",
                    types.InputMessagesFilterEmpty
                ),

            "docs":
                getattr(
                    types,
                    "InputMessagesFilterDocument",
                    types.InputMessagesFilterEmpty
                ),

            "links":
                getattr(
                    types,
                    "InputMessagesFilterUrl",
                    types.InputMessagesFilterEmpty
                ),

            "music":
                getattr(
                    types,
                    "InputMessagesFilterMusic",
                    types.InputMessagesFilterEmpty
                ),

            "voice":
                getattr(
                    types,
                    "InputMessagesFilterVoice",
                    types.InputMessagesFilterEmpty
                ),

            "gif":
                getattr(
                    types,
                    "InputMessagesFilterGif",
                    types.InputMessagesFilterEmpty
                )
        }

        filter_class = filter_map.get(
            filter_key,
            types.InputMessagesFilterEmpty
        )

        filter_type = filter_class()

        query_text = context.user_data.get(
            "search_query",
            ""
        )

        if not query_text:

            await query.edit_message_text(
                "❌ Search session expired.\n"
                "Please start a new search."
            )

            return

        await query.answer(
            f"Searching {filter_key}..."
        )

        await fetch_search(
            update,
            context,
            query_text,
            filter_type,
            is_callback=True
        )

        return


# ============================================================
# RESOLVE ENTITY
# ============================================================

async def resolve_entity_from_target(
    context,
    target
):

    try:

        ent = await telethon_client.get_entity(
            target
        )

        return (
            ent,
            "resolved via get_entity"
        )

    except Exception:

        pass

    chat_map = (
        context.user_data.get(
            "last_search_chats"
        )
        or {}
    )

    t = str(target).lstrip("@")

    for ch in chat_map.values():

        try:

            username = getattr(
                ch,
                "username",
                None
            )

            if (
                username
                and username.lower()
                == t.lower()
            ):

                return (
                    ch,
                    "from last_search_chats"
                )

        except Exception:

            pass

    try:

        tid = int(target)

    except Exception:

        tid = None

    if tid is not None:

        for ch in chat_map.values():

            try:

                cid = (
                    getattr(ch, "id", None)
                    or getattr(ch, "channel_id", None)
                    or getattr(ch, "chat_id", None)
                )

                if (
                    cid is not None
                    and int(cid) == tid
                ):

                    access_hash = getattr(
                        ch,
                        "access_hash",
                        None
                    )

                    if access_hash:

                        try:

                            peer = InputPeerChannel(
                                channel_id=tid,
                                access_hash=int(
                                    access_hash
                                )
                            )

                            ent = await telethon_client.get_entity(
                                peer
                            )

                            return (
                                ent,
                                "resolved via InputPeerChannel"
                            )

                        except Exception:

                            return (
                                ch,
                                "using chat object"
                            )

                    return (
                        ch,
                        "from last_search_chats by id"
                    )

            except Exception:

                pass

    return (
        None,
        "not resolved"
    )


# ============================================================
# PROFILE
# ============================================================

async def fetch_profile(
    update,
    context,
    target
):

    try:

        ent, note = await resolve_entity_from_target(
            context,
            target
        )

        if ent is None:

            await update.message.reply_text(
                f"❌ Could not resolve profile "
                f"for '{target}'. {note}.\n\n"
                f"Try @username or numeric ID."
            )

            return

        context.user_data[
            "profile_entity"
        ] = ent

        try:

            msgs = await telethon_client.get_messages(
                ent,
                limit=50
            )

            context.user_data[
                "post_messages"
            ] = list(msgs)

        except Exception:

            context.user_data[
                "post_messages"
            ] = []

        save_user_history(
            getattr(ent, "id", 0),
            getattr(ent, "username", ""),
            getattr(ent, "first_name", ""),
            getattr(ent, "last_name", "")
        )

        first_name = escape(
            str(
                getattr(
                    ent,
                    "first_name",
                    ""
                )
                or ""
            )
        )

        last_name = escape(
            str(
                getattr(
                    ent,
                    "last_name",
                    ""
                )
                or ""
            )
        )

        username = escape(
            str(
                getattr(
                    ent,
                    "username",
                    "N/A"
                )
                or "N/A"
            )
        )

        about = escape(
            str(
                getattr(
                    ent,
                    "about",
                    "No bio"
                )
                or "No bio"
            )
        )

        text = (
            f"<blockquote>"
            f"<b>{first_name} "
            f"{last_name}</b>\n"
            f"@{username}\n\n"
            f"{about}\n\n"
            f"ID: {getattr(ent, 'id', 'N/A')}\n"
            f"Verified: "
            f"{getattr(ent, 'verified', False)}\n"
            f"Premium: "
            f"{getattr(ent, 'premium', False)}\n"
            f"Bot: "
            f"{getattr(ent, 'bot', False)}"
            f"</blockquote>"
        )

        kb = [

            [
                InlineKeyboardButton(
                    "📰 View Posts",
                    callback_data="posts_1"
                ),

                InlineKeyboardButton(
                    "📸 View Stories",
                    callback_data="story_start"
                )
            ],

            [
                InlineKeyboardButton(
                    "⬅️ Back",
                    callback_data="more"
                )
            ]

        ]

        try:

            photo = await telethon_client.download_profile_photo(
                ent,
                file=BytesIO()
            )

            if photo:

                photo.seek(0)

                await update.message.reply_photo(
                    photo=photo,
                    caption=text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup(kb)
                )

                return

        except Exception:

            pass

        await update.message.reply_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(kb)
        )

    except Exception as e:

        await update.message.reply_text(
            f"❌ Error fetching profile: {e}"
        )


# ============================================================
# PROFILE POSTS
# ============================================================

async def handle_posts_pagination(
    update,
    context,
    page
):

    query = update.callback_query

    await query.answer()

    messages = context.user_data.get(
        "post_messages"
    )

    if not messages:

        await query.edit_message_text(
            "No posts found. "
            "Please load profile first."
        )

        return

    per_page = 5

    total = len(messages)

    total_pages = max(
        1,
        (total + per_page - 1)
        // per_page
    )

    if page < 1:
        page = 1

    if page > total_pages:
        page = total_pages

    start = (
        page - 1
    ) * per_page

    end = min(
        start + per_page,
        total
    )

    page_items = messages[
        start:end
    ]

    if not page_items:

        await query.edit_message_text(
            "No more posts."
        )

        return

    text = (
        f"📰 <b>POSTS</b>\n"
        f"Page {page}/{total_pages}\n\n"
    )

    entity = context.user_data.get(
        "profile_entity"
    )

    for msg in page_items:

        media_emoji = get_media_type(msg)

        date_str = ""

        if getattr(
            msg,
            "date",
            None
        ):

            date_str = msg.date.strftime(
                "%Y-%m-%d %H:%M"
            )

        raw_content = getattr(
            msg,
            "message",
            None
        )

        if raw_content:

            if len(raw_content) > 120:

                raw_content = (
                    raw_content[:120]
                    + "..."
                )

        else:

            raw_content = "[Media]"

        content = escape(
            str(raw_content)
        )

        link = ""

        if (
            entity
            and getattr(
                entity,
                "username",
                None
            )
        ):

            link = (
                f"https://t.me/"
                f"{entity.username}/"
                f"{msg.id}"
            )

        else:

            chat_id = extract_chat_id_from_msg(
                msg
            )

            chat_map = (
                context.user_data.get(
                    "last_search_chats"
                )
                or {}
            )

            chat_obj = None

            if chat_id is not None:

                chat_obj = (
                    chat_map.get(
                        int(chat_id)
                    )
                    or chat_map.get(
                        str(chat_id)
                    )
                )

            if chat_obj:

                link = tg_link_from_chat_obj(
                    chat_obj,
                    msg.id
                )

            elif chat_id is not None:

                link = tg_link_from_chat_id(
                    chat_id,
                    msg.id
                )

        if link:

            text += (
                f"{media_emoji} "
                f"<a href='{link}'>"
                f"{content}"
                f"</a>"
                f" - {date_str}\n"
            )

        else:

            text += (
                f"{media_emoji} "
                f"{content}"
                f" - {date_str}\n"
            )

    kb = []

    nav_row = []

    if page > 1:

        nav_row.append(
            InlineKeyboardButton(
                "⬅️ Previous",
                callback_data=(
                    f"posts_{page - 1}"
                )
            )
        )

    if page < total_pages:

        nav_row.append(
            InlineKeyboardButton(
                "Next ➡️",
                callback_data=(
                    f"posts_{page + 1}"
                )
            )
        )

    if nav_row:

        kb.append(nav_row)

    kb.append(
        [
            InlineKeyboardButton(
                "⬅️ Back to Profile",
                callback_data="profile"
            )
        ]
    )

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True
    )


# ============================================================
# SEARCH HELPERS
# ============================================================

def build_search_chat_map(
    *results
):

    chat_map = {}

    for result in results:

        if not result:
            continue

        for chat in (
            getattr(
                result,
                "chats",
                []
            )
            or []
        ):

            try:

                cid = getattr(
                    chat,
                    "id",
                    None
                )

                if cid is not None:

                    chat_map[
                        int(cid)
                    ] = chat

                    chat_map[
                        str(cid)
                    ] = chat

                    try:

                        chat_map[
                            abs(int(cid))
                        ] = chat

                    except Exception:

                        pass

                username = getattr(
                    chat,
                    "username",
                    None
                )

                if username:

                    chat_map[
                        username.lower()
                    ] = chat

            except Exception:

                continue

    return chat_map


def find_chat_for_message(
    msg,
    chat_map
):

    chat_id = extract_chat_id_from_msg(
        msg
    )

    if chat_id is not None:

        chat_obj = (
            chat_map.get(
                int(chat_id)
            )
            or chat_map.get(
                str(chat_id)
            )
            or chat_map.get(
                abs(int(chat_id))
            )
        )

        if chat_obj:

            return chat_obj

    sender_chat = getattr(
        msg,
        "sender_chat",
        None
    )

    if sender_chat:

        return sender_chat

    return None


def build_message_link(
    msg,
    chat_obj
):

    if chat_obj:

        username = getattr(
            chat_obj,
            "username",
            None
        )

        if username:

            return (
                f"https://t.me/"
                f"{username}/"
                f"{msg.id}"
            )

        cid = getattr(
            chat_obj,
            "id",
            None
        )

        if cid is not None:

            numeric = safe_numeric_for_c(
                cid
            )

            if numeric:

                return (
                    f"https://t.me/c/"
                    f"{numeric}/"
                    f"{msg.id}"
                )

    chat_id = extract_chat_id_from_msg(
        msg
    )

    if chat_id is not None:

        return tg_link_from_chat_id(
            chat_id,
            msg.id
        )

    return ""


def get_search_source_name(
    chat_obj,
    msg
):

    if chat_obj:

        title = (
            getattr(
                chat_obj,
                "title",
                None
            )
            or getattr(
                chat_obj,
                "first_name",
                None
            )
            or getattr(
                chat_obj,
                "username",
                None
            )
        )

        if title:

            return str(title)

    sender = getattr(
        msg,
        "sender",
        None
    )

    if sender:

        name = (
            getattr(
                sender,
                "first_name",
                None
            )
            or getattr(
                sender,
                "title",
                None
            )
            or getattr(
                sender,
                "username",
                None
            )
        )

        if name:

            return str(name)

    return "Telegram"


# ============================================================
# GLOBAL SEARCH
# ============================================================

async def fetch_search(
    update,
    context,
    text,
    filter_type=None,
    is_callback=False
):

    text = (text or "").strip()

    if not text:

        msg = (
            "⚠️ Please enter a search keyword."
        )

        if is_callback:

            await update.callback_query.edit_message_text(
                msg
            )

        else:

            await update.effective_message.reply_text(
                msg
            )

        return

    try:

        context.user_data[
            "search_query"
        ] = text

        context.user_data[
            "search_filter"
        ] = (
            filter_type
            or types.InputMessagesFilterEmpty()
        )

        # ----------------------------------------------------
        # GLOBAL NORMAL SEARCH
        # ----------------------------------------------------

        global_result = None

        global_error = None

        try:

            global_result = await telethon_client(
                functions.messages.SearchGlobalRequest(
                    q=text,

                    filter=(
                        filter_type
                        or types.InputMessagesFilterEmpty()
                    ),

                    min_date=None,
                    max_date=None,

                    offset_rate=0,

                    offset_peer=(
                        types.InputPeerEmpty()
                    ),

                    offset_id=0,

                    limit=100
                )
            )

        except Exception as e:

            global_error = e

            print(
                "messages.searchGlobal error:",
                e
            )

        # ----------------------------------------------------
        # PUBLIC CHANNEL GLOBAL POST SEARCH
        # ----------------------------------------------------
        #
        # This is the important part for:
        #
        # "Search public channels I have NOT joined"
        #
        # Telegram documents channels.searchPosts as
        # global public-channel post search.
        #
        # We DO NOT automatically pay Stars.
        # ----------------------------------------------------

        public_posts = None

        public_search_error = None

        try:

            search_posts_request = getattr(
                functions.channels,
                "SearchPostsRequest",
                None
            )

            if search_posts_request:

                public_posts = await telethon_client(
                    search_posts_request(
                        hashtag=None,
                        query=text,

                        offset_rate=0,

                        offset_peer=(
                            types.InputPeerEmpty()
                        ),

                        offset_id=0,

                        limit=100
                    )
                )

            else:

                public_search_error = (
                    "Your Telethon version does not "
                    "support channels.searchPosts."
                )

        except Exception as e:

            public_search_error = e

            print(
                "channels.searchPosts error:",
                e
            )

        # ----------------------------------------------------
        # SAVE RAW RESULTS
        # ----------------------------------------------------

        context.user_data[
            "search_global_result"
        ] = global_result

        context.user_data[
            "search_posts_result"
        ] = public_posts

        # ----------------------------------------------------
        # CHAT MAP
        # ----------------------------------------------------

        chat_map = build_search_chat_map(
            global_result,
            public_posts
        )

        context.user_data[
            "last_search_chats"
        ] = chat_map

        # ----------------------------------------------------
        # COMBINE RESULTS
        # ----------------------------------------------------

        combined = []

        seen = set()

        def add_results(
            result,
            source
        ):

            if not result:
                return

            for msg in (
                getattr(
                    result,
                    "messages",
                    []
                )
                or []
            ):

                msg_id = getattr(
                    msg,
                    "id",
                    None
                )

                chat_id = extract_chat_id_from_msg(
                    msg
                )

                key = (
                    chat_id,
                    msg_id
                )

                if key in seen:
                    continue

                seen.add(key)

                chat_obj = find_chat_for_message(
                    msg,
                    chat_map
                )

                combined.append(
                    {
                        "message": msg,
                        "chat": chat_obj,
                        "source": source
                    }
                )

        # Public channel posts first
        add_results(
            public_posts,
            "public_channel"
        )

        # Global Telegram search
        add_results(
            global_result,
            "global"
        )

        # ----------------------------------------------------
        # SORT NEWEST FIRST
        # ----------------------------------------------------

        combined.sort(
            key=lambda item:
                getattr(
                    item["message"],
                    "date",
                    None
                )
                or 0,
            reverse=True
        )

        context.user_data[
            "search_items"
        ] = combined

        # ----------------------------------------------------
        # NO RESULTS
        # ----------------------------------------------------

        if not combined:

            explanation = (
                "❌ No public Telegram results found."
            )

            if public_search_error:

                error_text = str(
                    public_search_error
                )

                if (
                    "flood"
                    in error_text.lower()
                    or "stars"
                    in error_text.lower()
                    or "premium"
                    in error_text.lower()
                ):

                    explanation += (
                        "\n\n⚠️ Telegram may require "
                        "Stars/Premium for this global "
                        "public-post search."
                    )

            if global_error:

                print(
                    "Global search failed:",
                    global_error
                )

            if is_callback:

                await update.callback_query.edit_message_text(
                    explanation
                )

            else:

                await update.effective_message.reply_text(
                    explanation
                )

            return

        # ----------------------------------------------------
        # SHOW FIRST PAGE
        # ----------------------------------------------------

        await render_search_page(
            update,
            context,
            page=1,
            is_callback=is_callback
        )

    except Exception as e:

        print(
            "GLOBAL SEARCH ERROR:",
            e
        )

        msg = (
            "❌ Search failed.\n\n"
            f"{escape(str(e))}"
        )

        if is_callback:

            await update.callback_query.edit_message_text(
                msg,
                parse_mode=ParseMode.HTML
            )

        else:

            await update.effective_message.reply_text(
                msg,
                parse_mode=ParseMode.HTML
            )


# ============================================================
# RENDER SEARCH RESULTS
# ============================================================

async def render_search_page(
    update,
    context,
    page=1,
    is_callback=True
):

    items = context.user_data.get(
        "search_items",
        []
    )

    query_text = context.user_data.get(
        "search_query",
        ""
    )

    if not items:

        msg = (
            "❌ No search results."
        )

        if is_callback:

            await update.callback_query.edit_message_text(
                msg
            )

        else:

            await update.effective_message.reply_text(
                msg
            )

        return

    per_page = 8

    total = len(items)

    total_pages = max(
        1,
        (total + per_page - 1)
        // per_page
    )

    if page < 1:
        page = 1

    if page > total_pages:
        page = total_pages

    start = (
        page - 1
    ) * per_page

    end = min(
        start + per_page,
        total
    )

    page_items = items[
        start:end
    ]

    if not page_items:

        msg = "❌ No results on this page."

        if is_callback:

            await update.callback_query.edit_message_text(
                msg
            )

        else:

            await update.effective_message.reply_text(
                msg
            )

        return

    safe_query = escape(
        query_text
    )

    text = (
        "🔎 <b>GLOBAL TELEGRAM SEARCH</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🔍 <b>{safe_query}</b>\n\n"
    )

    for index, item in enumerate(
        page_items,
        start=start + 1
    ):

        msg = item["message"]

        chat_obj = item.get(
            "chat"
        )

        source_type = item.get(
            "source",
            "global"
        )

        # ----------------------------------------------------
        # CONTENT
        # ----------------------------------------------------

        content = getattr(
            msg,
            "message",
            None
        )

        if content:

            content = content.strip()

            if len(content) > 180:

                content = (
                    content[:180]
                    + "..."
                )

        else:

            content = (
                f"[{get_media_type(msg)} Media]"
            )

        content = escape(
            str(content)
        )

        # ----------------------------------------------------
        # SOURCE
        # ----------------------------------------------------

        source_name = escape(
            get_search_source_name(
                chat_obj,
                msg
            )
        )

        # ----------------------------------------------------
        # LINK
        # ----------------------------------------------------

        link = build_message_link(
            msg,
            chat_obj
        )

        # ----------------------------------------------------
        # DATE
        # ----------------------------------------------------

        date_text = ""

        if getattr(
            msg,
            "date",
            None
        ):

            date_text = msg.date.strftime(
                "%Y-%m-%d %H:%M"
            )

        # ----------------------------------------------------
        # SOURCE TYPE
        # ----------------------------------------------------

        if source_type == "public_channel":

            source_icon = "📢"

        else:

            source_icon = "🌍"

        # ----------------------------------------------------
        # RESULT
        # ----------------------------------------------------

        text += (
            f"<b>{index}.</b> "
            f"{source_icon} "
            f"<b>{source_name}</b>\n"
        )

        text += (
            f"   {content}\n"
        )

        if date_text:

            text += (
                f"   🕒 {date_text}\n"
            )

        if link:

            text += (
                f"   🔗 "
                f"<a href='{link}'>"
                f"Open message"
                f"</a>\n"
            )

        text += "\n"

    text += (
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📄 Page {page}/{total_pages}\n"
        f"📊 Results loaded: {total}\n"
        "🌍 Public Telegram search"
    )

    # --------------------------------------------------------
    # FILTER BUTTONS
    # --------------------------------------------------------

    filter_buttons = [

        InlineKeyboardButton(
            "🔎 All",
            callback_data="filter_all"
        ),

        InlineKeyboardButton(
            "🖼 Photos",
            callback_data="filter_photos"
        ),

        InlineKeyboardButton(
            "🎬 Videos",
            callback_data="filter_videos"
        ),

        InlineKeyboardButton(
            "📄 Files",
            callback_data="filter_docs"
        ),

        InlineKeyboardButton(
            "🔗 Links",
            callback_data="filter_links"
        ),

        InlineKeyboardButton(
            "🎵 Music",
            callback_data="filter_music"
        ),

        InlineKeyboardButton(
            "🎤 Voice",
            callback_data="filter_voice"
        ),

        InlineKeyboardButton(
            "🎞 GIF",
            callback_data="filter_gif"
        )

    ]

    filter_rows = [

        filter_buttons[i:i + 2]

        for i in range(
            0,
            len(filter_buttons),
            2
        )

    ]

    # --------------------------------------------------------
    # PAGINATION
    # --------------------------------------------------------

    nav_buttons = []

    if page > 1:

        nav_buttons.append(
            InlineKeyboardButton(
                "⬅️ Previous",
                callback_data=(
                    f"search_{page - 1}"
                )
            )
        )

    if page < total_pages:

        nav_buttons.append(
            InlineKeyboardButton(
                "Next ➡️",
                callback_data=(
                    f"search_{page + 1}"
                )
            )
        )

    if nav_buttons:

        filter_rows.append(
            nav_buttons
        )

    # --------------------------------------------------------
    # BOTTOM
    # --------------------------------------------------------

    filter_rows.append(

        [

            InlineKeyboardButton(
                "🔄 New Search",
                callback_data="search"
            ),

            InlineKeyboardButton(
                "⬅️ Back",
                callback_data="more"
            )

        ]

    )

    markup = InlineKeyboardMarkup(
        filter_rows
    )

    # --------------------------------------------------------
    # SEND
    # --------------------------------------------------------

    if is_callback:

        await update.callback_query.edit_message_text(
            text,
            reply_markup=markup,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True
        )

    else:

        await update.effective_message.reply_text(
            text,
            reply_markup=markup,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True
        )


# ============================================================
# SEARCH PAGINATION
# ============================================================

async def handle_search_pagination(
    update,
    context,
    page
):

    query = update.callback_query

    await query.answer()

    items = context.user_data.get(
        "search_items"
    )

    if not items:

        await query.edit_message_text(
            "❌ Search session expired.\n"
            "Please search again."
        )

        return

    await render_search_page(
        update,
        context,
        page=page,
        is_callback=True
    )


# ============================================================
# STORIES
# ============================================================

async def display_story(
    update,
    context
):

    query = update.callback_query

    stories = context.user_data.get(
        "stories_list"
    )

    index = context.user_data.get(
        "story_index",
        0
    )

    if not stories:

        await query.edit_message_text(
            "No stories found."
        )

        return

    if index < 0:
        index = 0

    if index >= len(stories):
        index = len(stories) - 1

    context.user_data[
        "story_index"
    ] = index

    story = stories[index]

    kb = [

        [

            InlineKeyboardButton(
                "⬅️ Previous",
                callback_data="story_prev"
            ),

            InlineKeyboardButton(
                "Next ➡️",
                callback_data="story_next"
            )

        ],

        [

            InlineKeyboardButton(
                "⬅️ Back to Profile",
                callback_data="profile"
            )

        ]

    ]

    try:

        media_file = BytesIO()

        await telethon_client.download_media(
            story,
            file=media_file
        )

        media_file.seek(0)

        await query.edit_message_text(
            f"Story {index + 1}/"
            f"{len(stories)} "
            f"(media file)",
            reply_markup=InlineKeyboardMarkup(kb)
        )

    except Exception as e:

        await query.edit_message_text(
            f"Story {index + 1}/"
            f"{len(stories)}\n"
            f"Cannot download media: {e}",
            reply_markup=InlineKeyboardMarkup(kb)
        )


async def fetch_stories(
    update,
    context,
    entity
):

    try:

        stories = None

        if hasattr(
            telethon_client,
            "get_stories"
        ):

            try:

                stories = await telethon_client.get_stories(
                    entity
                )

            except Exception:

                stories = None

        if (
            not stories
            and hasattr(
                types,
                "GetStoriesRequest"
            )
        ):

            try:

                stories = await telethon_client(
                    types.GetStoriesRequest(
                        entity
                    )
                )

            except Exception:

                stories = None

        if not stories:

            await update.callback_query.edit_message_text(
                "No stories available "
                "for this user."
            )

            return

        context.user_data[
            "story_entity"
        ] = entity

        context.user_data[
            "stories_list"
        ] = stories

        context.user_data[
            "story_index"
        ] = 0

        await display_story(
            update,
            context
        )

    except Exception as e:

        await update.callback_query.edit_message_text(
            f"❌ Failed to fetch stories: {e}"
        )


# ============================================================
# WORD FREQUENCY
# ============================================================

async def perform_words_analysis(
    update,
    context,
    target,
    limit=20
):

    try:

        entity = await telethon_client.get_entity(
            target
        )

        messages = await telethon_client.get_messages(
            entity,
            limit=200
        )

        if not messages:

            await update.message.reply_text(
                "No messages found for this user."
            )

            return

        stop_words = {

            "the",
            "a",
            "an",
            "is",
            "are",
            "was",
            "were",
            "to",
            "of",
            "in",
            "on",
            "for",
            "and",
            "or",
            "but",
            "with",
            "at",
            "by",
            "from",
            "up",
            "about",
            "into",
            "through",
            "during",
            "before",
            "after",
            "above",
            "below",
            "can",
            "will",
            "just",
            "not",
            "you",
            "your",
            "i",
            "me",
            "my",
            "it",
            "its",
            "this",
            "that",
            "these",
            "those",
            "we",
            "our",
            "they",
            "them",
            "their",
            "be",
            "been",
            "being",
            "do",
            "does",
            "did",
            "doing",
            "have",
            "has",
            "had",
            "having",
            "he",
            "she",
            "his",
            "her",
            "him",
            "so",
            "if",
            "then",
            "than",
            "too",
            "very",
            "am",
            "as",
            "www",
            "http",
            "https",
            "t.me",
            "telegram"

        }

        word_data = {}

        for msg in messages:

            if getattr(
                msg,
                "message",
                None
            ):

                words = re.findall(
                    r"\b[a-zA-Z0-9_]+\b",
                    msg.message.lower()
                )

                for word in words:

                    if (
                        len(word) > 2
                        and word not in stop_words
                    ):

                        if word not in word_data:

                            word_data[word] = {
                                "count": 0,
                                "messages": set()
                            }

                        word_data[word][
                            "count"
                        ] += 1

                        word_data[word][
                            "messages"
                        ].add(
                            msg.id
                        )

        if not word_data:

            await update.message.reply_text(
                "No meaningful words found."
            )

            return

        sorted_words = sorted(
            word_data.items(),
            key=lambda x:
                x[1]["count"],
            reverse=True
        )[:limit]

        username = escape(
            str(
                getattr(
                    entity,
                    "username",
                    "N/A"
                )
                or "N/A"
            )
        )

        text = (
            f"<blockquote>"
            f"Word frequency for "
            f"@{username}:\n"
        )

        for word, data in sorted_words:

            text += (
                f"- {escape(word)}: "
                f"{data['count']}\n"
            )

        text += "</blockquote>"

        kb = [

            [

                InlineKeyboardButton(
                    "⬅️ Back",
                    callback_data="more"
                )

            ]

        ]

        await update.message.reply_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(kb)
        )

    except Exception as e:

        await update.message.reply_text(
            f"❌ Error: {e}"
        )


async def fetch_words(
    update,
    context,
    target
):

    context.user_data[
        "word_target"
    ] = target

    await perform_words_analysis(
        update,
        context,
        target,
        20
    )


# ============================================================
# FRIENDS
# ============================================================

async def fetch_friends(
    update,
    context,
    text
):

    parts = text.split()

    if len(parts) < 2:

        await update.message.reply_text(
            "⚠️ Use:\n"
            "@group @user"
        )

        return

    try:

        group_entity = await telethon_client.get_entity(
            parts[0]
        )

        target_user = await telethon_client.get_entity(
            parts[1]
        )

        messages = await telethon_client.get_messages(
            group_entity,
            limit=500
        )

        reply_data = {}

        for m in messages:

            if (
                getattr(
                    m,
                    "sender_id",
                    None
                )
                == target_user.id
                and getattr(
                    m,
                    "reply_to_msg_id",
                    None
                )
            ):

                try:

                    reply_to_msg = await telethon_client.get_messages(
                        group_entity,
                        ids=m.reply_to_msg_id
                    )

                    if (
                        reply_to_msg
                        and getattr(
                            reply_to_msg,
                            "sender_id",
                            None
                        )
                    ):

                        sender_id = (
                            reply_to_msg.sender_id
                        )

                        if sender_id not in reply_data:

                            reply_data[
                                sender_id
                            ] = {
                                "count": 0,
                                "date": str(
                                    m.date
                                )
                            }

                            try:

                                sender_entity = await telethon_client.get_entity(
                                    sender_id
                                )

                                reply_data[
                                    sender_id
                                ][
                                    "name"
                                ] = (
                                    f"{sender_entity.first_name} "
                                    f"{getattr(sender_entity, 'last_name', '')}"
                                )

                            except Exception:

                                reply_data[
                                    sender_id
                                ][
                                    "name"
                                ] = "Unknown"

                        reply_data[
                            sender_id
                        ][
                            "count"
                        ] += 1

                except Exception:

                    pass

        sorted_replies = sorted(
            reply_data.items(),
            key=lambda x:
                x[1]["count"],
            reverse=True
        )[:10]

        text_output = (
            "<blockquote>"
            "Replies in group:\n"
        )

        for sid, data in sorted_replies:

            text_output += (
                f"|{data['date'][:10]} - "
                f"{escape(data['name'])} "
                f"({data['count']})\n"
            )

        text_output += "</blockquote>"

        await update.message.reply_text(
            text_output,
            parse_mode=ParseMode.HTML
        )

    except Exception as e:

        await update.message.reply_text(
            f"❌ Error: {e}"
        )


# ============================================================
# NAMES
# ============================================================

async def fetch_names(
    update,
    context,
    target
):

    try:

        ent = await telethon_client.get_entity(
            target
        )

        save_user_history(
            ent.id,
            getattr(
                ent,
                "username",
                ""
            ),
            getattr(
                ent,
                "first_name",
                ""
            ),
            getattr(
                ent,
                "last_name",
                ""
            )
        )

        history = get_user_history(
            ent.id
        )

        name = escape(
            str(
                getattr(
                    ent,
                    "first_name",
                    ""
                )
                or ""
            )
        )

        username = escape(
            str(
                getattr(
                    ent,
                    "username",
                    "N/A"
                )
                or "N/A"
            )
        )

        text = (
            f"<blockquote>"
            f"Names history "
            f"{name} (@{username}):\n\n"
            f"usernames:\n"
        )

        if history:

            seen = set()

            for h in history:

                if (
                    h[0]
                    and h[0] not in seen
                ):

                    text += (
                        f"1. @{escape(str(h[0]))} "
                        f"[{h[3][:10]}]\n"
                    )

                    seen.add(
                        h[0]
                    )

        else:

            text += (
                "No history yet.\n"
            )

        text += (
            "\nfirst name / last name:\n"
        )

        if history:

            for h in history[:5]:

                text += (
                    f"|{h[3][:10]} "
                    f"-> "
                    f"{escape(str(h[1] or ''))} "
                    f"{escape(str(h[2] or ''))}\n"
                )

        else:

            text += (
                "No history yet.\n"
            )

        text += "</blockquote>"

        await update.message.reply_text(
            text,
            parse_mode=ParseMode.HTML
        )

    except Exception as e:

        await update.message.reply_text(
            f"❌ Error: {e}"
        )


# ============================================================
# MESSAGE HANDLER
# ============================================================

async def handle_link(
    update,
    context
):

    user_id = update.effective_user.id

    text = (
        update.message.text
        or ""
    ).strip()

    # ========================================================
    # PASSWORD
    # ========================================================

    if (
        context.user_data.get(
            "state"
        )
        == "awaiting_password"
    ):

        if text == BOT_PASSWORD:

            add_authenticated_user(
                user_id
            )

            context.user_data[
                "state"
            ] = None

            await update.message.reply_text(
                "✅ Access granted!"
            )

            keyboard = [

                [
                    InlineKeyboardButton(
                        "📥 Inbox",
                        callback_data="inbox"
                    ),

                    InlineKeyboardButton(
                        "👤 Profile",
                        callback_data="profile"
                    )
                ],

                [
                    InlineKeyboardButton(
                        "🔗 Fetch Telegram",
                        callback_data="fetch"
                    )
                ],

                [
                    InlineKeyboardButton(
                        "➕ More Commands",
                        callback_data="more"
                    )
                ]

            ]

            await update.message.reply_text(
                "🤖 TELEGRAM ASSISTANT",
                reply_markup=InlineKeyboardMarkup(
                    keyboard
                )
            )

        else:

            await update.message.reply_text(
                "❌ Incorrect password. "
                "Please try again."
            )

        return

    # ========================================================
    # AUTH
    # ========================================================

    if not is_authenticated(user_id):

        await update.message.reply_text(
            "🔐 Password required.\n"
            "Please run /start "
            "and authenticate first."
        )

        return

    # ========================================================
    # REPLY
    # ========================================================

    if context.user_data.get(
        "reply_to"
    ):

        target_chat = context.user_data[
            "reply_to"
        ]

        try:

            await telethon_client.send_message(
                target_chat,
                text
            )

            context.user_data[
                "reply_to"
            ] = None

            await update.message.reply_text(
                "✅ Reply sent!"
            )

        except Exception as e:

            await update.message.reply_text(
                f"❌ Failed: {e}"
            )

        return

    # ========================================================
    # STATE
    # ========================================================

    state = context.user_data.get(
        "state"
    )

    if state:

        context.user_data[
            "state"
        ] = None

        # ----------------------------------------------------
        # PROFILE
        # ----------------------------------------------------

        if state == "profile_query":

            await fetch_profile(
                update,
                context,
                text
            )

            return

        # ----------------------------------------------------
        # SEARCH
        # ----------------------------------------------------

        if state == "search":

            context.user_data[
                "search_query"
            ] = text

            # IMPORTANT:
            # This performs GLOBAL Telegram search.
            await fetch_search(
                update,
                context,
                text
            )

            return

        # ----------------------------------------------------
        # WORDS
        # ----------------------------------------------------

        if state == "words":

            await fetch_words(
                update,
                context,
                text
            )

            return

        # ----------------------------------------------------
        # FRIENDS
        # ----------------------------------------------------

        if state == "friends":

            await fetch_friends(
                update,
                context,
                text
            )

            return

        # ----------------------------------------------------
        # NAMES
        # ----------------------------------------------------

        if state == "names":

            await fetch_names(
                update,
                context,
                text
            )

            return

        # ----------------------------------------------------
        # FETCH LINK
        # ----------------------------------------------------

        if state == "fetch_link":

            username, msg_id = parse_tg_link(
                text
            )

            if not username:

                await update.message.reply_text(
                    "❌ Invalid Telegram link.\n\n"
                    "Example:\n"
                    "https://t.me/channel/123"
                )

                return

            try:

                entity = await telethon_client.get_entity(
                    username
                )

                if msg_id:

                    msg = await telethon_client.get_messages(
                        entity,
                        ids=msg_id
                    )

                    if msg:

                        await safe_send(
                            update.message.chat_id,
                            context.bot,
                            msg,
                            from_chat_id=entity.id,
                            message_id=msg.id
                        )

                    else:

                        await update.message.reply_text(
                            "❌ Message not found."
                        )

                else:

                    status_msg = await update.message.reply_text(
                        "⏳ Fetching latest 20 messages..."
                    )

                    messages = await telethon_client.get_messages(
                        entity,
                        limit=20
                    )

                    if not messages:

                        await status_msg.edit_text(
                            "❌ No messages found."
                        )

                        return

                    for idx, msg in enumerate(
                        messages,
                        1
                    ):

                        if idx % 5 == 0:

                            try:

                                await status_msg.edit_text(
                                    f"⏳ Fetching "
                                    f"{idx}/{len(messages)}..."
                                )

                            except Exception:

                                pass

                        await safe_send(
                            update.message.chat_id,
                            context.bot,
                            msg,
                            from_chat_id=entity.id,
                            message_id=msg.id
                        )

                    await status_msg.edit_text(
                        "✅ Batch complete!"
                    )

            except Exception as e:

                await handle_telethon_error(
                    update,
                    e
                )

            return

    # ========================================================
    # DIRECT TELEGRAM LINK
    # ========================================================

    if "t.me" in text:

        username, msg_id = parse_tg_link(
            text
        )

        if not username:

            await update.message.reply_text(
                "❌ Invalid Telegram link format."
            )

            return

        try:

            entity = await telethon_client.get_entity(
                username
            )

            if msg_id:

                msg = await telethon_client.get_messages(
                    entity,
                    ids=msg_id
                )

                if msg:

                    await safe_send(
                        update.message.chat_id,
                        context.bot,
                        msg,
                        from_chat_id=entity.id,
                        message_id=msg.id
                    )

                else:

                    await update.message.reply_text(
                        "❌ Message not found."
                    )

            else:

                status_msg = await update.message.reply_text(
                    "⏳ Fetching latest 20 messages..."
                )

                messages = await telethon_client.get_messages(
                    entity,
                    limit=20
                )

                if not messages:

                    await status_msg.edit_text(
                        "❌ No messages found."
                    )

                    return

                for idx, msg in enumerate(
                    messages,
                    1
                ):

                    if idx % 5 == 0:

                        try:

                            await status_msg.edit_text(
                                f"⏳ Fetching "
                                f"{idx}/{len(messages)}..."
                            )

                        except Exception:

                            pass

                    await safe_send(
                        update.message.chat_id,
                        context.bot,
                        msg,
                        from_chat_id=entity.id,
                        message_id=msg.id
                    )

                await status_msg.edit_text(
                    "✅ Batch complete!"
                )

        except Exception as e:

            await handle_telethon_error(
                update,
                e
            )

        return

    # ========================================================
    # DEFAULT
    # ========================================================

    await update.message.reply_text(
        "👋 Use the menu buttons, "
        "or send a Telegram link."
    )


# ============================================================
# INBOX LISTENER
# ============================================================

@telethon_client.on(
    events.NewMessage(
        incoming=True
    )
)
async def inbox_listener(event):

    if (
        event.is_private
        and not event.out
    ):

        try:

            sender = await event.get_sender()

            add_inbox_message(

                event.chat_id,

                sender.id,

                getattr(
                    sender,
                    "first_name",
                    "Unknown"
                ),

                getattr(
                    sender,
                    "username",
                    "N/A"
                ),

                (
                    event.raw_text
                    if getattr(
                        event,
                        "raw_text",
                        None
                    )
                    else ""
                ),

                get_media_type(
                    event
                ),

                str(
                    event.date
                )
            )

        except Exception as e:

            print(
                f"Inbox Error: {e}"
            )


# ============================================================
# MAIN
# ============================================================

async def main():

    init_db()

    # --------------------------------------------------------
    # TELETHON
    # --------------------------------------------------------

    try:

        await telethon_client.start()

        print(
            "Telethon connected!"
        )

    except Exception as e:

        print(
            f"Telethon fail: {e}"
        )

        return

    # --------------------------------------------------------
    # PYTHON TELEGRAM BOT
    # --------------------------------------------------------

    bot_app = (
        Application
        .builder()
        .token(BOT_TOKEN)
        .build()
    )

    bot_app.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    bot_app.add_handler(
        CommandHandler(
            "logout",
            logout
        )
    )

    bot_app.add_handler(
        CallbackQueryHandler(
            menu_callback
        )
    )

    bot_app.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND,
            handle_link
        )
    )

    print(
        "Bot running..."
    )

    # --------------------------------------------------------
    # START APPLICATION
    # --------------------------------------------------------

    await bot_app.initialize()

    await bot_app.start()

    try:

        await bot_app.updater.start_polling()

    except Exception:

        await bot_app.start_polling()

    # --------------------------------------------------------
    # FLASK
    # --------------------------------------------------------

    threading.Thread(
        target=lambda:
            app.run(
                host="0.0.0.0",
                port=10000
            ),
        daemon=True
    ).start()

    # --------------------------------------------------------
    # KEEP RUNNING
    # --------------------------------------------------------

    await asyncio.Event().wait()


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    asyncio.run(
        main()
    )
