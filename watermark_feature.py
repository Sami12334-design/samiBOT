"""Watermark tools for SamiBOT: add text watermarks or remove existing watermarks.

Add functionality is unchanged. Remove functionality delegates to the
classical-CV core in ``watermark_removal.py`` which supports auto (HSV color
detection), region (user-supplied rectangle or preset), color (target color
+ tolerance), and text (PDF selectable text) modes.
"""
import asyncio
import os
from io import BytesIO

import pymupdf
from PIL import Image, ImageDraw, ImageFont, ImageOps
import cv2
import numpy as np
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from watermark_removal import (
    remove_watermark as _core_remove_watermark,
    remove_pdf_watermark as _core_remove_pdf_watermark,
    RemovalResult,
    WatermarkRemovalError,
)

_MAX_MEDIA_BYTES = 18 * 1024 * 1024
_REMOVE_TIMEOUT_IMAGE = 30
_REMOVE_TIMEOUT_PDF = 60
_MAX_ATTEMPTS = 3  # failed processing attempts allowed before the session is cleared
_AUTO_SENTINEL = "__AUTO_WATERMARK__"
# Presets understood by watermark_removal._region_detect (hyphenated names).
_REGION_PRESETS = {
    "top-left": "↖️ Top left",
    "top-right": "↗️ Top right",
    "bottom-left": "↙️ Bottom left",
    "bottom-right": "↘️ Bottom right",
}

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


class WatermarkError(Exception):
    """User-facing watermark processing error with a safe message."""


def _keyboard(rows):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data=data) for label, data in row]
        for row in rows
    ])


def _remove_mode_keyboard():
    return _keyboard([
        [("🤖 Automatic (detect colored banners)", "wm_remove_mode|auto")],
        [("📍 Region (pick a position)", "wm_remove_mode|region")],
        [("🎨 Color (pick a color)", "wm_remove_mode|color")],
        [("📝 Specific text (PDF selectable text)", "wm_remove_mode|specific")],
        [("❌ Cancel", "wm_cancel")],
    ])


def _retry_keyboard(wm):
    first = [("🔁 Retry", "wm_retry")]
    if wm.get("action") == "remove":
        first.append(("🔀 Change method", "wm_change_method"))
    return _keyboard([first, [("🔄 Start over", "watermark"), ("❌ Cancel", "wm_cancel")]])


async def watermark_start(update: Update, context):
    context.user_data["wm"] = {}
    context.user_data["state"] = "wm_upload_first"
    await update.effective_message.reply_text(
        "💧 WATERMARK STUDIO\nFirst, upload the image or PDF you want to process.",
        reply_markup=_keyboard([[("❌ Cancel", "wm_cancel")]])
    )


async def watermark_callback(update: Update, context, data):
    q = update.callback_query
    wm = context.user_data.setdefault("wm", {})
    if data == "watermark":
        context.user_data["wm"] = {}
        context.user_data["state"] = "wm_upload_first"
        await q.message.reply_text(
            "💧 WATERMARK STUDIO\nFirst, upload the image or PDF you want to process.",
            reply_markup=_keyboard([[("❌ Cancel", "wm_cancel")]])
        )
        return True
    if data == "wm_cancel":
        context.user_data.pop("wm", None)
        context.user_data["state"] = None
        await q.message.reply_text("❌ Watermark operation cancelled.")
        return True
    if data.startswith("wm_remove_mode|"):
        mode = data.split("|", 1)[1]
        if mode not in ("auto", "region", "color", "specific"):
            await q.message.reply_text("❌ Invalid option. Please choose again.", reply_markup=_remove_mode_keyboard())
            return True
        if not wm.get("file_id"):
            await q.message.reply_text("❌ Session expired. Start again with /watermark.")
            return True
        if mode == "specific" and wm.get("kind") == "image":
            await q.message.reply_text(
                "ℹ️ Specific text removal only works on PDFs with selectable text. "
                "For images, choose Automatic, Region, or Color.",
                reply_markup=_remove_mode_keyboard())
            return True
        if mode in ("region", "color") and wm.get("kind") == "pdf":
            await q.message.reply_text(
                "ℹ️ Region and color removal work on images only. "
                "For a PDF, use Specific text (selectable PDF text).",
                reply_markup=_remove_mode_keyboard())
            return True
        wm["action"] = "remove"
        wm["remove_mode"] = mode
        if mode == "region":
            context.user_data["state"] = "wm_choose_region"
            rows = [[(_REGION_PRESETS["top-left"], "wm_region|top-left"), (_REGION_PRESETS["top-right"], "wm_region|top-right")],
                    [(_REGION_PRESETS["bottom-left"], "wm_region|bottom-left"), (_REGION_PRESETS["bottom-right"], "wm_region|bottom-right")],
                    [("❌ Cancel", "wm_cancel")]]
            await q.message.reply_text("Where is the watermark?", reply_markup=_keyboard(rows))
        elif mode == "color":
            context.user_data["state"] = "wm_choose_remove_color"
            await q.message.reply_text("Choose the watermark color to remove:",
                reply_markup=_keyboard([[("⚫ Black", "wm_rmcolor|black"), ("⚪ White", "wm_rmcolor|white")],
                                        [("🔴 Red", "wm_rmcolor|red"), ("🔵 Blue", "wm_rmcolor|blue")],
                                        [("🟢 Green", "wm_rmcolor|green"), ("🟡 Yellow", "wm_rmcolor|yellow")],
                                        [("◻️ Gray", "wm_rmcolor|gray"), ("❌ Cancel", "wm_cancel")]]))
        elif mode == "specific":
            context.user_data["state"] = "wm_text_remove"
            await q.message.reply_text("Send the exact watermark text to remove.", reply_markup=_keyboard([[("❌ Cancel", "wm_cancel")]]))
        else:
            wm["text"] = _AUTO_SENTINEL
            context.user_data["state"] = "wm_processing"
            await q.message.reply_text(
                "🤖 Scanning for likely watermark text (for example, diagonal or faint text). "
                "Automatic detection can miss watermarks or select ordinary text, so check the result."
            )
            await _process_uploaded(q.message, context, wm["file_id"], wm, wm["kind"], wm.get("filename", ""))
        return True
    if data.startswith("wm_region|") or data.startswith("wm_rmcolor|"):
        which, value = data.split("|", 1)
        if wm.get("action") != "remove" or wm.get("kind") != "image" or not wm.get("file_id"):
            await q.message.reply_text("❌ Session expired. Start again with /watermark.")
            return True
        if which == "wm_region" and value in _REGION_PRESETS:
            wm["remove_mode"], wm["region_preset"] = "region", value
        elif which == "wm_rmcolor" and value in _COLORS:
            wm["remove_mode"], wm["target_color"] = "color", value
        else:
            await q.message.reply_text("❌ Invalid choice. Please choose again.")
            return True
        context.user_data["state"] = "wm_processing"
        await q.message.reply_text("✅ Settings saved. Removing the watermark…")
        await _process_uploaded(q.message, context, wm["file_id"], wm, wm["kind"], wm.get("filename", ""))
        return True
    if data == "wm_retry":
        if not wm.get("file_id") or wm.get("kind") not in ("image", "pdf") or wm.get("action") not in ("add", "remove"):
            await q.message.reply_text("❌ Session expired. Start again with /watermark.")
            return True
        if context.user_data.get("state") == "wm_processing":
            await q.message.reply_text("⏳ Still processing your file. Please wait.")
            return True
        context.user_data["state"] = "wm_processing"
        await q.message.reply_text("🔁 Retrying with the same file and settings…")
        await _process_uploaded(q.message, context, wm["file_id"], wm, wm["kind"], wm.get("filename", ""))
        return True
    if data == "wm_change_method":
        if wm.get("action") != "remove" or not wm.get("file_id"):
            await q.message.reply_text("❌ Session expired. Start again with /watermark.")
            return True
        context.user_data["state"] = "wm_choose_remove_mode"
        await q.message.reply_text("Choose how to remove the watermark:", reply_markup=_remove_mode_keyboard())
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
        if action == "add":
            context.user_data["state"] = "wm_text_add"
            await q.message.reply_text(
                "Send the new watermark text (up to 80 characters).",
                reply_markup=_keyboard([[("❌ Cancel", "wm_cancel")]])
            )
        else:
            context.user_data["state"] = "wm_choose_remove_mode"
            await q.message.reply_text("Choose how to remove the watermark:", reply_markup=_remove_mode_keyboard())
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
        if wm.get("file_id"):
            context.user_data["state"] = "wm_processing"
            await q.message.reply_text("✅ Settings saved. Applying the watermark to your uploaded file…")
            await _process_uploaded(q.message, context, wm["file_id"], wm, wm["kind"], wm.get("filename", ""))
        else:
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
        if wm.get("file_id"):
            context.user_data["state"] = "wm_processing"
            await update.effective_message.reply_text(f"🔎 Looking for “{text}”…")
            await _process_uploaded(update.effective_message, context, wm["file_id"], wm, wm["kind"], wm.get("filename", ""))
        else:
            context.user_data["state"] = "wm_awaiting_file"
            await update.effective_message.reply_text("Upload the image or PDF now.", reply_markup=_keyboard([[("❌ Cancel", "wm_cancel")]]))
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


# --- REMOVE: delegates to watermark_removal.py core processor ---

def _remove_image_auto(data, config=None):
    """Auto-detect and remove a colored watermark banner (e.g. orange + white text)."""
    result = _core_remove_watermark(data, mode="auto", config=config)
    if not result.success:
        raise WatermarkError(
            "; ".join(result.warnings)
            or "Automatic removal could not find a clear watermark on this image."
        )
    count = len(result.regions)
    return result.image_bytes, count, result.warnings


def _remove_image_region(data, region, config=None):
    """Remove a watermark from a user-selected region or position preset."""
    cfg = dict(config or {})
    cfg["region"] = region
    result = _core_remove_watermark(data, mode="region", config=cfg)
    if not result.success:
        raise WatermarkError(
            "; ".join(result.warnings)
            or "No watermark was found in the selected region."
        )
    return result.image_bytes, len(result.regions), result.warnings


def _remove_image_color(data, target_color, config=None):
    """Remove a watermark matching a user-specified color with tolerance."""
    cfg = dict(config or {})
    cfg["target_color"] = target_color
    result = _core_remove_watermark(data, mode="color", config=cfg)
    if not result.success:
        raise WatermarkError(
            "; ".join(result.warnings)
            or "No watermark matching that color was found."
        )
    return result.image_bytes, len(result.regions), result.warnings


def _remove_image(data, wm):
    """Run the image-removal mode chosen in the menu.

    Returns ``(image_bytes, region_count, warnings)`` like the wrappers above.
    """
    mode = wm.get("remove_mode") or ("auto" if wm.get("text") == _AUTO_SENTINEL else "specific")
    if mode == "auto":
        return _remove_image_auto(data)
    if mode == "region":
        preset = wm.get("region_preset")
        if preset not in _REGION_PRESETS:
            raise WatermarkError("No region was selected. Please choose where the watermark is.")
        return _remove_image_region(data, {"preset": preset})
    if mode == "color":
        name = wm.get("target_color")
        if name not in _COLORS:
            raise WatermarkError("No color was selected. Please choose the watermark color.")
        r, g, b = _COLORS[name]
        return _remove_image_color(data, (b, g, r))  # core expects BGR
    raise WatermarkError(
        "Specific text removal only works on PDFs with selectable text. "
        "For images, use Automatic, Region, or Color."
    )


def _process_pdf(data, wm):
    doc = pymupdf.open(stream=data, filetype="pdf")
    if not doc.page_count:
        doc.close()
        raise WatermarkError("This PDF contains no pages.")
    if doc.page_count > 150:
        doc.close()
        raise WatermarkError("For safety, PDFs are limited to 150 pages per operation.")
    if wm["action"] == "add":
        try:
            for page in doc:
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
            return doc.tobytes(garbage=4, deflate=True)
        finally:
            doc.close()
    # Remove action: delegate to core PDF processor.
    doc.close()
    remove_mode = wm.get("remove_mode", "auto")
    pdf_text = wm.get("text") if remove_mode == "specific" else None
    core_mode = "text" if pdf_text else "auto"
    try:
        result = _core_remove_pdf_watermark(
            data, mode=core_mode, text=pdf_text,
        )
    except WatermarkRemovalError as exc:
        raise WatermarkError(str(exc)) from exc
    if not result["success"]:
        raise WatermarkError("; ".join(result["warnings"]) or "No watermark was detected in the PDF.")
    wm["_pdf_matches"] = result["pages_processed"]
    return result["pdf_bytes"]


async def _download_file(message, context, file_id):
    tg_file = await context.bot.get_file(file_id)
    data = BytesIO()
    await tg_file.download_to_memory(data)
    raw = data.getvalue()
    if not raw:
        raise WatermarkError("The uploaded file is empty.")
    if len(raw) > _MAX_MEDIA_BYTES:
        raise WatermarkError("File is too large. Please send a file smaller than 18 MB.")
    return raw


async def watermark_photo_input(update: Update, context):
    state = context.user_data.get("state")
    if state not in ("wm_upload_first", "wm_awaiting_file"):
        return False
    wm = context.user_data.setdefault("wm", {})
    file_id = update.effective_message.photo[-1].file_id
    if state == "wm_upload_first":
        wm.update({"kind": "image", "file_id": file_id, "filename": "image"})
        context.user_data["state"] = "wm_choose_action"
        await update.effective_message.reply_text(
            "What would you like to do with this image?",
            reply_markup=_keyboard([[( "➕ Add a new watermark", "wm_action|add")],
                                    [( "🧽 Remove an existing watermark", "wm_action|remove")],
                                    [( "❌ Cancel", "wm_cancel")]])
        )
        return True
    await _process_uploaded(update.effective_message, context, file_id, wm, "image")
    return True


async def watermark_document_input(update: Update, context):
    state = context.user_data.get("state")
    if state not in ("wm_upload_first", "wm_awaiting_file"):
        return False
    message = update.effective_message
    wm = context.user_data.setdefault("wm", {})
    doc = message.document
    mime = (doc.mime_type or "").lower()
    filename = (doc.file_name or "").lower()
    is_pdf = mime == "application/pdf" or filename.endswith(".pdf")
    is_image = mime.startswith("image/") or filename.endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"))
    if state == "wm_upload_first":
        if not (is_pdf or is_image):
            await message.reply_text("❌ Upload an image or PDF.")
            return True
        wm.update({"kind": "pdf" if is_pdf else "image", "file_id": doc.file_id, "filename": doc.file_name or ""})
        context.user_data["state"] = "wm_choose_action"
        await message.reply_text(
            "What would you like to do with this file?",
            reply_markup=_keyboard([[( "➕ Add a new watermark", "wm_action|add")],
                                    [( "🧽 Remove an existing watermark", "wm_action|remove")],
                                    [( "❌ Cancel", "wm_cancel")]])
        )
        return True
    if wm.get("kind") == "pdf" and not is_pdf:
        await message.reply_text("❌ Please upload a PDF document for this operation.")
        return True
    if wm.get("kind") == "image" and not is_image:
        await message.reply_text("❌ Please upload an image file (PNG, JPG, WEBP, BMP, or TIFF).")
        return True
    await _process_uploaded(message, context, doc.file_id, wm, "pdf" if is_pdf else "image", filename)
    return True


def _friendly_error(exc):
    """Turn an exception into a safe, plain-language user message."""
    if isinstance(exc, (WatermarkError, WatermarkRemovalError)):
        return str(exc)
    msg = str(exc).strip() or type(exc).__name__ or "Unknown processing error."
    # Avoid leaking internal paths or library internals.
    if "MemoryError" in msg or isinstance(exc, MemoryError):
        return "The image is too large for watermark removal on this server. Try a smaller image (under 800px)."
    if "cv2" in msg or "OpenCV" in msg:
        return "Image processing failed on this server. Try a smaller or simpler image."
    return msg[:900]


async def _report_failure(status, context, wm, text):
    """Tell the user what failed and keep the session so they can retry.

    A failed attempt keeps the settings and the uploaded file reference. The
    session is only cleared after _MAX_ATTEMPTS failures. If the upload itself
    was rejected, the settings are kept and a new file is requested.
    """
    same_session = context.user_data.get("wm") is wm  # don't touch a session the user restarted
    if not wm.get("file_id"):
        if same_session:
            context.user_data["state"] = "wm_awaiting_file"
        text += "\nSend a different file to try again."
        markup = _keyboard([[("❌ Cancel", "wm_cancel")]])
    elif wm.get("_attempts", 0) >= _MAX_ATTEMPTS:
        if same_session:
            context.user_data["state"] = None
            context.user_data.pop("wm", None)
        text += "\nToo many failed attempts. Please start again."
        markup = _keyboard([[("🔄 Start over", "watermark")]])
    else:
        if same_session:
            context.user_data["state"] = "wm_retry"
        markup = _retry_keyboard(wm)
    try:
        await status.edit_text(text, reply_markup=markup)
    except Exception:
        pass  # session state is already set; a failed message edit must not crash the bot


async def _process_uploaded(message, context, file_id, wm, kind, filename=""):
    # Remember the input so a failed attempt can be retried without re-uploading.
    if wm.get("file_id") != file_id:
        wm["_attempts"] = 0
    wm["file_id"], wm["kind"] = file_id, kind
    if filename:
        wm["filename"] = filename
    wm["_attempts"] = wm.get("_attempts", 0) + 1
    status = await message.reply_text("⏳ Processing your file…")
    succeeded = False
    try:
        try:
            raw = await _download_file(message, context, file_id)
        except WatermarkError:
            wm.pop("file_id", None)  # empty / too large: this upload cannot be retried
            raise
        if kind == "image":
            if wm.get("action") == "add":
                await status.edit_text("💧 Adding watermark to your image…")
                result = await asyncio.wait_for(
                    asyncio.to_thread(_add_image_watermark, raw, wm), timeout=45
                )
                with Image.open(BytesIO(result)) as check_image:
                    check_image.verify()
                caption = "✅ Watermark added to your image."
            else:
                await status.edit_text("🤖 Detecting and removing the watermark…")
                result, count, warns = await asyncio.wait_for(
                    asyncio.to_thread(_remove_image, raw, wm), timeout=_REMOVE_TIMEOUT_IMAGE
                )
                if wm.get("remove_mode") == "auto":
                    caption = (
                        f"✅ Automatically removed {count} watermark region(s). "
                        "Please review the image; automatic detection may also affect ordinary text."
                    )
                else:
                    caption = f"✅ Removed {count} watermark region(s). Please review the image."
                if warns:
                    caption = (caption + " " + "; ".join(warns))[:1000]
                with Image.open(BytesIO(result)) as check_image:
                    check_image.verify()
            output = BytesIO(result)
            output.name = "watermarked_image.png" if wm.get("action") == "add" else "watermark_removed.png"
            await message.reply_document(document=output, filename=output.name, caption=caption)
        else:
            await status.edit_text("📄 Processing your PDF…")
            if wm.get("action") == "remove":
                if wm.get("remove_mode") == "auto" or wm.get("text") == _AUTO_SENTINEL:
                    raise WatermarkError(
                        "Automatic PDF watermark removal is not supported safely. "
                        "Use Specific watermark text for selectable PDF text, or convert the page to an image and use image removal."
                    )
                if wm.get("remove_mode") in ("region", "color"):
                    raise WatermarkError(
                        "Region and color removal work on images only. "
                        "For a PDF, use Specific watermark text for selectable PDF text, or convert the page to an image."
                    )
            result = await asyncio.wait_for(
                asyncio.to_thread(_process_pdf, raw, wm), timeout=_REMOVE_TIMEOUT_PDF
            )
            if not result:
                raise WatermarkError("The PDF processor returned an empty file. Please retry with a smaller PDF.")
            if wm.get("action") == "remove":
                matches = wm.get("_pdf_matches", 0)
                caption = f"✅ Removed {matches} matching text occurrence(s). White redaction blocks may be visible on colored backgrounds."
            else:
                with pymupdf.open(stream=result, filetype="pdf") as out_doc:
                    page_count = out_doc.page_count
                caption = f"✅ Watermark added to the PDF ({page_count} pages)."
            output = BytesIO(result)
            output.name = "watermarked.pdf" if wm.get("action") == "add" else "watermark_removed.pdf"
            await message.reply_document(document=output, filename=output.name, caption=caption)
        succeeded = True
        try:
            await status.edit_text("✅ Finished.")
        except Exception:
            pass  # the file was already delivered; a cosmetic edit failing is not a processing failure
    except asyncio.TimeoutError:
        await _report_failure(
            status, context, wm,
            "⏱️ Watermark removal timed out on this server. "
            "Try a smaller image (under 800px) or a shorter PDF. "
            "If it keeps happening, the server may be low on memory.",
        )
    except (WatermarkError, WatermarkRemovalError) as exc:
        await _report_failure(status, context, wm, "❌ " + _friendly_error(exc))
    except Exception as exc:
        await _report_failure(status, context, wm, "❌ Could not process this file: " + _friendly_error(exc))
    finally:
        if succeeded:
            if context.user_data.get("wm") is wm:
                context.user_data["state"] = None
                context.user_data.pop("wm", None)
        elif context.user_data.get("wm") is wm and context.user_data.get("state") == "wm_processing":
            # e.g. task cancelled mid-flight: never leave the user stuck in "processing"
            context.user_data["state"] = "wm_retry"
