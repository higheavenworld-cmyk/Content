"""End-to-end: source → script → voice → footage → render → posting package."""
from __future__ import annotations

import re
import shutil
import time
from pathlib import Path

from . import extract, footage, learn, music, render, voice
from .config import OUTPUT_DIR, WORK_DIR, load_settings
from .script import Script, min_seconds, write_script


def mmss(sec: float) -> str:
    return f"{int(sec // 60)}:{int(sec % 60):02d}"


def load_source(url: str, files: list[str]) -> extract.Source:
    src = extract.Source()
    if url and url.strip():
        src.merge(extract.from_url(url.strip()))
    if files:
        src.merge(extract.from_files(files))
    if not src.text and not src.media_files:
        raise ValueError("Bitte einen Link eingeben oder Dateien hochladen.")
    return src


def make_script(src: extract.Source, target: float) -> tuple[Script, str]:
    if not src.text.strip():
        raise ValueError("In der Quelle wurde kein Text gefunden. Lade eine Text-/PDF-Datei hoch oder nutze einen Link.")
    return write_script(src.title, src.text, src.url, target)


def _speak(script: Script, voice_name: str, work: Path, rate: str, min_total: float | None, log):
    """Voice-over that is guaranteed to reach min_total (pauses, then slower speech), or a clear error."""
    texts = [sc.text for sc in script.scenes]
    voices = voice.synth_many(texts, voice_name, work, rate, log)
    if not min_total:
        return voices
    reachable = render.natural_duration(voices) + render.max_stretch(len(voices))
    if reachable < min_total and not voice_name.startswith(voice.CLONE_PREFIX):
        speech = sum(len(a) / voice.SR for a, _ in voices)
        overhead = render.natural_duration(voices) - speech
        needed = min_total - overhead - render.max_stretch(len(voices)) * 0.5
        cur = int(str(rate).rstrip("%"))
        slower = max(-25, int((1 + cur / 100) * speech / needed * 100 - 100) - 1)
        if slower < cur:
            log(f"Text etwas zu kurz – spreche langsamer ({slower:+d} %), damit die Mindestlänge sicher erreicht wird…")
            voices = voice.synth_many(texts, voice_name, work, f"{slower:+d}%", log)
            reachable = render.natural_duration(voices) + render.max_stretch(len(voices))
    if reachable < min_total:
        missing = min_total - reachable
        raise ValueError(f"Das Script ist zu kurz: Das Video würde nur {mmss(reachable)} lang, nötig sind mindestens "
                         f"{mmss(min_total)}. Es fehlen ca. {missing:.0f} Sekunden (≈ {missing * 2.4:.0f} Wörter). "
                         f"Füge im Script Szenen hinzu, nutze eine längere Quelle oder wähle eine kürzere Länge.")
    return voices


def make_video(script: Script, src: extract.Source, voice_name: str, use_article=True, use_free=True,
               target: float = 40, size: tuple[int, int] = (1080, 1920), log=print) -> dict:
    s = load_settings()
    min_total = min_seconds(target)
    slug = re.sub(r"[^a-z0-9]+", "-", " ".join(script.headline).lower()).strip("-")[:40] or "video"
    vid = time.strftime("%Y%m%d-%H%M%S") + "_" + slug
    out = OUTPUT_DIR / vid
    work = WORK_DIR / vid
    out.mkdir(parents=True)
    work.mkdir(parents=True)
    try:
        log("1/4 Voice-Over wird gesprochen…")
        voices = _speak(script, voice_name, work, s["speech_rate"], min_total, log)

        log("2/4 Footage wird gesucht…")
        clips, credits = footage.collect(script.scenes, src.media_files, src.image_urls, work / "footage",
                                         use_article, use_free, s.get("pexels_key", ""), log)

        log(f"3/4 Video wird gerendert ({size[0]}×{size[1]})…")

        def mix(voice_track, total, sfx):
            return music.mix(voice_track, music.background(total + 1, work), float(s["music_volume"]), sfx)

        render.render(script, clips, voices, mix, out / "video.mp4", out / "cover.png", work, size, min_total, log)
        real = video_seconds(out / "video.mp4")
        if min_total and real < min_total - 0.05:
            raise RuntimeError(f"Kontrolle fehlgeschlagen: Video ist nur {real:.1f} s lang (Minimum {min_total:.0f} s).")
    except Exception:
        shutil.rmtree(out, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)

    log("4/4 Posting-Paket wird erstellt…")
    caption = script.caption.strip() + "\n\n" + " ".join(script.hashtags)
    if credits:
        caption += "\n\n" + "\n".join(credits)
    (out / "caption.txt").write_text(caption, encoding="utf-8")
    (out / "script.json").write_text(script.to_json(), encoding="utf-8")
    learn.log_video(vid, script.scenes[0].text, real, voice_name)
    log(f"Fertig: {out}")
    return {"id": vid, "folder": out, "video": out / "video.mp4", "cover": out / "cover.png",
            "caption": caption, "seconds": real, "min_seconds": min_total}


def video_seconds(path: Path) -> float:
    """Real duration of the finished file (checked, not assumed)."""
    import subprocess
    err = subprocess.run([voice.ffmpeg(), "-hide_banner", "-i", str(path)], capture_output=True, text=True,
                         encoding="utf-8", errors="replace").stderr
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", err)
    if not m:
        raise RuntimeError("Videolänge konnte nicht geprüft werden.")
    h, mi, sec = m.groups()
    return int(h) * 3600 + int(mi) * 60 + float(sec)
