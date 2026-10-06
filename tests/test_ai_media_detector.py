import io
import os
import wave
import unittest

import numpy as np
from PIL import Image

from ai_media_detector import analyze_audio_bytes, analyze_image_bytes


class AIMediaDetectorTests(unittest.IsolatedAsyncioTestCase):
    async def test_image_pipeline_without_remote(self):
        os.environ.pop("HF_TOKEN", None)
        os.environ.pop("AI_DETECT_ONNX_IMAGE_MODEL", None)
        image = Image.new("RGB", (128, 128), (120, 140, 160))
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        result = await analyze_image_bytes(buf.getvalue(), "test.png")
        self.assertIn(result.decision, {"likely_ai", "likely_human", "uncertain"})
        self.assertGreaterEqual(result.ai_likelihood, 0)
        self.assertLessEqual(result.ai_likelihood, 100)

    async def test_audio_pipeline_without_remote(self):
        os.environ.pop("HF_TOKEN", None)
        os.environ.pop("AI_DETECT_ONNX_AUDIO_MODEL", None)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            t = np.arange(16000, dtype=np.float32) / 16000
            x = (0.1 * np.sin(2 * np.pi * 220 * t) * 32767).astype(np.int16)
            wf.writeframes(x.tobytes())
        result = await analyze_audio_bytes(buf.getvalue(), "test.wav")
        self.assertIn(result.decision, {"likely_ai", "likely_human", "uncertain"})
        self.assertGreaterEqual(result.ai_likelihood, 0)
        self.assertLessEqual(result.ai_likelihood, 100)


if __name__ == "__main__":
    unittest.main()
