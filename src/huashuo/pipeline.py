"""The stages behind the CLI: import, check, plan, synthesize, package.

Each stage reads what the previous one left in the work directory, so any of them can be
re-run on its own (CLI-3) and a crash in one never costs the work of the others.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from huashuo import __version__
from huashuo.cast import default_cast, load_cast, merge_cast
from huashuo.huaben import Script, check, merge, read_script, write_script
from huashuo.ingest import read_book
from huashuo.structure import build
from huashuo.units import DEFAULT_MAX_CHARS, Plan, plan
from huashuo.workdir import Workdir, read_json, write_bytes_atomic, write_json_atomic, write_text_atomic

# Rough planning figures from the M1 Max measurements (design doc §5.5, appendix A).
CHARS_PER_AUDIO_SECOND = {"zh": 4.4, "en": 15.0}
DEFAULT_RTF = 2.7
CACHE_BYTES_PER_SECOND = 24000 * 2            # 16-bit mono WAV at 24 kHz
M4B_BYTES_PER_SECOND = 64000 / 8


class PipelineError(Exception):
    pass


@dataclass
class ImportResult:
    workdir: Workdir
    script: Script
    text: str
    chapters: int
    skipped: int
    encoding: str | None
    merge_report: list[str] = field(default_factory=list)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass
class MachineOutput:
    """Everything the machine derives from the source. M2 adds dialogue splitting and
    speaker attribution here, so the script and the cast are each merged once."""
    text: str
    script: Script
    cast: dict
    chapters: int
    skipped: int


def machine_output(book, language: str | None, read_notes: bool) -> MachineOutput:
    built = build(book, language, read_notes)
    cast = default_cast(built.script.header["language"])
    return MachineOutput(built.text, built.script, cast, built.chapters, built.skipped)


def _place_cover(wd: Workdir, book, cover: Path | None, previous: dict) -> tuple[Path | None, str | None]:
    """The cover to use, and who chose it. A cover the user supplied (--cover, recorded in
    state/ingest.json) is never replaced by the book's own on a later import."""
    existing = wd.find_cover()
    if cover is not None:
        for old in (wd.cover(e) for e in (".jpg", ".jpeg", ".png")):
            old.unlink(missing_ok=True)
        path = wd.cover(cover.suffix.lower() if cover.suffix.lower() in (".jpg", ".jpeg", ".png") else ".jpg")
        write_bytes_atomic(path, cover.read_bytes())
        return path, "user"
    if existing is not None and previous.get("cover_from") == "user":
        return existing, "user"
    if book.cover:
        path = wd.cover(book.cover_ext)
        write_bytes_atomic(path, book.cover)
        return path, "book"
    return existing, previous.get("cover_from")


def import_book(source: Path, wd: Workdir, encoding: str | None = None,
                language: str | None = None, cover: Path | None = None,
                read_notes: bool | None = None) -> ImportResult:
    """Read the source and (re)build text.txt, the script and the cast, keeping user edits.

    Options left as None reuse what the previous import of this book recorded.
    """
    record = read_json(wd.ingest_record) or {}
    previous = record.get("options", {})
    encoding = encoding if encoding is not None else previous.get("encoding")
    language = language if language is not None else previous.get("language")
    read_notes = read_notes if read_notes is not None else previous.get("read_notes", False)
    book = read_book(source, encoding)
    machine = machine_output(book, language, read_notes)
    header = machine.script.header
    header["source"] = {"path": os.path.relpath(source.resolve(), wd.root.resolve()),
                        "format": book.format, "sha256": _sha256_file(source)}
    cover_path, cover_from = _place_cover(wd, book, cover, record)
    if cover_path is not None:
        header["cover"] = cover_path.name

    write_text_atomic(wd.text, machine.text)

    base = read_script(wd.script_base) if wd.script_base.is_file() else None
    current = read_script(wd.script) if wd.script.is_file() else None
    result = merge(base, current, machine.script)
    merged = result.script
    # The text is regenerated on every import, so its fingerprint always follows it.
    merged.header["text_sha256"] = header["text_sha256"]
    write_script(wd.script, merged)
    write_script(wd.script_base, machine.script)
    if result.orphans:
        with (wd.state / "orphaned-edits.jsonl").open("a", encoding="utf-8") as handle:
            for orphan in result.orphans:
                handle.write(json.dumps({"saved": time.strftime("%Y-%m-%d %H:%M:%S"), **orphan},
                                        ensure_ascii=False) + "\n")

    base_cast = read_json(wd.cast_base)
    current_cast = load_cast(wd.cast, header["language"]) if wd.cast.is_file() else None
    cast, cast_report = merge_cast(base_cast, current_cast, machine.cast)
    write_json_atomic(wd.cast, cast)
    write_json_atomic(wd.cast_base, machine.cast)

    write_json_atomic(wd.ingest_record, {"huashuo": __version__, "imported": time.strftime("%Y-%m-%d %H:%M:%S"),
                                         "source": str(source.resolve()), "encoding": book.encoding,
                                         "language": header["language"], "cover_from": cover_from,
                                         "options": {"encoding": encoding, "language": language,
                                                     "read_notes": read_notes}})
    return ImportResult(wd, merged, machine.text, machine.chapters, machine.skipped, book.encoding,
                        result.report + cast_report)


@dataclass
class Project:
    workdir: Workdir
    script: Script
    text: str
    cast: dict

    @property
    def language(self) -> str:
        return self.script.header.get("language", "zh")


def load_project(wd: Workdir) -> Project:
    if not wd.script.is_file():
        raise PipelineError(f"no script in {wd.root}; run `huashuo import` first")
    script = read_script(wd.script)
    return Project(wd, script, wd.text.read_text(encoding="utf-8"),
                   load_cast(wd.cast, script.header.get("language", "zh")))


def check_project(project: Project) -> list:
    return check(project.script, project.text, project.cast)


def make_plan(project: Project, *, read_titles: bool = True, voice: str | None = None,
              max_chars: int = DEFAULT_MAX_CHARS, sample_chars: int | None = None,
              chapters: set[int] | None = None) -> Plan:
    """The units to synthesize; optionally only the first `sample_chars` characters of
    reading, or only some chapters (1-based, as listed by `huashuo import`)."""
    full = plan(project.script, project.cast, project.language, max_chars, read_titles, voice)
    if chapters:
        keep = [i for i, u in enumerate(full.units) if u.chapter + 1 in chapters]
        return _subset(full, keep)
    if sample_chars is not None:
        keep, budget = [], 0
        for i, unit in enumerate(full.units):
            keep.append(i)
            budget += len(unit.text) if unit.kind == "body" else 0
            if budget >= sample_chars:
                break
        return _subset(full, keep)
    return full


def _subset(full: Plan, keep: list[int]) -> Plan:
    from huashuo.units import END, Chapter

    index = {old: new for new, old in enumerate(keep)}
    units = [full.units[i] for i in keep]
    chapters = []
    for number, ch in enumerate(full.chapters):
        firsts = [index[i] for i in keep if full.units[i].chapter == number]
        if firsts:
            chapters.append(Chapter(ch.title, ch.block_id, min(firsts), ch.level))
    if units:
        units[-1].after = END
    return Plan(units=units, chapters=chapters)


@dataclass
class Estimate:
    chars: int
    audio_seconds: float
    synth_seconds: float
    cache_bytes: float
    m4b_bytes: float


def estimate(p: Plan, language: str, rtf: float = DEFAULT_RTF) -> Estimate:
    audio = p.chars / CHARS_PER_AUDIO_SECOND.get(language, 4.4)
    return Estimate(p.chars, audio, audio / rtf, audio * CACHE_BYTES_PER_SECOND, audio * M4B_BYTES_PER_SECOND)
