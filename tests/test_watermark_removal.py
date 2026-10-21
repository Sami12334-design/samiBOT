"""Focused tests for the watermark-removal core processor.

Uses synthetic images only — no private user files or external APIs.
Run with: python -m pytest tests/test_watermark_removal.py -v
"""
import io
import unittest

import cv2
import numpy as np
from PIL import Image

from watermark_removal import (
    remove_watermark,
    remove_pdf_watermark,
    RemovalResult,
    WatermarkRemovalError,
)


def _encode_png(bgr):
    ok, buf = cv2.imencode(".png", bgr)
    assert ok
    return buf.tobytes()


def _encode_jpeg(bgr, quality=90):
    ok, buf = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, quality])
    assert ok
    return buf.tobytes()


def _make_blank_page(w=400, h=300):
    img = np.full((h, w, 3), 245, dtype=np.uint8)
    for y in range(40, h - 40, 30):
        cv2.line(img, (30, y), (w - 30, y), (40, 40, 40), 1)
    return img


def _make_orange_watermark(img, x=280, y=250, w=100, h=30):
    overlay = img.copy()
    cv2.rectangle(overlay, (x, y), (x + w, y + h), (10, 120, 220), -1)
    cv2.putText(overlay, "Golden", (x + 8, y + 20),
                cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1, cv2.LINE_AA)
    return overlay


def _make_orange_watermark_over_text(img):
    overlay = img.copy()
    h, w = overlay.shape[:2]
    x, y, bw, bh = w // 2 - 50, h // 2 - 15, 100, 30
    cv2.rectangle(overlay, (x, y), (x + bw, y + bh), (10, 120, 220), -1)
    cv2.putText(overlay, "Golden", (x + 8, y + 20),
                cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1, cv2.LINE_AA)
    return overlay


class TestRemoveWatermark(unittest.TestCase):

    def test_1_watermark_on_blank_margin(self):
        page = _make_blank_page()
        page = _make_orange_watermark(page)
        result = remove_watermark(_encode_png(page), mode="auto")
        self.assertTrue(result.success)
        self.assertGreater(len(result.regions), 0)
        self.assertGreater(result.mask_coverage, 0)
        self.assertLess(result.mask_coverage, 0.15)

    def test_2_watermark_overlapping_text(self):
        page = _make_blank_page()
        page = _make_orange_watermark_over_text(page)
        result = remove_watermark(_encode_png(page), mode="auto")
        self.assertTrue(result.success)
        self.assertLess(result.mask_coverage, 0.15)
        decoded = cv2.imdecode(np.frombuffer(result.image_bytes, np.uint8), cv2.IMREAD_COLOR)
        self.assertIsNotNone(decoded)
        self.assertEqual(decoded.shape[0], page.shape[0])
        self.assertEqual(decoded.shape[1], page.shape[1])

    def test_3_no_watermark(self):
        page = _make_blank_page()
        img_bytes = _encode_png(page)
        result = remove_watermark(img_bytes, mode="auto")
        self.assertFalse(result.success)
        self.assertEqual(result.image_bytes, img_bytes)
        self.assertGreater(len(result.warnings), 0)

    def test_4_corrupted_image(self):
        with self.assertRaises(WatermarkRemovalError):
            remove_watermark(b"not an image at all", mode="auto")

    def test_5_empty_image_bytes(self):
        with self.assertRaises(WatermarkRemovalError):
            remove_watermark(b"", mode="auto")

    def test_6_mask_exceeds_15_percent(self):
        img = np.full((200, 200, 3), 0, dtype=np.uint8)
        img[:] = (10, 120, 220)
        img_bytes = _encode_png(img)
        result = remove_watermark(img_bytes, mode="auto")
        self.assertFalse(result.success)
        self.assertEqual(result.image_bytes, img_bytes)
        self.assertTrue(any("safety limit" in w for w in result.warnings))

    def test_7_small_candidate_filtered(self):
        page = _make_blank_page(w=1000, h=1000)
        page[500, 500] = (10, 120, 220)
        img_bytes = _encode_png(page)
        result = remove_watermark(img_bytes, mode="auto")
        self.assertFalse(result.success)
        self.assertEqual(result.image_bytes, img_bytes)

    def test_8_user_supplied_rectangle(self):
        page = _make_blank_page()
        page = _make_orange_watermark(page, x=280, y=250, w=100, h=30)
        config = {"region": {"rect": (270, 240, 120, 50)}}
        result = remove_watermark(_encode_png(page), mode="region", config=config)
        self.assertTrue(result.success)
        self.assertGreater(len(result.regions), 0)

    def test_9_configurable_color_and_tolerance(self):
        page = _make_blank_page()
        page = _make_orange_watermark(page)
        config = {"target_color": (10, 120, 220), "color_tolerance": 40}
        result = remove_watermark(_encode_png(page), mode="color", config=config)
        self.assertTrue(result.success)
        self.assertGreater(len(result.regions), 0)

    def test_10a_jpeg_input(self):
        page = _make_blank_page()
        page = _make_orange_watermark(page)
        result = remove_watermark(_encode_jpeg(page), mode="auto")
        self.assertTrue(result.success)
        decoded = cv2.imdecode(np.frombuffer(result.image_bytes, np.uint8), cv2.IMREAD_COLOR)
        self.assertIsNotNone(decoded)

    def test_10b_png_input(self):
        page = _make_blank_page()
        page = _make_orange_watermark(page)
        result = remove_watermark(_encode_png(page), mode="auto")
        self.assertTrue(result.success)
        decoded = cv2.imdecode(np.frombuffer(result.image_bytes, np.uint8), cv2.IMREAD_COLOR)
        self.assertIsNotNone(decoded)

    def test_11_output_dimensions_and_decodability(self):
        page = _make_blank_page(w=350, h=250)
        page = _make_orange_watermark(page, x=250, y=200, w=80, h=30)
        result = remove_watermark(_encode_png(page), mode="auto")
        self.assertTrue(result.success)
        self.assertGreater(len(result.image_bytes), 0)
        decoded = cv2.imdecode(np.frombuffer(result.image_bytes, np.uint8), cv2.IMREAD_COLOR)
        self.assertIsNotNone(decoded)
        self.assertEqual(decoded.shape[:2], (250, 350))

    def test_12_pixel_preservation_outside_mask(self):
        page = _make_blank_page()
        page = _make_orange_watermark(page, x=280, y=250, w=100, h=30)
        img_bytes = _encode_png(page)
        result = remove_watermark(img_bytes, mode="auto", debug=True)
        self.assertTrue(result.success)
        orig_bgr = cv2.imdecode(np.frombuffer(img_bytes, np.uint8), cv2.IMREAD_COLOR)
        out_bgr = cv2.imdecode(np.frombuffer(result.image_bytes, np.uint8), cv2.IMREAD_COLOR)
        orig_corner = orig_bgr[0:50, 0:50]
        out_corner = out_bgr[0:50, 0:50]
        np.testing.assert_array_equal(orig_corner, out_corner)

    def test_13_failure_fallback_to_original(self):
        page = _make_blank_page()
        img_bytes = _encode_png(page)
        result = remove_watermark(img_bytes, mode="auto")
        self.assertFalse(result.success)
        self.assertEqual(result.image_bytes, img_bytes)

    def test_14_concurrent_processing_no_collisions(self):
        import concurrent.futures
        page1 = _make_orange_watermark(_make_blank_page(w=400, h=300))
        page2 = _make_orange_watermark(_make_blank_page(w=500, h=400))
        page3 = _make_blank_page(w=300, h=200)
        bytes1 = _encode_png(page1)
        bytes2 = _encode_png(page2)
        bytes3 = _encode_png(page3)

        def run(b):
            return remove_watermark(b, mode="auto")

        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(run, b) for b in (bytes1, bytes2, bytes3)]
            results = [f.result() for f in futures]
        self.assertTrue(results[0].success)
        self.assertTrue(results[1].success)
        self.assertFalse(results[2].success)
        d0 = cv2.imdecode(np.frombuffer(results[0].image_bytes, np.uint8), cv2.IMREAD_COLOR)
        d1 = cv2.imdecode(np.frombuffer(results[1].image_bytes, np.uint8), cv2.IMREAD_COLOR)
        self.assertEqual(d0.shape[:2], (300, 400))
        self.assertEqual(d1.shape[:2], (400, 500))

    def test_15_pdf_text_based_removal(self):
        import pymupdf
        doc = pymupdf.open()
        page = doc.new_page(width=400, height=300)
        page.insert_text((50, 100), "Exam Question 1", fontsize=12, fontname="helv")
        page.insert_text((50, 200), "WATERMARK", fontsize=24, fontname="helv", color=(0.8, 0.4, 0.1))
        pdf_bytes = doc.tobytes()
        doc.close()
        result = remove_pdf_watermark(pdf_bytes, mode="text", text="WATERMARK")
        self.assertTrue(result["success"])
        self.assertGreater(result["pages_processed"], 0)

    def test_15b_pdf_image_based_removal(self):
        import pymupdf
        page_img = _make_orange_watermark(_make_blank_page(w=400, h=300))
        img_bytes = _encode_png(page_img)
        doc = pymupdf.open()
        page = doc.new_page(width=400, height=300)
        page.insert_image(page.rect, stream=img_bytes)
        pdf_bytes = doc.tobytes()
        doc.close()
        result = remove_pdf_watermark(pdf_bytes, mode="auto")
        self.assertTrue(result["success"])
        self.assertGreater(result["pages_processed"], 0)

    def test_16_region_preset(self):
        page = _make_blank_page()
        page = _make_orange_watermark(page, x=280, y=250, w=100, h=30)
        config = {"region": {"preset": "bottom-right"}}
        result = remove_watermark(_encode_png(page), mode="region", config=config)
        self.assertTrue(result.success)

    def test_17_debug_mode(self):
        page = _make_blank_page()
        page = _make_orange_watermark(page)
        result = remove_watermark(_encode_png(page), mode="auto", debug=True)
        self.assertTrue(result.success)
        self.assertIn("mode", result.debug)
        self.assertIn("regions", result.debug)
        self.assertIn("mask_coverage", result.debug)

    def test_18_no_op_not_reported_as_success(self):
        page = _make_blank_page()
        img_bytes = _encode_png(page)
        result = remove_watermark(img_bytes, mode="auto")
        self.assertFalse(result.success)
        self.assertEqual(result.image_bytes, img_bytes)


if __name__ == "__main__":
    unittest.main()
