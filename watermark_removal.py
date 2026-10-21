"""Core watermark-removal processor for SamiBOT.

Classical computer-vision only (OpenCV + NumPy). No generative AI, no
diffusion inpainting, no cloud services. Designed to remove promotional
watermarks (e.g. an orange banner with white text) from exam-page images
while preserving surrounding content.

The primary entry point is ``remove_watermark(image_bytes, mode, config, debug)``.
A PDF adapter ``remove_pdf_watermark`` handles image-based and text-based PDFs.
"""
from __future__ import annotations

import io
import json
from dataclasses import dataclass, field
from typing import Any, Optional

import cv2
import numpy as np
from PIL import Image, ImageOps

# ---------------------------------------------------------------------------
# Configuration defaults — all overridable via the ``config`` dict.
# ---------------------------------------------------------------------------

_DEFAULTS = {
    # Auto-mode HSV color detection (OpenCV hue scale: 0-179).
    "hue_low": 5,
    "hue_high": 25,
    "sat_min": 120,
    "val_min": 150,
    # White-text detection inside a detected banner.
    "white_sat_max": 60,
    "white_val_min": 200,
    # Connected-component / contour filtering.
    "min_area_fraction": 0.0005,   # 0.05% of image area
    "max_mask_fraction": 0.15,     # 15% of image area
    # Dilation to cover antialiased edges.
    "dilate_kernel": 7,
    "dilate_iterations": 2,
    # Inpainting.
    "inpaint_radius": 6,
    "inpaint_method": "telea",     # "telea" or "ns"
    # Region-mode preset defaults (fraction of image dimensions).
    "preset_width_frac": 0.35,
    "preset_height_frac": 0.12,
    # Color-mode tolerance.
    "color_tolerance": 40,
    # Working resolution cap (0 = no cap).
    "max_dimension": 0,
}


@dataclass
class RemovalResult:
    """Structured return from remove_watermark."""
    success: bool
    image_bytes: bytes
    regions: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    mask_coverage: float = 0.0
    elapsed_ms: int = 0
    debug: dict[str, Any] = field(default_factory=dict)


class WatermarkRemovalError(Exception):
    """User-facing error with a safe message."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _cfg(config: dict | None, key: str):
    merged = dict(_DEFAULTS)
    if config:
        merged.update(config)
    return merged[key]


def _full_config(config: dict | None) -> dict:
    merged = dict(_DEFAULTS)
    if config:
        merged.update(config)
    return merged


def _decode_image(image_bytes: bytes) -> np.ndarray:
    """Decode image bytes to a BGR uint8 array, honoring EXIF orientation."""
    if not image_bytes:
        raise WatermarkRemovalError("Empty image data.")
    buf = np.frombuffer(image_bytes, dtype=np.uint8)
    decoded = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    if decoded is None:
        raise WatermarkRemovalError(
            "Unsupported or corrupted image. Send JPG, PNG, or WEBP."
        )
    # Honor EXIF orientation via Pillow (lightweight, already a dependency).
    try:
        with Image.open(io.BytesIO(image_bytes)) as pil:
            oriented = ImageOps.exif_transpose(pil)
            if oriented is not None and oriented.size != (decoded.shape[1], decoded.shape[0]):
                oriented = oriented.convert("RGB")
                decoded = cv2.cvtColor(np.asarray(oriented), cv2.COLOR_RGB2BGR)
    except Exception:
        pass
    return decoded


def _maybe_resize(img: np.ndarray, max_dim: int) -> tuple[np.ndarray, float]:
    if max_dim <= 0:
        return img, 1.0
    h, w = img.shape[:2]
    scale = min(1.0, float(max_dim) / max(w, h))
    if scale >= 1.0:
        return img, 1.0
    resized = cv2.resize(img, (max(1, int(w * scale)), max(1, int(h * scale))),
                         interpolation=cv2.INTER_AREA)
    return resized, scale


def _encode_png(bgr: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", bgr, [cv2.IMWRITE_PNG_COMPRESSION, 6])
    if not ok:
        raise WatermarkRemovalError("Failed to encode the processed image.")
    return buf.tobytes()


def _inpaint(bgr: np.ndarray, mask: np.ndarray, cfg: dict) -> np.ndarray:
    if mask.max() == 0:
        return bgr
    if mask.dtype != np.uint8:
        mask = mask.astype(np.uint8)
    if mask.shape[:2] != bgr.shape[:2]:
        raise WatermarkRemovalError("Mask dimensions do not match the image.")
    radius = max(1, int(cfg["inpaint_radius"]))
    method = cv2.INPAINT_TELEA if cfg["inpaint_method"] == "telea" else cv2.INPAINT_NS
    return cv2.inpaint(bgr, mask, radius, method)


def _mask_coverage(mask: np.ndarray) -> float:
    total = mask.shape[0] * mask.shape[1]
    if total == 0:
        return 0.0
    return float(np.count_nonzero(mask)) / total


def _dilate(mask: np.ndarray, cfg: dict) -> np.ndarray:
    k = max(3, int(cfg["dilate_kernel"]))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    return cv2.dilate(mask, kernel, iterations=max(1, int(cfg["dilate_iterations"])))


def _bbox_from_mask(mask: np.ndarray) -> list[int] | None:
    ys, xs = np.where(mask > 0)
    if len(xs) == 0:
        return None
    return [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())]


def _validate_and_filter_mask(mask: np.ndarray, cfg: dict) -> tuple[np.ndarray, list[dict]]:
    """Filter connected components by min area and return valid regions."""
    h, w = mask.shape[:2]
    total_area = h * w
    min_area = cfg["min_area_fraction"] * total_area
    max_area = cfg["max_mask_fraction"] * total_area

    num, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    filtered = np.zeros_like(mask)
    regions: list[dict] = []

    for i in range(1, num):
        area = int(stats[i, cv2.CC_STAT_AREA])
        if area < min_area:
            continue
        region_mask = (labels == i).astype(np.uint8) * 255
        bbox = _bbox_from_mask(region_mask)
        if bbox is None:
            continue
        regions.append({
            "bbox": bbox,
            "area": area,
            "area_fraction": area / total_area,
        })
        filtered = cv2.bitwise_or(filtered, region_mask)

    return filtered, regions


def _enforce_max_coverage(mask: np.ndarray, cfg: dict) -> bool:
    """Return True if mask is within the max-coverage limit."""
    coverage = _mask_coverage(mask)
    return coverage <= cfg["max_mask_fraction"]


# ---------------------------------------------------------------------------
# Mode: auto — HSV color detection + white-text inclusion
# ---------------------------------------------------------------------------

def _detect_color_banner(bgr: np.ndarray, cfg: dict) -> np.ndarray:
    """Detect watermark banner pixels using configurable HSV thresholds."""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    hue_lo = int(cfg["hue_low"])
    hue_hi = int(cfg["hue_high"])
    sat_min = int(cfg["sat_min"])
    val_min = int(cfg["val_min"])
    color_mask = cv2.inRange(hsv, (hue_lo, sat_min, val_min), (hue_hi, 255, 255))
    # Clean up noise.
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    color_mask = cv2.morphologyEx(color_mask, cv2.MORPH_OPEN, kernel)
    color_mask = cv2.morphologyEx(color_mask, cv2.MORPH_CLOSE, kernel)
    return color_mask


def _fill_banner_interior(color_mask: np.ndarray) -> np.ndarray:
    """Fill the interior of detected banner contours so the full banner is masked."""
    contours, _ = cv2.findContours(color_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    filled = np.zeros_like(color_mask)
    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area < 10:
            continue
        # Use the convex hull to approximate rounded rectangles.
        hull = cv2.convexHull(cnt)
        cv2.drawContours(filled, [hull], -1, 255, thickness=cv2.FILLED)
    return filled


def _detect_white_text_in_regions(bgr: np.ndarray, banner_mask: np.ndarray, cfg: dict) -> np.ndarray:
    """Detect white/near-white text pixels inside the detected banner regions."""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    white_mask = cv2.inRange(
        hsv,
        (0, 0, int(cfg["white_val_min"])),
        (180, int(cfg["white_sat_max"]), 255),
    )
    # Only keep white pixels that fall within the banner mask (dilated slightly
    # to catch text that extends just past the banner edge).
    dilated_banner = cv2.dilate(banner_mask, np.ones((9, 9), np.uint8), iterations=1)
    return cv2.bitwise_and(white_mask, dilated_banner)


def _auto_detect(bgr: np.ndarray, cfg: dict) -> tuple[np.ndarray, list[dict]]:
    """Auto-mode: detect colored banner + white text, combine, validate."""
    color_mask = _detect_color_banner(bgr, cfg)
    if color_mask.max() == 0:
        return np.zeros_like(color_mask), []

    banner_filled = _fill_banner_interior(color_mask)
    white_text = _detect_white_text_in_regions(bgr, banner_filled, cfg)
    combined = cv2.bitwise_or(banner_filled, white_text)

    # Filter by min area.
    filtered, regions = _validate_and_filter_mask(combined, cfg)
    return filtered, regions


# ---------------------------------------------------------------------------
# Mode: region — user-supplied rectangle or position preset
# ---------------------------------------------------------------------------

def _region_detect(bgr: np.ndarray, cfg: dict, region: dict) -> tuple[np.ndarray, list[dict]]:
    """Region-mode: build a conservative mask within a user-specified area."""
    h, w = bgr.shape[:2]
    rect = region.get("rect")  # (x, y, width, height)
    preset = region.get("preset")

    if rect:
        x, y, rw, rh = rect
        x = max(0, min(int(x), w))
        y = max(0, min(int(y), h))
        x2 = max(x + 1, min(int(x + rw), w))
        y2 = max(y + 1, min(int(y + rh), h))
    elif preset:
        pw = int(w * cfg["preset_width_frac"])
        ph = int(h * cfg["preset_height_frac"])
        margin = 10
        if preset == "bottom-right":
            x, y, x2, y2 = w - pw - margin, h - ph - margin, w - margin, h - margin
        elif preset == "bottom-left":
            x, y, x2, y2 = margin, h - ph - margin, pw + margin, h - margin
        elif preset == "top-right":
            x, y, x2, y2 = w - pw - margin, margin, w - margin, ph + margin
        elif preset == "top-left":
            x, y, x2, y2 = margin, margin, pw + margin, ph + margin
        else:
            raise WatermarkRemovalError(f"Unknown preset: {preset}")
    else:
        raise WatermarkRemovalError("Region mode requires 'rect' or 'preset'.")

    roi = bgr[y:y2, x:x2]
    if roi.size == 0:
        raise WatermarkRemovalError("The selected region is outside the image bounds.")

    # Within the ROI, detect colored + white pixels (reuse auto logic locally).
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    color_mask = cv2.inRange(
        hsv,
        (int(cfg["hue_low"]), int(cfg["sat_min"]), int(cfg["val_min"])),
        (int(cfg["hue_high"]), 255, 255),
    )
    white_mask = cv2.inRange(
        hsv, (0, 0, int(cfg["white_val_min"])),
        (180, int(cfg["white_sat_max"]), 255),
    )
    local = cv2.bitwise_or(color_mask, white_mask)

    # If local detection finds nothing, use the full ROI as a fallback mask
    # (the user explicitly asked to remove this region).
    if local.max() == 0:
        local = np.full(roi.shape[:2], 255, dtype=np.uint8)

    full_mask = np.zeros((h, w), dtype=np.uint8)
    full_mask[y:y2, x:x2] = local

    filtered, regions = _validate_and_filter_mask(full_mask, cfg)
    # Add the explicit ROI bbox if nothing passed the component filter.
    if not regions:
        regions = [{"bbox": [x, y, x2, y2], "area": int((x2 - x) * (y2 - y)),
                     "area_fraction": ((x2 - x) * (y2 - y)) / (w * h)}]
        filtered = full_mask
    return filtered, regions


# ---------------------------------------------------------------------------
# Mode: color — user-specified target color with tolerance
# ---------------------------------------------------------------------------

def _color_detect(bgr: np.ndarray, cfg: dict, target_color: tuple[int, int, int]) -> tuple[np.ndarray, list[dict]]:
    """Color-mode: detect pixels close to a user-specified BGR color."""
    target = np.array(target_color, dtype=np.int32)
    diff = np.abs(bgr.astype(np.int32) - target)
    dist = np.sqrt(np.sum(diff ** 2, axis=2)).astype(np.float32)
    tolerance = float(cfg["color_tolerance"])
    raw = (dist <= tolerance).astype(np.uint8) * 255

    # Morphological cleanup.
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    raw = cv2.morphologyEx(raw, cv2.MORPH_OPEN, kernel)
    raw = cv2.morphologyEx(raw, cv2.MORPH_CLOSE, kernel)

    # Also detect white text inside detected regions.
    filled = _fill_banner_interior(raw)
    white_text = _detect_white_text_in_regions(bgr, filled, cfg)
    combined = cv2.bitwise_or(filled, white_text)

    filtered, regions = _validate_and_filter_mask(combined, cfg)
    return filtered, regions


# ---------------------------------------------------------------------------
# Public API: remove_watermark
# ---------------------------------------------------------------------------

def remove_watermark(
    image_bytes: bytes,
    mode: str = "auto",
    config: dict | None = None,
    debug: bool = False,
) -> RemovalResult:
    """Remove a watermark from an image using classical CV only.

    Args:
        image_bytes: Raw JPEG/PNG/WEBP image bytes.
        mode: "auto", "region", or "color".
        config: Optional dict overriding defaults from _DEFAULTS.
            For region mode: {"region": {"rect": (x,y,w,h)}} or
            {"region": {"preset": "bottom-right"}}.
            For color mode: {"target_color": (B, G, R)}.
        debug: If True, include debug artifacts in the result.

    Returns:
        RemovalResult with processed image bytes, detected regions, and warnings.
    """
    import time
    started = time.perf_counter()
    cfg = _full_config(config)

    # Decode.
    original_bytes = image_bytes  # Never overwrite the original.
    bgr = _decode_image(original_bytes)
    h, w = bgr.shape[:2]

    # Optional working-resolution cap (preserves aspect ratio).
    working, scale = _maybe_resize(bgr, cfg["max_dimension"])

    # Detect based on mode.
    if mode == "auto":
        mask, regions = _auto_detect(working, cfg)
    elif mode == "region":
        region = (config or {}).get("region")
        if not region:
            raise WatermarkRemovalError("Region mode requires a 'region' in config.")
        mask, regions = _region_detect(working, cfg, region)
    elif mode == "color":
        target_color = (config or {}).get("target_color")
        if not target_color or len(target_color) != 3:
            raise WatermarkRemovalError("Color mode requires 'target_color' (B, G, R) in config.")
        mask, regions = _color_detect(working, cfg, tuple(target_color))
    else:
        raise WatermarkRemovalError(f"Unknown mode: {mode}. Use 'auto', 'region', or 'color'.")

    warnings: list[str] = []

    # No watermark found — return original unchanged.
    if mask.max() == 0 or not regions:
        warnings.append("No credible watermark was detected. The original image is returned unchanged.")
        return RemovalResult(
            success=False,
            image_bytes=original_bytes,
            warnings=warnings,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            debug={"mode": mode, "regions": [], "mask_coverage": 0.0} if debug else {},
        )

    # Dilate to cover antialiased edges.
    mask = _dilate(mask, cfg)

    # Enforce max coverage safety limit.
    coverage = _mask_coverage(mask)
    if coverage > cfg["max_mask_fraction"]:
        warnings.append(
            f"The detected watermark region covers {coverage*100:.1f}% of the image, "
            f"exceeding the {cfg['max_mask_fraction']*100:.0f}% safety limit. "
            "Automatic removal was aborted to protect the image. The original is returned unchanged."
        )
        return RemovalResult(
            success=False,
            image_bytes=original_bytes,
            regions=regions,
            warnings=warnings,
            mask_coverage=coverage,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            debug={"mode": mode, "regions": regions, "mask_coverage": coverage} if debug else {},
        )

    # Inpaint.
    repaired = _inpaint(working, mask, cfg)

    # If we downscaled for processing, upscale back to original resolution.
    if scale < 1.0:
        repaired = cv2.resize(repaired, (w, h), interpolation=cv2.INTER_CUBIC)
        # Pixels outside the mask: replace with original to guarantee preservation.
        mask_full = cv2.resize(mask, (w, h), interpolation=cv2.INTER_NEAREST)
        original_outside = (mask_full == 0)
        repaired = np.where(original_outside[..., None], bgr, repaired)

    # Encode as lossless PNG.
    result_bytes = _encode_png(repaired)

    # Validate output.
    decoded_check = cv2.imdecode(np.frombuffer(result_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
    if decoded_check is None:
        warnings.append("The processed image failed validation. The original is returned.")
        return RemovalResult(
            success=False,
            image_bytes=original_bytes,
            warnings=warnings,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
        )

    # Scale region bboxes back to original coordinates if needed.
    scaled_regions = []
    for r in regions:
        bbox = r["bbox"]
        if scale < 1.0:
            bbox = [int(v / scale) for v in bbox]
        scaled_regions.append({
            "bbox": bbox,
            "description": _describe_region(mode, r),
            "area": r.get("area"),
            "area_fraction": r.get("area_fraction"),
        })

    debug_info = {}
    if debug:
        debug_info = {
            "mode": mode,
            "regions": scaled_regions,
            "mask_coverage": coverage,
            "mask_shape": list(mask.shape),
            "image_shape": [w, h],
            "config": cfg,
        }

    return RemovalResult(
        success=True,
        image_bytes=result_bytes,
        regions=scaled_regions,
        warnings=warnings,
        mask_coverage=coverage,
        elapsed_ms=int((time.perf_counter() - started) * 1000),
        debug=debug_info,
    )


def _describe_region(mode: str, region: dict) -> str:
    if mode == "color":
        return "user-specified color watermark region"
    if mode == "region":
        return "user-selected watermark region"
    return "detected colored watermark banner with text"


# ---------------------------------------------------------------------------
# PDF support
# ---------------------------------------------------------------------------

def remove_pdf_watermark(
    pdf_bytes: bytes,
    mode: str = "auto",
    config: dict | None = None,
    text: str | None = None,
    debug: bool = False,
) -> dict:
    """Remove watermarks from a PDF.

    For text-based PDFs: remove selectable text matching ``text`` via redaction.
    For image-based (scanned) PDFs: render pages, run remove_watermark, and
    rebuild the PDF preserving page order and dimensions.

    Returns a dict with:
        - success: bool
        - pdf_bytes: bytes (processed or original)
        - warnings: list[str]
        - pages_processed: int
    """
    import pymupdf
    import time
    started = time.perf_counter()
    cfg = _full_config(config)
    warnings: list[str] = []

    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    if not doc.page_count:
        doc.close()
        raise WatermarkRemovalError("This PDF contains no pages.")
    if doc.page_count > 150:
        doc.close()
        raise WatermarkRemovalError("For safety, PDFs are limited to 150 pages per operation.")

    # If text is provided, try text-based removal first (selectable text).
    if text and mode == "text":
        total_matches = 0
        for page in doc:
            rects = page.search_for(text, quads=False)
            if not rects:
                continue
            total_matches += len(rects)
            for rect in rects:
                page.add_redact_annot(rect, fill=(1, 1, 1), cross_out=False)
            page.apply_redactions(images=0, graphics=0, text=0)
        if total_matches == 0:
            warnings.append(
                "That text was not found as selectable PDF text. "
                "The PDF may be scanned/image-based."
            )
            result = doc.tobytes(garbage=4, deflate=True)
            doc.close()
            return {
                "success": False,
                "pdf_bytes": pdf_bytes,
                "warnings": warnings,
                "pages_processed": 0,
                "elapsed_ms": int((time.perf_counter() - started) * 1000),
            }
        result = doc.tobytes(garbage=4, deflate=True)
        doc.close()
        return {
            "success": True,
            "pdf_bytes": result,
            "warnings": warnings,
            "pages_processed": total_matches,
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
        }

    # Image-based / scanned PDF: render each page, process, rebuild.
    if mode in ("auto", "region", "color"):
        pages_processed = 0
        new_doc = pymupdf.open()  # empty doc
        for page_idx in range(doc.page_count):
            page = doc[page_idx]
            # Render at 200 DPI for good quality without huge memory use.
            zoom = 200 / 72
            mat = pymupdf.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat)
            img_bytes = pix.tobytes("png")

            try:
                result = remove_watermark(img_bytes, mode=mode, config=config, debug=debug)
            except WatermarkRemovalError as exc:
                warnings.append(f"Page {page_idx + 1}: {exc}")
                # Keep original page as-is.
                result = RemovalResult(success=False, image_bytes=img_bytes)

            if result.success:
                # Insert the processed image as a new page with original dimensions.
                img_pil = Image.open(io.BytesIO(result.image_bytes))
                img_w, img_h = img_pil.size
                # Convert PNG bytes to pixmap for insertion.
                rect = page.rect
                new_page = new_doc.new_page(width=rect.width, height=rect.height)
                new_page.insert_image(rect, stream=result.image_bytes)
                pages_processed += 1
            else:
                # Keep the original rendered page.
                rect = page.rect
                new_page = new_doc.new_page(width=rect.width, height=rect.height)
                new_page.insert_image(rect, stream=img_bytes)

        result_bytes = new_doc.tobytes(garbage=4, deflate=True)
        new_doc.close()
        doc.close()

        if pages_processed == 0:
            warnings.append("No watermark was detected on any page. The original PDF is returned.")
            return {
                "success": False,
                "pdf_bytes": pdf_bytes,
                "warnings": warnings,
                "pages_processed": 0,
                "elapsed_ms": int((time.perf_counter() - started) * 1000),
            }

        return {
            "success": True,
            "pdf_bytes": result_bytes,
            "warnings": warnings,
            "pages_processed": pages_processed,
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
        }

    doc.close()
    raise WatermarkRemovalError(f"Unsupported PDF removal mode: {mode}")
