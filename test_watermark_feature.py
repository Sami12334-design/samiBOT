"""Tests for the Telegram watermark workflow (watermark_feature.py).

Run from the repo root:  python -m unittest tests/test_watermark_feature.py
Telegram objects are replaced with small fakes; image/PDF processing is real.
"""
import ast
import asyncio
import os
import py_compile
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv2
import numpy as np
import pymupdf

import watermark_feature as wf
from watermark_removal import remove_watermark

ORANGE_BGR = (0, 128, 255)
RED_BGR = (35, 35, 230)  # wf._COLORS["red"] (230, 35, 35) in BGR order


def _png(img):
    return cv2.imencode(".png", img)[1].tobytes()


def make_image(banner_bgr=None):
    img = np.full((200, 300, 3), 120, np.uint8)
    if banner_bgr is not None:
        cv2.rectangle(img, (200, 165), (290, 190), banner_bgr, -1)
    return _png(img)


def make_pdf(text="CONFIDENTIAL"):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), text, fontsize=24)
    page.insert_text((72, 200), "Keep this body text", fontsize=12)
    data = doc.tobytes()
    doc.close()
    return data


def pdf_text(data):
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        return "".join(page.get_text() for page in doc)


class FakeMessage:
    """Doubles as the 'status' message returned by reply_text."""

    def __init__(self):
        self.texts, self.markups, self.docs = [], [], []

    async def reply_text(self, text, **kw):
        self.texts.append(text)
        self.markups.append(kw.get("reply_markup"))
        return self

    async def edit_text(self, text, **kw):
        self.texts.append(text)
        self.markups.append(kw.get("reply_markup"))
        return self

    async def reply_document(self, document=None, filename=None, caption=None):
        self.docs.append((document.getvalue(), filename, caption))

    def buttons(self):
        markup = next((m for m in reversed(self.markups) if m is not None), None)
        if markup is None:
            return []
        return [b.callback_data for row in markup.inline_keyboard for b in row]


def make_context(wm=None, state="wm_processing"):
    user_data = {"state": state}
    if wm is not None:
        user_data["wm"] = wm
    return SimpleNamespace(user_data=user_data)


def session(kind="image", mode="auto", **extra):
    wm = {"kind": kind, "file_id": "FILE", "filename": "x", "action": "remove", "remove_mode": mode}
    wm.update(extra)
    return wm


class WatermarkFeatureTests(unittest.IsolatedAsyncioTestCase):
    async def run_process(self, raw, wm, kind=None, state="wm_processing"):
        ctx = make_context(wm, state)
        msg = FakeMessage()

        async def fake_download(message, context, file_id):
            return raw

        with mock.patch.object(wf, "_download_file", fake_download):
            await wf._process_uploaded(msg, ctx, wm["file_id"], wm, kind or wm["kind"])
        return ctx, msg

    # 1. No undefined helper names / NameError in the automatic handler
    def test_no_undefined_remove_helpers_referenced(self):
        with open(wf.__file__, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and n.id.startswith("_remove_image")}
        self.assertTrue(names)
        for name in names:
            self.assertTrue(hasattr(wf, name), f"{name} is referenced but not defined")

    async def test_auto_mode_runs_without_nameerror(self):
        ctx, msg = await self.run_process(make_image(ORANGE_BGR), session(mode="auto"))
        self.assertEqual(len(msg.docs), 1, msg.texts)
        self.assertFalse(any("not defined" in t for t in msg.texts))
        self.assertIsNotNone(cv2.imdecode(np.frombuffer(msg.docs[0][0], np.uint8), cv2.IMREAD_COLOR))

    # 2. Wrapper return structure
    def test_auto_wrapper_return_structure(self):
        data, count, warnings = wf._remove_image_auto(make_image(ORANGE_BGR))
        self.assertIsInstance(data, bytes)
        self.assertIsInstance(count, int)
        self.assertGreaterEqual(count, 1)
        self.assertIsInstance(warnings, list)

    # 3. Region and color dispatch
    def test_region_dispatch_uses_region_wrapper(self):
        with mock.patch.object(wf, "_remove_image_region", return_value=(b"x", 1, [])) as m:
            wf._remove_image(b"raw", session(mode="region", region_preset="bottom-right"))
        m.assert_called_once_with(b"raw", {"preset": "bottom-right"})

    def test_color_dispatch_uses_color_wrapper_with_bgr(self):
        with mock.patch.object(wf, "_remove_image_color", return_value=(b"x", 1, [])) as m:
            wf._remove_image(b"raw", session(mode="color", target_color="red"))
        m.assert_called_once_with(b"raw", RED_BGR)

    async def test_region_mode_end_to_end(self):
        ctx, msg = await self.run_process(
            make_image(ORANGE_BGR), session(mode="region", region_preset="bottom-right"))
        self.assertEqual(len(msg.docs), 1, msg.texts)
        self.assertIsNone(ctx.user_data["state"])

    async def test_color_mode_end_to_end(self):
        ctx, msg = await self.run_process(
            make_image(RED_BGR), session(mode="color", target_color="red"))
        self.assertEqual(len(msg.docs), 1, msg.texts)

    async def test_region_and_color_without_selection_fail_safely(self):
        for mode in ("region", "color"):
            ctx, msg = await self.run_process(make_image(), session(mode=mode))
            self.assertEqual(msg.docs, [])
            self.assertEqual(ctx.user_data["state"], "wm_retry")

    # 4. Specific-text mode on images never calls an undefined function
    async def test_specific_text_on_image_gives_clear_message(self):
        ctx, msg = await self.run_process(
            make_image(), session(mode="specific", text="SAMPLE"))
        self.assertEqual(msg.docs, [])
        self.assertTrue(any("only works on PDFs" in t for t in msg.texts), msg.texts)
        self.assertFalse(any("not defined" in t for t in msg.texts))

    async def test_specific_button_on_image_does_not_ask_for_text(self):
        wm = session(mode=None)
        ctx = make_context(wm, "wm_choose_remove_mode")
        msg = FakeMessage()
        update = SimpleNamespace(callback_query=SimpleNamespace(message=msg))
        handled = await wf.watermark_callback(update, ctx, "wm_remove_mode|specific")
        self.assertTrue(handled)
        self.assertEqual(ctx.user_data["state"], "wm_choose_remove_mode")
        self.assertIn("PDFs with selectable text", msg.texts[-1])

    # 5. PDF workflow is preserved and not hijacked by image modes
    async def test_pdf_specific_text_still_works(self):
        wm = session(kind="pdf", mode="specific", text="CONFIDENTIAL")
        ctx, msg = await self.run_process(make_pdf(), wm)
        self.assertEqual(len(msg.docs), 1, msg.texts)
        out = pdf_text(msg.docs[0][0])
        self.assertNotIn("CONFIDENTIAL", out)
        self.assertIn("Keep this body text", out)
        self.assertIsNone(ctx.user_data["state"])

    async def test_pdf_auto_region_color_are_rejected_not_misrouted(self):
        for mode in ("auto", "region", "color"):
            ctx, msg = await self.run_process(make_pdf(), session(kind="pdf", mode=mode))
            self.assertEqual(msg.docs, [], mode)
            self.assertEqual(ctx.user_data["state"], "wm_retry", mode)

    async def test_region_button_on_pdf_is_declined(self):
        wm = session(kind="pdf", mode=None)
        ctx = make_context(wm, "wm_choose_remove_mode")
        msg = FakeMessage()
        update = SimpleNamespace(callback_query=SimpleNamespace(message=msg))
        await wf.watermark_callback(update, ctx, "wm_remove_mode|region")
        self.assertIn("images only", msg.texts[-1])
        self.assertEqual(ctx.user_data["state"], "wm_choose_remove_mode")

    # 6. A processing exception keeps a retryable session
    async def test_processing_exception_keeps_session_and_offers_retry(self):
        wm = session(mode="auto")
        with mock.patch.object(wf, "_remove_image", side_effect=RuntimeError("boom")):
            ctx, msg = await self.run_process(make_image(ORANGE_BGR), wm)
        self.assertEqual(ctx.user_data["state"], "wm_retry")
        self.assertIs(ctx.user_data["wm"], wm)
        self.assertEqual(wm["file_id"], "FILE")
        self.assertIn("wm_retry", msg.buttons())
        self.assertIn("wm_cancel", msg.buttons())
        self.assertIn("watermark", msg.buttons())  # start over

    async def test_retry_button_reprocesses_and_cleans_up_on_success(self):
        wm = session(mode="auto")
        with mock.patch.object(wf, "_remove_image", side_effect=RuntimeError("boom")):
            ctx, _ = await self.run_process(make_image(ORANGE_BGR), wm)
        msg = FakeMessage()
        update = SimpleNamespace(callback_query=SimpleNamespace(message=msg))

        async def fake_download(message, context, file_id):
            return make_image(ORANGE_BGR)

        with mock.patch.object(wf, "_download_file", fake_download):
            await wf.watermark_callback(update, ctx, "wm_retry")
        self.assertEqual(len(msg.docs), 1, msg.texts)
        self.assertNotIn("wm", ctx.user_data)
        self.assertIsNone(ctx.user_data["state"])

    async def test_change_method_shows_menu_and_keeps_file(self):
        wm = session(mode="auto", _attempts=1)
        ctx = make_context(wm, "wm_retry")
        msg = FakeMessage()
        update = SimpleNamespace(callback_query=SimpleNamespace(message=msg))
        await wf.watermark_callback(update, ctx, "wm_change_method")
        self.assertEqual(ctx.user_data["state"], "wm_choose_remove_mode")
        self.assertIn("wm_remove_mode|region", msg.buttons())
        self.assertEqual(wm["file_id"], "FILE")

    async def test_session_cleared_after_max_attempts(self):
        wm = session(mode="auto", _attempts=wf._MAX_ATTEMPTS - 1)
        with mock.patch.object(wf, "_remove_image", side_effect=RuntimeError("boom")):
            ctx, msg = await self.run_process(make_image(), wm)
        self.assertNotIn("wm", ctx.user_data)
        self.assertIsNone(ctx.user_data["state"])
        self.assertIn("watermark", msg.buttons())

    # 7. Success cleans up
    async def test_success_clears_state_and_session(self):
        ctx, msg = await self.run_process(make_image(ORANGE_BGR), session(mode="auto"))
        self.assertEqual(len(msg.docs), 1)
        self.assertIsNone(ctx.user_data["state"])
        self.assertNotIn("wm", ctx.user_data)

    async def test_restarted_session_is_not_clobbered_by_late_failure(self):
        wm = session(mode="auto")
        ctx = make_context(wm)
        msg = FakeMessage()

        async def fake_download(message, context, file_id):
            context.user_data["wm"] = {"kind": "image"}  # user pressed Start over meanwhile
            context.user_data["state"] = "wm_upload_first"
            raise RuntimeError("late failure")

        with mock.patch.object(wf, "_download_file", fake_download):
            await wf._process_uploaded(msg, ctx, "FILE", wm, "image")
        self.assertEqual(ctx.user_data["state"], "wm_upload_first")
        self.assertEqual(ctx.user_data["wm"], {"kind": "image"})

    # 8. No watermark found: failure status, original bytes, no false success
    def test_core_returns_original_bytes_when_nothing_found(self):
        raw = make_image()
        result = remove_watermark(raw, mode="auto")
        self.assertFalse(result.success)
        self.assertEqual(result.image_bytes, raw)

    async def test_no_watermark_is_reported_as_failure_not_success(self):
        ctx, msg = await self.run_process(make_image(), session(mode="auto"))
        self.assertEqual(msg.docs, [])
        self.assertFalse(any(t.startswith("✅") for t in msg.texts), msg.texts)
        self.assertTrue(any("No credible watermark" in t for t in msg.texts), msg.texts)

    # 9. Invalid inputs fail safely
    async def test_garbage_image_bytes_do_not_crash(self):
        ctx, msg = await self.run_process(b"not an image", session(mode="auto"))
        self.assertEqual(msg.docs, [])
        self.assertTrue(any(t.startswith("❌") for t in msg.texts), msg.texts)
        self.assertEqual(ctx.user_data["state"], "wm_retry")

    async def test_garbage_pdf_bytes_do_not_crash(self):
        ctx, msg = await self.run_process(b"%PDF-broken", session(kind="pdf", mode="specific", text="X"))
        self.assertEqual(msg.docs, [])
        self.assertTrue(any(t.startswith("❌") for t in msg.texts), msg.texts)

    async def test_rejected_upload_asks_for_a_new_file(self):
        wm = session(mode="auto")
        ctx = make_context(wm)
        msg = FakeMessage()

        async def too_large(message, context, file_id):
            raise wf.WatermarkError("File is too large. Please send a file smaller than 18 MB.")

        with mock.patch.object(wf, "_download_file", too_large):
            await wf._process_uploaded(msg, ctx, "FILE", wm, "image")
        self.assertEqual(ctx.user_data["state"], "wm_awaiting_file")
        self.assertNotIn("file_id", wm)
        self.assertEqual(wm["remove_mode"], "auto")  # settings kept

    async def test_invalid_and_stale_callbacks_are_handled(self):
        msg = FakeMessage()
        update = SimpleNamespace(callback_query=SimpleNamespace(message=msg))
        ctx = make_context(session(mode=None), "wm_choose_remove_mode")
        await wf.watermark_callback(update, ctx, "wm_remove_mode|bogus")
        self.assertIn("Invalid option", msg.texts[-1])
        # no session at all -> genuine expiry message
        for data in ("wm_remove_mode|auto", "wm_region|bottom-right", "wm_rmcolor|red", "wm_retry", "wm_change_method"):
            empty = make_context(None, None)
            m = FakeMessage()
            await wf.watermark_callback(SimpleNamespace(callback_query=SimpleNamespace(message=m)), empty, data)
            self.assertIn("Session expired", m.texts[-1], data)
        ctx2 = make_context(session(mode="region"), "wm_choose_region")
        m = FakeMessage()
        await wf.watermark_callback(SimpleNamespace(callback_query=SimpleNamespace(message=m)), ctx2, "wm_region|nowhere")
        self.assertIn("Invalid choice", m.texts[-1])

    async def test_region_and_color_buttons_start_processing(self):
        for data, key, value in (("wm_region|bottom-right", "region_preset", "bottom-right"),
                                 ("wm_rmcolor|red", "target_color", "red")):
            wm = session(mode="region" if "region" in data else "color")
            ctx = make_context(wm, "wm_choose_region")
            msg = FakeMessage()
            with mock.patch.object(wf, "_process_uploaded", new=mock.AsyncMock()) as proc:
                await wf.watermark_callback(SimpleNamespace(callback_query=SimpleNamespace(message=msg)), ctx, data)
            proc.assert_awaited_once()
            self.assertEqual(wm[key], value)

    # 10. Startup / dependencies
    def test_module_and_bot_compile_and_imports_resolve(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        py_compile.compile(os.path.join(root, "watermark_feature.py"), doraise=True)
        py_compile.compile(os.path.join(root, "bot.py"), doraise=True)
        for name in ("watermark_start", "watermark_callback", "watermark_text_input",
                     "watermark_photo_input", "watermark_document_input"):
            self.assertTrue(callable(getattr(wf, name)), name)


if __name__ == "__main__":
    unittest.main()
