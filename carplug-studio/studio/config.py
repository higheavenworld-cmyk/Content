"""Branding, paths and user settings for CarPlug Studio."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
FONTS = ASSETS / "fonts"
MUSIC_DIR = ASSETS / "music"
OUTPUT_DIR = ROOT / "ausgabe"
WORK_DIR = ROOT / ".arbeit"
VOICES_DIR = ROOT / "stimmen"
SETTINGS_FILE = ROOT / "einstellungen.json"

# --- CarPlug branding (taken from the thumbnail / logo) ---------------------
W, H = 1080, 1920
FPS = 30
BLACK = (12, 12, 12)
WHITE = (240, 240, 240)
RED = (224, 20, 20)
LOGO_RED = (203, 45, 34)
GREY = (190, 190, 190)
FONT_HEADLINE = FONTS / "DejaVuSansCondensed-BoldOblique.ttf"
FONT_BOLD = FONTS / "DejaVuSansCondensed-Bold.ttf"
LOGO_FILE = ASSETS / "logo_source.jpg"
BRAND_URL = "CARPLUG.ONLINE"
BRAND_HASHTAGS = ["#carplug", "#autonews", "#auto", "#cars"]

# Video formats (width, height). "Eigenes Format" lets you type any size.
FORMATS = {
    "9:16 Hochformat – TikTok, Reels, Shorts (1080×1920)": (1080, 1920),
    "4:5 Instagram/Facebook-Feed (1080×1350)": (1080, 1350),
    "1:1 Quadratisch (1080×1080)": (1080, 1080),
    "3:4 Hochformat (1080×1440)": (1080, 1440),
    "16:9 Querformat – YouTube (1920×1080)": (1920, 1080),
    "16:9 Querformat 4K (3840×2160)": (3840, 2160),
}
CUSTOM_FORMAT = "Eigenes Format (Breite × Höhe frei wählen)"

# Target lengths in seconds. From 1 minute on, the video is guaranteed to be a bit LONGER
# than the target (TikTok Creator Rewards only pays for videos over 1 minute).
LENGTHS = {
    "15 Sekunden": 15, "30 Sekunden": 30, "45 Sekunden": 45,
    "1 Minute (TikTok-Bonus ✓)": 60, "1,5 Minuten": 90, "2 Minuten": 120, "3 Minuten": 180,
    "5 Minuten": 300, "7 Minuten": 420, "10 Minuten": 600,
}
MIN_SAFETY_SECONDS = 3.0   # 1 min target -> video is at least 1:03
WORDS_PER_SECOND = 2.4     # spoken German at the default speed (used for planning)

# Free Microsoft neural voices (no account needed)
VOICES = {
    "Conrad (männlich, News)": "de-DE-ConradNeural",
    "Killian (männlich, jung)": "de-DE-KillianNeural",
    "Florian (männlich, multilingual)": "de-DE-FlorianMultilingualNeural",
    "Seraphina (weiblich, multilingual)": "de-DE-SeraphinaMultilingualNeural",
    "Katja (weiblich, News)": "de-DE-KatjaNeural",
    "Amala (weiblich, jung)": "de-DE-AmalaNeural",
}

DEFAULT_SETTINGS = {
    "ollama_model": "qwen2.5:7b",       # optional local AI for scripts
    "ollama_url": "http://localhost:11434",
    "youtube_all": False,                # also non-CC YouTube videos as footage (copyright risk)
    "cookies_browser": "",               # browser with your Instagram/TikTok login (only if a link needs it)
    "music_volume": 0.18,
    "speech_rate": "+8%",
}


def load_settings() -> dict:
    data = dict(DEFAULT_SETTINGS)
    try:
        data.update(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    return data


def save_settings(data: dict) -> None:
    SETTINGS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


for _d in (OUTPUT_DIR, WORK_DIR, VOICES_DIR, MUSIC_DIR):
    _d.mkdir(parents=True, exist_ok=True)
