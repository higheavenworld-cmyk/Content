"""Voice-over: free Microsoft neural voices (edge-tts) or your own cloned voice (Chatterbox, local)."""
from __future__ import annotations

import asyncio
import json
import re
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .config import ROOT, VOICES, VOICES_DIR

SR = 44100
CLONE_PREFIX = "Meine Stimme: "


@dataclass
class Word:
    text: str
    start: float
    end: float


def ffmpeg() -> str:
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def to_wav(src: Path, dst: Path) -> None:
    subprocess.run([ffmpeg(), "-y", "-loglevel", "error", "-i", str(src), "-ac", "1", "-ar", str(SR), str(dst)],
                   check=True)


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path)) as w:
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    return data.astype(np.float32) / 32768.0


def write_wav(path: Path, samples: np.ndarray) -> None:
    pcm = (np.clip(samples, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


# --- voice list ---------------------------------------------------------------
def clone_python() -> Path | None:
    for rel in (".venv-klon/Scripts/python.exe", ".venv-klon/bin/python"):
        p = ROOT / rel
        if p.exists():
            return p
    return None


def clone_available() -> bool:
    return clone_python() is not None


def voice_choices() -> list[str]:
    clones = [CLONE_PREFIX + p.stem for p in sorted(VOICES_DIR.glob("*.wav"))]
    return list(VOICES) + clones


def save_clone_sample(name: str, audio_path: str) -> Path:
    name = re.sub(r"[^\w\- ]", "", name).strip() or "meine-stimme"
    dst = VOICES_DIR / f"{name}.wav"
    to_wav(Path(audio_path), dst)
    return dst


# --- synthesis ----------------------------------------------------------------
def synth_many(texts: list[str], voice: str, work: Path, rate: str = "+8%",
               log=print) -> list[tuple[np.ndarray, list[Word]]]:
    """Speak every scene; returns [(samples at 44.1 kHz mono, word timings), ...]."""
    if voice.startswith(CLONE_PREFIX):
        return _synth_clone(texts, VOICES_DIR / f"{voice[len(CLONE_PREFIX):]}.wav", work, log)
    return [_synth_edge(t, VOICES.get(voice, voice), work / f"voice_{i}.wav", rate) for i, t in enumerate(texts)]


def _synth_edge(text: str, voice_id: str, out_wav: Path, rate: str) -> tuple[np.ndarray, list[Word]]:
    import edge_tts

    words: list[Word] = []
    mp3 = out_wav.with_suffix(".mp3")

    async def run():
        com = edge_tts.Communicate(text, voice_id, rate=rate, boundary="WordBoundary")
        with open(mp3, "wb") as f:
            async for ch in com.stream():
                if ch["type"] == "audio":
                    f.write(ch["data"])
                elif ch["type"] == "WordBoundary":
                    s = ch["offset"] / 1e7
                    words.append(Word(ch["text"], s, s + ch["duration"] / 1e7))

    asyncio.run(run())
    to_wav(mp3, out_wav)
    audio = read_wav(out_wav)
    return audio, words or estimate_words(text, len(audio) / SR)


def _synth_clone(texts: list[str], ref: Path, work: Path, log) -> list[tuple[np.ndarray, list[Word]]]:
    py = clone_python()
    if py is None:
        raise RuntimeError("Stimmen-Klon ist nicht installiert. Bitte 'Stimme-klonen-installieren.bat' ausführen.")
    items = [{"text": t, "out": str(work / f"voice_{i}_raw.wav")} for i, t in enumerate(texts)]
    job = work / "clone_job.json"
    job.write_text(json.dumps({"reference": str(ref), "items": items}, ensure_ascii=False), encoding="utf-8")
    proc = subprocess.Popen([str(py), str(Path(__file__).with_name("clone_worker.py")), str(job)],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                            errors="replace")
    tail = []
    for line in proc.stdout:
        line = line.strip()
        if line:
            tail = (tail + [line])[-15:]
            if line.startswith("Klon-Stimme"):
                log(line)
    if proc.wait() != 0:
        raise RuntimeError("Stimmen-Klon fehlgeschlagen:\n" + "\n".join(tail))
    out = []
    for i, (t, it) in enumerate(zip(texts, items)):
        wav = work / f"voice_{i}.wav"
        to_wav(Path(it["out"]), wav)
        audio = read_wav(wav)
        out.append((audio, align_words(wav, t, len(audio) / SR)))
    return out


def align_words(wav: Path, text: str, duration: float) -> list[Word]:
    """Exact word timings via faster-whisper if installed, otherwise a good estimate."""
    try:
        from faster_whisper import WhisperModel
        model = WhisperModel("base", device="cpu", compute_type="int8")
        segs, _ = model.transcribe(str(wav), language="de", word_timestamps=True)
        words = [Word(w.word.strip(), w.start, w.end) for s in segs for w in (s.words or [])]
        if words:
            return words
    except Exception:
        pass
    return estimate_words(text, duration)


def estimate_words(text: str, duration: float) -> list[Word]:
    toks = text.split()
    if not toks:
        return []
    weights = [len(t) + (4 if t[-1] in ".,!?:;" else 1) for t in toks]
    scale, t, out = duration * 0.96 / sum(weights), duration * 0.02, []
    for tok, wt in zip(toks, weights):
        out.append(Word(tok, t, t + wt * scale * 0.85))
        t += wt * scale
    return out
