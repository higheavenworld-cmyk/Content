"""Pull text + images out of a link or uploaded files."""
from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin

import requests

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
TEXT_EXT = {".txt", ".md"}


@dataclass
class Source:
    title: str = ""
    text: str = ""
    url: str = ""
    image_urls: list[str] = field(default_factory=list)   # images found in the article
    media_files: list[Path] = field(default_factory=list)  # uploaded / downloaded images and videos
    media_info: dict = field(default_factory=dict)          # path -> {title, text, credit, origin}
    transcripts: dict = field(default_factory=dict)         # video path -> [(start, end, text)]

    def merge(self, other: "Source") -> None:
        self.title = self.title or other.title
        self.text = (self.text + "\n\n" + other.text).strip()
        self.url = self.url or other.url
        self.image_urls += [u for u in other.image_urls if u not in self.image_urls]
        self.media_files += [m for m in other.media_files if m not in self.media_files]
        self.media_info.update(other.media_info)
        self.transcripts.update(other.transcripts)


def from_url(url: str) -> Source:
    import trafilatura

    html = requests.get(url, headers={"User-Agent": UA}, timeout=25).text
    text = trafilatura.extract(html, include_comments=False, include_tables=False, favor_precision=True) or ""
    meta = trafilatura.extract_metadata(html)
    title = (meta.title if meta and meta.title else "") or _first(r"<title[^>]*>(.*?)</title>", html)
    return Source(title=_clean(title), text=text, url=url, image_urls=_article_images(html, url))


def from_files(paths: list[str | Path]) -> Source:
    src = Source()
    for p in map(Path, paths or []):
        ext = p.suffix.lower()
        if ext in IMAGE_EXT or ext in VIDEO_EXT:
            src.media_files.append(p)
            src.media_info[str(p)] = {"title": p.stem, "text": "", "credit": "", "origin": "upload"}
            if ext in VIDEO_EXT:
                from .linkgrab import transcribe
                segs = transcribe(p)
                if segs:
                    src.transcripts[str(p)] = segs
                    src.text += "\n\n" + " ".join(t for _, _, t in segs)
        elif ext in TEXT_EXT:
            src.text += "\n\n" + p.read_text(encoding="utf-8", errors="ignore")
        elif ext == ".pdf":
            from pypdf import PdfReader
            src.text += "\n\n" + "\n".join(pg.extract_text() or "" for pg in PdfReader(str(p)).pages)
        elif ext == ".docx":
            with zipfile.ZipFile(p) as z:
                xml = z.read("word/document.xml").decode("utf-8", "ignore")
            import html as h
            plain = h.unescape(re.sub(r"<[^>]+>", "", re.sub(r"</w:p>", "\n", xml)))
            src.text += "\n\n" + "\n".join(ln.strip() for ln in plain.splitlines() if ln.strip())
    src.text = src.text.strip()
    if not src.title and src.text:
        src.title = src.text.splitlines()[0][:120]
    return src


# --- helpers ----------------------------------------------------------------
_SKIP = re.compile(r"logo|icon|avatar|sprite|banner|pixel|tracking|placeholder|\.svg|\.gif|data:", re.I)


def _article_images(html: str, base: str) -> list[str]:
    urls: list[str] = []
    for pat in (r'<meta[^>]+property=["\']og:image(?::url)?["\'][^>]+content=["\']([^"\']+)',
                r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image'):
        urls += re.findall(pat, html, re.I)
    body = re.search(r"<article.*?</article>", html, re.S | re.I)
    scope = body.group(0) if body else html
    for tag in re.findall(r"<img[^>]+>", scope, re.I):
        srcset = re.search(r'(?:data-)?srcset=["\']([^"\']+)', tag)
        if srcset:  # take the largest candidate
            cands = [c.strip().split(" ")[0] for c in srcset.group(1).split(",") if c.strip()]
            if cands:
                urls.append(cands[-1])
                continue
        m = re.search(r'(?:data-src|data-lazy-src|src)=["\']([^"\']+)', tag)
        if m:
            urls.append(m.group(1))
    out = []
    for u in urls:
        u = urljoin(base, u.replace("&amp;", "&"))
        if u.startswith("http") and not _SKIP.search(u) and u not in out:
            out.append(u)
    return out[:25]


def _first(pattern: str, s: str) -> str:
    m = re.search(pattern, s, re.S | re.I)
    return m.group(1) if m else ""


def _clean(s: str, keep_newlines: bool = False) -> str:
    import html as h
    s = h.unescape(re.sub(r"<[^>]+>", " ", s))
    if keep_newlines:
        return "\n".join(re.sub(r"[ \t]+", " ", ln).strip() for ln in s.splitlines() if ln.strip())
    return re.sub(r"\s+", " ", s).strip()
