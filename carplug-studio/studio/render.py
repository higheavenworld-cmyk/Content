"""Renders the final 9:16 video in CarPlug branding (motion graphics, subtitles, music).

Every text element stays inside the platform SAFE zone, so nothing is hidden behind the
TikTok / Reels / Shorts interface (top bar, right-hand buttons, caption area at the bottom).
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from .config import BLACK, BRAND_URL, FONT_BOLD, FONT_HEADLINE, FPS, GREY, H, LOGO_FILE, RED, W, WHITE
from .footage import Clip
from .voice import SR, Word, ffmpeg, write_wav

# Safe zone (1080x1920): top bar ~240 px, right button column ~170 px, caption area ~480 px.
SAFE_L, SAFE_T, SAFE_R, SAFE_B = 64, 250, 910, 1440
SAFE_W = SAFE_R - SAFE_L
SAFE_CX = (SAFE_L + SAFE_R) // 2
GAP = 0.12  # pause between scenes (seconds)
DARK = (18, 18, 20)

UNIT_LABELS = [(r"PS|KW\b", "LEISTUNG"), (r"KM/H", "TOPSPEED"), (r"NM\b", "DREHMOMENT"), (r"KWH", "AKKU"),
               (r"EURO|€", "PREIS"), (r"KILOMETER|\bKM\b", "REICHWEITE"), (r"SEKUNDEN", "SPRINT"),
               (r"TONNEN|KILO|KG", "GEWICHT"), (r"ZYLINDER|LITER", "MOTOR"), (r"PROZENT|%", "ANTEIL"),
               (r"ZOLL", "FELGEN")]


@dataclass
class Segment:
    kind: str                 # intro | body | outro
    start: float
    dur: float
    clip: Clip | None
    overlay: str = ""
    words: list[Word] = field(default_factory=list)


# --- helpers ---------------------------------------------------------------------
def ease(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return 1 - (1 - x) ** 3


def ease_back(x: float) -> float:
    """Ease-out with a small overshoot – gives the 'pop' feeling."""
    x = min(max(x, 0.0), 1.0)
    c = 1.70158
    return 1 + (c + 1) * (x - 1) ** 3 + c * (x - 1) ** 2


@lru_cache(maxsize=64)
def font(size: int, upright: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_BOLD if upright else FONT_HEADLINE), size)


def fit_size(text: str, max_w: int, size: int, min_size: int = 36, upright: bool = False) -> int:
    while size > min_size and font(size, upright).getlength(text) > max_w:
        size -= 2
    return size


@lru_cache(maxsize=8)
def logo_icon(size: int) -> Image.Image:
    im = Image.open(LOGO_FILE).convert("RGB").resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size * 4, size * 4), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size * 4 - 1, size * 4 - 1), radius=size * 4 // 5, fill=255)
    im.putalpha(mask.resize((size, size), Image.LANCZOS))
    return im


@lru_cache(maxsize=8)
def vgradient(h: int, a0: int, a1: int, power: float = 1.0) -> Image.Image:
    a = a0 + (a1 - a0) * np.linspace(0, 1, h, dtype=np.float32) ** power
    g = Image.new("RGBA", (W, h), BLACK + (0,))
    g.putalpha(Image.fromarray(np.repeat(a[:, None], W, axis=1).astype(np.uint8), "L"))
    return g


@lru_cache(maxsize=1)
def vignette() -> Image.Image:
    y, x = np.mgrid[0:H, 0:W].astype(np.float32)
    d = np.sqrt(((x - W / 2) / (W * 0.75)) ** 2 + ((y - H / 2) / (H * 0.7)) ** 2)
    a = (np.clip(d - 0.45, 0, 1) * 200).astype(np.uint8)
    v = Image.new("RGBA", (W, H), BLACK + (0,))
    v.putalpha(Image.fromarray(a, "L"))
    return v


@lru_cache(maxsize=1)
def red_glow() -> Image.Image:
    y, x = np.mgrid[0:H, 0:W].astype(np.float32)
    d = np.sqrt(((x - W / 2) / 620) ** 2 + ((y - 760) / 620) ** 2)
    a = np.clip(1 - d, 0, 1) ** 2
    img = np.zeros((H, W, 3), np.float32)
    img[:] = BLACK
    img += a[..., None] * (np.array(RED, np.float32) - BLACK) * 0.55
    return Image.fromarray(img.astype(np.uint8), "RGB")


def text_img(text: str, f: ImageFont.FreeTypeFont, color, shadow: bool = False) -> Image.Image:
    l, t, r, b = f.getbbox(text)
    pad = 24 if shadow else 8
    im = Image.new("RGBA", (r - l + 2 * pad + 10, b - t + 2 * pad), (0, 0, 0, 0))
    if shadow:
        sh = Image.new("RGBA", im.size, (0, 0, 0, 0))
        ImageDraw.Draw(sh).text((pad - l + 3, pad - t + 6), text, font=f, fill=(0, 0, 0, 200))
        im = sh.filter(ImageFilter.GaussianBlur(9))
    ImageDraw.Draw(im).text((pad - l, pad - t), text, font=f, fill=color)
    return im


def rounded_mask(size, radius: int) -> Image.Image:
    m = Image.new("L", (size[0] * 2, size[1] * 2), 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, size[0] * 2 - 1, size[1] * 2 - 1), radius=radius * 2, fill=255)
    return m.resize(size, Image.LANCZOS)


def pill(text: str, size: int, fg, bg, icon: Image.Image | None = None, pad_x: int = 22,
         h: int = 58) -> Image.Image:
    f = font(size, upright=True)
    l, t, r, b = f.getbbox(text)
    iw = icon.width + 12 if icon else 0
    w = int(pad_x * 2 + iw + (r - l))
    im = Image.new("RGBA", (w, h), bg)
    im.putalpha(Image.eval(rounded_mask((w, h), h // 2), lambda v: v * bg[3] // 255 if len(bg) == 4 else v))
    d = ImageDraw.Draw(im)
    if icon:
        im.paste(icon, (pad_x - 8, (h - icon.height) // 2), icon)
    d.text((pad_x + iw - l, (h - (b - t)) // 2 - t), text, font=f, fill=fg)
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


def glass(frame: Image.Image, box, radius: int = 30, darken: float = 0.5, opacity: float = 1.0) -> None:
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
    """'ÜBER 500.000 EURO' -> ('ÜBER ', 500000, ' EURO', fmt)."""
    m = re.match(r"^(\D*?)(\d{1,3}(?:\.\d{3})+|\d+)(?:,(\d+))?(.*)$", text)
    if not m or m.group(3):
        return None
    pre, num, _, post = m.groups()
    thousands = "." in num
    return pre, int(num.replace(".", "")), post, thousands


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

    def __init__(self, path: Path, w: int, h: int, seed: int):
        im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
        scale = max(w / im.width, h / im.height) * 1.18
        self.im = im.resize((int(im.width * scale) + 1, int(im.height * scale) + 1), Image.LANCZOS)
        self.w, self.h = w, h
        self.zoom_in = seed % 2 == 0
        self.dx = (-1, 1)[seed % 3 == 0]

    def frame(self, p: float, punch: float = 0.0) -> Image.Image:
        z = (1.0 + 0.10 * p) if self.zoom_in else (1.10 - 0.10 * p)
        z *= 1 + punch
        iw, ih = self.im.size
        cw, ch = min(self.w * 1.05 / z, iw), min(self.h * 1.05 / z, ih)
        cx = iw / 2 + self.dx * (iw - cw) / 2 * (p - 0.5) * 0.8
        cy = ih / 2
        return self.im.resize((self.w, self.h), Image.BILINEAR, box=(cx - cw / 2, cy - ch / 2, cx + cw / 2, cy + ch / 2))


class VideoSource:
    def __init__(self, path: Path, w: int, h: int, dur: float):
        self.w, self.h = w, h
        vf = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},fps={FPS}"
        self.proc = subprocess.Popen([ffmpeg(), "-loglevel", "error", "-stream_loop", "-1", "-i", str(path),
                                      "-t", f"{dur + 1:.2f}", "-vf", vf, "-an", "-f", "rawvideo",
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
    size = fit_size(" ".join(words), SAFE_W - 40, 82, 50)
    f = font(size)
    space = f.getlength(" ") * 1.1
    widths = [f.getlength(w) for w in words]
    asc, desc = f.getmetrics()
    pad_x, pad_y, pad = 16, 8, 30
    total = sum(widths) + space * (len(words) - 1)
    im = Image.new("RGBA", (int(total + 2 * pad + 2 * pad_x), asc + desc + 2 * pad), (0, 0, 0, 0))
    shadow = Image.new("RGBA", im.size, (0, 0, 0, 0))
    ds = ImageDraw.Draw(shadow)
    x = pad + pad_x
    for w, ww in zip(words, widths):
        ds.text((x + 3, pad + 6), w, font=f, fill=(0, 0, 0, 230))
        x += ww + space
    im = shadow.filter(ImageFilter.GaussianBlur(8))
    d = ImageDraw.Draw(im)
    x = pad + pad_x
    for i, (w, ww) in enumerate(zip(words, widths)):
        if i == active:
            d.rounded_rectangle((x - pad_x, pad - pad_y + desc * 0.3, x + ww + pad_x, pad + asc + desc * 0.6 + pad_y),
                                radius=16, fill=RED)
        d.text((x, pad), w, font=f, fill=WHITE)
        x += ww + space
    return im


def draw_subtitles(frame: Image.Image, chunks: list[list[Word]], t: float, y_center: int = 1335) -> None:
    for ci, ch in enumerate(chunks):
        end = chunks[ci + 1][0].start if ci + 1 < len(chunks) else ch[-1].end + 0.4
        if ch[0].start <= t < end:
            active = max((i for i, w in enumerate(ch) if w.start <= t), default=0)
            img = scaled(subtitle_img(tuple(w.text.upper() for w in ch), active),
                         0.82 + 0.18 * ease_back((t - ch[0].start) / 0.16))
            paste(frame, img, (SAFE_CX - img.width / 2, y_center - img.height / 2))
            return


# --- renderer -------------------------------------------------------------------------
class Renderer:
    def __init__(self, headline: list[str], subline: str, topic: str, n_body: int):
        self.headline = [h.strip().upper() for h in headline if h.strip()]
        self.subline = subline.strip().upper()
        self.n_body = max(n_body, 1)
        self.chip_brand = pill("CARPLUG", 30, WHITE, RED + (255,), icon=logo_icon(40), pad_x=18)
        self.chip_topic = pill(topic or "AUTO-NEWS", 28, WHITE, (20, 20, 22, 170)) if topic else None
        self.chip_news = pill("NEWS", 28, WHITE, (20, 20, 22, 170))
        self.grad_top = vgradient(460, 160, 0)
        self.grad_bottom = vgradient(1160, 0, 235, 1.4)
        self.grad_intro = vgradient(1300, 0, 250, 1.2)
        size = 140
        while size > 64 and any(font(size).getlength(h) > SAFE_W - 10 for h in self.headline):
            size -= 2
        self.head_layers = [text_img(h, font(size), RED if i == 1 else WHITE, shadow=True)
                            for i, h in enumerate(self.headline)]
        self.head_step = int(size * 1.02)
        self.sub_layer = text_img(self.subline, font(fit_size(self.subline, SAFE_W, 40, 24)), GREY) \
            if self.subline else None

    # shared top bar: story-style segments + chips (inside the safe zone)
    def topbar(self, frame: Image.Image, body_index: int, p: float, chips_opacity: float = 1.0) -> None:
        d = ImageDraw.Draw(frame, "RGBA")
        gap = 8
        seg_w = (SAFE_W - gap * (self.n_body - 1)) / self.n_body
        for i in range(self.n_body):
            x0 = SAFE_L + i * (seg_w + gap)
            d.rounded_rectangle((x0, SAFE_T, x0 + seg_w, SAFE_T + 6), radius=3, fill=(255, 255, 255, 70))
            fill = 1.0 if i < body_index else (p if i == body_index else 0)
            if fill > 0:
                d.rounded_rectangle((x0, SAFE_T, x0 + max(6, seg_w * fill), SAFE_T + 6), radius=3,
                                    fill=WHITE if i < body_index else RED)
        y = SAFE_T + 26
        paste(frame, self.chip_brand, (SAFE_L, y), chips_opacity)
        if self.chip_topic:
            paste(frame, self.chip_topic, (SAFE_L + self.chip_brand.width + 12, y), chips_opacity)

    def intro(self, src, seg: Segment, lt: float) -> Image.Image:
        frame = src.frame(lt / max(seg.dur, 0.1)).copy()
        paste(frame, vignette(), (0, 0))
        paste(frame, self.grad_top, (0, 0))
        paste(frame, self.grad_intro, (0, H - 1300))
        # headline block is bottom-anchored to the safe zone
        block_h = len(self.head_layers) * self.head_step + (self.sub_layer.height + 20 if self.sub_layer else 0)
        y = SAFE_B - block_h - 10
        d = ImageDraw.Draw(frame)
        bar = ease(lt / 0.35)
        d.rounded_rectangle((SAFE_L, y - 44, SAFE_L + max(10, int(90 * bar)), y - 32), radius=6, fill=RED)
        for i, layer in enumerate(self.head_layers):
            p = ease((lt - 0.06 - i * 0.09) / 0.4)
            paste(frame, layer, (SAFE_L - 24, y + i * self.head_step - 24 + 50 * (1 - p)), p)
        if self.sub_layer:
            p = ease((lt - 0.4) / 0.35)
            paste(frame, self.sub_layer, (SAFE_L - 8, y + len(self.head_layers) * self.head_step + 16), p)
        # chips: brand + NEWS
        cy = SAFE_T + 26
        p = ease((lt - 0.15) / 0.35)
        paste(frame, self.chip_brand, (SAFE_L - 30 * (1 - p), cy), p)
        paste(frame, self.chip_news, (SAFE_L + self.chip_brand.width + 12 - 30 * (1 - p), cy), p)
        return frame

    def body(self, src, seg: Segment, lt: float, body_index: int, chunks) -> Image.Image:
        punch = 0.08 * (1 - ease(lt / 0.4))
        frame = src.frame(lt / max(seg.dur, 0.1), punch)
        paste(frame, vignette(), (0, 0))
        paste(frame, self.grad_top, (0, 0))
        paste(frame, self.grad_bottom, (0, H - 1160))
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
            c = ease((lt - 0.15) / 0.8)
            shown = pre + fmt_number(int(round(val * c)), th) + post
            final = pre + fmt_number(val, th) + post
        else:
            shown = final = text
        size = fit_size(final, SAFE_W - 90, 128, 60)
        big = self._big(shown, size)
        label = label_for(text)
        lab = self._label(label) if label != "FAKT" else None
        pad = 28
        lab_h = lab.height + 8 if lab else 0
        w = max(self._big(final, size).width, (lab.width + 22) if lab else 0) + 2 * pad
        h = pad + lab_h + big.height + pad - 10
        x0, y0 = SAFE_L, 900 + 40 * (1 - p)
        glass(frame, (x0, y0, x0 + w, y0 + h), radius=30, darken=0.45, opacity=p)
        if lab:
            ImageDraw.Draw(frame).rounded_rectangle((x0 + pad, y0 + pad + 6, x0 + pad + 7, y0 + pad + lab.height - 6),
                                                    radius=3, fill=RED)
            paste(frame, lab, (x0 + pad + 14, y0 + pad), p)
        paste(frame, big, (x0 + pad - 8, y0 + pad + lab_h - 4), p)

    @lru_cache(maxsize=256)
    def _big(self, text: str, size: int) -> Image.Image:
        return text_img(text, font(size), WHITE)

    @lru_cache(maxsize=32)
    def _label(self, text: str) -> Image.Image:
        return text_img(" ".join(text), font(32, upright=True), (255, 85, 75))

    @staticmethod
    def wipe(frame: Image.Image, lt: float, dur: float = 0.3) -> None:
        """Diagonal CarPlug-red swipe that covers the cut between scenes."""
        if lt >= dur:
            return
        p = lt / dur
        cx = -500 + (W + 1000) * ease(p)
        d = ImageDraw.Draw(frame)
        for off, col, wd in ((-160, (150, 10, 10), 120), (0, RED, 260)):
            x = cx + off
            d.polygon([(x - wd, H), (x + 260 - wd, 0), (x + 260, 0), (x, H)], fill=col)

    def outro(self, lt: float) -> Image.Image:
        frame = red_glow().copy()
        p = ease_back(lt / 0.5)
        size = max(10, int(360 * (0.6 + 0.4 * p)))
        icon = logo_icon(360).resize((size, size), Image.BILINEAR)
        d = ImageDraw.Draw(frame, "RGBA")
        ring = ease((lt % 1.6) / 1.6)
        r = 200 + 160 * ring
        d.ellipse((W / 2 - r, 760 - r, W / 2 + r, 760 + r), outline=RED + (int(180 * (1 - ring)),), width=6)
        paste(frame, icon, ((W - size) / 2, 760 - size / 2), min(1.0, lt / 0.25))
        q = ease((lt - 0.3) / 0.4)
        follow = self._big("FOLG CARPLUG", 104)
        paste(frame, follow, ((W - follow.width) / 2, 1010 + 40 * (1 - q)), q)
        tag = text_img("Täglich neue Auto-News", font(42, upright=True), GREY)
        paste(frame, tag, ((W - tag.width) / 2, 1140), ease((lt - 0.45) / 0.4))
        url = pill(BRAND_URL, 34, WHITE, RED + (255,), h=70, pad_x=30)
        paste(frame, url, ((W - url.width) / 2, 1230 + 30 * (1 - ease((lt - 0.6) / 0.4))), ease((lt - 0.6) / 0.4))
        return frame


# --- main entry -------------------------------------------------------------------------
def render(script, clips: list[Clip], voices: list[tuple[np.ndarray, list[Word]]], music_mix_fn,
           out_mp4: Path, cover_png: Path, work: Path, log=print) -> float:
    """Build timeline, mix audio, render frames, encode MP4. Returns duration in seconds."""
    segs, t = [], 0.0
    n = len(script.scenes)
    for i, (sc, (audio, words)) in enumerate(zip(script.scenes, voices)):
        kind = "intro" if i == 0 else "outro" if i == n - 1 else "body"
        dur = len(audio) / SR + (1.0 if kind == "outro" else GAP + 0.15)
        segs.append(Segment(kind, t, dur, clips[i], sc.overlay, words))
        t += dur
    total = t

    # audio: voice clips at their segment start, then music + whooshes at every cut
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
    r = Renderer(script.headline, script.subline, topic, sum(s.kind == "body" for s in segs))
    cmd = [ffmpeg(), "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
           "-r", str(FPS), "-i", "-", "-i", str(wav), "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
           "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart",
           str(out_mp4)]
    enc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    frames_total = int(total * FPS)
    done, body_index = 0, -1
    try:
        for i, seg in enumerate(segs):
            src = None
            if seg.kind != "outro":
                src = (VideoSource(seg.clip.path, W, H, seg.dur) if seg.clip.kind == "video"
                       else ImageSource(seg.clip.path, W, H, seed=i))
            if seg.kind == "body":
                body_index += 1
            chunks = chunk_words(seg.words)
            for k in range(int(round((seg.start + seg.dur) * FPS)) - int(round(seg.start * FPS))):
                lt = k / FPS
                if seg.kind == "intro":
                    frame = r.intro(src, seg, lt)
                    if abs(lt - 1.2) < 0.5 / FPS or (k == 0 and seg.dur < 1.2):
                        frame.save(cover_png)
                elif seg.kind == "body":
                    frame = r.body(src, seg, lt, body_index, chunks)
                else:
                    frame = r.outro(lt)
                enc.stdin.write(frame.tobytes())
                done += 1
                if done % (FPS * 3) == 0:
                    log(f"Rendern… {100 * done / frames_total:.0f} %")
            if isinstance(src, VideoSource):
                src.close()
    finally:
        enc.stdin.close()
        enc.wait()
    if enc.returncode != 0:
        raise RuntimeError("Video-Encoding fehlgeschlagen.")
    if not cover_png.exists() and segs[0].clip.kind == "image":
        r.intro(ImageSource(segs[0].clip.path, W, H, 0), segs[0], 1.5).save(cover_png)
    return total
