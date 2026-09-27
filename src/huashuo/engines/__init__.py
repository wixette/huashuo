"""TTS engines behind one small interface (SYN-2).

An engine turns (text, voice, language, instruct, seed) into mono float32 audio. The
script never names an engine; the planner resolves voices from cast.json and the engine
only has to understand voice references: "preset:<name>" (the model's own voices) and
"library:<lang>/<id>" (the built-in library, library.py).
"""

from __future__ import annotations

from typing import Protocol

import numpy as np


class EngineError(Exception):
    pass


class Engine(Protocol):
    name: str
    sample_rate: int

    def identity(self) -> dict:
        """Everything besides text, voice, instruct and language that changes the audio.

        Goes into each unit's cache key, so switching model or sampling settings never
        reuses audio made with other settings.
        """

    def voice_identity(self, voice: str) -> str:
        """The voice as it goes into cache keys (see library_voice_identity)."""

    def check_voice(self, voice: str) -> None:
        """Raise EngineError with a helpful message if the voice cannot be used."""

    def synthesize(self, text: str, voice: str, language: str, instruct: str | None,
                   seed: int) -> np.ndarray: ...

    def close(self) -> None: ...


def library_voice_identity(voice: str) -> str:
    """A library voice includes its fingerprint, so regenerating a library voice never
    reuses audio made with the old one; a preset is its name."""
    kind, _ = parse_voice(voice)
    if kind == "library":
        from huashuo.library import get
        return f"{voice}@{get(voice).fingerprint()}"
    return voice


def library_voice(voice: str):
    """The library entry of a library voice, as an EngineError if there is none."""
    from huashuo.library import LibraryError, get
    try:
        return get(voice)
    except LibraryError as exc:
        raise EngineError(str(exc)) from exc


def parse_voice(voice: str) -> tuple[str, str]:
    kind, sep, name = voice.partition(":")
    if not sep or not name or kind not in ("preset", "library"):
        raise EngineError(f"invalid voice {voice!r}: expected preset:<name> or library:<lang>/<id>")
    return kind, name


def create_engine(name: str, **options) -> Engine:
    if name == "qwen3":
        from huashuo.engines.qwen3 import Qwen3Engine
        return Qwen3Engine(**options)
    if name == "fake":
        from huashuo.engines.fake import FakeEngine
        return FakeEngine(**options)
    raise EngineError(f"unknown engine {name!r} (available: qwen3)")
