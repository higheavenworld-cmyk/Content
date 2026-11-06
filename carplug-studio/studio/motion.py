"""Doc-edit look ("ADHS-Edit"): word-by-word captions, own motion-graphic shots, quick transitions, film look.

Everything here is drawn by the program itself – it is CarPlug's own creative layer on top of the footage.
"""
from __future__ import annotations

import random
import re
from functools import lru_cache

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from . import render as R
from .config import BLACK, FONT_BOLD, FONT_HEADLINE, FONT_SERIF, RED, WHITE

PAPER = (243, 239, 231)
INK = (24, 22, 20)
NUM_RED = (255, 64, 52)
SMALL_WORDS = set("""der die das ein eine einen einem einer eines dem den des und oder mit von für auf ist sind zu
im in am an bei aus als wie auch noch nur so es er sie wir du ich man hat haben wird werden war waren sich nicht ja
aber dass weil wenn dann denn doch mehr sehr schon um bis über unter nach vor durch gegen ohne ab zum zur vom beim
hier da diese dieser dieses diesen jetzt kann können soll sollen muss müssen will wollen dieser jeder alle""".split())


# --- word-by-word captions ---------------------------------------------------------------
def word_style(word: str) -> str:
    core = re.sub(r"[^\w€%/-]", "", word)
    if not core:
        return "small"
    if any(ch.isdigit() for ch in core) or core.upper() in {"PS", "KW", "KM/H", "NM", "KWH", "€", "EURO"}:
        return "num"
    if core.lower() in SMALL_WORDS:
        return "small"
    if core[0].isupper() or len(core) >= 8:  # German nouns are capitalised → the words that carry meaning
        return "key"
    return "small"


@lru_cache(maxsize=1024)
def word_img(text: str, style: str, scale: float, dark: bool) -> tuple[Image.Image, int]:
    """(image, baseline y) of one word with soft glow."""
    s = R.L.s * scale
    if style == "key":
        f = ImageFont.truetype(str(FONT_SERIF), max(10, int(150 * s)))
        color = INK if dark else WHITE
    elif style == "num":
        f = ImageFont.truetype(str(FONT_HEADLINE), max(10, int(112 * s)))
        color = NUM_RED
    else:
        f = ImageFont.truetype(str(FONT_BOLD), max(8, int(56 * s)))
        color = (90, 84, 78) if dark else (236, 236, 236)
    asc, desc = f.getmetrics()
    pad = int(30 * s) + 4
    w = int(f.getlength(text)) + 2 * pad
    h = asc + desc + 2 * pad
    base = pad + asc
    glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    g = ImageDraw.Draw(glow)
    if dark:
        g.text((pad, base + int(4 * s)), text, font=f, fill=(0, 0, 0, 60), anchor="ls")
        glow = glow.filter(ImageFilter.GaussianBlur(max(1, int(6 * s))))
    else:
        g.text((pad + int(2 * s), base + int(5 * s)), text, font=f, fill=(0, 0, 0, 210), anchor="ls")
        glow = glow.filter(ImageFilter.GaussianBlur(max(1, int(10 * s))))
        halo = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ImageDraw.Draw(halo).text((pad, base), text, font=f, fill=color[:3] + (70,), anchor="ls")
        glow.alpha_composite(halo.filter(ImageFilter.GaussianBlur(max(1, int(14 * s)))))
    ImageDraw.Draw(glow).text((pad, base), text, font=f, fill=color, anchor="ls")
    return glow, base


def draw_kinetic(frame: Image.Image, chunks, t: float, y_center: float | None = None, dark: bool = False,
                 scale: float = 1.0, reveal_all: bool = False) -> None:
    """Words appear exactly when spoken; the current phrase builds up word by word (doc-edit captions)."""
    L = R.L
    y_center = y_center if y_center is not None else L.t + 0.58 * L.sh
    if chunks and t < chunks[0][0].start:  # the first word is already there in the very first frame
        t = chunks[0][0].start
    for ci, ch in enumerate(chunks):
        end = chunks[ci + 1][0].start if ci + 1 < len(chunks) else ch[-1].end + 0.35
        if not (ch[0].start - 0.02 <= t < end):
            continue
        visible = ch if reveal_all else ([w for w in ch if w.start <= t + 0.02] or ch[:1])
        items = []
        for k, w in enumerate(visible):
            img, base = word_img(w.text, word_style(w.text), scale, dark)
            age = t - w.start
            p = R.ease(age / 0.14) if (k == len(visible) - 1 and not reveal_all) else 1.0
            items.append((img, base, p))
        max_w = L.sw * 0.98
        lines, cur, cur_w = [], [], 0.0
        for it in items:
            iw = it[0].width - L.px(40)
            if cur and cur_w + iw > max_w:
                lines.append(cur)
                cur, cur_w = [], 0.0
            cur.append(it)
            cur_w += iw
        lines.append(cur)
        heights = [max(i[1] for i in ln) + max(i[0].height - i[1] for i in ln) for ln in lines]
        y = y_center - sum(h * 0.8 for h in heights) / 2
        for ln, lh in zip(lines, heights):
            total = sum(i[0].width - L.px(40) for i in ln)
            x = L.cx - total / 2 - L.px(20)
            top = max(i[1] for i in ln)
            for img, base, p in ln:
                im = img if p >= 0.99 else R.scaled(img, 0.9 + 0.1 * p)
                dy = (1 - p) * L.px(18)
                R.paste(frame, im, (x + (img.width - im.width) / 2, y + top - base * im.height / img.height + dy), p)
                x += img.width - L.px(40)
            y += lh * 0.8
        return


# --- motion-graphic shots -------------------------------------------------------------------
@lru_cache(maxsize=2)
def paper() -> Image.Image:
    L = R.L
    rng = np.random.default_rng(7)
    base = np.zeros((L.h, L.w, 3), np.float32) + np.array(PAPER, np.float32)
    base += rng.normal(0, 5, (L.h, L.w, 1))  # paper grain
    yy, xx = np.mgrid[0:L.h, 0:L.w]
    d = np.sqrt(((xx - L.w / 2) / L.w) ** 2 + ((yy - L.h / 2) / L.h) ** 2)
    base *= (1 - 0.18 * np.clip(d - 0.2, 0, 1))[..., None]
    return Image.fromarray(np.clip(base, 0, 255).astype(np.uint8), "RGB")


@lru_cache(maxsize=2)
def hud_grid() -> Image.Image:
    L = R.L
    im = Image.new("RGB", (L.w, L.h), (10, 12, 16))
    d = ImageDraw.Draw(im)
    step = L.px(54)
    for x in range(0, L.w, step):
        d.line((x, 0, x, L.h), fill=(22, 26, 32), width=1)
    for y in range(0, L.h, step):
        d.line((0, y, L.w, y), fill=(22, 26, 32), width=1)
    return im


def _particles(frame: Image.Image, t: float, seed: int, color=(255, 255, 255), n: int = 70) -> None:
    L = R.L
    rng = random.Random(seed)
    d = ImageDraw.Draw(frame, "RGBA")
    for _ in range(n):
        x0, y0 = rng.random() * L.w, rng.random() * L.h
        vx, vy = (rng.random() - 0.5) * 60, -20 - rng.random() * 60
        r = L.px(1 + rng.random() * 3)
        a = int(60 + 150 * rng.random())
        x, y = (x0 + vx * t) % L.w, (y0 + vy * t) % L.h
        d.ellipse((x - r, y - r, x + r, y + r), fill=color + (a,))


def typo_card(st: float) -> Image.Image:
    """Cream paper card – the caption itself becomes the picture (dark text is drawn on top by the caller)."""
    L = R.L
    z = 1.0 + 0.03 * st
    im = paper()
    if z > 1.001:
        cw, ch = L.w / z, L.h / z
        im = im.resize((L.w, L.h), Image.BILINEAR, box=((L.w - cw) / 2, (L.h - ch) / 2, (L.w + cw) / 2, (L.h + ch) / 2))
    return im.copy()


def number_card(text: str, label: str, st: float, seed: int) -> Image.Image:
    """Big number counting up on black with a red glow and floating particles."""
    L = R.L
    frame = R.red_glow().copy()
    _particles(frame, st, seed, (255, 120, 100), 50)
    num = R.split_number(text.upper())
    if num:
        pre, val, post, th = num
        shown_num = R.fmt_number(int(round(val * R.ease(st / 0.7))), th)
        unit, prefix = post.strip(), pre.strip()
    else:
        shown_num, unit, prefix = text.upper(), "", ""
    p = R.ease_back(st / 0.35)
    f = ImageFont.truetype(str(FONT_SERIF), max(10, int(330 * L.s * (0.85 + 0.15 * p))))
    while f.getlength(shown_num) > L.sw * 0.95 and f.size > 40:
        f = ImageFont.truetype(str(FONT_SERIF), f.size - 10)
    cy = L.t + L.sh * 0.48
    d = ImageDraw.Draw(frame)
    w = f.getlength(shown_num)
    d.text((L.cx - w / 2, cy), shown_num, font=f, fill=WHITE, anchor="ls")
    if unit:
        uf = ImageFont.truetype(str(FONT_HEADLINE), L.px(84))
        uw = uf.getlength(unit)
        R.paste(frame, R.text_img(unit, uf, NUM_RED), (L.cx - uw / 2 - L.px(8), cy + L.px(24)), R.ease((st - 0.15) / 0.3))
    top_line = " ".join(label) if label and label != "FAKT" else ""
    if prefix:  # "ÜBER 500.000 EURO": ÜBER above the number, EURO below
        top_line = (top_line + "   ·   " if top_line else "") + prefix
    if top_line:
        lf = ImageFont.truetype(str(FONT_BOLD), L.px(34))
        lw = lf.getlength(top_line)
        d.text((L.cx - lw / 2, cy - f.size * 0.95), top_line, font=lf, fill=(255, 110, 100))
    return frame


def hud_frame(src_frame: Image.Image, keyword: str, st: float, seed: int) -> Image.Image:
    """Footage inside a framed panel on an animated tech grid – CarPlug's own treatment of the picture."""
    L = R.L
    frame = hud_grid().copy()
    d = ImageDraw.Draw(frame, "RGBA")
    scan_y = (st * 0.55 % 1.0) * L.h
    d.rectangle((0, scan_y, L.w, scan_y + L.px(3)), fill=RED + (90,))
    pw = int(L.sw * 0.96)
    ph = int(min(L.sh * 0.62, pw * 1.25))
    x0, y0 = L.cx - pw // 2, int(L.t + L.sh * 0.12)
    p = R.ease(st / 0.3)
    panel = src_frame.resize((pw, int(pw * src_frame.height / src_frame.width)), Image.BILINEAR)
    top = max(0, (panel.height - ph) // 2)
    panel = panel.crop((0, top, pw, top + ph))
    mask = R.rounded_mask(panel.size, L.px(26))
    if p < 0.99:
        mask = mask.point(lambda v: int(v * p))
    frame.paste(panel, (x0, y0 + int((1 - p) * L.px(40))), mask)
    c, lw = L.px(46), L.px(5)
    for (cx, cy, sx, sy) in ((x0, y0, 1, 1), (x0 + pw, y0, -1, 1), (x0, y0 + ph, 1, -1), (x0 + pw, y0 + ph, -1, -1)):
        d.line((cx, cy, cx + sx * c, cy), fill=RED, width=lw)
        d.line((cx, cy, cx, cy + sy * c), fill=RED, width=lw)
    rng = random.Random(seed + int(st * 5))
    mono = ImageFont.truetype(str(FONT_BOLD), L.px(24))
    for k in range(4):
        tx = x0 + (pw * (0.08 + 0.25 * k))
        d.text((tx, y0 + ph + L.px(18)), f"x: {rng.randint(300, 2400)}  y: {rng.randint(300, 2400)}", font=mono,
               fill=(200, 210, 220, 150))
    if keyword:
        chip = R.pill(keyword.upper()[:24], 26, WHITE, RED + (255,), h=52)
        R.paste(frame, chip, (x0 + L.px(20), y0 + L.px(20)), p)
    return frame


def split_frame(a: Image.Image, b: Image.Image, st: float) -> Image.Image:
    """Two pictures at once (stacked in portrait, side by side in landscape) with a red divider."""
    L = R.L
    frame = Image.new("RGB", (L.w, L.h), BLACK)
    p = R.ease(st / 0.3)
    if L.h >= L.w:
        hh = L.h // 2
        fa = a.resize((L.w, int(L.w * a.height / a.width)), Image.BILINEAR)
        fb = b.resize((L.w, int(L.w * b.height / b.width)), Image.BILINEAR)
        fa = fa.crop((0, max(0, (fa.height - hh) // 2), L.w, max(0, (fa.height - hh) // 2) + hh))
        fb = fb.crop((0, max(0, (fb.height - hh) // 2), L.w, max(0, (fb.height - hh) // 2) + hh))
        frame.paste(fa, (int(-(1 - p) * L.w), 0))
        frame.paste(fb, (int((1 - p) * L.w), hh))
        ImageDraw.Draw(frame).rectangle((0, hh - L.px(4), int(L.w * p), hh + L.px(4)), fill=RED)
    else:
        hw = L.w // 2
        fa = a.resize((int(L.h * a.width / a.height), L.h), Image.BILINEAR)
        fb = b.resize((int(L.h * b.width / b.height), L.h), Image.BILINEAR)
        fa = fa.crop((max(0, (fa.width - hw) // 2), 0, max(0, (fa.width - hw) // 2) + hw, L.h))
        fb = fb.crop((max(0, (fb.width - hw) // 2), 0, max(0, (fb.width - hw) // 2) + hw, L.h))
        frame.paste(fa, (0, int(-(1 - p) * L.h)))
        frame.paste(fb, (hw, int((1 - p) * L.h)))
        ImageDraw.Draw(frame).rectangle((hw - L.px(4), 0, hw + L.px(4), int(L.h * p)), fill=RED)
    return frame


# --- transitions -----------------------------------------------------------------------------
TRANSITIONS = ["punch", "punch", "flash", "punch", "glitch", "blur"]


def transition_kind(seed: int) -> str:
    return TRANSITIONS[seed % len(TRANSITIONS)]


def apply_transition(frame: Image.Image, kind: str, st: float) -> Image.Image:
    """Short effect on the first frames of a shot (the zoom kick itself is done by the source)."""
    if kind == "flash" and st < 0.1:
        return Image.blend(frame, Image.new("RGB", frame.size, (255, 255, 255)), 0.6 * (1 - st / 0.1))
    if kind == "glitch" and st < 0.12:
        a = np.asarray(frame)
        shift = int(R.L.px(26) * (1 - st / 0.12))
        out = a.copy()
        out[..., 0] = np.roll(a[..., 0], shift, axis=1)
        out[..., 2] = np.roll(a[..., 2], -shift, axis=1)
        return Image.fromarray(out)
    if kind == "blur" and st < 0.12:
        r = R.L.px(14) * (1 - st / 0.12)
        small = frame.resize((frame.width // 4, frame.height // 4), Image.BILINEAR)
        return small.filter(ImageFilter.GaussianBlur(max(0.1, r / 4))).resize(frame.size, Image.BILINEAR)
    return frame


# --- film look ---------------------------------------------------------------------------------
@lru_cache(maxsize=1)
def _lut() -> list[int]:
    def curve(v, lift, gain, gamma):
        x = v / 255
        x = lift + (gain - lift) * x
        return int(max(0, min(255, 255 * (x ** gamma))))
    r = [curve(v, 0.035, 1.0, 0.97) for v in range(256)]
    g = [curve(v, 0.03, 0.985, 1.0) for v in range(256)]
    b = [curve(v, 0.045, 0.94, 1.03) for v in range(256)]
    return r + g + b


def film_look(frame: Image.Image) -> Image.Image:
    """Warm, slightly lifted grade + soft glow on highlights (the dreamy doc-edit look)."""
    graded = frame.point(_lut())
    small = graded.resize((graded.width // 6, graded.height // 6), Image.BILINEAR)
    bright = small.point(lambda v: max(0, v - 165) * 2)
    glow = bright.filter(ImageFilter.GaussianBlur(5)).resize(graded.size, Image.BILINEAR)
    return ImageChops.screen(graded, glow.point(lambda v: int(v * 0.45)))
