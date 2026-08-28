import re
import asyncio
from io import BytesIO
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler, ContextTypes
from telethon import TelegramClient
from telethon.errors import FloodWaitError, ChannelPrivateError, UsernameNotOccupiedError, MessageIdInvalidError

# --- CONFIGURATION ---
# ⚠️ REPLACE WITH YOUR OWN SECURE CREDENTIALS
BOT_TOKEN = '8958561939:AAHlgrekgfWpD6P9CthJXl6DDExKBt6xJSI' 
API_ID = 31232460 
API_HASH = 'b4c8adec7803669bf198a72dd534896e'

telethon_client = TelegramClient('session', API_ID, API_HASH)

# --- HELPER: SAFE MEDIA SENDER (STRICT DOWNLOAD & REUPLOAD, NO FORWARDING) ---
async def safe_send(chat_id, bot, msg, from_chat_id, message_id):
    """Strictly downloads media and reuploads. Never forwards."""
    text = msg.message  # Caption or text content
    try:
        # Attempt to download any media type
        if msg.photo or msg.video or msg.document or msg.voice or msg.audio or msg.gif:
            media_bytes = BytesIO()
            
            # TRY DOWNLOAD (User's explicit request - ignore restricted flags)
            await telethon_client.download_media(msg, file=media_bytes)
            media_bytes.seek(0) 

            # Send the downloaded media with its caption
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
        
        # If it's just a sticker
        elif msg.sticker:
            sticker_bytes = BytesIO()
            await telethon_client.download_media(msg, file=sticker_bytes)
            sticker_bytes.seek(0)
            await bot.send_sticker(chat_id, sticker=sticker_bytes)
            
        # If it's plain text
        elif text:
            await bot.send_message(chat_id, text=text)
            
        # If it's something else (Polls, Locations, etc.)
        else:
            await bot.send_message(chat_id, "⚠️ This is an unsupported message type (e.g., Poll, Location).")
            
    except Exception as e:
        error_str = str(e)
        
        # STRICT BLOCK: Do NOT forward. Report the block instead.
        if "must forward even restricted" in error_str.lower() or "cannot be reused" in error_str.lower():
            await bot.send_message(chat_id, "🔒 [RISK TAKEN] Telegram is actively blocking the download of this specific 'Restricted Saving Content' media. The code is fully prepared to download and re-upload the moment this restriction is lifted or bypassed. No forwarding was used as per user request.")
        
        # Handle other errors
        else:
            await bot.send_message(chat_id, f"Failed to send media: {error_str}")

# --- HELPER: DYNAMIC ERROR HANDLER ---
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

# --- HELPERS ---
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

# --- BOT COMMANDS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Hi! Send me a Telegram link (public channel or specific message), and I'll fetch it for you."
    )

async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if "t.me" not in text:
        await update.message.reply_text("Please send a valid Telegram link (e.g., https://t.me/...).")
        return
    context.user_data[update.effective_user.id] = text
    keyboard = [
        [
            InlineKeyboardButton("1. Fetch Result", callback_data="single"),
            InlineKeyboardButton("2. Batch (Max 20)", callback_data="batch")
        ]
    ]
    await update.message.reply_text("Choose an option:", reply_markup=InlineKeyboardMarkup(keyboard))

# --- BUTTON HANDLERS ---
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    link = context.user_data.get(update.effective_user.id)
    if not link:
        await query.message.reply_text("Link not found. Please send a link again.")
        return

    username, msg_id = parse_tg_link(link)

    # Option 1: Single Result
    if query.data == "single":
        await query.message.reply_text("Fetching single result...")
        if not username or not msg_id:
            await query.message.reply_text("For single fetch, please provide a specific message link (e.g., t.me/channel/123).")
            return
        try:
            entity = await telethon_client.get_entity(username)
            msg = await telethon_client.get_messages(entity, ids=msg_id)
            # Pass entity.id and msg.id for reference, though safe_send will force download
            await safe_send(query.message.chat_id, context.bot, msg, from_chat_id=entity.id, message_id=msg.id)
        except Exception as e:
            await handle_telethon_error(query, e)

    # Option 2: Batch Fetch (Max 20)
    elif query.data == "batch":
        status_msg = await query.message.reply_text("Fetching batch (max 20 results)...")
        if not username:
            await status_msg.edit_text("Please provide a valid channel link (e.g., t.me/channel_name).")
            return
        try:
            entity = await telethon_client.get_entity(username)
            messages = await telethon_client.get_messages(entity, limit=20)
            if not messages:
                await status_msg.edit_text("No messages found in this channel.")
                return
            
            for idx, msg in enumerate(messages, 1):
                if idx % 5 == 0 or idx == len(messages):
                    await status_msg.edit_text(f"Fetching {idx}/{len(messages)}...")
                
                # Pass entity.id and msg.id to safe_send
                await safe_send(query.message.chat_id, context.bot, msg, from_chat_id=entity.id, message_id=msg.id)
            
            await status_msg.edit_text(f"✅ Batch complete! Fetched {len(messages)} messages.")
        except Exception as e:
            await handle_telethon_error(query, e)

# --- MAIN EXECUTION (ASYNC) ---
async def main():
    try:
        print("Attempting to connect Telethon...")
        await telethon_client.start()
        print("Telethon connected successfully!")
    except Exception as e:
        print(f"❌ Telethon connection failed (Check your API_ID and API_HASH). Error: {e}")
        return

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))
    app.add_handler(CallbackQueryHandler(handle_callback))
    print("Bot is running...")
    
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    
    # Keep the script running forever
    await asyncio.Event().wait()

if __name__ == '__main__':
    asyncio.run(main())