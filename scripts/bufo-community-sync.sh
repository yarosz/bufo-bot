#!/bin/bash
# Bufo community sync — pull new bufo from upstream, upload to Slack, announce.
set -euo pipefail

cd "$(dirname "$(dirname "$(readlink -f "$0")")")"
LOG="$HOME/.bufo-sync.log"

echo "=== $(date) ===" >> "$LOG"
.venv/bin/python3 bufo-rollout.py sync --auto --live >> "$LOG" 2>&1
echo "" >> "$LOG"
