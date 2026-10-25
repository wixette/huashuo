"""v0.2.0 issues 1 and 3: analyze a synthesized work directory. Pace of every unit against its
voice's median, slow outliers, the 马里奥 passage, what the ASR heard for 笹, instruct effects.

usage: .venv/bin/python experiments/v020_unit_analysis.py WORKDIR OUT.json [BOOK.txt]

With the book, only the units of its current plan are analyzed; otherwise every unit in the
cache, including those of earlier runs that the plan no longer uses.
"""
import difflib
import json
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from pypinyin import lazy_pinyin

from huashuo.audio import read_wav, trim_bounds

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


def heard_at(reference: str, transcript: str, char: str) -> list[str]:
    """The syllables the transcript has where the reference has `char` (by pinyin alignment)."""
    ref = [("?" if c == char else p) for c, p in zip(reference, _py(reference))]
    hyp_chars = [c for c in transcript if "一" <= c <= "鿿"]
    hyp = lazy_pinyin("".join(hyp_chars))
    ref_chars = list(reference)
    out = []
    sm = difflib.SequenceMatcher(a=ref, b=hyp, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        for i in range(i1, i2):
            if ref_chars[i] != char:
                continue
            if tag in ("replace", "equal") and j2 > j1:
                j = min(j1 + (i - i1), j2 - 1)
                out.append(f"{hyp_chars[j]}({hyp[j]})")
            else:
                out.append("<none>")
    return out


def _py(text):
    # one pinyin per character (non-hanzi kept as themselves)
    return [lazy_pinyin(c)[0] if "一" <= c <= "鿿" else c for c in text]


def plan_keys(book):
    """The cache keys of the book's current plan, as `huashuo package BOOK` computes them."""
    from huashuo import cli
    from huashuo.synth import unit_keys

    args = cli._parser().parse_args(["package", str(book)])
    _, project, plan = cli._prepare(args)
    return set(unit_keys(plan.units, cli._engine(args), project.language))


def main():
    wd, out = Path(sys.argv[1]), Path(sys.argv[2])
    rows = []
    current = plan_keys(Path(sys.argv[3])) if len(sys.argv) > 3 else None
    for meta_path in sorted((wd / "cache" / "units").glob("*.json")):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if current is not None and meta["key"] not in current:
            continue
        audio, sr = read_wav(meta_path.with_suffix(".wav"))
        a, b = trim_bounds(audio, sr)
        clip = audio[a:b]
        gaps = silences(clip, sr)
        total = len(clip) / sr
        pause = sum(e - s for s, e in gaps) / sr
        n = len(COUNTED.findall(meta["text"]))
        rows.append(dict(key=meta["key"], text=meta["text"], voice=meta["voice"].split("/")[-1],
                         instruct=meta.get("instruct"), blocks=meta["blocks"], chars=n,
                         secs=round(total, 2), rate=round(n / max(total, 0.1), 2),
                         speech_rate=round(n / max(total - pause, 0.1), 2), pause=round(pause, 2),
                         longest_pause=round(max(((e - s) / sr for s, e in gaps), default=0), 2),
                         problem=meta.get("problem"), asr=meta.get("asr"), seed=meta.get("seed")))
    rows.sort(key=lambda r: r["blocks"][0])

    by_voice = defaultdict(list)
    for r in rows:
        if r["chars"] >= 8 and not r["instruct"]:
            by_voice[r["voice"]].append(r["rate"])
    medians = {v: statistics.median(x) for v, x in by_voice.items()}
    print("voice medians (units >= 8 chars, no instruct):")
    for v, m in sorted(medians.items()):
        print(f"  {v:20s} {m:.2f} chars/s  n={len(by_voice[v])}")
    for r in rows:
        r["ratio"] = round(r["rate"] / medians.get(r["voice"], r["rate"] or 1), 2)

    print("\nlength buckets, ratio to voice median (5/50/95 percentile):")
    for lo, hi in [(1, 4), (4, 8), (8, 20), (20, 40), (40, 80), (80, 200)]:
        x = [r["ratio"] for r in rows if lo <= r["chars"] < hi and r["voice"] in medians]
        if x:
            print(f"  {lo:3d}-{hi:3d} chars n={len(x):3d}  {np.percentile(x, 5):.2f} {np.percentile(x, 50):.2f} "
                  f"{np.percentile(x, 95):.2f}")

    print("\ninstruct effect (ratio, units >= 4 chars):")
    groups = defaultdict(list)
    for r in rows:
        if r["chars"] >= 4:
            groups[r["instruct"] or "-"].append(r["ratio"])
    for k, x in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        print(f"  {k:12s} n={len(x):3d} median ratio {statistics.median(x):.2f}")

    inner = [r["ratio"] for r in rows if re.search(r"[‘’]", r["text"]) and r["chars"] >= 4]
    plain = [r["ratio"] for r in rows if not re.search(r"[‘’]", r["text"]) and r["chars"] >= 4]
    print(f"\ninner quotes ‘’: n={len(inner)} median ratio {statistics.median(inner) if inner else 0:.2f};"
          f" others {statistics.median(plain):.2f}")

    print("\nslow outliers (ratio < 0.75, >= 4 chars):")
    for r in rows:
        if r["chars"] >= 4 and r["ratio"] < 0.75:
            print(f"  {r['ratio']:.2f} {r['rate']:.2f}/s sp {r['speech_rate']:.2f} pause {r['pause']:.1f}s "
                  f"{r['voice']:16s} {r['instruct'] or '-':10s} {r['text'][:40]}")

    print("\nMario passage:")
    for r in rows:
        if "马里奥" in r["text"] or r["text"] in ("“什么？”", "什么？"):
            print(f"  {r['ratio']:.2f} {r['rate']:.2f}/s sp {r['speech_rate']:.2f} pause {r['pause']:.1f}s "
                  f"secs {r['secs']} {r['voice']:16s} {r['instruct'] or '-'} {r['blocks'][0]} {r['text']}")

    print("\n笹 readings heard by ASR:")
    heard = defaultdict(int)
    for r in rows:
        # since v0.2.0 the TTS is given the homophone: 替垣 (rare.py)
        target = "笹" if "笹" in r["text"] else "替" if "替垣" in r["text"] else None
        if target and r["asr"]:
            h = heard_at(r["text"], r["asr"], target)
            r["heard_笹"] = h
            for x in h:
                heard[x] += 1
    for k, v in sorted(heard.items(), key=lambda kv: -kv[1]):
        print(f"  {k}: {v}")
    out.write_text(json.dumps({"medians": medians, "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
