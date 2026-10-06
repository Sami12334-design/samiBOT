"""
Optional local ONNX inference layer.

No model weights are committed to the repository. Set AI_DETECT_ONNX_IMAGE_MODEL
or AI_DETECT_ONNX_AUDIO_MODEL to a local .onnx file that you have permission
to use. The adapter is intentionally conservative: if the model contract is
not recognizable, it returns None and the waterfall continues.

Image models:
  - rank-4 input [N,C,H,W] or [N,H,W,C]
  - resize is controlled by AI_DETECT_ONNX_IMAGE_SIZE (default 224)
  - normalization is controlled by AI_DETECT_ONNX_MEAN / _STD
  - output is interpreted as binary logits/probabilities; label order can be
    configured with AI_DETECT_ONNX_LABELS, e.g. "real,ai"

Audio models:
  - rank-2 input [N,T] is supported for raw float32 waveform
  - audio is resampled to 16 kHz before inference
  - label order via AI_DETECT_ONNX_LABELS
"""

from __future__ import annotations

import io
import os
import wave
from functools import lru_cache
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image

try:
    import onnxruntime as ort
except Exception:
    ort = None

try:
    from scipy import signal as scipy_signal
except Exception:
    scipy_signal = None

try:
    import imageio_ffmpeg
except Exception:
    imageio_ffmpeg = None


def _labels():
    raw = os.getenv("AI_DETECT_ONNX_LABELS", "real,ai")
    return [x.strip().casefold() for x in raw.split(",") if x.strip()]


def _ai_index(labels):
    for i, label in enumerate(labels):
        if any(k in label for k in ("ai", "fake", "deepfake", "synthetic", "generated", "spoof")):
            return i
    return 1 if len(labels) == 2 else None


def _softmax(x):
    x = np.asarray(x, dtype=np.float32).reshape(-1)
    x = x - np.max(x)
    exp = np.exp(x)
    return exp / max(float(exp.sum()), 1e-9)


@lru_cache(maxsize=4)
def _session(path):
    if ort is None:
        return None
    if not path or not Path(path).is_file():
        return None
    options = ort.SessionOptions()
    options.intra_op_num_threads = max(1, int(os.getenv("AI_DETECT_ONNX_THREADS", "1")))
    options.inter_op_num_threads = 1
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    return ort.InferenceSession(
        path,
        sess_options=options,
        providers=["CPUExecutionProvider"],
    )


def _output_to_ai_score(output, labels):
    arr = np.asarray(output)
    if arr.size == 1:
        value = float(arr.reshape(-1)[0])
        # A single sigmoid/logit output is assumed to mean the AI class.
        if value < 0 or value > 1:
            value = 1.0 / (1.0 + np.exp(-np.clip(value, -30, 30)))
        return max(0.0, min(100.0, value * 100.0))

    values = arr.reshape(-1).astype(np.float32)
    if np.all(values >= 0) and np.isclose(float(values.sum()), 1.0, atol=1e-3):
        probs = values
    else:
        probs = _softmax(values)
    idx = _ai_index(labels)
    if idx is None or idx >= len(probs):
        return None
    return float(probs[idx] * 100.0)


def _image_input(data, shape):
    with Image.open(io.BytesIO(data)) as im:
        im = im.convert("RGB")
        size = int(os.getenv("AI_DETECT_ONNX_IMAGE_SIZE", "224"))
        im = im.resize((size, size), Image.Resampling.BICUBIC)
        x = np.asarray(im, dtype=np.float32) / 255.0

    mean = np.asarray(
        [float(v) for v in os.getenv(
            "AI_DETECT_ONNX_IMAGE_MEAN", "0.485,0.456,0.406"
        ).split(",")],
        dtype=np.float32,
    )
    std = np.asarray(
        [float(v) for v in os.getenv(
            "AI_DETECT_ONNX_IMAGE_STD", "0.229,0.224,0.225"
        ).split(",")],
        dtype=np.float32,
    )
    x = (x - mean) / std

    dims = [d for d in shape if isinstance(d, int) and d > 0]
    if len(shape) != 4:
        raise ValueError("image ONNX model must have a rank-4 input")

    # NCHW is the common Transformers/vision convention.
    if shape[1] == 3 or (shape[1] is None and len(dims) >= 3):
        x = np.transpose(x, (2, 0, 1))[None, ...]
    else:
        x = x[None, ...]
    return x.astype(np.float32)


def _wav_to_float32(data):
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        wav = data
    else:
        if imageio_ffmpeg is None:
            return None
        proc = __import__("subprocess").run(
            [
                imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner",
                "-loglevel", "error", "-i", "pipe:0", "-ac", "1",
                "-ar", "16000", "-f", "wav", "pipe:1",
            ],
            input=data,
            stdout=__import__("subprocess").PIPE,
            stderr=__import__("subprocess").PIPE,
            timeout=20,
            check=False,
        )
        wav = proc.stdout if proc.returncode == 0 else b""
    if not wav.startswith(b"RIFF"):
        return None

    with wave.open(io.BytesIO(wav), "rb") as wf:
        sr = wf.getframerate()
        channels = wf.getnchannels()
        width = wf.getsampwidth()
        frames = wf.readframes(min(wf.getnframes(), sr * 30))
    if width != 2:
        return None
    x = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        x = x.reshape(-1, channels).mean(axis=1)
    if sr != 16000 and scipy_signal is not None:
        n = int(len(x) * 16000 / sr)
        x = scipy_signal.resample(x, n).astype(np.float32)
    return x


def local_onnx_image(data) -> Optional[float]:
    path = os.getenv("AI_DETECT_ONNX_IMAGE_MODEL", "").strip()
    session = _session(path)
    if session is None:
        return None
    try:
        inp = session.get_inputs()[0]
        shape = tuple(
            int(v) if isinstance(v, (int, np.integer)) else None
            for v in inp.shape
        )
        x = _image_input(data, shape)
        output = session.run(None, {inp.name: x})[0]
        return _output_to_ai_score(output, _labels())
    except Exception:
        return None


def local_onnx_audio(data) -> Optional[float]:
    path = os.getenv("AI_DETECT_ONNX_AUDIO_MODEL", "").strip()
    session = _session(path)
    if session is None:
        return None
    try:
        x = _wav_to_float32(data)
        if x is None:
            return None
        inp = session.get_inputs()[0]
        shape = tuple(
            int(v) if isinstance(v, (int, np.integer)) else None
            for v in inp.shape
        )
        if len(shape) != 2:
            # Mel-spectrogram and feature-based models need model-specific
            # preprocessing; do not guess it.
            return None
        output = session.run(None, {inp.name: x[None, :]})[0]
        return _output_to_ai_score(output, _labels())
    except Exception:
        return None


def clear_onnx_cache():
    _session.cache_clear()
