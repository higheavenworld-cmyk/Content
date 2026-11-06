"""Image understanding: how well does a picture match a sentence? (SigLIP 2, runs locally, understands German)

The model (~1.5 GB) is downloaded once on first use and then works offline.
If it cannot be loaded, footage selection falls back to keyword matching.
"""
from __future__ import annotations

import threading
from functools import lru_cache

import numpy as np
from PIL import Image

from .config import ROOT

MODEL = "google/siglip2-base-patch16-224"
CACHE = ROOT / ".modelle"
_lock = threading.Lock()
_error: str | None = None


@lru_cache(maxsize=1)
def _models():
    global _error
    try:
        from fastembed import ImageEmbedding, TextEmbedding
        CACHE.mkdir(exist_ok=True)
        return (ImageEmbedding(model_name=MODEL, cache_dir=str(CACHE)),
                TextEmbedding(model_name=MODEL, cache_dir=str(CACHE)))
    except Exception as e:  # not installed / no internet on first start
        _error = str(e)[:200]
        return None


def available() -> bool:
    with _lock:
        return _models() is not None


def last_error() -> str | None:
    return _error


def _norm(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=np.float32)
    return x / (np.linalg.norm(x, axis=-1, keepdims=True) + 1e-8)


def embed_images(images: list) -> np.ndarray:
    """images: file paths or PIL images → (n, d) normalised vectors."""
    if not images:
        return np.zeros((0, 1), np.float32)
    with _lock:
        img_model, _ = _models()
        prepared = [im.convert("RGB") if isinstance(im, Image.Image) else str(im) for im in images]
        return _norm(np.array(list(img_model.embed(prepared, batch_size=16))))


def embed_texts(texts: list[str]) -> np.ndarray:
    with _lock:
        _, txt_model = _models()
        return _norm(np.array(list(txt_model.embed(texts))))


def describe_scene(text: str, subject: str) -> str:
    """Short picture description for one scene: subject + what the sentence is about (≤ 64 tokens)."""
    words = text.split()
    short = " ".join(words[:28])
    return f"Foto: {subject}. {short}" if subject else f"Foto: {short}"
