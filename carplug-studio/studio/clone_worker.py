"""Runs inside the separate voice-clone environment (.venv-klon) and speaks all scenes in one go.

Usage: python clone_worker.py job.json
job.json = {"reference": "stimmen/x.wav", "items": [{"text": "...", "out": "voice_0_raw.wav"}, ...]}
"""
import json
import re
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
        parts = []
        # sentence by sentence: more stable than one long text, natural short breath in between
        for sent in [x for x in re.split(r"(?<=[.!?])\s+", item["text"]) if x.strip()]:
            parts.append(speak(model, sent, job["reference"]))
            parts.append(torch.zeros(1, int(model.sr * 0.12)))
        wav = torch.cat(parts[:-1], dim=1) if parts else torch.zeros(1, model.sr // 2)
        ta.save(item["out"], wav.cpu(), model.sr)


def speak(model, text: str, reference: str):
    """A little more energy than neutral (news style), slightly faster pacing."""
    try:
        wav = model.generate(text, language_id="de", audio_prompt_path=reference, exaggeration=0.6,
                             cfg_weight=0.4, temperature=0.75)
    except TypeError:  # older chatterbox version without these options
        wav = model.generate(text, language_id="de", audio_prompt_path=reference)
    return wav.cpu() if wav.dim() == 2 else wav.cpu()[None, :]


if __name__ == "__main__":
    main(sys.argv[1])
