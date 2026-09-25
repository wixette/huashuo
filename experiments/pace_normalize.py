#!/usr/bin/env python3
"""Prototype: steady the narration pace after synthesis, without re-synthesizing.

For cached narration units:
  1. each unit is cut at its silences (>= SPLIT_PAUSE) into phrases; nothing is cut
     mid-sound, and no alignment to the text is needed (forced alignment drifts on
     minute-long units);
  2. each phrase is transcribed (Qwen3-ASR) only to count its characters, so its speech
     rate is known; homophones do not matter for the count;
  3. the target is the median phrase rate of the voice over all given units;
  4. phrases more than TOLERANCE off the target are time-stretched toward it (ffmpeg
     atempo, pitch kept), limited to MIN_FACTOR..MAX_FACTOR; phrases under MIN_CHARS
     characters are left alone;
  5. silences between phrases longer than MAX_PAUSE are shortened to it.

Writes <u>_A_original.wav, <u>_B_pauses.wav, <u>_C_pauses_tempo.wav per unit and prints
the phrase rates before and after.

Usage (from the repository root):
    .venv/bin/python experiments/pace_normalize.py WORKDIR OUT_DIR UNIT_INDEX [UNIT_INDEX ...]
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from pace_check import silences  # noqa: E402

from huashuo.asr import AsrChecker, normalize  # noqa: E402
from huashuo.audio import read_wav, trim_bounds, write_wav  # noqa: E402
from huashuo.engines.qwen3 import Qwen3Engine  # noqa: E402
from huashuo.pipeline import load_project, make_plan  # noqa: E402
from huashuo.synth import unit_keys  # noqa: E402
from huashuo.workdir import Workdir  # noqa: E402

SPLIT_PAUSE = 0.3
MAX_PAUSE = 0.7
TOLERANCE = 0.12
MIN_FACTOR, MAX_FACTOR = 0.85, 1.2
MIN_CHARS = 5
SR = 24000


def atempo(audio: np.ndarray, factor: float) -> np.ndarray:
    if abs(factor - 1) < 0.01:
        return audio
    raw = subprocess.run(["ffmpeg", "-v", "quiet", "-f", "f32le", "-ar", str(SR), "-ac", "1", "-i", "pipe:0",
                          "-filter:a", f"atempo={factor:.4f}", "-f", "f32le", "-ar", str(SR), "-ac", "1", "pipe:1"],
                         input=audio.astype(np.float32).tobytes(), capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32)


def phrases(audio: np.ndarray) -> tuple[list[np.ndarray], list[int]]:
    """Speech phrases and the silence (samples) after each."""
    gaps = silences(audio, min_len=SPLIT_PAUSE)
    parts, pauses, pos = [], [], 0
    for s, e in gaps:
        parts.append(audio[pos:s])
        pauses.append(e - s)
        pos = e
    parts.append(audio[pos:])
    pauses.append(0)
    return parts, pauses


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("workdir", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("units", type=int, nargs="+")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    wd = Workdir(args.workdir)
    plan = make_plan(load_project(wd))
    keys = unit_keys(plan.units, Qwen3Engine(), "zh")
    asr = AsrChecker()
    units = {}
    for index in args.units:
        audio, _ = read_wav(wd.units / f"{keys[index]}.wav")
        a, b = trim_bounds(audio, SR)
        audio = audio[a:b]
        parts, pauses = phrases(audio)
        counts = [len(normalize(asr.transcribe(p, SR, "zh"), "zh")) if len(p) > 0.3 * SR else 0 for p in parts]
        rates = [c / (len(p) / SR) if c else 0.0 for p, c in zip(parts, counts)]
        units[index] = (audio, parts, pauses, counts, rates)
    asr.close()
    target = float(np.median([r for *_, counts, rates in units.values() for c, r in zip(counts, rates)
                              if c >= MIN_CHARS]))
    print(f"target speech rate (median phrase): {target:.2f} chars/s")
    for index, (audio, parts, pauses, counts, rates) in units.items():
        factors = []
        for c, r in zip(counts, rates):
            off = r / target if c >= MIN_CHARS else 1.0
            factors.append(1.0 if abs(off - 1) <= TOLERANCE else min(MAX_FACTOR, max(MIN_FACTOR, target / r)))
        cap = int(MAX_PAUSE * SR)

        def join(chunks):
            out = []
            for chunk, pause in zip(chunks, pauses):
                out += [chunk, np.zeros(min(pause, cap), np.float32)]
            return np.concatenate(out)

        stretched = [atempo(p, f) for p, f in zip(parts, factors)]
        write_wav(args.out / f"u{index}_A_original.wav", audio, SR)
        write_wav(args.out / f"u{index}_B_pauses.wav", join(parts), SR)
        write_wav(args.out / f"u{index}_C_pauses_tempo.wav", join(stretched), SR)
        after = [c / (len(s) / SR) if c else 0.0 for s, c in zip(stretched, counts)]
        used = [i for i, c in enumerate(counts) if c >= MIN_CHARS]
        spread = lambda xs: float(np.std(xs) / np.mean(xs))
        print(f"u{index}: {len(parts)} phrases, {len(audio)/SR:.1f}s -> {len(join(parts))/SR:.1f}s (pauses capped) "
              f"-> {len(join(stretched))/SR:.1f}s (tempo); phrase-rate spread "
              f"{spread([rates[i] for i in used]):.0%} -> {spread([after[i] for i in used]):.0%}")
        print("   before:", " ".join(f"{rates[i]:.1f}" for i in used))
        print("   factor:", " ".join(f"{factors[i]:.2f}" for i in used))


if __name__ == "__main__":
    main()
