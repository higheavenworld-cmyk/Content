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
    "pexels_key": "",                    # optional, free account -> real video clips
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
