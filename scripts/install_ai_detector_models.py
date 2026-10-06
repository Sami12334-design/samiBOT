#!/usr/bin/env python3
"""Install the small local AI-image ONNX detector used by SamiBOT.

The default image model is an MIT-licensed 11.8M-parameter distilled ViT.
Only the selected ONNX file is downloaded; no model weights are committed to
the Git repository.

Usage:
    python scripts/install_ai_detector_models.py
    python scripts/install_ai_detector_models.py --dest /opt/samibot-models
    python scripts/install_ai_detector_models.py --audio
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from urllib.request import Request, urlopen

IMAGE_REPO = "onnx-community/ai-image-detect-distilled-ONNX"
IMAGE_REVISION = "7f067e23521eeb6d6525221af82c613fb746aaff"
IMAGE_FILE = "onnx/model_int8.onnx"
IMAGE_SHA256 = "7273cb9cd81e17eae04771010d2199ba6ae34ea2a75a275518c0bc4a2c26ffd2"
IMAGE_URL = (
    f"https://huggingface.co/{IMAGE_REPO}/resolve/{IMAGE_REVISION}/"
    f"{IMAGE_FILE}?download=true"
)

AUDIO_REPO = "pranjal-pravesh/wav2vec2-large-xlsr-deepfake-audio-classification"
AUDIO_FILE = "model_int8.onnx"
# This optional model is about 355 MB and therefore is NOT installed by default.
AUDIO_SHA256 = "0"  # Fill only after independently verifying the upstream file.


def download(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_suffix(destination.suffix + ".part")
    req = Request(url, headers={"User-Agent": "SamiBOT-ai-detector-installer/1.0"})
    with urlopen(req, timeout=120) as response, tmp.open("wb") as out:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
    tmp.replace(destination)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def install_image(dest: Path) -> Path:
    model = dest / "image" / "model_int8.onnx"
    if model.exists() and sha256(model) == IMAGE_SHA256:
        return model

    print(f"Downloading {IMAGE_REPO}:{IMAGE_REVISION} ({IMAGE_FILE})")
    download(IMAGE_URL, model)
    actual = sha256(model)
    if actual != IMAGE_SHA256:
        model.unlink(missing_ok=True)
        raise RuntimeError(
            f"Image model SHA256 mismatch: expected {IMAGE_SHA256}, got {actual}"
        )

    manifest = {
        "repository": IMAGE_REPO,
        "revision": IMAGE_REVISION,
        "file": IMAGE_FILE,
        "sha256": IMAGE_SHA256,
        "license": "MIT",
        "input": {"size": 224, "mean": [0.5, 0.5, 0.5], "std": [0.5, 0.5, 0.5]},
        "labels": ["fake", "real"],
        "note": "Model reports fake/AI-like probability, not proof of origin.",
    }
    (model.parent / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return model


def install_audio(dest: Path) -> Path:
    if AUDIO_SHA256 == "0":
        raise RuntimeError(
            "Audio model is intentionally not auto-installed yet: upstream size is "
            "about 355 MB and its exact file hash must be pinned before unattended "
            "deployment. Keep HF remote inference enabled, or pin/verify the model "
            "yourself before using this option."
        )
    model = dest / "audio" / AUDIO_FILE
    download(
        f"https://huggingface.co/{AUDIO_REPO}/resolve/main/{AUDIO_FILE}?download=true",
        model,
    )
    if sha256(model) != AUDIO_SHA256:
        model.unlink(missing_ok=True)
        raise RuntimeError("Audio model SHA256 mismatch")
    return model


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dest",
        default=os.getenv("AI_DETECT_MODEL_DIR", "./models/ai-detector"),
        help="Directory where model files are stored.",
    )
    parser.add_argument(
        "--audio",
        action="store_true",
        help="Attempt optional local audio model installation.",
    )
    args = parser.parse_args()

    dest = Path(args.dest).expanduser().resolve()
    image = install_image(dest)

    print()
    print("Image detector installed:")
    print(f"  {image}")
    print("Set these environment variables:")
    print(f"  AI_DETECT_ONNX_IMAGE_MODEL={image}")
    print("  AI_DETECT_ONNX_IMAGE_LABELS=fake,real")
    print("  AI_DETECT_ONNX_IMAGE_SIZE=224")
    print("  AI_DETECT_ONNX_IMAGE_MEAN=0.5,0.5,0.5")
    print("  AI_DETECT_ONNX_IMAGE_STD=0.5,0.5,0.5")
    print("  AI_DETECT_ONNX_THREADS=1")

    if args.audio:
        print()
        print(install_audio(dest))


if __name__ == "__main__":
    main()
