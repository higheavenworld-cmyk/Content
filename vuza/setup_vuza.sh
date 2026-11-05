#!/usr/bin/env bash
# Clone, install and start VUZA (AI Video Creator + Pinterest Video Scraper).
# Usage: bash vuza/setup_vuza.sh [target_dir]
set -euo pipefail

TARGET="${1:-VUZA-Free-AI-Video-Creator-and-Pinterest-Video-Scraper}"
REPO="https://github.com/AliRash3ed/VUZA-Free-AI-Video-Creator-and-Pinterest-Video-Scraper.git"

# 1. Clone the repo
[ -d "$TARGET/.git" ] || git clone "$REPO" "$TARGET"
cd "$TARGET"

# 2. Install Python dependencies (in a virtualenv)
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 3. Playwright browser (needed for Pinterest scraping).
# If a Chromium is already preinstalled (e.g. Claude Code cloud containers ship one
# in /opt/pw-browsers matching the global Node Playwright), pin the Python package
# to that version instead of downloading another browser.
if [ -d /opt/pw-browsers ] && V=$(node -e "console.log(require('playwright/package.json').version)" 2>/dev/null); then
  .venv/bin/pip install "playwright==$V"
else
  .venv/bin/playwright install chromium
fi

# 4. Start VUZA -> http://localhost:8000
exec .venv/bin/python app.py
