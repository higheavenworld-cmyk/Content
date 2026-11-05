"""Find and download fitting footage for every scene — no account needed.

Order: your uploads → images from the source article → Wikimedia Commons / Openverse
(free licences, credits are added to the caption) → Pexels videos (only if you added a free key).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import requests
from PIL import Image

from .extract import IMAGE_EXT, UA, VIDEO_EXT

HEAD = {"User-Agent": "CarPlugStudio/1.0 (social video tool)"}


@dataclass
class Clip:
    path: Path
    kind: str            # "image" | "video"
    credit: str = ""     # attribution for the caption (free licences require it)


def collect(scenes, media_files, article_images, work: Path, use_article=True, use_free=True,
            pexels_key="", log=print) -> tuple[list[Clip], list[str]]:
    """Returns one clip per scene and the list of credits."""
    work.mkdir(parents=True, exist_ok=True)
    pool: list[Clip] = []
    for p in media_files:
        ext = Path(p).suffix.lower()
        if ext in VIDEO_EXT:
            pool.append(Clip(Path(p), "video"))
        elif ext in IMAGE_EXT and _ok_image(Path(p)):
            pool.append(Clip(Path(p), "image"))
    if use_article:
        for url in article_images:
            c = _download(url, work, "")
            if c:
                pool.append(c)
        log(f"Artikel-Bilder: {sum(1 for c in pool if c.path.parent == work)}")

    needed = len(scenes)
    queries = list(dict.fromkeys(s.query for s in scenes if s.query))
    if len(pool) < needed and pexels_key:
        for q in queries:
            pool += _pexels(q, pexels_key, work, needed - len(pool))
            if len(pool) >= needed:
                break
    if len(pool) < needed and use_free:
        for q in queries:
            pool += _commons(q, work, needed - len(pool) + 2)
            if len(pool) >= needed:
                break
        for q in queries:
            if len(pool) >= needed:
                break
            pool += _openverse(q, work, needed - len(pool) + 2)
    if not pool:
        raise RuntimeError("Kein Bildmaterial gefunden. Lade Bilder/Videos hoch oder gib einen Link mit Bildern an.")
    clips = [pool[i % len(pool)] for i in range(needed)]
    credits = list(dict.fromkeys(c.credit for c in pool[:needed] if c.credit))
    return clips, credits


# --- sources ----------------------------------------------------------------
def _commons(query: str, work: Path, n: int) -> list[Clip]:
    try:
        r = requests.get("https://commons.wikimedia.org/w/api.php", headers=HEAD, timeout=20, params={
            "action": "query", "format": "json", "generator": "search", "gsrnamespace": 6,
            "gsrsearch": f"{query} filetype:bitmap", "gsrlimit": 15, "prop": "imageinfo",
            "iiprop": "url|size|extmetadata", "iiurlwidth": 1600})
        pages = sorted(r.json().get("query", {}).get("pages", {}).values(), key=lambda p: p.get("index", 0))
    except Exception:
        return []
    out = []
    for p in pages:
        info = (p.get("imageinfo") or [{}])[0]
        if info.get("width", 0) < 900:
            continue
        meta = info.get("extmetadata", {})
        artist = _strip(meta.get("Artist", {}).get("value", "Wikimedia Commons"))
        lic = meta.get("LicenseShortName", {}).get("value", "")
        c = _download(info.get("thumburl") or info.get("url"), work, f"Foto: {artist}, {lic} (Wikimedia Commons)")
        if c:
            out.append(c)
        if len(out) >= n:
            break
    return out


def _openverse(query: str, work: Path, n: int) -> list[Clip]:
    try:
        r = requests.get("https://api.openverse.org/v1/images/", headers=HEAD, timeout=20, params={
            "q": query, "license_type": "commercial", "page_size": 20, "aspect_ratio": "wide,square,tall"})
        results = r.json().get("results", [])
    except Exception:
        return []
    out = []
    for it in results:
        if (it.get("width") or 0) < 900:
            continue
        lic = f"{it.get('license', '').upper()} {it.get('license_version', '')}".strip()
        c = _download(it.get("url"), work, f"Foto: {it.get('creator') or 'unbekannt'}, {lic} (Openverse)")
        if c:
            out.append(c)
        if len(out) >= n:
            break
    return out


def _pexels(query: str, key: str, work: Path, n: int) -> list[Clip]:
    try:
        r = requests.get("https://api.pexels.com/videos/search", headers={"Authorization": key}, timeout=20,
                         params={"query": query, "orientation": "portrait", "per_page": max(n, 3)})
        videos = r.json().get("videos", [])
    except Exception:
        return []
    out = []
    for v in videos[:n]:
        files = sorted((f for f in v.get("video_files", []) if (f.get("height") or 0) >= 1280),
                       key=lambda f: f.get("height") or 0)
        if not files:
            continue
        c = _download(files[0]["link"], work, f"Video: {v.get('user', {}).get('name', '')} (Pexels)", video=True)
        if c:
            out.append(c)
    return out


# --- helpers ----------------------------------------------------------------
def _download(url: str | None, work: Path, credit: str, video: bool = False) -> Clip | None:
    if not url:
        return None
    ext = ".mp4" if video else ".img"
    dst = work / (hashlib.md5(url.encode()).hexdigest()[:16] + ext)
    try:
        if not dst.exists():
            r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
            r.raise_for_status()
            dst.write_bytes(r.content)
        if video:
            return Clip(dst, "video", credit)
        return Clip(dst, "image", credit) if _ok_image(dst) else None
    except Exception:
        return None


def _ok_image(p: Path, min_side: int = 500) -> bool:
    try:
        with Image.open(p) as im:
            return min(im.size) >= min_side and max(im.size) / min(im.size) < 3.2
    except Exception:
        return False


def _strip(s: str) -> str:
    import re
    return re.sub(r"<[^>]+>", "", s).strip()[:60]
