#!/usr/bin/env bash
set -euo pipefail

python -m pip install --upgrade pip

# Unicode fonts for multilingual PDF output.
# These are used locally; no PDF/translation API is involved.
if command -v apt-get >/dev/null 2>&1; then
  apt-get update -y || true
  apt-get install -y fonts-noto-core fonts-noto-cjk fonts-noto-extra espeak espeak-ng || true
fi

python -m pip install -r requirements.txt

# Install Deno for yt-dlp's JavaScript-assisted extractors.
if [ ! -x .deno/bin/deno ]; then
  curl -fsSL https://deno.land/install.sh | DENO_INSTALL="$PWD/.deno" sh
fi

# Small local English speech-recognition model for Vosk.
# No API key/cookie is required.
VOSK_DIR="/opt/vosk-model-small-en-us-0.15"
if [ ! -d "$VOSK_DIR" ]; then
  mkdir -p /tmp/vosk
  curl -L --fail --retry 3 -o /tmp/vosk/model.zip https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip
  unzip -q /tmp/vosk/model.zip -d /tmp/vosk
  mkdir -p "$(dirname "$VOSK_DIR")"
  cp -a /tmp/vosk/vosk-model-small-en-us-0.15 "$VOSK_DIR"
fi
