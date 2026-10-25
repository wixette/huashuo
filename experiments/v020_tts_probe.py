"""v0.2.0 issues 1-3: synthesize text variants x seeds with the book's engine; measure ASR, pace,
pauses and pitch. The jobs that were run are in v020_data/probe_jobs.json, the results in
v020_data/probe_results.json.

usage: .venv/bin/python experiments/v020_tts_probe.py JOBS.json OUT_DIR
JOBS.json: [{"group", "cond", "text", "reference"?, "voice", "instruct"?, "seeds": [..]}]

Writes OUT_DIR/results.json and, per group, blind/<group>/NN.wav numbered in random order
with the key in blind/<group>/key.json.
"""
import json
import random
import re
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path("experiments").resolve()))
from exp3_tone_check import yin_f0  # noqa: E402

from huashuo.asr import AsrChecker, cer  # noqa: E402
from huashuo.audio import trim_bounds, write_wav  # noqa: E402
from huashuo.engines.qwen3 import Qwen3Engine  # noqa: E402

COUNTED = re.compile(r"[㐀-鿿A-Za-z0-9]")


def silences(audio, sr, min_len=0.25):
    hop = int(0.02 * sr)
    f = len(audio) // hop
    if f == 0:
        return []
    db = 20 * np.log10(np.sqrt((audio[:f * hop].reshape(f, hop) ** 2).mean(1)) + 1e-9)
    quiet = db < db.max() - 35
    out, start = [], None
    for i, q in enumerate(quiet):
        if q and start is None:
            start = i
        elif not q and start is not None:
            if (i - start) * 0.02 >= min_len:
                out.append((start * hop, i * hop))
            start = None
    return out


def pitch(audio, sr):
    f0 = yin_f0(audio, sr)
    v = f0[~np.isnan(f0)]
    if len(v) < 5:
        return {}
    st = 12 * np.log2(v / np.median(v))
    tail = st[int(len(st) * 0.7):]
    head = st[:max(1, int(len(st) * 0.3))]
    return {"f0_med": round(float(np.median(v)), 1), "range_st": round(float(np.percentile(st, 90) - np.percentile(st, 10)), 2),
            "end_minus_start_st": round(float(np.mean(tail) - np.mean(head)), 2),
            "last_st": round(float(np.mean(st[-5:])), 2)}


def main():
    jobs = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    out = Path(sys.argv[2])
    (out / "wav").mkdir(parents=True, exist_ok=True)
    engine, asr = Qwen3Engine(), AsrChecker()
    results = []
    done = {}
    if (out / "results.json").is_file():
        for r in json.loads((out / "results.json").read_text(encoding="utf-8")):
            done[r["file"]] = r
    for job in jobs:
        for seed in job["seeds"]:
            name = f"{job['group']}__{job['cond']}__{seed}.wav"
            if name in done:
                results.append(done[name])
                continue
            audio = engine.synthesize(job["text"], job["voice"], "zh", job.get("instruct"), seed)
            sr = engine.sample_rate
            a, b = trim_bounds(audio, sr)
            clip = audio[a:b]
            write_wav(out / "wav" / name, clip, sr)
            gaps = silences(clip, sr)
            secs = len(clip) / sr
            pause = sum(e - s for s, e in gaps) / sr
            ref = job.get("reference", job["text"])
            n = len(COUNTED.findall(ref))
            heard = asr.transcribe(clip, sr, "zh")
            r = {"file": name, "group": job["group"], "cond": job["cond"], "seed": seed, "text": job["text"],
                 "voice": job["voice"], "instruct": job.get("instruct"), "secs": round(secs, 2),
                 "rate": round(n / max(secs, 0.1), 2), "pause": round(pause, 2),
                 "longest_pause": round(max(((e - s) / sr for s, e in gaps), default=0), 2),
                 "asr": heard, "cer": round(cer(ref, heard, "zh"), 3), **pitch(clip, sr)}
            results.append(r)
            print(json.dumps(r, ensure_ascii=False), flush=True)
            (out / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")

    rng = random.Random(20261025)
    groups = sorted({r["group"] for r in results})
    for g in groups:
        items = [r for r in results if r["group"] == g]
        rng.shuffle(items)
        d = out / "blind" / g
        d.mkdir(parents=True, exist_ok=True)
        key = {}
        for i, r in enumerate(items, 1):
            (d / f"{i:02d}.wav").write_bytes((out / "wav" / r["file"]).read_bytes())
            key[f"{i:02d}"] = {"cond": r["cond"], "seed": r["seed"], "instruct": r["instruct"], "text": r["text"]}
        (d / "key.json").write_text(json.dumps(key, ensure_ascii=False, indent=1), encoding="utf-8")
    engine.close()


if __name__ == "__main__":
    main()
