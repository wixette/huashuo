"""Group 话本 blocks into synthesis units and M4B chapters (docs/script-ir.md §6).

A unit is one TTS call. Consecutive readable blocks with the same voice and emotion are
joined up to `max_chars`, because short text without context comes out rushed; units
never cross a chapter, heading, break, skip, or a change of voice or emotion. Each unit
records what kind of boundary follows it, which is what post-processing turns into a
pause (POST-3).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from huashuo.huaben import Script, spoken_text

DEFAULT_MAX_CHARS = 400

# Boundary kinds, in the order post.py assigns pauses to them.
SENTENCE, PARAGRAPH, HEADING, TITLE, BREAK, CHAPTER_END, END = (
    "sentence", "paragraph", "heading", "title", "break", "chapter_end", "end")


@dataclass
class Unit:
    kind: str                    # "title" (chapter title), "heading" or "body"
    text: str                    # what is sent to the engine
    voice: str
    instruct: str | None
    block_ids: list[str]
    chapter: int                 # index into Plan.chapters
    after: str = PARAGRAPH       # boundary that follows this unit
    pause_override: float | None = None


@dataclass
class Chapter:
    title: str                   # M4B chapter name (volume prefix included)
    block_id: str
    first_unit: int
    level: int = 1


@dataclass
class Plan:
    units: list[Unit] = field(default_factory=list)
    chapters: list[Chapter] = field(default_factory=list)

    @property
    def chars(self) -> int:
        return sum(len(u.text) for u in self.units)


# Sentence ends, with any closing quotes or brackets that belong to the sentence.
_ZH_SENTENCE = re.compile(r'[^。！？…\n]*(?:[。！？…]+[”’」』》）】"\')]*|$)')
_EN_SENTENCE = re.compile(r'[^.!?\n]*(?:[.!?]+["\')\]]*\s*|$)')
_CLAUSE = re.compile(r"[^，、；：,;]*[，、；：,;]?")


def split_long(text: str, max_chars: int, language: str) -> list[str]:
    """Cut an over-long paragraph at sentence ends, then clause marks, then hard."""
    pattern = _ZH_SENTENCE if language == "zh" else _EN_SENTENCE
    pieces: list[str] = []
    for sentence in (s for s in pattern.findall(text) if s.strip()):
        if len(sentence) <= max_chars:
            pieces.append(sentence)
            continue
        for clause in (c for c in _CLAUSE.findall(sentence) if c):
            while len(clause) > max_chars:
                pieces.append(clause[:max_chars])
                clause = clause[max_chars:]
            pieces.append(clause)
    out, current = [], ""
    for piece in pieces:
        if current and len(current) + len(piece) > max_chars:
            out.append(current)
            current = piece
        else:
            current += piece
    if current:
        out.append(current)
    return [p.strip() for p in out if p.strip()]


def voice_of(block: dict, cast: dict) -> tuple[str, str | None]:
    narrator = cast["narrator"]["voice"]
    if block.get("type") != "dialogue":
        return narrator, None
    character = cast.get("characters", {}).get(block.get("speaker"))
    voice = character.get("voice") if character else None
    return voice or narrator, block.get("emotion") or None


def plan(script: Script, cast: dict, language: str, max_chars: int = DEFAULT_MAX_CHARS,
         read_titles: bool = True, voice_override: str | None = None) -> Plan:
    result = Plan()
    units = result.units
    joiner = "" if language == "zh" else " "
    group: list[tuple[dict, str]] = []   # (block, text) waiting to become a body unit
    group_voice: tuple[str, str | None] | None = None
    volume = None

    def close(after: str) -> None:
        if units and units[-1].after in (PARAGRAPH, SENTENCE):
            units[-1].after = after

    def flush() -> None:
        nonlocal group, group_voice
        if not group:
            return
        voice, instruct = group_voice
        text = joiner.join(t for _, t in group)
        ids = [b["id"] for b, _ in group]
        pieces = split_long(text, max_chars, language) if len(text) > max_chars else [text]
        for i, piece in enumerate(pieces):
            units.append(Unit("body", piece, voice, instruct, ids, len(result.chapters) - 1,
                              after=SENTENCE if i < len(pieces) - 1 else PARAGRAPH))
        last = group[-1][0]
        if isinstance(last.get("pause_after"), (int, float)):
            units[-1].pause_override = float(last["pause_after"])
        group, group_voice = [], None

    for block in script.blocks:
        kind = block.get("type")
        if kind == "chapter":
            flush()
            close(CHAPTER_END)
            title = spoken_text(block).strip()
            if block.get("level") == 1:
                volume = title
            name = f"{volume} · {title}" if block.get("level") == 2 and volume else title
            result.chapters.append(Chapter(name, block["id"], len(units), block.get("level", 1)))
            if read_titles:
                voice = voice_override or cast["narrator"]["voice"]
                units.append(Unit("title", title, voice, None, [block["id"]],
                                  len(result.chapters) - 1, after=TITLE))
        elif kind == "heading":
            flush()
            close(HEADING)
            units.append(Unit("heading", spoken_text(block).strip(), voice_override or cast["narrator"]["voice"],
                              None, [block["id"]], len(result.chapters) - 1, after=HEADING))
        elif kind == "break":
            flush()
            close(BREAK)
        elif kind in ("narration", "dialogue"):
            voice = voice_of(block, cast)
            if voice_override and voice[0] == cast["narrator"]["voice"]:
                voice = (voice_override, voice[1])
            text = spoken_text(block).strip()
            if not text:
                continue
            size = sum(len(t) for _, t in group) + len(text)
            if group and (voice != group_voice or size > max_chars):
                flush()
            group.append((block, text))
            group_voice = voice
            if isinstance(block.get("pause_after"), (int, float)):
                flush()
        else:
            flush()  # skip and reserved types: silent, and units do not cross them
    flush()
    if units:
        units[-1].after = END

    _drop_empty_chapters(result)

    # A volume title directly followed by its first chapter is not a chapter of its own:
    # the chapter starts where the volume title is read.
    merged: list[Chapter] = []
    for chapter in result.chapters:
        if merged and chapter.first_unit == _content_start(merged[-1], result.units):
            chapter.first_unit = merged[-1].first_unit
            merged[-1] = chapter
        else:
            merged.append(chapter)
    result.chapters = merged
    for index, chapter in enumerate(result.chapters):
        end = result.chapters[index + 1].first_unit if index + 1 < len(result.chapters) else len(result.units)
        for unit in result.units[chapter.first_unit:end]:
            unit.chapter = index
    return result


def _drop_empty_chapters(result: Plan) -> None:
    """Remove chapters with nothing to read but their own title, such as an opening section
    holding only a skipped copyright page. A volume title followed by its chapters stays;
    the merge in plan() folds it into the first chapter."""
    units, chapters = result.units, result.chapters
    keep_units: list[Unit] = []
    keep_chapters: list[Chapter] = []
    for index, chapter in enumerate(chapters):
        end = chapters[index + 1].first_unit if index + 1 < len(chapters) else len(units)
        own = units[chapter.first_unit:end]
        is_volume = chapter.level == 1 and index + 1 < len(chapters) and chapters[index + 1].level == 2
        if all(u.kind == "title" for u in own) and not is_volume:
            continue
        chapter.first_unit = len(keep_units)
        keep_units.extend(own)
        keep_chapters.append(chapter)
    if keep_units:
        keep_units[-1].after = END
    result.units, result.chapters = keep_units, keep_chapters


def _content_start(chapter: Chapter, units: list[Unit]) -> int:
    """Index just past the chapter's own title unit (where its content would begin)."""
    i = chapter.first_unit
    while i < len(units) and units[i].kind == "title" and units[i].block_ids == [chapter.block_id]:
        i += 1
    return i
