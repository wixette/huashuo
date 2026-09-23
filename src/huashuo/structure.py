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
from huashuo.ingest import Book, Paragraph, Section, detect_language

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
    if not looks_like_title(line, language) or line.endswith(("”", "\"")):
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


# Stray Japanese kana inside Chinese text are almost always markup residue (this is how
# some converted editions mark footnote references) and would be read aloud as sounds;
# placeholder boxes stand for characters the converter could not render. Neither is read,
# but both stay in `text`; the cleaned reading goes into `say`. Real Japanese passages,
# where kana are frequent, are left alone.
_KANA = re.compile(r"[\u3041-\u309f\u30a1-\u30fa\u30fd-\u30ff]")
_PLACEHOLDERS = re.compile(r"[□■�]")
_MAX_STRAY_KANA_SHARE = 0.05


def speech_cleanup(text: str, language: str) -> str | None:
    """The text to read when it differs from the text on the page, else None."""
    if language != "zh":
        return None
    kana = len(_KANA.findall(text))
    cleaned = text
    if kana and kana <= _MAX_STRAY_KANA_SHARE * len(text) + 1:
        cleaned = _KANA.sub("", cleaned)
    cleaned = _PLACEHOLDERS.sub("", cleaned).strip()
    return cleaned if cleaned != text and cleaned else None


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


# Section numbers inside a chapter: 「一」「（二）」「3」「IV」.
_SECTION_NUMBER = re.compile(r"^[（(]?([一二三四五六七八九十百零〇]+|[0-9０-９]+|[IVXLC]+)[)）.．、]?$")
_SENTENCE_MARKS = re.compile(r"[。！？!?；;]")
_TITLE_PUNCT = re.compile(r"[，,、：:︰“”\"'‘’（）()…—\-·]")
# A line ending in a colon introduces speech or a list; it is never a title.
_LEAD_IN = re.compile(r"[：:︰]\s*$")
# Labels of annotation sections: headings inside a chapter, never chapters themselves.
_NOTE_LABELS = re.compile(r"^[□■\s]*(注釋|注释|註釋|註|注|附注|附註|按|译注|譯注|译者注|譯者注|校记|校記)[︰：:]?$")
# Dates and bare numbers (「一九一九年三月」「十二」), which end stories rather than begin them.
_DATE_LIKE = re.compile(r"[0-9０-９零〇一二三四五六七八九十]+\s*[年月日号號]|^[0-9０-９零〇一二三四五六七八九十]+$")

# Unnumbered titles (short-story collections): a very short line with no punctuation,
# followed by prose, far enough from the previous chapter that ordinary short lines
# inside a chapter do not qualify.
_UNNUMBERED_MAX_CHARS = 8
_UNNUMBERED_MIN_GAP = 600
_PROSE_MIN_CHARS = 20

_GUTENBERG_START = re.compile(r"\*\*\*\s*START OF (THE|THIS) PROJECT GUTENBERG", re.I)
_GUTENBERG_END = re.compile(r"\*\*\*\s*END OF (THE|THIS) PROJECT GUTENBERG", re.I)


def _is_marker(text: str) -> bool:
    return bool(_GUTENBERG_START.search(text) or _GUTENBERG_END.search(text))


def _is_prose(text: str) -> bool:
    return len(text) >= _PROSE_MIN_CHARS or bool(_SENTENCE_MARKS.search(text))


def looks_like_title(text: str, language: str) -> bool:
    """Short, and not a sentence. Guards against EPUBs whose markup or table of contents
    labels ordinary prose as headings (common in converted-from-text files)."""
    return (0 < len(text) <= _MAX_TITLE_CHARS[language] and not _SENTENCE_MARKS.search(text)
            and len(re.findall(r"[，,]", text)) <= 1 and not _LEAD_IN.search(text)
            and not _NOTE_LABELS.match(text))


# Hard-wrapped text (common in texts converted from plain-text releases): lines cut at a
# fixed width mid-sentence. Rejoined before anything else, or every line break would
# become a paragraph with a pause in the middle of a sentence.
_ENDS_PARAGRAPH = re.compile(r"[。！？…!?”」』）)\]】》]\s*$|[.!?]['\"’”)]?\s*$")


def unwrap(paragraphs: list[Paragraph], language: str) -> list[Paragraph]:
    """Join lines of a hard-wrapped text back into paragraphs; leave other text alone.

    Detected when a large share of the lines have (nearly) the same, maximal length: the
    wrap width. A line then continues into the next when it reaches that width and does
    not end a sentence.
    """
    body = [p for p in paragraphs if p.kind == "p"]
    lengths = [len(p.text) for p in body if len(p.text) >= 10]
    if len(lengths) < 20:
        return paragraphs
    width = sorted(lengths)[int(len(lengths) * 0.9)]
    near = sum(1 for n in lengths if width - 3 <= n <= width + 1)
    if near < 0.4 * len(lengths):
        return paragraphs
    joiner = "" if language == "zh" else " "
    out: list[Paragraph] = []
    last_line: list[int] = []   # length of the final wrapped line of each output paragraph
    for p in paragraphs:
        if (out and out[-1].kind == "p" and p.kind == "p" and last_line[-1] >= width - 3
                and not _ENDS_PARAGRAPH.search(out[-1].text) and not _SECTION_NUMBER.match(p.text)
                and not _is_marker(out[-1].text) and not _is_marker(p.text)
                and classify_heading(p.text, language) is None):
            out[-1] = Paragraph(out[-1].text + joiner + p.text, "p")
            last_line[-1] = len(p.text)
        else:
            out.append(p)
            last_line.append(len(p.text))
    return out


@dataclass
class _Item:
    paragraph: Paragraph
    skip: str | None
    section: Section | None   # set on the first paragraph of a titled TOC section


def _flatten(book: Book, language: str) -> list[_Item]:
    items: list[_Item] = []
    for section in book.sections:
        titled = bool(section.title) and not section.skip and looks_like_title(section.title, language)
        paragraphs = unwrap(section.paragraphs, language) or ([Paragraph(section.title)] if titled else [])
        for i, p in enumerate(paragraphs):
            items.append(_Item(p, section.skip, section if titled and i == 0 else None))
    # Project Gutenberg boilerplate: everything outside the START/END markers.
    start = next((i for i, it in enumerate(items) if _GUTENBERG_START.search(it.paragraph.text)), None)
    end = next((i for i, it in enumerate(items) if _GUTENBERG_END.search(it.paragraph.text)), None)
    if start is not None:
        credits = start + 1
        while credits < min(start + 4, len(items)) and items[credits].paragraph.text.lower().startswith("produced by"):
            credits += 1
        for it in items[:credits]:
            it.skip, it.section = "license", None
    if end is not None:
        for it in items[end:]:
            it.skip, it.section = "license", None
    return items


def build(book: Book, language: str | None = None, read_notes: bool = False) -> Built:
    """Lay out text.txt and the blocks. Chapters come from heading lines (第X章, Chapter N,
    unnumbered story titles) and, for EPUB, from table-of-contents entries that look like
    titles.

    Editorial annotations (a 「注釋」 label and everything after it up to the next
    chapter) are kept but not read unless `read_notes` is set.
    """
    sample = "".join(p.text for s in book.sections for p in s.paragraphs[:200])[:20000]
    language = language or book.language or detect_language(sample)
    b = _Builder(language)
    items = _flatten(book, language)
    skipped = 0
    since_chapter = _UNNUMBERED_MIN_GAP   # characters read since the last chapter started

    def next_prose(i: int) -> str:
        for it in items[i + 1:i + 4]:
            if not _SECTION_NUMBER.match(it.paragraph.text):
                return it.paragraph.text
        return ""

    def unnumbered_title(i: int, text: str) -> bool:
        return (language == "zh" and len(text) <= _UNNUMBERED_MAX_CHARS and not _NOTE_LABELS.match(text)
                and since_chapter >= _UNNUMBERED_MIN_GAP
                and not _SENTENCE_MARKS.search(text) and not _TITLE_PUNCT.search(text.strip("《》"))
                and not _DATE_LIKE.search(text) and _is_prose(next_prose(i)))

    in_notes = False

    def chapter(title: str, level: int) -> None:
        nonlocal since_chapter, in_notes
        b.chapter_block(title, level)
        since_chapter, in_notes = 0, False

    for i, item in enumerate(items):
        p, text = item.paragraph, item.paragraph.text
        if item.section is not None:
            section = item.section
            level = min(classify_heading(section.title, language) or (1 if section.level == 1 else 2), 2)
            if _same_title(text, section.title):
                chapter(text, level)   # the section's own heading repeats its TOC title
                continue
            chapter(section.title, level)
        if item.skip:
            b.ensure_chapter(book.title)
            b.block("skip", text, reason=item.skip)
            skipped += 1
            continue
        previous = b.blocks[-1] if b.blocks else None
        if previous and previous["type"] == "chapter" and _same_title(text, previous["text"]):
            b.block("skip", text, reason="duplicate")   # a title printed twice in a row
            skipped += 1
            continue
        if b.chapter < 0 and _same_title(text, book.title):
            # A title line at the top of the file names the opening section; use it
            # instead of inserting the title a second time.
            chapter(text, 1)
            continue
        level = classify_heading(text, language)
        if level is None and _NOTE_LABELS.match(text) and not read_notes:
            in_notes = True
        if level is not None:
            chapter(text, level)
        elif in_notes and not unnumbered_title(i, text):
            b.ensure_chapter(book.title)
            b.block("skip", text, reason="notes")
            skipped += 1
        elif is_break(text):
            b.ensure_chapter(book.title)
            b.block("break", text)
        elif _SECTION_NUMBER.match(text) or _NOTE_LABELS.match(text):
            b.ensure_chapter(book.title)
            b.block("heading", text, level=3)
        elif unnumbered_title(i, text):
            chapter(text, 2)
        elif p.kind != "p" and looks_like_title(text, language):
            b.ensure_chapter(book.title)
            b.block("heading", text, level=int(p.kind[1]))
        else:
            b.ensure_chapter(book.title)
            b.block("narration", text)
            since_chapter += len(text)

    # Two levels only when the book has both volumes and chapters; otherwise every chapter
    # is top level (docs/script-ir.md §4). The opening section c000 is always top level
    # and does not count as a volume.
    levels = {blk["level"] for blk in b.blocks if blk["type"] == "chapter" and blk["id"] != "c000"}
    for block in b.blocks:
        if block["type"] == "chapter" and (levels != {1, 2} or block["id"] == "c000"):
            block["level"] = 1

    for block in b.blocks:
        if block["type"] in ("chapter", "heading", "narration"):
            say = speech_cleanup(block["text"], language)
            if say:
                block["say"] = say

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
