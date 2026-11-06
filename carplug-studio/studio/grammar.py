"""Spelling & grammar check for the script (LanguageTool, free, no account).

Only safe fixes are applied (typos, grammar, capitalisation, punctuation). Model names, numbers and
brand words are protected. If the service is not reachable, the script stays as it is.
"""
from __future__ import annotations

import re

import requests

SAFE = {"TYPOS", "GRAMMAR", "CASING", "PUNCTUATION", "CONFUSED_WORDS", "COMPOUNDING", "TYPOGRAPHY"}
PROTECT = re.compile(r"\d|[a-zäöü][A-ZÄÖÜ]|^[A-ZÄÖÜ]{2,}$")  # numbers, xDrive-style names, BMW-style names


def correct_texts(texts: list[str], protect_words: set[str] | None = None, log=None) -> list[str]:
    if not texts:
        return texts
    canon = {w.lower(): w for w in (protect_words or set())}  # correct spelling of names: "bmw" -> "BMW"
    sep = "\n\n"
    joined = sep.join(texts)
    try:
        r = requests.post("https://api.languagetool.org/v2/check", timeout=20,
                          data={"text": joined, "language": "de-DE"})
        r.raise_for_status()
        matches = r.json().get("matches", [])
    except Exception:
        return texts  # no internet / limit reached: keep the script unchanged
    fixes = []
    for m in matches:
        reps = m.get("replacements") or []
        cat = (m.get("rule", {}).get("category", {}) or {}).get("id", "")
        if not reps or cat not in SAFE:
            continue
        off, ln = m["offset"], m["length"]
        wrong = joined[off:off + ln]
        if "\n" in wrong or wrong.lower() in canon or wrong.lower() in ("carplug", "news"):
            continue  # names are handled by normalize_names (own list), never "corrected" by the service
        if PROTECT.search(wrong) and cat != "PUNCTUATION":
            continue
        fixes.append((off, ln, reps[0]["value"]))
    out = joined
    for off, ln, value in sorted(fixes, reverse=True):
        out = out[:off] + value + out[off + ln:]
    parts = out.split(sep)
    if len(parts) != len(texts):
        return texts
    if log and fixes:
        log(f"Rechtschreibung/Grammatik: {len(fixes)} Korrektur(en) im Script.")
    return parts


UNITS = [(r"\bps\b", "PS"), (r"\bkw\b", "kW"), (r"\bkwh\b", "kWh"), (r"\bkm/h\b", "km/h"), (r"\bnm\b", "Nm"),
         (r"\beuro\b", "Euro"), (r"\bcarplug\b", "CarPlug")]


def normalize_names(text: str, names: set[str]) -> str:
    """Write brands, models and units correctly even if the source was all lowercase (auto-captions)."""
    for name in sorted(names, key=len, reverse=True):
        if len(name) >= 2 and (name.isupper() or any(c.isupper() for c in name[1:]) or name[0].isupper()):
            text = re.sub(rf"(?<![\w-]){re.escape(name)}(?![\w-])", name, text, flags=re.I)
    for pat, rep in UNITS:
        text = re.sub(pat, rep, text, flags=re.I)
    # 500000 -> 500.000 (German thousands separator; years like 2026 stay as they are)
    text = re.sub(r"(?<![\d.,])(\d{5,})(?![\d.,]\d)", lambda m: f"{int(m.group(1)):,}".replace(",", "."), text)
    return text
