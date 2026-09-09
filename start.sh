#!/usr/bin/env bash
set -e

# Build is handled by render-build.sh.
# This script is ONLY used to start the bot and Flask server.
echo "Starting bot..."
exec python3 bot.py
