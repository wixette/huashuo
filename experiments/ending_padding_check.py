#!/usr/bin/env python3
"""Does padding the end of a unit stop the TTS from cutting its last syllable? (2026-09-27)

In 广告 the listener found four endings cut in half or too weak (惑, 包, 目, 量) and two
borderline (说, 里); the ASR check passed them all, since the syllable is still there. This
synthesizes each of those units again, in one take, three seeds per variant:

    A  the text as is (what a plain retry with a new seed gives)
    B  the text + 「……」
    C  the text + 「。」

and writes one M4B to listen to: a chapter per unit and variant, the three seeds in a row.
For each take it prints whether the ASR heard the last character, the clip's length, and
how loud its last 40 ms is against the speech level (above -25 dB: stopped mid-sound).

Usage: .venv/bin/python experiments/ending_padding_check.py WORKDIR OUT.m4b
    WORKDIR  a work directory synthesized with the default options (examples/ad, for instance)
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from huashuo.asr import AsrChecker, lost_ending
from huashuo.audio import gain_db, loudness, to_pcm16, trim_bounds
from huashuo.engines.qwen3 import Qwen3Engine
from huashuo.m4b import BookInfo, write_m4b
from huashuo.pipeline import load_project, make_plan
from huashuo.workdir import Workdir

ENDINGS = {"满脸疑惑。": "惑 (bad)", "旅行背包。": "包 (bad)", "的精品项目。”": "目 (bad)",
           "充满力量！”": "量 (bad)", "坚定地说：": "说 (borderline)", "攥在手里。": "里 (borderline)"}
VARIANTS = {"A": lambda t: t, "B": lambda t: t + "……", "C": lambda t: t + "。"}
SEEDS = (7001, 7002, 7003)
GAP = 1.2


def end_db(audio: np.ndarray, sr: int) -> float:
    hop = int(0.02 * sr)
    n = len(audio) // hop
    db = 20 * np.log10(np.sqrt((audio[:n * hop].reshape(n, hop) ** 2).mean(1)) + 1e-9)
    return float(db[-2:].mean() - np.percentile(db[db > db.max() - 40], 80))


def main() -> None:
    wd, out = Workdir(Path(sys.argv[1])), Path(sys.argv[2])
    project = load_project(wd)
    units = [u for u in make_plan(project).units if any(u.text.endswith(e) for e in ENDINGS)]
    engine, asr = Qwen3Engine(), AsrChecker()
    pcm, chapters, cursor, sr = [], [], 0, 24000
    for unit in units:
        label = next(v for e, v in ENDINGS.items() if unit.text.endswith(e))
        for name, pad in VARIANTS.items():
            start = cursor
            for seed in SEEDS:
                audio = engine.synthesize(pad(unit.text), unit.voice, "zh", unit.instruct, seed)
                sr = engine.sample_rate
                heard = asr.transcribe(audio, sr, "zh")
                print(f"{label:16} {name} seed {seed}: {len(audio) / sr:5.1f} s, end {end_db(audio, sr):5.0f} dB, "
                      f"last character {'lost' if lost_ending(unit.page_text, heard, 'zh') else 'heard'}; "
                      f"…{heard[-8:]}", flush=True)
                a, b = trim_bounds(audio, sr)
                clip = audio[a:b] * 10 ** (gain_db(loudness(audio[a:b], sr), None) / 20)
                pcm += [to_pcm16(clip), bytes(2 * int(GAP * sr))]
                cursor += len(clip) + int(GAP * sr)
            chapters.append((f"{label} {name}", start, cursor))
    engine.close()
    write_m4b(out, iter(pcm), sr, BookInfo("ending padding check", "", "zh"), chapters, None)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
