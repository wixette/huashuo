# Experiments

**A dated record, not maintained tools.** These scripts produced the measurements behind
design decisions in [话说 Huashuo](../README.md); each is referenced from the design doc
([docs/design-and-research.md](../docs/design-and-research.md)). They are kept as run at the
time and may not work with later versions of the package. Run them from the repository
root with the project venv (`uv pip install --python .venv/bin/python -e ".[dev]"`).

Scripts that call an LLM read `HUASHUO_LLM_API_KEY` (or `OPENAI_API_KEY`) from the
environment or a git-ignored `.env`, and stop before a cost cap (`--max-cost`).

| Script | Date | What it is | Design doc |
|---|---|---|---|
| `novel_tts.py` | before 2026-09-23 | Single-voice text-to-MP3 prototype (below) | §9 |
| `exp1_voice_routes.py` | 2026-09-23 | EXP-1 round 2: routes for reusing a VoiceDesign voice | §5.6 |
| `exp1_candidates.py` | 2026-09-24 | EXP-1 round 3a: reference candidates for the voices in `exp1_voices.json`, with a similarity report | §5.6 |
| `exp1_stability.py` | 2026-09-24 | EXP-1 round 3b: route C stability of the chosen voices, with ASR, identification and pause checks | §5.6 |
| `exp2_batch_tagging.py` | 2026-09-24 | EXP-2: batched speaker attribution, scored on `exp2_data/` (LLM, `--max-cost`) | §7.2.1 |
| `exp3_tone_check.py` | 2026-09-24 | EXP-3 attempt 1: tone-consistency accent detector (negative result) | §5.8 |
| `m2_eval.py` | 2026-09-24 | M2: the package's speaker attribution on the EXP-2 benchmarks (LLM, `--max-cost`) | §7.2.1 |
| `m3_build_library.py` | 2026-09-25 | M3: writes the chosen voices (`m3_voices.json`) into `src/huashuo/voices/zh/` | §5.6 |
| `m3_emotion_check.py` | 2026-09-25 | M3: voice stability under each emotion instruct | §5.9 |
| `m5_reading_check.py` | 2026-09-25 | M5: how Qwen3-TTS reads numbers and pinyin, via ASR | §10.3 |
| `narrator_check.py` | 2026-09-25 | Narrator candidates (`narrator_voices*.json`): pace, pitch, consistency | §5.11 |
| `pace_check.py` | 2026-09-25 | Narration pace: how steady it is and what steadies it (temperature, unit length) | §5.11 |
| `pace_normalize.py` | 2026-09-25 | Prototype: steady the pace after synthesis (not adopted) | §5.11 |
| `title_tone_check.py` | 2026-09-26 | Tone of number-only chapter titles (「1」 read yì) | §5.13 |

## EXP-1 rounds 3a and 3b

```bash
# 3a: VoiceDesign candidates (3 seeds per voice) for every voice in exp1_voices.json,
#     plus the Chinese presets reading the same probe text, and a similarity report
.venv/bin/python experiments/exp1_candidates.py OUT_3A
# 3b: the chosen seeds (chosen_seed in exp1_voices.json) injected into CustomVoice;
#     20 clips per voice, ASR / identification / pause checks, one listening file per voice
.venv/bin/python experiments/exp1_stability.py OUT_3A OUT_3B
```

## exp3_tone_check.py — EXP-3 attempt 1 (negative result)

Per-unit Mandarin tone profiles from forced alignment, pypinyin tones and a YIN pitch
track, scored against a reference profile. It did not separate accented from standard
speech; see design doc §5.8 for why.

```bash
.venv/bin/python experiments/exp3_tone_check.py OUT_DIR SAMPLES.json
```

## exp2_batch_tagging.py — EXP-2 batched speaker attribution

Pass 1 builds the cast from ~2,500-character chunks with explicit insert/update/merge
operations; pass 2 asks, per chunk of numbered segments, only `index -> speaker` for the
quotes, with the speaker constrained to the cast names plus `narrator` and `unknown`;
unanswered indices are asked again. Scored against a benchmark with hand-checked speakers:

| Benchmark | Quotes | What it tests |
|---|---|---|
| `exp2_data/sample40.json` | 12 | The design doc §3.2 sample: forward reference, a name inside the quote, trailing tags, interrupted speech |
| `exp2_data/kongyiji.json` | 38 | Lu Xun's 孔乙己 (traditional characters): untagged back-and-forth, an anonymous crowd, quoted phrases that are not speech, the first-person narrator speaking |

The model comes from `HUASHUO_LLM_MODEL`, `HUASHUO_LLM_API_KEY` (or `OPENAI_API_KEY`) and
`HUASHUO_LLM_BASE_URL`, read from the environment or a `.env` file in the repository root
(git-ignored). Any OpenAI-compatible endpoint works.

```bash
.venv/bin/python experiments/exp2_batch_tagging.py experiments/exp2_data/kongyiji.json \
    --model gpt-6-sol --max-cost 0.20 --out result.json   # built-in prices; stops before the cap
# per-line baseline with the same model and prompts:
.venv/bin/python experiments/exp2_batch_tagging.py experiments/exp2_data/kongyiji.json \
    --attr-chunk-chars 1 --context 8
```

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

It is kept as a reference, not as the project's CLI. Its chunking, per-chunk cache, resume
and validation logic have since been carried into the `huashuo` package (§9 of the
[design doc](../docs/design-and-research.md)), which also changed seeding from chunk index
to cache key.

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
