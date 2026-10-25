#!/usr/bin/env python3
"""v0.2.0: the pace of every library voice against the narrators (docs/design-and-research.md §5.6, §5.14).

A character voice much slower than the narrator sounds like the book suddenly slowing down
whenever that character speaks (old_man_hoarse in 白夜行: 0.65x the narrator). Every voice
reads the same 8 calm lines (exp1_stability.py N1-N4, D1-D4) with no instruct, through
huashuo's own engine (route C, as a book is made), with SEEDS seeds each; the rate is
counted characters per second of trimmed audio. Each voice's median is recorded as `pace`
in its JSON, and tests/test_library.py keeps every character voice within 0.75-1.3x the
narrators (2026-10-25: all but old_man_hoarse 0.67x and old_man_kind 0.68x, both redesigned).

Usage (from the repository root):
    .venv/bin/python experiments/v020_voice_pace.py OUT.json [VOICE_ID ...]
    .venv/bin/python experiments/v020_voice_pace.py OUT.json --file DIR/candidate.npy ...   # a candidate vector

With --file, each .npy is registered as a temporary library voice copied from
old_man_hoarse's JSON, so candidates can be measured before they enter the library.
"""

from __future__ import annotations

import json
import re
import statistics
import sys
from pathlib import Path

LINES = [
    "天刚蒙蒙亮，镇子口的石桥上已经有了挑担子的人，吆喝声一阵高过一阵。",
    "他在门口站了很久，直到屋里的灯一盏接一盏地熄灭，才转身走进雨里。",
    "那封信被压在箱子最底下，纸已经发黄，字迹却还清清楚楚。",
    "一九八七年的冬天格外冷，河面结了厚厚的冰，孩子们在上面追来追去。",
    "你先坐下，喝口热茶，慢慢说。",
    "这件事我想了很久，还是觉得应该告诉你。",
    "明天一早我们就出发，路上要走三天，你把东西收拾好。",
    "别担心，我认得这条路，天黑之前一定能到。",
]
SEEDS = [11, 12]
COUNTED = re.compile(r"[㐀-鿿]")


def main(argv: list[str]) -> None:
    from huashuo import library
    from huashuo.audio import trim_bounds
    from huashuo.engines.qwen3 import Qwen3Engine

    out = Path(argv[0])
    ids = argv[1:]
    if ids[:1] == ["--file"]:
        import shutil
        import tempfile

        tmp = Path(tempfile.mkdtemp()) / "voices"
        shutil.copytree(library.ROOT, tmp)
        names = []
        for npy in map(Path, ids[1:]):
            name = f"cand_{npy.stem}"
            shutil.copy(npy, tmp / "zh" / f"{name}.npy")
            entry = json.loads((library.ROOT / "zh" / "old_man_hoarse.json").read_text(encoding="utf-8"))
            (tmp / "zh" / f"{name}.json").write_text(json.dumps({**entry, "id": name}, ensure_ascii=False),
                                                    encoding="utf-8")
            names.append(name)
        library.ROOT = tmp
        library._load.cache_clear()
        ids = names
    voices = [f"library:zh/{i}" for i in ids] if ids else \
        sorted(v.ref for v in library.all_voices().values() if v.kind == "library" and v.language == "zh")
    results = json.loads(out.read_text(encoding="utf-8")) if out.is_file() else {}
    engine = Qwen3Engine()
    for voice in voices:
        if voice in results:
            continue
        rates = []
        for text in LINES:
            for seed in SEEDS:
                audio = engine.synthesize(text, voice, "zh", None, seed)
                a, b = trim_bounds(audio, engine.sample_rate)
                rates.append(len(COUNTED.findall(text)) / max((b - a) / engine.sample_rate, 0.1))
        results[voice] = {"median": round(statistics.median(rates), 2), "rates": [round(r, 2) for r in rates]}
        print(voice, results[voice]["median"], flush=True)
        out.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    engine.close()
    narrators = [results[v]["median"] for v in results if "narrator" in v]
    if narrators:
        base = statistics.mean(narrators)
        for voice, r in sorted(results.items(), key=lambda kv: kv[1]["median"]):
            print(f"{voice:34s} {r['median']:.2f}  {r['median'] / base:.2f}x the narrators' {base:.2f}")


if __name__ == "__main__":
    main(sys.argv[1:])
