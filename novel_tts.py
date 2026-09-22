#!/usr/bin/env python3
"""Convert a long Chinese text file into a single MP3 using Qwen3-TTS on MLX.

Pipeline
--------
    read UTF-8 text
      -> normalize (whitespace only; no number/polyphone rewriting)
      -> split into chunks (paragraph boundaries preferred, hard cap on chars)
      -> synthesize chunk by chunk, writing each result to a disk cache
      -> validate each chunk, retry once on a suspicious result
      -> stream all chunks into ffmpeg and encode one MP3

Resumability
------------
Every chunk is stored as a 16-bit WAV under a cache directory whose name is derived
from the input content plus the generation parameters. A run with ``--continue``
reuses whatever is already on disk and only synthesizes the missing chunks, so an
interrupted multi-hour job can be picked up where it stopped. Chunk files are written
atomically (temp file + rename), so a crash can never leave a half-written chunk that
would be mistaken for a finished one.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

import numpy as np

# --------------------------------------------------------------------------------------
# Defaults
# --------------------------------------------------------------------------------------

DEFAULT_MODEL = "mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit"
DEFAULT_VOICE = "serena"
DEFAULT_LANGUAGE = "chinese"
DEFAULT_MAX_CHUNK_CHARS = 400
DEFAULT_TEMPERATURE = 0.9
DEFAULT_BITRATE = "128k"
DEFAULT_SAMPLE_CHARS = 300
DEFAULT_SEED = 0

# The talker emits codec frames at 12.5 Hz and stops at max_tokens, so a chunk can never
# produce more than this many seconds of audio. Hitting the ceiling means the sentence was
# cut off mid-word rather than finished.
CODEC_FRAME_RATE = 12.5
MAX_TOKENS = 4096
MAX_CHUNK_AUDIO_SECONDS = MAX_TOKENS / CODEC_FRAME_RATE

# Measured on this model/voice: natural Chinese narration runs ~4.4 chars per second of
# audio. Anything far outside this band means the model rushed, stalled, or hallucinated.
MIN_CHARS_PER_SECOND = 2.0
MAX_CHARS_PER_SECOND = 8.0

# Release GPU buffers periodically so a multi-hour run does not accumulate memory.
CLEAR_CACHE_EVERY = 20

CACHE_FORMAT_VERSION = 1


# --------------------------------------------------------------------------------------
# Text loading and chunking
# --------------------------------------------------------------------------------------

# Sentence terminators, plus any closing quotes/brackets that belong to the same sentence.
_SENTENCE_RE = re.compile(r'[^。！？…\n]*(?:[。！？…]+[”’」』》）】"\')]*|$)')

# Fallback split points used only when a single sentence exceeds the chunk cap.
_CLAUSE_RE = re.compile(r'[^，、；：,;]*[，、；：,;]?')


def load_text(path: Path) -> list[str]:
    """Read a UTF-8 text file and return its paragraphs.

    Normalization is deliberately minimal (the v1 scope accepts some error rate on
    numbers, English words and polyphones): line endings are unified, each line is
    stripped, and blank lines are dropped. Every surviving line is one paragraph.
    """
    raw = path.read_text(encoding="utf-8")
    raw = raw.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    return [line.strip() for line in raw.split("\n") if line.strip()]


def split_sentences(paragraph: str) -> list[str]:
    """Split a paragraph into sentences, keeping terminating punctuation attached."""
    return [s for s in (m.strip() for m in _SENTENCE_RE.findall(paragraph)) if s]


def split_long_sentence(sentence: str, max_chars: int) -> list[str]:
    """Break an over-long sentence at clause punctuation, then hard-cut if still too long."""
    pieces, current = [], ""
    for clause in (c for c in _CLAUSE_RE.findall(sentence) if c):
        if current and len(current) + len(clause) > max_chars:
            pieces.append(current)
            current = clause
        else:
            current += clause
    if current:
        pieces.append(current)

    out: list[str] = []
    for piece in pieces:
        while len(piece) > max_chars:
            out.append(piece[:max_chars])
            piece = piece[max_chars:]
        if piece:
            out.append(piece)
    return out


def build_chunks(paragraphs: list[str], max_chars: int) -> list[str]:
    """Group paragraphs into synthesis chunks of at most ``max_chars`` characters.

    Chunk boundaries land on paragraph ends whenever possible, because the model decides
    pacing from the text it is given: short, context-free chunks sound rushed, and a
    boundary inside a paragraph is audible. A chunk is closed at a paragraph end once it
    has reached half the cap, which keeps chunk sizes reasonably even without cutting
    paragraphs apart unnecessarily.
    """
    chunks: list[str] = []
    current = ""

    for paragraph in paragraphs:
        for sentence in split_sentences(paragraph):
            for part in (
                split_long_sentence(sentence, max_chars)
                if len(sentence) > max_chars
                else [sentence]
            ):
                if current and len(current) + len(part) > max_chars:
                    chunks.append(current)
                    current = part
                else:
                    current += part
        # Paragraph boundary: a natural place to cut, taken once the chunk is big enough.
        if current and len(current) >= max_chars * 0.5:
            chunks.append(current)
            current = ""

    if current:
        chunks.append(current)
    return chunks


# --------------------------------------------------------------------------------------
# Cache layout
# --------------------------------------------------------------------------------------


@dataclass
class Job:
    """Everything that identifies one synthesis job and where its artifacts live."""

    key: str
    cache_dir: Path
    chunks: list[str]
    params: dict

    @property
    def chunks_dir(self) -> Path:
        return self.cache_dir / "chunks"

    def chunk_path(self, index: int) -> Path:
        return self.chunks_dir / f"{index:06d}.wav"

    @property
    def total_chars(self) -> int:
        return sum(len(c) for c in self.chunks)


def make_job(text_path: Path, chunks: list[str], params: dict, cache_root: Path) -> Job:
    """Derive the cache key from the chunk text plus every parameter that affects audio.

    Because the key covers the text and the parameters, a different book, voice, model or
    chunk size automatically gets its own cache directory, and ``--continue`` can never
    mix audio from two different configurations into one MP3.
    """
    digest = hashlib.sha256()
    digest.update(f"v{CACHE_FORMAT_VERSION}\n".encode())
    for name in ("model", "voice", "language", "max_chunk_chars", "temperature", "seed"):
        digest.update(f"{name}={params[name]}\n".encode())
    for chunk in chunks:
        digest.update(chunk.encode("utf-8"))
        digest.update(b"\x00")
    key = digest.hexdigest()[:16]

    cache_dir = cache_root / f"{text_path.stem}-{key}"
    return Job(key=key, cache_dir=cache_dir, chunks=chunks, params=params)


def write_manifest(job: Job, text_path: Path) -> None:
    """Record the job description next to the cached audio (for inspection and debugging)."""
    manifest = {
        "cache_format_version": CACHE_FORMAT_VERSION,
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "input": str(text_path.resolve()),
        "job_key": job.key,
        "params": job.params,
        "chunk_count": len(job.chunks),
        "total_chars": job.total_chars,
        "chunks": [
            {"index": i, "chars": len(c), "preview": c[:30]}
            for i, c in enumerate(job.chunks)
        ],
    }
    path = job.cache_dir / "manifest.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def scan_jobs(cache_root: Path) -> list[dict]:
    """List every job directory under a cache root, newest first.

    One cache root holds one directory per job, where a job is a unique combination of
    input text and generation parameters. This is what ``--list-jobs`` reports and what
    a failed ``--continue`` uses to show the user which cache they probably meant.
    """
    jobs = []
    if not cache_root.is_dir():
        return jobs
    for directory in sorted(cache_root.iterdir()):
        manifest_path = directory / "manifest.json"
        if not manifest_path.is_file():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        manifest["dir"] = directory
        manifest["chunks_done"] = len(list((directory / "chunks").glob("*.wav")))
        jobs.append(manifest)
    jobs.sort(key=lambda m: m.get("created", ""), reverse=True)
    return jobs


def describe_params(params: dict) -> str:
    """Render the parameters that take part in the cache key, for display."""
    return "  ".join(
        f"{name}={params.get(name)}"
        for name in ("voice", "language", "max_chunk_chars", "temperature", "seed")
    )


def append_result_log(job: Job, record: dict) -> None:
    """Append one chunk result to a JSONL log (append-only, so it survives interruption)."""
    with (job.cache_dir / "results.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


# --------------------------------------------------------------------------------------
# WAV helpers (16-bit mono, written atomically)
# --------------------------------------------------------------------------------------


def float_to_pcm16(audio: np.ndarray) -> bytes:
    """Convert model output in [-1, 1] to little-endian 16-bit PCM."""
    clipped = np.clip(np.asarray(audio, dtype=np.float32), -1.0, 1.0)
    return (clipped * 32767.0).astype("<i2").tobytes()


def write_wav_atomic(path: Path, pcm: bytes, sample_rate: int) -> None:
    """Write a WAV to a temp file and rename it, so readers never see a partial file."""
    tmp = path.with_suffix(".wav.tmp")
    with wave.open(str(tmp), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm)
    os.replace(tmp, path)


def read_wav_frames(path: Path) -> Optional[tuple[bytes, int, int]]:
    """Return (pcm_bytes, frame_count, sample_rate), or None if the file is unusable."""
    try:
        with wave.open(str(path), "rb") as handle:
            frames = handle.getnframes()
            if frames <= 0:
                return None
            return handle.readframes(frames), frames, handle.getframerate()
    except (wave.Error, EOFError, OSError):
        return None


# --------------------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------------------


def check_chunk(text: str, duration: float) -> Optional[str]:
    """Return a short reason string if the audio looks wrong, else None.

    v1 keeps this deliberately cheap: it only looks at duration against character count,
    which catches the three failure modes that actually happen (empty output, truncation
    at the token ceiling, and runaway repetition) without any extra model in the loop.
    """
    if duration < 0.05:
        return "empty audio"
    if duration >= MAX_CHUNK_AUDIO_SECONDS * 0.95:
        return f"hit token ceiling ({duration:.1f}s), likely truncated"
    cps = len(text) / duration
    if cps < MIN_CHARS_PER_SECOND:
        return f"too slow ({cps:.2f} chars/s)"
    if cps > MAX_CHARS_PER_SECOND:
        return f"too fast ({cps:.2f} chars/s)"
    return None


# --------------------------------------------------------------------------------------
# Progress reporting
# --------------------------------------------------------------------------------------


def format_hms(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 3600:02d}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"


class Progress:
    """Single-line progress with an ETA extrapolated from observed chars-per-second.

    Timing is tracked over characters rather than chunks because chunk sizes vary, and
    cached chunks are excluded from the rate so that resuming does not distort the ETA.
    """

    def __init__(self, total_chunks: int, total_chars: int, enabled: bool):
        self.total_chunks = total_chunks
        self.total_chars = total_chars
        self.enabled = enabled
        self.done_chunks = 0
        self.done_chars = 0
        self.audio_seconds = 0.0
        self.synth_chars = 0
        self.synth_seconds = 0.0
        self.start = time.time()

    def advance(self, chars: int, audio_seconds: float, elapsed: Optional[float]) -> None:
        """Record one finished chunk; ``elapsed`` is None when it came from the cache."""
        self.done_chunks += 1
        self.done_chars += chars
        self.audio_seconds += audio_seconds
        if elapsed is not None:
            self.synth_chars += chars
            self.synth_seconds += elapsed
        self.render()

    def note(self, message: str) -> None:
        """Print a message above the progress line without disturbing it."""
        if self.enabled:
            sys.stdout.write("\r\033[K")
        print(message)
        self.render()

    def render(self) -> None:
        if not self.enabled:
            return
        pct = 100.0 * self.done_chars / max(1, self.total_chars)
        if self.synth_chars > 0:
            rate = self.synth_seconds / self.synth_chars
            eta = format_hms((self.total_chars - self.done_chars) * rate)
            rtf = f"{self.audio_seconds / self.synth_seconds:.2f}x" if self.synth_seconds else "--"
        else:
            eta, rtf = "--:--:--", "--"
        sys.stdout.write(
            f"\r\033[K[{self.done_chunks}/{self.total_chunks}] {pct:5.1f}%  "
            f"audio {format_hms(self.audio_seconds)}  "
            f"elapsed {format_hms(time.time() - self.start)}  "
            f"eta {eta}  rtf {rtf}"
        )
        sys.stdout.flush()

    def finish(self) -> None:
        if self.enabled:
            sys.stdout.write("\n")
            sys.stdout.flush()


# --------------------------------------------------------------------------------------
# Synthesis
# --------------------------------------------------------------------------------------


def synthesize(
    job: Job, indices: list[int], progress: Progress, verbose: bool, force: bool
) -> dict:
    """Synthesize the requested chunks, skipping any that are already cached.

    With ``force`` the cache is ignored and every requested chunk is regenerated; the old
    files are overwritten in place rather than deleted first.

    Each chunk is generated, validated, and retried once with a different seed if it looks
    wrong. A chunk that fails twice is kept anyway and reported at the end: the v1 contract
    is to finish the book and tell the user which chunks to re-check, not to stall on one
    bad sentence.
    """
    import mlx.core as mx
    from mlx_audio.tts.utils import load_model

    stats = {"generated": 0, "cached": 0, "retried": 0, "warnings": []}

    # Resolve which chunks still need work before paying for the model load.
    pending = []
    for index in indices:
        cached = None if force else read_wav_frames(job.chunk_path(index))
        if cached is None:
            pending.append(index)
        else:
            stats["cached"] += 1
            job.params["sample_rate"] = cached[2]
            progress.advance(len(job.chunks[index]), cached[1] / cached[2], None)

    if not pending:
        return stats

    model = load_model(job.params["model"])
    sample_rate = model.sample_rate
    job.params["sample_rate"] = sample_rate

    voice = job.params["voice"]
    available = [s.lower() for s in model.get_supported_speakers()]
    if voice.lower() not in available:
        raise SystemExit(
            f"voice '{voice}' is not available in this model.\n"
            f"available voices: {', '.join(model.get_supported_speakers())}"
        )

    for position, index in enumerate(pending):
        text = job.chunks[index]
        audio, duration, elapsed, problem = None, 0.0, 0.0, None

        for attempt in range(2):
            # Seed per chunk so a resumed run reproduces the same audio, and so the retry
            # draws a genuinely different sample instead of repeating the same failure.
            mx.random.seed(job.params["seed"] + index * 1000 + attempt)
            started = time.time()
            pieces = [
                np.asarray(result.audio)
                for result in model.generate_custom_voice(
                    text=text,
                    speaker=voice,
                    language=job.params["language"],
                    temperature=job.params["temperature"],
                    max_tokens=MAX_TOKENS,
                    verbose=False,
                )
            ]
            elapsed = time.time() - started
            audio = np.concatenate(pieces) if pieces else np.zeros(0, dtype=np.float32)
            duration = len(audio) / sample_rate
            problem = check_chunk(text, duration)
            if problem is None:
                break
            if attempt == 0:
                stats["retried"] += 1
                progress.note(f"  ! chunk {index}: {problem} — retrying")

        if problem is not None:
            stats["warnings"].append({"index": index, "reason": problem, "preview": text[:30]})
            progress.note(f"  ! chunk {index}: {problem} — kept anyway ({text[:20]}…)")

        write_wav_atomic(job.chunk_path(index), float_to_pcm16(audio), sample_rate)
        append_result_log(
            job,
            {
                "index": index,
                "chars": len(text),
                "duration": round(duration, 3),
                "elapsed": round(elapsed, 3),
                "chars_per_second": round(len(text) / duration, 3) if duration > 0 else 0,
                "problem": problem,
            },
        )
        stats["generated"] += 1

        if verbose:
            progress.note(
                f"  chunk {index}: {len(text)} chars -> {duration:.2f}s "
                f"in {elapsed:.2f}s (rtf {duration / max(elapsed, 1e-9):.2f}x)"
            )
        progress.advance(len(text), duration, elapsed)

        if (position + 1) % CLEAR_CACHE_EVERY == 0:
            mx.clear_cache()

    return stats


# --------------------------------------------------------------------------------------
# MP3 assembly
# --------------------------------------------------------------------------------------


def encode_mp3(job: Job, indices: list[int], output: Path, bitrate: str) -> None:
    """Stream every cached chunk into ffmpeg and write one MP3.

    The PCM is piped chunk by chunk rather than concatenated in memory: a full-length novel
    can be a dozen hours of audio, which would be several gigabytes as a single array.
    """
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise SystemExit("ffmpeg not found on PATH — required for MP3 encoding")

    first = read_wav_frames(job.chunk_path(indices[0]))
    if first is None:
        raise SystemExit(f"cached chunk {indices[0]} is missing or unreadable")
    job.params["sample_rate"] = first[2]

    tmp_output = output.with_suffix(output.suffix + ".tmp")
    command = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "s16le", "-ar", str(job.params["sample_rate"]), "-ac", "1", "-i", "pipe:0",
        "-codec:a", "libmp3lame", "-b:a", bitrate, "-f", "mp3",
        str(tmp_output),
    ]

    process = subprocess.Popen(command, stdin=subprocess.PIPE)
    try:
        for index in indices:
            frames = read_wav_frames(job.chunk_path(index))
            if frames is None:
                raise SystemExit(f"cached chunk {index} is missing or unreadable")
            process.stdin.write(frames[0])
        process.stdin.close()
    except BrokenPipeError:
        pass

    if process.wait() != 0:
        raise SystemExit(f"ffmpeg failed with exit code {process.returncode}")
    os.replace(tmp_output, output)


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="novel_tts",
        description="Convert a Chinese UTF-8 text file into a single MP3 with Qwen3-TTS.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("input", type=Path, help="input UTF-8 .txt file")
    parser.add_argument("-o", "--output", type=Path, default=None,
                        help="output MP3 path (default: input name with .mp3)")
    parser.add_argument("--voice", default=DEFAULT_VOICE, help="speaker name, fixed for the whole book")
    parser.add_argument("--language", default=DEFAULT_LANGUAGE, help="language passed to the model")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="MLX model repo id or local path")
    parser.add_argument("--max-chunk-chars", type=int, default=DEFAULT_MAX_CHUNK_CHARS,
                        help="upper bound on characters per synthesis chunk")
    parser.add_argument("--temperature", type=float, default=DEFAULT_TEMPERATURE, help="sampling temperature")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="base random seed")
    parser.add_argument("--bitrate", default=DEFAULT_BITRATE, help="MP3 bitrate")
    parser.add_argument("--cache-dir", type=Path, default=None,
                        help="cache root (default: .novel_tts_cache next to the output)")
    parser.add_argument("--continue", dest="resume", action="store_true",
                        help="reuse chunks already in the cache instead of regenerating them")
    parser.add_argument("--sample", type=int, nargs="?", const=DEFAULT_SAMPLE_CHARS, default=None,
                        metavar="CHARS",
                        help="only synthesize roughly the first CHARS characters, "
                             "written to <output>.sample.mp3")
    parser.add_argument("--list-voices", action="store_true", help="print the model's voices and exit")
    parser.add_argument("--list-jobs", action="store_true",
                        help="list the cached jobs under the cache root and exit")
    parser.add_argument("--dry-run", action="store_true", help="show the chunk plan and exit")
    parser.add_argument("--verbose", action="store_true", help="print one line per chunk")
    parser.add_argument("--no-progress", action="store_true", help="disable the live progress line")
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)

    if args.list_voices:
        from mlx_audio.tts.utils import load_model

        model = load_model(args.model)
        print("voices:   ", ", ".join(model.get_supported_speakers()))
        print("languages:", ", ".join(model.get_supported_languages()))
        return 0

    if not args.input.is_file():
        raise SystemExit(f"input file not found: {args.input}")

    # 1. Text -> chunks.
    paragraphs = load_text(args.input)
    chunks = build_chunks(paragraphs, args.max_chunk_chars)
    if not chunks:
        raise SystemExit("input file contains no text")

    output = args.output or args.input.with_suffix(".mp3")
    cache_root = args.cache_dir or output.parent / ".novel_tts_cache"

    params = {
        "model": args.model,
        "voice": args.voice,
        "language": args.language,
        "max_chunk_chars": args.max_chunk_chars,
        "temperature": args.temperature,
        "seed": args.seed,
        "sample_rate": 24000,  # replaced with the model's real rate once it is loaded
    }
    job = make_job(args.input, chunks, params, cache_root)

    if args.list_jobs:
        jobs = scan_jobs(cache_root)
        print(f"cache root: {cache_root}")
        if not jobs:
            print("  (no cached jobs)")
        for manifest in jobs:
            marker = "*" if manifest["dir"] == job.cache_dir else " "
            print(f"{marker} {manifest['dir'].name}  "
                  f"{manifest['chunks_done']}/{manifest.get('chunk_count', '?')} chunks  "
                  f"{manifest.get('created', '?')}")
            print(f"    input:  {manifest.get('input')}")
            print(f"    params: {describe_params(manifest.get('params', {}))}")
        if jobs:
            print("\n(* = the job the current command line maps to)")
        return 0

    # 2. Decide which chunks this run covers. Sample mode takes whole chunks from the
    #    start until the character budget is met, so the cached audio stays valid for a
    #    later full run of the same book.
    if args.sample is not None:
        indices, budget = [], 0
        for index, chunk in enumerate(chunks):
            indices.append(index)
            budget += len(chunk)
            if budget >= args.sample:
                break
        output = output.with_name(f"{output.stem}.sample.mp3")
    else:
        indices = list(range(len(chunks)))

    selected_chars = sum(len(chunks[i]) for i in indices)
    print(f"input:  {args.input}  ({sum(len(p) for p in paragraphs)} chars, {len(paragraphs)} paragraphs)")
    print(f"chunks: {len(indices)} of {len(chunks)}  ({selected_chars} chars, max {args.max_chunk_chars}/chunk)")
    print(f"voice:  {args.voice}   model: {args.model}")
    print(f"cache:  {job.cache_dir}")
    print(f"output: {output}")

    # "--continue" with an empty cache almost always means a parameter was left out or
    # changed, which would silently restart a multi-hour job from zero. Refuse instead,
    # and show which cached jobs exist for this input so the difference is visible.
    if args.resume and not list(job.chunks_dir.glob("*.wav")):
        sys.stdout.flush()  # keep the header above the error when output is piped
        print(f"\nnothing to continue: no cached chunks in {job.cache_dir}", file=sys.stderr)
        siblings = [m for m in scan_jobs(cache_root)
                    if Path(m.get("input", "")) == args.input.resolve() and m["chunks_done"] > 0]
        if siblings:
            print("cached jobs for this input (parameters differ from the current ones):",
                  file=sys.stderr)
            for manifest in siblings:
                print(f"  {manifest['dir'].name}  "
                      f"{manifest['chunks_done']}/{manifest.get('chunk_count', '?')} chunks  "
                      f"{describe_params(manifest.get('params', {}))}", file=sys.stderr)
            print(f"current:    {describe_params(params)}", file=sys.stderr)
            print("\nre-run with the same parameters as the job you want to resume.",
                  file=sys.stderr)
        else:
            print("drop --continue to start this job from the beginning.", file=sys.stderr)
        return 2

    if args.dry_run:
        for index in indices:
            print(f"  [{index:5d}] {len(chunks[index]):4d} chars | {chunks[index][:40]}…")
        return 0

    # 3. Synthesize. Without --continue the cache is ignored and every chunk is
    #    regenerated (existing files are overwritten in place, never deleted).
    job.chunks_dir.mkdir(parents=True, exist_ok=True)
    write_manifest(job, args.input)

    progress = Progress(
        len(indices), selected_chars,
        enabled=not args.no_progress and sys.stdout.isatty(),
    )
    started = time.time()
    try:
        stats = synthesize(job, indices, progress, args.verbose, force=not args.resume)
    except KeyboardInterrupt:
        progress.finish()
        print("\ninterrupted. resume with:")
        print(f"  {sys.argv[0]} {args.input} --continue" + (f" -o {args.output}" if args.output else ""))
        return 130
    progress.finish()

    # 4. Concatenate and encode.
    print(f"encoding {output} …")
    encode_mp3(job, indices, output, args.bitrate)

    total = time.time() - started
    audio = progress.audio_seconds
    print(f"done in {format_hms(total)} — {format_hms(audio)} of audio "
          f"({stats['generated']} generated, {stats['cached']} cached, {stats['retried']} retried)")
    if stats["warnings"]:
        print(f"{len(stats['warnings'])} chunk(s) kept despite failing validation:")
        for warning in stats["warnings"][:20]:
            print(f"  chunk {warning['index']}: {warning['reason']} | {warning['preview']}…")
        if len(stats["warnings"]) > 20:
            print(f"  … and {len(stats['warnings']) - 20} more (see {job.cache_dir / 'results.jsonl'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
