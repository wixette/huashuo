"""The built-in voice library (CAST-1 … CAST-3, design doc §5.6).

Every library voice was designed once with Qwen3-TTS VoiceDesign from a text description,
chosen by ear from several seeds, and fixed as a speaker vector (from the Base model's
speaker encoder). At synthesis time the vector is injected into CustomVoice (route C), so
a library voice sounds the same in every book. Each voice ships as two small files:

    voices/<language>/<id>.json   description, how it was made, tags used for casting
    voices/<language>/<id>.npy    the 2048-value speaker vector (float32)

Presets of the CustomVoice model are described in voices/presets.json with the same tags,
so casting can treat both alike.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import numpy as np

ROOT = Path(__file__).with_name("voices")
AGES = ("child", "teen", "young_adult", "middle_aged", "elderly")


class LibraryError(Exception):
    pass


@dataclass(frozen=True)
class Voice:
    ref: str                     # "library:zh/v5_young_man" or "preset:serena"
    language: str
    gender: str                  # male / female
    age: str                     # one of AGES
    description: str = ""
    role: str = ""
    traits: tuple[str, ...] = field(default=())
    vector_path: Path | None = None
    default_narrator: bool = False   # the narrator for new books in this language

    @property
    def narrator_only(self) -> bool:
        """A library voice designed for narration: never given to a character."""
        return self.kind == "library" and "narrator" in self.traits

    @property
    def kind(self) -> str:
        return self.ref.split(":", 1)[0]

    def vector(self) -> np.ndarray:
        if self.vector_path is None:
            raise LibraryError(f"{self.ref} has no speaker vector (it is a preset)")
        return np.load(self.vector_path).astype(np.float32).reshape(-1)

    def fingerprint(self) -> str:
        """Changes whenever the voice itself changes; part of every unit's cache key."""
        if self.vector_path is None:
            return self.ref
        stat = self.vector_path.stat()
        return _fingerprint(self.vector_path, stat.st_size, stat.st_mtime_ns)


@lru_cache(maxsize=None)
def _fingerprint(path: Path, size: int, mtime_ns: int) -> str:
    """Hash of the vector file, computed once per version of the file (size and mtime
    are part of the cache key, so an edited file is hashed again)."""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


@lru_cache(maxsize=None)
def _load(root: Path) -> dict[str, Voice]:
    voices: dict[str, Voice] = {}
    presets = root / "presets.json"
    if presets.is_file():
        for name, info in json.loads(presets.read_text(encoding="utf-8")).items():
            ref = f"preset:{name}"
            voices[ref] = Voice(ref, info["language"], info["gender"], info["age"], info.get("description", ""),
                                info.get("role", ""), tuple(info.get("traits", ())))
    for meta_path in sorted(root.glob("*/*.json")):
        info = json.loads(meta_path.read_text(encoding="utf-8"))
        ref = f"library:{meta_path.parent.name}/{meta_path.stem}"
        vector = meta_path.with_suffix(".npy")
        if not vector.is_file():
            raise LibraryError(f"{meta_path}: missing {vector.name}")
        voices[ref] = Voice(ref, meta_path.parent.name, info["gender"], info["age"], info.get("description", ""),
                            info.get("role", ""), tuple(info.get("traits", ())), vector,
                            bool(info.get("default_narrator")))
    return voices


def all_voices(root: Path | None = None) -> dict[str, Voice]:
    return _load(root or ROOT)


def get(ref: str, root: Path | None = None) -> Voice:
    voices = _load(root or ROOT)
    if ref not in voices:
        kind, _, name = ref.partition(":")
        known = ", ".join(sorted(v for v in voices if v.startswith(kind + ":"))) or "none"
        raise LibraryError(f"unknown voice {ref!r}; known {kind} voices: {known}")
    return voices[ref]


def has_library(language: str, root: Path | None = None) -> bool:
    """Whether the library has designed voices for a language. Without them there is no
    automatic casting: presets alone are too few (English has two, one of them the narrator)."""
    return any(v.kind == "library" and v.language == language for v in _load(root or ROOT).values())


def castable(language: str, root: Path | None = None) -> list[Voice]:
    """Voices casting may choose from for a language: the library's character voices.

    Presets are left out; they are used only when named in cast.json. The library voices
    were designed and checked for accent; the presets were not, and serena drifts into a
    Shaanxi-like dialect: in a blind test 3 of 12 takes of her lines did (with a
    「用标准普通话说」 instruct 1 of 12, plus 3 with odd sounds added), a library voice 0 of
    12 (design doc §5.8, requirements Q21). Narrator voices are left out too: a character
    should not sound like the narration."""
    return [v for v in _load(root or ROOT).values()
            if v.language == language and v.kind == "library" and not v.narrator_only]


def presets(language: str, root: Path | None = None) -> list[Voice]:
    """The model's preset voices for a language, for choosing by hand."""
    return [v for v in _load(root or ROOT).values() if v.language == language and v.kind == "preset"]


def narrators(language: str, root: Path | None = None) -> list[Voice]:
    """Library voices designed for narration."""
    return [v for v in _load(root or ROOT).values() if v.language == language and v.narrator_only]


FALLBACK_NARRATOR = {"zh": "preset:serena", "en": "preset:ryan"}


def default_narrator(language: str, root: Path | None = None) -> str:
    """The narrator for a new book (CAST-4): the library's default narrator for the
    language, else a preset."""
    marked = [v.ref for v in _load(root or ROOT).values() if v.language == language and v.default_narrator]
    return marked[0] if marked else FALLBACK_NARRATOR.get(language, FALLBACK_NARRATOR["zh"])
