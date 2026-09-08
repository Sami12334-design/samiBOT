#!/usr/bin/env bash
set -euo pipefail

# 1. Upgrade pip
python -m pip install --upgrade pip

# 2. Install system fonts and eSpeak for local Text-to-Speech and PDF generation
if command -v apt-get >/dev/null 2>&1; then
  apt-get update -y
  apt-get install -y fonts-noto-core fonts-noto-cjk fonts-noto-extra espeak-ng
fi

# 3. Install Python dependencies
python -m pip install -r requirements.txt

# 4. Install Deno for yt-dlp's JavaScript-assisted extractors
if [ ! -x .deno/bin/deno ]; then
  curl -fsSL https://deno.land/install.sh | DENO_INSTALL="$PWD/.deno" sh
fi

# 5. Small local English speech-recognition model for Vosk (No API key required)
VOSK_DIR="/opt/vosk-model-small-en-us-0.15"
if [ ! -d "$VOSK_DIR" ]; then
  mkdir -p /tmp/vosk
  curl -L --fail --retry 3 -o /tmp/vosk/model.zip https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip
  unzip -q /tmp/vosk/model.zip -d /tmp/vosk
  mkdir -p "$(dirname "$VOSK_DIR")"
  cp -a /tmp/vosk/vosk-model-small-en-us-0.15 "$VOSK_DIR"
fi

echo "Build completed successfully!"
