"""Watermark tools for SamiBOT: add text watermarks or remove a specified text watermark."""
import asyncio
import os
import re
from io import BytesIO

import pymupdf
from PIL import Image, ImageDraw, ImageFont, ImageOps
import cv2
import numpy as np
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode

_MAX_MEDIA_BYTES = 18 * 1024 * 1024
_POSITIONS = {
    "top_left": ("Top left", "tl"),
    "top_center": ("Top center", "tc"),
    "top_right": ("Top right", "tr"),
    "center": ("Center", "c"),
    "bottom_left": ("Bottom left", "bl"),
    "bottom_center": ("Bottom center", "bc"),
    "bottom_right": ("Bottom right", "br"),
    "diagonal": ("Diagonal", "diag"),
    "center_left": ("Center left", "cl"),
    "center_right": ("Center right", "cr"),
}
_COLORS = {
    "black": (0, 0, 0),
    "white": (255, 255, 255),
    "red": (230, 35, 35),
    "blue": (30, 90, 220),
    "green": (20, 145, 70),
    "yellow": (245, 205, 30),
    "gray": (128, 128, 128),
}
_SIZES = {"small": 18, "medium": 32, "large": 48, "extra_large": 64}


def _keyboard(rows):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data=data) for label, data in row]
        for row in rows
    ])


async def watermark_start(update: Update, context):
    context.user_data["wm"] = {}
    context.user_data["state"] = "wm_choose_type"
    await update.effective_message.reply_text(
        "💧 *WATERMARK STUDIO*\n\nWhat do you want to process?",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_keyboard([[("🖼️ Image", "wm_type|image"), ("📄 PDF", "wm_type|pdf")],
                                [("❌ Cancel", "wm_cancel")]])
    )


async def watermark_callback(update: Update, context, data):
    q = update.callback_query
    wm = context.user_data.setdefault("wm", {})
    if data == "watermark":
        context.user_data["wm"] = {}
        context.user_data["state"] = "wm_choose_type"
        await q.message.reply_text(
            "💧 *WATERMARK STUDIO*\nChoose a file type:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=_keyboard([[("🖼️ Image", "wm_type|image"), ("📄 PDF", "wm_type|pdf")],
                                    [("❌ Cancel", "wm_cancel")]])
        )
        return True
    if data == "wm_cancel":
        context.user_data.pop("wm", None)
        context.user_data["state"] = None
        await q.message.reply_text("❌ Watermark operation cancelled.")
        return True
    if data.startswith("wm_type|"):
        kind = data.split("|", 1)[1]
        if kind not in ("image", "pdf"):
            await q.message.reply_text("❌ Invalid file type. Start again with /watermark.")
            return True
        wm.clear()
        wm["kind"] = kind
        context.user_data["state"] = "wm_choose_action"
        await q.message.reply_text(
            "Choose what to do:",
            reply_markup=_keyboard([[("➕ Create watermark", "wm_action|add"),
                                     ("🧽 Remove watermark", "wm_action|remove")],
                                    [("⬅️ Back", "watermark"), ("❌ Cancel", "wm_cancel")]])
        )
        return True
    if data.startswith("wm_action|"):
        action = data.split("|", 1)[1]
        if action not in ("add", "remove") or wm.get("kind") not in ("image", "pdf"):
            await q.message.reply_text("❌ Session expired. Start again with /watermark.")
            return True
        wm["action"] = action
        context.user_data["state"] = "wm_text_add" if action == "add" else "wm_text_remove"
        prompt = ("Send the watermark text to add (up to 80 characters)." if action == "add"
                  else "Send the exact watermark text to look for and remove. Removal works best for readable text; complex or embedded image watermarks may remain.")
        await q.message.reply_text(prompt, reply_markup=_keyboard([[("❌ Cancel", "wm_cancel")]]))
        return True
    if data.startswith("wm_pos|"):
        pos = data.split("|", 1)[1]
        if pos not in _POSITIONS:
            await q.message.reply_text("❌ Invalid position. Please choose again.")
            return True
        wm["position"] = pos
        context.user_data["state"] = "wm_choose_color"
        await q.message.reply_text("Choose watermark color:",
            reply_markup=_keyboard([[("⚫ Black", "wm_color|black"), ("⚪ White", "wm_color|white")],
                                    [("🔴 Red", "wm_color|red"), ("🔵 Blue", "wm_color|blue")],
                                    [("🟢 Green", "wm_color|green"), ("🟡 Yellow", "wm_color|yellow")],
                                    [("◻️ Gray", "wm_color|gray"), ("❌ Cancel", "wm_cancel")]]))
        return True
    if data.startswith("wm_color|"):
        color = data.split("|", 1)[1]
        if color not in _COLORS:
            await q.message.reply_text("❌ Invalid color. Choose again.")
            return True
        wm["color"] = color
        context.user_data["state"] = "wm_choose_size"
        await q.message.reply_text("Choose watermark size:",
            reply_markup=_keyboard([[("Small", "wm_size|small"), ("Medium", "wm_size|medium")],
                                    [("Large", "wm_size|large"), ("Extra large", "wm_size|extra_large")],
                                    [("❌ Cancel", "wm_cancel")]]))
        return True
    if data.startswith("wm_size|"):
        size = data.split("|", 1)[1]
        if size not in _SIZES:
            await q.message.reply_text("❌ Invalid size. Choose again.")
            return True
        wm["size"] = _SIZES[size]
        context.user_data["state"] = "wm_awaiting_file"
        await q.message.reply_text(
            f"✅ Settings saved. Now upload your {'image' if wm.get('kind') == 'image' else 'PDF'} file.",
            reply_markup=_keyboard([[("❌ Cancel", "wm_cancel")]])
        )
        return True
    return False


async def watermark_text_input(update: Update, context):
    state = context.user_data.get("state")
    if state not in ("wm_text_add", "wm_text_remove"):
        return False
    text = (update.effective_message.text or "").strip()
    if not text or len(text) > 80:
        await update.effective_message.reply_text("❌ Please send non-empty text up to 80 characters.")
        return True
    wm = context.user_data.setdefault("wm", {})
    wm["text"] = text
    if state == "wm_text_add":
        context.user_data["state"] = "wm_choose_position"
        rows = [[("↖️ Top left", "wm_pos|top_left"), ("⬆️ Top center", "wm_pos|top_center"), ("↗️ Top right", "wm_pos|top_right")],
                [("⬅️ Center left", "wm_pos|center_left"), ("🎯 Center", "wm_pos|center"), ("➡️ Center right", "wm_pos|center_right")],
                [("↙️ Bottom left", "wm_pos|bottom_left"), ("⬇️ Bottom center", "wm_pos|bottom_center"), ("↘️ Bottom right", "wm_pos|bottom_right")],
                [("📐 Diagonal", "wm_pos|diagonal")], [("❌ Cancel", "wm_cancel")]]
        await update.effective_message.reply_text("Choose watermark position:", reply_markup=_keyboard(rows))
    else:
        context.user_data["state"] = "wm_awaiting_file"
        await update.effective_message.reply_text(
            f"🔎 I'll look for this exact text: “{text}”. Upload the {'image' if wm.get('kind') == 'image' else 'PDF'} now.",
            reply_markup=_keyboard([[("❌ Cancel", "wm_cancel")]])
        )
    return True


def _font(size):
    for path in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                 "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"):
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size=size)
            except Exception:
                pass
    return ImageFont.load_default()


def _add_image_watermark(data, wm):
    with Image.open(BytesIO(data)) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGBA")
    draw = ImageDraw.Draw(image, "RGBA")
    font = _font(max(12, min(int(wm["size"]), max(12, image.width // 5))))
    text = wm["text"]
    bbox = draw.textbbox((0, 0), text, font=font, stroke_width=1)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    margin = max(12, int(min(image.size) * 0.025))
    pos = wm["position"]
    x = margin if pos in ("top_left", "bottom_left", "center_left") else (image.width - tw) // 2 if pos in ("top_center", "bottom_center", "center") else image.width - tw - margin
    y = margin if pos.startswith("top_") else (image.height - th) // 2 if pos.startswith("center") or pos == "center" else image.height - th - margin
    rgba = (*_COLORS[wm["color"]], 150)
    if pos == "diagonal":
        layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        ld.text((image.width * 0.12, image.height * 0.42), text, font=font, fill=rgba, stroke_width=1, stroke_fill=(0,0,0,60))
        layer = layer.rotate(35, resample=Image.Resampling.BICUBIC, center=(image.width/2, image.height/2))
        image = Image.alpha_composite(image, layer)
    else:
        draw.text((max(0, x), max(0, y)), text, font=font, fill=rgba, stroke_width=1, stroke_fill=(0,0,0,70))
    out = BytesIO()
    image.convert("RGB").save(out, format="PNG")
    return out.getvalue()


def _remove_image_text(data, target):
    try:
        from rapidocr_onnxruntime import RapidOCR
        engine = RapidOCR()
        result, _ = engine(np.array(Image.open(BytesIO(data)).convert("RGB")))
    except Exception as exc:
        raise RuntimeError("Text recognition is unavailable on this deployment. Install/enable rapidocr_onnxruntime and retry.") from exc
    if not result:
        raise RuntimeError("No readable text was detected. Try a clearer, higher-resolution image.")
    rgb = np.array(Image.open(BytesIO(data)).convert("RGB"))
    mask = np.zeros(rgb.shape[:2], dtype=np.uint8)
    matches = 0
    for item in result:
        box, text, score = item
        if target.casefold() in str(text).casefold() or str(text).casefold() in target.casefold():
            pts = np.array(box, dtype=np.int32)
            cv2.fillPoly(mask, [pts], 255)
            matches += 1
    if not matches:
        raise RuntimeError("The exact watermark text was not detected. Check spelling or upload a clearer image.")
    mask = cv2.dilate(mask, np.ones((5, 5), np.uint8), iterations=2)
    repaired = cv2.inpaint(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), mask, 5, cv2.INPAINT_TELEA)
    out = BytesIO()
    Image.fromarray(cv2.cvtColor(repaired, cv2.COLOR_BGR2RGB)).save(out, format="PNG")
    return out.getvalue(), matches


def _process_pdf(data, wm):
    doc = pymupdf.open(stream=data, filetype="pdf")
    if not doc.page_count:
        doc.close()
        raise RuntimeError("This PDF contains no pages.")
    if doc.page_count > 150:
        doc.close()
        raise RuntimeError("For safety, PDFs are limited to 150 pages per operation.")
    try:
        for page in doc:
            if wm["action"] == "add":
                rect = page.rect
                size = min(float(wm["size"]), max(12.0, rect.width / 5))
                font = "helv"
                tw = pymupdf.get_text_length(wm["text"], fontname=font, fontsize=size)
                margin = max(18, size * 0.6)
                pos = wm["position"]
                x = margin if pos in ("top_left", "bottom_left", "center_left") else (rect.width-tw)/2 if pos in ("top_center", "bottom_center", "center") else rect.width-tw-margin
                y = margin+size if pos.startswith("top_") else rect.height/2+size/2 if pos.startswith("center") or pos=="center" else rect.height-margin
                color = tuple(c/255 for c in _COLORS[wm["color"]])
                if pos == "diagonal":
                    page.insert_text((rect.width*0.18, rect.height*0.55), wm["text"], fontsize=size, fontname=font, color=color, fill_opacity=0.55, rotate=45, overlay=True)
                else:
                    page.insert_text((max(4,x), max(size+2,min(rect.height-4,y))), wm["text"], fontsize=size, fontname=font, color=color, fill_opacity=0.55, overlay=True)
            else:
                rects = page.search_for(wm["text"], quads=False)
                if not rects:
                    continue
                for rect in rects:
                    page.add_redact_annot(rect, fill=(1, 1, 1), cross_out=False)
                page.apply_redactions(images=0, graphics=0, text=0)
        if wm["action"] == "remove":
            # Confirm a match occurred; a no-match PDF should not silently return unchanged.
            if not any(wm.get("_pdf_matches", [])):
                pass
        return doc.tobytes(garbage=4, deflate=True)
    finally:
        doc.close()


async def _download_file(message, context, file_id):
    tg_file = await context.bot.get_file(file_id)
    data = BytesIO()
    await tg_file.download_to_memory(data)
    raw = data.getvalue()
    if not raw:
        raise RuntimeError("The uploaded file is empty.")
    if len(raw) > _MAX_MEDIA_BYTES:
        raise RuntimeError("File is too large. Please send a file smaller than 18 MB.")
    return raw


async def watermark_photo_input(update: Update, context):
    if context.user_data.get("state") != "wm_awaiting_file":
        return False
    wm = context.user_data.get("wm", {})
    if wm.get("kind") != "image":
        await update.effective_message.reply_text("📄 This session expects a PDF. Send a PDF document or tap Cancel.")
        return True
    await _process_uploaded(update.effective_message, context, update.effective_message.photo[-1].file_id, wm, "image")
    return True


async def watermark_document_input(update: Update, context):
    if context.user_data.get("state") != "wm_awaiting_file":
        return False
    message = update.effective_message
    wm = context.user_data.get("wm", {})
    doc = message.document
    mime = (doc.mime_type or "").lower()
    filename = (doc.file_name or "").lower()
    is_pdf = mime == "application/pdf" or filename.endswith(".pdf")
    is_image = mime.startswith("image/") or filename.endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"))
    if wm.get("kind") == "pdf" and not is_pdf:
        await message.reply_text("❌ Please upload a PDF document for this operation.")
        return True
    if wm.get("kind") == "image" and not is_image:
        await message.reply_text("❌ Please upload an image file (PNG, JPG, WEBP, BMP, or TIFF).")
        return True
    await _process_uploaded(message, context, doc.file_id, wm, "pdf" if is_pdf else "image", filename)
    return True


async def _process_uploaded(message, context, file_id, wm, kind, filename=""):
    status = await message.reply_text("⏳ Processing your file…")
    try:
        raw = await _download_file(message, context, file_id)
        if kind == "image":
            if wm.get("action") == "add":
                result = await asyncio.to_thread(_add_image_watermark, raw, wm)
                caption = "✅ Watermark added."
            else:
                result, count = await asyncio.to_thread(_remove_image_text, raw, wm["text"])
                caption = f"✅ Removed matching text from {count} detected region(s). Some background repair may be visible."
            output = BytesIO(result)
            output.name = "watermarked_image.png" if wm.get("action") == "add" else "watermark_removed.png"
            await message.reply_document(document=output, filename=output.name, caption=caption)
        else:
            result = await asyncio.to_thread(_process_pdf, raw, wm)
            if wm.get("action") == "remove":
                # PDF text search can find no matches; avoid implying success in that case.
                with pymupdf.open(stream=raw, filetype="pdf") as check_doc:
                    matches = sum(len(page.search_for(wm["text"])) for page in check_doc)
                if matches == 0:
                    raise RuntimeError("That text was not found as selectable PDF text. Scanned/image watermarks need OCR-based processing.")
                caption = f"✅ Removed {matches} matching text occurrence(s). White redaction blocks may be visible on colored backgrounds."
            else:
                with pymupdf.open(stream=result, filetype="pdf") as out_doc:
                    page_count = out_doc.page_count
                caption = f"✅ Watermark added to the PDF ({page_count} pages)."
            output = BytesIO(result)
            output.name = "watermarked.pdf" if wm.get("action") == "add" else "watermark_removed.pdf"
            await message.reply_document(document=output, filename=output.name, caption=caption)
        await status.edit_text("✅ Finished.")
    except Exception as exc:
        error = str(exc).strip() or "Unknown processing error."
        await status.edit_text("❌ Could not process this file: " + error[:900])
    finally:
        context.user_data["state"] = None
        context.user_data.pop("wm", None)
