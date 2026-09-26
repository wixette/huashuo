# 话说 Huashuo

话说，这是一个开源的中文有声书生成项目：用 LLM 把小说整理成多角色剧本，再用 Qwen3-TTS 在
Apple Silicon 上本地合成，封装成标准有声书。

An open-source, Chinese-first audiobook generator: an LLM turns a novel into a multi-voice
script, Qwen3-TTS renders it locally on Apple Silicon via MLX, and the result is packaged as
a standard audiobook. "Chinese" means modern vernacular: Traditional-Chinese books are read
from an automatic Simplified conversion (OpenCC; the book's text is unchanged, and the TTS
misreads many Traditional characters otherwise). Classical Chinese, or modern prose mixed
with classical wording, may be misread or drift into a dialect accent; that is a limit of
the TTS model and not yet a quality target.

**Status:** M1–M5 are done: TXT/EPUB in, M4B out; LLM speaker attribution; a voice per
character from a built-in library of 16 designed Chinese voices, with emotion hints;
punctuation clean-up, web-novel noise skipping, a pronunciation dictionary, an opening and
closing announcement. Next: the first-stage acceptance run. In the first stage English
books are read entirely by the narrator's voice; the English voice library comes later.
Install from source for now.

```bash
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e .
.venv/bin/huashuo book.epub --dry-run        # chapters, skipped text, time and size estimate
.venv/bin/huashuo book.epub --sample         # a few minutes, to audition the voice
.venv/bin/huashuo audition book.epub         # one real line per character, in their voices
.venv/bin/huashuo book.epub                  # the whole book -> book.m4b (resumable)
.venv/bin/huashuo redo book.epub --at 1:28   # heard a glitch at 1:28? re-synthesize that part
.venv/bin/huashuo clean book.epub            # done listening? delete the audio cache
```

Needs an Apple Silicon Mac and ffmpeg (`brew install ffmpeg`). The work directory
`book.huashuo/` holds the editable script (`script.huaben.jsonl`, see
[docs/script-ir.md](docs/script-ir.md)) and the cast (`cast.json`: who speaks with which
voice, chosen automatically by gender, age and personality); edits there survive
re-imports. `huashuo voices --library` lists the voices to choose from (characters are cast from the 16
designed library voices; the model's presets only when named in `cast.json`), and
`--no-emotions` reads dialogue without the emotion hints. A name read wrongly? Add
`单于 = chán yú` (or a homophone) to `book.huashuo/pron.txt` and synthesize again; only the
parts with that word are redone. `--title`/`--author` override the metadata, `--loudness`
and `--pause paragraph=0.8` tune the sound, `--no-opening`/`--no-closing` drop the
announcements. The narrator voice is chosen per book (a male or female narrator matching a
first-person narrator or a clear protagonist); `--narrator female|male|<voice>` overrides it.

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
`.env` in the current directory. The cost is estimated first (about $10 for a
300,000-character novel with many characters) and capped by `--max-llm-cost` (default $5
per run); the first time a book is sent to an endpoint you are asked to agree (`--yes` in
scripts). Answers are cached as they arrive, so re-imports are free, and a run the cap
stopped resumes where it stopped: import again with a higher `--max-llm-cost` and only the
rest is paid for. If the text changed since (for example after an upgrade), the previous
answers are kept and you are asked before anything is paid again. Without a
key, or with `--no-llm`, the book is still made, with dialogue read by the narrator. Quotes that need a look are listed in
`book.huashuo/review.txt`.

- [docs/requirements.md](docs/requirements.md): first-stage requirements (novel → M4B),
  milestones, acceptance criteria and the decision log. Start here.
- [docs/design-and-research.md](docs/design-and-research.md): the design doc, covering
  research, measurements, architecture decisions and naming.
- [docs/script-ir.md](docs/script-ir.md): the 话本 (Script IR) format, the cast table and
  the per-book work directory.
- [examples/](examples/): 《一条被洗澡水拍死的鱼》, a short story by 半轻人 (CC BY-NC-ND 4.0),
  as TXT and EPUB: the example input and the golden set for the tests.
- [experiments/](experiments/): the original single-voice prototype and the scripts behind
  EXP-1 and M3 (voice library, emotion hints), EXP-2 (speaker attribution) and EXP-3
  (accent detection).

License: Apache-2.0. The cover font, Noto Serif SC (`src/huashuo/covers/`), is under the SIL
Open Font License (`src/huashuo/covers/OFL.txt`); the cover templates and logos
(`docs/assets/`) were made for the project by its designer.
