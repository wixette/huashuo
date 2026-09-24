#!/usr/bin/env python3
"""M5: how does Qwen3-TTS read numbers, and can it take pinyin? (PRON-1, PRON-2)

Every line is rendered by one voice with a fixed seed and transcribed by Qwen3-ASR, which
writes what it hears in characters (a cardinal 1234 comes back as 一千二百三十四, a digit
string as 一二三四). The transcript, not the text, shows the reading. Lines are grouped:

    numbers   years, counts, ordinals, percentages, scores, times, dates, decimals,
              phone numbers, units, fractions, currency, ranges, room numbers, model names
    pinyin    a word followed by its pinyin (tone marks, tone digits), pinyin alone, and a
              homophone substitution, to see which forms steer the pronunciation

For listening: all.wav, each line after a beep, in the order of report.json.

Usage (from the repository root):
    .venv/bin/python experiments/m5_reading_check.py OUT_DIR [--voice preset:serena]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from huashuo.asr import AsrChecker
from huashuo.audio import write_wav
from huashuo.engines.qwen3 import Qwen3Engine

NUMBERS = [
    ("year", "他是1998年出生的。"),
    ("year_short", "那是98年的冬天。"),
    ("count", "剑身上刻着1234个小字。"),
    ("count_big", "这座城有12,000户人家，共35000人。"),
    ("wan", "他一年挣1.5万元。"),
    ("ordinal", "他考了第3名。"),
    ("percent", "成功率只有5%，失败率是12.5%。"),
    ("score", "比赛最后以3:2结束。"),
    ("time", "早上8:30出发，晚上20:15到达。"),
    ("date_cn", "2026年9月25日是星期五。"),
    ("date_iso", "日期写的是2026-09-25。"),
    ("decimal", "圆周率约等于3.14。"),
    ("phone", "他的电话是13812345678。"),
    ("phone_area", "办公室电话010-12345678。"),
    ("unit_latin", "他每天跑5km，体重70kg。"),
    ("unit_temp", "今天最高30℃，最低-5℃。"),
    ("fraction", "只剩下1/3的粮食了。"),
    ("currency", "一张票¥100，换成美元大约$14。"),
    ("range", "要走3-5天才能到。"),
    ("room", "他住在302房间。"),
    ("model", "他换了一部iPhone 15。"),
    ("no", "他是No.1。"),
]
PINYIN = [
    ("plain", "匈奴的单于骑着马来了。"),
    ("marks_paren", "匈奴的单于（chán yú）骑着马来了。"),
    ("marks_only", "匈奴的chán yú骑着马来了。"),
    ("digits_only", "匈奴的chan2 yu2骑着马来了。"),
    ("homophone", "匈奴的蝉于骑着马来了。"),
    ("poly_plain", "他重新整理了银行的账目。"),
    ("poly_marks", "他chóng新整理了银háng的账目。"),
    ("name_plain", "这是尉迟恭的府邸。"),
    ("name_marks", "这是yù chí恭的府邸。"),
]


def beep(sr: int) -> np.ndarray:
    t = np.arange(int(sr * 0.15)) / sr
    return np.concatenate([np.zeros(int(sr * 0.4)), 0.1 * np.sin(2 * np.pi * 880 * t) * np.hanning(len(t)),
                           np.zeros(int(sr * 0.35))]).astype(np.float32)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("out", type=Path)
    parser.add_argument("--voice", default="preset:serena")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    lines = [("numbers", k, t) for k, t in NUMBERS] + [("pinyin", k, t) for k, t in PINYIN]
    engine = Qwen3Engine()
    clips = []
    for group, key, text in lines:
        audio = engine.synthesize(text, args.voice, "zh", None, 7)
        clips.append((group, key, text, audio))
    sr = engine.sample_rate
    engine.close()
    asr = AsrChecker()
    report, parts, t = [], [], 0.0
    for group, key, text, audio in clips:
        heard = asr.transcribe(audio, sr, "zh")
        report.append({"group": group, "key": key, "text": text, "heard": heard, "at": f"{int(t // 60)}:{int(t % 60):02d}"})
        parts += [beep(sr), audio]
        t += (len(parts[-2]) + len(audio)) / sr
        print(f"{group:8} {key:12} {text}\n{'':22}heard: {heard}")
    asr.close()
    write_wav(args.out / "all.wav", np.concatenate(parts), sr)
    (args.out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
