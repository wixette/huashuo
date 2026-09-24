"""A deterministic stand-in engine for tests and dry pipeline runs: no model, instant.

Produces a quiet, speech-length tone whose duration follows the text length, so every
later stage (validation, loudness, pauses, chapters, M4B) sees realistic input.
"""

from __future__ import annotations

import zlib

import numpy as np

from huashuo.engines import EngineError, parse_voice

PRESETS = ["serena", "vivian", "uncle_fu", "ryan", "aiden", "dylan", "eric"]


class FakeEngine:
    name = "fake"

    def __init__(self, sample_rate: int = 24000, chars_per_second: float = 4.4,
                 fail_on: str | None = None) -> None:
        self.sample_rate = sample_rate
        self.chars_per_second = chars_per_second
        self.fail_on = fail_on   # texts containing this come back empty (to test retries)
        self.calls = 0

    def identity(self) -> dict:
        return {"engine": self.name, "cps": self.chars_per_second}

    def voice_identity(self, voice: str) -> str:
        kind, _ = parse_voice(voice)
        if kind == "library":
            from huashuo.library import get
            return f"{voice}@{get(voice).fingerprint()}"
        return voice

    def check_voice(self, voice: str) -> None:
        kind, name = parse_voice(voice)
        if kind == "library":
            from huashuo.library import LibraryError, get
            try:
                get(voice)
            except LibraryError as exc:
                raise EngineError(str(exc)) from exc
        elif name not in PRESETS:
            raise EngineError(f"{voice}: not a fake-engine preset")

    def synthesize(self, text: str, voice: str, language: str, instruct: str | None,
                   seed: int) -> np.ndarray:
        self.calls += 1
        if self.fail_on and self.fail_on in text:
            return np.zeros(0, dtype=np.float32)
        rng = np.random.default_rng(seed)
        seconds = max(0.3, len(text) / self.chars_per_second)
        t = np.arange(int(seconds * self.sample_rate)) / self.sample_rate
        pitch = 180 + 40 * (zlib.crc32(voice.encode()) % 5)  # stable across runs
        tone = 0.2 * np.sin(2 * np.pi * pitch * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t))
        pad = np.zeros(int(0.2 * self.sample_rate))  # models leave some silence at the edges
        return np.concatenate([pad, tone + 0.002 * rng.standard_normal(len(t)), pad]).astype(np.float32)

    def close(self) -> None:
        pass
