"""Speaker attribution with an LLM (SCR-3 … SCR-7, SCR-11; LLM-1 … LLM-7).

The design EXP-2 validated (design doc §7.2.1):

  Pass 1  cast       Paragraph text in ~2,500-character chunks. The model returns explicit
                     insert / update / merge operations against the running cast, so
                     aliases (苏姑娘 -> 苏晚晴) are merged as a state change.
  Pass 2  speakers   Numbered segments in ~1,500-character chunks with context on both
                     sides. The model answers only index -> speaker for the quotes, never
                     echoing text; the speaker is a Literal of the cast names plus
                     "narrator" (a quote that is not speech) and "unknown".
  Reconcile          Unanswered quotes are asked again one at a time.

Every call goes through `Caller`: responses are cached on disk by the exact prompt, model
and prompt version, so an unchanged book is re-imported for free; and a budget guard
stops before any uncached call that could push spending past the cap.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, create_model

from huashuo.huaben import Script
from huashuo.units import paragraph_of

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

PROMPT_VERSION = 1
DEFAULT_MODEL = "gpt-6-sol"          # requirements §9.1 Q17
DEFAULT_MAX_COST = 5.0               # NFR-4: about $3, at most $5 per 300k characters
CAST_CHUNK_CHARS = 2500
ATTR_CHUNK_CHARS = 1500
CONTEXT_SEGMENTS = 3
MAX_OUTPUT_TOKENS = {"cast": 3000, "speakers": 2000}

# USD per million tokens (input, output), design doc §6.3 (2026-09).
PRICES = {"gpt-6-luna": (0.10, 0.50), "gpt-6-sol": (2.00, 10.00), "gpt-6-astra": (10.00, 50.00),
          "gpt-5.6-luna": (0.20, 1.20), "gpt-5.6-sol": (4.00, 20.00), "gpt-5.6-terra": (2.00, 12.00),
          "gpt-5.4-mini": (0.75, 4.50), "gpt-5.4-nano": (0.20, 1.25), "gpt-5-nano": (0.05, 0.40),
          "gpt-4o-mini": (0.15, 0.60)}
# Measured in EXP-2 over both passes: ~3.6 input and ~0.35 output tokens per character.
TOKENS_PER_CHAR = (3.6, 0.35)


class LLMError(Exception):
    """The LLM stage cannot run; the message says why and what to do."""


class BudgetExceeded(LLMError):
    pass


# --------------------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------------------


def load_dotenv(paths: list[Path] | None = None) -> None:
    """Read KEY=VALUE lines from .env files into the environment (existing values win).
    Off when HUASHUO_NO_DOTENV is set, as it is throughout the test suite."""
    if os.environ.get("HUASHUO_NO_DOTENV"):
        return
    for path in paths or [Path.cwd() / ".env", Path.home() / ".config" / "huashuo" / "env"]:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


@dataclass
class LLMConfig:
    model: str
    base_url: str | None
    api_key: str | None
    price_in: float
    price_out: float
    max_cost: float = DEFAULT_MAX_COST
    output_mode: str = "native"      # response_format; avoids the reasoning + tools error (§6.4)
    reasoning_effort: str | None = "none"

    @property
    def endpoint(self) -> str:
        return self.base_url or "https://api.openai.com/v1"

    @property
    def local(self) -> bool:
        return is_local(self.endpoint)


def is_local(url: str) -> bool:
    host = urlparse(url).hostname or ""
    return host in ("localhost", "127.0.0.1", "::1") or host.endswith(".local")


def llm_config(model: str | None = None, base_url: str | None = None,
               max_cost: float | None = None) -> LLMConfig | None:
    """Settings from arguments, then HUASHUO_LLM_* / OPENAI_API_KEY (and .env files).
    None when no key is configured and the endpoint is not local: run without the LLM."""
    load_dotenv()
    model = model or os.environ.get("HUASHUO_LLM_MODEL") or DEFAULT_MODEL
    base_url = base_url or os.environ.get("HUASHUO_LLM_BASE_URL") or None
    key = os.environ.get("HUASHUO_LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
    local = is_local(base_url or "")
    if not key and not local:
        return None
    if local:
        price = (0.0, 0.0)
    elif model in PRICES:
        price = PRICES[model]
    elif os.environ.get("HUASHUO_LLM_PRICE_IN") and os.environ.get("HUASHUO_LLM_PRICE_OUT"):
        price = (float(os.environ["HUASHUO_LLM_PRICE_IN"]), float(os.environ["HUASHUO_LLM_PRICE_OUT"]))
    else:
        raise LLMError(f"no price known for model {model!r}, so the budget cannot be enforced; set "
                       f"HUASHUO_LLM_PRICE_IN and HUASHUO_LLM_PRICE_OUT (USD per million tokens)")
    return LLMConfig(model, base_url, key or "local", price[0], price[1],
                     max_cost if max_cost is not None else DEFAULT_MAX_COST)


def offline_config(model: str | None) -> LLMConfig:
    """For cache-only runs: only the model name matters (it is part of the cache key)."""
    return LLMConfig(model or os.environ.get("HUASHUO_LLM_MODEL") or DEFAULT_MODEL, None, None, 0.0, 0.0)


def estimate_cost(chars: int, config: LLMConfig) -> float:
    tokens_in, tokens_out = chars * TOKENS_PER_CHAR[0], chars * TOKENS_PER_CHAR[1]
    return (tokens_in * config.price_in + tokens_out * config.price_out) / 1e6


def make_model(config: LLMConfig):
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider

    return OpenAIChatModel(config.model, provider=OpenAIProvider(base_url=config.endpoint, api_key=config.api_key))


# --------------------------------------------------------------------------------------
# Calls: cache, budget, usage
# --------------------------------------------------------------------------------------


@dataclass
class Usage:
    requests: int = 0
    cached: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0
    seconds: float = 0.0


class Caller:
    """Runs LLM calls through the response cache and the budget.

    With `offline=True` only cached answers are used: nothing is sent anywhere, and a
    miss stops the stage (what was answered stays; the rest remains "unknown"). This is
    how a book is re-imported without a key or with --no-llm without losing its
    attribution.
    """

    def __init__(self, config: LLMConfig, cache_dir: Path, model=None, offline: bool = False) -> None:
        self.config = config
        self.cache_dir = cache_dir
        self.offline = offline
        self.model = model if model is not None or offline else make_model(config)
        self.usage = Usage()
        # One event loop for the caller's lifetime: the model's HTTP client binds to the loop
        # it first runs on, so a fresh loop per call (asyncio.run) breaks the second call.
        self._loop: asyncio.AbstractEventLoop | None = None

    def close(self) -> None:
        if self._loop is not None and not self._loop.is_closed():
            self._loop.close()
        self._loop = None

    def _run(self, coroutine):
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
        return self._loop.run_until_complete(coroutine)

    def _key(self, stage: str, instructions: str, prompt: str, output_type) -> str:
        payload = json.dumps({"v": PROMPT_VERSION, "stage": stage, "model": self.config.model,
                              "instructions": instructions, "prompt": prompt,
                              "schema": output_type.model_json_schema()}, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]

    def call(self, stage: str, instructions: str, prompt: str, output_type):
        from pydantic_ai import Agent, NativeOutput, PromptedOutput, ToolOutput
        from pydantic_ai.usage import UsageLimits

        from huashuo.workdir import read_json, write_json_atomic

        key = self._key(stage, instructions, prompt, output_type)
        path = self.cache_dir / f"{key}.json"
        stored = read_json(path)
        if stored is not None:
            self.usage.cached += 1
            return output_type.model_validate(stored["output"])
        if self.offline:
            raise LLMError("no LLM calls allowed and the answer is not cached")

        max_output = MAX_OUTPUT_TOKENS[stage]
        worst = ((len(prompt) + len(instructions) + 500) * self.config.price_in
                 + max_output * 4 * self.config.price_out) / 1e6       # up to 3 validation retries
        if self.usage.cost + worst > self.config.max_cost:
            raise BudgetExceeded(f"the next call could cost up to ${worst:.3f}; ${self.usage.cost:.3f} of the "
                                 f"${self.config.max_cost:.2f} cap is spent (raise it with --max-llm-cost)")

        wrap = {"native": NativeOutput, "tool": ToolOutput, "prompted": PromptedOutput}[self.config.output_mode]
        agent = Agent(self.model, output_type=wrap(output_type), instructions=instructions, retries=3)
        settings: dict = {"max_tokens": max_output}
        if self.config.reasoning_effort and not self.config.local:
            settings["openai_reasoning_effort"] = self.config.reasoning_effort
        started = time.time()
        try:
            result = self._run(agent.run(prompt, model_settings=settings,
                                         usage_limits=UsageLimits(request_limit=4)))
        except Exception as exc:
            raise LLMError(f"{stage} call to {self.config.model} failed: {exc}") from exc
        usage = result.usage() if callable(result.usage) else result.usage
        cost = ((usage.input_tokens or 0) * self.config.price_in + (usage.output_tokens or 0) * self.config.price_out) / 1e6
        self.usage.requests += usage.requests
        self.usage.input_tokens += usage.input_tokens or 0
        self.usage.output_tokens += usage.output_tokens or 0
        self.usage.cost += cost
        self.usage.seconds += time.time() - started
        write_json_atomic(path, {"stage": stage, "model": self.config.model, "output": result.output.model_dump(),
                                 "usage": {"input": usage.input_tokens, "output": usage.output_tokens, "cost": cost}})
        return result.output


# --------------------------------------------------------------------------------------
# Pass 1: cast
# --------------------------------------------------------------------------------------

Age = Literal["child", "teen", "young_adult", "middle_aged", "elderly", "unknown"]


class CastOp(BaseModel):
    op: Literal["insert", "update", "merge"] = Field(
        description="insert: a new speaking character; update: add details to an existing one; "
                    "merge: `name` is another way of referring to the existing `merge_into`")
    name: str = Field(description="fullest name given in the text; for unnamed characters a short role")
    merge_into: str | None = Field(None, description="only for merge: the existing cast name")
    aliases: list[str] = Field(default_factory=list)
    gender: Literal["male", "female", "unknown"] = "unknown"
    age: Age = "unknown"
    description: str = Field("", description="one sentence: role, personality, voice")


class CastUpdate(BaseModel):
    operations: list[CastOp]


CAST_INSTRUCTIONS = {
    "zh": """你在为一部小说制作多角色有声书，负责整理「会说话的角色」名单。

每次你会收到：目前的角色表（JSON）和小说的下一段原文。请只输出对角色表的修改操作：
- insert：原文中出现了一个会说话、角色表里还没有的角色
- update：给已有角色补充别名、性别、年龄或描述
- merge：原文用另一个称呼指代角色表里已有的人（如「苏姑娘」就是「苏晚晴」）。name 写这个称呼，merge_into 写已有的名字

规则：
- 规范名优先用全名；只有称谓时用最具体的称谓（「掌柜」「老者」）。
- 同姓不等于同一人（「萧战」和「萧炎」是两个人），只有原文明确指同一人时才 merge。
- 别名只收指代这个角色本人的称呼，不要收他对别人的称呼（丫鬟称主人「小姐」，「小姐」不是丫鬟的别名）。
- 第一人称叙述者如果说话，作为角色「我」加入。
- 一群不具名、轮流插话的人（「喝酒的人」「众人」「旁人」）作为一个群体角色加入。
- 不要加入地点、物品、只被提到但从不说话的人。
- 性别、年龄只依据原文线索（他/她、公子/姑娘、老者/少年），没有线索就写 unknown。
- 没有需要修改的地方时，返回空的 operations。""",
    "en": """You are preparing a novel as a multi-voice audiobook and maintain the list of speaking characters.

Each time you get the current cast (JSON) and the next passage. Return only operations on the cast:
- insert: a speaking character not yet in the cast
- update: add aliases, gender, age or a description to an existing character
- merge: the passage refers to an existing character by another name ("Mr. Darcy" is "Fitzwilliam Darcy");
  put that name in `name` and the existing one in `merge_into`

Rules:
- Use the fullest name as the canonical name; for unnamed characters the most specific role ("the innkeeper").
- A shared surname does not make two people one; merge only when the text clearly means the same person.
- Aliases are ways of referring to this character, not what this character calls others.
- A first-person narrator who speaks joins as the character "I".
- An unnamed group that speaks in turns ("the drinkers", "the crowd") joins as one group character.
- Do not add places, objects, or people who are mentioned but never speak.
- Gender and age only from clues in the text; otherwise unknown.
- If nothing changes, return an empty list of operations.""",
}


def apply_ops(cast: dict[str, dict], ops: list[CastOp]) -> None:
    def resolve(name: str) -> str | None:
        if name in cast:
            return name
        return next((k for k, v in cast.items() if name in v["aliases"]), None)

    for op in ops:
        name = op.name.strip()
        if not name or name in ("narrator", "unknown"):
            continue
        if op.op == "merge" and op.merge_into and (target := resolve(op.merge_into.strip())):
            entry = cast[target]
            for alias in [name, *op.aliases]:
                if alias != target and alias not in entry["aliases"]:
                    entry["aliases"].append(alias)
            if name in cast and name != target:
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


def _chunks(items: list, size: int, length: Callable) -> list[list]:
    out, current, count = [], [], 0
    for item in items:
        if current and count + length(item) > size:
            out.append(current)
            current, count = [], 0
        current.append(item)
        count += length(item)
    return out + ([current] if current else [])


def build_cast(paragraphs: list[str], language: str, caller: Caller) -> dict[str, dict]:
    instructions = CAST_INSTRUCTIONS.get(language, CAST_INSTRUCTIONS["zh"])
    label = ("目前的角色表：", "原文：") if language == "zh" else ("Current cast:", "Passage:")
    cast: dict[str, dict] = {}
    for chunk in _chunks(paragraphs, CAST_CHUNK_CHARS, len):
        prompt = f"{label[0]}\n{json.dumps(cast, ensure_ascii=False)}\n\n{label[1]}\n" + "\n".join(chunk)
        apply_ops(cast, caller.call("cast", instructions, prompt, CastUpdate).operations)
    return cast


# --------------------------------------------------------------------------------------
# Pass 2: speakers
# --------------------------------------------------------------------------------------

SPEAKER_INSTRUCTIONS = {
    "zh": """你在为一部小说制作多角色有声书，负责判断每一段引号内的话由谁来读。

你会收到角色表，以及一段编号的原文。每行是一个片段：「引」表示引号里的内容，「叙」表示叙述。
请只为「需要回答的编号」各给出一个答案，不要复述原文：
- speaker：说这句话的角色，必须是角色表中的规范名；
  引号里不是有人说出的话（书名、引用的词句、招牌上的字）时写 narrator；
  实在无法判断时写 unknown。
- confidence：0 到 1 之间，表示你的把握。

判断时注意：
- 说话人标签可能在引号之前、之后，或者被省略（两人轮流对话时，按轮次推断）。
- 引号里出现某人的名字，通常说明是在对这个人说话，而不是这个人在说。
- 被叙述打断的一句话，前后两段属于同一个说话人。
- 后文的叙述也可能说明前面是谁在说。""",
    "en": """You are preparing a novel as a multi-voice audiobook and decide who reads each quoted passage.

You get the cast and numbered segments; "Q" marks a quotation, "N" narration.
Answer only for the requested indices, without repeating any text:
- speaker: the canonical cast name of whoever says it;
  "narrator" if the quotation is not something said (a title, a quoted phrase, a sign);
  "unknown" only if it truly cannot be decided.
- confidence: between 0 and 1.

Keep in mind:
- Speech tags may come before, after, or be omitted (infer from turn-taking).
- A name inside the quotation usually addresses that person rather than naming the speaker.
- A sentence interrupted by narration keeps the same speaker.
- Later narration can reveal an earlier speaker.""",
}


def _answers_type(names: list[str]):
    speaker = Literal[tuple(names + ["narrator", "unknown"])]  # type: ignore[misc]
    answer = create_model("Answer", index=(int, ...), speaker=(speaker, ...),
                          confidence=(float, Field(0.5, ge=0, le=1)))
    return create_model("Answers", answers=(list[answer], ...))


def _cast_summary(cast: dict[str, dict], language: str) -> str:
    rows = []
    for name, c in cast.items():
        alias = (f"（又称：{'、'.join(c['aliases'])}）" if language == "zh" else f" (also: {', '.join(c['aliases'])})") \
            if c["aliases"] else ""
        rows.append(f"- {name}{alias}: {c['gender']}, {c['age']}. {c['description']}")
    return "\n".join(rows) or ("（空）" if language == "zh" else "(empty)")


@dataclass
class Segment:
    index: int
    kind: str       # "quote" or "narration"
    text: str


def attribute_segments(segments: list[Segment], cast: dict[str, dict], language: str,
                       caller: Caller) -> dict[int, tuple[str, float]]:
    instructions = SPEAKER_INSTRUCTIONS.get(language, SPEAKER_INSTRUCTIONS["zh"])
    output_type = _answers_type(list(cast))
    zh = language == "zh"
    header = ("角色表：\n" if zh else "Cast:\n") + _cast_summary(cast, language) + "\n\n"
    tag = {"quote": "引" if zh else "Q", "narration": "叙" if zh else "N"}
    answers: dict[int, tuple[str, float]] = {}

    def ask(core: list[Segment], wanted: list[int]) -> None:
        lo = max(0, core[0].index - CONTEXT_SEGMENTS)
        hi = min(len(segments), core[-1].index + 1 + CONTEXT_SEGMENTS)
        body = "\n".join(f"[{s.index}] {tag[s.kind]}：{s.text}" if zh else f"[{s.index}] {tag[s.kind]}: {s.text}"
                         for s in segments[lo:hi])
        ask_line = ("需要回答的编号：" if zh else "Answer for indices: ") + ", ".join(map(str, wanted))
        prompt = header + ("原文（编号片段）：\n" if zh else "Segments:\n") + body + "\n\n" + ask_line
        for a in caller.call("speakers", instructions, prompt, output_type).answers:
            if a.index in wanted:                       # drop answers nobody asked for
                answers[a.index] = (a.speaker, round(float(a.confidence), 2))

    for core in _chunks(segments, ATTR_CHUNK_CHARS, lambda s: len(s.text)):
        wanted = [s.index for s in core if s.kind == "quote"]
        if wanted:
            ask(core, wanted)
    for index in [s.index for s in segments if s.kind == "quote" and s.index not in answers]:
        ask([segments[index]], [index])                 # reconcile, one at a time
    return answers


# --------------------------------------------------------------------------------------
# The whole stage
# --------------------------------------------------------------------------------------


@dataclass
class Attribution:
    blocks: list[dict]
    characters: dict[str, dict]
    usage: Usage
    stopped: str | None = None           # why attribution ended early, if it did
    review: list[dict] = field(default_factory=list)


LOW_CONFIDENCE = 0.6


def attribute_script(script: Script, language: str, caller: Caller) -> Attribution:
    """Fill in `speaker` and `conf` on the script's dialogue blocks and derive the cast.

    Returns new blocks (the input is not changed). Quotes the model calls "narrator" become
    narration. If a call fails or the budget runs out, everything answered so far is kept,
    the rest stays "unknown", and `stopped` says why.
    """
    blocks = [dict(b) for b in script.blocks]
    readable = [i for i, b in enumerate(blocks) if b.get("type") in ("narration", "dialogue")]
    paragraphs: list[str] = []
    last = None
    for i in readable:
        para = paragraph_of(blocks[i]["id"])
        if para == last:
            paragraphs[-1] += blocks[i]["text"]
        else:
            paragraphs.append(blocks[i]["text"])
            last = para
    segments = [Segment(n, "quote" if blocks[i]["type"] == "dialogue" else "narration", blocks[i]["text"])
                for n, i in enumerate(readable)]

    cast: dict[str, dict] = {}
    answers: dict[int, tuple[str, float]] = {}
    stopped = None
    if any(s.kind == "quote" for s in segments):
        try:
            cast = build_cast(paragraphs, language, caller)
            answers = attribute_segments(segments, cast, language, caller)
        except LLMError as exc:
            stopped = str(exc)
        finally:
            caller.close()

    review = []
    for n, i in enumerate(readable):
        block = blocks[i]
        if block["type"] != "dialogue":
            continue
        speaker, conf = answers.get(n, ("unknown", None))
        if speaker == "narrator":
            block["type"] = "narration"
            block.pop("speaker", None)
            continue
        block["speaker"] = speaker
        if conf is not None:
            block["conf"] = conf
        if speaker == "unknown" or (conf is not None and conf < LOW_CONFIDENCE):
            review.append({"id": block["id"], "text": block["text"], "speaker": speaker, "conf": conf})

    lines: dict[str, int] = {}
    for block in blocks:
        if block.get("type") == "dialogue" and block.get("speaker") in cast:
            lines[block["speaker"]] = lines.get(block["speaker"], 0) + 1
    characters = {name: {**entry, "lines": lines.get(name, 0)} for name, entry in cast.items()}
    return Attribution(blocks, characters, caller.usage, stopped, review)
