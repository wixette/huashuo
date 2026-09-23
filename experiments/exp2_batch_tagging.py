#!/usr/bin/env python3
"""EXP-2: paragraph-batched speaker attribution (design doc §7.2, requirements SCR-3…SCR-7).

Two passes, as in audiobook-creator's design but with our own prompts and a batched
second pass:

  Pass 1  cast       Chunks of ~2,500 characters. The model returns explicit operations
                     (insert / update / merge) against the running cast, so aliases such as
                     苏姑娘 -> 苏晚晴 are merged as a state change, not hoped for.
  Pass 2  attribute  Numbered segments in chunks, with a few segments of context on either
                     side. The model answers only index -> speaker for the quotes it is
                     asked about, never echoing text. The speaker field is a Literal of the
                     cast names plus "narrator" (quoted phrases that are not speech) and
                     "unknown", so it cannot invent a name.
  Reconcile          Unanswered indices are asked again, answers for indices that were not
                     asked are dropped.

Scored against a benchmark file (experiments/exp2_data/*.json): accuracy on every quote,
plus requests, tokens, time and, given prices, cost.

Model configuration (OpenAI-compatible endpoint, requirements §3.9), from the environment
or a `.env` file in the repository root:
    HUASHUO_LLM_MODEL      e.g. gpt-6-luna
    HUASHUO_LLM_API_KEY    (falls back to OPENAI_API_KEY)
    HUASHUO_LLM_BASE_URL   default https://api.openai.com/v1

Usage (from the repository root):
    .venv/bin/python experiments/exp2_batch_tagging.py experiments/exp2_data/kongyiji.json
    .venv/bin/python experiments/exp2_batch_tagging.py BENCH.json --model test   # offline plumbing check
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, create_model

ROOT = Path(__file__).resolve().parents[1]
QUOTE = re.compile(r"“[^”]*”|「[^」]*」|『[^』]*』|\"[^\"]*\"")


# --------------------------------------------------------------------------------------
# Configuration and model
# --------------------------------------------------------------------------------------


def load_dotenv(path: Path = ROOT / ".env") -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def make_model(name: str, base_url: str | None):
    if name == "test":
        from pydantic_ai.models.test import TestModel
        return TestModel()
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider

    key = os.environ.get("HUASHUO_LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not key:
        sys.exit("no API key: set HUASHUO_LLM_API_KEY (or OPENAI_API_KEY), e.g. in .env")
    url = base_url or os.environ.get("HUASHUO_LLM_BASE_URL") or "https://api.openai.com/v1"
    return OpenAIChatModel(name, provider=OpenAIProvider(base_url=url, api_key=key))


def wrap_output(output_type, mode: str):
    from pydantic_ai import NativeOutput, PromptedOutput, ToolOutput
    return {"tool": ToolOutput, "native": NativeOutput, "prompted": PromptedOutput}[mode](output_type)


@dataclass
class Meter:
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0

    def add(self, usage, seconds: float) -> None:
        usage = usage() if callable(usage) else usage   # a method in pydantic-ai 1.x, a property in 2.x
        self.requests += usage.requests
        self.input_tokens += usage.input_tokens or 0
        self.output_tokens += usage.output_tokens or 0
        self.seconds += seconds


# --------------------------------------------------------------------------------------
# Segmentation (SCR-2)
# --------------------------------------------------------------------------------------


@dataclass
class Segment:
    index: int
    paragraph: int
    kind: str          # "quote" or "narration"
    text: str


def segment(paragraphs: list[str]) -> list[Segment]:
    segments: list[Segment] = []
    for p, text in enumerate(paragraphs):
        cursor = 0
        for match in QUOTE.finditer(text):
            if text[cursor:match.start()].strip():
                segments.append(Segment(len(segments), p, "narration", text[cursor:match.start()].strip()))
            segments.append(Segment(len(segments), p, "quote", match.group()))
            cursor = match.end()
        if text[cursor:].strip():
            segments.append(Segment(len(segments), p, "narration", text[cursor:].strip()))
    return segments


def chunks_by_chars(items: list, size: int, length) -> list[list]:
    out, current, count = [], [], 0
    for item in items:
        if current and count + length(item) > size:
            out.append(current)
            current, count = [], 0
        current.append(item)
        count += length(item)
    return out + ([current] if current else [])


# --------------------------------------------------------------------------------------
# Pass 1: cast
# --------------------------------------------------------------------------------------

Age = Literal["child", "teen", "young_adult", "middle_aged", "elderly", "unknown"]


class CastOp(BaseModel):
    op: Literal["insert", "update", "merge"] = Field(
        description="insert: a character not yet in the cast; update: add details to an existing one; "
                    "merge: `name` is another way of referring to the existing `merge_into`")
    name: str = Field(description="the fullest name the text gives; for unnamed characters a short role, e.g. 掌柜")
    merge_into: str | None = Field(None, description="only for merge: the existing cast name")
    aliases: list[str] = Field(default_factory=list, description="other ways the text refers to this character")
    gender: Literal["male", "female", "unknown"] = "unknown"
    age: Age = "unknown"
    description: str = Field("", description="one sentence: role, personality, voice")


class CastUpdate(BaseModel):
    operations: list[CastOp]


CAST_INSTRUCTIONS = """你在为一部小说制作多角色有声书，负责整理「会说话的角色」名单。

每次你会收到：目前的角色表（JSON）和小说的下一段原文。请只输出对角色表的修改操作：
- insert：原文中出现了一个会说话、角色表里还没有的角色
- update：给已有角色补充别名、性别、年龄或描述
- merge：原文用另一个称呼指代角色表里已有的人（如「苏姑娘」就是「苏晚晴」）。name 写这个称呼，merge_into 写已有的名字

规则：
- 规范名优先用全名；只有称谓时用最具体的称谓（「掌柜」「老者」）。
- 同姓不等于同一人（「萧战」和「萧炎」是两个人），只有原文明确指同一人时才 merge。
- 第一人称叙述者如果说话，作为角色「我」加入。
- 一群不具名、轮流插话的人（「喝酒的人」「众人」「旁人」）作为一个群体角色加入。
- 不要加入地点、物品、只被提到但从不说话的人。
- 性别、年龄只依据原文线索（他/她、公子/姑娘、老者/少年），没有线索就写 unknown。
- 没有需要修改的地方时，返回空的 operations。"""


def apply_ops(cast: dict[str, dict], ops: list[CastOp]) -> None:
    def resolve(name: str) -> str | None:
        if name in cast:
            return name
        return next((k for k, v in cast.items() if name in v["aliases"]), None)

    for op in ops:
        name = op.name.strip()
        if not name:
            continue
        if op.op == "merge" and op.merge_into and (target := resolve(op.merge_into.strip())):
            entry = cast[target]
            for alias in [name, *op.aliases]:
                if alias != target and alias not in entry["aliases"]:
                    entry["aliases"].append(alias)
            if name in cast and name != target:          # it had been inserted separately
                entry["aliases"] += [a for a in cast.pop(name)["aliases"] if a not in entry["aliases"]]
            continue
        existing = resolve(name)
        if existing is None:
            cast[name] = {"aliases": [a for a in op.aliases if a != name], "gender": op.gender,
                          "age": op.age, "description": op.description}
            continue
        entry = cast[existing]
        entry["aliases"] += [a for a in op.aliases if a != existing and a not in entry["aliases"]]
        if op.gender != "unknown":
            entry["gender"] = op.gender
        if op.age != "unknown":
            entry["age"] = op.age
        if op.description:
            entry["description"] = op.description


def build_cast(paragraphs: list[str], model, mode: str, chunk_chars: int, meter: Meter) -> dict[str, dict]:
    from pydantic_ai import Agent

    agent = Agent(model, output_type=wrap_output(CastUpdate, mode), instructions=CAST_INSTRUCTIONS, retries=3)
    cast: dict[str, dict] = {}
    for chunk in chunks_by_chars(paragraphs, chunk_chars, len):
        prompt = f"目前的角色表：\n{json.dumps(cast, ensure_ascii=False)}\n\n原文：\n" + "\n".join(chunk)
        started = time.time()
        result = agent.run_sync(prompt)
        meter.add(result.usage, time.time() - started)
        apply_ops(cast, result.output.operations)
    return cast


# --------------------------------------------------------------------------------------
# Pass 2: batched attribution
# --------------------------------------------------------------------------------------

ATTRIBUTE_INSTRUCTIONS = """你在为一部小说制作多角色有声书，负责判断每一段引号内的话由谁来读。

你会收到角色表，以及一段编号的原文。每行是一个片段：「引」表示引号里的内容，「叙」表示叙述。
请只为「需要回答的编号」各给出一个答案，不要复述原文：
- speaker：说这句话的角色，必须是角色表中的规范名；
  引号里不是有人说出的话（书名、引用的词句、招牌上的字）时写 narrator；
  实在无法判断时写 unknown。
- confidence：0 到 1 之间，表示你的把握。

判断时注意：
- 说话人标签可能在引号之前、之后，或者被省略（两人轮流对话时，按轮次推断）。
- 引号里出现某人的名字，通常说明是在对这个人说话，而不是这个人在说。
- 被叙述打断的一句话，前后两段属于同一个说话人。"""


def attribution_type(names: list[str]):
    speaker = Literal[tuple(names + ["narrator", "unknown"])]  # type: ignore[misc]
    answer = create_model("Answer", index=(int, ...), speaker=(speaker, ...),
                          confidence=(float, Field(0.5, ge=0, le=1)))
    return create_model("Answers", answers=(list[answer], ...))


def render(segments: list[Segment]) -> str:
    return "\n".join(f"[{s.index}] {'引' if s.kind == 'quote' else '叙'}：{s.text}" for s in segments)


def cast_summary(cast: dict[str, dict]) -> str:
    rows = []
    for name, c in cast.items():
        alias = f"（又称：{'、'.join(c['aliases'])}）" if c["aliases"] else ""
        rows.append(f"- {name}{alias}：{c['gender']}，{c['age']}。{c['description']}")
    return "\n".join(rows) or "（空）"


def attribute(segments: list[Segment], cast: dict[str, dict], model, mode: str, chunk_chars: int,
              context: int, meter: Meter) -> dict[int, tuple[str, float]]:
    from pydantic_ai import Agent

    names = list(cast)
    agent = Agent(model, output_type=wrap_output(attribution_type(names), mode),
                  instructions=ATTRIBUTE_INSTRUCTIONS, retries=3)
    header = f"角色表：\n{cast_summary(cast)}\n\n"
    answers: dict[int, tuple[str, float]] = {}

    def ask(core: list[Segment], wanted: list[int]) -> None:
        lo = max(0, core[0].index - context)
        hi = min(len(segments), core[-1].index + 1 + context)
        prompt = (header + "原文（编号片段）：\n" + render(segments[lo:hi]) +
                  f"\n\n需要回答的编号：{', '.join(map(str, wanted))}")
        started = time.time()
        result = agent.run_sync(prompt)
        meter.add(result.usage, time.time() - started)
        for a in result.output.answers:
            if a.index in wanted:                       # drop answers nobody asked for
                answers[a.index] = (a.speaker, a.confidence)

    for core in chunks_by_chars(segments, chunk_chars, lambda s: len(s.text)):
        wanted = [s.index for s in core if s.kind == "quote"]
        if wanted:
            ask(core, wanted)
    missing = [s.index for s in segments if s.kind == "quote" and s.index not in answers]
    for index in missing:                               # reconcile: ask again, one at a time
        ask([segments[index]], [index])
    return answers


# --------------------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------------------


def _norm(text: str) -> str:
    return re.sub(r"[\s\"“”「」『』,，。！？!?、：:；;…—\-]", "", text)


def score(bench: dict, segments: list[Segment], answers: dict, cast: dict) -> dict:
    """Match gold quotes to quote segments in order, then compare speakers through aliases."""
    quotes = [s for s in segments if s.kind == "quote"]
    rows, cursor = [], 0
    for gold in bench["quotes"]:
        target = _norm(gold["quote"])
        j = next((k for k in range(cursor, len(quotes)) if _norm(quotes[k].text) == target
                  or (target and target in _norm(quotes[k].text))), None)
        if j is None:
            rows.append({"quote": gold["quote"], "gold": gold["speaker"], "predicted": None, "ok": False})
            continue
        cursor = j + 1
        predicted, confidence = answers.get(quotes[j].index, (None, None))
        accepted = set()
        for g in gold["speaker"]:
            accepted |= set(bench["aliases"].get(g, [g]))
        names = {predicted} | set(cast.get(predicted, {}).get("aliases", [])) if predicted else set()
        rows.append({"quote": gold["quote"], "gold": gold["speaker"], "predicted": predicted,
                     "confidence": confidence, "ok": bool(names & accepted)})
    correct = sum(r["ok"] for r in rows)
    return {"correct": correct, "total": len(rows), "accuracy": correct / max(1, len(rows)), "rows": rows}


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("bench", type=Path)
    parser.add_argument("--model", default=os.environ.get("HUASHUO_LLM_MODEL"))
    parser.add_argument("--base-url")
    parser.add_argument("--output-mode", choices=["tool", "native", "prompted"], default="native",
                        help="structured output via response_format (native, avoids the reasoning+tools "
                             "error of design doc §6.4), function tools, or prompted JSON")
    parser.add_argument("--cast-chunk-chars", type=int, default=2500)
    parser.add_argument("--attr-chunk-chars", type=int, default=1500)
    parser.add_argument("--context", type=int, default=3, help="segments of context on each side")
    parser.add_argument("--price-in", type=float, help="USD per million input tokens")
    parser.add_argument("--price-out", type=float, help="USD per million output tokens")
    parser.add_argument("--out", type=Path, help="write the full result as JSON")
    args = parser.parse_args()
    if not args.model:
        sys.exit("no model: pass --model or set HUASHUO_LLM_MODEL")

    bench = json.loads(args.bench.read_text(encoding="utf-8"))
    model = make_model(args.model, args.base_url)
    segments = segment(bench["paragraphs"])
    quotes = sum(s.kind == "quote" for s in segments)
    print(f"{bench['name']}: {len(bench['paragraphs'])} paragraphs, {len(segments)} segments, {quotes} quotes")

    m1, m2 = Meter(), Meter()
    cast = build_cast(bench["paragraphs"], model, args.output_mode, args.cast_chunk_chars, m1)
    print(f"\npass 1: {len(cast)} characters, {m1.requests} requests, {m1.input_tokens}+{m1.output_tokens} tokens, {m1.seconds:.1f}s")
    for name, c in cast.items():
        print(f"  {name} {c['aliases'] or ''} {c['gender']}/{c['age']}  {c['description'][:40]}")
    answers = attribute(segments, cast, model, args.output_mode, args.attr_chunk_chars, args.context, m2)
    print(f"pass 2: {m2.requests} requests, {m2.input_tokens}+{m2.output_tokens} tokens, {m2.seconds:.1f}s")

    result = score(bench, segments, answers, cast)
    print(f"\naccuracy: {result['correct']}/{result['total']} = {result['accuracy']:.0%}")
    for r in result["rows"]:
        if not r["ok"]:
            print(f"  ✗ {r['quote'][:30]:<32} gold {'/'.join(r['gold'])}  got {r['predicted']} ({r.get('confidence')})")
    total_in, total_out = m1.input_tokens + m2.input_tokens, m1.output_tokens + m2.output_tokens
    cost = ""
    if args.price_in is not None and args.price_out is not None:
        cost = f", ${(total_in * args.price_in + total_out * args.price_out) / 1e6:.4f}"
    print(f"total: {m1.requests + m2.requests} requests, {total_in}+{total_out} tokens, "
          f"{m1.seconds + m2.seconds:.1f}s{cost}")
    if args.out:
        args.out.write_text(json.dumps({"model": args.model, "cast": cast, **result,
                                        "usage": {"pass1": m1.__dict__, "pass2": m2.__dict__}},
                                       ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
