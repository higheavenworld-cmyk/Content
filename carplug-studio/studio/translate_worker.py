"""Offline translation, runs inside the voice-clone environment (it already has torch + transformers).

Usage: python translate_worker.py job.json result.json
Models (OPUS-MT by Helsinki-NLP, ~300 MB each) are downloaded once from Hugging Face, then work offline.
"""
import json
import sys

from transformers import MarianMTModel, MarianTokenizer

_CACHE = {}


def model(name):
    if name not in _CACHE:
        _CACHE[name] = (MarianTokenizer.from_pretrained(name), MarianMTModel.from_pretrained(name))
    return _CACHE[name]


def run(lines, name):
    tok, mdl = model(name)
    out = []
    for i in range(0, len(lines), 16):
        batch = lines[i:i + 16]
        enc = tok(batch, return_tensors="pt", padding=True, truncation=True, max_length=512)
        gen = mdl.generate(**enc, max_new_tokens=512, num_beams=4)
        out += tok.batch_decode(gen, skip_special_tokens=True)
    return out


def main(job_file, result_file):
    job = json.load(open(job_file, encoding="utf-8"))
    lines, target = job["lines"], job["target"]
    if target == "de":
        if job["src"] != "en":
            lines = run(lines, "Helsinki-NLP/opus-mt-mul-en")  # any language -> English first
        out = run(lines, "Helsinki-NLP/opus-mt-en-de")
    else:
        out = run(lines, "Helsinki-NLP/opus-mt-de-en" if job["src"] in ("de", "en") else "Helsinki-NLP/opus-mt-mul-en")
    json.dump(out, open(result_file, "w", encoding="utf-8"), ensure_ascii=False)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
