#!/usr/bin/env bash
set -e

# Install Python dependencies
pip install -r requirements.txt

# Install DejaVu fonts for Unicode PDF support
# This allows Text → PDF to correctly render Amharic/Ethiopic text.
apt-get update
apt-get install -y fonts-dejavu

# yt-dlp currently needs a supported JavaScript runtime
# for YouTube's JS challenges.
# Keep Deno inside the Render service so the runtime
# is available to bot.py.
if [ ! -x .deno/bin/deno ]; then
  curl -fsSL https://deno.land/install.sh | DENO_INSTALL="$PWD/.deno" sh
fi
