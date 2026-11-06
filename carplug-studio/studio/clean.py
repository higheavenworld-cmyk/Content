"""Keep only the clean picture: no added text, captions, watermarks or channel names.

Text is found with a small OCR detector (rapidocr, runs offline). Then, in this order:
1. find a crop window (in the output aspect ratio) that contains no text at all,
2. otherwise remove overlay-like text (big captions, corner watermarks) – inpainting for images,
   ffmpeg 'delogo' for videos – and crop around what is left,
3. otherwise the image / video part is not used.
"""
from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from .tools import ffmpeg, probe

Box = tuple[float, float, float, float]  # x0, y0, x1, y1 in pixels of the source

MAX_FIX_AREA = 0.12   # at most 12 % of the picture may be retouched
MARGIN = 0.015        # safety margin around detected text


@lru_cache(maxsize=1)
def _ocr():
    try:
        from rapidocr_onnxruntime import RapidOCR
        return RapidOCR()
    except Exception:
        return None


def available() -> bool:
    return _ocr() is not None


def detect_text(rgb: np.ndarray) -> list[Box]:
    """Bounding boxes of any text in an RGB image (fast: detection only, no reading)."""
    ocr = _ocr()
    if ocr is None:
        return []
    h, w = rgb.shape[:2]
    scale = min(1.0, 1280 / max(w, h))  # high enough resolution to also find small watermarks
    small = rgb if scale == 1.0 else np.asarray(Image.fromarray(rgb).resize((int(w * scale), int(h * scale))))
    res, _ = ocr(small, use_det=True, use_cls=False, use_rec=False)
    boxes = []
    for quad in res or []:
        xs = [p[0] / scale for p in quad]
        ys = [p[1] / scale for p in quad]
        boxes.append((min(xs), min(ys), max(xs), max(ys)))
    return boxes


def overlay_like(boxes: list[Box], w: int, h: int) -> list[Box]:
    """Text that was ADDED on top (captions, titles, watermarks) – not signs or number plates in the scene."""
    out = []
    for b in boxes:
        bh = b[3] - b[1]
        near_edge = b[0] < w * 0.08 or b[2] > w * 0.92 or b[1] < h * 0.08 or b[3] > h * 0.92
        if bh >= h * 0.025 or near_edge:
            out.append(b)
    return out


def _expand(b: Box, w: int, h: int) -> Box:
    mx, my = w * MARGIN, h * MARGIN
    return max(0, b[0] - mx), max(0, b[1] - my), min(w, b[2] + mx), min(h, b[3] + my)


def find_window(w: int, h: int, aspect: float, boxes: list[Box], min_scale: float) -> Box | None:
    """Largest crop (width/height = aspect) without any of the boxes, as central as possible."""
    if w / h > aspect:
        base_w, base_h = h * aspect, h
    else:
        base_w, base_h = w, w / aspect
    boxes = [_expand(b, w, h) for b in boxes]
    for s in (1.0, 0.92, 0.84, 0.76, 0.68, 0.6, 0.52):
        if s < min_scale - 1e-6:
            break
        ww, wh = base_w * s, base_h * s
        best, best_d = None, 1e18
        for x in np.linspace(0, w - ww, 17):
            for y in np.linspace(0, h - wh, 17):
                if any(x < b[2] and b[0] < x + ww and y < b[3] and b[1] < y + wh for b in boxes):
                    continue
                d = (x + ww / 2 - w / 2) ** 2 + (y + wh / 2 - h / 2) ** 2
                if d < best_d:
                    best, best_d = (x, y, x + ww, y + wh), d
        if best:
            return best
    return None


def content_box(rgb: np.ndarray) -> Box:
    """Area without black/plain bars (letterbox, pillarbox, empty borders)."""
    g = rgb.astype(np.float32).mean(axis=2)
    h, w = g.shape
    rows = (g.std(axis=1) > 7) & (g.mean(axis=1) > 28)   # plain or almost-black rows = bars
    cols = (g.std(axis=0) > 7) & (g.mean(axis=0) > 28)
    ys, xs = np.flatnonzero(rows), np.flatnonzero(cols)
    if len(ys) < h * 0.3 or len(xs) < w * 0.3:
        return 0, 0, w, h
    return float(xs[0]), float(ys[0]), float(xs[-1] + 1), float(ys[-1] + 1)


def window_in(content: Box, aspect: float, boxes: list[Box], min_scale: float) -> Box | None:
    """find_window restricted to the content area (coordinates stay in full-image pixels)."""
    cx0, cy0, cx1, cy1 = content
    shifted = [(b[0] - cx0, b[1] - cy0, b[2] - cx0, b[3] - cy0) for b in boxes]
    win = find_window(int(cx1 - cx0), int(cy1 - cy0), aspect, shifted, min_scale)
    return None if win is None else (win[0] + cx0, win[1] + cy0, win[2] + cx0, win[3] + cy0)


def _area(boxes: list[Box], w: int, h: int) -> float:
    return sum((b[2] - b[0]) * (b[3] - b[1]) for b in boxes) / (w * h)


# --- images ---------------------------------------------------------------------
def clean_image(src: Path, dst_dir: Path, aspect: float) -> Path | None:
    """Returns a cleaned copy (cropped and/or retouched) or None if the image is unusable."""
    try:
        im = ImageOps.exif_transpose(Image.open(src)).convert("RGB")
    except Exception:
        return None
    w, h = im.size
    if min(w, h) < 400:
        return None
    rgb = np.asarray(im)
    content = content_box(rgb)
    if min(content[2] - content[0], content[3] - content[1]) < 400:
        return None
    boxes = detect_text(rgb)
    dst = dst_dir / (src.stem + "_clean.jpg")
    # strict: the final picture must not contain ANY text (no captions, watermarks, logos with text)
    win = window_in(content, aspect, boxes, 0.52)
    if win is None:
        merged = _merge(boxes)
        if _area(merged, w, h) > MAX_FIX_AREA:
            return None
        im = Image.fromarray(inpaint(rgb, [_expand(b, w, h) for b in merged]))
        win = window_in(content, aspect, [], 1.0)
    crop = im.crop(tuple(map(int, win)))
    if min(crop.size) < 360:
        return None
    crop.save(dst, quality=93)
    return dst


def inpaint(rgb: np.ndarray, boxes: list[Box]) -> np.ndarray:
    import cv2
    mask = np.zeros(rgb.shape[:2], np.uint8)
    for b in boxes:
        mask[int(b[1]):int(b[3]) + 1, int(b[0]):int(b[2]) + 1] = 255
    return cv2.inpaint(rgb, mask, 7, cv2.INPAINT_TELEA)


# --- videos ---------------------------------------------------------------------
@dataclass
class VideoInfo:
    path: Path
    w: int
    h: int
    dur: float
    samples: list[tuple[float, list[Box]]] = field(default_factory=list)  # (time, text boxes)
    content: Box | None = None  # picture area without black bars
    frames: list = field(default_factory=list)  # small preview frame per sample (for image understanding)


def analyze_video(path: Path, max_samples: int = 90) -> VideoInfo | None:
    w, h, dur = probe(path)
    if not (w and h and dur >= 1.0):
        return None
    info = VideoInfo(path, w, h, dur)
    step = max(0.8, dur / max_samples)  # dense: captions are caught even if they only show briefly
    with tempfile.TemporaryDirectory() as tmp:
        sw = 640 if w >= h else int(640 * w / h) // 2 * 2
        subprocess.run([ffmpeg(), "-loglevel", "error", "-i", str(path), "-vf", f"fps=1/{step:.3f},scale={sw}:-2",
                        "-q:v", "4", str(Path(tmp) / "s_%04d.jpg")], check=False)
        contents = []
        for i, f in enumerate(sorted(Path(tmp).glob("s_*.jpg"))):
            frame = Image.open(f).convert("RGB")
            info.frames.append(frame.copy())
            rgb = np.asarray(frame)
            k = w / rgb.shape[1]
            boxes = [(b[0] * k, b[1] * k, b[2] * k, b[3] * k) for b in detect_text(rgb)] if available() else []
            info.samples.append((i * step + step / 2, boxes))
            contents.append([v * k for v in content_box(rgb)])
    if contents:  # bars must be present in most frames to count
        c = np.median(np.array(contents), axis=0)
        info.content = (float(c[0]), float(c[1]), float(c[2]), float(c[3]))
    return info


@dataclass
class VideoCut:
    start: float
    crop: Box                       # in source pixels
    delogo: list[Box] = field(default_factory=list)


def plan_cut(v: VideoInfo, start: float, dur: float, aspect: float) -> VideoCut | None:
    """Crop/retouch plan for [start, start+dur] so that no added text is visible, or None."""
    content = v.content or (0, 0, v.w, v.h)
    boxes = [b for t, bs in v.samples if start - 1 <= t <= start + dur + 1 for b in bs]
    win = window_in(content, aspect, boxes, 0.6)
    if win:
        return VideoCut(start, win)
    merged = _merge(boxes)  # strict: every text is removed, otherwise this part of the video is not used
    if _area(merged, v.w, v.h) <= MAX_FIX_AREA:
        return VideoCut(start, window_in(content, aspect, [], 1.0), [_expand(b, v.w, v.h) for b in merged])
    return None


def _merge(boxes: list[Box]) -> list[Box]:
    """Merge overlapping boxes (the same watermark found in several frames)."""
    out: list[list[float]] = []
    for b in sorted(boxes):
        for o in out:
            if b[0] <= o[2] and o[0] <= b[2] and b[1] <= o[3] and o[1] <= b[3]:
                o[:] = [min(o[0], b[0]), min(o[1], b[1]), max(o[2], b[2]), max(o[3], b[3])]
                break
        else:
            out.append(list(b))
    return [tuple(o) for o in out]


def ffmpeg_filters(cut: VideoCut, w: int, h: int) -> str:
    parts = []
    for b in cut.delogo:
        x0, y0 = max(1, int(b[0])), max(1, int(b[1]))
        x1, y1 = min(w - 2, int(b[2])), min(h - 2, int(b[3]))
        if x1 - x0 > 4 and y1 - y0 > 4:
            parts.append(f"delogo=x={x0}:y={y0}:w={x1 - x0}:h={y1 - y0}")
    x0, y0, x1, y1 = map(int, cut.crop)
    cw, ch = (x1 - x0) // 2 * 2, (y1 - y0) // 2 * 2
    parts.append(f"crop={cw}:{ch}:{x0}:{y0}")
    return ",".join(parts)
