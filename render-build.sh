#!/usr/bin/env bash
set -euo pipefail

python -m pip install --upgrade pip
python -m pip install -r requirements.txt

# Install Deno for yt-dlp's JavaScript-assisted extractors.
if [ ! -x .deno/bin/deno ]; then
  curl -fsSL https://deno.land/install.sh | DENO_INSTALL="$PWD/.deno" sh
fi

# Vosk model (moved to /tmp to avoid read-only file system errors)
VOSK_DIR="/tmp/vosk-model-small-en-us-0.15"
if [ ! -d "$VOSK_DIR" ]; then
  mkdir -p /tmp/vosk
  curl -L --fail --retry 3 -o /tmp/vosk/model.zip https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip
  unzip -q /tmp/vosk/model.zip -d /tmp/vosk
  cp -a /tmp/vosk/vosk-model-small-en-us-0.15 "$VOSK_DIR"
fi

echo "Build completed successfully!"
