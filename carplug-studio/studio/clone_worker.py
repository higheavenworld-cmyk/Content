"""Runs inside the separate voice-clone environment (.venv-klon) and speaks all scenes in one go.

Usage: python clone_worker.py job.json
job.json = {"reference": "stimmen/x.wav", "items": [{"text": "...", "out": "voice_0_raw.wav"}, ...]}
"""
import json
import sys

import torch
import torchaudio as ta
from chatterbox.mtl_tts import ChatterboxMultilingualTTS


def main(job_file: str) -> None:
    job = json.load(open(job_file, encoding="utf-8"))
    if torch.cuda.is_available():
        device = "cuda"
    elif torch.backends.mps.is_available():
        device = "mps"
    else:
        device = "cpu"
    model = ChatterboxMultilingualTTS.from_pretrained(device=device)
    for i, item in enumerate(job["items"], 1):
        print(f"Klon-Stimme spricht Szene {i}/{len(job['items'])}…", flush=True)
        wav = model.generate(item["text"], language_id="de", audio_prompt_path=job["reference"])
        ta.save(item["out"], wav.cpu(), model.sr)


if __name__ == "__main__":
    main(sys.argv[1])
