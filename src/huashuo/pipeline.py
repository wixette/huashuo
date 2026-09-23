"""The stages behind the CLI: import, check, plan, synthesize, package.

Each stage reads what the previous one left in the work directory, so any of them can be
re-run on its own (CLI-3) and a crash in one never costs the work of the others.
"""

from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from huashuo import __version__
from huashuo.engines import DEFAULT_NARRATOR
from huashuo.huaben import Script, check, merge, merge_fields, read_script, write_script
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


def import_book(source: Path, wd: Workdir, encoding: str | None = None,
                language: str | None = None, cover: Path | None = None) -> ImportResult:
    """Read the source and (re)build text.txt, the script and the cast, keeping user edits."""
    book = read_book(source, encoding)
    built = build(book, language)
    header = built.script.header
    header["source"] = {"path": os.path.relpath(source.resolve(), wd.root.resolve()),
                        "format": book.format, "sha256": _sha256_file(source)}

    cover_path = None
    if cover is not None:
        cover_path = wd.cover(cover.suffix.lower() or ".jpg")
        write_bytes_atomic(cover_path, cover.read_bytes())
    elif book.cover:
        cover_path = wd.cover(book.cover_ext)
        write_bytes_atomic(cover_path, book.cover)
    if cover_path is not None:
        header["cover"] = cover_path.name

    write_text_atomic(wd.text, built.text)

    base = read_script(wd.script_base) if wd.script_base.is_file() else None
    current = read_script(wd.script) if wd.script.is_file() else None
    merged, report = merge(base, current, built.script)
    # The text is regenerated on every import, so its fingerprint always follows it.
    merged.header["text_sha256"] = header["text_sha256"]
    write_script(wd.script, merged)
    write_script(wd.script_base, built.script)

    new_cast = {"version": 1, "narrator": {"voice": DEFAULT_NARRATOR[header["language"]]},
                "characters": {}}
    base_cast, cur_cast = read_json(wd.cast_base), read_json(wd.cast)
    if cur_cast is None:
        cast = new_cast
    elif base_cast is None:
        cast = cur_cast
    else:
        cast = merge_fields(base_cast, cur_cast, new_cast)
        cast["narrator"] = merge_fields(base_cast.get("narrator", {}), cur_cast.get("narrator", {}),
                                        new_cast["narrator"])
    write_json_atomic(wd.cast, cast)
    write_json_atomic(wd.cast_base, new_cast)

    write_json_atomic(wd.ingest_record, {"huashuo": __version__, "imported": time.strftime("%Y-%m-%d %H:%M:%S"),
                                         "source": str(source.resolve()), "encoding": book.encoding,
                                         "language": header["language"], "options": {"encoding": encoding,
                                                                                     "language": language}})
    return ImportResult(wd, merged, built.text, built.chapters, built.skipped, book.encoding, report)


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
    return Project(wd, read_script(wd.script), wd.text.read_text(encoding="utf-8"),
                   read_json(wd.cast) or {"version": 1, "narrator": {"voice": DEFAULT_NARRATOR["zh"]},
                                          "characters": {}})


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
