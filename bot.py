import re
import asyncio
import os
import threading
from io import BytesIO
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler, ContextTypes
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError, ChannelPrivateError, UsernameNotOccupiedError, MessageIdInvalidError

# --- FLASK SETUP (Keeps Render awake) ---
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is alive!"

# --- CONFIGURATION (READ FROM RENDER ENVIRONMENT VARIABLES) ---
BOT_TOKEN = os.environ.get('BOT_TOKEN', '')
API_ID = int(os.environ.get('API_ID', 0))
API_HASH = os.environ.get('API_HASH', '')
STRING_SESSION = os.environ.get('STRING_SESSION', '')

# --- NEW PASSWORD CONFIGURATION ---
BOT_PASSWORD = os.environ.get("BOT_PASSWORD", "ptss25")

telethon_client = TelegramClient(StringSession(STRING_SESSION), API_ID, API_HASH)

# --- GLOBAL AUTHENTICATION STATE ---
authenticated_users = set()

# --- INBOX DATA STORE ---
inbox_data = {}

# --- HELPER: GET MEDIA TYPE ---
def get_media_type(event):
    if event.photo:
        return "📷 Photo"
    elif event.video:
        return "🎬 Video"
    elif event.document:
        if event.gif:
            return "🎞️ GIF"
        return "📄 Document"
    elif event.audio:
        return "🎵 Audio"
    elif event.voice:
        return "🎤 Voice"
    elif event.sticker:
        return "🧩 Sticker"
    else:
        return "💬 Text"

# --- EXISTING SAFE MEDIA SENDER ---
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
            await bot.send_message(chat_id, "⚠️ This is an unsupported message type (e.g., Poll, Location).")
    except Exception as e:
        error_str = str(e)
        if "must forward even restricted" in error_str.lower() or "cannot be reused" in error_str.lower():
            await bot.send_message(chat_id, "🔒 [RISK TAKEN] Telegram is actively blocking the download of this specific 'Restricted Saving Content' media.")
        else:
            await bot.send_message(chat_id, f"Failed to send media: {error_str}")

# --- EXISTING DYNAMIC ERROR HANDLER ---
async def handle_telethon_error(update, error):
    error_str = str(error)
    if isinstance(error, FloodWaitError):
        await update.message.reply_text(f"⚠️ Too many requests! Please wait {error.seconds} seconds.")
    elif isinstance(error, ChannelPrivateError):
        await update.message.reply_text("🔒 This channel is private, or I do not have access to it.")
    elif isinstance(error, UsernameNotOccupiedError):
        await update.message.reply_text("❌ The channel username provided does not exist.")
    elif isinstance(error, MessageIdInvalidError):
        await update.message.reply_text("❌ The message ID provided is invalid or too old.")
    elif "TimeoutError" in error_str:
        await update.message.reply_text("⏳ Connection timed out. Check your internet connection.")
    else:
        await update.message.reply_text(f"❌ An unexpected error occurred:\n{error_str}")

# --- EXISTING FETCHER HELPERS ---
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

# --- PASSWORD & AUTH COMMANDS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in authenticated_users:
        context.user_data['state'] = 'awaiting_password'
        await update.message.reply_text(
            "🔐 TELEGRAM ASSISTANT\n\n"
            "Password required.\n\n"
            "Please enter the password to continue."
        )
        return

    keyboard = [
        [InlineKeyboardButton("📥 Inbox", callback_data="inbox"), InlineKeyboardButton("👤 Profile", callback_data="profile")],
        [InlineKeyboardButton("🔗 Fetch Telegram", callback_data="fetch")],
        [InlineKeyboardButton("➕ More Commands", callback_data="more")]
    ]
    await update.message.reply_text(
        "🤖 TELEGRAM ASSISTANT\n\nChoose an option:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def logout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in authenticated_users:
        authenticated_users.remove(user_id)
    await update.message.reply_text(
        "🔒 You have been logged out.\n\nPlease enter the password again to continue."
    )

# --- MENU CALLBACK (PROTECTED) ---
async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id

    # GLOBAL AUTH CHECK
    if user_id not in authenticated_users:
        await query.edit_message_text("🔐 Password required.\n\nPlease enter the bot password first.")
        return

    if query.data == "inbox":
        if not inbox_data:
            text = "📥 INBOX\n\nNo new private messages detected."
            keyboard = [[InlineKeyboardButton("⬅️ Back", callback_data="main_menu")]]
        else:
            text = "📥 INBOX\n"
            keyboard = []
            for chat_id, data in inbox_data.items():
                last_msg = data['messages'][-1]
                content = last_msg['text'] if last_msg['text'] else f"[{last_msg['media_type']}]"
                text += f"\n👤 {data['first_name']} {data['last_name']}\n"
                text += f"🔴 {data['unread']} new message(s)\n"
                text += f"\"{content}\"\n"
                keyboard.append([InlineKeyboardButton(f"👤 {data['first_name']} {data['last_name']}", callback_data=f"conv_{chat_id}")])
            keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="main_menu")])
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

    elif query.data.startswith("conv_"):
        chat_id = int(query.data.split("_")[1])
        data = inbox_data[chat_id]
        data['unread'] = 0
        text = f"💬 CHAT WITH {data['first_name'].upper()}\n\n"
        for msg in data['messages'][-5:]:
            sender_name = data['first_name'] if msg['sender_id'] == data['sender_id'] else "Me"
            content = msg['text'] if msg['text'] else f"[{msg['media_type']}]"
            text += f"{sender_name}:\n{content}\n\n"
        keyboard = [
            [InlineKeyboardButton("↩️ Reply", callback_data=f"reply_{chat_id}"), InlineKeyboardButton("👤 Profile", callback_data="profile")],
            [InlineKeyboardButton("🔄 Refresh", callback_data=f"refresh_{chat_id}"), InlineKeyboardButton("⬅️ Back", callback_data="inbox")]
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

    elif query.data.startswith("reply_"):
        chat_id = int(query.data.split("_")[1])
        context.user_data['reply_to'] = chat_id
        await query.edit_message_text(f"💬 Type your reply to {inbox_data[chat_id]['first_name']}:")

    elif query.data.startswith("refresh_"):
        chat_id = int(query.data.split("_")[1])
        data = inbox_data[chat_id]
        data['unread'] = 0
        text = f"💬 CHAT WITH {data['first_name'].upper()}\n\n"
        for msg in data['messages'][-5:]:
            sender_name = data['first_name'] if msg['sender_id'] == data['sender_id'] else "Me"
            content = msg['text'] if msg['text'] else f"[{msg['media_type']}]"
            text += f"{sender_name}:\n{content}\n\n"
        keyboard = [
            [InlineKeyboardButton("↩️ Reply", callback_data=f"reply_{chat_id}"), InlineKeyboardButton("👤 Profile", callback_data="profile")],
            [InlineKeyboardButton("🔄 Refresh", callback_data=f"refresh_{chat_id}"), InlineKeyboardButton("⬅️ Back", callback_data="inbox")]
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

    elif query.data == "profile":
        await query.edit_message_text("👤 PROFILE\n\nPlease enter the Telegram username or ID you want to inspect:")
        context.user_data['state'] = 'profile_query'

    elif query.data == "fetch":
        await query.edit_message_text("🔗 Fetch Telegram\n\nPlease send me a Telegram link (e.g., https://t.me/channel/123):")
        context.user_data['state'] = 'fetch_link'

    elif query.data == "more":
        keyboard = [
            [InlineKeyboardButton("🤖 AI Agent", callback_data="ai"), InlineKeyboardButton("⚙️ Settings", callback_data="settings")],
            [InlineKeyboardButton("📊 Statistics", callback_data="stats"), InlineKeyboardButton("🔔 Notifications", callback_data="notifications")],
            [InlineKeyboardButton("⬅️ Back", callback_data="main_menu")]
        ]
        await query.edit_message_text("➕ MORE COMMANDS\n\nChoose a feature:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif query.data == "main_menu":
        keyboard = [
            [InlineKeyboardButton("📥 Inbox", callback_data="inbox"), InlineKeyboardButton("👤 Profile", callback_data="profile")],
            [InlineKeyboardButton("🔗 Fetch Telegram", callback_data="fetch")],
            [InlineKeyboardButton("➕ More Commands", callback_data="more")]
        ]
        await query.edit_message_text("🤖 Telegram Assistant\nChoose an option:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif query.data == "ai" or query.data == "settings" or query.data == "stats" or query.data == "notifications":
        await query.edit_message_text("🚧 Coming soon!")

# --- PROFILE FETCH HANDLER ---
async def profile_fetch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get('state') == 'profile_query':
        target = update.message.text
        context.user_data['state'] = None
        try:
            entity = await telethon_client.get_entity(target)
            first_name = getattr(entity, 'first_name', 'N/A')
            last_name = getattr(entity, 'last_name', '')
            username = getattr(entity, 'username', 'N/A')
            user_id = entity.id
            user_type = "Bot" if entity.bot else "User"
            bio = getattr(entity, 'about', 'No bio')
            text = f"👤 PROFILE\n\nName: {first_name} {last_name}\nUsername: @{username}\nID: {user_id}\nType: {user_type}\nBio: {bio}"
            try:
                photo = await telethon_client.download_profile_photo(entity, file=BytesIO())
                photo.seek(0)
                await update.message.reply_photo(photo=photo, caption=text)
            except Exception:
                await update.message.reply_text(text)
        except Exception as e:
            await update.message.reply_text(f"❌ Error finding user: {str(e)}")

# --- EXISTING FETCHER HANDLER (Modified for Password Check) ---
async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text

    # 1. Handle Password Input
    if context.user_data.get('state') == 'awaiting_password':
        if text == BOT_PASSWORD:
            authenticated_users.add(user_id)
            context.user_data['state'] = None
            await update.message.reply_text("✅ Access granted!")
            keyboard = [
                [InlineKeyboardButton("📥 Inbox", callback_data="inbox"), InlineKeyboardButton("👤 Profile", callback_data="profile")],
                [InlineKeyboardButton("🔗 Fetch Telegram", callback_data="fetch")],
                [InlineKeyboardButton("➕ More Commands", callback_data="more")]
            ]
            await update.message.reply_text("🤖 TELEGRAM ASSISTANT\n\nChoose an option:", reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await update.message.reply_text("❌ Incorrect password.\n\nPlease try again.")
        return

    # 2. Global Auth Check for any other text
    if user_id not in authenticated_users:
        await update.message.reply_text("🔐 Password required.\n\nPlease enter the bot password first.")
        return

    # 3. Handle Reply State
    if context.user_data.get('reply_to'):
        target_chat = context.user_data['reply_to']
        try:
            await telethon_client.send_message(target_chat, text)
            context.user_data['reply_to'] = None
            if target_chat in inbox_data:
                inbox_data[target_chat]['messages'].append({
                    'id': 0,
                    'text': text,
                    'media_type': '💬 Text',
                    'sender_id': API_ID,
                    'date': 'Just now'
                })
            await update.message.reply_text("✅ Reply sent!")
        except Exception as e:
            await update.message.reply_text(f"❌ Failed to send reply: {str(e)}")
        return

    # 4. Handle Profile State
    if context.user_data.get('state') == 'profile_query':
        await profile_fetch(update, context)
        return

    # 5. Standard Fetch Link Logic (Preserved)
    if "t.me" in text:
        username, msg_id = parse_tg_link(text)
        if not username:
            await update.message.reply_text("Invalid link format.")
            return
        context.user_data['state'] = None
        try:
            entity = await telethon_client.get_entity(username)
            if msg_id:
                msg = await telethon_client.get_messages(entity, ids=msg_id)
                await safe_send(update.message.chat_id, context.bot, msg, from_chat_id=entity.id, message_id=msg.id)
            else:
                status_msg = await update.message.reply_text("Fetching batch (max 20 results)...")
                messages = await telethon_client.get_messages(entity, limit=20)
                if not messages:
                    await status_msg.edit_text("No messages found in this channel.")
                    return
                for idx, msg in enumerate(messages, 1):
                    if idx % 5 == 0 or idx == len(messages):
                        await status_msg.edit_text(f"Fetching {idx}/{len(messages)}...")
                    await safe_send(update.message.chat_id, context.bot, msg, from_chat_id=entity.id, message_id=msg.id)
                await status_msg.edit_text(f"✅ Batch complete! Fetched {len(messages)} messages.")
        except Exception as e:
            await handle_telethon_error(update, e)
    else:
        await update.message.reply_text("👋 Please use the menu buttons to navigate, or send a Telegram link to fetch.")

# --- NEW TELETHON PRIVATE INBOX LISTENER ---
@telethon_client.on(events.NewMessage(incoming=True))
async def inbox_listener(event):
    if event.is_private and not event.out:
        try:
            sender = await event.get_sender()
            chat_id = event.chat_id
            sender_id = sender.id
            first_name = getattr(sender, 'first_name', 'Unknown')
            last_name = getattr(sender, 'last_name', '')
            username = getattr(sender, 'username', 'No Username')
            text = event.raw_text if event.raw_text else ""
            media_type = get_media_type(event)

            if chat_id not in inbox_data:
                inbox_data[chat_id] = {
                    'sender_id': sender_id,
                    'first_name': first_name,
                    'last_name': last_name,
                    'username': username,
                    'messages': [],
                    'unread': 0
                }

            inbox_data[chat_id]['messages'].append({
                'id': event.id,
                'text': text,
                'media_type': media_type,
                'sender_id': sender_id,
                'date': str(event.date)
            })
            inbox_data[chat_id]['unread'] += 1

            if len(inbox_data[chat_id]['messages']) > 50:
                inbox_data[chat_id]['messages'] = inbox_data[chat_id]['messages'][-50:]
        except Exception as e:
            print(f"Inbox Listener Error: {e}")

# --- MAIN EXECUTION ---
async def main():
    try:
        print("Attempting to connect Telethon...")
        await telethon_client.start()
        print("Telethon connected successfully!")
    except Exception as e:
        print(f"❌ Telethon connection failed (Check your STRING_SESSION). Error: {e}")
        return

    bot_app = Application.builder().token(BOT_TOKEN).build()
    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(CommandHandler("logout", logout))
    bot_app.add_handler(CallbackQueryHandler(menu_callback))
    bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))

    print("Bot is running...")

    await bot_app.initialize()
    await bot_app.start()
    await bot_app.updater.start_polling()

    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=10000), daemon=True).start()

    await asyncio.Event().wait()

if __name__ == '__main__':
    asyncio.run(main())
