"""
SamiBOT — admin-only Fayda print-preparation utility.

This tool prepares a print-ready A4 sheet from the user's ORIGINAL uploaded
image(s), without rebuilding or altering identity-card security features.
It cannot make a printout an official ID; physical cards must be ordered via
the National ID Program's authorized providers.

Dependencies: python-telegram-bot >= 20, Pillow, reportlab, pypdf
"""
from __future__ import annotations

import io
import os
import tempfile
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageOps, UnidentifiedImageError
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

MAX_UPLOAD_BYTES = 15 * 1024 * 1024
MAX_IMAGES = 2
PAGE_W, PAGE_H = A4


def _open_image(data: bytes) -> Image.Image:
    if not data or len(data) > MAX_UPLOAD_BYTES:
        raise ValueError("File is empty or larger than 15 MB.")
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
        return ImageOps.exif_transpose(image).convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValueError("Unsupported or damaged image. Send JPG, PNG, or WEBP.") from exc


def _draw_fit(c: canvas.Canvas, image: Image.Image, x: float, y: float,
              width: float, height: float) -> None:
    """Draw an image proportionally, centered in a box; source pixels stay intact."""
    iw, ih = image.size
    scale = min(width / iw, height / ih)
    dw, dh = iw * scale, ih * scale
    c.drawImage(ImageReader(image), x + (width - dw) / 2, y + (height - dh) / 2,
                width=dw, height=dh, preserveAspectRatio=True, mask="auto")


def images_to_a4_pdf(image_bytes: Iterable[bytes], *, add_copy_notice: bool = True) -> bytes:
    """Place front (first upload) on the right and back (second upload) on the left
    on one landscape A4 page, using only the supplied source images.
    """
    items = list(image_bytes)
    if not 1 <= len(items) <= MAX_IMAGES:
        raise ValueError("Send one or two images (front first, then back).")
    images = [_open_image(b) for b in items]

    # Landscape A4 matches the requested side-by-side print layout.
    page_w, page_h = PAGE_H, PAGE_W
    out = io.BytesIO()
    c = canvas.Canvas(out, pagesize=(page_w, page_h), pageCompression=1)
    margin = 22
    gap = 12
    notice_h = 18 if add_copy_notice else 0
    usable_h = page_h - 2 * margin - notice_h
    box_w = (page_w - 2 * margin - gap) / 2

    # Upload order is front first, back second; print back left and front right.
    ordered = list(reversed(images)) if len(images) == 2 else images
    for i, image in enumerate(ordered):
        x = margin + i * (box_w + gap)
        y = margin + notice_h
        _draw_fit(c, image, x, y, box_w, usable_h)
        c.setStrokeColorRGB(0.72, 0.72, 0.72)
        c.rect(x, y, box_w, usable_h, stroke=1, fill=0)

    if add_copy_notice:
        c.setFont("Helvetica-Bold", 7)
        c.setFillColorRGB(0.35, 0.35, 0.35)
        c.drawCentredString(
            page_w / 2, 7,
            "PRINT COPY OF SOURCE IMAGE — NOT ISSUED OR VERIFIED BY THE NATIONAL ID PROGRAM"
        )
    c.save()
    return out.getvalue()

def pdf_to_a4_pdf(source: bytes) -> bytes:
    """Scale original PDF pages onto A4 without changing their content."""
    if not source or len(source) > MAX_UPLOAD_BYTES:
        raise ValueError("PDF is empty or larger than 15 MB.")
    try:
        from pypdf import PdfReader, PdfWriter, Transformation
    except ImportError as exc:
        raise RuntimeError("Install pypdf to enable PDF conversion.") from exc
    try:
        reader = PdfReader(io.BytesIO(source), strict=True)
        if reader.is_encrypted:
            raise ValueError("Password-protected PDFs are not supported.")
        if not reader.pages or len(reader.pages) > 40:
            raise ValueError("PDF must contain between 1 and 40 pages.")
        writer = PdfWriter()
        target_w, target_h = A4
        for page in reader.pages:
            page_w = float(page.mediabox.width)
            page_h = float(page.mediabox.height)
            if page_w <= 0 or page_h <= 0:
                raise ValueError("PDF contains an invalid page size.")
            scale = min((target_w - 36) / page_w, (target_h - 36) / page_h)
            dx = (target_w - page_w * scale) / 2
            dy = (target_h - page_h * scale) / 2
            page.add_transformation(Transformation().scale(scale).translate(dx, dy))
            page.mediabox.lower_left = (0, 0)
            page.mediabox.upper_right = (target_w, target_h)
            page.cropbox.lower_left = (0, 0)
            page.cropbox.upper_right = (target_w, target_h)
            writer.add_page(page)
        dest = io.BytesIO()
        writer.write(dest)
        return dest.getvalue()
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("Could not read this PDF. Please send a valid, unlocked PDF.") from exc


def register_fayda_printing(application, admin_ids: set[int] | list[int]) -> None:
    """Register PTB handlers; call from bot startup after building Application."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
    from telegram.ext import (CallbackQueryHandler, CommandHandler, ContextTypes,
                              MessageHandler, ConversationHandler, filters)

    admins = {int(x) for x in admin_ids}
    WAITING_MODE, WAITING_FILES = range(2)

    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.callback_query:
            await update.callback_query.answer()
        user = update.effective_user
        if not user or user.id not in admins:
            if update.effective_message:
                await update.effective_message.reply_text("This feature is available to admins only.")
            return ConversationHandler.END
        keyboard = [[InlineKeyboardButton("PDF to A4", callback_data="fayda_pdf")],
                    [InlineKeyboardButton("Screenshot to A4", callback_data="fayda_image")],
                    [InlineKeyboardButton("Cancel", callback_data="fayda_cancel")]]
        await update.effective_message.reply_text(
            "🪪 Fayda Print Preparation\n\n"
            "Choose a source format. This tool preserves the supplied document/image; "
            "it does not recreate or certify an official ID. For a physical official card, "
            "use an authorized Fayda printing provider.",
            reply_markup=InlineKeyboardMarkup(keyboard))
        return WAITING_MODE

    async def choose(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        if not query.from_user or query.from_user.id not in admins:
            await query.edit_message_text("This feature is available to admins only.")
            return ConversationHandler.END
        if query.data == "fayda_cancel":
            await query.edit_message_text("Cancelled.")
            return ConversationHandler.END
        context.user_data["fayda_mode"] = "pdf" if query.data == "fayda_pdf" else "image"
        context.user_data["fayda_files"] = []
        hint = ("Send one PDF (up to 15 MB, maximum 40 pages)." if query.data == "fayda_pdf"
                else "Send 1 or 2 screenshots as photos or image files (front/back). "
                     "When both are sent, press Done.")
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("Done", callback_data="fayda_done"),
                                          InlineKeyboardButton("Cancel", callback_data="fayda_cancel")]])
        await query.edit_message_text(hint, reply_markup=keyboard)
        return WAITING_FILES

    async def receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not user or user.id not in admins:
            await update.effective_message.reply_text("This feature is available to admins only.")
            return ConversationHandler.END
        mode = context.user_data.get("fayda_mode")
        msg = update.effective_message
        if not msg:
            return WAITING_FILES
        if mode == "pdf":
            doc = msg.document
            if not doc or (doc.mime_type != "application/pdf" and not (doc.file_name or "").lower().endswith(".pdf")):
                await msg.reply_text("Please send a PDF document.")
                return WAITING_FILES
            if doc.file_size and doc.file_size > MAX_UPLOAD_BYTES:
                await msg.reply_text("Maximum file size is 15 MB.")
                return WAITING_FILES
            tgfile = await doc.get_file()
            raw = bytes(await tgfile.download_as_bytearray())
            try:
                result = pdf_to_a4_pdf(raw)
            except (ValueError, RuntimeError) as exc:
                await msg.reply_text(str(exc))
                return WAITING_FILES
            await msg.reply_document(document=io.BytesIO(result), filename="fayda_a4_print_copy.pdf",
                                     caption="A4 print copy. Source PDF content was preserved; this is not official issuance.")
            return ConversationHandler.END
        photo = msg.photo[-1] if msg.photo else None
        doc = msg.document
        if not photo and not (doc and (doc.mime_type or "").startswith("image/")):
            await msg.reply_text("Send a screenshot/photo or image file.")
            return WAITING_FILES
        size = photo.file_size if photo else doc.file_size
        if size and size > MAX_UPLOAD_BYTES:
            await msg.reply_text("Maximum image size is 15 MB.")
            return WAITING_FILES
        if len(context.user_data["fayda_files"]) >= MAX_IMAGES:
            await msg.reply_text("Two images are already selected. Press Done to create the A4 sheet.")
            return WAITING_FILES
        tgfile = await (photo.get_file() if photo else doc.get_file())
        raw = bytes(await tgfile.download_as_bytearray())
        try:
            _open_image(raw)
        except ValueError as exc:
            await msg.reply_text(str(exc))
            return WAITING_FILES
        context.user_data["fayda_files"].append(raw)
        count = len(context.user_data["fayda_files"])
        await msg.reply_text(f"Image {count}/2 received. Send the other side or press Done.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Done", callback_data="fayda_done"),
                                                InlineKeyboardButton("Cancel", callback_data="fayda_cancel")]]))
        return WAITING_FILES

    async def finish(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        if not query.from_user or query.from_user.id not in admins:
            await query.edit_message_text("This feature is available to admins only.")
            return ConversationHandler.END
        if query.data == "fayda_cancel":
            context.user_data.pop("fayda_files", None)
            await query.edit_message_text("Cancelled.")
            return ConversationHandler.END
        files = context.user_data.get("fayda_files", [])
        if not files:
            await query.edit_message_text("No images received. Start again with /fayda.")
            return ConversationHandler.END
        try:
            result = images_to_a4_pdf(files)
        except ValueError as exc:
            await query.edit_message_text(str(exc))
            return ConversationHandler.END
        await query.message.reply_document(document=io.BytesIO(result), filename="fayda_a4_print_copy.pdf",
            caption="A4 print sheet made from the original images. It is not an official ID.")
        context.user_data.pop("fayda_files", None)
        context.user_data.pop("fayda_mode", None)
        return ConversationHandler.END

    async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data.pop("fayda_files", None)
        context.user_data.pop("fayda_mode", None)
        if update.effective_message:
            await update.effective_message.reply_text("Cancelled.")
        return ConversationHandler.END

    conversation = ConversationHandler(
        entry_points=[CommandHandler("fayda", start),
                      CallbackQueryHandler(start, pattern=r"^fayda_open$")],
        states={
            WAITING_MODE: [CallbackQueryHandler(choose, pattern=r"^fayda_(pdf|image|cancel)$")],
            WAITING_FILES: [
                CallbackQueryHandler(finish, pattern=r"^fayda_(done|cancel)$"),
                MessageHandler(filters.Document.ALL | filters.PHOTO, receive),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )
    application.add_handler(conversation)
