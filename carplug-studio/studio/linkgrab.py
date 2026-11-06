"""Get text + media out of (almost) any link.

- Video platforms (TikTok, YouTube, Instagram, X/Twitter, Facebook, Vimeo, Reddit, … ~1800 sites):
  yt-dlp downloads the video, title, description and subtitles. Without subtitles the speech
  is transcribed locally (faster-whisper). Timestamps are kept, so footage can be matched to
  exactly what is being said.
- Image posts / galleries (Instagram, Pinterest, X, Reddit, Imgur, TikTok photo posts, …): gallery-dl.
- Direct image / video links: downloaded as they are.
- Everything else (blogs, news, websites): article text + images + embedded video.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

import requests

from . import extract
from .config import load_settings
from .extract import IMAGE_EXT, UA, VIDEO_EXT, Source
from .tools import ffmpeg_dir

MAX_VIDEO_SECONDS = 30 * 60


def grab(url: str, work: Path, log=print) -> Source:
    url = url.strip()
    work.mkdir(parents=True, exist_ok=True)
    ext = Path(urlparse(url).path).suffix.lower()
    if ext in IMAGE_EXT or ext in VIDEO_EXT:
        return _direct(url, work, ext)
    src = _with_ytdlp(url, work, log)
    if src and src.title.startswith("\x00generic"):  # a normal web page with an embedded video
        src.title = src.title[len("\x00generic"):]
        try:
            art = extract.from_url(url)
            art.merge(src)
            return art
        except Exception:
            return src
    if src:
        return src
    src = _with_gallery_dl(url, work, log)
    if src:
        return src
    try:
        ctype = requests.head(url, headers={"User-Agent": UA}, timeout=15, allow_redirects=True).headers.get(
            "content-type", "")
        if ctype.startswith(("image/", "video/")):
            return _direct(url, work, "." + ctype.split("/")[1].split(";")[0].replace("jpeg", "jpg"))
    except requests.RequestException:
        pass
    log("Webseite wird gelesen…")
    src = extract.from_url(url)
    video = _og_video(url)
    if video:
        s2 = _direct(video, work, ".mp4")
        src.media_files += s2.media_files
    return src


# --- direct files ------------------------------------------------------------------
def _direct(url: str, work: Path, ext: str) -> Source:
    dst = work / (hashlib.md5(url.encode()).hexdigest()[:12] + ext)
    r = requests.get(url, headers={"User-Agent": UA}, timeout=60)
    r.raise_for_status()
    dst.write_bytes(r.content)
    src = Source(url=url, media_files=[dst])
    src.media_info[str(dst)] = {"title": Path(urlparse(url).path).stem, "text": "", "credit": "",
                                "origin": "link"}
    if ext in VIDEO_EXT:
        segs = transcribe(dst)
        src.transcripts[str(dst)] = segs
        src.text = " ".join(t for _, _, t in segs)
    return src


def _og_video(url: str) -> str | None:
    try:
        html = requests.get(url, headers={"User-Agent": UA}, timeout=20).text
    except requests.RequestException:
        return None
    m = re.search(r'<meta[^>]+property=["\']og:video(?::secure_url|:url)?["\'][^>]+content=["\']([^"\']+\.mp4[^"\']*)',
                  html, re.I)
    return m.group(1) if m else None


# --- video platforms (yt-dlp) ----------------------------------------------------------
class _Quiet:
    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass


VIDEO_SITES = re.compile(r"(^|\.)(youtube\.com|youtu\.be|tiktok\.com|instagram\.com|x\.com|twitter\.com|"
                         r"facebook\.com|fb\.watch|vimeo\.com|reddit\.com|dailymotion\.com|twitch\.tv|"
                         r"threads\.net|snapchat\.com|linkedin\.com|bilibili\.com)$", re.I)


def _js_runtime() -> dict:
    """YouTube needs a JavaScript runtime since late 2025 – Deno is installed with the program."""
    try:
        import deno
        return {"deno": {"path": deno.find_deno_bin()}}
    except Exception:
        return {"deno": {}, "node": {}}


def ytdlp_opts(work: Path, **extra) -> dict:
    s = load_settings()
    opts = {
        "logger": _Quiet(),
        "quiet": True, "no_warnings": True, "noplaylist": True, "noprogress": True,
        "outtmpl": str(work / "%(extractor)s_%(id)s.%(ext)s"),
        "format": "bv*+ba/b",
        "format_sort": ["res:1080", "ext:mp4:m4a", "vcodec:h264"],
        "merge_output_format": "mp4",
        "ffmpeg_location": ffmpeg_dir(),
        "js_runtimes": _js_runtime(),
        "remote_components": {"ejs:github"},
        "socket_timeout": 30, "retries": 5, "fragment_retries": 5,
    }
    if s.get("cookies_browser"):
        opts["cookiesfrombrowser"] = (s["cookies_browser"],)
    opts.update(extra)
    return opts


def _friendly(err: Exception) -> str:
    m = re.sub(r"\x1b\[[0-9;]*m", "", str(err))
    low = m.lower()
    if "not a bot" in low or "sign in to confirm" in low:
        return ("YouTube möchte eine Bestätigung, dass du kein Bot bist. Öffne YouTube einmal in Firefox (eingeloggt), "
                "wähle unter ⚙️ Einstellungen den Login-Browser „firefox“ und versuche es erneut.")
    if "private" in low or "login" in low or "cookies" in low or ("age" in low and "restrict" in low):
        return ("Dieser Link braucht einen Login (privat, Altersgrenze oder Instagram). Unter ⚙️ Einstellungen den "
                "Browser wählen, in dem du eingeloggt bist, und erneut versuchen.")
    if "unavailable" in low or "removed" in low:
        return "Das Video ist nicht (mehr) verfügbar oder in deinem Land gesperrt."
    if "unable to connect" in low or "timed out" in low or "connection" in low and "error" in low:
        return "Keine Verbindung zur Plattform. Prüfe dein Internet (oder Firewall/VPN) und versuche es erneut."
    if "unsupported url" in low:
        return "Dieser Link wird nicht unterstützt."
    if "ffmpeg" in low:
        return "Video konnte nicht zusammengesetzt werden (ffmpeg). Bitte CarPlug-Studio-Installieren.bat erneut ausführen."
    return "Video konnte nicht geladen werden: " + m[:300]


def _with_ytdlp(url: str, work: Path, log) -> Source | None:
    import yt_dlp

    platform = bool(VIDEO_SITES.search(urlparse(url).hostname or ""))
    try:
        with yt_dlp.YoutubeDL(ytdlp_opts(work, simulate=True)) as y:
            info = y.extract_info(url, download=False)
    except Exception as e:
        if platform:  # a video platform link must never silently turn into "read the web page"
            raise ValueError(_friendly(e)) from e
        return None
    if not info or info.get("_type") == "playlist" and not info.get("entries"):
        return None
    if info.get("_type") == "playlist":
        info = next((e for e in info["entries"] if e), None)
        if not info:
            return None
    if info.get("extractor", "").lower().startswith("generic") and not info.get("formats") and not info.get("url"):
        return None
    if (info.get("duration") or 0) > MAX_VIDEO_SECONDS:
        raise ValueError("Das Video ist länger als 30 Minuten – bitte ein kürzeres verwenden.")
    sub_langs = _sub_langs(info)
    log(f"Video wird geladen: {info.get('title', '')[:60]}…")
    try:
        with yt_dlp.YoutubeDL(ytdlp_opts(work)) as y:
            info = y.extract_info(url, download=True)
    except Exception as e:
        if platform:
            raise ValueError(_friendly(e)) from e
        return None
    path = _downloaded_file(info, work)
    if not path:
        if platform:
            raise ValueError("Video wurde geladen, aber die Datei fehlt. Bitte erneut versuchen.")
        return None
    segs = []
    for lang in sub_langs[:3]:  # subtitles separately: a subtitle error must not break the video download
        try:
            with yt_dlp.YoutubeDL(ytdlp_opts(work, skip_download=True, writesubtitles=True, writeautomaticsub=True,
                                             subtitleslangs=[lang], subtitlesformat="vtt")) as y:
                segs = _subtitles(y.extract_info(url, download=True))
        except Exception:
            segs = []
        if segs:
            break
    if not segs:
        log("Sprache wird erkannt (keine Untertitel)…")
        segs = transcribe(path)
    generic = (info.get("extractor") or "").lower().startswith("generic")
    src = Source(title=("\x00generic" if generic else "") + (info.get("title") or ""), url=url, media_files=[path])
    desc = (info.get("description") or "").strip()
    credit = f"Video: {info.get('uploader') or info.get('channel') or ''} ({info.get('extractor_key', '')})"
    src.media_info[str(path)] = {"title": info.get("title") or "", "text": desc + " " + " ".join(info.get("tags") or []),
                                 "credit": credit, "origin": "link"}
    src.transcripts[str(path)] = segs
    spoken = " ".join(t for _, _, t in segs)
    src.text = "\n".join(x for x in (desc, spoken) if x)
    for t in info.get("thumbnails") or []:
        if t.get("url") and (t.get("width") or 0) >= 720:
            src.image_urls.append(t["url"])
    return src


def _downloaded_file(info: dict, work: Path) -> Path | None:
    for key in ("requested_downloads",):
        for d in info.get(key) or []:
            p = Path(d.get("filepath") or "")
            if p.exists():
                return p
    p = Path(info.get("filepath") or info.get("_filename") or "")
    if p.exists():
        return p
    stem = f"{info.get('extractor')}_{info.get('id')}"
    cands = [c for c in work.glob(stem + ".*") if c.suffix.lower() in VIDEO_EXT]
    return cands[0] if cands else None


def _sub_langs(info: dict) -> list[str]:
    """Subtitle languages to try, best first: German, then YouTube's own German translation of the
    automatic captions (no translation service needed), then the original language."""
    manual = info.get("subtitles") or {}
    auto = info.get("automatic_captions") or {}
    out = []
    if "de" in manual:
        out.append("de")
    if "de" in auto:
        out.append("de")
    lang = (info.get("language") or "").split("-")[0]
    if lang and lang in manual:
        out.append(lang)
    orig = next((k for k in auto if k.endswith("-orig")), None)
    for k in (orig, lang if lang in auto else None, "en" if "en" in manual else None, "en" if "en" in auto else None):
        if k:
            out.append(k)
    if manual and not out:
        out.append(next(iter(manual)))
    return list(dict.fromkeys(out))


def _subtitles(info: dict) -> list[tuple[float, float, str]]:
    subs = info.get("requested_subtitles") or {}
    for lang in sorted(subs, key=lambda k: (not k.startswith("de"), k)):
        p = subs[lang].get("filepath")
        if p and Path(p).exists():
            segs = parse_vtt(Path(p).read_text(encoding="utf-8", errors="ignore"))
            if segs:
                return segs
    return []


def parse_vtt(text: str) -> list[tuple[float, float, str]]:
    def secs(t: str) -> float:
        parts = t.replace(",", ".").split(":")
        return sum(float(p) * 60 ** i for i, p in enumerate(reversed(parts)))

    out: list[tuple[float, float, str]] = []
    seen_last = ""
    for block in re.split(r"\n\s*\n", text):
        m = re.search(r"([\d:.,]+)\s*-->\s*([\d:.,]+)", block)
        if not m:
            continue
        lines = [re.sub(r"<[^>]+>", "", ln).strip() for ln in block[m.end():].splitlines()]
        lines = [ln for ln in lines if ln and not ln.startswith(("NOTE", "align:", "position:"))]
        new = [ln for ln in lines if ln != seen_last]  # auto-captions repeat the previous line
        if not new:
            continue
        seen_last = lines[-1]
        txt = " ".join(new).strip()
        if txt and (not out or txt != out[-1][2]):
            out.append((secs(m.group(1)), secs(m.group(2)), txt))
    return out


_WHISPER = None


def transcribe(path: Path) -> list[tuple[float, float, str]]:
    """Local speech recognition with timestamps (faster-whisper). Empty list if not installed."""
    global _WHISPER
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return []
    try:
        if _WHISPER is None:
            _WHISPER = WhisperModel("small", device="cpu", compute_type="int8")
        segs, _ = _WHISPER.transcribe(str(path), vad_filter=True)
        return [(s.start, s.end, s.text.strip()) for s in segs if s.text.strip()]
    except Exception:
        return []


# --- image posts (gallery-dl) -------------------------------------------------------------
def _with_gallery_dl(url: str, work: Path, log) -> Source | None:
    dest = work / ("gallery_" + hashlib.md5(url.encode()).hexdigest()[:10])
    cmd = [sys.executable, "-m", "gallery_dl", "--write-metadata", "--range", "1-20", "-D", str(dest)]
    browser = load_settings().get("cookies_browser")
    if browser:
        cmd += ["--cookies-from-browser", browser]
    try:
        subprocess.run(cmd + [url], capture_output=True, timeout=180, check=False)
    except Exception:
        return None
    files = [p for p in sorted(dest.glob("*")) if p.suffix.lower() in IMAGE_EXT | VIDEO_EXT] if dest.exists() else []
    if not files:
        return None
    log(f"{len(files)} Bilder/Videos aus dem Post geladen.")
    src = Source(url=url, media_files=files)
    texts = []
    for f in files:
        meta = {}
        mf = f.with_name(f.name + ".json")
        if mf.exists():
            try:
                meta = json.loads(mf.read_text(encoding="utf-8"))
            except ValueError:
                meta = {}
        desc = str(meta.get("description") or meta.get("content") or meta.get("title") or "")
        if desc and desc not in texts:
            texts.append(desc)
        src.media_info[str(f)] = {"title": desc[:120], "text": desc, "origin": "link",
                                  "credit": f"Bild: {meta.get('username') or meta.get('uploader') or ''}"}
    src.text = "\n".join(texts)
    src.title = (texts[0].splitlines()[0][:120] if texts else "")
    return src
