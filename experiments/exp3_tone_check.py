#!/usr/bin/env python3
"""EXP-3: detect accent drift from Mandarin tones (design doc §5.8).

Speaker vectors capture who is speaking, not how they pronounce, and ASR transcribes
accented Mandarin almost as well as standard Mandarin, so neither catches a unit that
drifts into a dialect accent. Tones might: Chinese dialects keep the Mandarin tone
categories but realize them with different pitch shapes (Guanzhong/Shaanxi: tone 1 is
low falling 21 instead of high level 55; tone 4 is high level 44 instead of falling 51).

Per unit:
  1. Qwen3-ForcedAligner gives each character's time span.
  2. pypinyin (with tone sandhi) gives the tone standard Mandarin expects.
  3. A YIN pitch track gives the contour actually spoken, in semitones relative to the
     unit's median pitch, sampled at five points per syllable.
  4. The unit's tone profile is the mean contour of each tone (4 x 5 values).

A unit's score is the distance between its profile and the reference profile: the
median over a set of known-standard units of the same voice (or, in the pipeline, over
the whole book, since drift is rare). Drifted units should score high.

Usage (from the repository root):
    .venv/bin/python experiments/exp3_tone_check.py OUT_DIR SAMPLES.json
SAMPLES.json: [{"name", "wav", "text", "label": "standard" | "accent", "group"}, ...]
Reference is built from the samples labelled "standard" in the "reference" group.
"""

from __future__ import annotations

import json
import re
import sys
import wave
from pathlib import Path

import numpy as np

SR = 24000
HOP = 0.01                 # pitch frame step, seconds
F0_MIN, F0_MAX = 70.0, 450.0
MIN_SYLLABLE = 0.08        # shorter syllables carry too few pitch frames
MIN_VOICED = 0.5           # share of voiced frames a syllable needs
CORE = 0.7                 # middle share of each syllable used (alignment steps are 80 ms)
LEVEL_WEIGHT = 0.3         # height matters less than shape: intonation moves it
POINTS = 5                 # contour samples per syllable
SKIP_CHARS = set("一不")    # sandhi pypinyin does not fully apply
_CJK = re.compile(r"[㐀-鿿豈-﫿]")


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path)) as f:
        assert f.getframerate() == SR and f.getnchannels() == 1
        return np.frombuffer(f.readframes(f.getnframes()), "<i2").astype(np.float32) / 32767


def yin_f0(audio: np.ndarray, sr: int = SR, threshold: float = 0.25) -> np.ndarray:
    """F0 in Hz every HOP seconds, NaN where unvoiced (YIN, de Cheveigné & Kawahara 2002).

    The difference function of every frame is computed at once with FFTs:
    d(tau) = sum x[j]^2 + sum x[j+tau]^2 - 2 sum x[j] x[j+tau], over j < frame.
    """
    frame, hop = int(0.04 * sr), int(HOP * sr)
    tau_min, tau_max = int(sr / F0_MAX), int(sr / F0_MIN)
    width = frame + tau_max
    n = 1 + max(0, (len(audio) - width) // hop)
    idx = np.arange(width)[None, :] + hop * np.arange(n)[:, None]
    windows = audio[idx].astype(np.float64)                   # (n, width)
    head = windows[:, :frame]
    size = 1 << int(np.ceil(np.log2(2 * width)))
    cross = np.fft.irfft(np.fft.rfft(windows, size) * np.conj(np.fft.rfft(head, size)), size)[:, :tau_max + 1]
    sq = np.cumsum(windows ** 2, axis=1)
    energy_head = sq[:, frame - 1:frame]                      # sum x[j]^2, j < frame
    shifted = sq[:, frame - 1:frame + tau_max] - np.concatenate(
        [np.zeros((n, 1)), sq[:, :tau_max]], axis=1)          # sum x[j+tau]^2, j < frame
    d = energy_head + shifted - 2 * cross
    d[:, 0] = 0.0
    cmnd = np.ones_like(d)
    cmnd[:, 1:] = d[:, 1:] * np.arange(1, tau_max + 1) / np.maximum(np.cumsum(d[:, 1:], axis=1), 1e-12)

    f0 = np.full(n, np.nan)
    loud = (energy_head[:, 0] / frame) >= 1e-4
    for i in np.flatnonzero(loud):
        below = np.flatnonzero(cmnd[i, tau_min:] < threshold)
        if len(below) == 0:
            continue
        tau = tau_min + below[0]
        while tau + 1 <= tau_max and cmnd[i, tau + 1] < cmnd[i, tau]:
            tau += 1
        if 1 <= tau < tau_max:
            a, b, c = cmnd[i, tau - 1], cmnd[i, tau], cmnd[i, tau + 1]
            tau = tau + 0.5 * (a - c) / max(a - 2 * b + c, 1e-12)
        f0[i] = sr / tau
    return f0


def expected_tones(text: str) -> list[tuple[str, int]]:
    """(character, tone 1-5) for every Chinese character, in order."""
    from pypinyin import Style, lazy_pinyin

    chars = [c for c in text if _CJK.match(c)]
    syllables = lazy_pinyin("".join(chars), style=Style.TONE3, neutral_tone_with_five=True,
                            tone_sandhi=True, errors="ignore")
    tones = [int(s[-1]) if s and s[-1].isdigit() else 5 for s in syllables]
    return list(zip(chars, tones)) if len(tones) == len(chars) else []


def tone_profile(audio: np.ndarray, text: str, aligner) -> dict:
    items = [it for it in aligner.generate(audio=audio, text=text, language="Chinese")
             if it.text and _CJK.match(it.text)]
    tones = expected_tones(text)
    if not items or len(items) != len(tones):
        # Align characters by position; skip the unit if the counts disagree badly.
        n = min(len(items), len(tones))
        if n < 0.9 * max(len(items), len(tones), 1):
            return {"error": f"alignment {len(items)} chars vs text {len(tones)}"}
        items, tones = items[:n], tones[:n]

    f0 = yin_f0(audio)
    voiced = f0[~np.isnan(f0)]
    if len(voiced) < 50:
        return {"error": "almost no voiced frames"}
    semis = 12 * np.log2(f0 / np.median(voiced))

    contours: dict[int, list[np.ndarray]] = {1: [], 2: [], 3: [], 4: []}
    for item, (char, tone) in zip(items, tones):
        if tone == 5 or char in SKIP_CHARS or item.end_time - item.start_time < MIN_SYLLABLE:
            continue
        span = item.end_time - item.start_time
        a = int((item.start_time + span * (1 - CORE) / 2) / HOP)
        b = int((item.end_time - span * (1 - CORE) / 2) / HOP)
        seg = semis[a:b].copy()
        if len(seg) < POINTS or np.mean(~np.isnan(seg)) < MIN_VOICED:
            continue
        good = ~np.isnan(seg)
        seg = np.interp(np.arange(len(seg)), np.flatnonzero(good), seg[good])   # fill small gaps
        points = np.array([p.mean() for p in np.array_split(seg, POINTS)])
        contours[tone].append(points)
    profile = {t: np.mean(c, axis=0) for t, c in contours.items() if len(c) >= 3}
    return {"profile": profile, "counts": {t: len(c) for t, c in contours.items()}}


def distance(profile: dict, reference: dict) -> float:
    """RMS difference in semitones over the tones both profiles have: contour shape
    (each mean removed) plus a down-weighted difference in height."""
    shared = [t for t in reference if t in profile]
    if len(shared) < 3:
        return float("nan")
    per_tone = []
    for t in shared:
        p, r = profile[t], reference[t]
        shape = np.mean(((p - p.mean()) - (r - r.mean())) ** 2)
        per_tone.append(shape + LEVEL_WEIGHT * (p.mean() - r.mean()) ** 2)
    return float(np.sqrt(np.mean(per_tone)))


def describe(profile: dict) -> str:
    """Level and slope of each tone, the two things a dialect changes."""
    return "  ".join(f"T{t}:{np.mean(c):+.1f}/{c[-1] - c[0]:+.1f}" for t, c in sorted(profile.items()))


def main() -> None:
    from mlx_audio.stt import load

    out, samples = Path(sys.argv[1]), json.loads(Path(sys.argv[2]).read_text())
    out.mkdir(parents=True, exist_ok=True)
    aligner = load("mlx-community/Qwen3-ForcedAligner-0.6B-8bit")
    results = []
    for s in samples:
        r = tone_profile(read_wav(Path(s["wav"])), s["text"], aligner)
        results.append({**s, **r})
        status = r.get("error") or describe(r["profile"])
        print(f"  {s['name']:<34} {s['label']:<9} {status}", flush=True)

    refs = [r["profile"] for r in results if r.get("group") == "reference" and "profile" in r]
    reference = {t: np.median([p[t] for p in refs if t in p], axis=0) for t in (1, 2, 3, 4)}
    print(f"\nreference ({len(refs)} units): {describe(reference)}\n")
    print(f"{'sample':<34}{'label':<10}{'group':<12}{'distance':>9}")
    for r in sorted(results, key=lambda r: -(distance(r["profile"], reference) if "profile" in r else -1)):
        d = distance(r["profile"], reference) if "profile" in r else float("nan")
        r["distance"] = d
        print(f"{r['name']:<34}{r['label']:<10}{r.get('group', ''):<12}{d:9.2f}")

    std = [r["distance"] for r in results if r["label"] == "standard" and np.isfinite(r["distance"])]
    acc = [r["distance"] for r in results if r["label"] == "accent" and np.isfinite(r["distance"])]
    if std and acc:
        print(f"\nstandard: max {max(std):.2f}, mean {np.mean(std):.2f}   "
              f"accent: min {min(acc):.2f}, mean {np.mean(acc):.2f}   "
              f"separable: {min(acc) > max(std)}")
    (out / "results.json").write_text(json.dumps(
        [{k: v for k, v in r.items() if k != "profile"} | {"profile": {t: list(map(float, c)) for t, c in r.get("profile", {}).items()}}
         for r in results], ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
