"""Find fitting, never-repeating footage for every scene — no account or API key needed.

Sources (in this priority):
  1. media from your links (TikTok/YouTube/Instagram videos, post images) and uploads
     → for videos with speech the exact moment that matches the sentence is used
  2. images from the article
  3. free footage: YouTube videos with Creative-Commons licence, Wikimedia Commons (images + videos),
     Openverse (images) — searched separately for every scene
Every picture/video part runs through the text/watermark filter (clean.py) first.
Each scene gets its own picture or a different, non-overlapping part of a video.
"""
from __future__ import annotations

import hashlib
import heapq
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote_plus

import numpy as np
import requests
from PIL import Image

from . import clean, vision
from .extract import IMAGE_EXT, UA, VIDEO_EXT

HEAD = {"User-Agent": "CarPlugStudio/1.0 (social video tool)"}
ORIGIN_WEIGHT = {"link": 3.0, "upload": 3.0, "article": 2.0, "youtube": 1.6, "commons": 1.2, "openverse": 1.0}
STOP = set("der die das und oder mit von für auf ist sind ein eine einen dem den des im in zu zum zur bei aus "
           "auch noch nur wie was wird werden hat haben als an es sie er wir ihr mehr über unter nach vor "
           "the and for with from this that are was has have its into".split())
TOPICS = {  # German words in the sentence -> English words for picture search / matching
    r"innenraum|cockpit|armaturen|display|bildschirm|lenkrad|sitz": "interior",
    r"motor|zylinder|v8|v12|hubraum|verbrenner": "engine",
    r"felge|räder|rad\b|reifen|bremse": "wheels",
    r"akku|batterie|lade|ladeleistung|ladesäule|strom": "charging",
    r"reichweite|autobahn|fahrt|fährt|unterwegs": "driving",
    r"beschleunig|sprint|höchstgeschwindigkeit|km/h|topspeed|schnell|rennstrecke": "racing",
    r"heck|kofferraum|rückleuchte": "rear",
    r"front|kühlergrill|niere|scheinwerfer|gesicht": "front",
    r"preis|euro|kosten|kostet|händler": "showroom",
    r"panzer|schutz|beschuss|sicherheit": "armored",
    r"design|optik|karosserie|silhouette|look": "design",
    r"werk|fabrik|produktion|montage": "factory",
    r"cabrio|dach": "convertible",
    r"suv|gelände|offroad": "offroad",
}


@dataclass
class Clip:
    path: Path
    kind: str            # "image" | "video"
    credit: str = ""
    start: float = 0.0   # video: where the part starts
    vf: str = ""         # video: ffmpeg crop / watermark-removal filters
    flip: bool = False   # mirrored (only when footage has to be reused)
    zoom: float = 1.0    # > 1: detail shot of the same picture
    fx: float = 0.5      # horizontal focus of the crop (0 = left, 1 = right)


@dataclass
class Cand:
    kind: str
    origin: str
    path: Path | None = None
    url: str | None = None
    meta: str = ""                        # title / description / tags for matching
    credit: str = ""
    clean_path: Path | None = None        # image after text removal
    video: clean.VideoInfo | None = None
    transcript: list = field(default_factory=list)
    used: list = field(default_factory=list)  # used (start, end) parts of a video
    uses: int = 0
    dhash: int | None = None
    num: int = 0                          # number shown to the user (1, 2, 3 …)
    emb: np.ndarray | None = None         # image understanding: one vector (image) or one per sample (video)
    subject: float = 0.0                  # how clearly it shows the topic (e.g. the car)
    topic: str = "own"                    # own (link/upload/article) | subject | topic search
    shows: str = ""                       # what the AI thinks it shows: subject | other-car | other


@dataclass
class Selection:
    """Result of footage planning – shown to the user, who can swap pictures by number."""
    clips: list[Clip]
    nums: list[int]               # chosen footage number per scene (0 = outro, no footage)
    credits: list[str]
    pool: list[Cand]
    smart: bool                   # True = image understanding was used
    weak: list = field(default_factory=list)    # per slot: footage does not clearly fit -> own motion graphic
    reused: list = field(default_factory=list)  # per slot: picture had to be reused


# --- public entry --------------------------------------------------------------
def collect(slots, durations, src, work: Path, aspect: float, use_article=True, use_free=True,
            youtube_all=False, log=print) -> Selection:
    """slots: one entry per shot (Scene-like: text, overlay, query) – there is no outro slot."""
    pool = build_pool(slots, durations, src, work, aspect, use_article, use_free, youtube_all, log, has_outro=False)
    smart = rate_pool(pool, slots, log)
    return assign(pool, slots, durations, aspect, smart, {}, log, has_outro=False)


def build_pool(scenes, durations, src, work: Path, aspect: float, use_article=True, use_free=True,
               youtube_all=False, log=print, has_outro: bool = True) -> list[Cand]:
    work.mkdir(parents=True, exist_ok=True)
    pool: list[Cand] = []
    lock = threading.Lock()
    for p in src.media_files:
        info = src.media_info.get(str(p), {})
        kind = "video" if Path(p).suffix.lower() in VIDEO_EXT else "image"
        pool.append(Cand(kind, info.get("origin", "upload"), path=Path(p), meta=info.get("title", "") + " " +
                         info.get("text", ""), credit=info.get("credit", ""),
                         transcript=src.transcripts.get(str(p), [])))
    if use_article:
        pool += [Cand("image", "article", url=u, meta=src.title) for u in src.image_urls[:20]]
    log(f"Prüfe {len(pool)} Bilder/Videos aus deinen Quellen auf Text und Wasserzeichen…")
    pool = _prepare_all(pool, work, aspect, lock)
    log(f"{len(pool)} saubere Bilder/Videos aus deinen Quellen.")
    n_needed = max(len(scenes) - (1 if has_outro else 0), 1)
    if use_free and not vision.available():
        log("Freies Footage wird nur mit KI-Bildverständnis gesucht (sonst Fehlergefahr) – nutze nur deine Quellen.")
        use_free = False
    if use_free:  # always add free footage – own creative mix, not only the link's material
        for q, kind in _queries(scenes):
            if _capacity(pool, durations) >= n_needed * 2.5:  # plenty of choice so the AI can be strict
                break
            log(f"Suche Footage: „{q}“…")
            pool += _prepare_all(_search(q, work, youtube_all, kind), work, aspect, lock)
    if not pool:
        raise RuntimeError("Kein sauberes Bildmaterial gefunden. Lade eigene Bilder/Videos hoch oder nutze andere Links.")
    for i, c in enumerate(pool, 1):
        c.num = i
    return pool


def subject_of(scenes) -> str:
    return next((s.query for s in scenes if s.query), "")


def rate_pool(pool: list[Cand], scenes, log=print) -> bool:
    """Image understanding: vectors for every picture / video frame, and how clearly each shows the topic.
    Pictures that clearly show something else (other cars, people, logos …) are removed."""
    if not vision.CACHE.exists() or not any(vision.CACHE.rglob("*.onnx")):
        log("KI-Bildverständnis wird beim ersten Mal geladen (ca. 1,5 GB, nur einmalig)…")
    if not vision.available():
        log("Hinweis: KI-Bildverständnis nicht verfügbar – Auswahl nach Stichworten. "
            + (f"({vision.last_error()})" if vision.last_error() else ""))
        return False
    log("KI bewertet, was auf den Bildern zu sehen ist…")
    imgs = [c for c in pool if c.kind == "image"]
    if imgs:
        for c, e in zip(imgs, vision.embed_images([c.clean_path for c in imgs])):
            c.emb = e[None, :]
    for c in pool:
        if c.kind == "video":
            c.emb = vision.embed_images(c.video.frames) if c.video.frames else None
    subject = subject_of(scenes) or (scenes[0].text if scenes else "")
    t_subj = vision.embed_texts([f"Foto: {subject}"])[0]
    for c in pool:
        if c.emb is None or not len(c.emb):
            c.subject = -1.0
            continue
        sims = np.sort(c.emb @ t_subj)[::-1]
        c.subject = float(sims[: max(1, len(sims) // 3)].mean())  # video: its best third of frames
    _check_cars(pool, subject, log)
    return True


DISTRACTORS = ["Mercedes-Benz S-Class", "Mercedes-Benz G-Class", "Audi A8", "Audi Q7", "Porsche 911", "Porsche Taycan",
               "Tesla Model 3", "Tesla Model S", "Volkswagen Golf", "Volkswagen ID.4", "Toyota Corolla", "Ford Mustang",
               "Range Rover", "Rolls-Royce Phantom", "Ferrari", "Lamborghini", "BMW 3 Series", "BMW X5", "BMW i4",
               "Hyundai Ioniq 5", "Kia EV6", "BYD Seal", "Volvo XC90", "Mini Cooper", "Fiat 500", "Skoda Octavia"]
NON_CAR = ["Foto einer Person", "Foto eines Gebäudes", "Foto einer Landschaft", "Foto eines technischen Details",
           "Foto eines Gegenstands", "Grafik mit Text oder Logo"]


def _check_cars(pool: list[Cand], subject: str, log) -> None:
    """Free footage must show exactly the right car (or no car at all for sub-topics like 'bulletproof glass'),
    and must look like the car in your own links. Wrong model / wrong generation -> removed."""
    if not subject:
        return
    s_low = subject.lower()
    others = [d for d in DISTRACTORS if d.lower() not in s_low and s_low.split(" ")[-1] not in d.lower()]
    labels = [f"Foto eines {subject}"] + [f"Foto eines {d}" for d in others] + NON_CAR
    t = vision.embed_texts(labels)
    own = [c for c in pool if c.topic == "own" and c.emb is not None and len(c.emb)]
    centroid = None
    if own:
        centroid = vision._norm(np.mean([c.emb.mean(axis=0) for c in own], axis=0))
        own_cons = sorted(float((c.emb.mean(axis=0) @ centroid)) for c in own)
        min_cons = own_cons[max(0, len(own_cons) // 5)] - 0.06
    removed_wrong = removed_style = 0
    for c in list(pool):
        if c.emb is None or not len(c.emb):
            continue
        sims = c.emb.mean(axis=0) @ t.T
        best = int(np.argmax(sims))
        c.shows = "subject" if best == 0 else ("other-car" if best <= len(others) else "other")
        if c.topic == "own":
            continue  # your links/uploads are the reference
        if c.shows == "other-car":
            pool.remove(c)
            removed_wrong += 1
            continue
        if c.shows == "subject" and centroid is not None and float(c.emb.mean(axis=0) @ centroid) < min_cons:
            pool.remove(c)  # right model name, but looks different from the car in your link (generation/colour)
            removed_style += 1
    if removed_wrong or removed_style:
        log(f"Aussortiert: {removed_wrong} Bild(er) mit falschem Auto/Motiv, {removed_style} passen optisch nicht "
            f"zum Footage aus deinem Link.")


# --- assignment ----------------------------------------------------------------------
def assign(pool: list[Cand], scenes, durations, aspect: float, smart: bool, overrides: dict[int, int],
           log=print, has_outro: bool = True) -> Selection:
    """Give every scene the best fitting footage overall (not just the first good one), never twice.
    overrides: {scene index: footage number} chosen by the user."""
    for c in pool:
        c.used, c.uses = [], 0
    n = len(scenes)
    last = n - 1 if has_outro else n  # outro: logo screen, no footage
    by_num = {c.num: c for c in pool}
    fits = _fit_matrix(pool, scenes, smart)
    chosen: dict[int, tuple[Cand, float | None, clean.VideoCut | None]] = {}

    for i, num in overrides.items():  # 1) the user's choice always wins
        c = by_num.get(int(num))
        if c is None or i >= last:
            continue
        plan = _video_plan(c, i, durations[i], aspect, fits, smart, scenes[i].text) if c.kind == "video" else None
        if c.kind == "video" and plan is None:
            c.used.clear()
            plan = _video_plan(c, i, durations[i], aspect, fits, smart, scenes[i].text)
        chosen[i] = (c, *(plan or (None, None)))
        c.uses += 1
        if plan:
            c.used.append((plan[0], plan[0] + durations[i]))

    max_share = max(2, int(0.4 * last))  # no single link video may dominate (own edit, not a re-upload)

    def too_many_in_row(i: int, c: Cand) -> bool:
        same = lambda j: j in chosen and chosen[j][0] is c  # noqa: E731
        return (same(i - 1) and same(i - 2)) or (same(i + 1) and same(i + 2)) or (same(i - 1) and same(i + 1))

    heap = []  # 2) best (scene, footage) pairs first
    for i in range(last):
        if i in chosen:
            continue
        for c in pool:
            heapq.heappush(heap, (-_pair_score(c, i, fits, smart, scenes[i]), i, c.num))
    while heap and len(chosen) < last:
        neg, i, num = heapq.heappop(heap)
        if i in chosen:
            continue
        c = by_num[num]
        if c.kind == "image":
            if c.uses:
                continue
            chosen[i] = (c, None, None)
            c.uses += 1
            continue
        if too_many_in_row(i, c) or (c.topic == "own" and c.uses >= max_share):
            continue
        plan = _video_plan(c, i, durations[i], aspect, fits, smart, scenes[i].text)
        if plan is None:
            continue
        chosen[i] = (c, *plan)
        c.uses += 1
        c.used.append((plan[0], plan[0] + min(durations[i], c.video.dur)))

    reused = 0
    for i in range(last):  # 3) not enough material: reuse a picture mirrored, never the neighbour's
        if i in chosen:
            continue
        neighbours = {chosen[j][0].num for j in (i - 1, i + 1) if j in chosen}
        options = [c for c in pool if c.num not in neighbours] or pool
        c = min(options, key=lambda x: (x.uses, -_pair_score(x, i, fits, smart, scenes[i])))
        c.uses += 1
        reused += 1
        if c.kind == "image":
            chosen[i] = (c, None, None, c.uses)
        else:
            c.used.clear()
            plan = _video_plan(c, i, durations[i], aspect, fits, smart, scenes[i].text)
            chosen[i] = (c, *(plan or (0.0, clean.VideoCut(0.0, (0, 0, c.video.w, c.video.h)))), c.uses)
    if reused:
        log(f"Hinweis: Zu wenig unterschiedliches Material – {reused} Schnitt(e) zeigen einen anderen Ausschnitt "
            f"eines schon genutzten Bildes. Mehr Links/Bilder = mehr Abwechslung.")

    weak, reused_flags = [], []
    for i in range(last):
        c = chosen[i][0]
        fit = _fit_only(c, i, fits) if smart else 0.0
        said = c.kind == "video" and _transcript_time(c, scenes[i].text) is not None
        weak.append(bool(smart and not said and i not in overrides and fit < -0.15))
        reused_flags.append(len(chosen[i]) > 3)

    clips, nums, credits = [], [], []
    variants = [(1.0, 0.5, False), (1.45, 0.25, False), (1.45, 0.75, True), (1.8, 0.5, False), (1.3, 0.1, True)]
    for i in range(n):
        if i == last:
            ref = clips[-1] if clips else None
            clips.append(ref or Clip(pool[0].clean_path or pool[0].path, pool[0].kind))
            nums.append(0)
            continue
        c, start, cut, *rep_n = chosen[i]
        zoom, fx, flip = variants[(rep_n[0] - 1) % len(variants)] if rep_n else (1.0, 0.5, False)
        if c.kind == "image":
            clips.append(Clip(c.clean_path, "image", c.credit, flip=flip, zoom=zoom, fx=fx))
        else:
            clips.append(Clip(c.path, "video", c.credit, start or 0.0,
                              clean.ffmpeg_filters(cut, c.video.w, c.video.h), flip=flip))
        nums.append(c.num)
        if c.credit and c.credit not in credits:
            credits.append(c.credit)
    log(f"Footage: {len(set(nums) - {0})} verschiedene Bilder/Videos für {last} Schnitte"
        + (" (KI-Bildverständnis)." if smart else " (Stichworte)."))
    return Selection(clips, nums, credits, pool, smart, weak, reused_flags)


def _fit_only(c: Cand, i: int, fits) -> float:
    """How well the picture shows what is said in slot i (z-score, 0 = average)."""
    if fits is None or c.num not in fits:
        return 0.0
    col = fits[c.num][:, i]
    return float(np.sort(col)[::-1][: max(1, len(col) // 4)].mean())


def _fit_matrix(pool, scenes, smart):
    """How well each picture (or each video frame) matches each scene's sentence."""
    if not smart:
        return None
    subject = subject_of(scenes)
    t = vision.embed_texts([vision.describe_scene(s.text, subject) for s in scenes])
    fits = {}
    all_vals = []
    for c in pool:
        if c.emb is None or not len(c.emb):
            continue
        m = c.emb @ t.T  # (frames, scenes)
        fits[c.num] = m
        all_vals.append(m.ravel())
    if all_vals:
        v = np.concatenate(all_vals)
        mu, sd = float(v.mean()), float(v.std() or 1.0)
        for k in fits:
            fits[k] = (fits[k] - mu) / sd
    subj = np.array([c.subject for c in pool])
    s_mu, s_sd = float(subj.mean()), float(subj.std() or 1.0)
    for c in pool:
        c.subject_z = (c.subject - s_mu) / s_sd
    return fits


def _pair_score(c: Cand, i: int, fits, smart: bool, sc) -> float:
    bonus = {"upload": 0.6, "link": 0.4, "article": 0.4}.get(c.origin, 0.0)
    if smart and fits is not None and c.num in fits:
        col = fits[c.num][:, i]
        fit = float(np.sort(col)[::-1][: max(1, len(col) // 4)].mean())  # video: its best quarter
        subj = getattr(c, "subject_z", 0.0)
        w_subj = 1.2 if i == 0 else 0.5  # the opening picture (cover) must show the topic clearly
        score = fit + w_subj * subj + bonus
    else:
        kw = _scene_keywords(sc)
        score = ORIGIN_WEIGHT.get(c.origin, 1.0) + 2.5 * len(kw & _tokens(c.meta)) / max(3, len(kw))
    if c.kind == "video":
        if _transcript_time(c, sc.text) is not None:
            score += 2.0  # exactly what is being said
        score += 0.2
    return score


def _video_plan(c: Cand, i: int, dur: float, aspect: float, fits, smart: bool, text: str):
    """Best free part of a video for scene i: what is said there, or the frames that fit best."""
    v = c.video
    need = min(dur, v.dur)
    if v.dur < 1.5:
        return None
    starts: list[float] = []
    want = _transcript_time(c, text)
    if want is not None:
        starts.append(max(0.0, min(want - 0.3, v.dur - need)))
    if smart and fits is not None and c.num in fits and v.samples:
        col = fits[c.num][:, i]
        ranked = sorted(range(len(col)), key=lambda k: -col[k])
        starts += [max(0.0, min(v.samples[k][0] - need / 2, v.dur - need)) for k in ranked]
    starts += list(np.arange(0.0, max(v.dur - need, 0) + 0.01, max(1.0, need / 2)))
    for s in starts:
        if any(s < b and a < s + need for a, b in c.used):
            continue
        cut = clean.plan_cut(v, s, need, aspect)
        if cut:
            return s, cut
    return None


def mg_preview(kind: str, work: Path) -> Path:
    """Small placeholder picture for motion-graphic cuts in the preview gallery."""
    dst = work / "vorschau" / f"mg_{kind}.jpg"
    if not dst.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        from PIL import ImageDraw
        im = Image.new("RGB", (240, 360), (243, 239, 231) if kind == "typo" else (20, 10, 10))
        d = ImageDraw.Draw(im)
        d.text((20, 160), "Text-Karte" if kind == "typo" else "123 PS", fill=(30, 30, 30) if kind == "typo" else
               (255, 70, 60))
        im.save(dst, quality=85)
    return dst


def thumbnails(sel: Selection, work: Path) -> list[tuple[str, str]]:
    """Preview pictures of all found footage, numbered (for the user's choice)."""
    out = []
    tdir = work / "vorschau"
    tdir.mkdir(parents=True, exist_ok=True)
    for c in sel.pool:
        dst = tdir / f"{c.num:03d}.jpg"
        if not dst.exists():
            try:
                if c.kind == "image":
                    im = Image.open(c.clean_path).convert("RGB")
                else:
                    im = c.video.frames[len(c.video.frames) // 2] if c.video.frames else Image.new("RGB", (9, 16))
                im.thumbnail((360, 360))
                im.save(dst, quality=85)
            except Exception:
                continue
        label = f"Nr. {c.num} · {'🎬 Video' if c.kind == 'video' else '🖼️ Bild'} · {c.origin}"
        out.append((str(dst), label))
    return out


# --- preparing (download + text filter + duplicates) ------------------------------------
def _prepare_all(cands: list[Cand], work: Path, aspect: float, lock) -> list[Cand]:
    with ThreadPoolExecutor(max_workers=4) as ex:
        prepared = list(ex.map(lambda c: _prepare(c, work, aspect), cands))
    out = []
    with lock:
        hashes: list[int] = []
        for c in prepared:
            if c is None:
                continue
            if c.dhash is not None:
                if any(bin(c.dhash ^ h).count("1") <= 6 for h in hashes):
                    continue  # same picture in another size / from another site
                hashes.append(c.dhash)
            out.append(c)
    return out


def _prepare(c: Cand, work: Path, aspect: float) -> Cand | None:
    try:
        if c.path is None:
            c.path = _download(c.url, work, video=c.kind == "video")
            if c.path is None:
                return None
        if c.kind == "image":
            c.clean_path = clean.clean_image(c.path, work, aspect)
            if c.clean_path is None:
                return None
            c.dhash = _dhash(c.clean_path)
        else:
            c.video = clean.analyze_video(c.path)
            if c.video is None:
                return None
        return c
    except Exception:
        return None


def _capacity(pool: list[Cand], durations: list[float]) -> float:
    avg = max(np.mean(durations), 2.0) if durations else 4.0
    return sum(1 if c.kind == "image" else c.video.dur / avg for c in pool)


def _dhash(p: Path) -> int:
    g = np.asarray(Image.open(p).convert("L").resize((9, 8), Image.BILINEAR), dtype=np.int16)
    bits = (g[:, 1:] > g[:, :-1]).flatten()
    return int("".join("1" if b else "0" for b in bits), 2)


# --- keyword matching (fallback without image understanding) -------------------------------
def _tokens(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-zäöüß0-9]{3,}", s.lower()) if w not in STOP}


def _scene_keywords(sc) -> set[str]:
    kw = _tokens(sc.query + " " + sc.overlay + " " + sc.text)
    for pat, en in TOPICS.items():
        if re.search(pat, sc.text.lower()):
            kw.add(en)
    return kw


def _transcript_time(c: Cand, text: str) -> float | None:
    words = _tokens(text)
    if not c.transcript or len(words) < 3:
        return None
    best, best_t = 0.0, None
    for i, (s, _, t) in enumerate(c.transcript):  # look at 2 neighbouring segments together
        joined = t + " " + (c.transcript[i + 1][2] if i + 1 < len(c.transcript) else "")
        ov = len(words & _tokens(joined)) / len(words)
        if ov > best:
            best, best_t = ov, s
    return best_t if best >= 0.35 else None


# --- searching -------------------------------------------------------------------------
def _queries(slots) -> list[tuple[str, str]]:
    """Searches: the exact subject (e.g. 'BMW 7 Series' + interior/front/…) and one sub-topic per cut
    (the key word that is spoken, translated to English, e.g. 'Panzerglas' -> 'bulletproof glass').
    No generic car searches – they bring the wrong cars."""
    from . import motion, translate
    base = subject_of(slots)
    out: list[tuple[str, str]] = []
    if base:
        out.append((base, "subject"))
        for sl in slots:
            for pat, en in TOPICS.items():
                if re.search(pat, sl.text.lower()):
                    out.append((f"{base} {en}", "subject"))
                    break
    words: list[str] = []
    for sl in slots:
        keys = [re.sub(r"[^\wäöüÄÖÜß-]", "", w) for w in sl.text.split() if motion.word_style(w) == "key"]
        keys = [k for k in keys if len(k) >= 4 and k.lower() not in base.lower()]
        if keys:
            words.append(max(keys, key=len))
    words = list(dict.fromkeys(words))[:14]
    if words:
        en = translate.to_english("\n".join(words)).split("\n")
        en = en if len(en) == len(words) else words
        for w in en:
            w = w.strip()
            if w:
                out.append((w, "topic"))
    seen, uniq = set(), []
    for q, kind in out:
        if q.lower() not in seen:
            seen.add(q.lower())
            uniq.append((q, kind))
    return uniq


def _search(q: str, work: Path, youtube_all: bool, kind: str = "subject") -> list[Cand]:
    found: list[Cand] = []
    jobs = [lambda: _commons(q, "bitmap", 6), lambda: _openverse(q, 6)]
    if kind == "subject":  # videos only for the exact subject (sub-topics: pictures)
        jobs += [lambda: _youtube(q, work, 3, cc_only=not youtube_all), lambda: _commons(q, "video", 2)]
    with ThreadPoolExecutor(max_workers=4) as ex:
        for res in ex.map(lambda f: f(), jobs):
            for c in res:
                c.topic = kind
            found += res
    return found


def _youtube(query: str, work: Path, n: int, cc_only: bool) -> list[Cand]:
    try:
        import yt_dlp
        from yt_dlp.utils import download_range_func

        from .linkgrab import ytdlp_opts
        sp = "EgIwAQ%253D%253D" if cc_only else "EgIQAQ%253D%253D"  # Creative Commons / only videos
        url = f"https://www.youtube.com/results?search_query={quote_plus(query)}&sp={sp}"
        with yt_dlp.YoutubeDL(ytdlp_opts(work, extract_flat=True, playlistend=n * 4, simulate=True,
                                         writesubtitles=False, writeautomaticsub=False)) as y:
            res = y.extract_info(url, download=False) or {}
    except Exception:
        return []
    out = []
    for e in res.get("entries") or []:
        if len(out) >= n:
            break
        d = e.get("duration") or 0
        if not e.get("id") or not 15 <= d <= 1800:
            continue
        a = d * 0.12
        opts = ytdlp_opts(work, writesubtitles=False, writeautomaticsub=False,
                          format="bv*[height<=1080][ext=mp4]/bv*[height<=1080]",
                          outtmpl=str(work / "yt_%(id)s.%(ext)s"),
                          download_ranges=download_range_func(None, [(a, min(d, a + 150))]),
                          force_keyframes_at_cuts=False)
        try:
            with yt_dlp.YoutubeDL(opts) as y:
                info = y.extract_info(f"https://www.youtube.com/watch?v={e['id']}", download=False)
                lic = (info.get("license") or "").lower()
                if cc_only and "creative commons" not in lic:
                    continue
                info = y.extract_info(f"https://www.youtube.com/watch?v={e['id']}", download=True)
        except Exception:
            continue
        paths = [Path(d["filepath"]) for d in info.get("requested_downloads") or [] if d.get("filepath")]
        if not paths or not paths[0].exists():
            continue
        credit = f"Video: {info.get('uploader', '')} (YouTube" + (", CC BY)" if cc_only else ")")
        out.append(Cand("video", "youtube", path=paths[0], credit=credit,
                        meta=f"{info.get('title', '')} {' '.join(info.get('tags') or [])} {query}"))
    return out


def _commons(query: str, ftype: str, n: int) -> list[Cand]:
    try:
        r = requests.get("https://commons.wikimedia.org/w/api.php", headers=HEAD, timeout=20, params={
            "action": "query", "format": "json", "generator": "search", "gsrnamespace": 6,
            "gsrsearch": f"{query} filetype:{ftype}", "gsrlimit": 20, "prop": "imageinfo",
            "iiprop": "url|size|extmetadata", "iiurlwidth": 1920})
        pages = sorted(r.json().get("query", {}).get("pages", {}).values(), key=lambda p: p.get("index", 0))
    except Exception:
        return []
    out = []
    for p in pages:
        info = (p.get("imageinfo") or [{}])[0]
        if ftype == "bitmap" and info.get("width", 0) < 900:
            continue
        if ftype == "video" and not 0 < info.get("size", 0) < 150_000_000:
            continue
        meta = info.get("extmetadata", {})
        artist = re.sub(r"<[^>]+>", "", meta.get("Artist", {}).get("value", "Wikimedia Commons")).strip()[:60]
        lic = meta.get("LicenseShortName", {}).get("value", "")
        url = info.get("url") if ftype == "video" else (info.get("thumburl") or info.get("url"))
        desc = re.sub(r"<[^>]+>", " ", meta.get("ImageDescription", {}).get("value", ""))
        out.append(Cand("video" if ftype == "video" else "image", "commons", url=url,
                        meta=f"{p.get('title', '')} {desc} {query}",
                        credit=f"{'Video' if ftype == 'video' else 'Foto'}: {artist}, {lic} (Wikimedia Commons)"))
        if len(out) >= n:
            break
    return out


def _openverse(query: str, n: int) -> list[Cand]:
    try:
        r = requests.get("https://api.openverse.org/v1/images/", headers=HEAD, timeout=20, params={
            "q": query, "license_type": "commercial", "page_size": 20})
        results = r.json().get("results", [])
    except Exception:
        return []
    out = []
    for it in results:
        if (it.get("width") or 0) < 900:
            continue
        lic = f"{it.get('license', '').upper()} {it.get('license_version', '')}".strip()
        out.append(Cand("image", "openverse", url=it.get("url"),
                        meta=f"{it.get('title', '')} {' '.join(t.get('name', '') for t in it.get('tags') or [])} {query}",
                        credit=f"Foto: {it.get('creator') or 'unbekannt'}, {lic} (Openverse)"))
        if len(out) >= n:
            break
    return out


def _download(url: str | None, work: Path, video: bool = False) -> Path | None:
    if not url:
        return None
    ext = Path(url.split("?")[0]).suffix.lower()
    ext = ext if ext in (VIDEO_EXT if video else IMAGE_EXT) else (".mp4" if video else ".jpg")
    dst = work / (hashlib.md5(url.encode()).hexdigest()[:16] + ext)
    if dst.exists():
        return dst
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=60)
        r.raise_for_status()
        dst.write_bytes(r.content)
        return dst
    except Exception:
        return None
