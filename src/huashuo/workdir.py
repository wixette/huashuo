"""The per-book work directory (docs/script-ir.md §2) and atomic file writes."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


def write_bytes_atomic(path: Path, data: bytes) -> None:
    """Write to a temp file and rename, so a crash never leaves a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def write_text_atomic(path: Path, text: str) -> None:
    write_bytes_atomic(path, text.encode("utf-8"))


def write_json_atomic(path: Path, data) -> None:
    write_text_atomic(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def read_json(path: Path, default=None):
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass
class Workdir:
    root: Path

    @classmethod
    def for_input(cls, source: Path, override: Path | None = None) -> "Workdir":
        """`<input stem>.huashuo/` next to the input unless a directory is given.

        The stem is used as is: Chinese titles, spaces and punctuation all stay (IN-6).
        """
        return cls(override if override is not None else source.with_name(source.stem + ".huashuo"))

    @property
    def text(self) -> Path:
        return self.root / "text.txt"

    @property
    def script(self) -> Path:
        return self.root / "script.huaben.jsonl"

    @property
    def cast(self) -> Path:
        return self.root / "cast.json"

    @property
    def state(self) -> Path:
        return self.root / "state"

    @property
    def script_base(self) -> Path:
        return self.state / "script.auto.jsonl"

    @property
    def cast_base(self) -> Path:
        return self.state / "cast.auto.json"

    @property
    def ingest_record(self) -> Path:
        return self.state / "ingest.json"

    @property
    def units(self) -> Path:
        return self.root / "cache" / "units"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    def cover(self, ext: str) -> Path:
        return self.root / f"cover{ext}"

    def find_cover(self) -> Path | None:
        for ext in (".jpg", ".jpeg", ".png"):
            if self.cover(ext).is_file():
                return self.cover(ext)
        return None
