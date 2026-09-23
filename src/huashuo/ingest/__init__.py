"""Reading source files into a format-neutral Book (IN-1 … IN-5).

Readers only extract text and metadata; they do not decide chapters or what to skip.
That is structure.py's job, so TXT and EPUB go through the same rules.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


class IngestError(Exception):
    """The input cannot be read; the message says why and what to try."""


@dataclass
class Paragraph:
    text: str
    kind: str = "p"            # "p", or "h1".."h6" for headings found in markup


@dataclass
class Section:
    """A run of paragraphs. EPUB: one per TOC entry (or spine file); TXT: the whole file."""

    paragraphs: list[Paragraph]
    title: str | None = None   # from the EPUB table of contents
    level: int = 1             # nesting depth in the table of contents
    skip: str | None = None    # reason to keep but not read the whole section ("toc", ...)


@dataclass
class Book:
    title: str
    author: str
    language: str | None       # "zh" / "en" when the source says so, else detected later
    sections: list[Section]
    format: str                # "txt" / "epub"
    meta: dict = field(default_factory=dict)
    cover: bytes | None = None
    cover_ext: str = ".jpg"
    encoding: str | None = None


_CJK = re.compile(r"[㐀-鿿豈-﫿]")
_LATIN = re.compile(r"[A-Za-z]")


def detect_language(sample: str) -> str:
    """zh when Chinese characters are at least a fifth of the words, else en (IN-5).

    One CJK character is roughly one word; English averages about five letters a word.
    """
    cjk, latin_words = len(_CJK.findall(sample)), len(_LATIN.findall(sample)) / 5
    return "zh" if cjk and cjk >= 0.2 * (cjk + latin_words) else "en"


def normalize_language(value: str | None) -> str | None:
    if not value:
        return None
    value = value.lower()
    if value.startswith("zh") or value in ("chi", "zho", "chinese"):
        return "zh"
    if value.startswith("en") or value in ("eng", "english"):
        return "en"
    return None


def read_book(path: Path, encoding: str | None = None) -> Book:
    suffix = path.suffix.lower()
    if suffix == ".epub":
        from huashuo.ingest.epub import read_epub
        return read_epub(path)
    if suffix in (".txt", ".text", ""):
        from huashuo.ingest.txt import read_txt
        return read_txt(path, encoding)
    raise IngestError(f"unsupported input format {suffix!r}: use a .txt or .epub file "
                      f"(convert other formats with calibre's ebook-convert first)")
