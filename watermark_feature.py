"""Watermark tools for SamiBOT: add text watermarks or remove a specified text watermark.

Add functionality is unchanged. Remove functionality is hardened so it cannot
hang or crash the bot: OCR is loaded lazily and capped in resolution, inpainting
uses the faster NS algorithm, a fast non-OCR regional remover is used when OCR is
unavailable or the target is an image watermark, and every failure surfaces a
clear, user-facing message instead of a raw traceback.
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

_MAX_MEDIA_BYTES = 18 * 1024 * 1024
# Keep the working image small so inpainting stays fast (under ~15s) and never
# OOM the process. No OCR is used — removal is pure OpenCV.
_REMOVE_MAX_DIMENSION = 800
_REMOVE_INPAINT_RADIUS = 3
_REMOVE_TIMEOUT_IMAGE = 20
_REMOVE_TIMEOUT_PDF = 30

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
        if mode not in ("specific", "auto") or not wm.get("file_id"):
            await q.message.reply_text("❌ Session expired. Start again with /watermark.")
            return True
        wm["action"] = "remove"
        wm["remove_mode"] = mode
        if mode == "specific":
            context.user_data["state"] = "wm_text_remove"
            await q.message.reply_text("Send the exact watermark text to remove.", reply_markup=_keyboard([[("❌ Cancel", "wm_cancel")]]))
        else:
            wm["text"] = "__AUTO_WATERMARK__"
            context.user_data["state"] = "wm_processing"
            await q.message.reply_text(
                "🤖 Scanning for likely watermark text (for example, diagonal or faint text). "
                "Automatic detection can miss watermarks or select ordinary text, so check the result."
            )
            await _process_uploaded(q.message, context, wm["file_id"], wm, wm["kind"], wm.get("filename", ""))
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
            await q.message.reply_text(
                "Choose how to remove the watermark:",
                reply_markup=_keyboard([[( "1️⃣ Specific watermark text", "wm_remove_mode|specific")],
                                        [( "2️⃣ Automatic (look for the word watermark)", "wm_remove_mode|auto")],
                                        [( "❌ Cancel", "wm_cancel")]])
            )
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


# --- REMOVE: fast OpenCV-only helpers (no OCR, completes in seconds) ---

def _load_image_for_remove(data):
    """Open, EXIF-orient, and downscale so removal stays fast and memory-safe."""
    try:
        with Image.open(BytesIO(data)) as opened:
            rgb_pil = ImageOps.exif_transpose(opened).convert("RGB")
    except Exception as exc:
        raise WatermarkError("Unsupported or damaged image. Send JPG, PNG, or WEBP.") from exc
    width, height = rgb_pil.size
    scale = min(1.0, float(_REMOVE_MAX_DIMENSION) / max(width, height))
    if scale < 1.0:
        rgb_pil = rgb_pil.resize(
            (max(1, int(width * scale)), max(1, int(height * scale))),
            Image.Resampling.LANCZOS,
        )
    return np.asarray(rgb_pil)


def _inpaint(mask, bgr):
    """Fast NS inpainting with a small radius to avoid long CPU stalls."""
    if mask.max() == 0:
        return bgr
    mask = cv2.dilate(mask, np.ones((5, 5), np.uint8), iterations=1)
    return cv2.inpaint(bgr, mask, _REMOVE_INPAINT_RADIUS, cv2.INPAINT_NS)


def _encode_png(bgr):
    out = BytesIO()
    Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)).save(out, format="PNG", optimize=True)
    return out.getvalue()


def _render_text_templates(text, width, height):
    """Render the target text at several sizes/colors as grayscale templates for matching."""
    templates = []
    base_size = max(12, min(width, height) // 12)
    for size in (base_size, base_size * 2, base_size // 2):
        if size < 10:
            continue
        font = _font(size)
        for color in ((255, 255, 255), (0, 0, 0), (200, 200, 200), (128, 128, 128)):
            tmp_img = Image.new("L", (width, height), 128)
            draw = ImageDraw.Draw(tmp_img)
            bbox = draw.textbbox((0, 0), text, font=font, stroke_width=1)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
            if tw <= 0 or th <= 0 or tw >= width or th >= height:
                continue
            draw.text((2, 2), text, fill=color, font=font, stroke_width=1)
            cropped = tmp_img.crop((0, 0, min(tw + 6, width), min(th + 6, height)))
            templates.append(np.asarray(cropped))
    return templates


def _remove_image_text(data, target):
    """Remove a specific text watermark using fast template matching + inpainting.

    No OCR is used. The target text is rendered at several sizes and matched
    against the image with cv2.matchTemplate. This completes in seconds.
    """
    rgb = _load_image_for_remove(data)
    height, width = rgb.shape[:2]
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    mask = np.zeros((height, width), dtype=np.uint8)
    matches = 0

    for template in _render_text_templates(target, width, height):
        th, tw = template.shape[:2]
        if th >= height or tw >= width:
            continue
        for method in (cv2.TM_SQDIFF_NORMED, cv2.TM_CCOEFF_NORMED):
            try:
                result = cv2.matchTemplate(gray, template, method)
            except Exception:
                continue
            if method == cv2.TM_SQDIFF_NORMED:
                locs = np.where(result <= 0.3)
            else:
                locs = np.where(result >= 0.7)
            for pt in zip(*locs[::-1]):
                cv2.rectangle(mask, pt, (pt[0] + tw, pt[1] + th), 255, -1)
                matches += 1

    if not matches:
        raise WatermarkError(
            f"The text \"{target}\" was not found. Check the spelling, or try Automatic mode. "
            "For non-English text, Automatic mode may work better."
        )
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    repaired = _inpaint(mask, bgr)
    return _encode_png(repaired), matches


def _remove_image_watermark_auto(data):
    """Remove likely watermark regions from an image using fast color/contrast detection.

    No OCR is used. Detects light (near-white) and dark overlay regions that are
    typical of watermarks, then inpaints them. Completes in a few seconds.
    """
    rgb = _load_image_for_remove(data)
    height, width = rgb.shape[:2]
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    mask = np.zeros((height, width), dtype=np.uint8)
    selected = 0

    try:
        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
        # Watermarks are often light (near-white) or dark overlays over the image.
        light = cv2.inRange(hsv, (0, 0, 200), (180, 40, 255))
        dark = cv2.inRange(hsv, (0, 0, 0), (180, 40, 55))
        candidate = cv2.bitwise_or(light, dark)
        candidate = cv2.morphologyEx(
            candidate, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8)
        )
        # Keep only medium-sized connected regions (text-sized), not huge
        # background areas or tiny specks.
        num, labels, stats, _ = cv2.connectedComponentsWithStats(candidate, connectivity=8)
        max_area = 0.12 * width * height
        for i in range(1, num):
            area = int(stats[i, cv2.CC_STAT_AREA])
            if 40 <= area <= max_area:
                region = (labels == i).astype(np.uint8) * 255
                mask = cv2.bitwise_or(mask, region)
                selected += 1
    except Exception:
        pass

    if not selected:
        raise WatermarkError(
            "Automatic removal could not find a clear watermark on this image. "
            "Try Specific watermark text, or upload a clearer image with a more visible watermark."
        )

    repaired = _inpaint(mask, bgr)
    return _encode_png(repaired), selected


def _process_pdf(data, wm):
    doc = pymupdf.open(stream=data, filetype="pdf")
    if not doc.page_count:
        doc.close()
        raise WatermarkError("This PDF contains no pages.")
    if doc.page_count > 150:
        doc.close()
        raise WatermarkError("For safety, PDFs are limited to 150 pages per operation.")
    total_matches = 0
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
                total_matches += len(rects)
                for rect in rects:
                    page.add_redact_annot(rect, fill=(1, 1, 1), cross_out=False)
                page.apply_redactions(images=0, graphics=0, text=0)
        if wm["action"] == "remove" and total_matches == 0:
            raise WatermarkError(
                "That text was not found as selectable PDF text. "
                "Scanned or image-based watermarks need OCR; convert the page to an image and use image removal."
            )
        wm["_pdf_matches"] = total_matches
        return doc.tobytes(garbage=4, deflate=True)
    finally:
        doc.close()


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
    if isinstance(exc, WatermarkError):
        return str(exc)
    msg = str(exc).strip() or type(exc).__name__ or "Unknown processing error."
    # Avoid leaking internal paths or library internals.
    if "MemoryError" in msg or isinstance(exc, MemoryError):
        return "The image is too large for watermark removal on this server. Try a smaller image (under 800px)."
    if "cv2" in msg or "OpenCV" in msg:
        return "Image processing failed on this server. Try a smaller or simpler image."
    return msg[:900]


async def _process_uploaded(message, context, file_id, wm, kind, filename=""):
    status = await message.reply_text("⏳ Processing your file…")
    try:
        raw = await _download_file(message, context, file_id)
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
                is_auto = wm.get("remove_mode") == "auto" or wm.get("text") == "__AUTO_WATERMARK__"
                if is_auto:
                    await status.edit_text("🤖 Detecting and removing the watermark…")
                    result, count = await asyncio.wait_for(
                        asyncio.to_thread(_remove_image_watermark_auto, raw), timeout=_REMOVE_TIMEOUT_IMAGE
                    )
                    caption = (
                        f"✅ Automatically removed {count} watermark region(s). "
                        "Please review the image; automatic detection may also affect ordinary text."
                    )
                else:
                    await status.edit_text(f"🔎 Scanning image for “{wm.get('text', '')}”…")
                    result, count = await asyncio.wait_for(
                        asyncio.to_thread(_remove_image_text, raw, wm["text"]), timeout=_REMOVE_TIMEOUT_IMAGE
                    )
                    caption = f"✅ Removed matching text from {count} detected region(s). Some background repair may be visible."
                with Image.open(BytesIO(result)) as check_image:
                    check_image.verify()
            output = BytesIO(result)
            output.name = "watermarked_image.png" if wm.get("action") == "add" else "watermark_removed.png"
            await message.reply_document(document=output, filename=output.name, caption=caption)
        else:
            await status.edit_text("📄 Processing your PDF…")
            if wm.get("action") == "remove" and (
                wm.get("remove_mode") == "auto" or wm.get("text") == "__AUTO_WATERMARK__"
            ):
                raise WatermarkError(
                    "Automatic PDF watermark removal is not supported safely. "
                    "Use Specific watermark text for selectable PDF text, or convert the page to an image and use image removal."
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
        await status.edit_text("✅ Finished.")
    except asyncio.TimeoutError:
        await status.edit_text(
            "⏱️ Watermark removal timed out on this server. "
            "Try a smaller image (under 800px) or a shorter PDF. "
            "If it keeps happening, the server may be low on memory."
        )
    except WatermarkError as exc:
        await status.edit_text("❌ " + _friendly_error(exc))
    except Exception as exc:
        await status.edit_text("❌ Could not process this file: " + _friendly_error(exc))
    finally:
        context.user_data["state"] = None
        context.user_data.pop("wm", None)
