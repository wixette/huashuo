#!/usr/bin/env python3
"""Re-record the LLM answers the golden tests replay (examples/fish/llm-cache).

Needed when the prompts change: tests/test_golden.py then skips its speaker tests. Imports
both formats of the story with the real LLM into a temporary directory, capped, and
replaces the stored answers. The speaker labels (speakers.tsv) are the author's and are
not touched; the script reports where the new answers disagree with them.

Usage (from the repository root; needs HUASHUO_LLM_API_KEY or a .env):
    .venv/bin/python examples/refresh_golden.py [--max-cost 0.30]
"""

from __future__ import annotations

import argparse
import shutil
import tempfile
from pathlib import Path

from huashuo.attribution import llm_config
from huashuo.pipeline import LLMOptions, import_book, load_project
from huashuo.workdir import Workdir

FISH = Path(__file__).resolve().parent / "fish"
SOURCES = [FISH / "txt" / "一条被洗澡水拍死的鱼.txt", FISH / "epub" / "一条被洗澡水拍死的鱼.epub"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-cost", type=float, default=0.30, help="USD cap for each format")
    args = parser.parse_args()
    config = llm_config("gpt-6-sol", None, args.max_cost, 6)
    if config is None:
        raise SystemExit("no LLM key: set HUASHUO_LLM_API_KEY (or OPENAI_API_KEY)")
    gold = [line.split("\t")[1] for line in (FISH / "speakers.tsv").read_text(encoding="utf-8").splitlines()
            if line and not line.startswith("#")]
    answers: dict[str, bytes] = {}
    with tempfile.TemporaryDirectory() as tmp:
        for source in SOURCES:
            book = Path(tmp) / source.suffix[1:] / source.name
            book.parent.mkdir()
            shutil.copy(source, book)
            wd = Workdir.for_input(book)
            result = import_book(book, wd, llm=LLMOptions(config_override=config, assume_yes=True))
            usage = result.llm.usage
            got = [b.get("speaker") for b in load_project(wd).script.blocks if b["type"] == "dialogue"]
            differ = [(n + 1, g, s) for n, (g, s) in enumerate(zip(got, gold)) if g != s]
            print(f"{source.suffix[1:]}: {usage.requests} requests, ${usage.cost:.3f}; "
                  f"{len(got) - len(differ)}/{len(gold)} match the labels" + (f"; differ: {differ}" if differ else ""))
            for f in (wd.state / "llm-cache").glob("*.json"):
                answers[f.name] = f.read_bytes()
    target = FISH / "llm-cache"
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir()
    for name, data in answers.items():
        (target / name).write_bytes(data)
    print(f"{len(answers)} answers stored in {target}")


if __name__ == "__main__":
    main()
