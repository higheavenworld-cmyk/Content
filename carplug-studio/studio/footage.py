"""Find fitting, never-repeating footage for every scene — no account or API key needed.

Sources (in this priority):
  1. media from your links (TikTok/YouTube/Instagram videos, post images) and uploads
     → for videos with speech the exact moment that matches the sentence is used
  2. images from the article
  3. free footage: YouTube videos with Creative-Commons licence, Wikimedia Commons (images + videos),
     Openverse (images) — searched separately for every scene
Every picture/video part runs through the text/watermark filter (clean.py) first.
Each scene gets its own picture or a different, non-overlapping part of a video.
"""
from __future__ import annotations

import hashlib
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote_plus

import numpy as np
import requests
from PIL import Image

from . import clean
from .extract import IMAGE_EXT, UA, VIDEO_EXT

HEAD = {"User-Agent": "CarPlugStudio/1.0 (social video tool)"}
ORIGIN_WEIGHT = {"link": 3.0, "upload": 3.0, "article": 2.0, "youtube": 1.6, "commons": 1.2, "openverse": 1.0}
STOP = set("der die das und oder mit von für auf ist sind ein eine einen dem den des im in zu zum zur bei aus "
           "auch noch nur wie was wird werden hat haben als an es sie er wir ihr mehr über unter nach vor "
           "the and for with from this that are was has have its into".split())
TOPICS = {  # German words in the sentence -> English words for picture search / matching
    r"innenraum|cockpit|armaturen|display|bildschirm|lenkrad|sitz": "interior",
    r"motor|zylinder|v8|v12|hubraum|verbrenner": "engine",
    r"felge|räder|rad\b|reifen|bremse": "wheels",
    r"akku|batterie|lade|ladeleistung|ladesäule|strom": "charging",
    r"reichweite|autobahn|fahrt|fährt|unterwegs": "driving",
    r"beschleunig|sprint|höchstgeschwindigkeit|km/h|topspeed|schnell|rennstrecke": "racing",
    r"heck|kofferraum|rückleuchte": "rear",
    r"front|kühlergrill|niere|scheinwerfer|gesicht": "front",
    r"preis|euro|kosten|kostet|händler": "showroom",
    r"panzer|schutz|beschuss|sicherheit": "armored",
    r"design|optik|karosserie|silhouette|look": "design",
    r"werk|fabrik|produktion|montage": "factory",
    r"cabrio|dach": "convertible",
    r"suv|gelände|offroad": "offroad",
}


@dataclass
class Clip:
    path: Path
    kind: str            # "image" | "video"
    credit: str = ""
    start: float = 0.0   # video: where the part starts
    vf: str = ""         # video: ffmpeg crop / watermark-removal filters
    flip: bool = False   # mirrored (only when footage has to be reused)


@dataclass
class Cand:
    kind: str
    origin: str
    path: Path | None = None
    url: str | None = None
    meta: str = ""                        # title / description / tags for matching
    credit: str = ""
    clean_path: Path | None = None        # image after text removal
    video: clean.VideoInfo | None = None
    transcript: list = field(default_factory=list)
    used: list = field(default_factory=list)  # used (start, end) parts of a video
    uses: int = 0
    dhash: int | None = None


# --- public entry --------------------------------------------------------------
def collect(scenes, durations, src, work: Path, aspect: float, use_article=True, use_free=True,
            youtube_all=False, log=print) -> tuple[list[Clip], list[str]]:
    work.mkdir(parents=True, exist_ok=True)
    pool: list[Cand] = []
    lock = threading.Lock()

    # 1) your media + article pictures
    for p in src.media_files:
        info = src.media_info.get(str(p), {})
        kind = "video" if Path(p).suffix.lower() in VIDEO_EXT else "image"
        pool.append(Cand(kind, info.get("origin", "upload"), path=Path(p), meta=info.get("title", "") + " " +
                         info.get("text", ""), credit=info.get("credit", ""),
                         transcript=src.transcripts.get(str(p), [])))
    if use_article:
        pool += [Cand("image", "article", url=u, meta=src.title) for u in src.image_urls[:25]]
    log(f"Prüfe {len(pool)} eigene Bilder/Videos auf Text und Wasserzeichen…")
    pool = _prepare_all(pool, work, aspect, lock)
    log(f"{len(pool)} saubere Bilder/Videos aus deinen Quellen.")

    # 2) free footage, searched per scene until there is enough for every scene
    total_seconds = sum(durations)
    if use_free and _capacity(pool, durations) < len(scenes):
        for q in _queries(scenes):
            if _capacity(pool, durations) >= len(scenes) * 1.3:
                break
            log(f"Suche Footage: „{q}“…")
            found = _search(q, work, youtube_all)
            pool += _prepare_all(found, work, aspect, lock)
    if not pool:
        raise RuntimeError("Kein sauberes Bildmaterial gefunden. Lade eigene Bilder/Videos hoch oder nutze andere Links.")

    # 3) assign the best fitting, unused footage to every scene
    clips, credits, prev = [], [], None
    for i, (sc, dur) in enumerate(zip(scenes, durations)):
        clip, cand = _pick(sc, dur, pool, prev, aspect)
        clips.append(clip)
        prev = cand
        if cand.credit and cand.credit not in credits:
            credits.append(cand.credit)
    reused = sum(1 for c in clips if c.flip)
    if reused:
        log(f"Hinweis: Zu wenig unterschiedliches Material – {reused} Szene(n) nutzen ein Bild gespiegelt erneut.")
    log(f"Footage: {len({str(c.path) for c in clips})} verschiedene Quellen für {len(clips)} Szenen "
        f"({total_seconds:.0f} s).")
    return clips, credits


# --- preparing (download + text filter + duplicates) ------------------------------------
def _prepare_all(cands: list[Cand], work: Path, aspect: float, lock) -> list[Cand]:
    with ThreadPoolExecutor(max_workers=4) as ex:
        prepared = list(ex.map(lambda c: _prepare(c, work, aspect), cands))
    out = []
    with lock:
        hashes: list[int] = []
        for c in prepared:
            if c is None:
                continue
            if c.dhash is not None:
                if any(bin(c.dhash ^ h).count("1") <= 6 for h in hashes):
                    continue  # same picture in another size / from another site
                hashes.append(c.dhash)
            out.append(c)
    return out


def _prepare(c: Cand, work: Path, aspect: float) -> Cand | None:
    try:
        if c.path is None:
            c.path = _download(c.url, work, video=c.kind == "video")
            if c.path is None:
                return None
        if c.kind == "image":
            c.clean_path = clean.clean_image(c.path, work, aspect)
            if c.clean_path is None:
                return None
            c.dhash = _dhash(c.clean_path)
        else:
            c.video = clean.analyze_video(c.path)
            if c.video is None:
                return None
        return c
    except Exception:
        return None


def _capacity(pool: list[Cand], durations: list[float]) -> float:
    avg = max(np.mean(durations), 2.0) if durations else 4.0
    return sum(1 if c.kind == "image" else c.video.dur / avg for c in pool)


def _dhash(p: Path) -> int:
    g = np.asarray(Image.open(p).convert("L").resize((9, 8), Image.BILINEAR), dtype=np.int16)
    bits = (g[:, 1:] > g[:, :-1]).flatten()
    return int("".join("1" if b else "0" for b in bits), 2)


# --- matching ----------------------------------------------------------------------
def _tokens(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-zäöüß0-9]{3,}", s.lower()) if w not in STOP}


def _scene_keywords(sc) -> set[str]:
    kw = _tokens(sc.query + " " + sc.overlay + " " + sc.text)
    for pat, en in TOPICS.items():
        if re.search(pat, sc.text.lower()):
            kw.add(en)
    return kw


def _transcript_time(c: Cand, text: str) -> float | None:
    words = _tokens(text)
    if not c.transcript or len(words) < 3:
        return None
    best, best_t = 0.0, None
    for i, (s, _, t) in enumerate(c.transcript):  # look at 2 neighbouring segments together
        joined = t + " " + (c.transcript[i + 1][2] if i + 1 < len(c.transcript) else "")
        ov = len(words & _tokens(joined)) / len(words)
        if ov > best:
            best, best_t = ov, s
    return best_t if best >= 0.35 else None


def _free_start(c: Cand, dur: float, aspect: float, want: float | None) -> tuple[float, clean.VideoCut] | None:
    v = c.video
    if v.dur < 1.5:
        return None
    need = min(dur, v.dur)
    starts = []
    if want is not None:
        starts.append(max(0.0, min(want - 0.3, v.dur - need)))
    starts += list(np.arange(0.0, max(v.dur - need, 0) + 0.01, max(1.0, need / 2)))
    for s in starts:
        if any(s < b and a < s + need for a, b in c.used):
            continue
        cut = clean.plan_cut(v, s, need, aspect)
        if cut:
            return s, cut
    return None


def _pick(sc, dur: float, pool: list[Cand], prev: Cand | None, aspect: float) -> tuple[Clip, Cand]:
    kw = _scene_keywords(sc)
    best = None
    for c in pool:
        score = ORIGIN_WEIGHT.get(c.origin, 1.0)
        meta = _tokens(c.meta)
        score += 2.5 * len(kw & meta) / max(3, len(kw))
        want = None
        if c.kind == "video":
            want = _transcript_time(c, sc.text)
            if want is not None:
                score += 5  # exactly what is being said
            score += 0.5
        elif c.uses:
            continue  # a picture is never shown twice (as long as alternatives exist)
        if c is prev:
            score -= 3
        score -= 1.5 * c.uses
        if best and score <= best[0]:
            continue
        if c.kind == "video":
            plan = _free_start(c, dur, aspect, want)
            if not plan:
                continue
            best = (score, c, plan)
        else:
            best = (score, c, None)
    if best is None:  # everything used: reuse a picture mirrored, never the one before
        options = ([x for x in pool if x is not prev and x.kind == "image"] or [x for x in pool if x is not prev]
                   or pool)
        c = min(options, key=lambda x: x.uses)
        c.uses += 1
        if c.kind == "image":
            return Clip(c.clean_path, "image", c.credit, flip=True), c
        c.used.clear()
        plan = _free_start(c, dur, aspect, None)
        s, cut = plan if plan else (0.0, clean.VideoCut(0.0, (0, 0, c.video.w, c.video.h)))
        return Clip(c.path, "video", c.credit, s, clean.ffmpeg_filters(cut, c.video.w, c.video.h), flip=True), c
    _, c, plan = best
    c.uses += 1
    if c.kind == "image":
        return Clip(c.clean_path, "image", c.credit), c
    s, cut = plan
    c.used.append((s, s + min(dur, c.video.dur)))
    return Clip(c.path, "video", c.credit, s, clean.ffmpeg_filters(cut, c.video.w, c.video.h)), c


# --- searching -------------------------------------------------------------------------
def _queries(scenes) -> list[str]:
    """One search per scene: model + what the sentence is about (interior, engine, …), then broader."""
    out = []
    base = next((s.query for s in scenes if s.query), "")
    for sc in scenes:
        q = sc.query or base
        for pat, en in TOPICS.items():
            if re.search(pat, sc.text.lower()):
                q = f"{(sc.query or base)} {en}".strip()
                break
        if q and q not in out:
            out.append(q)
    if base:
        out.append(base.split(" ")[0] + " car")
    out += ["sports car driving", "car on highway", "luxury car"]
    return list(dict.fromkeys(out))


def _search(q: str, work: Path, youtube_all: bool) -> list[Cand]:
    found: list[Cand] = []
    with ThreadPoolExecutor(max_workers=4) as ex:
        for res in ex.map(lambda f: f(), [lambda: _youtube(q, work, 3, cc_only=not youtube_all),
                                          lambda: _commons(q, "bitmap", 6), lambda: _commons(q, "video", 2),
                                          lambda: _openverse(q, 6)]):
            found += res
    return found


def _youtube(query: str, work: Path, n: int, cc_only: bool) -> list[Cand]:
    try:
        import yt_dlp
        from yt_dlp.utils import download_range_func

        from .linkgrab import ytdlp_opts
        sp = "EgIwAQ%253D%253D" if cc_only else "EgIQAQ%253D%253D"  # Creative Commons / only videos
        url = f"https://www.youtube.com/results?search_query={quote_plus(query)}&sp={sp}"
        with yt_dlp.YoutubeDL(ytdlp_opts(work, extract_flat=True, playlistend=n * 4, simulate=True,
                                         writesubtitles=False, writeautomaticsub=False)) as y:
            res = y.extract_info(url, download=False) or {}
    except Exception:
        return []
    out = []
    for e in res.get("entries") or []:
        if len(out) >= n:
            break
        d = e.get("duration") or 0
        if not e.get("id") or not 15 <= d <= 1800:
            continue
        a = d * 0.12
        opts = ytdlp_opts(work, writesubtitles=False, writeautomaticsub=False,
                          format="bv*[height<=1080][ext=mp4]/bv*[height<=1080]",
                          outtmpl=str(work / "yt_%(id)s.%(ext)s"),
                          download_ranges=download_range_func(None, [(a, min(d, a + 150))]),
                          force_keyframes_at_cuts=False)
        try:
            with yt_dlp.YoutubeDL(opts) as y:
                info = y.extract_info(f"https://www.youtube.com/watch?v={e['id']}", download=False)
                lic = (info.get("license") or "").lower()
                if cc_only and "creative commons" not in lic:
                    continue
                info = y.extract_info(f"https://www.youtube.com/watch?v={e['id']}", download=True)
        except Exception:
            continue
        paths = [Path(d["filepath"]) for d in info.get("requested_downloads") or [] if d.get("filepath")]
        if not paths or not paths[0].exists():
            continue
        credit = f"Video: {info.get('uploader', '')} (YouTube" + (", CC BY)" if cc_only else ")")
        out.append(Cand("video", "youtube", path=paths[0], credit=credit,
                        meta=f"{info.get('title', '')} {' '.join(info.get('tags') or [])} {query}"))
    return out


def _commons(query: str, ftype: str, n: int) -> list[Cand]:
    try:
        r = requests.get("https://commons.wikimedia.org/w/api.php", headers=HEAD, timeout=20, params={
            "action": "query", "format": "json", "generator": "search", "gsrnamespace": 6,
            "gsrsearch": f"{query} filetype:{ftype}", "gsrlimit": 20, "prop": "imageinfo",
            "iiprop": "url|size|extmetadata", "iiurlwidth": 1920})
        pages = sorted(r.json().get("query", {}).get("pages", {}).values(), key=lambda p: p.get("index", 0))
    except Exception:
        return []
    out = []
    for p in pages:
        info = (p.get("imageinfo") or [{}])[0]
        if ftype == "bitmap" and info.get("width", 0) < 900:
            continue
        if ftype == "video" and not 0 < info.get("size", 0) < 150_000_000:
            continue
        meta = info.get("extmetadata", {})
        artist = re.sub(r"<[^>]+>", "", meta.get("Artist", {}).get("value", "Wikimedia Commons")).strip()[:60]
        lic = meta.get("LicenseShortName", {}).get("value", "")
        url = info.get("url") if ftype == "video" else (info.get("thumburl") or info.get("url"))
        desc = re.sub(r"<[^>]+>", " ", meta.get("ImageDescription", {}).get("value", ""))
        out.append(Cand("video" if ftype == "video" else "image", "commons", url=url,
                        meta=f"{p.get('title', '')} {desc} {query}",
                        credit=f"{'Video' if ftype == 'video' else 'Foto'}: {artist}, {lic} (Wikimedia Commons)"))
        if len(out) >= n:
            break
    return out


def _openverse(query: str, n: int) -> list[Cand]:
    try:
        r = requests.get("https://api.openverse.org/v1/images/", headers=HEAD, timeout=20, params={
            "q": query, "license_type": "commercial", "page_size": 20})
        results = r.json().get("results", [])
    except Exception:
        return []
    out = []
    for it in results:
        if (it.get("width") or 0) < 900:
            continue
        lic = f"{it.get('license', '').upper()} {it.get('license_version', '')}".strip()
        out.append(Cand("image", "openverse", url=it.get("url"),
                        meta=f"{it.get('title', '')} {' '.join(t.get('name', '') for t in it.get('tags') or [])} {query}",
                        credit=f"Foto: {it.get('creator') or 'unbekannt'}, {lic} (Openverse)"))
        if len(out) >= n:
            break
    return out


def _download(url: str | None, work: Path, video: bool = False) -> Path | None:
    if not url:
        return None
    ext = Path(url.split("?")[0]).suffix.lower()
    ext = ext if ext in (VIDEO_EXT if video else IMAGE_EXT) else (".mp4" if video else ".jpg")
    dst = work / (hashlib.md5(url.encode()).hexdigest()[:16] + ext)
    if dst.exists():
        return dst
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=60)
        r.raise_for_status()
        dst.write_bytes(r.content)
        return dst
    except Exception:
        return None
