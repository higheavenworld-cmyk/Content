"""End-to-end: source → script → voice → footage → render → posting package."""
from __future__ import annotations

import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from . import extract, footage, learn, music, render, translate, voice
from .config import OUTPUT_DIR, WORK_DIR, load_settings
from .script import Scene, Script, min_seconds, write_script


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
    shots: list = None        # render.Shot per cut (pictures change every few seconds)
    slots: list = None        # Scene-like entry per shot, used for footage matching


def _pace() -> float | None:
    return render.PACES.get(load_settings().get("pace", ""), 1.15)


def _doc() -> bool:
    return str(load_settings().get("caption_style", "Doku")).startswith("Doku")


def plan_motion(prep: "Prepared", doc: bool) -> None:
    """Decide which cuts become CarPlug's own motion graphics (numbers, text cards, HUD frames, split screens).
    Rule: a cut whose footage does not clearly fit NEVER shows that footage. Not too many: max ~1/3."""
    from . import motion
    from .script import NUM_UNIT
    shots, sel = prep.shots, prep.sel
    imgs = sorted([c for c in sel.pool if c.kind == "image"], key=lambda c: -c.subject)
    safe = [c for c in imgs if c.shows in ("subject", "") and c.topic == "own"] or imgs  # surely on topic
    used_nums: set[str] = set()
    since, n_mg = 99, 0
    limit = max(1, int(len(shots) * 0.34))
    variety = ["hud", "typo", "split"]
    v = 0
    prev_kind = None
    for k, sh in enumerate(shots):
        sh.mg = None
        if k == 0:  # the very first cut is always real footage (strongest scroll-stopper)
            continue
        weak = k < len(sel.weak) and sel.weak[k]
        nxt = shots[k + 1].text.split()[:2] if k + 1 < len(shots) and shots[k + 1].scene == sh.scene else []
        m = NUM_UNIT.search(" ".join([sh.text] + nxt))  # number and unit can fall into two cuts ("als 4" | "Tonnen")
        if m and not re.search(r"\d", sh.text):
            m = None
        keys = [w for w in sh.text.split() if motion.word_style(w) == "key"]
        keyword = max(keys, key=len) if keys else ""

        def safe_hud():
            c = safe[k % len(safe)]
            sh.clip = footage.Clip(c.clean_path, "image", c.credit, zoom=1.25, fx=(k % 3) / 2)
            sh.mg = {"type": "hud", "keyword": keyword}

        if doc and m and m.group(0) not in used_nums and (since >= 2 or weak) and n_mg < limit + 2:
            used_nums.add(m.group(0))
            sh.mg = {"type": "number", "text": m.group(0).strip()}
        elif weak:  # never show footage that does not fit – alternate the replacements so it stays varied
            if doc and keys and prev_kind != "typo":
                sh.mg = {"type": "typo"}
            elif safe:
                safe_hud()
            else:
                sh.mg = {"type": "typo"} if doc else None
        elif doc and since >= 5 and n_mg < limit:
            kind = variety[v % len(variety)]
            v += 1
            if kind == "split":
                other = next((c for c in safe if str(c.clean_path) != str(sh.clip.path)), None)
                if other is None or sh.clip.kind != "image":
                    kind = "hud"
                else:
                    sh.mg = {"type": "split", "clip2": footage.Clip(other.clean_path, "image", other.credit)}
            if kind == "hud":
                sh.mg = {"type": "hud", "keyword": keyword}
            elif kind == "typo":
                sh.mg = {"type": "typo"} if keys else {"type": "hud", "keyword": ""}
        prev_kind = (sh.mg or {}).get("type")
        if sh.mg:
            since, n_mg = 0, n_mg + 1
        else:
            since += 1


def _key(script: Script, voice_name, use_article, use_free, target, size) -> tuple:
    return (tuple(sc.text for sc in script.scenes), voice_name, use_article, use_free, target, tuple(size),
            _pace(), _doc())


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
    shots = render.plan_shots(script, voices, durations, _pace())
    slots = [Scene(sh.text, script.scenes[sh.scene].overlay, script.scenes[sh.scene].query) for sh in shots]
    sel = footage.collect(slots, [sh.dur for sh in shots], src, work / "footage", size[0] / size[1],
                          use_article, use_free, bool(s.get("youtube_all")), log)
    for sh, clip in zip(shots, sel.clips):
        sh.clip = clip
    prep = Prepared(_key(script, voice_name, use_article, use_free, target, size), src, voice_name, target,
                    tuple(size), min_total, work, voices, durations, sel, t0, shots, slots)
    plan_motion(prep, _doc())
    n_mg = sum(1 for sh in shots if sh.mg)
    log(f"Schnitte: {len(shots)} · davon {n_mg} eigene Motion Graphics.")
    return prep


def scene_numbers(prep: Prepared) -> dict[int, list[int]]:
    """Footage numbers per scene (one per shot)."""
    out: dict[int, list[int]] = {}
    for sh, num in zip(prep.shots, prep.sel.nums):
        out.setdefault(sh.scene, []).append(num)
    return out


def choose(prep: Prepared, script: Script, overrides: dict[int, list[int]], log=print) -> None:
    """Apply the user's footage numbers: {scene index: [number per shot, …]}."""
    slot_over: dict[int, int] = {}
    for scene, nums in overrides.items():
        idx = [k for k, sh in enumerate(prep.shots) if sh.scene == scene]
        nums = [n for n in nums if n]
        if not idx or not nums:
            continue
        for j, k in enumerate(idx):
            slot_over[k] = nums[j] if j < len(nums) else 0  # fewer numbers than cuts: the AI fills the rest
    slot_over = {k: n for k, n in slot_over.items() if n}
    changed = {k: n for k, n in slot_over.items() if prep.sel.nums[k] != n}  # what the user really changed
    if not changed:
        return  # numbers the program filled in itself are not "wishes" – the safety checks stay active
    keep = {k: n for k, n in slot_over.items() if k not in changed}
    old_weak = list(prep.sel.weak)
    prep.sel = footage.assign(prep.sel.pool, prep.slots, [sh.dur for sh in prep.shots],
                              prep.size[0] / prep.size[1], prep.sel.smart, {**keep, **changed}, log, has_outro=False)
    for k in keep:
        prep.sel.weak[k] = old_weak[k]
    for sh, clip in zip(prep.shots, prep.sel.clips):
        sh.clip = clip
    plan_motion(prep, _doc())


def finish(prep: Prepared, script: Script, log=print) -> dict:
    s = load_settings()
    slug = re.sub(r"[^a-z0-9]+", "-", " ".join(script.headline).lower()).strip("-")[:40] or "video"
    vid = time.strftime("%Y%m%d-%H%M%S") + "_" + slug
    out = OUTPUT_DIR / vid
    out.mkdir(parents=True)
    work = prep.work
    try:
        log(f"3/4 Video wird gerendert ({prep.size[0]}×{prep.size[1]})…")

        def mix(voice_track, total, events):
            bg = music.background(total + 1, work) if s.get("music_on") else None
            return music.mix(voice_track, bg, float(s["music_volume"]), events)

        render.render(script, prep.shots, prep.voices, mix, out / "video.mp4", out / "cover.png", work,
                      prep.size, prep.min_total, log, doc=_doc(), look=bool(s.get("look", True)))
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
