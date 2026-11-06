"""Everything ends up in German – whatever language the source is in.

Built to never run into "too many requests":
- every line is translated exactly once, in big batches (few requests), results are remembered on disk
- several free services without account, each with pauses and patient retries:
  local AI (Ollama, if running) → Microsoft (Edge translator) → Google → offline model (if the voice-clone
  module is installed) → MyMemory (only short texts)
Line structure is always kept, so timed transcripts stay aligned.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time

import requests

from .config import WORK_DIR, load_settings

DE = set("der die das und ist nicht ein eine einen mit auf für von zu den dem des sich auch es im wird werden "
         "hat haben noch nach bei aus wie über aber oder wenn man sind war sie er wir ich".split())
EN = set("the and is are not a an with on for of to it this that was be by from at as have has will "
         "you they we he she or but if which can its".split())
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36 Edg/124"
MAX_SOURCE_CHARS = 24000  # more is never needed for a script (10 min ≈ 9 000 characters)
CACHE_FILE = WORK_DIR / "uebersetzungen.json"
_lock = threading.Lock()


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


class RateLimited(RuntimeError):
    pass


# --- cache -------------------------------------------------------------------------------
def _key(line: str, target: str) -> str:
    return target + ":" + hashlib.md5(line.encode("utf-8")).hexdigest()


def _load_cache() -> dict:
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_cache(cache: dict) -> None:
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


# --- public API ----------------------------------------------------------------------------
def translate_lines(lines: list[str], target: str = "de", log=None) -> list[str]:
    """Translate a list of lines; returns the same number of lines in the same order."""
    with _lock:
        cache = _load_cache()
        todo = list(dict.fromkeys(ln for ln in lines if ln.strip() and _key(ln, target) not in cache))
        if todo:
            errors = []
            for name, fn in _engines(sum(len(x) for x in todo)):
                if not todo:
                    break
                try:
                    if log:
                        log(f"Übersetze {len(todo)} Zeile(n) mit {name}…")
                    done = fn(todo, target)
                    for src, dst in zip(todo, done):
                        if dst and dst.strip():
                            cache[_key(src, target)] = dst.strip()
                    _save_cache(cache)
                    todo = [ln for ln in todo if _key(ln, target) not in cache]
                except Exception as e:  # next service
                    errors.append(f"{name}: {_short(e)}")
                    _save_cache(cache)  # keep what was already translated
            if todo:
                raise TranslationError(
                    "Übersetzung ins Deutsche gerade nicht möglich (" + "; ".join(errors) + "). Die kostenlosen "
                    "Dienste begrenzen kurzzeitig die Anzahl der Anfragen – bitte in 10–15 Minuten erneut versuchen. "
                    "Bereits übersetzte Teile sind gespeichert.")
        return [cache.get(_key(ln, target), ln) if ln.strip() else ln for ln in lines]


def to_german(text: str, log=None) -> str:
    if not text.strip() or is_german(text):
        return text
    return "\n".join(translate_lines(text.split("\n"), "de", log))


def to_english(text: str) -> str:
    try:
        return "\n".join(translate_lines(text.split("\n"), "en"))
    except TranslationError:
        return text


def source_to_german(src, log=print) -> bool:
    """Translate title, text and timed transcripts in place – every line only once."""
    sample = (src.title + "\n" + src.text)[:4000]
    if is_german(sample):
        return False
    log("Quelle ist nicht auf Deutsch – wird übersetzt…")
    seg_lines = {p: [t.replace("\n", " ").strip() for _, _, t in segs] for p, segs in src.transcripts.items() if segs}
    text = src.text
    for lines in seg_lines.values():  # the transcript is already in the text as one long line – take it out
        text = text.replace(" ".join(lines), "\n")
    rest = []
    for ln in re.split(r"\n+", text):
        ln = ln.strip()
        if not ln:
            continue
        rest += [x for x in re.split(r"(?<=[.!?])\s+", ln) if x.strip()] if len(ln) > 400 else [ln]
    budget, wanted = MAX_SOURCE_CHARS, []
    for ln in [src.title] + rest + [x for lines in seg_lines.values() for x in lines]:
        if ln and budget > 0:
            wanted.append(ln)
            budget -= len(ln)
    if budget <= 0:
        log("Hinweis: Sehr lange Quelle – übersetzt werden die ersten ca. 24 000 Zeichen (reicht für jedes Script).")
    done = dict(zip(wanted, translate_lines(wanted, "de", log)))
    src.title = done.get(src.title, src.title)
    for p, lines in seg_lines.items():
        src.transcripts[p] = [(a, b, done.get(ln, ln)) for (a, b, _), ln in zip(src.transcripts[p], lines)]
    parts = [done[ln] for ln in rest if ln in done]
    for lines in seg_lines.values():
        parts.append(" ".join(done[ln] for ln in lines if ln in done))
    src.text = "\n".join(x for x in parts if x.strip())
    return True


def script_to_german(script) -> None:
    """Safety net: any scene / title the AI wrote in another language is translated."""
    for sc in script.scenes:
        if not is_german(sc.text):
            sc.text = to_german(sc.text)
    head = "\n".join(script.headline)
    if language(head.lower()) == "en":
        script.headline = (to_german(head).upper().split("\n") + ["", "", ""])[:3]
    if script.caption and language(script.caption) == "en":
        script.caption = to_german(script.caption)


# --- engines ---------------------------------------------------------------------------------
def _engines(total_chars: int):
    out = []
    if _ollama_running():
        out.append(("lokaler KI (Ollama)", _ollama))
    out += [("Microsoft Übersetzer", _microsoft), ("Google Übersetzer", _google),
            ("Offline-Übersetzer (auf deinem PC)", _offline)]  # always there – works without internet limits
    if total_chars < 3000:
        out.append(("MyMemory", _mymemory))
    return out


def _short(e: Exception) -> str:
    return re.sub(r"\s+", " ", str(e))[:90]


def _retry(fn, tries: int = 2):
    """One short retry on 'too many requests' – then the next service takes over (no long waiting)."""
    for k in range(tries):
        try:
            return fn()
        except RateLimited:
            if k == tries - 1:
                raise
            time.sleep(3)


_EDGE = {"token": "", "at": 0.0}


def _edge_token(force: bool = False) -> str:
    if force or not _EDGE["token"] or time.time() - _EDGE["at"] > 480:
        r = requests.get("https://edge.microsoft.com/translate/auth", headers={"User-Agent": UA}, timeout=20)
        r.raise_for_status()
        _EDGE.update(token=r.text.strip(), at=time.time())
    return _EDGE["token"]


def _microsoft(lines: list[str], target: str) -> list[str]:
    out: list[str] = []
    for batch in _batches(lines, max_items=100, max_chars=40000):
        def call(batch=batch):
            for attempt in range(2):
                r = requests.post("https://api-edge.cognitive.microsofttranslator.com/translate", timeout=60,
                                  params={"to": target, "api-version": "3.0"},
                                  headers={"Authorization": "Bearer " + _edge_token(force=attempt > 0),
                                           "Content-Type": "application/json", "User-Agent": UA},
                                  json=[{"Text": t} for t in batch])
                if r.status_code == 401 and attempt == 0:
                    continue
                if r.status_code == 429:
                    raise RateLimited("429")
                r.raise_for_status()
                return [item["translations"][0]["text"] for item in r.json()]
            raise RuntimeError("Anmeldung beim Dienst fehlgeschlagen")
        res = _retry(call)
        if len(res) != len(batch):
            raise RuntimeError("Antwort unvollständig")
        out += res
        time.sleep(0.6)
    return out


def _google(lines: list[str], target: str) -> list[str]:
    out: list[str] = []
    for batch in _batches(lines, max_items=60, max_chars=1300):
        out += _google_block(batch, target)
    return out


def _google_block(batch: list[str], target: str) -> list[str]:
    def call():
        r = requests.get("https://translate.googleapis.com/translate_a/single", timeout=30, headers={"User-Agent": UA},
                         params={"client": "gtx", "sl": "auto", "tl": target, "dt": "t", "q": "\n".join(batch)})
        if r.status_code == 429:
            raise RateLimited("429")
        r.raise_for_status()
        return "".join(part[0] for part in r.json()[0] if part and part[0]).split("\n")
    res = _retry(call)
    time.sleep(0.8)
    if len(res) == len(batch):
        return res
    if len(batch) == 1:
        return [" ".join(res)]
    half = len(batch) // 2  # lines got merged: split the block in two (few extra requests, never line by line)
    return _google_block(batch[:half], target) + _google_block(batch[half:], target)


def _mymemory(lines: list[str], target: str) -> list[str]:
    out = []
    for ln in lines:
        r = requests.get("https://api.mymemory.translated.net/get", timeout=20,
                         params={"q": ln[:480], "langpair": f"{'en' if language(ln) == 'en' else 'autodetect'}|{target}"})
        if r.status_code == 429:
            raise RateLimited("429")
        r.raise_for_status()
        d = r.json()
        if d.get("responseStatus") not in (200, "200"):
            raise RuntimeError(str(d.get("responseDetails"))[:80])
        out.append(d["responseData"]["translatedText"])
        time.sleep(0.5)
    return out


def _ollama_running() -> bool:
    try:
        s = load_settings()
        return requests.get(f"{s['ollama_url'].rstrip('/')}/api/tags", timeout=1.5).ok
    except Exception:
        return False


def _ollama(lines: list[str], target: str) -> list[str]:
    s = load_settings()
    lang = "Deutsche" if target == "de" else "Englische"
    out: list[str] = []
    for batch in _batches(lines, max_items=40, max_chars=3000):
        numbered = "\n".join(f"{i + 1}|{t}" for i, t in enumerate(batch))
        prompt = (f"Übersetze jede Zeile ins {lang}. Behalte die Nummerierung 'N|' am Zeilenanfang exakt bei. "
                  f"Antworte NUR mit den übersetzten Zeilen.\n\n{numbered}")
        r = requests.post(f"{s['ollama_url'].rstrip('/')}/api/generate", timeout=(3, 900),
                          json={"model": s["ollama_model"], "prompt": prompt, "stream": False,
                                "options": {"temperature": 0.2, "num_ctx": 8192}})
        r.raise_for_status()
        got = {}
        for ln in r.json()["response"].splitlines():
            m = re.match(r"\s*(\d+)\s*\|\s*(.*)", ln)
            if m:
                got[int(m.group(1))] = m.group(2)
        out += [got.get(i + 1, "") for i in range(len(batch))]
    return out


def _offline(lines: list[str], target: str) -> list[str]:
    """Offline translation on this PC (Argos/OPUS models, downloaded once) – no limits."""
    from . import offline_mt
    return offline_mt.translate(lines, target)


def _batches(lines: list[str], max_items: int, max_chars: int):
    batch, size = [], 0
    for ln in lines:
        if batch and (len(batch) >= max_items or size + len(ln) > max_chars):
            yield batch
            batch, size = [], 0
        batch.append(ln[:max_chars])
        size += len(ln)
    if batch:
        yield batch
