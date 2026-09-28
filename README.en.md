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

Huashuo turns Chinese novels (TXT / EPUB) into multi-voice M4B audiobooks on an Apple Silicon
Mac: an LLM finds who says each line, and Qwen3-TTS reads every character in a fitting voice,
locally. **In this release, multiple voices are for Chinese books; English books are read by
the narrator alone.**

## Listen

Both demos are in Chinese; the players start muted, so turn the sound on. **广告** (The Advertisement, by 半轻人, the whole story, 6 min):
11 speakers, 11 voices.

https://github.com/user-attachments/assets/dc5aa87d-1991-4598-ae9d-1d5fcd6bd7da

**一条被洗澡水拍死的鱼** (The Fish Slapped Dead by Bathwater, by 半轻人, a 2-minute excerpt):
the first-person narrator and 栖芒 in a bookshop.

https://github.com/user-attachments/assets/57e8dc6b-6961-46cf-aae2-d4eaf5153524

Speakers and voices were chosen automatically; in 广告 two characters' voices were swapped by
ear. The full audiobooks (M4B for Apple Books, MP3, MP3 per chapter) are attached to
[v0.1.0a1](https://github.com/wixette/huashuo/releases/tag/v0.1.0a1). Both stories are
CC BY-NC-ND 4.0, used with the author's permission.

## What it does

- **Multiple voices**: an LLM attributes every quote to its speaker; each character is cast
  by gender, age and personality from 16 voices designed for Chinese
- **Local synthesis**: Qwen3-TTS on Apple Silicon (MLX); only the text goes to the LLM, the
  audio never leaves your Mac
- **A proper audiobook**: M4B with chapters, cover and metadata; MP3 as an option
- **Checked as it goes**: speech recognition checks every unit and re-synthesizes dropped,
  wrong or rushed ones; heard a glitch, redo that moment
- **Editable**: speakers, voices and pronunciations can be fixed by hand and survive re-imports
- **Bounded cost**: an estimate first, a cap per run, every answer cached, resumable

## Status and limits

A pre-release for pilot users and developers:

- Apple Silicon Macs only (M1 or later, 16 GB of memory or more recommended); install from
  source; command line only
- Chinese books get multiple voices; English books a single narrator voice
- Verified in Apple Books only
- Known issues: the last syllable of a sentence is occasionally clipped (fix it with
  `huashuo redo`); Classical Chinese may be misread, and an accent occasionally drifts in

Please report problems and ideas as [issues](https://github.com/wixette/huashuo/issues).

## Install

```bash
brew install ffmpeg uv
git clone https://github.com/wixette/huashuo.git && cd huashuo
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e .
source .venv/bin/activate
```

The first synthesis downloads two models (speech synthesis and recognition, about 5 GB).

Multiple voices need an LLM: any OpenAI-compatible endpoint, `gpt-6-sol` by default. Put a
`.env` (git-ignored) in the directory you run `huashuo` from:

```bash
HUASHUO_LLM_API_KEY=your-key
# HUASHUO_LLM_MODEL=…      # optional: another model
# HUASHUO_LLM_BASE_URL=…   # optional: another endpoint
```

Without a key, `--single-voice` has the narrator read the whole book.

## Quick start

```bash
huashuo book.epub --dry-run        # import only: chapters, skipped text, expected length
huashuo book.epub --sample         # the first two minutes, to listen -> book.sample.m4b
huashuo audition book.epub         # one line per character, to hear the cast -> book.audition.m4b
huashuo book.epub                  # the whole book -> book.m4b (stop any time; run again to resume)
huashuo redo book.epub --at 1:28   # something wrong at 1:28? re-synthesize that part
huashuo clean book.epub            # done listening: delete the audio cache
```

Before a book is first sent to the LLM, you see the cost estimate and are asked to agree. On an M1 Max, synthesis takes about
half as long as the audio.

## Common tasks

The work directory `book.huashuo/` sits next to the book. Edit these files, run
`huashuo book.epub` again, and only what changed is redone.

| To | Do |
|---|---|
| change a character's voice | edit its `voice` in `cast.json`; `huashuo voices` lists them, `huashuo audition book.epub --library` plays each |
| fix a pronunciation | add `单于 = chán yú` (pinyin or a homophone) to `pron.txt` |
| fix a speaker | quotes to check are listed in `review.txt`; change the quote's `speaker` in `script.huaben.jsonl` |
| change the narrator | `--narrator female`, `male` or a voice; `auto` for the automatic choice |
| narrator only | `--single-voice`; `--multi-voice` switches back |
| MP3 output | `--format mp3` (one file) or `--format mp3-chapters` (a file per chapter) |
| title, author, cover | `--title`, `--author`, `--cover` |
| loudness and pauses | `--loudness -16`, `--pause paragraph=0.8` |
| no title announcement or closing line | `--no-opening`, `--no-closing` |

These options are remembered for the book. All commands and options: `huashuo --help` and
`huashuo <command> --help`.

## LLM cost

Estimated before any call: cents for a short story, about $10 for a 300,000-character novel
with many characters. A run stops before spending more than $5 by default
(`--max-llm-cost`). Answers are cached as they arrive: re-imports are free, and a stopped
book resumes with a higher cap, paying only for the rest.

## Development

```bash
uv pip install --python .venv/bin/python -e ".[dev]"
pytest                 # seconds; a fake TTS engine, no models, never a paid API
pytest -m model        # optional: with the real local models
```

The design documents under [docs/](docs/) are in Chinese:
[requirements](docs/requirements.md) (start here), [design and measurements](docs/design-and-research.md),
[the script format](docs/script-ir.md). [examples/](examples/) holds the two demo stories,
which are also the tests' golden sets.

## License

Code under Apache-2.0, by [wixette](https://github.com/wixette). The font, cover templates,
logos and example stories have their own terms; see [NOTICE](NOTICE). Built on
[Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS), [mlx-audio](https://github.com/Blaizzy/mlx-audio),
[OpenCC](https://github.com/BYVoid/OpenCC) and [ffmpeg](https://ffmpeg.org/).
