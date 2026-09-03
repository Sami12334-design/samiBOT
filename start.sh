#!/usr/bin/env bash
set -e
pip install -r requirements.txt
# Deno is used by yt-dlp for YouTube JavaScript challenges.
if [ ! -x .deno/bin/deno ]; then
  curl -fsSL https://deno.land/install.sh | DENO_INSTALL="$PWD/.deno" sh
fi
