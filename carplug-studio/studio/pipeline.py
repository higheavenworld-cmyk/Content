"""End-to-end: source → script → voice → footage → render → posting package."""
from __future__ import annotations

import re
import shutil
import time
from pathlib import Path

from . import extract, footage, learn, music, render, voice
from .config import OUTPUT_DIR, WORK_DIR, load_settings
from .script import Script, write_script


def load_source(url: str, files: list[str]) -> extract.Source:
    src = extract.Source()
    if url and url.strip():
        src.merge(extract.from_url(url.strip()))
    if files:
        src.merge(extract.from_files(files))
    if not src.text and not src.media_files:
        raise ValueError("Bitte einen Link eingeben oder Dateien hochladen.")
    return src


def make_script(src: extract.Source, length: str) -> tuple[Script, str]:
    if not src.text.strip():
        raise ValueError("In der Quelle wurde kein Text gefunden. Lade eine Text-/PDF-Datei hoch oder nutze einen Link.")
    return write_script(src.title, src.text, src.url, length)


def make_video(script: Script, src: extract.Source, voice_name: str, use_article=True, use_free=True,
               log=print) -> dict:
    s = load_settings()
    slug = re.sub(r"[^a-z0-9]+", "-", " ".join(script.headline).lower()).strip("-")[:40] or "video"
    vid = time.strftime("%Y%m%d-%H%M%S") + "_" + slug
    out = OUTPUT_DIR / vid
    work = WORK_DIR / vid
    out.mkdir(parents=True)
    work.mkdir(parents=True)

    log("1/4 Voice-Over wird gesprochen…")
    voices = voice.synth_many([sc.text for sc in script.scenes], voice_name, work, s["speech_rate"], log)

    log("2/4 Footage wird gesucht…")
    clips, credits = footage.collect(script.scenes, src.media_files, src.image_urls, work / "footage",
                                     use_article, use_free, s.get("pexels_key", ""), log)

    log("3/4 Video wird gerendert (dauert 1–3 Minuten)…")

    def mix(voice_track, total, sfx):
        return music.mix(voice_track, music.background(total + 1, work), float(s["music_volume"]), sfx)

    total = render.render(script, clips, voices, mix, out / "video.mp4", out / "cover.png", work, log)

    log("4/4 Posting-Paket wird erstellt…")
    caption = script.caption.strip() + "\n\n" + " ".join(script.hashtags)
    if credits:
        caption += "\n\n" + "\n".join(credits)
    (out / "caption.txt").write_text(caption, encoding="utf-8")
    (out / "script.json").write_text(script.to_json(), encoding="utf-8")
    learn.log_video(vid, script.scenes[0].text, total, voice_name)
    shutil.rmtree(work, ignore_errors=True)
    log(f"Fertig: {out}")
    return {"id": vid, "folder": out, "video": out / "video.mp4", "cover": out / "cover.png",
            "caption": caption, "seconds": total}
