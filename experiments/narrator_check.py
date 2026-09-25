#!/usr/bin/env python3
"""Choosing the narrator: calm, full-voiced, steady pace (after the first real short novel).

Each candidate voice reads the same narration test set, plainly and with a calm-reading
instruct. Candidates: presets, library voices, and new voices designed for narration
(exp1_candidates.py with narrator_voices.json). Measured on every clip:

    rate       spoken characters per second (the trimmed clip, pauses included)
    pitch      median F0 and its spread in semitones over voiced frames: a wide spread
               is a lively, animated reading; a narrower one a steadier reading
    CER        ASR check against the text (asr.judge rules)

The sentences cover what a narrator reads: description, action, a lead-in to dialogue,
a long passage, a tense scene, a reflective passage, dates, a chapter title.

For listening, <voice>_<mode>.wav holds all sentences of one candidate, a beep before each.

Usage (from the repository root):
    .venv/bin/python experiments/narrator_check.py CANDIDATES_DIR OUT_DIR [--seeds n1=0,1,2 ...]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import mlx.core as mx
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from exp1_stability import beep  # noqa: E402
from exp1_voice_routes import free, inject_speakers, load, save_wav  # noqa: E402

from huashuo.asr import AsrChecker, cer, normalize  # noqa: E402
from huashuo.audio import trim_bounds  # noqa: E402

SR = 24000
LINES = [
    ("describe", "天刚蒙蒙亮，镇子口的石桥上已经有了挑担子的人，雾气从河面上慢慢升起来，把远处的屋顶都遮住了。"),
    ("action", "他推开门，把湿透的外套挂在门后，一句话也没说，就坐到了炉子旁边。"),
    ("lead_in", "她看了他一眼，把杯子放回桌上，低声说："),
    ("long", "那一年的秋天来得特别早。九月刚过，山上的枫叶就红了一大片，远远看去，像是整座山都烧了起来。"
             "镇上的人说，这是好兆头，今年的收成一定不错。可是住在村东头的老周却整天愁眉苦脸，谁跟他说话，"
             "他都只是叹气。直到十月初的一个夜里，一场大风从北边刮过来，吹倒了半个村子的篱笆。"),
    ("tense", "枪声响起的那一刻，所有人都愣住了，紧接着是尖叫、奔跑和玻璃碎裂的声音。"),
    ("reflect", "很多年以后，每当想起那个夏天，我总会记起外婆坐在院子里，慢慢摇着蒲扇的样子。"),
    ("date", "一九八七年的冬天格外冷，河面结了厚厚的冰，孩子们在上面追来追去。"),
    ("title", "第三章 雨夜"),
]
CALM = "用平稳、从容的语气朗读，语速适中偏慢，情绪克制"
EXISTING = ["serena", "vivian", "uncle_fu", "library:zh/mid_woman_calm", "library:zh/young_woman_cool",
            "library:zh/mid_man_steady", "library:zh/young_man_deep"]
LIBRARY = Path(__file__).resolve().parents[1] / "src" / "huashuo" / "voices"


def f0_track(pcm: np.ndarray, sr: int = SR) -> np.ndarray:
    """F0 (Hz) of voiced 40 ms frames (10 ms hop), by autocorrelation in 70-400 Hz."""
    frame, hop = int(0.04 * sr), int(0.01 * sr)
    lo, hi = int(sr / 400), int(sr / 70)
    peak = np.abs(pcm).max() + 1e-9
    out = []
    for start in range(0, len(pcm) - frame, hop):
        x = pcm[start:start + frame]
        if np.sqrt((x ** 2).mean()) < 0.05 * peak:
            continue
        x = x - x.mean()
        ac = np.correlate(x, x, "full")[frame - 1:]
        if ac[0] <= 0:
            continue
        lag = lo + int(np.argmax(ac[lo:hi]))
        if ac[lag] / ac[0] > 0.45:                          # clearly periodic: voiced
            out.append(sr / lag)
    return np.array(out)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("candidates", type=Path)
    parser.add_argument("out", type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    vectors: dict[str, mx.array] = {}
    for ref in EXISTING:
        if ref.startswith("library:"):
            vectors[ref.split("/")[-1]] = mx.array(np.load(LIBRARY / "zh" / (ref.split("/")[-1] + ".npy")))
    for npy in sorted(args.candidates.glob("n*_s*.npy")):
        vectors[npy.stem] = mx.array(np.load(npy))
    speakers = [r for r in EXISTING if not r.startswith("library:")] + list(vectors)

    cv = load("CustomVoice")
    inject_speakers(cv, {name: vec.reshape(1, -1) for name, vec in vectors.items()})
    clips = []
    for speaker in speakers:
        for mode, instruct in (("plain", None), ("calm", CALM)):
            for key, text in LINES:
                mx.random.seed(7)
                results = list(cv.generate_custom_voice(text=text, speaker=speaker, language="chinese",
                                                        instruct=instruct))
                audio = np.array(mx.concatenate([r.audio for r in results]), dtype=np.float32)
                start, end = trim_bounds(audio, SR)
                clips.append(dict(voice=speaker, mode=mode, key=key, text=text, audio=audio[start:end]))
        print(f"  {speaker}: done", flush=True)
    free(cv)

    asr = AsrChecker()
    for c in clips:
        c["asr"] = asr.transcribe(c["audio"], SR, "zh")
        c["cer"] = cer(c["text"], c["asr"], "zh")
        c["rate"] = len(normalize(c["text"], "zh")) / (len(c["audio"]) / SR)
        f0 = f0_track(c["audio"])
        c["f0_median"] = float(np.median(f0)) if len(f0) else 0.0
        c["f0_spread"] = float(np.std(12 * np.log2(f0 / np.median(f0)))) if len(f0) > 10 else 0.0
    asr.close()

    print(f"\n{'voice':<24}{'mode':<7}{'字/s':>6}{'字/s long':>10}{'F0 Hz':>7}{'spread st':>10}{'CER':>7}")
    summary = []
    for speaker in speakers:
        for mode in ("plain", "calm"):
            cs = [c for c in clips if c["voice"] == speaker and c["mode"] == mode and c["key"] != "title"]
            row = dict(voice=speaker, mode=mode, rate=float(np.median([c["rate"] for c in cs])),
                       rate_long=next(c["rate"] for c in cs if c["key"] == "long"),
                       f0=float(np.median([c["f0_median"] for c in cs])),
                       spread=float(np.mean([c["f0_spread"] for c in cs])),
                       cer=float(np.mean([c["cer"] for c in cs])))
            summary.append(row)
            print(f"{speaker:<24}{mode:<7}{row['rate']:>6.2f}{row['rate_long']:>10.2f}{row['f0']:>7.0f}"
                  f"{row['spread']:>10.2f}{row['cer']:>7.1%}")
            parts = []
            for c in (c for c in clips if c["voice"] == speaker and c["mode"] == mode):
                parts += [beep(), c["audio"]]
            save_wav(args.out / f"{speaker}_{mode}.wav", mx.array(np.concatenate(parts)), SR)
    (args.out / "report.json").write_text(json.dumps(
        {"summary": summary, "clips": [{k: v for k, v in c.items() if k != "audio"} for c in clips]},
        ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nlistening files: {args.out}/<voice>_<plain|calm>.wav")


if __name__ == "__main__":
    main()
