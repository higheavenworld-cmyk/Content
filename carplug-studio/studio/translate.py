"""Everything ends up in German – whatever language the source is in.

Order: local AI (Ollama, if running) → Google Translate (free, no account) → MyMemory (free, no account).
Texts are translated in chunks; line breaks are kept, so timed transcripts stay aligned.
"""
from __future__ import annotations

import re
import time
from functools import lru_cache

import requests

from .config import load_settings

DE = set("der die das und ist nicht ein eine einen mit auf für von zu den dem des sich auch es im wird werden "
         "hat haben noch nach bei aus wie über aber oder wenn man sind war sie er wir ich".split())
EN = set("the and is are not a an with on for of to it this that was be by from at as have has will "
         "you they we he she or but if which can its".split())


def language(text: str) -> str:
    """'de', 'en' or 'other' – a quick guess from common words."""
    words = re.findall(r"[a-zA-ZäöüÄÖÜß]+", text.lower())[:3000]
    if len(words) < 3:
        return "de"
    de = sum(w in DE for w in words) / len(words)
    en = sum(w in EN for w in words) / len(words)
    if de >= 0.06 and de >= en:
        return "de"
    if en >= 0.06:
        return "en"
    return "de" if re.search(r"[äöüß]", text.lower()) else "other"


def is_german(text: str) -> bool:
    return language(text) == "de"


class TranslationError(RuntimeError):
    pass


def to_german(text: str, log=None) -> str:
    return translate(text, "de", log)


def to_english(text: str) -> str:
    try:
        return translate(text, "en")
    except TranslationError:
        return text


def translate(text: str, target: str, log=None) -> str:
    if not text.strip():
        return text
    if target == "de" and is_german(text):
        return text
    return _cached(text, target)


@lru_cache(maxsize=2048)
def _cached(text: str, target: str) -> str:
    errors = []
    for name, fn in (("lokale KI", _ollama), ("Google", _google), ("MyMemory", _mymemory)):
        try:
            out = _chunked(text, target, fn)
            if out.strip():
                return out
        except Exception as e:  # try the next service
            errors.append(f"{name}: {str(e)[:80]}")
    raise TranslationError("Übersetzung ins Deutsche fehlgeschlagen (" + "; ".join(errors) + "). "
                           "Bitte Internetverbindung prüfen oder Ollama installieren (siehe Anleitung).")


def _chunked(text: str, target: str, fn, limit: int = 1500) -> str:
    """Split at line ends / sentence ends into pieces the free services accept, keep every line break."""
    lines = text.split("\n")
    out_lines: list[str] = []
    buf: list[str] = []
    size = 0

    def flush():
        nonlocal buf, size
        if not buf:
            return
        joined = "\n".join(buf)
        res = fn(joined, target).split("\n") if joined.strip() else buf
        if len(res) != len(buf):  # the service merged lines – translate them one by one
            res = [fn(b, target) if b.strip() else b for b in buf]
        out_lines.extend(res)
        buf, size = [], 0

    for line in lines:
        pieces = [line] if len(line) <= limit else re.split(r"(?<=[.!?])\s+", line)
        if len(pieces) > 1:  # a very long line: translate its sentences together, keep it one line
            flush()
            parts, cur = [], ""
            for p in pieces:
                if len(cur) + len(p) > limit and cur:
                    parts.append(fn(cur, target))
                    cur = ""
                cur = (cur + " " + p).strip()
            if cur:
                parts.append(fn(cur, target))
            out_lines.append(" ".join(parts))
            continue
        if size + len(line) > limit:
            flush()
        buf.append(line)
        size += len(line) + 1
    flush()
    return "\n".join(out_lines)


def _google(text: str, target: str) -> str:
    for attempt in range(4):
        r = requests.get("https://translate.googleapis.com/translate_a/single", timeout=20, params={
            "client": "gtx", "sl": "auto", "tl": target, "dt": "t", "q": text})
        if r.status_code == 200:
            return "".join(part[0] for part in r.json()[0] if part and part[0])
        if r.status_code == 429:
            time.sleep(2 + attempt * 3)
            continue
        r.raise_for_status()
    raise RuntimeError("zu viele Anfragen (429)")


def _mymemory(text: str, target: str) -> str:
    out = []
    for piece in [text[i:i + 480] for i in range(0, len(text), 480)]:
        src_lang = "en" if language(piece) == "en" else "autodetect"
        r = requests.get("https://api.mymemory.translated.net/get", timeout=20,
                         params={"q": piece, "langpair": f"{src_lang}|{target}"})
        r.raise_for_status()
        d = r.json()
        if d.get("responseStatus") not in (200, "200"):
            raise RuntimeError(str(d.get("responseDetails"))[:80])
        out.append(d["responseData"]["translatedText"])
    return "".join(out)


def _ollama(text: str, target: str) -> str:
    s = load_settings()
    lang = "Deutsche" if target == "de" else "Englische"
    prompt = (f"Übersetze den folgenden Text ins {lang}. Behalte jeden Zeilenumbruch genau bei (gleiche Anzahl Zeilen). "
              f"Antworte NUR mit der Übersetzung.\n\n{text}")
    r = requests.post(f"{s['ollama_url'].rstrip('/')}/api/generate", timeout=(3, 600),
                      json={"model": s["ollama_model"], "prompt": prompt, "stream": False,
                            "options": {"temperature": 0.2, "num_ctx": 8192}})
    r.raise_for_status()
    return r.json()["response"].strip()


def source_to_german(src, log=print) -> bool:
    """Translate title, text and timed transcripts of a Source in place. Returns True if anything changed."""
    sample = (src.title + "\n" + src.text)[:4000]
    if is_german(sample):
        return False
    log("Quelle ist nicht auf Deutsch – wird übersetzt…")
    src.title = to_german(src.title) if src.title else src.title
    src.text = to_german(src.text)
    for path, segs in list(src.transcripts.items()):
        if not segs:
            continue
        lines = to_german("\n".join(t.replace("\n", " ") for _, _, t in segs)).split("\n")
        if len(lines) == len(segs):
            src.transcripts[path] = [(a, b, ln) for (a, b, _), ln in zip(segs, lines)]
    return True


def script_to_german(script) -> None:
    """Safety net: any scene / title the AI wrote in another language is translated."""
    for sc in script.scenes:
        if not is_german(sc.text):
            sc.text = to_german(sc.text)
    head = "\n".join(script.headline)
    if not is_german(head.lower()) and language(head.lower()) == "en":
        script.headline = (to_german(head).upper().split("\n") + ["", "", ""])[:3]
    if script.caption and language(script.caption) == "en":
        script.caption = to_german(script.caption)
