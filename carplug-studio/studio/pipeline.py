"""End-to-end: source → script → voice → footage → render → posting package."""
from __future__ import annotations

import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from . import extract, footage, learn, music, render, translate, voice
from .config import OUTPUT_DIR, WORK_DIR, load_settings
from .script import Script, min_seconds, write_script


def mmss(sec: float) -> str:
    return f"{int(sec // 60)}:{int(sec % 60):02d}"


def load_source(urls: str, files: list[str], log=print) -> extract.Source:
    """Any number of links (one per line) and/or uploaded files."""
    from . import linkgrab
    src = extract.Source()
    dl = WORK_DIR / "quellen" / time.strftime("%Y%m%d-%H%M%S")
    _cleanup_old(WORK_DIR / "quellen")
    for url in [u.strip() for u in re.split(r"[\s,]+", urls or "") if u.strip().startswith("http")]:
        log(f"Lese Link: {url[:80]}")
        src.merge(linkgrab.grab(url, dl, log))
    if files:
        src.merge(extract.from_files(files))
    if not src.text and not src.media_files:
        raise ValueError("Bitte einen Link eingeben oder Dateien hochladen.")
    return src


def _cleanup_old(folder: Path, days: float = 2) -> None:
    if not folder.exists():
        return
    for d in folder.iterdir():
        if d.is_dir() and time.time() - d.stat().st_mtime > days * 86400:
            shutil.rmtree(d, ignore_errors=True)


def make_script(src: extract.Source, target: float, log=print) -> tuple[Script, str]:
    if not src.text.strip():
        raise ValueError("In der Quelle wurde kein Text gefunden. Lade eine Text-/PDF-Datei hoch oder nutze einen Link.")
    try:
        translated = translate.source_to_german(src, log)
    except translate.TranslationError as e:
        raise ValueError(str(e)) from e
    script, info = write_script(src.title, src.text, src.url, target)
    translate.script_to_german(script)
    if translated:
        info += " Quelle wurde automatisch ins Deutsche übersetzt."
    return script, info


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


@dataclass
class Prepared:
    """Voice + footage plan, ready to render. Kept between "② Footage prüfen" and "③ Video erstellen"."""
    key: tuple
    src: extract.Source
    voice_name: str
    target: float
    size: tuple[int, int]
    min_total: float | None
    work: Path
    voices: list
    durations: list[float]
    sel: footage.Selection
    started: float


def _key(script: Script, voice_name, use_article, use_free, target, size) -> tuple:
    return (tuple(sc.text for sc in script.scenes), voice_name, use_article, use_free, target, tuple(size))


def prepare(script: Script, src: extract.Source, voice_name: str, use_article=True, use_free=True,
            target: float = 40, size: tuple[int, int] = (1080, 1920), log=print) -> Prepared:
    s = load_settings()
    min_total = min_seconds(target)
    _cleanup_old(WORK_DIR)
    work = WORK_DIR / ("job_" + time.strftime("%Y%m%d-%H%M%S"))
    work.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    log("1/4 Voice-Over wird gesprochen…")
    voices = _speak(script, voice_name, work, s["speech_rate"], min_total, log)
    log("2/4 Footage wird gesucht und von der KI ausgewählt…")
    durations = render.scene_durations(voices, min_total)
    sel = footage.collect(script.scenes, durations, src, work / "footage", size[0] / size[1],
                          use_article, use_free, bool(s.get("youtube_all")), log)
    return Prepared(_key(script, voice_name, use_article, use_free, target, size), src, voice_name, target,
                    tuple(size), min_total, work, voices, durations, sel, t0)


def choose(prep: Prepared, script: Script, overrides: dict[int, int], log=print) -> None:
    """Apply the user's footage numbers (scene index -> number)."""
    clean_over = {i: n for i, n in overrides.items() if n and i < len(script.scenes) - 1}
    if clean_over and any(prep.sel.nums[i] != n for i, n in clean_over.items()):
        prep.sel = footage.assign(prep.sel.pool, script.scenes, prep.durations, prep.size[0] / prep.size[1],
                                  prep.sel.smart, clean_over, log)


def finish(prep: Prepared, script: Script, log=print) -> dict:
    s = load_settings()
    slug = re.sub(r"[^a-z0-9]+", "-", " ".join(script.headline).lower()).strip("-")[:40] or "video"
    vid = time.strftime("%Y%m%d-%H%M%S") + "_" + slug
    out = OUTPUT_DIR / vid
    out.mkdir(parents=True)
    work = prep.work
    try:
        log(f"3/4 Video wird gerendert ({prep.size[0]}×{prep.size[1]})…")

        def mix(voice_track, total, sfx):
            return music.mix(voice_track, music.background(total + 1, work), float(s["music_volume"]), sfx)

        render.render(script, prep.sel.clips, prep.voices, mix, out / "video.mp4", out / "cover.png", work,
                      prep.size, prep.min_total, log)
        real = video_seconds(out / "video.mp4")
        if prep.min_total and real < prep.min_total - 0.05:
            raise RuntimeError(f"Kontrolle fehlgeschlagen: Video ist nur {real:.1f} s lang "
                               f"(Minimum {prep.min_total:.0f} s).")
    except Exception:
        shutil.rmtree(out, ignore_errors=True)
        raise

    log("4/4 Posting-Paket wird erstellt…")
    caption = script.caption.strip() + "\n\n" + " ".join(script.hashtags)
    if prep.sel.credits:
        caption += "\n\n" + "\n".join(prep.sel.credits)
    (out / "caption.txt").write_text(caption, encoding="utf-8")
    (out / "script.json").write_text(script.to_json(), encoding="utf-8")
    learn.log_video(vid, script.scenes[0].text, real, prep.voice_name)
    log(f"Fertig in {mmss(time.time() - prep.started)} Min.: {out}")
    return {"id": vid, "folder": out, "video": out / "video.mp4", "cover": out / "cover.png",
            "caption": caption, "seconds": real, "min_seconds": prep.min_total}


def is_current(prep: Prepared | None, script: Script, voice_name, use_article, use_free, target, size) -> bool:
    return prep is not None and prep.key == _key(script, voice_name, use_article, use_free, target, size) \
        and prep.work.exists()


def make_video(script: Script, src: extract.Source, voice_name: str, use_article=True, use_free=True,
               target: float = 40, size: tuple[int, int] = (1080, 1920), log=print) -> dict:
    prep = prepare(script, src, voice_name, use_article, use_free, target, size, log)
    try:
        return finish(prep, script, log)
    finally:
        shutil.rmtree(prep.work, ignore_errors=True)


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
