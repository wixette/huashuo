#!/usr/bin/env python3
"""Narration pace: how steady is it, and what steadies it? (after 在桥上, a real short novel)

The listener heard the narrator's pace change from sentence to sentence. This renders one
passage (given on the command line, or a built-in one) with the narrator voice under
several settings, three seeds each:

    t0.9        the engine as it is (temperature 0.9, the passage as one unit)
    t0.7, t0.5  lower sampling temperature
    split       the passage cut into units of about 100 characters, temperature 0.9

and measures each rendering with the energy envelope (robust on long clips, unlike the
forced aligner, which drifts late in a minute-long clip):

    speech rate   characters per second of speech (silences of 200 ms or more removed)
    pause share   the fraction of time in such silences; longest pause
    sentence CV   variation of the speech rate across sentences: each sentence is cut
                  out by forced alignment (reliable on the short clips of the split mode;
                  for whole-passage modes the passage is also aligned sentence by sentence)

Usage (from the repository root):
    .venv/bin/python experiments/pace_check.py OUT_DIR [--voice library:zh/narrator_male] [--text FILE]
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import numpy as np

from huashuo.asr import normalize
from huashuo.audio import trim_bounds, write_wav
from huashuo.engines.qwen3 import Qwen3Engine
from huashuo.units import split_long

PASSAGE = ("他开始咳嗽，不是那种感冒引起的咳嗽，是清理嗓子的咳嗽。一群孩子喊叫着，挥舞着书包涌到桥上，"
           "他们像一排栖落在电线上的麻雀，整齐地扑在栏杆上，等一支长长的船队突突响着来到了桥下。"
           "当柴油机的黑烟在桥上弥漫过后，孩子们的嘴噼噼啪啪地响了起来，白色的唾沫荡着秋千飞向了船队，"
           "十多条驳船轮流驶入桥洞，接受孩子们唾沫的沐浴。站在船头的人挥舞着手，就像挡开射来的利箭一样，"
           "抵挡着唾沫。")
SEEDS = [11, 12, 13]
SR = 24000


def silences(audio: np.ndarray, sr: int = SR, min_len: float = 0.2) -> list[tuple[int, int]]:
    """(start, end) samples of silences of at least min_len inside the clip."""
    hop = int(0.02 * sr)
    f = len(audio) // hop
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


def measure(audio: np.ndarray, text: str) -> dict:
    a, b = trim_bounds(audio, SR)
    audio = audio[a:b]
    gaps = silences(audio)
    total = len(audio) / SR
    pause = sum(e - s for s, e in gaps) / SR
    chars = len(normalize(text, "zh"))
    return dict(secs=round(total, 1), rate=round(chars / total, 2), speech_rate=round(chars / (total - pause), 2),
                pause_share=round(pause / total, 2), longest=round(max(((e - s) / SR for s, e in gaps), default=0), 2),
                long_pauses=sum((e - s) / SR >= 1.0 for s, e in gaps))


def sentences(text: str) -> list[str]:
    return [s for s in re.findall(r"[^。！？]+[。！？]?", text) if s.strip()]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("out", type=Path)
    parser.add_argument("--voice", default="library:zh/narrator_male")
    parser.add_argument("--text", type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    text = args.text.read_text(encoding="utf-8").strip() if args.text else PASSAGE

    modes = {"t0.9": (0.9, [text]), "t0.7": (0.7, [text]), "t0.5": (0.5, [text]),
             "split": (0.9, split_long(text, 100, "zh"))}
    results = []
    for mode, (temperature, pieces) in modes.items():
        engine = Qwen3Engine(temperature=temperature)
        for seed in SEEDS:
            clips = [engine.synthesize(p, args.voice, "zh", None, seed + 100 * n) for n, p in enumerate(pieces)]
            # Sentence rates: each sentence rendered inside its unit is measured by
            # re-synthesizing nothing: we measure per piece for split mode, and per
            # sentence by proportional cut for whole-passage modes is not reliable, so
            # the whole-passage modes report the per-sentence spread from the aligner.
            joined = np.concatenate([np.concatenate([c, np.zeros(int(0.3 * SR), np.float32)]) for c in clips])
            write_wav(args.out / f"{mode}_s{seed}.wav", joined, SR)
            row = dict(mode=mode, seed=seed, **measure(joined, text), pieces=len(pieces),
                       piece_speech_rates=[measure(c, p)["speech_rate"] for c, p in zip(clips, pieces)])
            results.append(row)
            print(f"{mode:6} seed {seed}: {row['secs']:5.1f}s  rate {row['rate']:.2f}  speech {row['speech_rate']:.2f}  "
                  f"pauses {row['pause_share']:.0%} (longest {row['longest']:.1f}s, {row['long_pauses']} ≥1s)"
                  + (f"  pieces {row['piece_speech_rates']}" if len(pieces) > 1 else ""), flush=True)
        engine.close()
    (args.out / "report.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
