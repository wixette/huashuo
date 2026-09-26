#!/usr/bin/env python3
"""How does the TTS read a chapter title that is only a number? (「1」 read yì, not yī)

Renders number titles 1-9 in several written forms with the narrator voices and measures
the pitch contour of the spoken syllable: the slope of F0 (semitones per second) over the
voiced part. 一 read alone should be yī (first tone, level); yì (fourth tone) falls
steeply, yí (second tone) rises. The ASR cannot hear tones, so pitch decides.

Usage (from the repository root):
    .venv/bin/python experiments/title_tone_check.py
"""

from __future__ import annotations

import numpy as np

from huashuo.engines.qwen3 import Qwen3Engine
from narrator_check import f0_track

NUMERALS = "零一二三四五六七八九"
VOICES = ["library:zh/narrator_male", "library:zh/narrator_female"]
SEEDS = [5, 6]


def forms(n: int) -> dict[str, str]:
    c = NUMERALS[n]
    return {"1": str(n), "1。": f"{n}。", "一": c, "一。": f"{c}。", "（一）": f"（{c}）"}


def slope(audio: np.ndarray, sr: int) -> tuple[float, float]:
    """F0 slope (semitones/s) over the voiced part, and the F0 range covered."""
    f0 = f0_track(audio, sr)
    if len(f0) < 6:
        return 0.0, 0.0
    st = 12 * np.log2(f0 / np.median(f0))
    t = np.arange(len(st)) * 0.01
    return float(np.polyfit(t, st, 1)[0]), float(st.max() - st.min())


def main() -> None:
    engine = Qwen3Engine()
    print(f"{'digit':>5} {'form':>6}  " + "  ".join(f"{v.split('/')[-1][:14]:>14} s{s}" for v in VOICES for s in SEEDS))
    for n in range(1, 10):
        for label, text in forms(n).items():
            cells = []
            for voice in VOICES:
                for seed in SEEDS:
                    audio = engine.synthesize(text, voice, "zh", None, seed)
                    k, _ = slope(audio, engine.sample_rate)
                    tone = "flat" if abs(k) < 8 else ("fall" if k < 0 else "rise")
                    cells.append(f"{k:+7.1f} {tone:>4}")
            print(f"{n:>5} {label:>6}  " + "  ".join(f"{c:>17}" for c in cells), flush=True)
    engine.close()


if __name__ == "__main__":
    main()
