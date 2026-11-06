"""Turn source text into a short-video script (hook, scenes, overlays, caption).

Uses a local AI (Ollama, free, no account) when it is running; otherwise a built-in
rule-based writer that picks the strongest facts (numbers, specs, prices).
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field

import requests

from . import learn
from .config import BRAND_HASHTAGS, MIN_SAFETY_SECONDS, WORDS_PER_SECOND, load_settings

BRANDS = ["BMW", "Mercedes-Benz", "Mercedes", "Audi", "Volkswagen", "VW", "Porsche", "Tesla", "Ford",
          "Opel", "Skoda", "Seat", "Cupra", "Hyundai", "Kia", "Toyota", "Lexus", "Honda", "Mazda", "Nissan",
          "Renault", "Peugeot", "Citroen", "Fiat", "Ferrari", "Lamborghini", "Maserati", "Alfa Romeo",
          "Volvo", "Polestar", "BYD", "Nio", "Xpeng", "Rolls-Royce", "Bentley", "Aston Martin", "McLaren",
          "Bugatti", "Rimac", "Lucid", "Rivian", "Dacia", "Mini", "Smart", "Jaguar", "Land Rover",
          "Range Rover", "Jeep", "Dodge", "Chevrolet", "Cadillac", "Lotus", "Genesis", "Xiaomi", "Zeekr"]
UNITS = r"(?:PS|kW|km/h|Nm|Euro|€|kWh|Kilometer|km|Sekunden|Zylinder|Liter|Prozent|%|Tonnen|Kilo|kg|Zoll)"
NUM_UNIT = re.compile(r"(?:ab\s|über\s|bis zu\s)?\d[\d.,]*\s?" + UNITS, re.I)
BOILER = re.compile(r"cookie|newsletter|©|copyright|foto:|bild:|quelle:|anzeige|abonn|datenschutz|"
                    r"javascript|werbung|klicken sie|teilen", re.I)
CTA = "Folg CarPlug für mehr Auto-News!"


@dataclass
class Scene:
    text: str            # what the voice says
    overlay: str = ""    # big keyword on screen (e.g. "612 PS")
    query: str = ""      # footage search term (English works best)


@dataclass
class Script:
    headline: list[str] = field(default_factory=lambda: ["", "", ""])  # white / RED / white
    subline: str = ""
    scenes: list[Scene] = field(default_factory=list)  # scene 0 = hook, last = call to action
    caption: str = ""
    hashtags: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)

    @staticmethod
    def from_json(s: str) -> "Script":
        d = json.loads(s)
        d["scenes"] = [Scene(**x) for x in d.get("scenes", [])]
        return Script(**d)


def words_per_second() -> float:
    rate = str(load_settings().get("speech_rate", "+0%")).rstrip("%")
    try:
        return WORDS_PER_SECOND * (1 + int(rate) / 100)
    except ValueError:
        return WORDS_PER_SECOND


def min_seconds(target: float) -> float | None:
    """From 1 minute on the video must be slightly LONGER than the target (TikTok Creator Rewards)."""
    return target + MIN_SAFETY_SECONDS if target >= 60 else None


def word_budget(target: float) -> int:
    # aim just above the minimum; missing seconds are filled by pauses / slower speech, never by cutting
    goal = target + MIN_SAFETY_SECONDS + 1.5 if target >= 60 else target * 1.03
    n_scenes = max(3, goal / 9)
    speak = goal - 1.0 - 0.27 * n_scenes
    return max(12, int(speak * words_per_second()))


def estimate_seconds(texts: list[str]) -> float:
    words = sum(len(t.split()) for t in texts)
    return words / words_per_second() + 0.27 * max(len(texts) - 1, 0) + 1.0


def write_script(title: str, text: str, url: str = "", target: float = 40) -> tuple[Script, str]:
    """Returns (script, info message about which writer was used)."""
    budget = word_budget(target)
    s = load_settings()
    try:
        script = _ollama(title, text, budget, s)
        if sum(len(sc.text.split()) for sc in script.scenes) < budget * 0.8:
            raise ValueError("KI-Script zu kurz")
        info = f"Script mit lokaler KI ({s['ollama_model']}) erstellt."
    except Exception:  # Ollama not installed / not running / too short answer
        script = _rules(title, text, budget)
        info = "Script mit eingebautem Regel-Writer erstellt (für bessere Texte: Ollama installieren, siehe Anleitung)."
    if url:
        script.caption += f"\n\nQuelle: {url}"
    return script, info


# --- local AI ---------------------------------------------------------------
PROMPT = """Du bist Social-Media-Redakteur von CarPlug (Auto-News, TikTok/Reels/Shorts, Deutsch).
Schreibe aus der QUELLE ein virales Video-Script mit insgesamt ca. {words} gesprochenen Wörtern
(das ist wichtig für die Videolänge – nicht kürzer!), aufgeteilt auf ca. {n} Faktenszenen.
WICHTIG: Der Sprechertext besteht NUR aus Inhalten der QUELLE. Füge KEINE eigenen Sätze, Überleitungen,
Ankündigungen oder Floskeln hinzu (kein "Und warte…", kein "Aber jetzt wird's krass", kein "Und jetzt der Preis",
keine Begrüßung, kein "In diesem Video"). Nichts erfinden, nichts kommentieren.
Regeln:
- Szene 1 ist der HOOK: der stärkste Fakt aus der Quelle (max. 15 Wörter).
- Danach ca. {n} Szenen, je 1-3 kurze Sätze (max. 15 Wörter pro Satz), sachlich nach der Quelle formuliert.
- Bei langen Videos: Hintergründe, Vergleiche und Einordnung – aber nur, was in der Quelle steht.
- Letzte Szene genau: "{cta}"
- overlay: 1-3 Wörter in GROSSBUCHSTABEN, z.B. "612 PS" oder "PANZERGLAS".
- query: englischer Suchbegriff für passende Fotos, möglichst Marke + Modell (z.B. "BMW 7 Series").
- headline: genau 3 kurze Zeilen fürs Titelbild in GROSSBUCHSTABEN, die mittlere ist das Reizwort.
- subline: 3 Stichpunkte getrennt mit " · " in GROSSBUCHSTABEN.
- caption: 1-2 Sätze für den Post mit Frage an die Community. hashtags: 5-8 passende.
{insights}
Antworte NUR mit JSON:
{{"headline":["","",""],"subline":"","scenes":[{{"text":"","overlay":"","query":""}}],"caption":"","hashtags":[]}}

TITEL: {title}
QUELLE:
{text}"""


def _ollama(title: str, text: str, budget: int, s: dict) -> Script:
    tips = learn.insights_for_prompt()
    n = max(2, round(budget / 24))
    prompt = PROMPT.format(n=n, words=budget, cta=CTA, title=title, text=text[:max(6000, min(budget * 12, 24000))],
                           insights=("Was bei uns bisher gut lief:\n" + tips) if tips else "")
    r = requests.post(f"{s['ollama_url'].rstrip('/')}/api/generate",
                      json={"model": s["ollama_model"], "prompt": prompt, "stream": False, "format": "json",
                            "options": {"temperature": 0.7, "num_ctx": 16384, "num_predict": -1}},
                      timeout=1800)
    r.raise_for_status()
    d = json.loads(r.json()["response"])
    scenes = [Scene(str(x.get("text", "")).strip(), str(x.get("overlay", "")).upper().strip(),
                    str(x.get("query", "")).strip()) for x in d["scenes"] if x.get("text")]
    if len(scenes) < 2:
        raise ValueError("too few scenes")
    if CTA.lower()[:10] not in scenes[-1].text.lower():
        scenes.append(Scene(CTA, "FOLG CARPLUG", scenes[-1].query))
    head = [str(x).upper() for x in d.get("headline", [])][:3]
    head += [""] * (3 - len(head))
    tags = [t if t.startswith("#") else "#" + t for t in map(str, d.get("hashtags", []))]
    return Script(head, str(d.get("subline", "")).upper(), scenes, str(d.get("caption", "")),
                  _merge_tags(tags))


# --- rule-based fallback ------------------------------------------------------
PRICE = re.compile(r"\d[\d.,]*\s?(?:Euro|€)|Preis|kostet", re.I)


def _wow(s: str) -> float:
    """How strong a fact is for the payoff: prices and big numbers win."""
    nums = [float(x.replace(".", "").replace(",", ".")) for x in re.findall(r"\d[\d.]*(?:,\d+)?", s)
            if x.replace(".", "").replace(",", "").isdigit()]
    return (5 if PRICE.search(s) else 0) + 2 * len(NUM_UNIT.findall(s)) + (min(max(nums), 1e6) / 2e5 if nums else 0)


def _punchy(s: str) -> list[str]:
    """Long written sentences -> short spoken ones (split only where it stays grammatical)."""
    if len(s.split()) <= 20:
        return [s]
    parts = re.split(r",\s+(?=(?:und|aber|doch|denn)\s)|\s+[–—]\s+|;\s+", s, maxsplit=1)
    if len(parts) == 2 and min(len(p.split()) for p in parts) >= 5:
        a, b = parts[0].rstrip(",;") + ".", parts[1]
        b = re.sub(r"^(und|aber|doch|denn)\s+", lambda m: m.group(1).capitalize() + " ", b)
        return [a] + _punchy(b[0].upper() + b[1:])
    return [s]


def _rules(title: str, text: str, budget: int) -> Script:
    title = re.sub(r"\s*[|–—-]\s*[^|–—-]{2,30}$", "", title).strip() or "Neue Auto-News"
    sents = [re.sub(r"\s+", " ", s).strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text)]
    sents = [s for s in sents if 40 <= len(s) <= 400 and not BOILER.search(s) and s.rstrip(".!?") != title]
    scored = []
    for i, s in enumerate(sents):
        score = 3 * len(NUM_UNIT.findall(s)) + len(re.findall(r"\d", s)) * 0.2 + (2 if i < 5 else 0)
        score += 1.5 * sum(b.lower() in s.lower() for b in BRANDS[:40])
        scored.append((score, i, s))
    query = _model_query(title + " " + text[:500])
    remaining = budget - 12 - len(CTA.split())  # hook + closing sentence
    picked = []
    for score, i, s in sorted(scored, reverse=True):  # strongest facts first, until the length fits
        if any(len(p.split()) > 32 for p in _punchy(s)):
            continue  # never cut a sentence in the middle – too long sentences are left out
        if remaining - len(s.split()) / 2 < 0:  # would overshoot by more than half a sentence
            continue
        picked.append((i, s))
        remaining -= len(s.split())
    picked.sort()
    payoff = max(picked, key=lambda x: _wow(x[1])) if len(picked) >= 3 else None
    if payoff:  # strongest fact at the end = reason to keep watching
        picked.remove(payoff)
        picked.append(payoff)

    # only text from the source – no own phrases (the closing sentence is the only fixed line)
    scenes = [Scene(_hook(title, text), (_overlay(title + " " + text[:400]) or ""), query)]
    group: list[str] = []
    bodies: list[str] = []
    for _, s in picked[:-1] if payoff else picked:  # short neighbouring sentences share one scene
        for part in _punchy(s):
            if group and len(" ".join(group + [part]).split()) > 30:
                bodies.append(" ".join(group))
                group = []
            group.append(part)
    if group:
        bodies.append(" ".join(group))
    for b in bodies:
        scenes.append(Scene(b, _overlay(b), query))
    if payoff:  # strongest fact last – as written in the source, without announcement
        p_text = " ".join(_punchy(payoff[1]))
        scenes.append(Scene(p_text, _overlay(p_text), query))
    scenes.append(Scene(CTA, "FOLG CARPLUG", query))

    keys = [sc.overlay for sc in scenes[1:-1] if sc.overlay][:3]
    brand = query.split(" ")[0] if query else ""
    tags = _merge_tags([f"#{brand.lower().replace('-', '')}"] if brand else [])
    return Script(_split_headline(title), " · ".join(dict.fromkeys(keys)), scenes,
                  f"{title} – was sagt ihr dazu? 👇", tags)


def _hook(title: str, text: str) -> str:
    t = title.rstrip(".!? ")
    return t + ("!" if NUM_UNIT.search(t) else ".")


def _shorten(s: str, max_words: int = 28) -> str:
    words = s.split()
    if len(words) <= max_words:
        return s
    cut = " ".join(words[:max_words])
    if "," in cut[len(cut) // 2:]:
        cut = cut[: cut.rfind(",")]
    return cut.rstrip(",;: ") + "."


def _overlay(s: str) -> str:
    m = NUM_UNIT.search(s)
    if m:
        return m.group(0).upper()
    q = _model_query(s)
    return q.upper() if " " in q else ""  # brand alone is too weak as an overlay


def _model_query(s: str) -> str:
    """'BMW 7er' -> 'BMW 7 Series', 'Mercedes S-Klasse' -> 'Mercedes S-Class' (better picture search)."""
    for b in sorted(BRANDS, key=len, reverse=True):
        m = re.search(rf"\b{re.escape(b)}\b\s*([A-Z0-9][\w-]*(?:\s[A-Z0-9][\w-]*)?)?", s)
        if m:
            q = (b + " " + (m.group(1) or "")).strip()
            q = re.sub(r"\b(\d)er\b", r"\1 Series", q)
            return q.replace("-Klasse", "-Class").replace("Klasse", "Class")
    return ""


def _split_headline(title: str) -> list[str]:
    """Split the title into 3 lines with the most even widths (middle line is shown in red)."""
    words = title.upper().split()[:9]
    if len(words) <= 3:
        return (words + ["", "", ""])[:3]
    best, best_w = None, 1e9
    for i in range(1, len(words) - 1):
        for j in range(i + 1, len(words)):
            lines = [" ".join(words[:i]), " ".join(words[i:j]), " ".join(words[j:])]
            width = max(map(len, lines)) - 0.1 * len(lines[1])
            if width < best_w:
                best, best_w = lines, width
    return best


def _merge_tags(tags: list[str]) -> list[str]:
    out = []
    for t in tags + BRAND_HASHTAGS:
        t = re.sub(r"[^\w#]", "", t.lower())
        if len(t) > 1 and t not in out:
            out.append(t)
    return out[:10]
