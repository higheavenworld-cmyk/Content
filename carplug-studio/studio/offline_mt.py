"""Offline translation on your own PC – no account, no limits, no internet after the first download.

Uses the free Argos Translate / OPUS models (CTranslate2 + SentencePiece; CTranslate2 is already installed
for speech recognition). Models are ~100 MB per language pair and are downloaded once.
Languages other than English are translated via English (e.g. French -> English -> German).
"""
from __future__ import annotations

import json
import re
import threading
import zipfile
from functools import lru_cache
from pathlib import Path

import requests

from .config import ROOT

MODELS = ROOT / ".modelle" / "uebersetzung"
INDEX_URLS = ["https://raw.githubusercontent.com/argosopentech/argospm-index/main/index.json",
              "https://github.com/argosopentech/argospm-index/raw/main/index.json"]
_lock = threading.Lock()


@lru_cache(maxsize=1)
def _index() -> list[dict]:
    for url in INDEX_URLS:
        try:
            r = requests.get(url, timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception:
            continue
    raise RuntimeError("Modell-Verzeichnis nicht erreichbar")


def _model_dir(src: str, tgt: str) -> Path:
    return MODELS / f"{src}_{tgt}"


def installed(src: str, tgt: str) -> bool:
    return any(_model_dir(src, tgt).rglob("metadata.json"))


def ensure(src: str, tgt: str, log=None) -> Path:
    """Download + unpack the model for src->tgt once. Returns the package folder."""
    with _lock:
        d = _model_dir(src, tgt)
        found = next(d.rglob("metadata.json"), None) if d.exists() else None
        if found:
            return found.parent
        pkg = next((p for p in _index() if p.get("from_code") == src and p.get("to_code") == tgt), None)
        if not pkg:
            raise RuntimeError(f"Kein Offline-Modell für {src}→{tgt}")
        if log:
            log(f"Lade Offline-Übersetzer {src}→{tgt} (einmalig, ca. 100 MB)…")
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / "download.zip"
        last = None
        for url in pkg.get("links", []):
            try:
                with requests.get(url, stream=True, timeout=60) as r:
                    r.raise_for_status()
                    with open(tmp, "wb") as f:
                        for chunk in r.iter_content(1 << 20):
                            f.write(chunk)
                with zipfile.ZipFile(tmp) as z:
                    z.extractall(d)
                tmp.unlink(missing_ok=True)
                break
            except Exception as e:  # try the next mirror
                last = e
        found = next(d.rglob("metadata.json"), None)
        if not found:
            raise RuntimeError(f"Download des Offline-Modells fehlgeschlagen: {last}")
        return found.parent


@lru_cache(maxsize=4)
def _load(pkg_dir: str):
    import ctranslate2
    import sentencepiece as spm
    p = Path(pkg_dir)
    meta = json.loads((p / "metadata.json").read_text(encoding="utf-8"))
    sp_file = p / "sentencepiece.model"
    if not sp_file.exists():
        raise RuntimeError("Modellformat nicht unterstützt (sentencepiece.model fehlt)")
    translator = ctranslate2.Translator(str(p / "model"), device="cpu", compute_type="int8")
    return translator, spm.SentencePieceProcessor(model_file=str(sp_file)), meta.get("target_prefix", "")


def _sentences(line: str) -> list[str]:
    parts = [s for s in re.split(r"(?<=[.!?])\s+", line.strip()) if s]
    out = []
    for s in parts:  # very long sentences are cut at commas (the models are trained on sentences)
        while len(s) > 400 and "," in s[50:400]:
            k = s.index(",", 50) + 1
            out.append(s[:k])
            s = s[k:].strip()
        out.append(s)
    return out or [line]


def _translate_pair(lines: list[str], src: str, tgt: str, log=None) -> list[str]:
    translator, sp, prefix = _load(str(ensure(src, tgt, log)))
    flat, owner = [], []
    for i, ln in enumerate(lines):
        for s in _sentences(ln):
            flat.append(s)
            owner.append(i)
    out_parts: list[list[str]] = [[] for _ in lines]
    for k in range(0, len(flat), 32):
        batch = flat[k:k + 32]
        tokens = [sp.encode(s, out_type=str) for s in batch]
        kwargs = {"beam_size": 3, "max_decoding_length": 300, "replace_unknowns": True}
        if prefix:
            kwargs["target_prefix"] = [[prefix]] * len(batch)
        res = translator.translate_batch(tokens, **kwargs)
        for j, r in enumerate(res):
            toks = r.hypotheses[0]
            if prefix and toks and toks[0] == prefix:
                toks = toks[1:]
            out_parts[owner[k + j]].append(sp.decode(toks))
    return [re.sub(r"\s+", " ", " ".join(p).replace("▁", " ")).strip() for p in out_parts]


def detect(text: str) -> str:
    try:
        from langdetect import detect as ld
        return ld(text[:2000])
    except Exception:
        return "en"


def translate(lines: list[str], tgt: str = "de", log=None) -> list[str]:
    """Translate lines offline. Source language is detected; non-English goes via English."""
    src = detect(" ".join(lines[:80]))
    src = {"zh-cn": "zh", "zh-tw": "zt"}.get(src, src.split("-")[0])
    if src == tgt:
        return lines
    try:
        return _translate_pair(lines, src, tgt, log)
    except RuntimeError:
        if src == "en" or tgt == "en":
            raise
        mid = _translate_pair(lines, src, "en", log)  # pivot through English
        return _translate_pair(mid, "en", tgt, log)
