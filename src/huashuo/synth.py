"""Synthesize every unit into the per-book cache (SYN-3 … SYN-7, SYN-10).

Each unit's audio lives at cache/units/<key>.wav, with a <key>.json sidecar recording how
it was made and how it checked out. The key is a hash of everything that affects the
audio, and the random seed is derived from the key: inserting a paragraph early in the
book does not change the seeds, or the cache hits, of everything after it
(docs/script-ir.md §6). A unit that fails its checks is retried with the next seed and,
if it never passes, kept and reported rather than stopping the book (SYN-6).
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from dataclasses import dataclass, field

import numpy as np

from huashuo.audio import read_wav, write_wav
from huashuo.units import Unit
from huashuo.workdir import Workdir, read_json, write_json_atomic

CACHE_VERSION = 1

# Plausible speaking rates. Chinese counts characters; English counts letters and digits.
# Measured: Chinese narration ~4.4 chars/s, EXP-1 voices 2.4-4.2; far outside means the
# model rushed, stalled or ran away.
_RATE_LIMITS = {"zh": (1.5, 9.0), "en": (5.0, 30.0)}
_COUNTED = {"zh": re.compile(r"[㐀-鿿豈-﫿A-Za-z0-9]"), "en": re.compile(r"[A-Za-z0-9]")}


def unit_key(unit: Unit, engine_identity: dict, language: str) -> str:
    payload = json.dumps({"v": CACHE_VERSION, "engine": engine_identity, "voice": unit.voice,
                          "instruct": unit.instruct, "language": language, "text": unit.text},
                         ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


# Each redo of a unit draws from its own block of seeds, so it never repeats a sample
# that was already tried (automatic retries use the first few seeds of each block).
REROLL_STRIDE = 100


def seed_for(key: str, attempt: int, rerolls: int = 0) -> int:
    return int(key[:8], 16) + REROLL_STRIDE * rerolls + attempt


def duration_problem(text: str, seconds: float, language: str, max_seconds: float | None) -> str | None:
    if seconds < 0.1:
        return "empty audio"
    if max_seconds and seconds >= max_seconds * 0.95:
        return f"hit the length ceiling ({seconds:.0f}s), probably ran away"
    counted = len(_COUNTED.get(language, _COUNTED["zh"]).findall(text))
    if counted < 8:
        return None  # too short for a rate to mean anything
    rate, (low, high) = counted / seconds, _RATE_LIMITS.get(language, _RATE_LIMITS["zh"])
    if rate < low:
        return f"too slow ({rate:.1f}/s): stalled or repeating"
    if rate > high:
        return f"too fast ({rate:.1f}/s): words probably dropped"
    return None


@dataclass
class Stats:
    generated: int = 0
    cached: int = 0
    retried: int = 0
    audio_seconds: float = 0.0
    warnings: list[dict] = field(default_factory=list)


class Progress:
    """One status line with an ETA from characters synthesized so far (SYN-7)."""

    def __init__(self, total_units: int, total_chars: int, enabled: bool, logged: bool = True) -> None:
        self.total_units, self.total_chars, self.enabled = total_units, total_chars, enabled
        # Without a terminal (piped to a log), print a plain line every 10% instead.
        self.logged, self.next_mark = logged and not enabled, 10
        self.done_units = self.done_chars = self.synth_chars = 0
        self.synth_seconds = self.audio_seconds = 0.0
        self.start = time.time()

    def advance(self, chars: int, audio_seconds: float, elapsed: float | None) -> None:
        self.done_units += 1
        self.done_chars += chars
        self.audio_seconds += audio_seconds
        if elapsed is not None:
            self.synth_chars += chars
            self.synth_seconds += elapsed
        self.render()

    def note(self, message: str) -> None:
        if self.enabled:
            sys.stdout.write("\r\033[K")
        print(message)
        self.render()

    def render(self) -> None:
        pct = 100.0 * self.done_chars / max(1, self.total_chars)
        if not self.enabled:
            if self.logged and pct >= self.next_mark:
                self.next_mark = (int(pct) // 10 + 1) * 10
                print(f"  {pct:5.1f}%  {self.done_units}/{self.total_units} units, audio "
                      f"{_hms(self.audio_seconds)}, elapsed {_hms(time.time() - self.start)}", flush=True)
            return
        if self.synth_chars:
            eta = _hms((self.total_chars - self.done_chars) * self.synth_seconds / self.synth_chars)
            rtf = f"{self.audio_seconds / max(self.synth_seconds, 1e-9):.2f}x"
        else:
            eta, rtf = "--:--:--", "--"
        sys.stdout.write(f"\r\033[K[{self.done_units}/{self.total_units}] {pct:5.1f}%  audio "
                         f"{_hms(self.audio_seconds)}  elapsed {_hms(time.time() - self.start)}  "
                         f"eta {eta}  rtf {rtf}")
        sys.stdout.flush()

    def finish(self) -> None:
        if self.enabled:
            sys.stdout.write("\n")


def _hms(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def cached_ok(wd: Workdir, key: str) -> dict | None:
    """The sidecar of a finished unit, or None if the unit still needs synthesizing."""
    meta = read_json(wd.units / f"{key}.json")
    if meta and (wd.units / f"{key}.wav").is_file():
        return meta
    return None


def _rejudge(wd: Workdir, key: str, meta: dict, unit: Unit, language: str, max_cer: float) -> dict:
    """Re-apply the current ASR comparison to a cached unit's stored transcript, so a
    better comparison clears (or raises) flags without synthesizing again."""
    from huashuo.asr import cer

    rate = cer(unit.text, meta["asr"], language)
    duration_only = meta.get("problem") and not str(meta["problem"]).startswith("ASR mismatch")
    problem = meta["problem"] if duration_only else (
        f"ASR mismatch ({rate:.0%}): heard 「{meta['asr']}」" if rate > max_cer else None)
    if problem != meta.get("problem") or round(rate, 4) != meta.get("cer"):
        meta = {**meta, "problem": problem, "cer": round(rate, 4)}
        write_json_atomic(wd.units / f"{key}.json", meta)
    return meta


def synthesize(units: list[Unit], engine, wd: Workdir, language: str, asr=None,
               max_attempts: int = 3, show_progress: bool = True) -> Stats:
    stats = Stats()
    identity = engine.identity()
    keys = [unit_key(u, identity, language) for u in units]
    progress = Progress(len(units), sum(len(u.text) for u in units),
                        enabled=show_progress and sys.stdout.isatty(), logged=show_progress)
    max_seconds = getattr(engine, "max_unit_seconds", None)
    rerolls = read_json(wd.rerolls, {})

    for voice in sorted({u.voice for u in units}):
        engine.check_voice(voice)

    try:
        for unit, key in zip(units, keys):
            meta = cached_ok(wd, key)
            if meta is not None and asr is not None and meta.get("asr") is not None:
                meta = _rejudge(wd, key, meta, unit, language, asr.max_cer)
            if meta is not None:
                stats.cached += 1
                stats.audio_seconds += meta["seconds"]
                progress.advance(len(unit.text), meta["seconds"], None)
                if meta.get("problem"):
                    stats.warnings.append({"key": key, "text": unit.text, "problem": meta["problem"]})
                continue

            best = None  # (score, audio, problem, transcript, error_rate, seed)
            started = time.time()
            for attempt in range(max_attempts):
                seed = seed_for(key, attempt, rerolls.get(key, 0))
                audio = engine.synthesize(unit.text, unit.voice, language, unit.instruct, seed)
                seconds = len(audio) / engine.sample_rate
                problem = duration_problem(unit.text, seconds, language, max_seconds)
                transcript = error_rate = None
                if problem is None and asr is not None:
                    transcript = asr.transcribe(audio, engine.sample_rate, language)
                    from huashuo.asr import cer
                    error_rate = cer(unit.text, transcript, language)
                    if error_rate > asr.max_cer:
                        problem = f"ASR mismatch ({error_rate:.0%}): heard 「{transcript}」"
                score = (problem is not None and error_rate is None, error_rate or 0.0)
                if best is None or score < best[0]:
                    best = (score, audio, problem, transcript, error_rate, seed)
                if problem is None:
                    break
                if attempt + 1 < max_attempts:
                    stats.retried += 1
                    progress.note(f"  ! {unit.text[:24]}…: {problem}; retrying")

            _, audio, problem, transcript, error_rate, seed = best
            elapsed = time.time() - started
            seconds = len(audio) / engine.sample_rate
            write_wav(wd.units / f"{key}.wav", audio if len(audio) else np.zeros(1, np.float32),
                      engine.sample_rate)
            write_json_atomic(wd.units / f"{key}.json", {
                "key": key, "text": unit.text, "voice": unit.voice, "instruct": unit.instruct,
                "seed": seed, "seconds": round(seconds, 3), "elapsed": round(elapsed, 3),
                "problem": problem, "asr": transcript,
                "cer": round(error_rate, 4) if error_rate is not None else None,
                "engine": identity, "blocks": unit.block_ids})
            if problem:
                stats.warnings.append({"key": key, "text": unit.text, "problem": problem})
                progress.note(f"  ! kept despite: {problem} | {unit.text[:30]}…")
            stats.generated += 1
            stats.audio_seconds += seconds
            progress.advance(len(unit.text), seconds, elapsed)
    finally:
        progress.finish()
    return stats


def reroll(wd: Workdir, key: str) -> int:
    """Mark a unit to be synthesized again with fresh seeds; returns how often it has
    been redone. The cached audio is removed so the next synthesis picks it up."""
    rerolls = read_json(wd.rerolls, {})
    rerolls[key] = rerolls.get(key, 0) + 1
    write_json_atomic(wd.rerolls, rerolls)
    for suffix in (".wav", ".json"):
        (wd.units / f"{key}{suffix}").unlink(missing_ok=True)
    return rerolls[key]


def unit_audio(wd: Workdir, key: str) -> tuple[np.ndarray, int]:
    return read_wav(wd.units / f"{key}.wav")
