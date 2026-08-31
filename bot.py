import re
import asyncio
import os
import threading
import sqlite3
import html
from io import BytesIO
from datetime import datetime

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
    ChannelPrivateError,
    MessageIdInvalidError,
)


# ============================================================
# FLASK
# ============================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "Telegram Search Bot is alive!"


# ============================================================
# CONFIGURATION
# ============================================================

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
STRING_SESSION = os.environ.get("STRING_SESSION", "")
BOT_PASSWORD = os.environ.get("BOT_PASSWORD", "ptss25")

DB_PATH = "bot_data.db"


if not BOT_TOKEN:
    print("WARNING: BOT_TOKEN is missing")

if not API_ID:
    print("WARNING: API_ID is missing")

if not API_HASH:
    print("WARNING: API_HASH is missing")

if not STRING_SESSION:
    print("WARNING: STRING_SESSION is missing")


# ============================================================
# TELETHON USER CLIENT
# ============================================================

telethon_client = TelegramClient(
    StringSession(STRING_SESSION),
    API_ID,
    API_HASH,
)


# ============================================================
# DATABASE
# ============================================================

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY
        )
    """)

    cursor.execute("""
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

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tracking (
            target_id INTEGER PRIMARY KEY,
            username TEXT
        )
    """)

    cursor.execute("""
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
    cursor = conn.cursor()

    cursor.execute(
        "SELECT user_id FROM users WHERE user_id = ?",
        (user_id,)
    )

    result = cursor.fetchone()

    conn.close()

    return result is not None


def add_authenticated_user(user_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute(
        "INSERT OR IGNORE INTO users (user_id) VALUES (?)",
        (user_id,)
    )

    conn.commit()
    conn.close()


def remove_authenticated_user(user_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute(
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
    date,
):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
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
    """, (
        chat_id,
        sender_id,
        name,
        username,
        text,
        media_type,
        date,
    ))

    conn.commit()
    conn.close()


def get_inbox_conversations():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
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
    """)

    rows = cursor.fetchall()

    conn.close()

    return rows


def save_user_history(
    user_id,
    username,
    first_name,
    last_name,
):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO user_history
        (
            user_id,
            username,
            first_name,
            last_name,
            date
        )
        VALUES (?, ?, ?, ?, ?)
    """, (
        user_id,
        username,
        first_name,
        last_name,
        str(datetime.now()),
    ))

    conn.commit()
    conn.close()


def get_user_history(user_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            username,
            first_name,
            last_name,
            date
        FROM user_history
        WHERE user_id = ?
        ORDER BY date DESC
    """, (user_id,))

    rows = cursor.fetchall()

    conn.close()

    return rows


# ============================================================
# GENERAL HELPERS
# ============================================================

def get_media_type(message):
    if getattr(message, "photo", None):
        return "📷"

    if getattr(message, "video", None):
        return "🎬"

    if getattr(message, "document", None):
        return "📄"

    if getattr(message, "audio", None):
        return "🎵"

    if getattr(message, "voice", None):
        return "🎤"

    if getattr(message, "sticker", None):
        return "🧩"

    if getattr(message, "gif", None):
        return "🎞️"

    return "💬"


def parse_tg_link(text):

    pattern = r"https?://t\.me/([a-zA-Z0-9_]+)/(\d+)"

    match = re.search(pattern, text)

    if match:
        return match.group(1), int(match.group(2))

    pattern2 = r"https?://t\.me/([a-zA-Z0-9_]+)"

    match = re.search(pattern2, text)

    if match:
        return match.group(1), None

    return None, None


def extract_chat_id_from_message(message):

    chat_id = getattr(message, "chat_id", None)

    if chat_id is not None:
        try:
            return int(chat_id)
        except Exception:
            pass

    peer = (
        getattr(message, "peer_id", None)
        or getattr(message, "to_id", None)
        or getattr(message, "from_id", None)
    )

    if peer is None:
        return None

    try:

        if isinstance(peer, types.PeerChannel):
            return int(peer.channel_id)

        if isinstance(peer, types.PeerChat):
            return int(peer.chat_id)

        if isinstance(peer, types.PeerUser):
            return int(peer.user_id)

    except Exception:
        pass

    for attr in (
        "channel_id",
        "chat_id",
        "user_id",
    ):

        value = getattr(peer, attr, None)

        if value is not None:
            try:
                return int(value)
            except Exception:
                pass

    return None


# ============================================================
# TELEGRAM MESSAGE LINK
# ============================================================

def build_message_link(chat, message_id):

    if not chat:
        return None

    username = getattr(chat, "username", None)

    if username:
        return f"https://t.me/{username}/{message_id}"

    # Private / non-public chats.
    # /c/ links are not useful to users who are not members.
    return None


# ============================================================
# SAFE MESSAGE SENDER
# ============================================================

async def safe_send(
    chat_id,
    bot,
    message,
    from_chat_id=None,
    message_id=None,
):

    text = getattr(message, "message", None)

    try:

        if (
            getattr(message, "photo", None)
            or getattr(message, "video", None)
            or getattr(message, "document", None)
            or getattr(message, "voice", None)
            or getattr(message, "audio", None)
            or getattr(message, "gif", None)
        ):

            media_bytes = BytesIO()

            await telethon_client.download_media(
                message,
                file=media_bytes,
            )

            media_bytes.seek(0)

            if getattr(message, "photo", None):

                await bot.send_photo(
                    chat_id,
                    photo=media_bytes,
                    caption=text or "",
                )

            elif getattr(message, "video", None):

                await bot.send_video(
                    chat_id,
                    video=media_bytes,
                    caption=text or "",
                )

            elif getattr(message, "document", None):

                await bot.send_document(
                    chat_id,
                    document=media_bytes,
                    caption=text or "",
                )

            elif getattr(message, "voice", None):

                await bot.send_voice(
                    chat_id,
                    voice=media_bytes,
                    caption=text or "",
                )

            elif getattr(message, "audio", None):

                await bot.send_audio(
                    chat_id,
                    audio=media_bytes,
                    caption=text or "",
                )

            elif getattr(message, "gif", None):

                await bot.send_animation(
                    chat_id,
                    animation=media_bytes,
                    caption=text or "",
                )

        elif getattr(message, "sticker", None):

            sticker_bytes = BytesIO()

            await telethon_client.download_media(
                message,
                file=sticker_bytes,
            )

            sticker_bytes.seek(0)

            await bot.send_sticker(
                chat_id,
                sticker=sticker_bytes,
            )

        elif text:

            await bot.send_message(
                chat_id,
                text=text,
            )

        else:

            await bot.send_message(
                chat_id,
                "⚠️ Unsupported Telegram message type.",
            )

    except Exception as e:

        error_text = str(e).lower()

        if "must forward even restricted" in error_text:

            await bot.send_message(
                chat_id,
                "🔒 This media has Telegram content restrictions."
            )

        else:

            await bot.send_message(
                chat_id,
                f"❌ Failed to fetch message:\n{e}"
            )


# ============================================================
# ERROR HANDLER
# ============================================================

async def handle_telethon_error(update, error):

    if isinstance(error, FloodWaitError):

        await update.message.reply_text(
            f"⚠️ Telegram rate limit.\n\n"
            f"Please wait {error.seconds} seconds."
        )

    elif isinstance(error, ChannelPrivateError):

        await update.message.reply_text(
            "🔒 This channel/group is private "
            "or the logged-in account does not have access."
        )

    elif isinstance(error, UsernameNotOccupiedError):

        await update.message.reply_text(
            "❌ Telegram username was not found."
        )

    elif isinstance(error, MessageIdInvalidError):

        await update.message.reply_text(
            "❌ Invalid message ID."
        )

    else:

        await update.message.reply_text(
            f"❌ Telegram error:\n{error}"
        )


# ============================================================
# MAIN MENU
# ============================================================

def main_keyboard():

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "📥 Inbox",
                callback_data="inbox"
            ),
            InlineKeyboardButton(
                "👤 Profile",
                callback_data="profile"
            ),
        ],
        [
            InlineKeyboardButton(
                "🔗 Fetch Telegram",
                callback_data="fetch"
            ),
        ],
        [
            InlineKeyboardButton(
                "➕ More Commands",
                callback_data="more"
            ),
        ],
    ])


def more_keyboard():

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🔎 Search",
                callback_data="search"
            ),
            InlineKeyboardButton(
                "📊 Statistics",
                callback_data="stats"
            ),
        ],
        [
            InlineKeyboardButton(
                "🔔 Track",
                callback_data="track"
            ),
            InlineKeyboardButton(
                "🔗 Names",
                callback_data="names"
            ),
        ],
        [
            InlineKeyboardButton(
                "👥 Groups",
                callback_data="groups"
            ),
            InlineKeyboardButton(
                "💬 Messages",
                callback_data="messages"
            ),
        ],
        [
            InlineKeyboardButton(
                "🔎 Analysis",
                callback_data="analysis"
            ),
            InlineKeyboardButton(
                "📢 Channels",
                callback_data="channels"
            ),
        ],
        [
            InlineKeyboardButton(
                "👍 Reputation",
                callback_data="rep"
            ),
            InlineKeyboardButton(
                "👥 Friends",
                callback_data="friends"
            ),
        ],
        [
            InlineKeyboardButton(
                "🔄 Reactions",
                callback_data="reactions"
            ),
            InlineKeyboardButton(
                "🎁 Gifts",
                callback_data="gifts"
            ),
        ],
        [
            InlineKeyboardButton(
                "📤 Share",
                callback_data="share"
            ),
            InlineKeyboardButton(
                "🔵 Words Frequency",
                callback_data="words"
            ),
        ],
        [
            InlineKeyboardButton(
                "👥 Common Groups",
                callback_data="common"
            ),
        ],
        [
            InlineKeyboardButton(
                "⬅️ Back",
                callback_data="main_menu"
            ),
        ],
    ])


# ============================================================
# START
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    user_id = update.effective_user.id

    if not is_authenticated(user_id):

        context.user_data["state"] = "awaiting_password"

        await update.message.reply_text(
            "🔐 TELEGRAM ASSISTANT\n\n"
            "Password required.\n\n"
            "Enter the password to continue."
        )

        return

    await update.message.reply_text(
        "🤖 TELEGRAM ASSISTANT\n\n"
        "Choose an option:",
        reply_markup=main_keyboard(),
    )


async def logout(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    remove_authenticated_user(
        update.effective_user.id
    )

    await update.message.reply_text(
        "🔒 Logged out."
    )


# ============================================================
# SEARCH UI
# ============================================================

def search_filter_keyboard():

    return [
        [
            InlineKeyboardButton(
                "🔎 All",
                callback_data="filter_all"
            ),
            InlineKeyboardButton(
                "📝 Posts",
                callback_data="filter_posts"
            ),
            InlineKeyboardButton(
                "📷 Photos",
                callback_data="filter_photos"
            ),
        ],
        [
            InlineKeyboardButton(
                "🎬 Videos",
                callback_data="filter_videos"
            ),
            InlineKeyboardButton(
                "📄 Documents",
                callback_data="filter_docs"
            ),
            InlineKeyboardButton(
                "🎵 Audio",
                callback_data="filter_audio"
            ),
        ],
        [
            InlineKeyboardButton(
                "🎤 Voice",
                callback_data="filter_voice"
            ),
            InlineKeyboardButton(
                "🔗 Links",
                callback_data="filter_links"
            ),
        ],
    ]


def search_navigation_keyboard(
    page,
    total_pages,
):

    buttons = []

    if page > 1:

        buttons.append(
            InlineKeyboardButton(
                "⬅️ Previous",
                callback_data=f"searchpage_{page - 1}"
            )
        )

    if page < total_pages:

        buttons.append(
            InlineKeyboardButton(
                "Next ➡️",
                callback_data=f"searchpage_{page + 1}"
            )
        )

    rows = []

    if buttons:
        rows.append(buttons)

    rows.append([
        InlineKeyboardButton(
            "🔄 New Search",
            callback_data="search"
        ),
        InlineKeyboardButton(
            "⬅️ Back",
            callback_data="more"
        ),
    ])

    return rows


# ============================================================
# GLOBAL SEARCH
# ============================================================

async def perform_global_search(
    query_text,
    filter_type=None,
):

    """
    IMPORTANT:

    This uses the Telethon USER ACCOUNT represented
    by STRING_SESSION.

    It is NOT using the Bot API account for global search.
    """

    results = []

    # --------------------------------------------------------
    # METHOD 1
    # Telegram global search
    # --------------------------------------------------------

    try:

        request = functions.messages.SearchGlobalRequest(
            q=query_text,
            filter=(
                filter_type
                if filter_type is not None
                else types.InputMessagesFilterEmpty()
            ),
            min_date=None,
            max_date=None,
            offset_rate=0,
            offset_peer=types.InputPeerEmpty(),
            offset_id=0,
            limit=100,
        )

        result = await telethon_client(request)

        chat_map = {}

        for chat in getattr(
            result,
            "chats",
            []
        ):

            chat_id = getattr(
                chat,
                "id",
                None
            )

            if chat_id is not None:

                chat_map[int(chat_id)] = chat

        for message in getattr(
            result,
            "messages",
            []
        ):

            chat_id = extract_chat_id_from_message(
                message
            )

            chat = (
                chat_map.get(chat_id)
                if chat_id is not None
                else None
            )

            results.append({
                "message": message,
                "chat": chat,
                "source": "global",
            })

    except FloodWaitError:

        raise

    except Exception as e:

        print(
            "Global search error:",
            repr(e)
        )


    # --------------------------------------------------------
    # METHOD 2
    # Public channel global search
    # --------------------------------------------------------
    #
    # Telegram provides channels.searchPosts specifically
    # for public channel posts, including channels where
    # the user is NOT a member.
    #
    # Some accounts/queries may be subject to Telegram's
    # search limits or Premium requirements.
    # --------------------------------------------------------

    try:

        SearchPostsRequest = getattr(
            functions.channels,
            "SearchPostsRequest",
            None
        )

        if SearchPostsRequest:

            channel_result = await telethon_client(
                SearchPostsRequest(
                    query=query_text,
                    hashtag=None,
                    offset_rate=0,
                    offset_peer=types.InputPeerEmpty(),
                    offset_id=0,
                    limit=100,
                )
            )

            chat_map = {}

            for chat in getattr(
                channel_result,
                "chats",
                []
            ):

                chat_id = getattr(
                    chat,
                    "id",
                    None
                )

                if chat_id is not None:
                    chat_map[int(chat_id)] = chat

            for message in getattr(
                channel_result,
                "messages",
                []
            ):

                chat_id = extract_chat_id_from_message(
                    message
                )

                chat = (
                    chat_map.get(chat_id)
                    if chat_id is not None
                    else None
                )

                results.append({
                    "message": message,
                    "chat": chat,
                    "source": "public_channel",
                })

    except Exception as e:

        # Do NOT destroy the normal global search
        # if channels.searchPosts is unavailable.
        print(
            "Public channel search unavailable:",
            repr(e)
        )


    # --------------------------------------------------------
    # Deduplicate
    # --------------------------------------------------------

    unique = {}

    for item in results:

        message = item["message"]
        chat = item["chat"]

        message_id = getattr(
            message,
            "id",
            None
        )

        chat_id = getattr(
            chat,
            "id",
            None
        )

        key = (
            chat_id,
            message_id
        )

        if key not in unique:

            unique[key] = item

    results = list(unique.values())

    # Newest first
    results.sort(
        key=lambda item: (
            getattr(
                item["message"],
                "date",
                datetime.min
            )
            or datetime.min
        ),
        reverse=True,
    )

    return results


# ============================================================
# RESULT FORMATTER
# ============================================================

def result_source_name(chat):

    if not chat:
        return "Unknown source"

    title = getattr(
        chat,
        "title",
        None
    )

    username = getattr(
        chat,
        "username",
        None
    )

    if title and username:

        return f"{title} (@{username})"

    if title:
        return title

    if username:
        return f"@{username}"

    return "Telegram"


def result_snippet(message):

    text = getattr(
        message,
        "message",
        None
    )

    if not text:

        return "[Media / attachment]"

    text = re.sub(
        r"\s+",
        " ",
        text
    ).strip()

    if len(text) > 90:

        return text[:87] + "..."

    return text


def build_result_button(
    item,
    number,
):

    message = item["message"]
    chat = item["chat"]

    source = result_source_name(
        chat
    )

    media_icon = get_media_type(
        message
    )

    snippet = result_snippet(
        message
    )

    link = build_message_link(
        chat,
        message.id
    )

    # --------------------------------------------------------
    # THIS IS THE IMPORTANT FIX.
    #
    # The result itself becomes a URL button.
    #
    # Clicking it opens:
    #
    #     t.me/channel/message_id
    #
    # instead of merely opening the channel.
    # --------------------------------------------------------

    if link:

        button_text = (
            f"{number}. {media_icon} "
            f"{source}: {snippet}"
        )

        # Telegram button text should not become enormous.
        if len(button_text) > 100:

            button_text = (
                button_text[:97]
                + "..."
            )

        return InlineKeyboardButton(
            button_text,
            url=link,
        )

    # No public username means Telegram cannot
    # provide a universally usable public message URL.
    return InlineKeyboardButton(
        f"{number}. {media_icon} {source}: {snippet}",
        callback_data="no_public_link",
    )


# ============================================================
# DISPLAY SEARCH RESULTS
# ============================================================

async def display_search_results(
    update,
    context,
    page=1,
    is_callback=False,
):

    results = context.user_data.get(
        "search_results",
        []
    )

    query_text = context.user_data.get(
        "search_query",
        ""
    )

    if not results:

        text = (
            f"🔎 SEARCH\n\n"
            f"Query: <b>{html.escape(query_text)}</b>\n\n"
            f"❌ No results found.\n\n"
            f"Try another keyword."
        )

        keyboard = [
            [
                InlineKeyboardButton(
                    "🔄 New Search",
                    callback_data="search"
                ),
                InlineKeyboardButton(
                    "⬅️ Back",
                    callback_data="more"
                ),
            ]
        ]

        markup = InlineKeyboardMarkup(
            keyboard
        )

        if is_callback:

            await update.callback_query.edit_message_text(
                text,
                parse_mode=ParseMode.HTML,
                reply_markup=markup,
            )

        else:

            await update.message.reply_text(
                text,
                parse_mode=ParseMode.HTML,
                reply_markup=markup,
            )

        return


    # Five results per page
    per_page = 5

    total = len(results)

    total_pages = max(
        1,
        (total + per_page - 1) // per_page
    )

    page = max(
        1,
        min(page, total_pages)
    )

    start = (
        page - 1
    ) * per_page

    end = min(
        start + per_page,
        total
    )

    page_results = results[
        start:end
    ]


    # --------------------------------------------------------
    # HEADER
    # --------------------------------------------------------

    text = (
        "🔎 <b>TELEGRAM GLOBAL SEARCH</b>\n\n"
        f"🔍 Query: <code>{html.escape(query_text)}</code>\n"
        f"📊 Results: {total}\n"
        f"📄 Page: {page}/{total_pages}\n\n"
        "👇 <b>Tap a result to open the exact Telegram message:</b>"
    )


    keyboard = []

    # --------------------------------------------------------
    # RESULT BUTTONS
    # --------------------------------------------------------

    for index, item in enumerate(
        page_results,
        start=start + 1
    ):

        button = build_result_button(
            item,
            index
        )

        keyboard.append([
            button
        ])


    # --------------------------------------------------------
    # FILTER BUTTONS
    # --------------------------------------------------------

    keyboard.extend(
        search_filter_keyboard()
    )


    # --------------------------------------------------------
    # PAGINATION
    # --------------------------------------------------------

    keyboard.extend(
        search_navigation_keyboard(
            page,
            total_pages,
        )
    )


    markup = InlineKeyboardMarkup(
        keyboard
    )


    if is_callback:

        await update.callback_query.edit_message_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=markup,
            disable_web_page_preview=True,
        )

    else:

        await update.message.reply_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=markup,
            disable_web_page_preview=True,
        )


# ============================================================
# SEARCH COMMAND
# ============================================================

async def fetch_search(
    update,
    context,
    text,
    filter_type=None,
    is_callback=False,
):

    text = text.strip()

    if not text:

        if is_callback:

            await update.callback_query.edit_message_text(
                "❌ Search query cannot be empty."
            )

        else:

            await update.message.reply_text(
                "❌ Search query cannot be empty."
            )

        return


    # --------------------------------------------------------
    # STATUS
    # --------------------------------------------------------

    if is_callback:

        try:

            await update.callback_query.edit_message_text(
                f"🔎 Searching Telegram globally...\n\n"
                f"Query: <b>{html.escape(text)}</b>\n\n"
                f"🌍 Searching public/global results...",
                parse_mode=ParseMode.HTML,
            )

        except Exception:
            pass

    else:

        status = await update.message.reply_text(
            f"🔎 Searching Telegram globally...\n\n"
            f"Query: {text}\n\n"
            f"🌍 Searching public/global results..."
        )


    try:

        results = await perform_global_search(
            text,
            filter_type,
        )

        context.user_data[
            "search_results"
        ] = results

        context.user_data[
            "search_query"
        ] = text

        context.user_data[
            "search_filter"
        ] = filter_type

        await display_search_results(
            update,
            context,
            page=1,
            is_callback=is_callback,
        )

    except FloodWaitError as e:

        message = (
            f"⏳ Telegram requested a cooldown.\n\n"
            f"Please wait {e.seconds} seconds."
        )

        if is_callback:

            await update.callback_query.edit_message_text(
                message
            )

        else:

            await update.message.reply_text(
                message
            )

    except Exception as e:

        print(
            "SEARCH ERROR:",
            repr(e)
        )

        message = (
            "❌ <b>Search failed</b>\n\n"
            f"<code>{html.escape(str(e))}</code>\n\n"
            "Check your Telethon user session."
        )

        if is_callback:

            await update.callback_query.edit_message_text(
                message,
                parse_mode=ParseMode.HTML,
            )

        else:

            await update.message.reply_text(
                message,
                parse_mode=ParseMode.HTML,
            )


# ============================================================
# PROFILE
# ============================================================

async def resolve_entity(
    context,
    target,
):

    try:

        entity = await telethon_client.get_entity(
            target
        )

        return entity

    except Exception:

        pass

    return None


async def fetch_profile(
    update,
    context,
    target,
):

    try:

        entity = await resolve_entity(
            context,
            target
        )

        if entity is None:

            await update.message.reply_text(
                "❌ Could not resolve this Telegram target."
            )

            return

        context.user_data[
            "profile_entity"
        ] = entity

        first_name = (
            getattr(entity, "first_name", "")
            or ""
        )

        last_name = (
            getattr(entity, "last_name", "")
            or ""
        )

        username = (
            getattr(entity, "username", None)
            or "N/A"
        )

        user_id = getattr(
            entity,
            "id",
            "N/A"
        )

        about = (
            getattr(entity, "about", None)
            or "No bio"
        )

        verified = getattr(
            entity,
            "verified",
            False
        )

        premium = getattr(
            entity,
            "premium",
            False
        )

        text = (
            "👤 <b>PROFILE</b>\n\n"
            f"Name: {html.escape(first_name)} "
            f"{html.escape(last_name)}\n"
            f"Username: @{html.escape(str(username))}\n"
            f"ID: <code>{user_id}</code>\n\n"
            f"Bio: {html.escape(about)}\n\n"
            f"Verified: {verified}\n"
            f"Premium: {premium}"
        )

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "📰 View Posts",
                    callback_data="posts_1"
                ),
            ],
            [
                InlineKeyboardButton(
                    "⬅️ Back",
                    callback_data="more"
                ),
            ],
        ])

        await update.message.reply_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
        )

    except Exception as e:

        await update.message.reply_text(
            f"❌ Profile error:\n{e}"
        )


# ============================================================
# POSTS
# ============================================================

async def fetch_profile_posts(
    update,
    context,
    page=1,
):

    entity = context.user_data.get(
        "profile_entity"
    )

    if not entity:

        await update.callback_query.edit_message_text(
            "❌ No profile loaded."
        )

        return

    try:

        messages = await telethon_client.get_messages(
            entity,
            limit=50,
        )

        messages = list(messages)

        if not messages:

            await update.callback_query.edit_message_text(
                "❌ No posts found."
            )

            return

        per_page = 5

        total = len(messages)

        total_pages = max(
            1,
            (total + per_page - 1) // per_page
        )

        page = max(
            1,
            min(page, total_pages)
        )

        start = (
            page - 1
        ) * per_page

        end = min(
            start + per_page,
            total
        )

        selected = messages[
            start:end
        ]

        text = (
            "📰 <b>POSTS</b>\n\n"
            f"Page {page}/{total_pages}\n\n"
        )

        keyboard = []

        for index, message in enumerate(
            selected,
            start=start + 1
        ):

            content = result_snippet(
                message
            )

            media = get_media_type(
                message
            )

            username = getattr(
                entity,
                "username",
                None
            )

            if username:

                link = (
                    f"https://t.me/"
                    f"{username}/"
                    f"{message.id}"
                )

                keyboard.append([
                    InlineKeyboardButton(
                        f"{index}. {media} {content}",
                        url=link,
                    )
                ])

            else:

                text += (
                    f"{index}. "
                    f"{media} "
                    f"{html.escape(content)}\n\n"
                )

        nav = []

        if page > 1:

            nav.append(
                InlineKeyboardButton(
                    "⬅️ Previous",
                    callback_data=f"posts_{page - 1}"
                )
            )

        if page < total_pages:

            nav.append(
                InlineKeyboardButton(
                    "Next ➡️",
                    callback_data=f"posts_{page + 1}"
                )
            )

        if nav:

            keyboard.append(nav)

        keyboard.append([
            InlineKeyboardButton(
                "⬅️ Back",
                callback_data="profile"
            )
        ])

        await update.callback_query.edit_message_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                keyboard
            ),
            disable_web_page_preview=True,
        )

    except Exception as e:

        await update.callback_query.edit_message_text(
            f"❌ Failed to load posts:\n{e}"
        )


# ============================================================
# MENU CALLBACK
# ============================================================

async def menu_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await query.answer()

    user_id = update.effective_user.id

    if not is_authenticated(user_id):

        await query.edit_message_text(
            "🔐 Authentication required.\n"
            "Use /start."
        )

        return

    data = query.data


    # --------------------------------------------------------
    # MAIN MENU
    # --------------------------------------------------------

    if data == "main_menu":

        await query.edit_message_text(
            "🤖 <b>TELEGRAM ASSISTANT</b>\n\n"
            "Choose an option:",
            parse_mode=ParseMode.HTML,
            reply_markup=main_keyboard(),
        )

        return


    # --------------------------------------------------------
    # INBOX
    # --------------------------------------------------------

    if data == "inbox":

        rows = get_inbox_conversations()

        if not rows:

            await query.edit_message_text(
                "📥 <b>INBOX</b>\n\n"
                "No private messages found.",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "⬅️ Back",
                            callback_data="main_menu"
                        )
                    ]
                ]),
            )

            return

        text = "📥 <b>INBOX</b>\n\n"

        keyboard = []

        seen = set()

        for row in rows:

            (
                chat_id,
                sender_id,
                name,
                username,
                msg_text,
                media,
                date,
            ) = row

            if chat_id in seen:
                continue

            seen.add(chat_id)

            text += (
                f"👤 {html.escape(name or 'Unknown')}\n"
                f"@{html.escape(username or 'N/A')}\n"
                f"{html.escape(msg_text or '[' + media + ']')}\n\n"
            )

            keyboard.append([
                InlineKeyboardButton(
                    f"💬 {name or 'User'}",
                    callback_data=f"conv_{chat_id}"
                )
            ])

        keyboard.append([
            InlineKeyboardButton(
                "⬅️ Back",
                callback_data="main_menu"
            )
        ])

        await query.edit_message_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                keyboard
            ),
        )

        return


    # --------------------------------------------------------
    # CONVERSATION
    # --------------------------------------------------------

    if data.startswith("conv_"):

        try:

            chat_id = int(
                data.split("_", 1)[1]
            )

        except Exception:

            await query.edit_message_text(
                "❌ Invalid conversation."
            )

            return

        rows = get_inbox_conversations()

        text = "💬 <b>CHAT</b>\n\n"

        for row in reversed(rows):

            if row[0] == chat_id:

                text += (
                    f"{html.escape(row[3] or 'User')}:\n"
                    f"{html.escape(row[4] or '[' + row[5] + ']')}\n\n"
                )

        await query.edit_message_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "⬅️ Back",
                        callback_data="inbox"
                    )
                ]
            ]),
        )

        return


    # --------------------------------------------------------
    # PROFILE
    # --------------------------------------------------------

    if data == "profile":

        entity = context.user_data.get(
            "profile_entity"
        )

        if entity:

            first_name = getattr(
                entity,
                "first_name",
                ""
            ) or ""

            last_name = getattr(
                entity,
                "last_name",
                ""
            ) or ""

            username = getattr(
                entity,
                "username",
                None
            ) or "N/A"

            text = (
                "👤 <b>PROFILE</b>\n\n"
                f"{html.escape(first_name)} "
                f"{html.escape(last_name)}\n"
                f"@{html.escape(str(username))}\n\n"
                f"ID: <code>{getattr(entity, 'id', 'N/A')}</code>"
            )

            await query.edit_message_text(
                text,
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "📰 View Posts",
                            callback_data="posts_1"
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "⬅️ Back",
                            callback_data="more"
                        )
                    ]
                ]),
            )

        else:

            context.user_data[
                "state"
            ] = "profile_query"

            await query.edit_message_text(
                "👤 <b>PROFILE</b>\n\n"
                "Send a Telegram username.\n\n"
                "Example:\n"
                "<code>@username</code>",
                parse_mode=ParseMode.HTML,
            )

        return


    # --------------------------------------------------------
    # POSTS
    # --------------------------------------------------------

    if data.startswith("posts_"):

        try:

            page = int(
                data.split("_", 1)[1]
            )

        except Exception:

            page = 1

        await fetch_profile_posts(
            update,
            context,
            page
        )

        return


    # --------------------------------------------------------
    # FETCH
    # --------------------------------------------------------

    if data == "fetch":

        context.user_data[
            "state"
        ] = "fetch_link"

        await query.edit_message_text(
            "🔗 <b>FETCH TELEGRAM</b>\n\n"
            "Send a Telegram message link.\n\n"
            "Example:\n"
            "<code>https://t.me/channel/123</code>",
            parse_mode=ParseMode.HTML,
        )

        return


    # --------------------------------------------------------
    # MORE
    # --------------------------------------------------------

    if data == "more":

        await query.edit_message_text(
            "➕ <b>MORE COMMANDS</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=more_keyboard(),
        )

        return


    # --------------------------------------------------------
    # SEARCH
    # --------------------------------------------------------

    if data == "search":

        context.user_data[
            "state"
        ] = "search"

        await query.edit_message_text(
            "🔎 <b>GLOBAL TELEGRAM SEARCH</b>\n\n"
            "Enter any keyword.\n\n"
            "Examples:\n"
            "• <code>BDU final exam</code>\n"
            "• <code>python</code>\n"
            "• <code>chemistry</code>\n"
            "• <code>Wachemo University</code>\n\n"
            "🌍 This searches using your Telegram user session, "
            "not only the channels you joined.",
            parse_mode=ParseMode.HTML,
        )

        return


    # --------------------------------------------------------
    # SEARCH PAGINATION
    # --------------------------------------------------------

    if data.startswith("searchpage_"):

        try:

            page = int(
                data.split("_", 1)[1]
            )

        except Exception:

            page = 1

        await display_search_results(
            update,
            context,
            page=page,
            is_callback=True,
        )

        return


    # --------------------------------------------------------
    # SEARCH FILTER
    # --------------------------------------------------------

    if data.startswith("filter_"):

        filter_name = data.split(
            "_",
            1
        )[1]

        filter_map = {

            "all":
                types.InputMessagesFilterEmpty,

            "posts":
                types.InputMessagesFilterEmpty,

            "photos":
                types.InputMessagesFilterPhotos,

            "videos":
                types.InputMessagesFilterVideo,

            "docs":
                types.InputMessagesFilterDocument,

            "audio":
                types.InputMessagesFilterAudio,

            "voice":
                types.InputMessagesFilterVoice,

            "links":
                types.InputMessagesFilterUrl,
        }

        filter_class = filter_map.get(
            filter_name,
            types.InputMessagesFilterEmpty
        )

        filter_type = filter_class()

        query_text = context.user_data.get(
            "search_query"
        )

        if not query_text:

            await query.edit_message_text(
                "❌ Search query expired.\n"
                "Please start a new search."
            )

            return

        await fetch_search(
            update,
            context,
            query_text,
            filter_type=filter_type,
            is_callback=True,
        )

        return


    # --------------------------------------------------------
    # OTHER FEATURES
    # --------------------------------------------------------

    if data in {
        "stats",
        "track",
        "names",
        "groups",
        "messages",
        "analysis",
        "channels",
        "rep",
        "friends",
        "reactions",
        "gifts",
        "share",
        "words",
        "common",
    }:

        prompts = {

            "stats":
                "📊 STATISTICS\n\n"
                "Send a Telegram username or link.",

            "track":
                "🔔 TRACK\n\n"
                "Send a Telegram username.",

            "names":
                "🔗 NAMES\n\n"
                "Send a Telegram username.",

            "groups":
                "👥 GROUPS\n\n"
                "Send a Telegram account ID.",

            "messages":
                "💬 MESSAGES\n\n"
                "Send a Telegram username or link.",

            "analysis":
                "🔎 ANALYSIS\n\n"
                "Send a Telegram username or link.",

            "channels":
                "📢 CHANNELS\n\n"
                "Send an account ID.",

            "rep":
                "👍 REPUTATION\n\n"
                "Send a Telegram username.",

            "friends":
                "👥 FRIENDS\n\n"
                "Use:\n"
                "<code>@group @user</code>",

            "reactions":
                "🔄 REACTIONS\n\n"
                "Send a Telegram username or link.",

            "gifts":
                "🎁 GIFTS\n\n"
                "Send a Telegram username.",

            "share":
                "📤 SHARE\n\n"
                "Send a Telegram message link.",

            "words":
                "🔵 WORD FREQUENCY\n\n"
                "Send a Telegram username or link.",

            "common":
                "👥 COMMON GROUPS\n\n"
                "Send a Telegram username.",
        }

        context.user_data[
            "state"
        ] = data

        await query.edit_message_text(
            prompts[data],
            parse_mode=ParseMode.HTML,
        )

        return


    # --------------------------------------------------------
    # NO PUBLIC LINK
    # --------------------------------------------------------

    if data == "no_public_link":

        await query.answer(
            "Telegram did not provide a public message link for this result.",
            show_alert=True,
        )

        return


# ============================================================
# MESSAGE HANDLER
# ============================================================

async def handle_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    text = (
        update.message.text
        or ""
    ).strip()

    user_id = update.effective_user.id


    # --------------------------------------------------------
    # PASSWORD
    # --------------------------------------------------------

    if context.user_data.get(
        "state"
    ) == "awaiting_password":

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

            await update.message.reply_text(
                "🤖 <b>TELEGRAM ASSISTANT</b>\n\n"
                "Choose an option:",
                parse_mode=ParseMode.HTML,
                reply_markup=main_keyboard(),
            )

        else:

            await update.message.reply_text(
                "❌ Incorrect password."
            )

        return


    # --------------------------------------------------------
    # AUTHENTICATION
    # --------------------------------------------------------

    if not is_authenticated(user_id):

        await update.message.reply_text(
            "🔐 Please use /start first."
        )

        return


    state = context.user_data.get(
        "state"
    )


    # --------------------------------------------------------
    # SEARCH
    # --------------------------------------------------------

    if state == "search":

        context.user_data[
            "state"
        ] = None

        await fetch_search(
            update,
            context,
            text,
        )

        return


    # --------------------------------------------------------
    # PROFILE
    # --------------------------------------------------------

    if state == "profile_query":

        context.user_data[
            "state"
        ] = None

        await fetch_profile(
            update,
            context,
            text,
        )

        return


    # --------------------------------------------------------
    # FETCH LINK
    # --------------------------------------------------------

    if state == "fetch_link" or "t.me/" in text:

        username, message_id = parse_tg_link(
            text
        )

        if not username:

            await update.message.reply_text(
                "❌ Invalid Telegram link."
            )

            return

        context.user_data[
            "state"
        ] = None

        try:

            entity = await telethon_client.get_entity(
                username
            )

            if message_id:

                message = await telethon_client.get_messages(
                    entity,
                    ids=message_id
                )

                if message:

                    await safe_send(
                        update.message.chat_id,
                        context.bot,
                        message,
                        from_chat_id=entity.id,
                        message_id=message.id,
                    )

                else:

                    await update.message.reply_text(
                        "❌ Message not found."
                    )

            else:

                status = await update.message.reply_text(
                    "🔄 Fetching recent messages..."
                )

                messages = await telethon_client.get_messages(
                    entity,
                    limit=20,
                )

                if not messages:

                    await status.edit_text(
                        "❌ No messages found."
                    )

                    return

                for message in messages:

                    await safe_send(
                        update.message.chat_id,
                        context.bot,
                        message,
                        from_chat_id=entity.id,
                        message_id=message.id,
                    )

                await status.edit_text(
                    f"✅ Fetched {len(messages)} messages."
                )

        except Exception as e:

            await handle_telethon_error(
                update,
                e
            )

        return


    # --------------------------------------------------------
    # WORDS
    # --------------------------------------------------------

    if state == "words":

        context.user_data[
            "state"
        ] = None

        try:

            entity = await telethon_client.get_entity(
                text
            )

            messages = await telethon_client.get_messages(
                entity,
                limit=200,
            )

            stop_words = {
                "the",
                "and",
                "for",
                "with",
                "this",
                "that",
                "from",
                "have",
                "has",
                "are",
                "was",
                "were",
                "you",
                "your",
                "telegram",
                "http",
                "https",
                "www",
            }

            word_count = {}

            for message in messages:

                content = getattr(
                    message,
                    "message",
                    None
                )

                if not content:
                    continue

                words = re.findall(
                    r"\b[a-zA-Z0-9_]+\b",
                    content.lower()
                )

                for word in words:

                    if (
                        len(word) > 2
                        and word not in stop_words
                    ):

                        word_count[word] = (
                            word_count.get(
                                word,
                                0
                            )
                            + 1
                        )

            top_words = sorted(
                word_count.items(),
                key=lambda x: x[1],
                reverse=True,
            )[:20]

            output = (
                "🔵 <b>WORD FREQUENCY</b>\n\n"
            )

            for word, count in top_words:

                output += (
                    f"<code>{html.escape(word)}</code>"
                    f" — {count}\n"
                )

            await update.message.reply_text(
                output,
                parse_mode=ParseMode.HTML,
            )

        except Exception as e:

            await update.message.reply_text(
                f"❌ Error:\n{e}"
            )

        return


    # --------------------------------------------------------
    # DEFAULT
    # --------------------------------------------------------

    await update.message.reply_text(
        "👋 Use the menu buttons.\n\n"
        "For global search:\n"
        "➕ More Commands → 🔎 Search"
    )


# ============================================================
# TELETHON INBOX LISTENER
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
                event.raw_text
                if getattr(
                    event,
                    "raw_text",
                    None
                )
                else "",
                get_media_type(event),
                str(event.date),
            )

        except Exception as e:

            print(
                "Inbox listener error:",
                e
            )


# ============================================================
# MAIN
# ============================================================

async def main():

    init_db()

    print(
        "=========================================="
    )

    print(
        "Starting Telegram Search Bot..."
    )

    print(
        "=========================================="
    )

    try:

        await telethon_client.start()

        print(
            "✅ Telethon USER ACCOUNT connected."
        )

    except Exception as e:

        print(
            "❌ Telethon connection failed:"
        )

        print(
            repr(e)
        )

        return


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
            handle_message
        )
    )


    print(
        "✅ Bot handlers loaded."
    )

    print(
        "🔎 Global search enabled."
    )

    print(
        "🌍 Public unjoined channel search enabled when Telegram exposes it."
    )

    await bot_app.initialize()

    await bot_app.start()

    await bot_app.updater.start_polling()

    print(
        "🤖 Bot is running."
    )


    # Flask health server
    threading.Thread(
        target=lambda: app.run(
            host="0.0.0.0",
            port=int(
                os.environ.get(
                    "PORT",
                    "10000"
                )
            ),
        ),
        daemon=True,
    ).start()


    # Keep process alive
    await asyncio.Event().wait()


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    try:

        asyncio.run(
            main()
        )

    except KeyboardInterrupt:

        print(
            "Bot stopped."
        )
