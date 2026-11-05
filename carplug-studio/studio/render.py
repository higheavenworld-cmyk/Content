"""Renders the final 9:16 video in CarPlug branding (motion graphics, subtitles, music)."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

from .config import (BLACK, BRAND_URL, FONT_BOLD, FONT_HEADLINE, FPS, GREY, H, LOGO_FILE, LOGO_RED, RED,
                     W, WHITE)
from .footage import Clip
from .voice import SR, Word, ffmpeg, write_wav

MARGIN = 72
INTRO_IMG_H = 1100
GAP = 0.12  # pause between scenes (seconds)


@dataclass
class Segment:
    kind: str                 # intro | body | outro
    start: float
    dur: float
    clip: Clip | None
    overlay: str = ""
    words: list[Word] = field(default_factory=list)


# --- small helpers --------------------------------------------------------------
def ease(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return 1 - (1 - x) ** 3


@lru_cache(maxsize=64)
def font(size: int, bold_only: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_BOLD if bold_only else FONT_HEADLINE), size)


def fit_font(text: str, max_w: int, size: int, min_size: int = 40) -> ImageFont.FreeTypeFont:
    while size > min_size and font(size).getlength(text) > max_w:
        size -= 4
    return font(size)


@lru_cache(maxsize=8)
def logo_icon(size: int) -> Image.Image:
    im = Image.open(LOGO_FILE).convert("RGB").resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size * 4, size * 4), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size * 4 - 1, size * 4 - 1), radius=size * 4 // 5, fill=255)
    im.putalpha(mask.resize((size, size), Image.LANCZOS))
    return im


@lru_cache(maxsize=8)
def gradient(h: int, top_alpha: int, bottom_alpha: int) -> Image.Image:
    a = np.linspace(top_alpha, bottom_alpha, h, dtype=np.float32) ** 1.0
    alpha = np.repeat(a[:, None], W, axis=1).astype(np.uint8)
    g = Image.new("RGBA", (W, h), BLACK + (0,))
    g.putalpha(Image.fromarray(alpha, "L"))
    return g


def text_layer(text: str, f: ImageFont.FreeTypeFont, color, stroke: int = 0) -> Image.Image:
    l, t, r, b = f.getbbox(text, stroke_width=stroke)
    im = Image.new("RGBA", (r - l + 30, b - t + 20), (0, 0, 0, 0))
    ImageDraw.Draw(im).text((10 - l, 10 - t), text, font=f, fill=color, stroke_width=stroke,
                            stroke_fill=(0, 0, 0))
    return im


def paste(base: Image.Image, layer: Image.Image, xy, opacity: float = 1.0) -> None:
    if opacity <= 0:
        return
    if opacity < 1:
        layer = layer.copy()
        layer.putalpha(layer.getchannel("A").point(lambda v: int(v * opacity)))
    base.paste(layer, (int(xy[0]), int(xy[1])), layer)


# --- footage sources ---------------------------------------------------------------
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
        box = (cx - cw / 2, cy - ch / 2, cx + cw / 2, cy + ch / 2)
        return self.im.resize((self.w, self.h), Image.BILINEAR, box=box)


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
        return self.last

    def close(self):
        self.proc.kill()


# --- subtitles -----------------------------------------------------------------
def chunk_words(words: list[Word], max_words: int = 3, max_chars: int = 20) -> list[list[Word]]:
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
    text = " ".join(words)
    f = fit_font(text, W - 2 * MARGIN, 88, 54)
    stroke = 7
    space = f.getlength(" ")
    widths = [f.getlength(w) for w in words]
    total = sum(widths) + space * (len(words) - 1)
    l, t, r, b = f.getbbox(text, stroke_width=stroke)
    im = Image.new("RGBA", (int(total) + 60, b - t + 30), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    x = 30
    for i, (w, ww) in enumerate(zip(words, widths)):
        d.text((x, 15 - t), w, font=f, fill=RED if i == active else WHITE, stroke_width=stroke, stroke_fill=(0, 0, 0))
        x += ww + space
    return im


def draw_subtitles(frame: Image.Image, chunks: list[list[Word]], t: float, y_center: int = 1330) -> None:
    for ci, ch in enumerate(chunks):
        end = chunks[ci + 1][0].start if ci + 1 < len(chunks) else ch[-1].end + 0.4
        if ch[0].start <= t < end:
            active = max((i for i, w in enumerate(ch) if w.start <= t), default=0)
            img = subtitle_img(tuple(w.text.upper() for w in ch), active)
            pop = ease((t - ch[0].start) / 0.09)
            s = 0.85 + 0.15 * pop
            if s < 0.999:
                img = img.resize((max(1, int(img.width * s)), max(1, int(img.height * s))), Image.BILINEAR)
            paste(frame, img, ((W - img.width) / 2, y_center - img.height / 2))
            return


# --- scene painters ----------------------------------------------------------------
class Renderer:
    def __init__(self, headline: list[str], subline: str, total: float):
        self.headline = [h.strip() for h in headline if h.strip()]
        self.subline = subline.strip()
        self.total = total
        self.brand_small = self._brand_badge(84, 40)
        self.brand_big = self._brand_badge(150, 50)
        self.grad_top = gradient(380, 170, 0)
        self.grad_bottom = gradient(1050, 0, 235)
        self.grad_intro = gradient(360, 0, 255)
        size = 128
        while size > 60 and any(font(size).getlength(h) > W - 2 * MARGIN - 20 for h in self.headline):
            size -= 4
        self.head_layers = [text_layer(h, font(size), RED if i == 1 else WHITE) for i, h in enumerate(self.headline)]
        self.head_step = int(size * 1.08)
        self.sub_layer = text_layer(self.subline, fit_font(self.subline, W - 2 * MARGIN, 46, 26), GREY) \
            if self.subline else None

    def _brand_badge(self, icon: int, text_size: int) -> Image.Image:
        txt = text_layer(BRAND_URL, font(text_size), WHITE)
        im = Image.new("RGBA", (icon + 30 + txt.width, max(icon, txt.height)), (0, 0, 0, 0))
        im.paste(logo_icon(icon), (0, 0), logo_icon(icon))
        im.paste(txt, (icon + 22, (icon - txt.height) // 2 + 4), txt)
        return im

    def progress(self, frame: Image.Image, t: float) -> None:
        ImageDraw.Draw(frame).rectangle((0, 0, int(W * min(t / self.total, 1)), 9), fill=RED)

    def intro(self, src, seg: Segment, lt: float, t: float) -> Image.Image:
        frame = Image.new("RGB", (W, H), BLACK)
        frame.paste(src.frame(lt / max(seg.dur, 0.1)), (0, 0))
        paste(frame, self.grad_intro, (0, INTRO_IMG_H - 360))
        y = 1180
        ImageDraw.Draw(frame).rectangle((MARGIN, y, MARGIN + int(60 * ease(lt / 0.35)), y + 10), fill=RED)
        y += 40
        for i, layer in enumerate(self.head_layers):
            p = ease((lt - 0.08 - i * 0.1) / 0.35)
            paste(frame, layer, (MARGIN - 10 - 120 * (1 - p), y + i * self.head_step), p)
        y += len(self.head_layers) * self.head_step + 16
        if self.sub_layer:
            paste(frame, self.sub_layer, (MARGIN - 10, y), ease((lt - 0.45) / 0.3))
        paste(frame, self.brand_big, (MARGIN - 10, H - 245), ease((lt - 0.3) / 0.4))
        return frame

    def body(self, src, seg: Segment, lt: float, t: float, chunks) -> Image.Image:
        punch = 0.12 * (1 - ease(lt / 0.35))
        frame = src.frame(lt / max(seg.dur, 0.1), punch).copy()
        paste(frame, self.grad_top, (0, 0))
        paste(frame, self.grad_bottom, (0, H - 1050))
        paste(frame, self.brand_small, (MARGIN - 20, 70))
        if seg.overlay:
            p = ease((lt - 0.1) / 0.3)
            layer = self._overlay_layer(seg.overlay)
            ImageDraw.Draw(frame).rectangle((MARGIN, 1010, MARGIN + int(60 * p), 1020), fill=RED)
            paste(frame, layer, (MARGIN - 10 - 80 * (1 - p), 1040), p)
        draw_subtitles(frame, chunks, lt)
        if lt < 0.12:  # flash transition
            flash = Image.new("RGB", (W, H), (255, 255, 255))
            frame = Image.blend(frame, flash, 0.55 * (1 - lt / 0.12))
        return frame

    @lru_cache(maxsize=32)
    def _overlay_layer(self, text: str) -> Image.Image:
        return text_layer(text.upper(), fit_font(text.upper(), W - 2 * MARGIN, 150, 70), WHITE, stroke=3)

    def outro(self, seg: Segment, lt: float) -> Image.Image:
        frame = Image.new("RGB", (W, H), BLACK)
        p = ease(lt / 0.45)
        size = int(520 * (0.75 + 0.25 * p))
        icon = logo_icon(520).resize((size, size), Image.BILINEAR) if size != 520 else logo_icon(520)
        paste(frame, icon, ((W - size) / 2, 520 + (520 - size) / 2), min(1.0, p * 1.5))
        q = ease((lt - 0.25) / 0.35)
        ImageDraw.Draw(frame).rectangle(((W - 60) // 2, 1150, (W - 60) // 2 + int(60 * q), 1160), fill=RED)
        follow = self._overlay_layer("FOLG CARPLUG")
        paste(frame, follow, ((W - follow.width) / 2, 1190 + 60 * (1 - q)), q)
        url = text_layer(BRAND_URL, font(56), RED)
        paste(frame, url, ((W - url.width) / 2, 1400), ease((lt - 0.45) / 0.35))
        return frame


# --- main entry ----------------------------------------------------------------------
def render(script, clips: list[Clip], voices: list[tuple[np.ndarray, list[Word]]], music_mix_fn,
           out_mp4: Path, cover_png: Path, work: Path, log=print) -> float:
    """Build timeline, mix audio, render frames, encode MP4. Returns duration in seconds."""
    segs, t = [], 0.0
    n = len(script.scenes)
    for i, (sc, (audio, words)) in enumerate(zip(script.scenes, voices)):
        kind = "intro" if i == 0 else "outro" if i == n - 1 else "body"
        dur = len(audio) / SR + (0.9 if kind == "outro" else GAP + 0.15)
        segs.append(Segment(kind, t, dur, clips[i], sc.overlay, words))
        t += dur
    total = t

    # audio: place every voice clip at its segment start, then music + whooshes
    voice_track = np.zeros(int(total * SR) + SR, np.float32)
    for seg, (audio, _) in zip(segs, voices):
        p = int(seg.start * SR) + (int(0.1 * SR) if seg.kind == "body" else 0)
        voice_track[p:p + len(audio)] += audio
    for seg in segs:
        for w in seg.words:
            w.start += 0.1 if seg.kind == "body" else 0
            w.end += 0.1 if seg.kind == "body" else 0
    mixed = music_mix_fn(voice_track, total, [s.start - 0.15 for s in segs[1:]])
    wav = work / "mix.wav"
    write_wav(wav, mixed[: int(total * SR)])

    r = Renderer(script.headline, script.subline, total)
    cmd = [ffmpeg(), "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
           "-r", str(FPS), "-i", "-", "-i", str(wav), "-c:v", "libx264", "-preset", "veryfast", "-crf", "19",
           "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart",
           str(out_mp4)]
    enc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    frames_total = int(total * FPS)
    done = 0
    try:
        for i, seg in enumerate(segs):
            src = None
            if seg.kind != "outro":
                h = INTRO_IMG_H if seg.kind == "intro" else H
                src = (VideoSource(seg.clip.path, W, h, seg.dur) if seg.clip.kind == "video"
                       else ImageSource(seg.clip.path, W, h, seed=i))
            chunks = chunk_words(seg.words)
            for k in range(int(round((seg.start + seg.dur) * FPS)) - int(round(seg.start * FPS))):
                lt = k / FPS
                tt = seg.start + lt
                if seg.kind == "intro":
                    frame = r.intro(src, seg, lt, tt)
                    if abs(lt - 1.2) < 0.5 / FPS or (k == 0 and seg.dur < 1.2):
                        frame.save(cover_png)
                elif seg.kind == "body":
                    frame = r.body(src, seg, lt, tt, chunks)
                else:
                    frame = r.outro(seg, lt)
                r.progress(frame, tt)
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
        r.intro(ImageSource(segs[0].clip.path, W, INTRO_IMG_H, 0), segs[0], 1.5, 1.5).save(cover_png)
    return total
