#!/usr/bin/env bash
set -e
pip install -r requirements.txt
# yt-dlp currently needs a supported JavaScript runtime for YouTube's JS challenges.
# Keep Deno inside the Render service so the runtime is available to bot.py.
if [ ! -x .deno/bin/deno ]; then
  curl -fsSL https://deno.land/install.sh | DENO_INSTALL="$PWD/.deno" sh
fi
