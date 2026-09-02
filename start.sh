#!/bin/sh
set -eu
node /opt/bgutil/server/build/main.js --port 4416 >/tmp/bgutil.log 2>&1 &
export YTDL_POT_PROVIDER_URL="${YTDL_POT_PROVIDER_URL:-http://127.0.0.1:4416}"
exec python bot.py
