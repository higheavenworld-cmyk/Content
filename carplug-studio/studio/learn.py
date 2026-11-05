"""Simple learning loop: remember which videos performed well and feed that back into new scripts.

Every rendered video is logged. You enter its numbers (views, likes, shares, watch time) later;
the best hooks and the best length are then given to the AI writer as examples.
"""
from __future__ import annotations

import json
import time

from .config import ROOT

LOG = ROOT / "lernen.json"
NOTES = ROOT / "lernen-notizen.txt"   # free text tips you want every script to follow


def _load() -> list[dict]:
    try:
        return json.loads(LOG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _save(rows: list[dict]) -> None:
    LOG.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")


def log_video(video_id: str, hook: str, seconds: float, voice: str) -> None:
    rows = [r for r in _load() if r["id"] != video_id]
    rows.append({"id": video_id, "datum": time.strftime("%Y-%m-%d"), "hook": hook,
                 "sekunden": round(seconds, 1), "stimme": voice,
                 "views": 0, "likes": 0, "shares": 0, "watch_prozent": 0})
    _save(rows)


def table() -> list[list]:
    return [[r["id"], r["datum"], r["hook"], r["sekunden"], r["views"], r["likes"], r["shares"],
             r["watch_prozent"]] for r in _load()]


def update_from_table(rows: list[list]) -> None:
    by_id = {r["id"]: r for r in _load()}
    for row in rows:
        if not row or str(row[0]) not in by_id:
            continue
        r = by_id[str(row[0])]
        for key, val in zip(("views", "likes", "shares", "watch_prozent"), row[4:8]):
            try:
                r[key] = float(val or 0)
            except (TypeError, ValueError):
                pass
    _save(list(by_id.values()))


def score(r: dict) -> float:
    views = max(r["views"], 1)
    # engagement & retention matter more than raw views for the algorithm
    return views * (1 + 8 * r["likes"] / views + 20 * r["shares"] / views) * (0.5 + r["watch_prozent"] / 100)


def insights_for_prompt() -> str:
    rated = [r for r in _load() if r["views"] > 0]
    lines = []
    if len(rated) >= 3:
        best = sorted(rated, key=score, reverse=True)
        lines.append("Beste Hooks: " + " | ".join(f'"{r["hook"]}"' for r in best[:3]))
        lines.append("Schwächste Hooks (vermeiden): " + " | ".join(f'"{r["hook"]}"' for r in best[-2:]))
        top = best[: max(3, len(best) // 3)]
        lines.append(f"Beste Videolänge bisher: ca. {sum(r['sekunden'] for r in top) / len(top):.0f} Sekunden.")
    try:
        notes = NOTES.read_text(encoding="utf-8").strip()
        if notes:
            lines.append(notes)
    except OSError:
        pass
    return "\n".join(lines)
