#!/bin/bash
# Start the Bufo Discovery Bot if it's not already running.
set -euo pipefail

cd "$(dirname "$(dirname "$(readlink -f "$0")")")"

if pgrep -f "bufo-discovery-bot.py" > /dev/null 2>&1; then
    echo "$(date): Bufo Discovery Bot already running." >> ~/.bufo-discovery.log
else
    echo "$(date): Starting Bufo Discovery Bot..." >> ~/.bufo-discovery.log
    caffeinate -i .venv/bin/python3 scripts/bufo-discovery-bot.py >> ~/.bufo-discovery.log 2>&1 &
    echo "$(date): Started (PID $!)." >> ~/.bufo-discovery.log
fi
