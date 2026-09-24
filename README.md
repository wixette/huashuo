# 话说 Huashuo

话说，这是一个开源的中文有声书生成项目：用 LLM 把小说整理成多角色剧本，再用 Qwen3-TTS 在
Apple Silicon 上本地合成，封装成标准有声书。

An open-source, Chinese-first audiobook generator: an LLM turns a novel into a multi-voice
script, Qwen3-TTS renders it locally on Apple Silicon via MLX, and the result is packaged as
a standard audiobook. "Chinese" here means Simplified Chinese in modern vernacular first;
Traditional Chinese and heavily classical texts work but are not yet a quality target.

**Status:** M1 (TXT/EPUB in, M4B out), M2 (dialogue splitting and LLM speaker
attribution) and M3 (per-character voices) are done: Chinese novels get a voice per
character from a built-in library of 16 designed voices, and dialogue carries emotion
hints. Next: M4 (remaining core features: `--title`/`--author`, punctuation cleanup,
configurable loudness and pauses, per-chapter progress), then M5 (pronunciation dictionary,
numbers, web-novel noise, opening and closing). In the first stage English books are read
entirely by the narrator's voice; the English voice library comes later. Install from
source for now.

```bash
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e .
.venv/bin/huashuo book.epub --dry-run        # chapters, skipped text, time and size estimate
.venv/bin/huashuo book.epub --sample         # a few minutes, to audition the voice
.venv/bin/huashuo audition book.epub         # one real line per character, in their voices
.venv/bin/huashuo book.epub                  # the whole book -> book.m4b (resumable)
.venv/bin/huashuo redo book.epub --at 1:28   # heard a glitch at 1:28? re-synthesize that part
```

Needs an Apple Silicon Mac and ffmpeg (`brew install ffmpeg`). The work directory
`book.huashuo/` holds the editable script (`script.huaben.jsonl`, see
[docs/script-ir.md](docs/script-ir.md)) and the cast (`cast.json`: who speaks with which
voice, chosen automatically by gender, age and personality); edits there survive
re-imports. `huashuo voices --library` lists the voices to choose from, and
`--no-emotions` reads dialogue without the emotion hints.

Development:

```bash
uv pip install --python .venv/bin/python -e ".[dev]"
.venv/bin/python -m pytest              # ~2 s, fake TTS engine, no models or API keys needed
.venv/bin/python -m pytest -m model     # opt-in: real Qwen3-TTS / ASR weights (local, offline)
```

Tests never call a paid API or the network: API keys are hidden, `.env` loading is off,
pydantic-ai refuses real requests and outbound connections fail (`tests/conftest.py`). LLM
code is tested with mock models. Accuracy checks against a real LLM are manual scripts with
an explicit `--max-cost` cap (see `experiments/exp2_batch_tagging.py`).

Speaker attribution (with emotion hints and voice suggestions for the main characters)
uses an LLM (default `gpt-6-sol`, any OpenAI-compatible endpoint) set
by `HUASHUO_LLM_MODEL`, `HUASHUO_LLM_API_KEY` and `HUASHUO_LLM_BASE_URL`, or a git-ignored
`.env` in the current directory. The cost is estimated first (about $3 for a
300,000-character novel) and capped by `--max-llm-cost` (default $5); the first time a
book is sent to an endpoint you are asked to agree (`--yes` in scripts). Answers are
cached, so re-imports are free; without a key, or with `--no-llm`, the book is still made,
with dialogue read by the narrator. Quotes that need a look are listed in
`book.huashuo/review.txt`.

- [docs/requirements.md](docs/requirements.md): first-stage requirements (novel → M4B),
  milestones, acceptance criteria and the decision log. Start here.
- [docs/design-and-research.md](docs/design-and-research.md): the design doc, covering
  research, measurements, architecture decisions and naming.
- [docs/script-ir.md](docs/script-ir.md): the 话本 (Script IR) format, the cast table and
  the per-book work directory.
- [experiments/](experiments/): the original single-voice prototype and the scripts behind
  EXP-1 and M3 (voice library, emotion hints), EXP-2 (speaker attribution) and EXP-3
  (accent detection).

License: Apache-2.0.
