"""Plain-text input: encoding detection (IN-1) and title/author from the file name (IN-4)."""

from __future__ import annotations

import codecs
import re
from pathlib import Path

from huashuo.ingest import Book, IngestError, Paragraph, Section

# Share of non-ASCII characters that must be common CJK characters or CJK punctuation for
# a GB18030 decode to be believed. GB18030 accepts most byte sequences, so a Big5 or
# Shift-JIS file can decode "successfully" into nonsense; real Chinese text scores ~0.99.
_MIN_CJK_SHARE = 0.9
_PLAUSIBLE = re.compile(r"[一-鿿　-〿＀-￯‐-‧·]")


def _plausible(text: str) -> bool:
    non_ascii = [c for c in text if ord(c) > 127]
    if not non_ascii:
        return "\x00" not in text
    return sum(bool(_PLAUSIBLE.match(c)) for c in non_ascii) / len(non_ascii) >= _MIN_CJK_SHARE


def decode(data: bytes, encoding: str | None = None) -> tuple[str, str]:
    """Return (text, encoding). Strict: an ambiguous file is an error, never mojibake.

    Tried in order: a byte-order mark, UTF-8, GB18030 (which covers GBK and GB2312), then
    UTF-16 without a mark. Every guess past UTF-8 must decode into text that is mostly
    Chinese characters and punctuation, because these encodings accept byte sequences
    that are not really theirs.
    """
    if encoding:
        try:
            return data.decode(encoding), encoding
        except (UnicodeDecodeError, LookupError) as exc:
            raise IngestError(f"cannot decode as {encoding}: {exc}") from exc

    for bom, name in ((codecs.BOM_UTF8, "utf-8-sig"), (codecs.BOM_UTF16_LE, "utf-16"),
                      (codecs.BOM_UTF16_BE, "utf-16")):
        if data.startswith(bom):
            return data.decode(name), name
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass
    candidates = ["gb18030"] + (["utf-16-le", "utf-16-be"] if len(data) % 2 == 0 else [])
    for name in candidates:
        try:
            text = data.decode(name)
        except UnicodeDecodeError:
            continue
        if _plausible(text):
            return text, name
    raise IngestError("cannot tell this file's encoding (tried UTF-8, GB18030/GBK and UTF-16); "
                      "pass --encoding, e.g. --encoding big5")


_TITLE_AUTHOR = re.compile(r"^《?(?P<title>[^》]+?)》?\s*(?:[（(]?\s*(?:作者|著者|by)\s*[:：]?\s*"
                           r"(?P<author>[^）)]+?)\s*[）)]?)?$", re.IGNORECASE)


def title_author_from_name(stem: str) -> tuple[str, str]:
    """「《红楼梦》作者：曹雪芹」-> ("红楼梦", "曹雪芹"); otherwise the stem and an empty author."""
    match = _TITLE_AUTHOR.match(stem.strip())
    if not match:
        return stem.strip(), ""
    return match["title"].strip(), (match["author"] or "").strip()


# 「作者：曹雪芹」 or "Author: Jane Austen" on a line of its own (structure.py skips it later).
AUTHOR_LINE = re.compile(r"^(?:作者|著者|author|by)\s*[:：]?\s*(?P<author>\S.{0,38})$", re.IGNORECASE)


def author_from_opening(paragraphs: list[Paragraph], limit: int = 10) -> str:
    """「作者：曹雪芹」 or "Author: Jane Austen" among the first few lines, if present."""
    for p in paragraphs[:limit]:
        match = AUTHOR_LINE.match(p.text)
        if match:
            return match["author"].strip()
    return ""


def read_txt(path: Path, encoding: str | None = None) -> Book:
    text, used = decode(path.read_bytes(), encoding)
    text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    # Strip each line, including the full-width spaces used for paragraph indentation.
    paragraphs = [Paragraph(line.strip(" \t　 ")) for line in text.split("\n")]
    paragraphs = [p for p in paragraphs if p.text]
    if not paragraphs:
        raise IngestError(f"{path} contains no text")
    title, author = title_author_from_name(path.stem)
    author = author or author_from_opening(paragraphs)
    return Book(title=title, author=author, language=None, sections=[Section(paragraphs)],
                format="txt", encoding=used)
