"""Renders the final video in CarPlug branding (motion graphics, subtitles, music) in any format.

All positions are computed from the chosen size. In tall formats (9:16) every text element stays
inside the platform SAFE zone, so nothing hides behind the TikTok / Reels / Shorts interface.
"""
from __future__ import annotations

import multiprocessing as mp
import os
import re
import subprocess
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from .config import BLACK, BRAND_URL, FONT_BOLD, FONT_HEADLINE, FPS, GREY, LOGO_FILE, RED, WHITE
from .footage import Clip
from .tools import probe
from .voice import SR, Word, ffmpeg, write_wav

GAP = 0.12            # pause between scenes (seconds)
LEAD = 0.27           # GAP + small breath after every intro/body scene
OUTRO_TAIL = 1.0      # logo stays this long after the last word
MAX_PAUSE = 0.6       # extra pause per scene allowed to reach a minimum length
MAX_OUTRO_EXTRA = 4.0
DARK = (18, 18, 20)

UNIT_LABELS = [(r"PS|KW\b", "LEISTUNG"), (r"KM/H", "TOPSPEED"), (r"NM\b", "DREHMOMENT"), (r"KWH", "AKKU"),
               (r"EURO|€", "PREIS"), (r"KILOMETER|\bKM\b", "REICHWEITE"), (r"SEKUNDEN", "SPRINT"),
               (r"TONNEN|KILO|KG", "GEWICHT"), (r"ZYLINDER|LITER", "MOTOR"), (r"PROZENT|%", "ANTEIL"),
               (r"ZOLL", "FELGEN")]


# --- layout ------------------------------------------------------------------------
class Layout:
    """Sizes and safe zone for one output format."""

    def __init__(self, w: int, h: int):
        self.w, self.h = w - w % 2, h - h % 2
        self.s = min(self.w, self.h) / 1080  # scale factor vs. the 1080 px design
        if self.h / self.w >= 1.6:  # tall: TikTok / Reels / Shorts interface zones
            self.l, self.t = round(self.w * 0.059), round(self.h * 0.13)
            self.r, self.b = round(self.w * 0.843), round(self.h * 0.75)
        else:  # feed / landscape: even margin all around
            m = round(min(self.w, self.h) * 0.06)
            self.l, self.t, self.r, self.b = m, m, self.w - m, self.h - m
        self.sw, self.sh = self.r - self.l, self.b - self.t
        self.cx = (self.l + self.r) // 2

    def px(self, v: float) -> int:
        return max(1, int(round(v * self.s)))


L = Layout(1080, 1920)


def set_layout(w: int, h: int) -> None:
    global L
    L = Layout(w, h)
    for fn in (vgradient, vignette, red_glow, subtitle_img):
        fn.cache_clear()


@dataclass
class Segment:
    kind: str                 # intro | body | outro
    start: float
    dur: float
    clip: Clip | None
    overlay: str = ""
    words: list[Word] = field(default_factory=list)


# --- duration planning (used to guarantee a minimum length) -------------------------------
def natural_duration(voices) -> float:
    n = len(voices)
    return sum(len(a) / SR for a, _ in voices) + LEAD * (n - 1) + OUTRO_TAIL


def max_stretch(n_scenes: int) -> float:
    return MAX_PAUSE * max(n_scenes - 2, 0) + MAX_OUTRO_EXTRA


def stretch_plan(voices, min_total: float | None) -> tuple[float, float]:
    """(extra pause per body scene, extra outro seconds) to reach min_total."""
    if not min_total:
        return 0.0, 0.0
    deficit = min_total - natural_duration(voices)
    if deficit <= 0:
        return 0.0, 0.0
    n_body = max(len(voices) - 2, 0)
    pause = min(MAX_PAUSE, deficit / n_body) if n_body else 0.0
    return pause, max(0.0, deficit - pause * n_body)


# --- helpers ---------------------------------------------------------------------
def ease(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return 1 - (1 - x) ** 3


def ease_back(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    c = 1.70158
    return 1 + (c + 1) * (x - 1) ** 3 + c * (x - 1) ** 2


@lru_cache(maxsize=128)
def font(size: int, upright: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_BOLD if upright else FONT_HEADLINE), max(8, size))


def fit_size(text: str, max_w: int, size: int, min_size: int = 20, upright: bool = False) -> int:
    while size > min_size and font(size, upright).getlength(text) > max_w:
        size -= 2
    return size


@lru_cache(maxsize=16)
def logo_icon(size: int) -> Image.Image:
    im = Image.open(LOGO_FILE).convert("RGB").resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size * 4, size * 4), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size * 4 - 1, size * 4 - 1), radius=size * 4 // 5, fill=255)
    im.putalpha(mask.resize((size, size), Image.LANCZOS))
    return im


@lru_cache(maxsize=8)
def vgradient(h: int, a0: int, a1: int, power: float = 1.0) -> Image.Image:
    a = a0 + (a1 - a0) * np.linspace(0, 1, h, dtype=np.float32) ** power
    g = Image.new("RGBA", (L.w, h), BLACK + (0,))
    g.putalpha(Image.fromarray(np.repeat(a[:, None], L.w, axis=1).astype(np.uint8), "L"))
    return g


@lru_cache(maxsize=1)
def vignette() -> Image.Image:
    y, x = np.mgrid[0:L.h, 0:L.w].astype(np.float32)
    d = np.sqrt(((x - L.w / 2) / (L.w * 0.75)) ** 2 + ((y - L.h / 2) / (L.h * 0.7)) ** 2)
    v = Image.new("RGBA", (L.w, L.h), BLACK + (0,))
    v.putalpha(Image.fromarray((np.clip(d - 0.45, 0, 1) * 200).astype(np.uint8), "L"))
    return v


@lru_cache(maxsize=1)
def red_glow() -> Image.Image:
    y, x = np.mgrid[0:L.h, 0:L.w].astype(np.float32)
    rad = 620 * L.s
    d = np.sqrt(((x - L.w / 2) / rad) ** 2 + ((y - L.h * 0.4) / rad) ** 2)
    a = np.clip(1 - d, 0, 1) ** 2
    img = np.zeros((L.h, L.w, 3), np.float32) + np.array(BLACK, np.float32)
    img += a[..., None] * (np.array(RED, np.float32) - BLACK) * 0.55
    return Image.fromarray(img.astype(np.uint8), "RGB")


def text_img(text: str, f: ImageFont.FreeTypeFont, color, shadow: bool = False) -> Image.Image:
    l, t, r, b = f.getbbox(text)
    pad = L.px(24) if shadow else L.px(8)
    im = Image.new("RGBA", (r - l + 2 * pad + L.px(10), b - t + 2 * pad), (0, 0, 0, 0))
    if shadow:
        sh = Image.new("RGBA", im.size, (0, 0, 0, 0))
        ImageDraw.Draw(sh).text((pad - l + L.px(3), pad - t + L.px(6)), text, font=f, fill=(0, 0, 0, 200))
        im = sh.filter(ImageFilter.GaussianBlur(L.px(9)))
    ImageDraw.Draw(im).text((pad - l, pad - t), text, font=f, fill=color)
    return im


def rounded_mask(size, radius: int) -> Image.Image:
    m = Image.new("L", (size[0] * 2, size[1] * 2), 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, size[0] * 2 - 1, size[1] * 2 - 1), radius=radius * 2, fill=255)
    return m.resize(size, Image.LANCZOS)


def pill(text: str, size: int, fg, bg, icon: Image.Image | None = None, pad_x: int = 22, h: int = 58) -> Image.Image:
    size, pad_x, h = L.px(size), L.px(pad_x), L.px(h)
    f = font(size, upright=True)
    l, t, r, b = f.getbbox(text)
    iw = icon.width + L.px(12) if icon else 0
    w = int(pad_x * 2 + iw + (r - l))
    im = Image.new("RGBA", (w, h), bg[:3] + (255,))
    alpha = bg[3] if len(bg) == 4 else 255
    im.putalpha(rounded_mask((w, h), h // 2).point(lambda v: v * alpha // 255))
    if icon:
        im.paste(icon, (pad_x - L.px(8), (h - icon.height) // 2), icon)
    ImageDraw.Draw(im).text((pad_x + iw - l, (h - (b - t)) // 2 - t), text, font=f, fill=fg)
    return im


def paste(base: Image.Image, layer: Image.Image, xy, opacity: float = 1.0) -> None:
    if opacity <= 0.01:
        return
    if opacity < 0.99:
        layer = layer.copy()
        layer.putalpha(layer.getchannel("A").point(lambda v: int(v * opacity)))
    base.paste(layer, (int(xy[0]), int(xy[1])), layer)


def scaled(layer: Image.Image, s: float) -> Image.Image:
    if abs(s - 1) < 0.005:
        return layer
    return layer.resize((max(1, int(layer.width * s)), max(1, int(layer.height * s))), Image.BILINEAR)


def glass(frame: Image.Image, box, radius: int, darken: float = 0.5, opacity: float = 1.0) -> None:
    """Frosted-glass panel: blurred + darkened copy of the background under the box."""
    x0, y0, x1, y1 = map(int, box)
    region = frame.crop((x0, y0, x1, y1))
    small = region.resize((max(1, region.width // 6), max(1, region.height // 6)), Image.BILINEAR)
    region = small.filter(ImageFilter.GaussianBlur(3)).resize(region.size, Image.BILINEAR)
    region = Image.blend(region, Image.new("RGB", region.size, DARK), darken)
    mask = rounded_mask(region.size, radius)
    if opacity < 0.99:
        mask = mask.point(lambda v: int(v * opacity))
    frame.paste(region, (x0, y0), mask)
    ImageDraw.Draw(frame, "RGBA").rounded_rectangle((x0, y0, x1 - 1, y1 - 1), radius=radius,
                                                    outline=(255, 255, 255, int(40 * opacity)), width=2)


def split_number(text: str):
    """'ÜBER 500.000 EURO' -> ('ÜBER ', 500000, ' EURO', uses_thousand_dots)."""
    m = re.match(r"^(\D*?)(\d{1,3}(?:\.\d{3})+|\d+)(?:,(\d+))?(.*)$", text)
    if not m or m.group(3):
        return None
    pre, num, _, post = m.groups()
    return pre, int(num.replace(".", "")), post, "." in num


def fmt_number(v: int, thousands: bool) -> str:
    return f"{v:,}".replace(",", ".") if thousands else str(v)


def label_for(text: str) -> str:
    for pat, label in UNIT_LABELS:
        if re.search(pat, text.upper()):
            return label
    return "FAKT"


# --- footage sources ------------------------------------------------------------------
class ImageSource:
    """Ken-Burns motion (slow zoom + pan) over a still image."""

    def __init__(self, path: Path, w: int, h: int, seed: int, flip: bool = False):
        im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
        if flip:
            im = ImageOps.mirror(im)
        scale = max(w / im.width, h / im.height) * 1.18
        self.im = im.resize((int(im.width * scale) + 1, int(im.height * scale) + 1), Image.LANCZOS)
        self.w, self.h = w, h
        self.zoom_in = seed % 2 == 0
        self.dx = (-1, 1)[seed % 3 == 0]

    def frame(self, p: float, punch: float = 0.0) -> Image.Image:
        z = ((1.0 + 0.10 * p) if self.zoom_in else (1.10 - 0.10 * p)) * (1 + punch)
        iw, ih = self.im.size
        cw, ch = min(self.w * 1.05 / z, iw), min(self.h * 1.05 / z, ih)
        cx = iw / 2 + self.dx * (iw - cw) / 2 * (p - 0.5) * 0.8
        cy = ih / 2
        return self.im.resize((self.w, self.h), Image.BILINEAR, box=(cx - cw / 2, cy - ch / 2, cx + cw / 2, cy + ch / 2))


class VideoSource:
    """Reads the planned part of a video: seek, crop/watermark filters, scale to the output size."""

    def __init__(self, clip: Clip, w: int, h: int, offset: float, dur: float):
        self.w, self.h = w, h
        _, _, src_dur = probe(clip.path)
        start = clip.start + offset
        if src_dur > 0.5:
            start %= src_dur
        vf = ",".join(x for x in (clip.vf, "hflip" if clip.flip else "",
                                  f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},fps={FPS}") if x)
        self.proc = subprocess.Popen([ffmpeg(), "-loglevel", "error", "-stream_loop", "-1", "-ss", f"{start:.3f}",
                                      "-i", str(clip.path), "-t", f"{dur + 1:.2f}", "-vf", vf, "-an", "-f", "rawvideo",
                                      "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE)
        self.last = Image.new("RGB", (w, h), BLACK)

    def frame(self, p: float, punch: float = 0.0) -> Image.Image:
        buf = self.proc.stdout.read(self.w * self.h * 3)
        if len(buf) == self.w * self.h * 3:
            self.last = Image.frombuffer("RGB", (self.w, self.h), buf)
        return self.last.copy()

    def close(self):
        self.proc.kill()


# --- subtitles ------------------------------------------------------------------------
def chunk_words(words: list[Word], max_words: int = 3, max_chars: int = 18) -> list[list[Word]]:
    chunks, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        chars = sum(len(x.text) + 1 for x in cur)
        if (nxt is None or len(cur) >= max_words or chars + len(nxt.text) > max_chars
                or nxt.start - w.end > 0.28 or w.text[-1:] in ".,!?:;"):
            chunks.append(cur)
            cur = []
    return chunks


@lru_cache(maxsize=256)
def subtitle_img(words: tuple[str, ...], active: int) -> Image.Image:
    """White words with soft shadow; the spoken word sits on a red rounded highlight."""
    size = fit_size(" ".join(words), min(L.sw - L.px(40), L.px(900)), L.px(82), L.px(46))
    f = font(size)
    space = f.getlength(" ") * 1.1
    widths = [f.getlength(w) for w in words]
    asc, desc = f.getmetrics()
    pad_x, pad_y, pad = L.px(16), L.px(8), L.px(30)
    total = sum(widths) + space * (len(words) - 1)
    shadow = Image.new("RGBA", (int(total + 2 * pad + 2 * pad_x), asc + desc + 2 * pad), (0, 0, 0, 0))
    ds = ImageDraw.Draw(shadow)
    x = pad + pad_x
    for w, ww in zip(words, widths):
        ds.text((x + L.px(3), pad + L.px(6)), w, font=f, fill=(0, 0, 0, 230))
        x += ww + space
    im = shadow.filter(ImageFilter.GaussianBlur(L.px(8)))
    d = ImageDraw.Draw(im)
    x = pad + pad_x
    for i, (w, ww) in enumerate(zip(words, widths)):
        if i == active:
            d.rounded_rectangle((x - pad_x, pad - pad_y + desc * 0.3, x + ww + pad_x, pad + asc + desc * 0.6 + pad_y),
                                radius=L.px(16), fill=RED)
        d.text((x, pad), w, font=f, fill=WHITE)
        x += ww + space
    return im


def draw_subtitles(frame: Image.Image, chunks: list[list[Word]], t: float) -> None:
    y_center = L.b - L.px(105)
    for ci, ch in enumerate(chunks):
        end = chunks[ci + 1][0].start if ci + 1 < len(chunks) else ch[-1].end + 0.4
        if ch[0].start <= t < end:
            active = max((i for i, w in enumerate(ch) if w.start <= t), default=0)
            img = scaled(subtitle_img(tuple(w.text.upper() for w in ch), active),
                         0.82 + 0.18 * ease_back((t - ch[0].start) / 0.16))
            paste(frame, img, (L.cx - img.width / 2, y_center - img.height / 2))
            return


# --- renderer -------------------------------------------------------------------------
class Renderer:
    def __init__(self, headline: list[str], subline: str, topic: str, n_body: int):
        self.headline = [h.strip().upper() for h in headline if h.strip()]
        self.subline = subline.strip().upper()
        self.n_body = max(n_body, 1)
        self.chip_brand = pill("CARPLUG", 30, WHITE, RED + (255,), icon=logo_icon(L.px(40)), pad_x=18)
        self.chip_topic = pill(topic, 28, WHITE, (20, 20, 22, 170)) if topic else None
        self.chip_news = pill("NEWS", 28, WHITE, (20, 20, 22, 170))
        # vignette + gradients pre-combined into ONE layer per scene type (much faster per frame)
        top = vgradient(int(L.h * 0.24), 160, 0)
        self.shade_body = self._combine(top, vgradient(int(L.h * 0.6), 0, 235, 1.4))
        self.shade_intro = self._combine(top, vgradient(int(L.h * 0.68), 0, 250, 1.2))
        # headline: fit the width, and never taller than ~55 % of the safe zone
        size = L.px(140)
        n = max(len(self.headline), 1)
        while size > L.px(40) and (any(font(size).getlength(h) > L.sw - L.px(10) for h in self.headline)
                                   or n * size * 1.02 > L.sh * 0.55):
            size -= 2
        self.head_layers = [text_img(h, font(size), RED if i == 1 else WHITE, shadow=True)
                            for i, h in enumerate(self.headline)]
        self.head_step = int(size * 1.02)
        self.sub_layer = text_img(self.subline, font(fit_size(self.subline, L.sw, L.px(40), L.px(22))), GREY) \
            if self.subline else None

    @staticmethod
    def _combine(top: Image.Image, bottom: Image.Image) -> Image.Image:
        layer = vignette().copy()
        layer.alpha_composite(top, (0, 0))
        layer.alpha_composite(bottom, (0, L.h - bottom.height))
        return layer

    def topbar(self, frame: Image.Image, body_index: int, p: float) -> None:
        """Story-style progress (one segment per scene; one continuous bar for long videos) + chips."""
        d = ImageDraw.Draw(frame, "RGBA")
        bar_h, gap = L.px(6), L.px(8)
        segments = self.n_body if self.n_body <= 12 else 1
        seg_w = (L.sw - gap * (segments - 1)) / segments
        for i in range(segments):
            x0 = L.l + i * (seg_w + gap)
            d.rounded_rectangle((x0, L.t, x0 + seg_w, L.t + bar_h), radius=bar_h // 2, fill=(255, 255, 255, 70))
            if segments == 1:
                fill, col = (body_index + p) / self.n_body, RED
            else:
                fill = 1.0 if i < body_index else (p if i == body_index else 0)
                col = WHITE if i < body_index else RED
            if fill > 0:
                d.rounded_rectangle((x0, L.t, x0 + max(bar_h, seg_w * fill), L.t + bar_h), radius=bar_h // 2, fill=col)
        y = L.t + L.px(26)
        paste(frame, self.chip_brand, (L.l, y))
        if self.chip_topic:
            paste(frame, self.chip_topic, (L.l + self.chip_brand.width + L.px(12), y))

    def intro(self, src, seg: Segment, lt: float) -> Image.Image:
        frame = src.frame(lt / max(seg.dur, 0.1)).copy()
        frame.paste(self.shade_intro, (0, 0), self.shade_intro)
        block_h = len(self.head_layers) * self.head_step + (self.sub_layer.height + L.px(20) if self.sub_layer else 0)
        y = L.b - block_h - L.px(10)
        bar = ease(lt / 0.35)
        ImageDraw.Draw(frame).rounded_rectangle((L.l, y - L.px(44), L.l + max(L.px(10), int(L.px(90) * bar)),
                                                 y - L.px(32)), radius=L.px(6), fill=RED)
        for i, layer in enumerate(self.head_layers):
            p = ease((lt - 0.06 - i * 0.09) / 0.4)
            paste(frame, layer, (L.l - L.px(24), y + i * self.head_step - L.px(24) + L.px(50) * (1 - p)), p)
        if self.sub_layer:
            paste(frame, self.sub_layer, (L.l - L.px(8), y + len(self.head_layers) * self.head_step + L.px(16)),
                  ease((lt - 0.4) / 0.35))
        cy = L.t + L.px(26)
        p = ease((lt - 0.15) / 0.35)
        paste(frame, self.chip_brand, (L.l - L.px(30) * (1 - p), cy), p)
        paste(frame, self.chip_news, (L.l + self.chip_brand.width + L.px(12) - L.px(30) * (1 - p), cy), p)
        return frame

    def body(self, src, seg: Segment, lt: float, body_index: int, chunks) -> Image.Image:
        frame = src.frame(lt / max(seg.dur, 0.1), 0.08 * (1 - ease(lt / 0.4)))
        frame.paste(self.shade_body, (0, 0), self.shade_body)
        self.topbar(frame, body_index, min(lt / seg.dur, 1.0))
        if seg.overlay:
            self.stat_card(frame, seg.overlay, lt)
        draw_subtitles(frame, chunks, lt)
        self.wipe(frame, lt)
        return frame

    def stat_card(self, frame: Image.Image, overlay: str, lt: float) -> None:
        p = ease((lt - 0.12) / 0.4)
        if p <= 0.01:
            return
        text = overlay.upper()
        num = split_number(text)
        if num:  # count-up animation for numbers
            pre, val, post, th = num
            shown = pre + fmt_number(int(round(val * ease((lt - 0.15) / 0.8))), th) + post
            final = pre + fmt_number(val, th) + post
        else:
            shown = final = text
        size = fit_size(final, min(L.sw - L.px(90), L.px(900)), L.px(128), L.px(50))
        big = self._big(shown, size)
        label = label_for(text)
        lab = self._label(label) if label != "FAKT" else None
        pad = L.px(28)
        lab_h = lab.height + L.px(8) if lab else 0
        w = max(self._big(final, size).width, (lab.width + L.px(22)) if lab else 0) + 2 * pad
        h = pad + lab_h + big.height + pad - L.px(10)
        x0, y0 = L.l, L.t + 0.55 * L.sh + L.px(40) * (1 - p)
        if y0 + h > L.b - L.px(190):  # keep clear of the subtitles
            y0 = L.b - L.px(190) - h
        glass(frame, (x0, y0, x0 + w, y0 + h), radius=L.px(30), darken=0.45, opacity=p)
        if lab:
            ImageDraw.Draw(frame).rounded_rectangle((x0 + pad, y0 + pad + L.px(6), x0 + pad + L.px(7),
                                                     y0 + pad + lab.height - L.px(6)), radius=L.px(3), fill=RED)
            paste(frame, lab, (x0 + pad + L.px(14), y0 + pad), p)
        paste(frame, big, (x0 + pad - L.px(8), y0 + pad + lab_h - L.px(4)), p)

    @lru_cache(maxsize=256)
    def _big(self, text: str, size: int) -> Image.Image:
        return text_img(text, font(size), WHITE)

    @lru_cache(maxsize=32)
    def _label(self, text: str) -> Image.Image:
        return text_img(" ".join(text), font(L.px(32), upright=True), (255, 85, 75))

    @staticmethod
    def wipe(frame: Image.Image, lt: float, dur: float = 0.3) -> None:
        """Diagonal CarPlug-red swipe over the cut between scenes."""
        if lt >= dur:
            return
        cx = -L.px(500) + (L.w + L.px(1000)) * ease(lt / dur)
        d = ImageDraw.Draw(frame)
        slant = L.h * 0.135
        for off, col, wd in ((-L.px(160), (150, 10, 10), L.px(120)), (0, RED, L.px(260))):
            x = cx + off
            d.polygon([(x - wd, L.h), (x + slant - wd, 0), (x + slant, 0), (x, L.h)], fill=col)

    def outro(self, lt: float) -> Image.Image:
        frame = red_glow().copy()
        logo = L.px(360)
        stack = logo + L.px(70 + 115 + 15 + 50 + 40 + 70)
        top = (L.t + L.b - stack) / 2
        cy = top + logo / 2
        p = ease_back(lt / 0.5)
        size = max(10, int(logo * (0.6 + 0.4 * p)))
        icon = logo_icon(logo).resize((size, size), Image.BILINEAR)
        ring = ease((lt % 1.6) / 1.6)
        r = logo * 0.55 + logo * 0.45 * ring
        ImageDraw.Draw(frame, "RGBA").ellipse((L.w / 2 - r, cy - r, L.w / 2 + r, cy + r),
                                              outline=RED + (int(180 * (1 - ring)),), width=L.px(6))
        paste(frame, icon, ((L.w - size) / 2, cy - size / 2), min(1.0, lt / 0.25))
        y = top + logo + L.px(70)
        q = ease((lt - 0.3) / 0.4)
        follow = self._big("FOLG CARPLUG", fit_size("FOLG CARPLUG", L.sw, L.px(104)))
        paste(frame, follow, ((L.w - follow.width) / 2, y - L.px(20) + L.px(40) * (1 - q)), q)
        y += L.px(115 + 15)
        tag = text_img("Täglich neue Auto-News", font(L.px(42), upright=True), GREY)
        paste(frame, tag, ((L.w - tag.width) / 2, y), ease((lt - 0.45) / 0.4))
        y += L.px(50 + 40)
        url = pill(BRAND_URL, 34, WHITE, RED + (255,), h=70, pad_x=30)
        q = ease((lt - 0.6) / 0.4)
        paste(frame, url, ((L.w - url.width) / 2, y + L.px(30) * (1 - q)), q)
        return frame


# --- main entry -------------------------------------------------------------------------
UNIT_SECONDS = 3.0  # work is split into pieces of ~3 s that are rendered in parallel


@dataclass
class Unit:
    """One piece of work for a render process."""
    index: int
    seg: Segment
    seg_index: int
    body_index: int
    k0: int           # first local frame of the segment in this unit
    k1: int           # end (exclusive)
    out: str
    cover: str | None
    size: tuple[int, int]
    headline: list[str]
    subline: str
    topic: str
    n_body: int


_RENDERER_CACHE: dict = {}


def _render_unit(u: Unit) -> tuple[int, int]:
    set_layout_if_needed(*u.size)
    key = (u.size, tuple(u.headline), u.subline, u.topic, u.n_body)
    r = _RENDERER_CACHE.get(key)
    if r is None:
        _RENDERER_CACHE.clear()
        r = _RENDERER_CACHE[key] = Renderer(u.headline, u.subline, u.topic, u.n_body)
    seg = u.seg
    src = None
    if seg.kind != "outro":
        src = (VideoSource(seg.clip, L.w, L.h, u.k0 / FPS, (u.k1 - u.k0) / FPS) if seg.clip.kind == "video"
               else ImageSource(seg.clip.path, L.w, L.h, seed=u.seg_index, flip=seg.clip.flip))
    chunks = chunk_words(seg.words)
    cmd = [ffmpeg(), "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{L.w}x{L.h}",
           "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-threads", "2",
           "-pix_fmt", "yuv420p", "-g", str(FPS * 2), u.out]
    enc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    try:
        for k in range(u.k0, u.k1):
            lt = k / FPS
            if seg.kind == "intro":
                frame = r.intro(src, seg, lt)
                if u.cover and abs(lt - 1.2) < 0.5 / FPS:
                    frame.save(u.cover)
            elif seg.kind == "body":
                frame = r.body(src, seg, lt, u.body_index, chunks)
            else:
                frame = r.outro(lt)
            enc.stdin.write(frame.tobytes())
    finally:
        enc.stdin.close()
        enc.wait()
        if isinstance(src, VideoSource):
            src.close()
    if enc.returncode != 0:
        raise RuntimeError("Video-Encoding fehlgeschlagen.")
    return u.index, u.k1 - u.k0


def set_layout_if_needed(w: int, h: int) -> None:
    if (L.w, L.h) != (w - w % 2, h - h % 2):
        set_layout(w, h)


def workers() -> int:
    return max(1, min(8, (os.cpu_count() or 2)))


def render(script, clips: list[Clip], voices: list[tuple[np.ndarray, list[Word]]], music_mix_fn,
           out_mp4: Path, cover_png: Path, work: Path, size: tuple[int, int] = (1080, 1920),
           min_total: float | None = None, log=print) -> float:
    """Build timeline, mix audio, render all pieces in parallel, join them. Returns duration in seconds."""
    set_layout(*size)
    segs, total = timeline(script, clips, voices, min_total)

    voice_track = np.zeros(int(total * SR) + SR, np.float32)
    for seg, (audio, _) in zip(segs, voices):
        lead = 0.1 if seg.kind == "body" else 0.0
        p = int((seg.start + lead) * SR)
        voice_track[p:p + len(audio)] += audio
        for w in seg.words:
            w.start += lead
            w.end += lead
    mixed = music_mix_fn(voice_track, total, [s.start - 0.15 for s in segs[1:]])
    wav = work / "mix.wav"
    write_wav(wav, mixed[: int(total * SR)])

    topic = next((s.query for s in script.scenes if s.query), "").upper()[:22]
    n_body = sum(s.kind == "body" for s in segs)
    units, body_index = [], -1
    for i, seg in enumerate(segs):
        if seg.kind == "body":
            body_index += 1
        n_frames = int(round((seg.start + seg.dur) * FPS)) - int(round(seg.start * FPS))
        step = int(UNIT_SECONDS * FPS)
        for k0 in range(0, n_frames, step):
            k1 = min(n_frames, k0 + step)
            if n_frames - k1 < FPS // 2:  # avoid tiny leftovers
                k1 = n_frames
            units.append(Unit(len(units), seg, i, body_index, k0, k1, str(work / f"part_{len(units):04d}.mp4"),
                              str(cover_png) if seg.kind == "intro" and k0 <= 1.2 * FPS < k1 else None,
                              (L.w, L.h), script.headline, script.subline, topic, n_body))
            if k1 == n_frames:
                break

    n_workers = workers()
    log(f"Rendern auf {n_workers} Prozessorkernen…")
    done, frames_total = 0, int(round(total * FPS))
    with ProcessPoolExecutor(max_workers=n_workers, mp_context=mp.get_context("spawn")) as ex:
        futures = [ex.submit(_render_unit, u) for u in units]
        for f in as_completed(futures):
            _, n = f.result()
            done += n
            log(f"Rendern… {100 * done / frames_total:.0f} % ({done // FPS} von {int(total)} s)")

    lst = work / "parts.txt"
    lst.write_text("".join(f"file '{Path(u.out).as_posix()}'\n" for u in units), encoding="utf-8")
    res = subprocess.run([ffmpeg(), "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
                          "-i", str(wav), "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                          "-shortest", "-movflags", "+faststart", str(out_mp4)], capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError("Zusammenfügen fehlgeschlagen: " + res.stderr[-400:])
    if not cover_png.exists() and segs[0].clip.kind == "image":
        r = Renderer(script.headline, script.subline, topic, n_body)
        r.intro(ImageSource(segs[0].clip.path, L.w, L.h, 0), segs[0], 1.5).save(cover_png)
    return total


def timeline(script, clips, voices, min_total):
    pause, outro_extra = stretch_plan(voices, min_total)
    segs, t = [], 0.0
    n = len(script.scenes)
    for i, (sc, (audio, words)) in enumerate(zip(script.scenes, voices)):
        kind = "intro" if i == 0 else "outro" if i == n - 1 else "body"
        dur = len(audio) / SR + (OUTRO_TAIL + outro_extra if kind == "outro" else
                                 LEAD + (pause if kind == "body" else 0))
        segs.append(Segment(kind, t, dur, clips[i], sc.overlay, [Word(w.text, w.start, w.end) for w in words]))
        t += dur
    return segs, t


def scene_durations(voices, min_total) -> list[float]:
    """Screen time of every scene (used to plan footage before rendering)."""
    pause, outro_extra = stretch_plan(voices, min_total)
    n = len(voices)
    return [len(a) / SR + (OUTRO_TAIL + outro_extra if i == n - 1 else LEAD + (pause if i else 0))
            for i, (a, _) in enumerate(voices)]
