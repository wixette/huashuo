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
from collections import Counter
import json
import math
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
MAX_OUTPUT_TOKENS = {"cast": 3000, "speakers": 2000, "voices": 1500}
DEFAULT_CONCURRENCY = 6              # pass-2 calls in flight at once
MAX_REQUESTS = 4                     # per call: the first and up to 3 validation retries

# USD per million tokens (input, output), design doc §6.3 (2026-09).
PRICES = {"gpt-6-luna": (0.10, 0.50), "gpt-6-sol": (2.00, 10.00), "gpt-6-astra": (10.00, 50.00),
          "gpt-5.6-luna": (0.20, 1.20), "gpt-5.6-sol": (4.00, 20.00), "gpt-5.6-terra": (2.00, 12.00),
          "gpt-5.4-mini": (0.75, 4.50), "gpt-5.4-nano": (0.20, 1.25), "gpt-5-nano": (0.05, 0.40),
          "gpt-4o-mini": (0.15, 0.60)}
# The cost model (estimate_cost), measured on 白夜行 (2026-09: 304,715 characters, 6,605
# quotes, 159 speaking characters; $2.30 for pass 1, $0.035 per pass-2 call). Every call
# carries the cast: pass 1 as JSON (about 125 characters per character), pass 2 as a list
# plus the names in the answer schema (about 80). On a long book that is most of the
# prompt, so a flat rate per character of text (the old 3.6 tokens, from short samples)
# came out at a third of the real cost.
TOKENS_PER_CHAR = {"zh": 0.85, "en": 0.3}    # prose, instructions and the cast list
CAST_JSON_TOKENS_PER_CHAR = 0.43
CAST_JSON_CHARS = 125                        # per character, in pass 1's cast JSON
CAST_LIST_CHARS = 80                         # per character, in each pass-2 prompt and schema
CAST_SCHEMA_CHARS, SPEAKER_SCHEMA_CHARS = 1300, 650
CAST_OUTPUT_TOKENS = 150                     # per pass-1 call
QUOTE_OUTPUT_TOKENS = 25                     # per quote answered in pass 2


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
    concurrency: int = DEFAULT_CONCURRENCY

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
               max_cost: float | None = None, concurrency: int | None = None) -> LLMConfig | None:
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
    concurrency = concurrency or int(os.environ.get("HUASHUO_LLM_CONCURRENCY") or DEFAULT_CONCURRENCY)
    return LLMConfig(model, base_url, key or "local", price[0], price[1],
                     max_cost if max_cost is not None else DEFAULT_MAX_COST, concurrency=max(1, concurrency))


def offline_config(model: str | None) -> LLMConfig:
    """For cache-only runs: only the model name matters (it is part of the cache key)."""
    return LLMConfig(model or os.environ.get("HUASHUO_LLM_MODEL") or DEFAULT_MODEL, None, None, 0.0, 0.0)


def expected_cast_size(quotes: int) -> int:
    """Speaking characters to expect before pass 1 has found them: about 2·√quotes (白夜行:
    6,605 quotes, 159 characters; short stories have fewer, so this errs high for them)."""
    return round(2 * math.sqrt(quotes))


def estimate_cost(script: Script, language: str, config: LLMConfig, cache: Path | None = None) -> float:
    """What attributing the book costs, from the prompts the two passes will send. The cast
    they carry is not known before pass 1, so its size is expected.

    With `cache`, answers already there are not counted: a run the cap stopped costs only
    what is left. Pass 1 is replayed from the cache as far as it goes; once it is complete
    the cast is known and pass 2's remaining calls are priced exactly."""
    paragraphs, segments = split_script(script)
    quotes = sum(s.kind == "quote" for s in segments)
    if not quotes:
        return 0.0
    rate = TOKENS_PER_CHAR.get(language, TOKENS_PER_CHAR["zh"])
    expected = expected_cast_size(quotes)
    cast_chunks = _chunks(paragraphs, CAST_CHUNK_CHARS, len)
    cast: dict[str, dict] = {}
    done = [0]
    if cache is not None and cache.is_dir():
        replay = Caller(config, cache, offline=True)
        try:
            build_cast(paragraphs, language, replay, lambda stage, n, total, usage: done.__setitem__(0, n), cast)
        except LLMError:
            pass
        finally:
            replay.close()
    tokens_in = tokens_out = 0.0
    instructions = len(CAST_INSTRUCTIONS.get(language, CAST_INSTRUCTIONS["zh"]))
    for n in range(done[0], len(cast_chunks)):
        known = max(len(cast), expected * min(1.0, 1.6 * n / len(cast_chunks)))   # characters appear early on
        tokens_in += (instructions + CAST_SCHEMA_CHARS + sum(map(len, cast_chunks[n]))) * rate \
            + known * CAST_JSON_CHARS * CAST_JSON_TOKENS_PER_CHAR
        tokens_out += CAST_OUTPUT_TOKENS
    if done[0] == len(cast_chunks) and cache is not None:
        instructions_, output_type = _speaker_request(cast, language)
        todo = [(prompt, wanted) for prompt, wanted in _speaker_prompts(segments, _cast_summary(cast, language), language)
                if not replay.cached("speakers", instructions_, prompt, output_type)]
        return (tokens_in * config.price_in + tokens_out * config.price_out) / 1e6 \
            + _speaker_cost(todo, instructions_, output_type, language, config)
    instructions = len(SPEAKER_INSTRUCTIONS.get(language, SPEAKER_INSTRUCTIONS["zh"])) \
        + len(EMOTION_INSTRUCTIONS.get(language, EMOTION_INSTRUCTIONS["zh"]))
    for prompt, wanted in _speaker_prompts(segments, "x" * (expected * CAST_LIST_CHARS), language):
        tokens_in += (instructions + SPEAKER_SCHEMA_CHARS + len(prompt)) * rate
        tokens_out += len(wanted) * QUOTE_OUTPUT_TOKENS
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
        self._reserved = 0.0             # worst-case cost of calls in flight
        self._in_flight = 0              # counted, not read off _reserved: float sums leave dust
        self._budget: asyncio.Condition | None = None

    def close(self) -> None:
        if self._loop is not None and not self._loop.is_closed():
            self._loop.close()
        self._loop = None

    def _run(self, coroutine):
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
            self._budget = None
        return self._loop.run_until_complete(coroutine)

    def _key(self, stage: str, instructions: str, prompt: str, output_type) -> str:
        payload = json.dumps({"v": PROMPT_VERSION, "stage": stage, "model": self.config.model,
                              "instructions": instructions, "prompt": prompt,
                              "schema": output_type.model_json_schema()}, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]

    def cached(self, stage: str, instructions: str, prompt: str, output_type) -> bool:
        return (self.cache_dir / f"{self._key(stage, instructions, prompt, output_type)}.json").is_file()

    def call(self, stage: str, instructions: str, prompt: str, output_type):
        return self._run(self.acall(stage, instructions, prompt, output_type))

    def run_many(self, jobs: list[Callable]) -> list:
        """Run coroutine factories with at most `config.concurrency` in flight; returns
        each result or the exception it raised, in the order given."""
        async def everything():
            gate = asyncio.Semaphore(max(1, self.config.concurrency))

            async def one(job):
                async with gate:
                    return await job()
            return await asyncio.gather(*(one(job) for job in jobs), return_exceptions=True)
        return self._run(everything())

    async def acall(self, stage: str, instructions: str, prompt: str, output_type):
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

        # Reserve the worst case before calling, so concurrent calls can never jointly
        # exceed the cap. A call that does not fit waits for calls in flight to settle
        # (their actual cost is usually far below the reservation) and fails only when
        # nothing is in flight and it still does not fit.
        # Worst case: a token per character of everything sent (a Chinese character is less
        # than one), the longest answer, and all of it again on each validation retry.
        max_output = MAX_OUTPUT_TOKENS[stage]
        sent = len(prompt) + len(instructions) + len(json.dumps(output_type.model_json_schema())) + 500
        worst = MAX_REQUESTS * (sent * self.config.price_in + max_output * self.config.price_out) / 1e6
        if self._budget is None:
            self._budget = asyncio.Condition()
        async with self._budget:
            while self.usage.cost + self._reserved + worst > self.config.max_cost:
                if self._in_flight == 0:
                    raise BudgetExceeded(f"the next call could cost up to ${worst:.3f}; ${self.usage.cost:.3f} "
                                         f"of the ${self.config.max_cost:.2f} cap is spent "
                                         f"(raise it with --max-llm-cost)")
                await self._budget.wait()
            self._reserved += worst
            self._in_flight += 1

        wrap = {"native": NativeOutput, "tool": ToolOutput, "prompted": PromptedOutput}[self.config.output_mode]
        agent = Agent(self.model, output_type=wrap(output_type), instructions=instructions, retries=3)
        settings: dict = {"max_tokens": max_output}
        if self.config.reasoning_effort and not self.config.local:
            settings["openai_reasoning_effort"] = self.config.reasoning_effort
        started = time.time()
        result = None
        try:
            result = await agent.run(prompt, model_settings=settings, usage_limits=UsageLimits(request_limit=MAX_REQUESTS))
        except Exception as exc:
            raise LLMError(f"{stage} call to {self.config.model} failed: {exc}") from exc
        finally:
            async with self._budget:
                if result is not None:          # count the cost before releasing the reservation
                    usage = result.usage() if callable(result.usage) else result.usage
                    cost = ((usage.input_tokens or 0) * self.config.price_in
                            + (usage.output_tokens or 0) * self.config.price_out) / 1e6
                    self.usage.requests += usage.requests
                    self.usage.input_tokens += usage.input_tokens or 0
                    self.usage.output_tokens += usage.output_tokens or 0
                    self.usage.cost += cost
                self._reserved -= worst
                self._in_flight -= 1
                if self._in_flight == 0:
                    self._reserved = 0.0
                self._budget.notify_all()
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


Progress = Callable[[str, int, int, Usage], None]    # (stage, done, total, usage so far)


def build_cast(paragraphs: list[str], language: str, caller: Caller,
               progress: Progress | None = None, cast: dict[str, dict] | None = None) -> dict[str, dict]:
    """Sequential by nature: each chunk is read against the cast built so far. `cast` is
    filled in place, so what was built before a failed call is still there."""
    instructions = CAST_INSTRUCTIONS.get(language, CAST_INSTRUCTIONS["zh"])
    label = ("目前的角色表：", "原文：") if language == "zh" else ("Current cast:", "Passage:")
    cast = {} if cast is None else cast
    chunks = _chunks(paragraphs, CAST_CHUNK_CHARS, len)
    for done, chunk in enumerate(chunks, 1):
        prompt = f"{label[0]}\n{json.dumps(cast, ensure_ascii=False)}\n\n{label[1]}\n" + "\n".join(chunk)
        apply_ops(cast, caller.call("cast", instructions, prompt, CastUpdate).operations)
        if progress:
            progress("cast", done, len(chunks), caller.usage)
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


# Emotion hints (SCR-12), asked for in the same call as the speaker. The model picks a
# label; the label's phrase becomes the block's `emotion` and the TTS `instruct`. Only
# emotions the TTS renders audibly are offered: in listening tests (design doc §5.9)
# complex attitudes (sarcasm, doubt, coldness) sounded the same with or without a hint,
# while plain emotions came through. The wording is firm but not extreme: stronger
# wording measured no more timbre drift than milder wording.
EMOTIONS = {
    "zh": {"高兴": "用高兴的语气说", "生气": "用生气的语气说", "悲伤": "用悲伤的语气说", "害怕": "用害怕的语气说",
           "惊讶": "用惊讶的语气说", "低声": "压低声音说", "严厉": "用严厉的语气说"},
    "en": {"happy": "Speak happily", "angry": "Speak angrily", "sad": "Speak sadly", "afraid": "Speak fearfully",
           "surprised": "Speak in a surprised tone", "hushed": "Speak in a lowered voice", "stern": "Speak sternly"},
}
EMOTION_INSTRUCTIONS = {
    "zh": """

另外给出 emotion：这句话的情绪。只在情绪明显、正好是可选的几种之一时填写（叙述写了「怒道」「低声说」「哭着说」之类，
或者话语本身明显带着这种情绪），否则留空字符串。讥讽、疑惑、冷淡之类的复杂语气也留空。大多数对白应当留空；拿不准时留空。""",
    "en": """

Also give emotion: the feeling in the line. Fill it in only when it is obvious and is one of the options (a tag such
as "she snapped" or "he whispered", or unmistakable feeling in the words); otherwise leave it an empty string.
Leave complex attitudes such as sarcasm, doubt or coldness empty too. Most lines should be left empty; when unsure,
leave it empty.""",
}


def _answers_type(names: list[str], emotions: list[str] | None = None):
    speaker = Literal[tuple(names + ["narrator", "unknown"])]  # type: ignore[misc]
    fields: dict = {"index": (int, ...), "speaker": (speaker, ...), "confidence": (float, Field(0.5, ge=0, le=1))}
    if emotions:
        fields["emotion"] = (Literal[tuple([""] + emotions)], "")  # type: ignore[misc]
    answer = create_model("Answer", **fields)
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


Answer = tuple[str, float, str]     # speaker, confidence, emotion label ("" for none)


def _speaker_batches(segments: list[Segment]) -> list[tuple[list[Segment], list[int]]]:
    """Pass 2's chunks that contain quotes, each with the indices of its quotes."""
    batches = [(core, [s.index for s in core if s.kind == "quote"])
               for core in _chunks(segments, ATTR_CHUNK_CHARS, lambda s: len(s.text))]
    return [b for b in batches if b[1]]


def _speaker_prompt(segments: list[Segment], core: list[Segment], wanted: list[int], cast_list: str,
                    language: str) -> str:
    zh = language == "zh"
    tag = {"quote": "引" if zh else "Q", "narration": "叙" if zh else "N"}
    lo = max(0, core[0].index - CONTEXT_SEGMENTS)
    hi = min(len(segments), core[-1].index + 1 + CONTEXT_SEGMENTS)
    body = "\n".join(f"[{s.index}] {tag[s.kind]}：{s.text}" if zh else f"[{s.index}] {tag[s.kind]}: {s.text}"
                     for s in segments[lo:hi])
    ask_line = ("需要回答的编号：" if zh else "Answer for indices: ") + ", ".join(map(str, wanted))
    return (("角色表：\n" if zh else "Cast:\n") + cast_list + "\n\n" + ("原文（编号片段）：\n" if zh else "Segments:\n")
            + body + "\n\n" + ask_line)


def _speaker_request(cast: dict[str, dict], language: str, emotions: bool = True) -> tuple[str, type]:
    """Pass 2's instructions and answer type for this cast."""
    instructions = SPEAKER_INSTRUCTIONS.get(language, SPEAKER_INSTRUCTIONS["zh"])
    if not emotions:
        return instructions, _answers_type(list(cast))
    return (instructions + EMOTION_INSTRUCTIONS.get(language, EMOTION_INSTRUCTIONS["zh"]),
            _answers_type(list(cast), list(EMOTIONS.get(language, EMOTIONS["zh"]))))


def _speaker_cost(todo: list[tuple[str, list[int]]], instructions: str, output_type, language: str,
                  config: LLMConfig) -> float:
    """What these pass-2 calls cost, the cast being known."""
    rate = TOKENS_PER_CHAR.get(language, TOKENS_PER_CHAR["zh"])
    schema = len(json.dumps(output_type.model_json_schema(), ensure_ascii=False))
    return sum((len(instructions) + schema + len(prompt)) * rate * config.price_in
               + len(wanted) * QUOTE_OUTPUT_TOKENS * config.price_out for prompt, wanted in todo) / 1e6


def _speaker_prompts(segments: list[Segment], cast_list: str, language: str) -> list[tuple[str, list[int]]]:
    return [(_speaker_prompt(segments, core, wanted, cast_list, language), wanted)
            for core, wanted in _speaker_batches(segments)]


def attribute_segments(segments: list[Segment], cast: dict[str, dict], language: str,
                       caller: Caller, progress: Progress | None = None,
                       answers: dict[int, Answer] | None = None, emotions: bool = True,
                       fallbacks: list[int] | None = None) -> dict[int, Answer]:
    """Chunks are independent once the cast is known, so they run concurrently. The first
    error (budget, API) is raised after everything else has finished; answers so far stay.

    With `emotions`, each answer also carries an emotion label. The prompt without them is
    kept exactly as before, so answers cached before emotion hints existed stay valid: with
    no calls allowed, a chunk not cached with emotions falls back to the plain answer."""
    plain_instructions, plain_type = _speaker_request(cast, language, emotions=False)
    instructions, output_type = _speaker_request(cast, language, emotions)
    cast_list = _cast_summary(cast, language)
    answers = {} if answers is None else answers        # filled in place: kept if a call fails

    def job(core: list[Segment], wanted: list[int], stage: str, total: int, done: list[int]):
        prompt = _speaker_prompt(segments, core, wanted, cast_list, language)

        async def run():
            try:
                try:
                    result = await caller.acall("speakers", instructions, prompt, output_type)
                except LLMError:
                    if not (caller.offline and emotions):
                        raise
                    result = await caller.acall("speakers", plain_instructions, prompt, plain_type)
                    if fallbacks is not None:
                        fallbacks.append(core[0].index)
                for a in result.answers:
                    if a.index in wanted:               # drop answers nobody asked for
                        answers[a.index] = (a.speaker, round(float(a.confidence), 2), getattr(a, "emotion", ""))
            finally:
                done[0] += 1
                if progress:
                    progress(stage, done[0], total, caller.usage)
        return run

    def run_all(pairs: list[tuple[list[Segment], list[int]]], stage: str) -> None:
        done = [0]
        results = caller.run_many([job(core, wanted, stage, len(pairs), done) for core, wanted in pairs])
        errors = [r for r in results if isinstance(r, BaseException)]
        if errors:
            raise next((e for e in errors if isinstance(e, LLMError)), errors[0])

    batches = _speaker_batches(segments)
    if not caller.offline:
        # The cast is known now, so what this pass costs is too: stop here rather than
        # spend up to the cap and leave most of the book unattributed.
        todo = [(prompt, wanted) for prompt, wanted in _speaker_prompts(segments, cast_list, language)
                if not caller.cached("speakers", instructions, prompt, output_type)]
        cost = _speaker_cost(todo, instructions, output_type, language, caller.config)
        if caller.usage.cost + cost > caller.config.max_cost:
            raise BudgetExceeded(f"assigning the speakers would cost about ${cost:.2f} more ({len(todo)} calls); "
                                 f"${caller.usage.cost:.2f} of the ${caller.config.max_cost:.2f} cap is spent. "
                                 f"Raise the cap with --max-llm-cost: the answers so far are kept, so a new "
                                 f"run pays only for the rest")
    run_all(batches, "speakers")
    missing = [s.index for s in segments if s.kind == "quote" and s.index not in answers]
    if missing:                                          # reconcile, one quote per call
        run_all([([segments[i]], [i]) for i in missing], "retry")
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
    suggested_voices: dict[str, str] = field(default_factory=dict)
    suggestions_failed: str | None = None   # casting then falls back to its rules alone
    without_emotions: int = 0               # chunks answered from answers cached before emotion hints


# --------------------------------------------------------------------------------------
# Casting suggestions for the main characters (CAST-5)
# --------------------------------------------------------------------------------------

VOICE_INSTRUCTIONS = {
    "zh": """你在为一部小说的多角色有声书选角。你会收到主要角色（按台词多少排序，含性别、年龄、描述）和可用的音色（含性别、年龄、特点）。
请为每个主要角色挑选最合适的音色：
- 性别必须一致；年龄尽量接近；
- 角色的性格、身份与音色特点相符（如内省的读书人配温和、书卷气的声音，而不是洪亮、市井气的声音）；
- 不同角色必须用不同的音色；
- 不要选旁白用的音色。""",
    "en": """You are casting a multi-voice audiobook. You get the main characters (by number of lines, with gender,
age and description) and the available voices (with gender, age and traits).
Pick the best voice for each main character:
- the same gender; the closest age;
- personality and role should suit the voice's traits;
- every character gets a different voice;
- never the narrator's voice.""",
}


def suggest_voices(characters: dict[str, dict], voices: list, narrator: str, language: str, caller: Caller,
                   main: int = 8) -> dict[str, str]:
    """One call: the model reads the main characters' profiles against the library's voice
    descriptions. Its picks are suggestions; casting.cast_voices enforces the rules."""
    from huashuo.casting import is_first_person

    names = [n for n in sorted(characters, key=lambda n: (-int(characters[n].get("lines") or 0), n))
             if not is_first_person(n, characters[n])][:main]
    refs = [v.ref for v in voices if v.ref != narrator]
    if not names or not refs:
        return {}
    choice = create_model("Choice", name=(Literal[tuple(names)], ...), voice=(Literal[tuple(refs)], ...))
    output_type = create_model("Choices", choices=(list[choice], ...))
    zh = language == "zh"
    people = "\n".join(f"- {n}：{characters[n].get('gender')}，{characters[n].get('age')}，"
                        f"{characters[n].get('lines', 0)} 句。{characters[n].get('description', '')}" for n in names)
    catalog = "\n".join(f"- {v.ref}：{v.gender}，{v.age}。{v.role}；{'、'.join(v.traits)}。{v.description}"
                         for v in voices if v.ref != narrator)
    prompt = (("主要角色：\n" if zh else "Main characters:\n") + people + "\n\n"
              + ("可用的音色：\n" if zh else "Available voices:\n") + catalog + "\n\n"
              + (f"旁白用的音色：{narrator}" if zh else f"Narrator's voice: {narrator}"))
    result = caller.call("voices", VOICE_INSTRUCTIONS.get(language, VOICE_INSTRUCTIONS["zh"]), prompt, output_type)
    return {c.name: c.voice for c in result.choices}


LOW_CONFIDENCE = 0.6


def split_script(script: Script) -> tuple[list[str], list[Segment]]:
    """The readable text as pass 1 sees it (paragraphs) and as pass 2 does (numbered
    narration and quote segments)."""
    readable = [b for b in script.blocks if b.get("type") in ("narration", "dialogue")]
    paragraphs: list[str] = []
    last = None
    for b in readable:
        para = paragraph_of(b["id"])
        if para == last:
            paragraphs[-1] += b["text"]
        else:
            paragraphs.append(b["text"])
            last = para
    segments = [Segment(n, "quote" if b["type"] == "dialogue" else "narration", b["text"])
                for n, b in enumerate(readable)]
    return paragraphs, segments


def attribute_script(script: Script, language: str, caller: Caller,
                     progress: Progress | None = None, voices: list | None = None,
                     narrator: str | None = None, emotions: bool = True) -> Attribution:
    """Fill in `speaker`, `conf` and `emotion` on the script's dialogue blocks and derive the cast.

    Returns new blocks (the input is not changed). Quotes the model calls "narrator" become
    narration. If a call fails or the budget runs out, everything answered so far is kept,
    the rest stays "unknown", and `stopped` says why.
    """
    blocks = [dict(b) for b in script.blocks]
    readable = [i for i, b in enumerate(blocks) if b.get("type") in ("narration", "dialogue")]
    paragraphs, segments = split_script(script)

    cast: dict[str, dict] = {}
    answers: dict[int, Answer] = {}
    stopped = None
    suggested: dict[str, str] = {}
    suggestions_failed = None
    fallbacks: list[int] = []
    if any(s.kind == "quote" for s in segments):
        try:
            cast = build_cast(paragraphs, language, caller, progress)
            attribute_segments(segments, cast, language, caller, progress, answers, emotions, fallbacks)
        except LLMError as exc:
            stopped = str(exc)
        if voices and narrator and not stopped:
            counts = Counter(answer[0] for answer in answers.values())
            with_lines = {name: {**entry, "lines": counts.get(name, 0)} for name, entry in cast.items()}
            try:
                suggested = suggest_voices(with_lines, voices, narrator, language, caller)
            except LLMError as exc:
                suggestions_failed = str(exc)
        caller.close()

    review = []
    for n, i in enumerate(readable):
        block = blocks[i]
        if block["type"] != "dialogue":
            continue
        speaker, conf, emotion = answers.get(n, ("unknown", None, ""))
        if speaker == "narrator":
            block["type"] = "narration"
            block.pop("speaker", None)
            continue
        block["speaker"] = speaker
        if emotion:
            block["emotion"] = EMOTIONS.get(language, EMOTIONS["zh"])[emotion]
        if conf is not None:
            block["conf"] = conf
        if speaker == "unknown" or (conf is not None and conf < LOW_CONFIDENCE):
            review.append({"id": block["id"], "text": block["text"], "speaker": speaker, "conf": conf})

    lines: dict[str, int] = {}
    for block in blocks:
        if block.get("type") == "dialogue" and block.get("speaker") in cast:
            lines[block["speaker"]] = lines.get(block["speaker"], 0) + 1
    characters = {name: {**entry, "lines": lines.get(name, 0)} for name, entry in cast.items()}
    return Attribution(blocks, characters, caller.usage, stopped, review, suggested, suggestions_failed,
                       len(fallbacks))
