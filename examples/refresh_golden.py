#!/usr/bin/env python3
"""Record the LLM answers a golden example replays (examples/<name>/llm-cache).

Needed when the prompts change, or a new LLM call is added: tests/test_golden.py then skips
the tests that need the missing answers. Imports every format of the example with the real
LLM into a temporary directory, capped, and replaces the stored answers. The stored answers
seed each import, so unchanged prompts are answered from them for free. The speaker labels
(speakers.tsv) are checked by the author and are not touched; the script reports where the
new answers disagree with them.

Usage (from the repository root; needs HUASHUO_LLM_API_KEY or a .env):
    .venv/bin/python examples/refresh_golden.py fish [--max-cost 0.30]
    .venv/bin/python examples/refresh_golden.py ad
"""

from __future__ import annotations

import argparse
import shutil
import tempfile
from pathlib import Path

import huashuo.workdir
from huashuo.attribution import Caller, attribute_script, llm_config
from huashuo.ingest import read_book
from huashuo.library import castable, default_narrator
from huashuo.pipeline import LLMOptions, import_book, load_project
from huashuo.structure import build
from huashuo.workdir import Workdir

EXAMPLES = Path(__file__).resolve().parent


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("example", help="the example's directory under examples/, e.g. fish")
    parser.add_argument("--max-cost", type=float, default=0.30, help="USD cap for each format")
    args = parser.parse_args()
    root = EXAMPLES / args.example
    sources = sorted(p for p in root.glob("*/*") if p.suffix in (".txt", ".epub"))
    if not sources:
        raise SystemExit(f"no .txt or .epub under {root}")
    config = llm_config("gpt-6-sol", None, args.max_cost, 6)
    if config is None:
        raise SystemExit("no LLM key: set HUASHUO_LLM_API_KEY (or OPENAI_API_KEY)")
    labels = root / "speakers.tsv"
    gold = [line.split("\t")[1] for line in labels.read_text(encoding="utf-8").splitlines()
            if line and not line.startswith("#")] if labels.is_file() else []
    target = root / "llm-cache"
    answers: dict[str, bytes] = {}
    # Keep only the answers these imports asked for (seeded ones that are stale are dropped).
    asked: set[str] = set()
    read_json = huashuo.workdir.read_json

    def recording(path, *args, **kwargs):
        if Path(path).parent.name == "llm-cache":
            asked.add(Path(path).name)
        return read_json(path, *args, **kwargs)
    huashuo.workdir.read_json = recording
    with tempfile.TemporaryDirectory() as tmp:
        for source in sources:
            book = Path(tmp) / source.suffix[1:] / source.name
            book.parent.mkdir()
            shutil.copy(source, book)
            wd = Workdir.for_input(book)
            if target.is_dir():
                shutil.copytree(target, wd.state / "llm-cache")
            # Attribute with calls allowed and the stored answers as cache, so every missing
            # answer is paid for, voice suggestions included (an import would reuse the
            # previous voices instead), and nothing else. The prompts are the import's own:
            # the same script, voices and default narrator.
            built = build(read_book(book))
            language = built.script.header["language"]
            caller = Caller(config, wd.state / "llm-cache")
            attribute_script(built.script, language, caller, None, castable(language), default_narrator(language))
            usage = caller.usage
            import_book(book, wd, llm=LLMOptions(config_override=config, assume_yes=True))   # answered from the cache
            got = [b.get("speaker") for b in load_project(wd).script.blocks if b["type"] == "dialogue"]
            differ = [(n + 1, g, s) for n, (g, s) in enumerate(zip(got, gold)) if g != s]
            print(f"{source.suffix[1:]}: {usage.requests} requests, ${usage.cost:.3f}"
                  + (f"; {len(got) - len(differ)}/{len(gold)} match the labels" if gold else
                     f"; no speakers.tsv yet, {len(got)} quotes")
                  + (f"; differ: {differ}" if differ else ""))
            for f in (wd.state / "llm-cache").glob("*.json"):
                if f.name in asked:
                    answers[f.name] = f.read_bytes()
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir()
    for name, data in answers.items():
        (target / name).write_bytes(data)
    print(f"{len(answers)} answers stored in {target}")


if __name__ == "__main__":
    main()
