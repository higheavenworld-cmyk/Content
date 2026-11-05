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
from .config import BRAND_HASHTAGS, load_settings

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
LENGTHS = {"Kurz (~20 s)": 3, "Mittel (~40 s)": 5, "Lang (~60 s)": 7}
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


def write_script(title: str, text: str, url: str = "", length: str = "Mittel (~40 s)") -> tuple[Script, str]:
    """Returns (script, info message about which writer was used)."""
    n = LENGTHS.get(length, 5)
    s = load_settings()
    try:
        script = _ollama(title, text, n, s)
        info = f"Script mit lokaler KI ({s['ollama_model']}) erstellt."
    except Exception:  # Ollama not installed / not running / bad answer
        script = _rules(title, text, n)
        info = "Script mit eingebautem Regel-Writer erstellt (für bessere Texte: Ollama installieren, siehe Anleitung)."
    if url:
        script.caption += f"\n\nQuelle: {url}"
    return script, info


# --- local AI ---------------------------------------------------------------
PROMPT = """Du bist Social-Media-Redakteur von CarPlug (Auto-News, TikTok/Reels/Shorts, Deutsch).
Schreibe aus der QUELLE ein virales Kurzvideo-Script mit {n} Faktenszenen.
Regeln:
- Szene 1 ist der HOOK: max. 12 Wörter, Neugier oder Überraschung, wichtigste Zahl nennen.
- Danach {n} Szenen, je 1-2 kurze Sätze (max. 25 Wörter), gesprochen, locker, nur Fakten aus der Quelle.
- Letzte Szene: "{cta}"
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


def _ollama(title: str, text: str, n: int, s: dict) -> Script:
    tips = learn.insights_for_prompt()
    prompt = PROMPT.format(n=n, cta=CTA, title=title, text=text[:6000],
                           insights=("Was bei uns bisher gut lief:\n" + tips) if tips else "")
    r = requests.post(f"{s['ollama_url'].rstrip('/')}/api/generate",
                      json={"model": s["ollama_model"], "prompt": prompt, "stream": False, "format": "json",
                            "options": {"temperature": 0.7}}, timeout=300)
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
def _rules(title: str, text: str, n: int) -> Script:
    title = re.sub(r"\s*[|–—-]\s*[^|–—-]{2,30}$", "", title).strip() or "Neue Auto-News"
    sents = [re.sub(r"\s+", " ", s).strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text)]
    sents = [s for s in sents if 40 <= len(s) <= 280 and not BOILER.search(s) and s.rstrip(".!?") != title]
    scored = []
    for i, s in enumerate(sents):
        score = 3 * len(NUM_UNIT.findall(s)) + len(re.findall(r"\d", s)) * 0.2 + (2 if i < 5 else 0)
        score += 1.5 * sum(b.lower() in s.lower() for b in BRANDS[:40])
        scored.append((score, i, s))
    picked = sorted(sorted(scored, reverse=True)[:n], key=lambda x: x[1])
    query = _model_query(title + " " + text[:500])

    scenes = [Scene(_hook(title, text), (_overlay(title + " " + text[:400]) or ""), query)]
    for _, _, s in picked:
        scenes.append(Scene(_shorten(s), _overlay(s), query))
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
    for b in sorted(BRANDS, key=len, reverse=True):
        m = re.search(rf"\b{re.escape(b)}\b\s*([A-Z0-9][\w-]*(?:\s[A-Z0-9][\w-]*)?)?", s)
        if m:
            return (b + " " + (m.group(1) or "")).strip()
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
