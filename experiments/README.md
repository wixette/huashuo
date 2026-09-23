# Experiments

Working prototypes and experiments for [话说 Huashuo](../README.md). Run everything from the
repository root with the project venv:

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ../mlx-audio   # mlx-audio @ cd605ec (v0.5.5)
```

| Script | What it is |
|---|---|
| `novel_tts.py` | Single-voice text-to-MP3 prototype (below) |
| `exp1_voice_routes.py` | EXP-1: routes for reusing a VoiceDesign voice (design doc §5.6) |

## exp1_voice_routes.py — EXP-1 voice routes

Designs one voice with VoiceDesign, then renders the same test lines through each route
in §5.6 of the [design doc](../docs/design-and-research.md): Base + ICL (A), Base +
x-vector (B), and the x-vector injected into CustomVoice (C, with and without
`instruct`), plus the nearest preset as a control. Prints duration, speaking rate, RTF and
mean-centered speaker-vector similarity; the WAVs are for listening.

```bash
.venv/bin/python experiments/exp1_voice_routes.py /path/to/out_dir
```

Needs the 1.7B 8-bit Base, VoiceDesign and CustomVoice models from `mlx-community`.

## novel_tts.py — single-voice prototype

The first experiment of [话说 Huashuo](../README.md): convert a long Chinese UTF-8 text
file into a single MP3 with Qwen3-TTS running locally on MLX (Apple Silicon).

It is kept as a working reference, not as the project's CLI. Its chunking, per-chunk
cache, resume and validation logic are what the real pipeline will evolve from (see
§9.1 of the [design doc](../docs/design-and-research.md)).

Built on [mlx-audio](https://github.com/Blaizzy/mlx-audio) @ `cd605ec` (v0.5.5), installed
as an editable dependency from `../mlx-audio` (relative to the repository root).

Run from the repository root.

### Usage

```bash
.venv/bin/python experiments/novel_tts.py book.txt -o book.mp3
```

Audition the opening before committing to a multi-hour run:

```bash
.venv/bin/python experiments/novel_tts.py book.txt --sample
```

Resume an interrupted run (chunks already synthesized are reused):

```bash
.venv/bin/python experiments/novel_tts.py book.txt -o book.mp3 --continue
```

Other useful flags: `--dry-run` (show the chunk plan), `--list-voices`,
`--voice`, `--max-chunk-chars`, `--verbose`.

### How it works

Text is split into chunks of at most `--max-chunk-chars` (default 400), preferring
paragraph boundaries. Each chunk is synthesized separately and cached on disk as a
16-bit WAV; the cache directory name is derived from the text content plus every
parameter that affects the audio, so different books or settings never share a cache.
Chunks are streamed into ffmpeg at the end to produce one MP3 without holding hours of
audio in memory.

Generation is seeded per chunk index, so re-running produces byte-identical audio and a
resumed job cannot mix differently-sounding segments.

### Measured on an M1 Max (0.6B-8bit, serena, Chinese)

- Steady-state RTF ~2.7–3.0x, ~4.4 characters per second of audio
- 100k characters ≈ 6.3 h of audio ≈ 2.1 h of generation
- Chunk cache ≈ 2.9 MB per minute of audio

### Limitations (v1)

- Fixed voice for the whole book; no per-character casting.
- Minimal text normalization: numbers, English words and polyphones are left to the
  model, with some error rate accepted.
- Validation only checks duration against character count (catches empty output,
  truncation at the token ceiling, and runaway repetition). A chunk that fails twice is
  kept and reported at the end rather than blocking the run.
