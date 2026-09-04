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

from PIL import (
    Image,
    ImageEnhance,
    ImageFilter,
    ImageOps,
    ImageChops
)

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile
)

from telegram.constants import ParseMode

from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    CallbackQueryHandler,
    ContextTypes
)

from telethon import (
    TelegramClient,
    events,
    functions,
    types
)

from telethon.sessions import StringSession

from telethon.utils import get_peer_id

from telethon.errors import (
    FloodWaitError,
    ChannelPrivateError,
    UsernameNotOccupiedError,
    MessageIdInvalidError
)

import pymupdf
import img2pdf

from pdf2docx import Converter

from pptx import Presentation
from pptx.util import Inches

from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter

from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer
)

from reportlab.lib.styles import (
    getSampleStyleSheet,
    ParagraphStyle
)

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

import yt_dlp
import speech_recognition as sr
import imageio_ffmpeg

from groq import Groq

import edge_tts


# ============================================================
# TELEGRAM STORIES SUPPORT
# ============================================================

try:
    from telethon.tl.functions.stories import (
        GetPeerStoriesRequest,
        GetStoriesByIDRequest
    )
except ImportError:
    GetPeerStoriesRequest = None
    GetStoriesByIDRequest = None


# ============================================================
# FLASK / RENDER KEEP-ALIVE
# ============================================================

app = Flask(__name__)


@app.route("/")
def home():
    return "Bot is alive!"


# ============================================================
# CONFIGURATION
# ============================================================

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

API_ID = int(os.environ.get("API_ID", 0))

API_HASH = os.environ.get("API_HASH", "")

STRING_SESSION = os.environ.get("STRING_SESSION", "")

BOT_PASSWORD = os.environ.get(
    "BOT_PASSWORD",
    "ptss25"
)

RENDER_URL = os.environ.get(
    "RENDER_URL",
    "https://samibot-s1h6.onrender.com"
)

GROQ_API_KEY = os.environ.get(
    "GROQ_API_KEY",
    ""
)


# ============================================================
# ADMIN IDS
# ============================================================

ADMIN_IDS = []

admin_ids_str = os.environ.get(
    "ADMIN_IDS",
    ""
)

if admin_ids_str:
    ADMIN_IDS = [
        int(x.strip())
        for x in admin_ids_str.split(",")
        if x.strip().isdigit()
    ]


# ============================================================
# TELETHON USER CLIENT
# ============================================================

telethon_client = TelegramClient(
    StringSession(STRING_SESSION),
    API_ID,
    API_HASH
)


# ============================================================
# TELEGRAM BOT INSTANCE
#
# This is filled later inside main().
# Message Manager will use it to notify the admin.
# ============================================================

PTB_BOT = None


# ============================================================
# SELF PING
# ============================================================

def self_ping():
    try:
        urllib.request.urlopen(
            RENDER_URL,
            timeout=5
        )
    except Exception:
        pass


# ============================================================
# ADMIN CHECK
# ============================================================

def is_admin(user_id):
    return user_id in ADMIN_IDS


# ============================================================
# DATABASE INITIALIZATION
# ============================================================

def init_db():

    conn = sqlite3.connect(
        "bot_data.db"
    )

    c = conn.cursor()

    # --------------------------------------------------------
    # AUTHENTICATED USERS
    # --------------------------------------------------------

    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY
        )
    """)

    # --------------------------------------------------------
    # INBOX
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # TRACKING
    # --------------------------------------------------------

    c.execute("""
        CREATE TABLE IF NOT EXISTS tracking (
            target_id INTEGER PRIMARY KEY,
            username TEXT
        )
    """)

    # --------------------------------------------------------
    # USER HISTORY
    # --------------------------------------------------------

    c.execute("""
        CREATE TABLE IF NOT EXISTS user_history (
            user_id INTEGER,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            date TEXT
        )
    """)

    # ========================================================
    # MESSAGE MANAGER
    # ========================================================
    #
    # rule_key:
    #
    # messaging
    # block_everyone_until
    # blocked_users
    # filter_links_until
    # filter_videos_until
    # keyword_rules
    #
    # ========================================================

    c.execute("""
        CREATE TABLE IF NOT EXISTS message_manager_rules (
            rule_key TEXT PRIMARY KEY,
            rule_value TEXT
        )
    """)

    # --------------------------------------------------------
    # DEFAULT MESSAGE MANAGER SETTINGS
    # --------------------------------------------------------

    defaults = {
        "messaging": "on",
        "block_everyone_until": "0",
        "blocked_users": "[]",
        "filter_links_until": "0",
        "filter_videos_until": "0",
        "keyword_rules": "{}"
    }

    for key, value in defaults.items():

        c.execute(
            """
            INSERT OR IGNORE INTO
            message_manager_rules
            (rule_key, rule_value)
            VALUES (?, ?)
            """,
            (key, value)
        )

    conn.commit()

    conn.close()


# ============================================================
# MESSAGE MANAGER DATABASE HELPERS
# ============================================================
# All functions use `rule_key` and `rule_value` columns.
# ============================================================

def mm_get_rule(key, default=None):

    conn = sqlite3.connect(
        "bot_data.db"
    )

    c = conn.cursor()

    c.execute(
        """
        SELECT rule_value
        FROM message_manager_rules
        WHERE rule_key = ?
        """,
        (key,)
    )

    row = c.fetchone()

    conn.close()

    if row is None:
        return default

    return row[0]


def mm_set_rule(key, value):

    conn = sqlite3.connect(
        "bot_data.db"
    )

    c = conn.cursor()

    c.execute(
        """
        INSERT OR REPLACE INTO
        message_manager_rules
        (rule_key, rule_value)
        VALUES (?, ?)
        """,
        (key, str(value))
    )

    conn.commit()

    conn.close()


def mm_get_json_rule(key, default):

    raw = mm_get_rule(
        key,
        json.dumps(default)
    )

    try:
        return json.loads(raw)
    except Exception:
        return default


def mm_set_json_rule(key, value):

    mm_set_rule(
        key,
        json.dumps(
            value,
            ensure_ascii=False
        )
    )


# ============================================================
# BASIC MESSAGE MANAGER STATUS
# ============================================================

def mm_is_messaging_on():

    return (
        mm_get_rule(
            "messaging",
            "on"
        ).lower()
        == "on"
    )


def mm_set_messaging(enabled):

    mm_set_rule(
        "messaging",
        "on" if enabled else "off"
    )


# ============================================================
# DURATION HELPERS
# ============================================================
# Convention:
#   -1  = permanent
#    0  = inactive / not set
#   >0  = Unix timestamp when the rule expires
# ============================================================

def mm_parse_duration(text):
    """
    Convert user input such as:
      30 seconds
      5 minutes
      2 hours
      3 days
      1 week
      1 month
      permanent

    Returns:
      seconds (int)
      0   = invalid / not understood
      -1  = permanent
    """
    if not text:
        return 0

    value = str(text).strip().lower()

    # Permanent
    if value in (
        "permanent",
        "forever",
        "perm",
        "always",
        "permanently",
        "∞"
    ):
        return -1

    # Also allow simple numbers followed by units
    match = re.fullmatch(
        r"\s*(\d+(?:\.\d+)?)\s*"
        r"(second|seconds|sec|secs|"
        r"minute|minutes|min|mins|"
        r"hour|hours|hr|hrs|"
        r"day|days|"
        r"week|weeks|"
        r"month|months)\s*",
        value
    )

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
        # Message Manager uses 30 days for one month.
        return int(number * 30 * 24 * 60 * 60)

    return 0


def mm_duration_to_until(duration_seconds):
    """
    Convert duration seconds into expiration timestamp.

    -1 = permanent
     0 = inactive (should not be used)
    """
    if duration_seconds == -1:
        return -1

    if duration_seconds <= 0:
        return 0

    return int(time.time()) + int(duration_seconds)


def mm_is_active_until(until):
    """
    Check whether a rule is active.

    -1 = permanently active
     0 = inactive
    """
    try:
        until = int(until)
    except Exception:
        return False

    if until == -1:
        return True

    if until <= 0:
        return False

    return time.time() < until


def mm_remaining_seconds(until):
    """
    Returns:
      -1 = permanent
       0 = expired/inactive
      >0 = remaining seconds
    """
    try:
        until = int(until)
    except Exception:
        return 0

    if until == -1:
        return -1

    if until <= 0:
        return 0

    return max(0, int(until - time.time()))


def mm_format_duration(seconds):
    """
    Convert seconds into readable text.
    """
    try:
        seconds = int(seconds)
    except Exception:
        return "Unknown"

    if seconds == -1:
        return "Permanent"

    if seconds <= 0:
        return "Inactive"

    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, secs = divmod(remainder, 60)

    parts = []

    if days:
        parts.append(f"{days} day{'s' if days != 1 else ''}")

    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")

    if minutes:
        parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")

    if secs and len(parts) < 2:
        parts.append(f"{secs} second{'s' if secs != 1 else ''}")

    if not parts:
        return "Less than 1 second"

    return " ".join(parts[:2])


def mm_until_text(until):
    """
    Human-readable status for a rule.
    """
    if not mm_is_active_until(until):
        return "OFF"

    remaining = mm_remaining_seconds(until)

    if remaining == -1:
        return "PERMANENT"

    return mm_format_duration(remaining)


# ============================================================
# BLOCK EVERYONE
# ============================================================

def mm_get_block_everyone_until():
    try:
        return int(mm_get_rule("block_everyone_until", "0"))
    except Exception:
        return 0


def mm_set_block_everyone_until(until):
    mm_set_rule("block_everyone_until", until)


def mm_is_everyone_blocked():
    return mm_is_active_until(
        mm_get_block_everyone_until()
    )


# ============================================================
# LINK FILTER
# ============================================================

def mm_get_filter_links_until():
    try:
        return int(mm_get_rule("filter_links_until", "0"))
    except Exception:
        return 0


def mm_set_filter_links_until(until):
    mm_set_rule("filter_links_until", until)


def mm_links_are_filtered():
    return mm_is_active_until(
        mm_get_filter_links_until()
    )


# ============================================================
# VIDEO FILTER
# ============================================================

def mm_get_filter_videos_until():
    try:
        return int(mm_get_rule("filter_videos_until", "0"))
    except Exception:
        return 0


def mm_set_filter_videos_until(until):
    mm_set_rule("filter_videos_until", until)


def mm_videos_are_filtered():
    return mm_is_active_until(
        mm_get_filter_videos_until()
    )


# ============================================================
# BLOCKED USERS
# ============================================================

def mm_get_blocked_users():
    data = mm_get_json_rule("blocked_users", [])

    if not isinstance(data, list):
        return []

    cleaned = []

    for item in data:
        if not isinstance(item, dict):
            continue

        try:
            uid = int(item.get("user_id"))
            until = int(item.get("until", 0))

            cleaned.append({
                "user_id": uid,
                "until": until
            })

        except Exception:
            continue

    return cleaned


def mm_save_blocked_users(users):
    mm_set_json_rule("blocked_users", users)


def mm_block_specific_user(user_id, until):
    try:
        user_id = int(user_id)
    except Exception:
        return False

    users = mm_get_blocked_users()

    # Replace existing rule for same user
    users = [
        x for x in users
        if int(x.get("user_id", 0)) != user_id
    ]

    users.append({
        "user_id": user_id,
        "until": int(until)
    })

    mm_save_blocked_users(users)

    return True


def mm_unblock_specific_user(user_id):
    try:
        user_id = int(user_id)
    except Exception:
        return False

    users = mm_get_blocked_users()

    new_users = [
        x for x in users
        if int(x.get("user_id", 0)) != user_id
    ]

    mm_save_blocked_users(new_users)

    return len(new_users) != len(users)


def mm_is_user_blocked(user_id):
    try:
        user_id = int(user_id)
    except Exception:
        return False

    users = mm_get_blocked_users()

    changed = False
    active = False
    new_users = []

    for item in users:
        try:
            uid = int(item["user_id"])
            until = int(item["until"])
        except Exception:
            continue

        if uid == user_id:
            if mm_is_active_until(until):
                active = True
                new_users.append(item)
            else:
                # Remove expired rule
                changed = True
        else:
            new_users.append(item)

    if changed:
        mm_save_blocked_users(new_users)

    return active


# ============================================================
# KEYWORD FILTER RULES
# ============================================================

def mm_get_keyword_rules():
    data = mm_get_json_rule("keyword_rules", {})

    if not isinstance(data, dict):
        return {}

    cleaned = {}

    for keyword, until in data.items():
        try:
            cleaned[str(keyword).lower()] = int(until)
        except Exception:
            continue

    return cleaned


def mm_save_keyword_rules(rules):
    mm_set_json_rule("keyword_rules", rules)


def mm_add_keyword(keyword, until):
    keyword = str(keyword).strip().lower()

    if not keyword:
        return False

    rules = mm_get_keyword_rules()

    rules[keyword] = int(until)

    mm_save_keyword_rules(rules)

    return True


def mm_remove_keyword(keyword):
    keyword = str(keyword).strip().lower()

    rules = mm_get_keyword_rules()

    if keyword not in rules:
        return False

    del rules[keyword]

    mm_save_keyword_rules(rules)

    return True


def mm_cleanup_keyword_rules():
    rules = mm_get_keyword_rules()

    changed = False
    cleaned = {}

    for keyword, until in rules.items():

        if mm_is_active_until(until):
            cleaned[keyword] = until
        else:
            changed = True

    if changed:
        mm_save_keyword_rules(cleaned)

    return cleaned


def mm_find_keyword(text):
    if not text:
        return None

    text_lower = text.lower()

    rules = mm_cleanup_keyword_rules()

    # Longest keyword first.
    # This avoids a short keyword accidentally matching
    # before a more specific keyword.
    for keyword in sorted(
        rules.keys(),
        key=len,
        reverse=True
    ):
        if keyword in text_lower:
            return keyword

    return None


def mm_get_active_keywords():
    return mm_cleanup_keyword_rules()


# ============================================================
# MESSAGE DETECTION
# ============================================================

def mm_contains_link(event):
    """
    Detect normal links, Telegram links and URLs.
    """
    text = getattr(event, "raw_text", "") or ""

    if re.search(
        r"(https?://|www\.|t\.me/|telegram\.me/)",
        text,
        re.IGNORECASE
    ):
        return True

    # Telegram message entities can also contain URLs
    try:
        entities = getattr(event.message, "entities", None)

        if entities:
            for entity in entities:
                entity_name = type(entity).__name__.lower()

                if (
                    "url" in entity_name
                    or "texturl" in entity_name
                ):
                    return True

    except Exception:
        pass

    return False


def mm_is_video(event):
    """
    Detect videos, video notes, GIFs and video documents.
    """
    try:
        if getattr(event, "video", None):
            return True

        if getattr(event, "gif", None):
            return True

        message = getattr(event, "message", None)

        if message:

            media = getattr(message, "media", None)

            if media:
                media_name = type(media).__name__.lower()

                if "document" in media_name:
                    document = getattr(media, "document", None)

                    if document:
                        attributes = getattr(
                            document,
                            "attributes",
                            []
                        )

                        for attr in attributes:
                            attr_name = type(attr).__name__.lower()

                            if (
                                "video" in attr_name
                                or "animated" in attr_name
                            ):
                                return True

    except Exception:
        pass

    return False


# ============================================================
# MESSAGE MANAGER MAIN DECISION
# ============================================================

def mm_should_block_message(sender_id, event):
    """
    Returns:
      True  -> message should NOT be forwarded
      False -> message may continue
    """

    # Never block administrators
    if sender_id in ADMIN_IDS:
        return False

    # Block everyone
    if mm_is_everyone_blocked():
        return True

    # Specific user block
    if mm_is_user_blocked(sender_id):
        return True

    # Link filter
    if mm_links_are_filtered():
        if mm_contains_link(event):
            return True

    # Video filter
    if mm_videos_are_filtered():
        if mm_is_video(event):
            return True

    # Keyword filter
    text = getattr(event, "raw_text", "") or ""

    if text:
        if mm_find_keyword(text):
            return True

    return False


def mm_refusal_text():
    return (
        "🤖 **Message Manager**\n\n"
        "Sorry, messaging is currently unavailable.\n"
        "Your message was not forwarded to the administrator.\n\n"
        "Please try again later. 🙏"
    )


# ============================================================
# DURATION KEYBOARD
# ============================================================

def mm_duration_keyboard(prefix):
    """
    prefix identifies what is being configured.
    Examples:
      mm_block_all
      mm_block_user
      mm_filter_links
      mm_filter_videos
      mm_keyword
    """

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "30 seconds",
                callback_data=f"mm_dur|{prefix}|30"
            ),
            InlineKeyboardButton(
                "1 minute",
                callback_data=f"mm_dur|{prefix}|60"
            )
        ],
        [
            InlineKeyboardButton(
                "1 hour",
                callback_data=f"mm_dur|{prefix}|3600"
            ),
            InlineKeyboardButton(
                "1 day",
                callback_data=f"mm_dur|{prefix}|86400"
            )
        ],
        [
            InlineKeyboardButton(
                "1 month",
                callback_data=f"mm_dur|{prefix}|2592000"
            ),
            InlineKeyboardButton(
                "♾ Permanent",
                callback_data=f"mm_dur|{prefix}|-1"
            )
        ],
        [
            InlineKeyboardButton(
                "✏️ Custom Duration",
                callback_data=f"mm_custom|{prefix}"
            )
        ],
        [
            InlineKeyboardButton(
                "⬅️ Back",
                callback_data="message_manager"
            )
        ]
    ])


# ============================================================
# MESSAGE MANAGER MAIN MENU
# ============================================================

def message_manager_keyboard():
    messaging_status = (
        "🟢 ON"
        if mm_is_messaging_on()
        else "🔴 OFF"
    )

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                f"💬 Messaging: {messaging_status}",
                callback_data="mm_toggle"
            )
        ],
        [
            InlineKeyboardButton(
                "🚫 Block Messages",
                callback_data="mm_block_menu"
            ),
            InlineKeyboardButton(
                "🔎 Filter Messages",
                callback_data="mm_filter_menu"
            )
        ],
        [
            InlineKeyboardButton(
                "📋 Active Rules",
                callback_data="mm_active_rules"
            ),
            InlineKeyboardButton(
                "👥 Blocked Users",
                callback_data="mm_blocked_users"
            )
        ],
        [
            InlineKeyboardButton(
                "📩 Forwarding",
                callback_data="mm_forwarding"
            )
        ],
        [
            InlineKeyboardButton(
                "⬅️ Back",
                callback_data="more"
            )
        ]
    ])


async def show_message_manager(query, context):
    """
    Display Message Manager home screen.
    """

    if not is_admin(query.from_user.id):
        await query.message.reply_text(
            "🔒 Message Manager is available to administrators only."
        )
        return

    messaging_status = (
        "🟢 ON — incoming messages can be forwarded"
        if mm_is_messaging_on()
        else "🔴 OFF — incoming messages are refused"
    )

    text = (
        "💬 **MESSAGE MANAGER**\n\n"
        f"Messaging: {messaging_status}\n\n"
        "This system works as a gatekeeper for your "
        "private Telegram messages.\n\n"
        "🟢 **ON:** incoming messages can reach you.\n"
        "🔴 **OFF:** messages are not forwarded to you.\n\n"
        "🚫 Block Messages = temporarily/permanently "
        "stop messages.\n"
        "🔎 Filter Messages = block only selected types "
        "such as links, videos or keywords.\n"
        "📋 Active Rules = see current restrictions."
    )

    await query.message.reply_text(
        text,
        reply_markup=message_manager_keyboard(),
        parse_mode=ParseMode.MARKDOWN
    )


# ------------------------------------------------------------
# BLOCK MENU
# ------------------------------------------------------------

def mm_block_menu_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "👥 Block Everyone",
                callback_data="mm_block_everyone"
            )
        ],
        [
            InlineKeyboardButton(
                "👤 Block Specific User",
                callback_data="mm_block_specific"
            )
        ],
        [
            InlineKeyboardButton(
                "🟢 Turn OFF Block Everyone",
                callback_data="mm_unblock_everyone"
            )
        ],
        [
            InlineKeyboardButton(
                "⬅️ Back",
                callback_data="message_manager"
            )
        ]
    ])


async def show_mm_block_menu(query, context):
    until = mm_get_block_everyone_until()

    status = mm_until_text(until)

    text = (
        "🚫 **BLOCK MESSAGES**\n\n"
        "👥 **Block Everyone**\n"
        "Stops incoming private messages from being "
        "forwarded to you.\n\n"
        f"Current status: **{status}**\n\n"
        "👤 **Block Specific User**\n"
        "Blocks messages from one selected Telegram user.\n\n"
        "🟢 **Turn OFF Block Everyone**\n"
        "Immediately removes the global block."
    )

    await query.message.reply_text(
        text,
        reply_markup=mm_block_menu_keyboard(),
        parse_mode=ParseMode.MARKDOWN
    )


# ------------------------------------------------------------
# FILTER MENU
# ------------------------------------------------------------

def mm_filter_menu_keyboard():
    links_status = (
        "🟢 ON"
        if mm_links_are_filtered()
        else "🔴 OFF"
    )

    videos_status = (
        "🟢 ON"
        if mm_videos_are_filtered()
        else "🔴 OFF"
    )

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                f"🔗 Links {links_status}",
                callback_data="mm_filter_links"
            ),
            InlineKeyboardButton(
                f"🎥 Videos {videos_status}",
                callback_data="mm_filter_videos"
            )
        ],
        [
            InlineKeyboardButton(
                "🔤 Keywords",
                callback_data="mm_filter_keywords"
            )
        ],
        [
            InlineKeyboardButton(
                "📋 Active Filters",
                callback_data="mm_active_filters"
            )
        ],
        [
            InlineKeyboardButton(
                "⬅️ Back",
                callback_data="message_manager"
            )
        ]
    ])


async def show_mm_filter_menu(query, context):
    links = mm_until_text(
        mm_get_filter_links_until()
    )

    videos = mm_until_text(
        mm_get_filter_videos_until()
    )

    keywords = mm_get_active_keywords()

    text = (
        "🔎 **FILTER MESSAGES**\n\n"
        "🔗 **Links**\n"
        "Blocks messages containing URLs.\n"
        f"Status: **{links}**\n\n"
        "🎥 **Videos**\n"
        "Blocks incoming video/video-note/GIF messages.\n"
        f"Status: **{videos}**\n\n"
        "🔤 **Keywords**\n"
        "Blocks messages containing selected words or "
        "phrases.\n"
        f"Active keywords: **{len(keywords)}**"
    )

    await query.message.reply_text(
        text,
        reply_markup=mm_filter_menu_keyboard(),
        parse_mode=ParseMode.MARKDOWN
    )


# ------------------------------------------------------------
# ACTIVE RULES
# ------------------------------------------------------------

async def show_mm_active_rules(query, context):
    block_all = mm_until_text(
        mm_get_block_everyone_until()
    )

    links = mm_until_text(
        mm_get_filter_links_until()
    )

    videos = mm_until_text(
        mm_get_filter_videos_until()
    )

    blocked_users = mm_get_blocked_users()
    keywords = mm_get_active_keywords()

    text = (
        "📋 **ACTIVE MESSAGE MANAGER RULES**\n\n"
        f"💬 Messaging: "
        f"{'🟢 ON' if mm_is_messaging_on() else '🔴 OFF'}\n\n"
        f"👥 Block Everyone: **{block_all}**\n"
        f"🔗 Link Filter: **{links}**\n"
        f"🎥 Video Filter: **{videos}**\n"
        f"👤 Specific Blocked Users: **{len(blocked_users)}**\n"
        f"🔤 Active Keywords: **{len(keywords)}**"
    )

    if keywords:
        text += "\n\n🔤 **Keywords:**\n"

        for keyword, until in list(keywords.items())[:30]:
            text += (
                f"• `{keyword}` — "
                f"{mm_until_text(until)}\n"
            )

    await query.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🔎 Filter Menu",
                    callback_data="mm_filter_menu"
                )
            ],
            [
                InlineKeyboardButton(
                    "🚫 Block Menu",
                    callback_data="mm_block_menu"
                )
            ],
            [
                InlineKeyboardButton(
                    "⬅️ Message Manager",
                    callback_data="message_manager"
                )
            ]
        ]),
        parse_mode=ParseMode.MARKDOWN
    )


# ------------------------------------------------------------
# BLOCKED USER LIST
# ------------------------------------------------------------

async def show_mm_blocked_users(query, context):
    users = mm_get_blocked_users()

    active = []

    for item in users:
        try:
            if mm_is_active_until(int(item["until"])):
                active.append(item)
        except Exception:
            pass

    if not active:
        text = (
            "👥 **BLOCKED USERS**\n\n"
            "No specific users are currently blocked."
        )
    else:
        text = (
            "👥 **BLOCKED USERS**\n\n"
        )

        for item in active[:50]:
            uid = item["user_id"]
            until = item["until"]

            text += (
                f"🆔 `{uid}`\n"
                f"⏳ {mm_until_text(until)}\n\n"
            )

    await query.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "👤 Block Another User",
                    callback_data="mm_block_specific"
                )
            ],
            [
                InlineKeyboardButton(
                    "⬅️ Back",
                    callback_data="mm_block_menu"
                )
            ]
        ]),
        parse_mode=ParseMode.MARKDOWN
    )


# ------------------------------------------------------------
# FORWARDING INFORMATION
# ------------------------------------------------------------

async def show_mm_forwarding(query, context):
    admin_text = []

    if ADMIN_IDS:
        for uid in ADMIN_IDS:
            admin_text.append(f"• `{uid}`")
    else:
        admin_text.append("❌ No ADMIN_IDS configured.")

    text = (
        "📩 **MESSAGE FORWARDING**\n\n"
        "When Message Manager allows an incoming private "
        "message, the Telethon account can forward the "
        "message information to the configured administrator.\n\n"
        "👑 **Configured administrators:**\n"
        + "\n".join(admin_text)
        + "\n\n"
        "⚠️ If ADMIN_IDS is empty, the bot cannot know "
        "where to send manager notifications."
    )

    await query.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "⬅️ Message Manager",
                    callback_data="message_manager"
                )
            ]
        ]),
        parse_mode=ParseMode.MARKDOWN
    )


# ------------------------------------------------------------
# SPECIFIC USER INPUT
# ------------------------------------------------------------

async def mm_request_specific_user(query, context):
    context.user_data["state"] = "mm_specific_user"

    await query.message.reply_text(
        "👤 **BLOCK SPECIFIC USER**\n\n"
        "Send the Telegram numeric User ID.\n\n"
        "Example:\n"
        "`123456789`\n\n"
        "After you send the ID, I will ask how long "
        "the block should remain active.",
        parse_mode=ParseMode.MARKDOWN
    )


# ------------------------------------------------------------
# KEYWORD INPUT
# ------------------------------------------------------------

async def mm_request_keyword(query, context):
    context.user_data["state"] = "mm_keyword"

    await query.message.reply_text(
        "🔤 **ADD KEYWORD FILTER**\n\n"
        "Send the word or phrase you want to block.\n\n"
        "Examples:\n"
        "`spam`\n"
        "`free money`\n"
        "`advertisement`\n\n"
        "The matching is case-insensitive.",
        parse_mode=ParseMode.MARKDOWN
    )


# ------------------------------------------------------------
# CUSTOM DURATION INPUT
# ------------------------------------------------------------

async def mm_request_custom_duration(query, context, prefix):
    context.user_data["state"] = "mm_custom_duration"
    context.user_data["mm_duration_prefix"] = prefix

    await query.message.reply_text(
        "✏️ **CUSTOM DURATION**\n\n"
        "Send a duration using one of these formats:\n\n"
        "• `30 seconds`\n"
        "• `5 minutes`\n"
        "• `2 hours`\n"
        "• `3 days`\n"
        "• `1 month`\n"
        "• `permanent`\n\n"
        "Example: `45 minutes`",
        parse_mode=ParseMode.MARKDOWN
    )


# ------------------------------------------------------------
# DURATION SELECTION
# ------------------------------------------------------------

async def mm_apply_duration(query, context, prefix, seconds):
    try:
        seconds = int(seconds)
    except Exception:
        await query.message.reply_text(
            "❌ Invalid duration."
        )
        return

    until = mm_duration_to_until(seconds)

    # ----------------------------------------
    # BLOCK EVERYONE
    # ----------------------------------------
    if prefix == "block_all":

        mm_set_block_everyone_until(until)

        await query.message.reply_text(
            "✅ **Block Everyone Updated**\n\n"
            f"Duration: **{mm_until_text(until)}**\n\n"
            "Incoming messages will not be forwarded "
            "while this rule is active.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🚫 Block Menu",
                        callback_data="mm_block_menu"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "💬 Message Manager",
                        callback_data="message_manager"
                    )
                ]
            ])
        )

        return

    # ----------------------------------------
    # BLOCK SPECIFIC USER
    # ----------------------------------------
    if prefix == "block_user":

        target_user = context.user_data.get(
            "mm_target_user"
        )

        if not target_user:
            await query.message.reply_text(
                "❌ No user ID was selected."
            )
            return

        mm_block_specific_user(
            int(target_user),
            until
        )

        context.user_data.pop(
            "mm_target_user",
            None
        )

        await query.message.reply_text(
            "✅ **User Blocked**\n\n"
            f"User ID: `{target_user}`\n"
            f"Duration: **{mm_until_text(until)}**",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "👥 Blocked Users",
                        callback_data="mm_blocked_users"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "⬅️ Block Menu",
                        callback_data="mm_block_menu"
                    )
                ]
            ])
        )

        return

    # ----------------------------------------
    # LINK FILTER
    # ----------------------------------------
    if prefix == "filter_links":

        mm_set_filter_links_until(until)

        await query.message.reply_text(
            "✅ **Link Filter Updated**\n\n"
            f"Duration: **{mm_until_text(until)}**\n\n"
            "Messages containing links will now be "
            "blocked while this rule is active.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔎 Filter Menu",
                        callback_data="mm_filter_menu"
                    )
                ]
            ])
        )

        return

    # ----------------------------------------
    # VIDEO FILTER
    # ----------------------------------------
    if prefix == "filter_videos":

        mm_set_filter_videos_until(until)

        await query.message.reply_text(
            "✅ **Video Filter Updated**\n\n"
            f"Duration: **{mm_until_text(until)}**\n\n"
            "Incoming videos will now be blocked "
            "while this rule is active.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔎 Filter Menu",
                        callback_data="mm_filter_menu"
                    )
                ]
            ])
        )

        return

    # ----------------------------------------
    # KEYWORD
    # ----------------------------------------
    if prefix == "keyword":

        keyword = context.user_data.get(
            "mm_pending_keyword"
        )

        if not keyword:
            await query.message.reply_text(
                "❌ No keyword selected."
            )
            return

        mm_add_keyword(
            keyword,
            until
        )

        context.user_data.pop(
            "mm_pending_keyword",
            None
        )

        await query.message.reply_text(
            "✅ **Keyword Filter Added**\n\n"
            f"Keyword: `{keyword}`\n"
            f"Duration: **{mm_until_text(until)}**",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔤 Keywords",
                        callback_data="mm_filter_keywords"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "⬅️ Filter Menu",
                        callback_data="mm_filter_menu"
                    )
                ]
            ])
        )

        return

    await query.message.reply_text(
        "❌ Unknown Message Manager setting."
    )


# ------------------------------------------------------------
# TURN OFF INDIVIDUAL RULES
# ------------------------------------------------------------

async def mm_turn_off_rule(query, rule_name):
    if rule_name == "block_all":
        mm_set_block_everyone_until(0)

    elif rule_name == "filter_links":
        mm_set_filter_links_until(0)

    elif rule_name == "filter_videos":
        mm_set_filter_videos_until(0)

    await query.message.reply_text(
        "🟢 Rule turned OFF successfully.",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "💬 Message Manager",
                    callback_data="message_manager"
                )
            ]
        ])
    )


# ------------------------------------------------------------
# KEYWORD LIST / MANAGEMENT
# ------------------------------------------------------------

async def show_mm_keywords(query, context):
    keywords = mm_get_active_keywords()

    text = "🔤 **KEYWORD FILTERS**\n\n"

    if not keywords:
        text += "No active keyword filters.\n"
    else:
        for keyword, until in list(keywords.items())[:50]:
            text += (
                f"• `{keyword}`\n"
                f"  ⏳ {mm_until_text(until)}\n"
            )

    text += (
        "\nYou can add a new keyword or remove an "
        "existing one."
    )

    buttons = [
        [
            InlineKeyboardButton(
                "➕ Add Keyword",
                callback_data="mm_add_keyword"
            )
        ]
    ]

    for keyword in list(keywords.keys())[:20]:
        # Callback data is intentionally encoded.
        safe_keyword = keyword[:50]

        buttons.append([
            InlineKeyboardButton(
                f"🗑️ Remove: {safe_keyword}",
                callback_data="mm_kw_remove|" + safe_keyword
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            "⬅️ Filter Menu",
            callback_data="mm_filter_menu"
        )
    ])

    await query.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode=ParseMode.MARKDOWN
    )


# ------------------------------------------------------------
# FILTER STATUS PAGE
# ------------------------------------------------------------

async def show_mm_active_filters(query, context):
    links = mm_get_filter_links_until()
    videos = mm_get_filter_videos_until()
    keywords = mm_get_active_keywords()

    text = (
        "📋 **ACTIVE FILTERS**\n\n"
        f"🔗 Links: **{mm_until_text(links)}**\n"
        f"🎥 Videos: **{mm_until_text(videos)}**\n"
        f"🔤 Keywords: **{len(keywords)} active**\n"
    )

    if keywords:
        text += "\n**Keywords:**\n"

        for keyword, until in list(
            keywords.items()
        )[:30]:
            text += (
                f"• `{keyword}` — "
                f"{mm_until_text(until)}\n"
            )

    await query.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🔎 Filter Menu",
                    callback_data="mm_filter_menu"
                )
            ],
            [
                InlineKeyboardButton(
                    "⬅️ Message Manager",
                    callback_data="message_manager"
                )
            ]
        ]),
        parse_mode=ParseMode.MARKDOWN
    )


# ------------------------------------------------------------
# MESSAGE MANAGER HELP
# ------------------------------------------------------------

async def show_mm_help(query, context):
    await query.message.reply_text(
        "💬 **MESSAGE MANAGER — HOW IT WORKS**\n\n"

        "🟢 **Messaging ON**\n"
        "Allowed incoming messages can be forwarded "
        "to the administrator.\n\n"

        "🔴 **Messaging OFF**\n"
        "Incoming messages are refused and are not "
        "forwarded.\n\n"

        "🚫 **Block Everyone**\n"
        "Temporarily or permanently stops all incoming "
        "private messages from reaching the administrator.\n\n"

        "👤 **Block Specific User**\n"
        "Stops messages from one Telegram user.\n\n"

        "🔗 **Link Filter**\n"
        "Blocks messages containing links.\n\n"

        "🎥 **Video Filter**\n"
        "Blocks videos, video notes and GIF/video media.\n\n"

        "🔤 **Keyword Filter**\n"
        "Blocks messages containing selected words or "
        "phrases.\n\n"

        "⏱️ Every rule can have its own duration.\n"
        "You can use seconds, minutes, hours, days, "
        "months or permanent rules.",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "⬅️ Message Manager",
                    callback_data="message_manager"
                )
            ]
        ]),
        parse_mode=ParseMode.MARKDOWN
    )


# ============================================================
# MESSAGE MANAGER CALLBACK + INPUT CONTROLLER
# ============================================================

async def handle_message_manager_callback(query, context):
    """
    Handles all Message Manager callback buttons.
    """

    user_id = query.from_user.id

    if not is_admin(user_id):
        await query.answer(
            "🔒 Admin only.",
            show_alert=True
        )
        return

    data = query.data or ""

    try:
        await query.answer()
    except Exception:
        pass

    # --------------------------------------------------------
    # MAIN MESSAGE MANAGER
    # --------------------------------------------------------
    if data == "message_manager":
        await show_message_manager(query, context)
        return

    # --------------------------------------------------------
    # TOGGLE MESSAGING
    # --------------------------------------------------------
    if data == "mm_toggle":

        new_status = not mm_is_messaging_on()

        mm_set_messaging(new_status)

        if new_status:
            message = (
                "🟢 **Messaging is ON**\n\n"
                "Incoming messages that pass the active "
                "Message Manager rules can be forwarded "
                "to the administrator."
            )
        else:
            message = (
                "🔴 **Messaging is OFF**\n\n"
                "Incoming messages will not be forwarded "
                "to the administrator."
            )

        await query.message.reply_text(
            message,
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "💬 Message Manager",
                        callback_data="message_manager"
                    )
                ]
            ]),
            parse_mode=ParseMode.MARKDOWN
        )

        return

    # --------------------------------------------------------
    # BLOCK MENU
    # --------------------------------------------------------
    if data == "mm_block_menu":
        await show_mm_block_menu(query, context)
        return

    # --------------------------------------------------------
    # BLOCK EVERYONE
    # --------------------------------------------------------
    if data == "mm_block_everyone":

        await query.message.reply_text(
            "🚫 **BLOCK EVERYONE**\n\n"
            "Choose how long you want to block incoming "
            "messages.\n\n"
            "You can change this setting later.",
            reply_markup=mm_duration_keyboard(
                "block_all"
            ),
            parse_mode=ParseMode.MARKDOWN
        )

        return

    # --------------------------------------------------------
    # UNBLOCK EVERYONE
    # --------------------------------------------------------
    if data == "mm_unblock_everyone":

        mm_set_block_everyone_until(0)

        await query.message.reply_text(
            "🟢 **Block Everyone is OFF.**\n\n"
            "Incoming messages are allowed again, "
            "subject to your other Message Manager filters.",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🚫 Block Menu",
                        callback_data="mm_block_menu"
                    )
                ]
            ]),
            parse_mode=ParseMode.MARKDOWN
        )

        return

    # --------------------------------------------------------
    # BLOCK SPECIFIC USER
    # --------------------------------------------------------
    if data == "mm_block_specific":
        await mm_request_specific_user(
            query,
            context
        )
        return

    # --------------------------------------------------------
    # BLOCKED USERS
    # --------------------------------------------------------
    if data == "mm_blocked_users":
        await show_mm_blocked_users(
            query,
            context
        )
        return

    # --------------------------------------------------------
    # FILTER MENU
    # --------------------------------------------------------
    if data == "mm_filter_menu":
        await show_mm_filter_menu(
            query,
            context
        )
        return

    # --------------------------------------------------------
    # LINK FILTER
    # --------------------------------------------------------
    if data == "mm_filter_links":

        current = mm_get_filter_links_until()

        current_status = mm_until_text(current)

        await query.message.reply_text(
            "🔗 **LINK FILTER**\n\n"
            "When enabled, messages containing URLs "
            "or Telegram links will be blocked.\n\n"
            f"Current status: **{current_status}**\n\n"
            "Choose the new duration:",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "⏱️ Set Duration",
                        callback_data="mm_link_duration"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🟢 Turn OFF",
                        callback_data="mm_off|filter_links"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "⬅️ Back",
                        callback_data="mm_filter_menu"
                    )
                ]
            ]),
            parse_mode=ParseMode.MARKDOWN
        )

        return

    # --------------------------------------------------------
    # LINK FILTER DURATION
    # --------------------------------------------------------
    if data == "mm_link_duration":

        await query.message.reply_text(
            "🔗 **LINK FILTER DURATION**\n\n"
            "Choose how long links should be blocked:",
            reply_markup=mm_duration_keyboard(
                "filter_links"
            ),
            parse_mode=ParseMode.MARKDOWN
        )

        return

    # --------------------------------------------------------
    # VIDEO FILTER
    # --------------------------------------------------------
    if data == "mm_filter_videos":

        current = mm_get_filter_videos_until()

        current_status = mm_until_text(current)

        await query.message.reply_text(
            "🎥 **VIDEO FILTER**\n\n"
            "When enabled, incoming videos, video notes "
            "and video media will be blocked.\n\n"
            f"Current status: **{current_status}**\n\n"
            "Choose the new duration:",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "⏱️ Set Duration",
                        callback_data="mm_video_duration"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🟢 Turn OFF",
                        callback_data="mm_off|filter_videos"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "⬅️ Back",
                        callback_data="mm_filter_menu"
                    )
                ]
            ]),
            parse_mode=ParseMode.MARKDOWN
        )

        return

    # --------------------------------------------------------
    # VIDEO FILTER DURATION
    # --------------------------------------------------------
    if data == "mm_video_duration":

        await query.message.reply_text(
            "🎥 **VIDEO FILTER DURATION**\n\n"
            "Choose how long videos should be blocked:",
            reply_markup=mm_duration_keyboard(
                "filter_videos"
            ),
            parse_mode=ParseMode.MARKDOWN
        )

        return

    # --------------------------------------------------------
    # KEYWORD MENU
    # --------------------------------------------------------
    if data == "mm_filter_keywords":

        await show_mm_keywords(
            query,
            context
        )

        return

    # --------------------------------------------------------
    # ADD KEYWORD
    # --------------------------------------------------------
    if data == "mm_add_keyword":

        await mm_request_keyword(
            query,
            context
        )

        return

    # --------------------------------------------------------
    # ACTIVE FILTERS
    # --------------------------------------------------------
    if data == "mm_active_filters":

        await show_mm_active_filters(
            query,
            context
        )

        return

    # --------------------------------------------------------
    # ACTIVE RULES
    # --------------------------------------------------------
    if data == "mm_active_rules":

        await show_mm_active_rules(
            query,
            context
        )

        return

    # --------------------------------------------------------
    # FORWARDING
    # --------------------------------------------------------
    if data == "mm_forwarding":

        await show_mm_forwarding(
            query,
            context
        )

        return

    # --------------------------------------------------------
    # HELP
    # --------------------------------------------------------
    if data == "mm_help":

        await show_mm_help(
            query,
            context
        )

        return

    # --------------------------------------------------------
    # TURN OFF RULE
    # --------------------------------------------------------
    if data.startswith("mm_off|"):

        rule = data.split("|", 1)[1]

        await mm_turn_off_rule(
            query,
            rule
        )

        return

    # --------------------------------------------------------
    # DURATION SELECTION
    #
    # Example:
    # mm_dur|block_all|3600
    # mm_dur|filter_links|86400
    # mm_dur|keyword|-1
    # --------------------------------------------------------
    if data.startswith("mm_dur|"):

        parts = data.split("|")

        if len(parts) != 3:
            await query.message.reply_text(
                "❌ Invalid duration selection."
            )
            return

        prefix = parts[1]

        try:
            seconds = int(parts[2])
        except Exception:
            await query.message.reply_text(
                "❌ Invalid duration."
            )
            return

        await mm_apply_duration(
            query,
            context,
            prefix,
            seconds
        )

        return

    # --------------------------------------------------------
    # CUSTOM DURATION
    # --------------------------------------------------------
    if data.startswith("mm_custom|"):

        parts = data.split("|")

        if len(parts) != 2:
            await query.message.reply_text(
                "❌ Invalid custom duration request."
            )
            return

        prefix = parts[1]

        await mm_request_custom_duration(
            query,
            context,
            prefix
        )

        return

    # --------------------------------------------------------
    # REMOVE KEYWORD
    # --------------------------------------------------------
    if data.startswith("mm_kw_remove|"):

        keyword = data.split(
            "|",
            1
        )[1]

        removed = mm_remove_keyword(
            keyword
        )

        if removed:
            message = (
                "🗑️ **Keyword Removed**\n\n"
                f"Keyword: `{keyword}`"
            )
        else:
            message = (
                "ℹ️ That keyword is no longer active."
            )

        await query.message.reply_text(
            message,
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔤 Keywords",
                        callback_data="mm_filter_keywords"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "⬅️ Filter Menu",
                        callback_data="mm_filter_menu"
                    )
                ]
            ]),
            parse_mode=ParseMode.MARKDOWN
        )

        return

    # --------------------------------------------------------
    # UNKNOWN MESSAGE MANAGER CALLBACK
    # --------------------------------------------------------
    await query.message.reply_text(
        "❌ Unknown Message Manager command."
    )


# ============================================================
# MESSAGE MANAGER TEXT INPUT
# ============================================================

async def handle_message_manager_input(
    update,
    context
):
    """
    Handles text entered by the admin while configuring
    Message Manager.
    """

    user_id = update.effective_user.id

    if not is_admin(user_id):
        return False

    state = context.user_data.get("state")

    # --------------------------------------------------------
    # SPECIFIC USER ID
    # --------------------------------------------------------
    if state == "mm_specific_user":

        raw = (
            update.message.text
            if update.message
            else ""
        )

        raw = raw.strip()

        # Accept @username? No.
        # This system intentionally requires numeric ID
        # because Telegram numeric IDs are unambiguous.
        if not raw.isdigit():

            await update.message.reply_text(
                "❌ Invalid Telegram User ID.\n\n"
                "Please send only the numeric ID.\n\n"
                "Example:\n"
                "`123456789`",
                parse_mode=ParseMode.MARKDOWN
            )

            return True

        target_user = int(raw)

        if target_user <= 0:

            await update.message.reply_text(
                "❌ Invalid User ID."
            )

            return True

        context.user_data[
            "mm_target_user"
        ] = target_user

        context.user_data[
            "state"
        ] = None

        await update.message.reply_text(
            "👤 **USER SELECTED**\n\n"
            f"User ID: `{target_user}`\n\n"
            "Now choose how long this user should "
            "be blocked:",
            reply_markup=mm_duration_keyboard(
                "block_user"
            ),
            parse_mode=ParseMode.MARKDOWN
        )

        return True

    # --------------------------------------------------------
    # KEYWORD
    # --------------------------------------------------------
    if state == "mm_keyword":

        keyword = (
            update.message.text
            if update.message
            else ""
        )

        keyword = keyword.strip()

        if not keyword:

            await update.message.reply_text(
                "❌ Keyword cannot be empty.\n\n"
                "Please send a word or phrase."
            )

            return True

        if len(keyword) > 100:

            await update.message.reply_text(
                "❌ Keyword is too long.\n\n"
                "Please keep it under 100 characters."
            )

            return True

        # Prevent accidentally storing huge
        # multiline text.
        keyword = re.sub(
            r"\s+",
            " ",
            keyword
        ).strip()

        context.user_data[
            "mm_pending_keyword"
        ] = keyword.lower()

        context.user_data[
            "state"
        ] = None

        await update.message.reply_text(
            "🔤 **KEYWORD RECEIVED**\n\n"
            f"Keyword: `{keyword.lower()}`\n\n"
            "Now choose how long this keyword should "
            "be filtered:",
            reply_markup=mm_duration_keyboard(
                "keyword"
            ),
            parse_mode=ParseMode.MARKDOWN
        )

        return True

    # --------------------------------------------------------
    # CUSTOM DURATION
    # --------------------------------------------------------
    if state == "mm_custom_duration":

        raw = (
            update.message.text
            if update.message
            else ""
        )

        raw = raw.strip()

        seconds = mm_parse_duration(raw)

        if seconds == 0:

            await update.message.reply_text(
                "❌ I couldn't understand that duration.\n\n"
                "Use examples such as:\n"
                "`30 seconds`\n"
                "`5 minutes`\n"
                "`2 hours`\n"
                "`3 days`\n"
                "`1 month`\n"
                "`permanent`",
                parse_mode=ParseMode.MARKDOWN
            )

            return True

        prefix = context.user_data.get(
            "mm_duration_prefix"
        )

        if not prefix:

            context.user_data[
                "state"
            ] = None

            await update.message.reply_text(
                "❌ Duration session expired."
            )

            return True

        # Clear input state before applying it.
        context.user_data[
            "state"
        ] = None

        context.user_data.pop(
            "mm_duration_prefix",
            None
        )

        # We don't have a CallbackQuery here,
        # so apply directly.

        until = mm_duration_to_until(
            seconds
        )

        # ------------------------------------
        # BLOCK EVERYONE
        # ------------------------------------
        if prefix == "block_all":

            mm_set_block_everyone_until(
                until
            )

            await update.message.reply_text(
                "✅ **Block Everyone Updated**\n\n"
                f"Duration: **{mm_until_text(until)}**",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "🚫 Block Menu",
                            callback_data="mm_block_menu"
                        )
                    ]
                ]),
                parse_mode=ParseMode.MARKDOWN
            )

            return True

        # ------------------------------------
        # SPECIFIC USER
        # ------------------------------------
        if prefix == "block_user":

            target_user = context.user_data.get(
                "mm_target_user"
            )

            if not target_user:

                await update.message.reply_text(
                    "❌ No target user was selected."
                )

                return True

            mm_block_specific_user(
                int(target_user),
                until
            )

            context.user_data.pop(
                "mm_target_user",
                None
            )

            await update.message.reply_text(
                "✅ **Specific User Blocked**\n\n"
                f"User ID: `{target_user}`\n"
                f"Duration: **{mm_until_text(until)}**",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "👥 Blocked Users",
                            callback_data="mm_blocked_users"
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "🚫 Block Menu",
                            callback_data="mm_block_menu"
                        )
                    ]
                ]),
                parse_mode=ParseMode.MARKDOWN
            )

            return True

        # ------------------------------------
        # LINK FILTER
        # ------------------------------------
        if prefix == "filter_links":

            mm_set_filter_links_until(
                until
            )

            await update.message.reply_text(
                "✅ **Link Filter Updated**\n\n"
                f"Duration: **{mm_until_text(until)}**",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "🔎 Filter Menu",
                            callback_data="mm_filter_menu"
                        )
                    ]
                ]),
                parse_mode=ParseMode.MARKDOWN
            )

            return True

        # ------------------------------------
        # VIDEO FILTER
        # ------------------------------------
        if prefix == "filter_videos":

            mm_set_filter_videos_until(
                until
            )

            await update.message.reply_text(
                "✅ **Video Filter Updated**\n\n"
                f"Duration: **{mm_until_text(until)}**",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "🔎 Filter Menu",
                            callback_data="mm_filter_menu"
                        )
                    ]
                ]),
                parse_mode=ParseMode.MARKDOWN
            )

            return True

        # ------------------------------------
        # KEYWORD
        # ------------------------------------
        if prefix == "keyword":

            keyword = context.user_data.get(
                "mm_pending_keyword"
            )

            if not keyword:

                await update.message.reply_text(
                    "❌ No keyword is waiting to be saved."
                )

                return True

            mm_add_keyword(
                keyword,
                until
            )

            context.user_data.pop(
                "mm_pending_keyword",
                None
            )

            await update.message.reply_text(
                "✅ **Keyword Filter Added**\n\n"
                f"Keyword: `{keyword}`\n"
                f"Duration: **{mm_until_text(until)}**",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "🔤 Keywords",
                            callback_data="mm_filter_keywords"
                        )
                    ]
                ]),
                parse_mode=ParseMode.MARKDOWN
            )

            return True

        await update.message.reply_text(
            "❌ Unknown Message Manager duration setting."
        )

        return True

    return False


# ============================================================
# MESSAGE MANAGER CALLBACK ROUTER WRAPPER
# ============================================================

async def route_message_manager_callback(
    query,
    context
):
    """
    Small wrapper used by the main callback handler.
    """

    data = query.data or ""

    manager_callbacks = (
        data == "message_manager"
        or data.startswith("mm_")
    )

    if not manager_callbacks:
        return False

    await handle_message_manager_callback(
        query,
        context
    )

    return True


# ============================================================
# MESSAGE MANAGER LIVE MESSAGE HANDLING
# ============================================================

def mm_should_refuse_message(sender_id, event):
    """
    Decide whether an incoming private message should be refused.

    Rules:
    1. Admins are never blocked by Message Manager.
    2. If Messaging is OFF -> refuse.
    3. If Block Everyone is active -> refuse.
    4. If this user is specifically blocked -> refuse.
    5. If a link filter matches -> refuse.
    6. If a video filter matches -> refuse.
    7. If a keyword filter matches -> refuse.
    """

    try:
        sender_id = int(sender_id)
    except Exception:
        return True

    # Admins are always allowed.
    if sender_id in ADMIN_IDS:
        return False

    # Main messaging switch.
    if not mm_is_messaging_on():
        return True

    # Existing Message Manager rule engine.
    try:
        return bool(mm_should_block_message(sender_id, event))
    except Exception as e:
        print(f"Message Manager decision error: {e}")
        return False


async def mm_send_refusal(event, blocked=False):
    """
    Send an automatic response to the person whose message
    was refused.

    This does NOT Telegram-block the user.
    It simply refuses the incoming message.
    """

    try:
        if blocked:
            text = "🚫 Your message has been blocked by the administrator."
        else:
            text = "🤖 Sorry, messaging is currently unavailable.\n\nPlease try again later."
        await event.respond(text)
    except Exception as e:
        print(f"Message Manager refusal error: {e}")


# ------------------------------------------------------------
# SENDER INFORMATION
# ------------------------------------------------------------

def mm_get_sender_name(sender):
    try:
        first = getattr(sender, "first_name", "") or ""
        last = getattr(sender, "last_name", "") or ""

        full_name = f"{first} {last}".strip()

        if full_name:
            return full_name

        username = getattr(sender, "username", None)

        if username:
            return f"@{username}"

        return "Unknown User"

    except Exception:
        return "Unknown User"


def mm_get_sender_username(sender):
    try:
        username = getattr(sender, "username", None)

        if username:
            return f"@{username}"

        return "N/A"

    except Exception:
        return "N/A"


def mm_get_message_preview(event):
    """
    Creates a safe text representation of the incoming message.
    """

    try:
        text = event.raw_text or ""

        if text.strip():
            return text.strip()

        if getattr(event.message, "photo", None):
            return "📷 Photo"

        if getattr(event.message, "video", None):
            return "🎥 Video"

        if getattr(event.message, "voice", None):
            return "🎤 Voice message"

        if getattr(event.message, "audio", None):
            return "🎵 Audio"

        if getattr(event.message, "document", None):
            return "📎 Document"

        if getattr(event.message, "sticker", None):
            return "😀 Sticker"

        if getattr(event.message, "gif", None):
            return "🎞 GIF"

        return "📩 Media message"

    except Exception:
        return "📩 Message"


# ------------------------------------------------------------
# ADMIN NOTIFICATION
# ------------------------------------------------------------

def mm_build_admin_notification(sender, event):
    """
    Build the notification that the admin receives.
    """

    sender_id = getattr(sender, "id", 0)
    name = mm_get_sender_name(sender)
    username = mm_get_sender_username(sender)
    preview = mm_get_message_preview(event)

    try:
        message_date = event.date.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        message_date = "Unknown"

    text = (
        "📩 NEW MESSAGE\n\n"
        f"👤 From: {name}\n"
        f"🆔 User ID: {sender_id}\n"
        f"🔗 Username: {username}\n"
        f"🕐 Time: {message_date}\n\n"
        "👇 Original message:\n"
        f"{preview}"
    )

    # Prevent extremely large Telegram messages.
    if len(text) > 3900:
        text = text[:3890] + "\n..."

    return text


# ------------------------------------------------------------
# FORWARD ORIGINAL MESSAGE TO ADMIN
# ------------------------------------------------------------

async def mm_forward_original_message(event, admin_id):
    """
    Forward the original Telegram message to the admin.

    This uses the connected Telethon account.

    If forwarding fails, the system will still try to send
    the text notification.
    """

    try:
        await telethon_client.forward_messages(
            entity=admin_id,
            messages=event.message
        )

        return True

    except Exception as e:
        print(
            f"Message Manager forward error "
            f"(admin={admin_id}): {e}"
        )

        return False


# ------------------------------------------------------------
# SEND ADMIN CONTROL MESSAGE
# ------------------------------------------------------------

async def mm_send_admin_control_message(
    admin_id,
    sender,
    event
):
    """
    Sends the admin a control message with:

    💬 Reply
    🚫 Block User
    """

    global PTB_BOT

    sender_id = getattr(sender, "id", 0)

    notification = mm_build_admin_notification(
        sender,
        event
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "💬 Reply",
                callback_data=f"mm_reply|{sender_id}"
            ),
            InlineKeyboardButton(
                "🚫 Block User",
                callback_data=f"mm_block_from_message|{sender_id}"
            )
        ]
    ])

    # Preferred method:
    # Bot API message gives us working CallbackQuery buttons.
    if PTB_BOT is not None:
        try:
            await PTB_BOT.send_message(
                chat_id=admin_id,
                text=notification,
                reply_markup=keyboard
            )

            return True

        except Exception as e:
            print(
                f"Message Manager Bot API notification "
                f"error: {e}"
            )

    # Fallback:
    # If Bot API cannot message the admin, use Telethon.
    try:
        await telethon_client.send_message(
            admin_id,
            notification
        )

        return True

    except Exception as e:
        print(
            f"Message Manager fallback notification "
            f"error: {e}"
        )

    return False


# ------------------------------------------------------------
# FORWARD COMPLETE INCOMING MESSAGE
# ------------------------------------------------------------

async def mm_forward_incoming_message(event, sender):
    """
    Forward an allowed incoming private message to every
    configured admin.

    Two things are sent:

    1. The original Telegram message.
    2. An admin control message containing Reply / Block User.
    """

    if not ADMIN_IDS:
        print(
            "⚠️ Message Manager: ADMIN_IDS is empty. "
            "No administrator is configured."
        )

        return False

    success = False

    for admin_id in ADMIN_IDS:

        try:
            # First forward the actual Telegram message.
            forwarded = await mm_forward_original_message(
                event,
                admin_id
            )

            # Then send the control/metadata message.
            control_sent = await mm_send_admin_control_message(
                admin_id,
                sender,
                event
            )

            if forwarded or control_sent:
                success = True

        except Exception as e:
            print(
                f"Message Manager admin forwarding error "
                f"(admin={admin_id}): {e}"
            )

    return success


# ------------------------------------------------------------
# MESSAGE MANAGER — REPLY TO USER
# ------------------------------------------------------------

async def handle_mm_reply_input(update, context):
    """
    Handles the administrator's reply after pressing
    the Reply button.
    """

    if update.effective_user is None:
        return True

    admin_id = update.effective_user.id

    if not is_admin(admin_id):
        return True

    text = ""

    if update.message:
        text = update.message.text or ""

    text = text.strip()

    if not text:
        await update.message.reply_text(
            "⚠️ Please type a text message to send."
        )
        return True

    target_user = context.user_data.get("mm_reply_to")

    if not target_user:
        context.user_data["state"] = None

        await update.message.reply_text(
            "❌ Reply target was not found.\n\n"
            "Please press 💬 Reply again."
        )

        return True

    try:
        target_user = int(target_user)
    except Exception:
        context.user_data["mm_reply_to"] = None
        context.user_data["state"] = None

        await update.message.reply_text(
            "❌ Invalid reply target."
        )

        return True

    try:
        await telethon_client.send_message(
            target_user,
            text
        )

        context.user_data["mm_reply_to"] = None
        context.user_data["state"] = None

        await update.message.reply_text(
            "✅ Reply sent successfully!"
        )

    except FloodWaitError as e:

        wait_seconds = getattr(
            e,
            "seconds",
            0
        )

        await update.message.reply_text(
            "⏳ Telegram temporarily limited the account.\n\n"
            f"Please wait about {wait_seconds} seconds "
            "and try again."
        )

    except Exception as e:

        print(
            f"Message Manager reply error: {e}"
        )

        context.user_data["mm_reply_to"] = None
        context.user_data["state"] = None

        await update.message.reply_text(
            "❌ Could not send the reply.\n\n"
            "The user may have blocked the account, "
            "deleted the account, or Telegram may have "
            "restricted messaging."
        )

    return True


# ------------------------------------------------------------
# MESSAGE MANAGER — BLOCK USER FROM ADMIN NOTIFICATION
# ------------------------------------------------------------

async def handle_mm_message_action_callback(
    query,
    context
):
    """
    Handles buttons attached to a NEW MESSAGE notification.

    Supported callbacks:

    mm_reply|USER_ID
    mm_block_from_message|USER_ID
    """

    data = query.data or ""

    # --------------------------------------------------------
    # REPLY
    # --------------------------------------------------------

    if data.startswith("mm_reply|"):

        if not is_admin(query.from_user.id):
            await query.answer(
                "❌ Admin only.",
                show_alert=True
            )
            return True

        try:
            target_user = int(
                data.split("|", 1)[1]
            )
        except Exception:
            await query.answer(
                "❌ Invalid user ID.",
                show_alert=True
            )
            return True

        context.user_data["mm_reply_to"] = target_user
        context.user_data["state"] = "mm_reply_text"

        await query.answer()

        await query.message.reply_text(
            "💬 REPLY MODE\n\n"
            f"🆔 User ID: {target_user}\n\n"
            "✍️ Type the message you want to send.\n\n"
            "Example:\n"
            "Hello! I received your message. 😊\n\n"
            "Send /cancel to cancel."
        )

        return True

    # --------------------------------------------------------
    # BLOCK SPECIFIC USER
    # --------------------------------------------------------

    if data.startswith("mm_block_from_message|"):

        if not is_admin(query.from_user.id):
            await query.answer(
                "❌ Admin only.",
                show_alert=True
            )
            return True

        try:
            target_user = int(
                data.split("|", 1)[1]
            )
        except Exception:
            await query.answer(
                "❌ Invalid user ID.",
                show_alert=True
            )
            return True

        context.user_data["mm_target_user"] = target_user

        await query.answer()

        await query.message.reply_text(
            "🚫 BLOCK USER\n\n"
            f"🆔 User ID: {target_user}\n\n"
            "Choose how long this user should be "
            "blocked from Message Manager:"
        )

        # Reuse the existing duration keyboard.
        keyboard = mm_duration_keyboard(
            "block_user"
        )

        await query.message.reply_text(
            "⏱ Select duration:",
            reply_markup=keyboard
        )

        return True

    return False


# ------------------------------------------------------------
# MESSAGE MANAGER — LIVE INCOMING MESSAGE PROCESSOR
# ------------------------------------------------------------

async def process_message_manager_incoming(event):
    """
    Main gatekeeper.

    Flow:

        Incoming message
               ↓
        Save to Inbox
               ↓
        Is admin?
          /         \
        YES          NO
         ↓            ↓
       Allow      Message Manager
                      ↓
               ON or OFF?
                 ↓
             Rules check
              /       \
           BLOCK      ALLOW
             ↓          ↓
          Refuse      Forward
                       to admin
    """

    try:
        if not event.is_private:
            return

        if event.out:
            return

        sender = await event.get_sender()

        if sender is None:
            return

        sender_id = getattr(
            sender,
            "id",
            None
        )

        if not sender_id:
            return

        # ----------------------------------------------------
        # ALWAYS SAVE TO EXISTING INBOX
        # ----------------------------------------------------

        try:
            add_inbox_message(
                event.chat_id,
                sender_id,
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
                event.raw_text or "",
                get_media_type(event),
                str(event.date)
            )
        except Exception as e:
            print(
                f"Inbox save error: {e}"
            )

        # ----------------------------------------------------
        # ADMIN BYPASS
        # ----------------------------------------------------

        if sender_id in ADMIN_IDS:
            return

        # ----------------------------------------------------
        # MESSAGE MANAGER DECISION
        # ----------------------------------------------------

        should_refuse = mm_should_refuse_message(
            sender_id,
            event
        )

        if should_refuse:

            print(
                "🚫 Message Manager refused message "
                f"from {sender_id}"
            )

            await mm_send_refusal(
                event,
                blocked=True
            )

            return

        # ----------------------------------------------------
        # MESSAGE IS ALLOWED
        # ----------------------------------------------------

        print(
            "✅ Message Manager allowed message "
            f"from {sender_id}"
        )

        forwarded = await mm_forward_incoming_message(
            event,
            sender
        )

        # ----------------------------------------------------
        # NO ADMIN CONFIGURED
        # ----------------------------------------------------

        if not forwarded:

            print(
                "⚠️ Message Manager could not "
                "forward message to an admin."
            )

            try:
                await event.respond(
                    "⚠️ Sorry, this account is "
                    "temporarily unavailable.\n\n"
                    "Please try again later. 🙏"
                )
            except Exception:
                pass

    except Exception as e:

        print(
            "❌ Message Manager incoming "
            f"processing error: {e}"
        )


# ------------------------------------------------------------
# MESSAGE MANAGER — CANCEL REPLY
# ------------------------------------------------------------

async def mm_cancel_reply(update, context):

    if update.effective_user is None:
        return False

    if not is_admin(update.effective_user.id):
        return False

    state = context.user_data.get("state")

    if state != "mm_reply_text":
        return False

    context.user_data["state"] = None
    context.user_data["mm_reply_to"] = None

    await update.message.reply_text(
        "❌ Reply cancelled."
    )

    return True


# ============================================================
# QR CODE TOOLS
# ============================================================
# NOTE: Insert the QR button into your Converter keyboard as described in the instructions.
# Also, add the QR callback handling inside `menu_callback()` and the input handling inside `handle_link()`.
# The functions below are ready to be used.
# ============================================================

import qrcode
import cv2
import numpy as np


def create_qr_image_sync(text):
    """
    Create a QR code image locally.
    No external API required.
    """

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=12,
        border=4,
    )

    qr.add_data(text)
    qr.make(fit=True)

    img = qr.make_image(
        fill_color="black",
        back_color="white"
    )

    output = BytesIO()
    img.save(output, format="PNG")
    output.seek(0)

    return output


def scan_qr_image_sync(image_bytes):
    """
    Read one or more QR codes from an image.
    Returns a list of decoded results.
    """

    image_array = bytearray(image_bytes)

    np_array = np.frombuffer(
        image_array,
        dtype=np.uint8
    )

    image = cv2.imdecode(
        np_array,
        cv2.IMREAD_COLOR
    )

    if image is None:
        raise ValueError(
            "Could not open the image."
        )

    detector = cv2.QRCodeDetector()

    results = []

    # --------------------------------------------------------
    # Try detecting multiple QR codes first.
    # --------------------------------------------------------

    try:
        retval, decoded_info, points, straight_qrcode = (
            detector.detectAndDecodeMulti(image)
        )

        if retval and decoded_info:

            for value in decoded_info:

                if value and value.strip():

                    value = value.strip()

                    if value not in results:
                        results.append(value)

    except Exception as e:
        print(
            f"Multi QR detection error: {e}"
        )

    # --------------------------------------------------------
    # Fallback to normal single QR detection.
    # --------------------------------------------------------

    if not results:

        try:
            data, points, _ = (
                detector.detectAndDecode(image)
            )

            if data and data.strip():
                results.append(data.strip())

        except Exception as e:
            print(
                f"Single QR detection error: {e}"
            )

    return results


def qr_menu_keyboard():

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "📷 Scan QR",
                callback_data="qr_scan"
            ),
            InlineKeyboardButton(
                "🎨 Create QR",
                callback_data="qr_create"
            )
        ],
        [
            InlineKeyboardButton(
                "⬅️ Back",
                callback_data="converter"
            )
        ]
    ])


async def show_qr_menu(update, context):

    query = update.callback_query

    try:
        await query.answer()
    except Exception:
        pass

    context.user_data["qr_mode"] = None
    context.user_data["state"] = None

    text = (
        "📱 QR CODE TOOLS\n\n"
        "Choose what you want to do:\n\n"
        "📷 Scan QR\n"
        "Send a QR-code image and I will read "
        "the information inside it.\n\n"
        "🎨 Create QR\n"
        "Send text, a website, contact information, "
        "or another supported value and I will create "
        "a QR-code image for you."
    )

    await query.message.reply_text(
        text,
        reply_markup=qr_menu_keyboard()
    )


async def start_qr_scan(update, context):

    query = update.callback_query

    try:
        await query.answer()
    except Exception:
        pass

    context.user_data["qr_mode"] = "scan"
    context.user_data["state"] = "qr_scan"

    await query.message.reply_text(
        "📷 SCAN QR CODE\n\n"
        "Send me a photo containing a QR code.\n\n"
        "I will automatically read the QR code "
        "and show you its contents.\n\n"
        "💡 You can send a screenshot or a normal photo."
    )


async def start_qr_create(update, context):

    query = update.callback_query

    try:
        await query.answer()
    except Exception:
        pass

    context.user_data["qr_mode"] = "create"
    context.user_data["state"] = "qr_create"

    await query.message.reply_text(
        "🎨 CREATE QR CODE\n\n"
        "Send me the text or link you want to put "
        "inside the QR code.\n\n"
        "Example:\n"
        "https://example.com\n\n"
        "or:\n"
        "Hello from Ethiopia 🇪🇹"
    )


async def handle_qr_photo(update, context):

    if context.user_data.get("state") != "qr_scan":
        return False

    if not update.message or not update.message.photo:

        await update.message.reply_text(
            "❌ Please send a QR-code image."
        )

        return True

    status = await update.message.reply_text(
        "🔍 Scanning QR code...\n\n"
        "Please wait."
    )

    temp_path = None

    try:

        photo = update.message.photo[-1]

        telegram_file = await context.bot.get_file(
            photo.file_id
        )

        with tempfile.NamedTemporaryFile(
            suffix=".jpg",
            delete=False
        ) as temp:

            temp_path = temp.name

        await telegram_file.download_to_drive(
            custom_path=temp_path
        )

        with open(
            temp_path,
            "rb"
        ) as image_file:

            image_bytes = image_file.read()

        results = await asyncio.to_thread(
            scan_qr_image_sync,
            image_bytes
        )

        if not results:

            await status.edit_text(
                "❌ No readable QR code was found.\n\n"
                "Try sending a clearer image with the "
                "complete QR code visible."
            )

            return True

        lines = [
            "✅ QR CODE FOUND!",
            ""
        ]

        for index, value in enumerate(
            results,
            start=1
        ):

            lines.append(
                f"📌 QR #{index}"
            )

            lines.append(value)

            lines.append("")

        lines.append(
            "📷 You can send another QR image anytime."
        )

        result_text = "\n".join(lines)

        if len(result_text) > 3900:
            result_text = (
                result_text[:3890]
                + "\n..."
            )

        await status.edit_text(
            result_text
        )

    except Exception as e:

        print(
            f"QR scan error: {e}"
        )

        await status.edit_text(
            "❌ QR scanning failed.\n\n"
            f"Error: {str(e)[:1000]}"
        )

    finally:

        if temp_path:

            try:
                os.remove(temp_path)
            except Exception:
                pass

        context.user_data["state"] = "qr_scan"

    return True


async def handle_qr_create(update, context):

    if context.user_data.get("state") != "qr_create":
        return False

    if not update.message:
        return True

    text = (
        update.message.text
        or update.message.caption
        or ""
    ).strip()

    if not text:

        await update.message.reply_text(
            "❌ Please send text or a link "
            "to put inside the QR code."
        )

        return True

    if len(text) > 4000:

        await update.message.reply_text(
            "❌ The content is too long.\n\n"
            "Please use 4000 characters or less."
        )

        return True

    status = await update.message.reply_text(
        "🎨 Creating QR code..."
    )

    try:

        qr_image = await asyncio.to_thread(
            create_qr_image_sync,
            text
        )

        await status.delete()

        await update.message.reply_photo(
            photo=qr_image,
            caption=(
                "✅ QR code created successfully!\n\n"
                "📌 Content:\n"
                f"{text[:900]}"
            ),
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "📱 QR Code Menu",
                        callback_data="qr_menu"
                    )
                ]
            ])
        )

    except Exception as e:

        print(
            f"QR creation error: {e}"
        )

        await status.edit_text(
            "❌ Could not create QR code.\n\n"
            f"Error: {str(e)[:1000]}"
        )

    context.user_data["state"] = "qr_create"

    return True


# ============================================================
# END OF QR CODE TOOLS
# ============================================================
