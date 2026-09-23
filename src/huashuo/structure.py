"""From a Book to text.txt plus 话本 blocks: chapters, headings, breaks and skips.

Every paragraph that survives reading becomes exactly one line of text.txt and exactly
one block whose `src` points at that line, so invariants I4 and I5 (docs/script-ir.md §5)
hold by construction. Chapter titles that exist only in an EPUB table of contents are
written into text.txt as their own line for the same reason.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from huashuo.huaben import FORMAT_VERSION, Script, sha256_text
from huashuo.ingest import Book, Paragraph, detect_language

_ZH_NUM = r"[0-9０-９零〇一二三四五六七八九十百千万两]+"
# 第X章 / 第X回 …; the character after the heading word must not continue a sentence,
# which rules out prose such as 「第一章的内容很长」 while keeping web-novel titles written
# without a separator, such as 「第一章陨落的天才」.
_ZH_CHAPTER = re.compile(rf"^第\s*{_ZH_NUM}\s*[章回节節集话話](?![的了是在都也就还又和与而把被让给对，。、])")
_ZH_VOLUME = re.compile(rf"^第\s*{_ZH_NUM}\s*[卷部](?![的了是在都也就还又和与而把被让给对，。、])")
_ZH_SPECIAL = re.compile(r"^(楔子|序章|序言|序幕|引子|前言|尾声|尾聲|终章|終章|后记|後記|番外篇?)(?:$|[\s：:·\-—　])")
_EN_CHAPTER = re.compile(
    r"^(chapter|CHAPTER|Chapter)\s+([0-9]+|[IVXLCDM]+|[A-Za-z]+(-[A-Za-z]+)?)\b"
    r"|^(Prologue|Epilogue|PROLOGUE|EPILOGUE)\b")
_EN_VOLUME = re.compile(r"^(Part|PART|Book|BOOK|Volume|VOLUME)\s+([0-9]+|[IVXLCDM]+|[A-Za-z]+)\b")
_MAX_TITLE_CHARS = {"zh": 40, "en": 80}

# Scene separators: a line made only of three or more decoration characters.
_BREAK = re.compile(r"^[\s*＊\-—_~～·•・◇◆○●☆★#＃=＝…]+$")


def classify_heading(line: str, language: str) -> int | None:
    """Return 1 for a volume, 2 for a chapter heading line, None for ordinary text."""
    if len(line) > _MAX_TITLE_CHARS[language] or line.endswith(("。", "！", "？", ".", "!", "?", "”", "\"")):
        return None
    if language == "zh":
        if _ZH_VOLUME.match(line):
            return 1
        if _ZH_CHAPTER.match(line) or _ZH_SPECIAL.match(line):
            return 2
        return None
    if _EN_VOLUME.match(line):
        return 1
    if _EN_CHAPTER.match(line):
        return 2
    return None


def is_break(line: str) -> bool:
    return bool(_BREAK.match(line)) and sum(not c.isspace() for c in line) >= 3


@dataclass
class Built:
    text: str
    script: Script
    chapters: int
    skipped: int


class _Builder:
    def __init__(self, language: str) -> None:
        self.language = language
        self.lines: list[str] = []
        self.offset = 0
        self.blocks: list[dict] = []
        self.chapter = -1       # c000 is created lazily for text before the first chapter
        self.para = 0

    def _line(self, text: str) -> list[int]:
        start = self.offset
        self.lines.append(text)
        self.offset += len(text) + 1  # "\n"
        return [start, start + len(text)]

    def chapter_block(self, title: str, level: int) -> None:
        self.chapter += 1
        self.para = 0
        self.blocks.append({"id": f"c{self.chapter:03d}", "type": "chapter", "text": title,
                            "level": level, "src": self._line(title)})

    def ensure_chapter(self, title: str) -> None:
        if self.chapter < 0:
            self.chapter_block(title, 1)

    def block(self, kind: str, text: str, **fields) -> None:
        self.para += 1
        self.blocks.append({"id": f"c{self.chapter:03d}.p{self.para:04d}", "type": kind, "text": text,
                            **fields, "src": self._line(text)})


def build(book: Book, language: str | None = None) -> Built:
    """Lay out text.txt and the blocks. TXT chapters come from heading lines, EPUB
    chapters from the table of contents (plus heading lines inside long sections)."""
    sample = "".join(p.text for s in book.sections for p in s.paragraphs[:200])[:20000]
    language = language or book.language or detect_language(sample)
    b = _Builder(language)
    skipped = 0

    def paragraphs_of(paragraphs: list[Paragraph], skip: str | None) -> None:
        nonlocal skipped
        for p in paragraphs:
            if skip:
                b.ensure_chapter(book.title)
                b.block("skip", p.text, reason=skip)
                skipped += 1
                continue
            if b.chapter < 0 and _same_title(p.text, book.title):
                # A title line at the top of the file names the opening section; use it
                # instead of inserting the title a second time.
                b.chapter_block(p.text, 1)
                continue
            level = classify_heading(p.text, language)
            if level is not None:
                b.chapter_block(p.text, level)
            elif is_break(p.text):
                b.ensure_chapter(book.title)
                b.block("break", p.text)
            elif p.kind != "p":
                b.ensure_chapter(book.title)
                b.block("heading", p.text, level=int(p.kind[1]))
            else:
                b.ensure_chapter(book.title)
                b.block("narration", p.text)

    for section in book.sections:
        paragraphs = section.paragraphs
        if section.title and not section.skip:
            # The TOC title starts a chapter; reuse the section's own heading line when it
            # repeats the title, so the title is not read twice.
            first = paragraphs[0].text if paragraphs else ""
            if _same_title(first, section.title):
                paragraphs = paragraphs[1:]
                title = first
            else:
                title = section.title
            level = classify_heading(title, language) or (1 if section.level == 1 else 2)
            b.chapter_block(title, min(level, 2))
        paragraphs_of(paragraphs, section.skip)

    # Two levels only when the book has both volumes and chapters; otherwise every chapter
    # is top level (docs/script-ir.md §4). The opening section c000 is always top level
    # and does not count as a volume.
    levels = {blk["level"] for blk in b.blocks if blk["type"] == "chapter" and blk["id"] != "c000"}
    for block in b.blocks:
        if block["type"] == "chapter" and (levels != {1, 2} or block["id"] == "c000"):
            block["level"] = 1

    text = "\n".join(b.lines) + "\n"
    header = {"type": "huaben", "version": FORMAT_VERSION, "title": book.title,
              "author": book.author, "language": language,
              "source": {"format": book.format}, "text": "text.txt", "text_sha256": sha256_text(text)}
    if book.meta:
        header["meta"] = book.meta
    chapters = sum(1 for blk in b.blocks if blk["type"] == "chapter")
    return Built(text=text, script=Script(header=header, blocks=b.blocks), chapters=chapters,
                 skipped=skipped)


def _same_title(line: str, title: str) -> bool:
    squash = lambda s: re.sub(r"[\s　:：·\-—]", "", s)
    return bool(line) and squash(line) == squash(title)
