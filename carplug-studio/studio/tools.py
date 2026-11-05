"""Small media helpers shared by the downloader, the cleaner and the renderer."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from .config import ROOT

TOOLS = ROOT / ".tools"


def ffmpeg() -> str:
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def ffmpeg_dir() -> str:
    """Folder with an executable literally called ffmpeg(.exe) – yt-dlp needs that name."""
    name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    if shutil.which(name):
        return str(Path(shutil.which(name)).parent)
    TOOLS.mkdir(exist_ok=True)
    dst = TOOLS / name
    if not dst.exists():
        shutil.copy2(ffmpeg(), dst)
        dst.chmod(0o755)
    return str(TOOLS)


def probe(path: Path) -> tuple[int, int, float]:
    """(width, height, duration seconds) of a video or image file."""
    err = subprocess.run([ffmpeg(), "-hide_banner", "-i", str(path)], capture_output=True, text=True,
                         encoding="utf-8", errors="replace").stderr
    w = h = 0
    m = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", err)
    if m:
        w, h = int(m.group(1)), int(m.group(2))
        rot = re.search(r"rotate\s*:\s*-?(90|270)|displaymatrix: rotation of -?(90|270)", err)
        if rot:
            w, h = h, w
    d = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", err)
    dur = int(d.group(1)) * 3600 + int(d.group(2)) * 60 + float(d.group(3)) if d else 0.0
    return w, h, dur
