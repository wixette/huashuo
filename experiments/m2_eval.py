#!/usr/bin/env python3
"""M2 check: the package's speaker attribution on the EXP-2 benchmarks, with a real LLM.

A manual script, never part of the test suite: it spends money. It builds the script the
way `huashuo import` does (structure + dialogue split), runs `huashuo.attribution` with
the configured model, and scores every gold quote (aliases count, as in EXP-2).

Usage (from the repository root; the LLM key comes from .env or the environment):
    .venv/bin/python experiments/m2_eval.py experiments/exp2_data/kongyiji.json --max-cost 0.15
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path

from huashuo.attribution import Caller, attribute_script, llm_config
from huashuo.ingest import Book, Paragraph, Section
from huashuo.structure import build


def _norm(text: str) -> str:
    return re.sub(r"[\s\"“”「」『』,，。！？!?、：:；;…—\-]", "", text)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("bench", type=Path)
    parser.add_argument("--model")
    parser.add_argument("--max-cost", type=float, required=True, help="USD cap for this run (required)")
    parser.add_argument("--cache", type=Path, help="answer cache directory (default: a fresh temporary one)")
    args = parser.parse_args()

    bench = json.loads(args.bench.read_text(encoding="utf-8"))
    config = llm_config(args.model, max_cost=args.max_cost)
    if config is None:
        sys.exit("no LLM key configured (HUASHUO_LLM_API_KEY / OPENAI_API_KEY or .env)")
    built = build(Book(bench["name"], "", bench["language"], [Section([Paragraph(p) for p in bench["paragraphs"]])], "txt"))
    cache = args.cache or Path(tempfile.mkdtemp(prefix="m2-eval-"))
    caller = Caller(config, cache)
    result = attribute_script(built.script, bench["language"], caller)

    quotes = [b for b in result.blocks if b["type"] in ("dialogue", "narration")]
    correct, rows, cursor = 0, [], 0
    for gold in bench["quotes"]:
        target = _norm(gold["quote"])
        j = next((k for k in range(cursor, len(quotes)) if _norm(quotes[k]["text"]) == target), None)
        if j is None:
            rows.append((gold["quote"], gold["speaker"], "(not found)"))
            continue
        cursor = j + 1
        block = quotes[j]
        predicted = block.get("speaker", "unknown") if block["type"] == "dialogue" else "narrator"
        accepted = set()
        for g in gold["speaker"]:
            accepted |= set(bench["aliases"].get(g, [g]))
        names = {predicted} | set(result.characters.get(predicted, {}).get("aliases", []))
        if names & accepted:
            correct += 1
        else:
            rows.append((gold["quote"], gold["speaker"], predicted))
    u = caller.usage
    print(f"{bench['name']} with {config.model}: {correct}/{len(bench['quotes'])} correct; "
          f"{u.requests} requests, {u.input_tokens:,}+{u.output_tokens:,} tokens, ${u.cost:.4f} (cap ${args.max_cost:.2f})")
    if result.stopped:
        print(f"stopped early: {result.stopped}")
    for quote, gold, got in rows:
        print(f"  ✗ {quote[:30]:<32} gold {'/'.join(gold)}  got {got}")
    print("cast:", ", ".join(f"{n}{c['aliases'] or ''}" for n, c in result.characters.items()))


if __name__ == "__main__":
    main()
