"""Background music + sound effects.

Put your own royalty-free tracks (mp3/wav) into assets/music — a random one is used.
Without any track, a driving beat is generated on the fly (100 % free of rights).
"""
from __future__ import annotations

import random
from pathlib import Path

import numpy as np

from .config import MUSIC_DIR
from .voice import SR, read_wav, to_wav


def background(seconds: float, work: Path) -> np.ndarray:
    tracks = [p for p in MUSIC_DIR.iterdir() if p.suffix.lower() in {".mp3", ".wav", ".m4a", ".ogg"}]
    n = int(seconds * SR)
    if tracks:
        wav = work / "music.wav"
        to_wav(random.choice(tracks), wav)
        m = read_wav(wav)
        m = np.tile(m, n // max(len(m), 1) + 1)[:n]
    else:
        m = generated_beat(seconds)
    fade = int(1.5 * SR)
    m[-fade:] *= np.linspace(1, 0, fade)
    return m / (np.abs(m).max() + 1e-6)


def generated_beat(seconds: float, bpm: float = 124, seed: int | None = None) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n = int(seconds * SR)
    out = np.zeros(n + SR, np.float32)
    beat = 60 / bpm
    # minor progression: Am – F – C – G  (root frequencies of the bass)
    roots = [110.0, 87.31, 130.81, 98.0]
    t_kick = np.arange(int(0.35 * SR)) / SR
    kick = np.sin(2 * np.pi * (50 + 90 * np.exp(-t_kick * 30)) * t_kick) * np.exp(-t_kick * 9)
    t_hat = np.arange(int(0.05 * SR)) / SR
    hat_noise = rng.standard_normal(len(t_hat)).astype(np.float32)
    hat = np.diff(hat_noise, prepend=0) * np.exp(-t_hat * 90) * 0.18
    i = 0
    while i * beat < seconds:
        pos = int(i * beat * SR)
        out[pos:pos + len(kick)] += kick * 0.9
        off = int((i + 0.5) * beat * SR)
        out[off:off + len(hat)] += hat
        bar, step = divmod(i, 4)
        f = roots[bar % 4]
        t_b = np.arange(int(beat * SR)) / SR
        saw = 2 * ((t_b * f) % 1) - 1
        bass = saw * np.exp(-t_b * 3) * 0.22
        out[pos:pos + len(bass)] += bass[: len(out) - pos]
        if step == 0:  # soft pad chord per bar
            t_p = np.arange(int(4 * beat * SR)) / SR
            env = np.minimum(t_p / 0.4, 1) * np.exp(-t_p * 0.35)
            pad = sum(np.sin(2 * np.pi * f * 2 * r * t_p) for r in (1, 1.189, 1.498)) * env * 0.05
            out[pos:pos + len(pad)] += pad[: len(out) - pos]
        i += 1
    return out[:n]


def whoosh(seconds: float = 0.45) -> np.ndarray:
    n = int(seconds * SR)
    noise = np.random.default_rng(1).standard_normal(n).astype(np.float32)
    # simple moving-average low-pass with a sweeping window = "whoosh"
    out = np.empty(n, np.float32)
    acc = 0.0
    for k in range(n):
        a = 0.02 + 0.25 * np.sin(np.pi * k / n)
        acc += a * (noise[k] - acc)
        out[k] = acc
    env = np.sin(np.pi * np.arange(n) / n) ** 2
    return out * env / (np.abs(out).max() + 1e-6) * 0.5


def mix(voice: np.ndarray, music: np.ndarray, music_volume: float, sfx_at: list[float]) -> np.ndarray:
    n = max(len(voice), len(music))
    v = np.pad(voice, (0, n - len(voice)))
    m = np.pad(music, (0, n - len(music)))
    # ducking: music gets quieter while someone speaks
    win = int(0.25 * SR)
    level = np.convolve(np.abs(v), np.ones(win) / win, mode="same")
    duck = np.where(level > 0.02, 1.0, 2.2)
    duck = np.convolve(duck, np.ones(win) / win, mode="same")
    out = v + m * music_volume * duck
    w = whoosh()
    for t in sfx_at:
        p = int(t * SR)
        seg = out[p:p + len(w)]
        seg += w[: len(seg)] * 0.6
    return out / max(1.0, np.abs(out).max() / 0.97)
