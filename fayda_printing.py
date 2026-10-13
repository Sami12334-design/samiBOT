"""
SamiBOT — admin-only Fayda print-preparation utility.

Builds a clean A4 sheet from original front/back image uploads. It only scales
and positions the supplied images proportionally; it does not reconstruct,
edit, or fabricate identity details, portraits, QR codes, or barcodes.
"""
from __future__ import annotations

import io
from typing import Iterable

from PIL import Image, ImageOps, UnidentifiedImageError
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

MAX_UPLOAD_BYTES = 15 * 1024 * 1024
MAX_IDS_PER_PAGE = 3
MAX_IMAGES = MAX_IDS_PER_PAGE * 2
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
    """Fit the complete source image proportionally inside a box."""
    iw, ih = image.size
    scale = min(width / iw, height / ih)
    dw, dh = iw * scale, ih * scale
    c.drawImage(ImageReader(image), x + (width - dw) / 2, y + (height - dh) / 2,
                width=dw, height=dh, preserveAspectRatio=True, mask="auto")


def _validate_pairs(image_bytes: Iterable[bytes]) -> list[Image.Image]:
    items = list(image_bytes)
    if not items or len(items) % 2:
        raise ValueError("Each ID needs two images: FRONT first, then BACK.")
    if len(items) > MAX_IMAGES:
        raise ValueError(f"Maximum {MAX_IDS_PER_PAGE} ID pairs per A4 page.")
    return [_open_image(item) for item in items]


def images_to_a4_pdf(image_bytes: Iterable[bytes], *, add_copy_notice: bool = True) -> bytes:
    """Create one portrait A4 sheet for up to three front/back pairs.

    Upload order must be FRONT, BACK, FRONT, BACK, etc. Each pair is printed
    on one row: BACK on the left and FRONT on the right.
    """
    images = _validate_pairs(image_bytes)
    out = io.BytesIO()
    c = canvas.Canvas(out, pagesize=A4, pageCompression=1)
    margin = 22
    header_h = 24
    footer_h = 22 if add_copy_notice else 8
    gap_x = 10
    gap_y = 12
    col_w = (PAGE_W - 2 * margin - gap_x) / 2
    row_h = (PAGE_H - 2 * margin - header_h - footer_h - (MAX_IDS_PER_PAGE - 1) * gap_y) / MAX_IDS_PER_PAGE

    c.setFont("Helvetica-Bold", 12)
    c.drawString(margin, PAGE_H - margin - 13, "FAYDA IMAGE PRINT SHEET")
    c.setFont("Helvetica", 7)
    c.drawRightString(PAGE_W - margin, PAGE_H - margin - 12,
                      f"{len(images) // 2} ID pair(s) • A4")
    top = PAGE_H - margin - header_h

    for idx in range(0, len(images), 2):
        pair_index = idx // 2
        row_top = top - pair_index * (row_h + gap_y)
        row_bottom = row_top - row_h
        # Images arrive FRONT then BACK. Print BACK left, FRONT right.
        for col, image_index, label in ((0, idx + 1, "BACK"),
                                        (1, idx, "FRONT")):
            x = margin + col * (col_w + gap_x)
            label_h = 11
            box_y = row_bottom
            box_h = row_h - label_h
            c.setStrokeColorRGB(0.78, 0.78, 0.78)
            c.setLineWidth(0.6)
            c.rect(x, box_y, col_w, box_h, stroke=1, fill=0)
            c.setFillColorRGB(0.2, 0.2, 0.2)
            c.setFont("Helvetica-Bold", 7)
            c.drawString(x + 2, row_top - 8, f"ID {pair_index + 1} — {label}")
            _draw_fit(c, images[image_index], x + 3, box_y + 3,
                      col_w - 6, box_h - 6)

    if add_copy_notice:
        c.setFillColorRGB(0.35, 0.35, 0.35)
        c.setFont("Helvetica", 6)
        c.drawCentredString(PAGE_W / 2, 9,
            "PRINT COPY OF USER-SUPPLIED IMAGES — NOT ISSUED OR VERIFIED BY THE NATIONAL ID PROGRAM")
    c.save()
    return out.getvalue()


def images_to_a4_preview_png(image_bytes: Iterable[bytes]) -> bytes:
    """Create a 300-DPI portrait A4 preview using the same back-left/front-right grid."""
    images = _validate_pairs(image_bytes)
    out_w, out_h = 2480, 3508
    scale = out_w / PAGE_W
    margin = 92
    header_h = 100
    footer_h = 78
    gap_x = 38
    gap_y = 48
    col_w = (out_w - 2 * margin - gap_x) // 2
    row_h = (out_h - 2 * margin - header_h - footer_h - 2 * gap_y) // MAX_IDS_PER_PAGE
    sheet = Image.new("RGB", (out_w, out_h), "white")

    from PIL import ImageDraw, ImageFont
    draw = ImageDraw.Draw(sheet)
    try:
        title_font = ImageFont.truetype("DejaVuSans-Bold.ttf", 38)
        label_font = ImageFont.truetype("DejaVuSans-Bold.ttf", 22)
        footer_font = ImageFont.truetype("DejaVuSans.ttf", 16)
    except OSError:
        title_font = label_font = footer_font = ImageFont.load_default()
    draw.text((margin, margin - 12), "FAYDA IMAGE PRINT SHEET", fill=(35, 35, 35), font=title_font)
    draw.text((out_w - margin, margin - 4), f"{len(images) // 2} ID pair(s) • A4",
              fill=(70, 70, 70), font=footer_font, anchor="ra")
    top = margin + header_h

    for idx in range(0, len(images), 2):
        pair_index = idx // 2
        row_top = top + pair_index * (row_h + gap_y)
        row_bottom = row_top + row_h
        for col, image_index, label in ((0, idx + 1, "BACK"), (1, idx, "FRONT")):
            x = margin + col * (col_w + gap_x)
            label_h = 32
            left, upper = x, row_top + label_h
            right, lower = x + col_w, row_bottom
            draw.text((x + 2, row_top), f"ID {pair_index + 1} — {label}",
                      fill=(45, 45, 45), font=label_font)
            draw.rectangle((left, upper, right, lower), outline=(195, 195, 195), width=2)
            image = images[image_index]
            fit_w, fit_h = col_w - 12, lower - upper - 12
            factor = min(fit_w / image.width, fit_h / image.height)
            resized = image.resize((max(1, int(image.width * factor)),
                                    max(1, int(image.height * factor))), Image.Resampling.LANCZOS)
            px = left + (col_w - resized.width) // 2
            py = upper + (lower - upper - resized.height) // 2
            sheet.paste(resized, (px, py))

    notice = "PRINT COPY OF USER-SUPPLIED IMAGES — NOT OFFICIAL ISSUANCE"
    draw.text((out_w // 2, out_h - margin), notice, fill=(90, 90, 90),
              font=footer_font, anchor="mm")
    output = io.BytesIO()
    sheet.save(output, format="PNG", dpi=(300, 300), optimize=True)
    return output.getvalue()


def register_fayda_printing(application, admin_ids: set[int] | list[int]) -> None:
    """Register the admin-only front/back image-to-A4 conversation."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
    from telegram.ext import (CallbackQueryHandler, CommandHandler, ContextTypes,
                              MessageHandler, ConversationHandler, filters)

    admins = {int(x) for x in admin_ids}
    WAITING_MODE, WAITING_FILES = range(2)

    def controls(include_add: bool = True):
        rows = []
        if include_add:
            rows.append([InlineKeyboardButton("➕ Add another ID", callback_data="fayda_more")])
        rows.append([InlineKeyboardButton("✅ Finish A4 sheet", callback_data="fayda_done"),
                     InlineKeyboardButton("Cancel", callback_data="fayda_cancel")])
        return InlineKeyboardMarkup(rows)

    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.callback_query:
            await update.callback_query.answer()
        user = update.effective_user
        if not user or user.id not in admins:
            if update.effective_message:
                await update.effective_message.reply_text("This feature is available to admins only.")
            return ConversationHandler.END
        context.user_data.pop("fayda_images", None)
        context.user_data["fayda_next_side"] = "FRONT"
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("Start image layout", callback_data="fayda_image")],
                                         [InlineKeyboardButton("Cancel", callback_data="fayda_cancel")]])
        await update.effective_message.reply_text(
            "🪪 Fayda Print Preparation\n\n"
            "Create a clean portrait A4 sheet with up to 3 ID pairs per page. "
            "For each person, send the FRONT image first, then the BACK image. "
            "The template places BACK on the left and FRONT on the right.\n\n"
            "Only the original uploaded images are used: identity text, portraits, QR codes, "
            "and barcodes are not recreated or edited. This is a print layout, not official issuance.",
            reply_markup=keyboard)
        return WAITING_MODE

    async def choose(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        if not query.from_user or query.from_user.id not in admins:
            await query.edit_message_text("This feature is available to admins only.")
            return ConversationHandler.END
        if query.data == "fayda_cancel":
            context.user_data.pop("fayda_images", None)
            await query.edit_message_text("Cancelled.")
            return ConversationHandler.END
        context.user_data["fayda_images"] = []
        context.user_data["fayda_next_side"] = "FRONT"
        await query.edit_message_text(
            "Upload ID 1 FRONT as a photo or image file. Please send the front and back as separate images.")
        return WAITING_FILES

    async def receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        msg = update.effective_message
        if not user or user.id not in admins:
            if msg:
                await msg.reply_text("This feature is available to admins only.")
            return ConversationHandler.END
        if not msg:
            return WAITING_FILES
        photo = msg.photo[-1] if msg.photo else None
        doc = msg.document
        if not photo and not (doc and (doc.mime_type or "").startswith("image/")):
            await msg.reply_text("Please send the requested FRONT/BACK image (JPG, PNG, or WEBP).")
            return WAITING_FILES
        size = photo.file_size if photo else doc.file_size
        if size and size > MAX_UPLOAD_BYTES:
            await msg.reply_text("Maximum image size is 15 MB.")
            return WAITING_FILES
        images = context.user_data.setdefault("fayda_images", [])
        side = context.user_data.get("fayda_next_side", "FRONT")
        if len(images) >= MAX_IMAGES:
            await msg.reply_text("This A4 sheet already has 3 complete ID pairs. Press Finish A4 sheet.")
            return WAITING_FILES
        tgfile = await (photo.get_file() if photo else doc.get_file())
        raw = bytes(await tgfile.download_as_bytearray())
        try:
            _open_image(raw)
        except ValueError as exc:
            await msg.reply_text(str(exc))
            return WAITING_FILES
        images.append(raw)
        if side == "FRONT":
            context.user_data["fayda_next_side"] = "BACK"
            await msg.reply_text(
                f"✅ ID {len(images) // 2 + 1} FRONT received. Now upload the BACK image for this same ID.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data="fayda_cancel")]]))
            return WAITING_FILES

        context.user_data["fayda_next_side"] = "FRONT"
        count = len(images) // 2
        if count >= MAX_IDS_PER_PAGE:
            await msg.reply_text("✅ Three complete ID pairs received. Press Finish to create the A4 sheet.",
                                 reply_markup=controls(include_add=False))
        else:
            await msg.reply_text(
                f"✅ ID {count} FRONT and BACK received. Add another person's FRONT image, or finish the sheet.",
                reply_markup=controls())
        return WAITING_FILES

    async def more(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        if not query.from_user or query.from_user.id not in admins:
            await query.edit_message_text("This feature is available to admins only.")
            return ConversationHandler.END
        images = context.user_data.get("fayda_images", [])
        if len(images) % 2:
            await query.answer("Please upload the BACK image for the current ID first.", show_alert=True)
            return WAITING_FILES
        if len(images) >= MAX_IMAGES:
            await query.answer("Maximum 3 ID pairs per A4 page.", show_alert=True)
            return WAITING_FILES
        context.user_data["fayda_next_side"] = "FRONT"
        await query.edit_message_text(f"Upload ID {len(images) // 2 + 1} FRONT image.")
        return WAITING_FILES

    async def finish(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        if not query.from_user or query.from_user.id not in admins:
            await query.edit_message_text("This feature is available to admins only.")
            return ConversationHandler.END
        if query.data == "fayda_cancel":
            context.user_data.pop("fayda_images", None)
            context.user_data.pop("fayda_next_side", None)
            await query.edit_message_text("Cancelled.")
            return ConversationHandler.END
        images = context.user_data.get("fayda_images", [])
        if not images:
            await query.edit_message_text("No images received. Start again with /fayda.")
            return ConversationHandler.END
        if len(images) % 2:
            await query.answer("The current ID is missing its BACK image.", show_alert=True)
            return WAITING_FILES
        try:
            pdf_result = images_to_a4_pdf(images)
            png_result = images_to_a4_preview_png(images)
        except ValueError as exc:
            await query.edit_message_text(str(exc))
            return ConversationHandler.END
        await query.message.reply_document(
            document=io.BytesIO(png_result), filename="fayda_a4_preview.png",
            caption="A4 preview using only your original front/back images. Check alignment before printing.")
        await query.message.reply_document(
            document=io.BytesIO(pdf_result), filename="fayda_a4_print_copy.pdf",
            caption="Clean A4 print layout. Original ID images are preserved; this is not an official ID.")
        context.user_data.pop("fayda_images", None)
        context.user_data.pop("fayda_next_side", None)
        await query.edit_message_text("✅ A4 sheet created. Review the preview before printing.")
        return ConversationHandler.END

    async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data.pop("fayda_images", None)
        context.user_data.pop("fayda_next_side", None)
        if update.effective_message:
            await update.effective_message.reply_text("Cancelled.")
        return ConversationHandler.END

    conversation = ConversationHandler(
        entry_points=[CommandHandler("fayda", start),
                      CallbackQueryHandler(start, pattern=r"^fayda_open$")],
        states={
            WAITING_MODE: [CallbackQueryHandler(choose, pattern=r"^fayda_(image|cancel)$")],
            WAITING_FILES: [
                CallbackQueryHandler(more, pattern=r"^fayda_more$"),
                CallbackQueryHandler(finish, pattern=r"^fayda_(done|cancel)$"),
                MessageHandler(filters.Document.ALL | filters.PHOTO, receive),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )
    application.add_handler(conversation)
