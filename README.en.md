<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/huashuo-logo-white.png">
    <img src="docs/assets/huashuo-logo-black.png" alt="话说 Huashuo" width="128">
  </picture>
</p>

<h1 align="center">话说 Huashuo</h1>

<p align="center">Multi-voice audiobooks, made on your Mac</p>

<p align="center">
  <a href="https://github.com/wixette/huashuo/actions/workflows/tests.yml"><img src="https://github.com/wixette/huashuo/actions/workflows/tests.yml/badge.svg" alt="tests"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-blue" alt="Apache-2.0"></a>
  <a href="https://github.com/wixette/huashuo/releases"><img src="https://img.shields.io/github/v/release/wixette/huashuo?include_prereleases&label=release" alt="release"></a>
</p>

<p align="center"><a href="README.md">简体中文</a> | English</p>

Huashuo turns Chinese novels (TXT / EPUB) into multi-voice audiobooks. An LLM finds who says
each line of dialogue; each character gets a fitting voice for their gender, age and
personality; Qwen3-TTS synthesizes the speech on your Mac; and the result is an M4B audiobook
with chapters and a cover, ready for Apple Books. Speech synthesis always runs locally, and the
LLM can be a cloud service or a model running on your own Mac, for a book made at no cost.
**In this release, multiple voices are for Chinese books; English books are read by the
narrator's voice alone.**

## Listen

Both demos are in Chinese. GitHub's player starts muted, so turn the sound on.

**广告** (The Advertisement, by 半轻人, the whole story, 6 minutes): 11 speaking characters,
each in a different voice.

https://github.com/user-attachments/assets/46a00a20-8378-4aa3-97c8-bdf618f45e63

**一条被洗澡水拍死的鱼** (The Fish Slapped Dead by Bathwater, by 半轻人, a 2-minute excerpt):
the first-person narrator and 栖芒 talking in a bookshop.

https://github.com/user-attachments/assets/b276f93a-4595-4c31-8619-d9aae55b9897

Who says each line and which voice each character gets were decided automatically; in 广告,
two characters' voices were swapped by hand after listening. The full audiobooks can be
downloaded from the [v0.1.0a1 release](https://github.com/wixette/huashuo/releases/tag/v0.1.0a1):
an M4B audiobook (for Apple Books), a single MP3 file, and one MP3 file per chapter. Both stories
are licensed CC BY-NC-ND 4.0 and used with the author's permission.

## What it does

- **Multiple voices**: an LLM attributes every line of dialogue to its speaker, and each
  character gets one of 16 voices designed for Chinese, chosen by gender, age and personality
- **Local synthesis**: Qwen3-TTS runs on Apple Silicon through MLX; the audio never goes
  through any server
- **Free if you like**: the LLM can be a model on your own Mac (LM Studio, Ollama and the
  like), so the whole book is made on your machine; or skip the LLM and have the narrator read
  everything
- **A standard audiobook**: an M4B file with chapters, a cover, and the title and author;
  MP3 output as an option
- **Automatic checks**: speech recognition checks every synthesized passage, and passages with
  missing or wrong words, or read too fast or too slow, are synthesized again; if you hear a
  problem, you can re-synthesize the passage at that time
- **Editable by hand**: a wrongly attributed line, a voice you don't like or a misread word can
  all be fixed, and the fixes survive re-imports
- **Cloud LLM costs under control**: the cost is estimated before any call and a run stops at
  a cap; the LLM's results are saved locally, so running again never pays twice, and an
  interrupted run picks up where it stopped

## Status and limits

A pre-release for pilot users and developers:

- Apple Silicon Macs only (M1 or later, 16 GB of memory or more recommended); install from
  source; a command-line interface only
- Chinese books get multiple voices; English books are read by the narrator's voice alone
- Playback is verified in Apple Books only
- Known issues: the last word of a sentence is occasionally clipped (re-synthesize it with
  `huashuo redo`); Classical Chinese, or prose mixed with it, may be misread, and a regional
  accent occasionally creeps in

Please report problems and ideas as [issues](https://github.com/wixette/huashuo/issues).

## Install

```bash
brew install ffmpeg uv
git clone https://github.com/wixette/huashuo.git && cd huashuo
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e .
source .venv/bin/activate
```

The first synthesis downloads two models from Hugging Face (speech synthesis and speech
recognition, about 5 GB together, kept in `~/.cache/huggingface/`). If Hugging Face is hard to
reach, set the environment variable `HF_ENDPOINT` to use a mirror.

Multiple voices also need an LLM; see [Choosing an LLM, and what it costs](#choosing-an-llm-and-what-it-costs).

## Quick start

```bash
huashuo book.epub --dry-run        # import only, no synthesis: chapters, text not read aloud, expected length
huashuo book.epub --sample         # synthesize about the first two minutes -> book.sample.m4b
huashuo audition book.epub         # one line per character, to hear the chosen voices -> book.audition.m4b
huashuo book.epub                  # the whole book -> book.m4b (stop any time; running again resumes)
huashuo redo book.epub --at 1:28   # something wrong at 1:28? re-synthesize that passage with a new seed
huashuo clean book.epub            # delete the audio cached while synthesizing (the audiobook is kept)
```

With a cloud LLM, before a book's text is first sent out you see the cost estimate and are
asked to agree. On an M1 Max, synthesis takes about half as long as the audiobook plays.

## Common tasks

Each book has a work directory, `book.huashuo/`, next to the book. Edit the files below, run
`huashuo book.epub` again, and only the affected passages are synthesized again.

| To | Do |
|---|---|
| change a character's voice | edit the character's `voice` in `cast.json`; `huashuo voices` lists every voice, `huashuo audition book.epub --library` plays each |
| fix a misread word | add a line to `pron.txt`, e.g. `单于 = chán yú` (pinyin or a homophone) |
| fix a wrongly attributed line | lines the LLM was unsure of are listed in `review.txt`; change the line's `speaker` in `script.huaben.jsonl` |
| change the narrator's voice | `--narrator female`, `--narrator male` or a specific voice; `--narrator auto` for the automatic choice |
| have the narrator read everything | `--single-voice`; `--multi-voice` switches back |
| MP3 output | `--format mp3` (the whole book as one MP3 file) or `--format mp3-chapters` (one MP3 file per chapter) |
| change the title, author or cover | `--title`, `--author`, `--cover` |
| adjust loudness and pauses | `--loudness -16` (loudness), `--pause paragraph=0.8` (0.8 s between paragraphs) |
| skip the title announcement or the closing line | `--no-opening`, `--no-closing` |

These options are remembered for the book, so they need not be repeated. All commands and
options: `huashuo --help` and `huashuo <command> --help`.

## Choosing an LLM, and what it costs

Multiple voices need an LLM to read the book once and find who says each line. Choose one of
the options below; the settings go in a `.env` file (git-ignored) in the directory you run
`huashuo` from.

**A cloud LLM** (the default, best quality): any OpenAI-compatible service; the default model
is `gpt-6-sol`.

```bash
HUASHUO_LLM_API_KEY=your-key
# HUASHUO_LLM_MODEL=…      # optional: another model
# HUASHUO_LLM_BASE_URL=…   # optional: another provider's endpoint
```

Measured costs with `gpt-6-sol`: a short story costs cents; a novel of about 210,000 Chinese
characters with about 100 roles (格非's 春尽江南) about $3; a novel of about 300,000 Chinese
characters with many roles (东野圭吾's 白夜行, about 160 of them) about $10. The cost grows mainly with
the number of roles. `--llm-model gpt-6-luna` costs about a twentieth as much and is as accurate on
ordinary dialogue, but more often wrong on back-and-forth lines with no "he said". The cost is
estimated before any call, and a run stops before spending more than $5 by default
(`--max-llm-cost`). The LLM's results are saved in the work directory as they arrive:
re-imports are free, and a book the cap stopped resumes with a higher cap, paying only for the
rest.

**An LLM on your Mac** (free): run a model with LM Studio, Ollama or similar and point huashuo
at it. No key, no cost, and the book's text never leaves your Mac.

```bash
HUASHUO_LLM_BASE_URL=http://localhost:1234/v1   # LM Studio; Ollama is http://localhost:11434/v1
HUASHUO_LLM_MODEL=…                             # the model loaded in the local server
```

The model must support structured output (JSON Schema), and long books need a large context
window. Note that this pre-release has not been tested with local models yet: models small
enough for a Mac usually understand Chinese fiction less well than large cloud models, so more
lines may be attributed wrongly. Try a short story or `--sample` first.

**No LLM** (free): `--single-voice` has the narrator read the whole book; nothing to set up.

## Development

```bash
uv pip install --python .venv/bin/python -e ".[dev]"
pytest                 # a few seconds: a simulated TTS engine, no model downloads, never a paid API
pytest -m model        # optional: tests with the real local models
```

The design documents under [docs/](docs/) are in Chinese:
[requirements](docs/requirements.md) (start here), [design and measurements](docs/design-and-research.md),
[the script format](docs/script-ir.md). [examples/](examples/) holds the two demo stories, which
are also the reference answers for the automated tests.

## License

The code is licensed under Apache-2.0, by [wixette](https://github.com/wixette). The font,
cover templates, logos and example stories have their own licenses; see [NOTICE](NOTICE).
Built on [Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS), [mlx-audio](https://github.com/Blaizzy/mlx-audio),
[OpenCC](https://github.com/BYVoid/OpenCC) and [ffmpeg](https://ffmpeg.org/).
