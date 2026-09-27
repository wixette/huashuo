"""EPUB 2/3 input, parsed with the standard library only (IN-2, IN-3).

EbookLib, the usual choice, is AGPL-licensed and would not fit an Apache-2.0 project, and
an EPUB is only a zip of XML and XHTML: container.xml -> OPF (metadata, manifest, spine)
-> NCX or nav (table of contents) -> XHTML documents.

Sections follow the table of contents: each TOC entry starts a section at the element it
points to (fragments included, so one XHTML file holding many chapters is split
correctly). Spine documents before the first TOC entry, or not reached by any, become
untitled sections.
"""

from __future__ import annotations

import posixpath
import re
import zipfile
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote
from xml.etree import ElementTree as ET

from huashuo.ingest import Book, IngestError, Paragraph, Section, normalize_language

NS = {
    "c": "urn:oasis:names:tc:opendocument:xmlns:container",
    "opf": "http://www.idpf.org/2007/opf",
    "dc": "http://purl.org/dc/elements/1.1/",
    "ncx": "http://www.daisy.org/z3986/2005/ncx/",
    "xhtml": "http://www.w3.org/1999/xhtml",
    "epub": "http://www.idpf.org/2007/ops",
}

# Titles of sections that are kept in the script but not read (TXT-4).
_SKIP_TITLES = [
    (re.compile(r"^\s*(目\s*录|目\s*錄|contents|table of contents)\s*$", re.I), "toc"),
    (re.compile(r"版权|版權|copyright", re.I), "copyright"),
]

_BLOCK_TAGS = {"p", "div", "li", "blockquote", "pre", "tr", "dt", "dd", "section", "article",
               "header", "footer", "aside", "figcaption", "table", "ul", "ol", "dl", "hr"}
_HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
_IGNORED_TAGS = {"script", "style", "head", "title", "rt", "rp", "svg", "math"}


@dataclass
class _Doc:
    paragraphs: list[Paragraph] = field(default_factory=list)
    anchors: dict[str, int] = field(default_factory=dict)  # element id -> paragraph index


class _XHTMLText(HTMLParser):
    """Collect paragraphs from XHTML, remembering where each element id appears."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.doc = _Doc()
        self._buf: list[str] = []
        self._kind = "p"
        self._ignore = 0

    def _flush(self) -> None:
        text = re.sub(r"[ \t\r\n ]+", " ", "".join(self._buf)).strip(" 　")
        if text:
            self.doc.paragraphs.append(Paragraph(text, self._kind))
        self._buf, self._kind = [], "p"

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in _IGNORED_TAGS:
            self._ignore += 1
            return
        if tag in _BLOCK_TAGS or tag in _HEADING_TAGS or tag == "br":
            self._flush()
        if tag in _HEADING_TAGS:
            self._kind = tag
        for name, value in attrs:
            if name in ("id", "name") and value and value not in self.doc.anchors:
                self.doc.anchors[value] = len(self.doc.paragraphs)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag.lower() in _IGNORED_TAGS:
            self._ignore -= 1

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in _IGNORED_TAGS:
            self._ignore = max(0, self._ignore - 1)
            return
        if tag in _BLOCK_TAGS or tag in _HEADING_TAGS:
            self._flush()

    def handle_data(self, data):
        if not self._ignore:
            self._buf.append(data)

    def close(self):
        super().close()
        self._flush()


def _parse_xhtml(data: bytes) -> _Doc:
    parser = _XHTMLText()
    parser.feed(data.decode("utf-8", errors="replace"))
    parser.close()
    return parser.doc


@dataclass
class _TocEntry:
    title: str
    href: str        # zip path of the document
    fragment: str | None
    level: int


def _xml(archive: zipfile.ZipFile, name: str) -> ET.Element:
    try:
        return ET.fromstring(archive.read(name))
    except KeyError as exc:
        raise IngestError(f"EPUB is missing {name}") from exc
    except ET.ParseError as exc:
        raise IngestError(f"EPUB file {name} is not valid XML: {exc}") from exc


def _join(base_dir: str, href: str) -> tuple[str, str | None]:
    path, _, fragment = unquote(href).partition("#")
    return posixpath.normpath(posixpath.join(base_dir, path)), fragment or None


def _text_of(element: ET.Element | None) -> str:
    return re.sub(r"\s+", " ", "".join(element.itertext())).strip() if element is not None else ""


def _toc_from_ncx(root: ET.Element, base_dir: str) -> list[_TocEntry]:
    entries: list[_TocEntry] = []

    def walk(node: ET.Element, level: int) -> None:
        for point in node.findall("ncx:navPoint", NS):
            label = _text_of(point.find("ncx:navLabel/ncx:text", NS))
            content = point.find("ncx:content", NS)
            if content is not None and content.get("src"):
                href, fragment = _join(base_dir, content.get("src"))
                entries.append(_TocEntry(label, href, fragment, level))
            walk(point, level + 1)

    nav_map = root.find("ncx:navMap", NS)
    if nav_map is not None:
        walk(nav_map, 1)
    return entries


def _toc_from_nav(data: bytes, base_dir: str) -> list[_TocEntry]:
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return []  # e.g. HTML entities such as &nbsp; that XML does not define; use the NCX
    navs = root.iter(f"{{{NS['xhtml']}}}nav")
    toc = next((n for n in navs if n.get(f"{{{NS['epub']}}}type") == "toc"), None)
    if toc is None:
        return []
    entries: list[_TocEntry] = []

    def walk(ol: ET.Element, level: int) -> None:
        for li in ol.findall("xhtml:li", NS):
            link = li.find("xhtml:a", NS)
            if link is not None and link.get("href"):
                href, fragment = _join(base_dir, link.get("href"))
                entries.append(_TocEntry(_text_of(link), href, fragment, level))
            for child in li.findall("xhtml:ol", NS):
                walk(child, level + 1)

    for ol in toc.findall("xhtml:ol", NS):
        walk(ol, 1)
    return entries


def read_epub(path: Path) -> Book:
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise IngestError(f"{path} is not a valid EPUB (not a zip file)") from exc

    with archive:
        container = _xml(archive, "META-INF/container.xml")
        rootfile = container.find("c:rootfiles/c:rootfile", NS)
        if rootfile is None or not rootfile.get("full-path"):
            raise IngestError("EPUB container.xml names no package document")
        opf_path = rootfile.get("full-path")
        opf_dir = posixpath.dirname(opf_path)
        opf = _xml(archive, opf_path)

        metadata = opf.find("opf:metadata", NS)

        def dc(name: str) -> str:
            return _text_of(metadata.find(f"dc:{name}", NS)) if metadata is not None else ""

        manifest = {}
        for item in opf.iterfind("opf:manifest/opf:item", NS):
            href, _ = _join(opf_dir, item.get("href", ""))
            manifest[item.get("id")] = {"href": href, "type": item.get("media-type", ""),
                                        "props": (item.get("properties") or "").split()}

        spine_el = opf.find("opf:spine", NS)
        spine = [manifest[ref.get("idref")]["href"]
                 for ref in (spine_el.iterfind("opf:itemref", NS) if spine_el is not None else [])
                 if ref.get("idref") in manifest and ref.get("linear", "yes") != "no"]
        nav_hrefs = {m["href"] for m in manifest.values() if "nav" in m["props"]}
        spine = [h for h in spine if h not in nav_hrefs]
        if not spine:
            raise IngestError("EPUB has an empty spine: no readable documents")

        # Table of contents: EPUB 3 nav first, then the EPUB 2 NCX.
        names = set(archive.namelist())
        toc: list[_TocEntry] = []
        for href in nav_hrefs & names:
            toc = _toc_from_nav(archive.read(href), posixpath.dirname(href))
            if toc:
                break
        if not toc:
            ncx_id = spine_el.get("toc") if spine_el is not None else None
            ncx = manifest.get(ncx_id) or next(
                (m for m in manifest.values() if m["type"] == "application/x-dtbncx+xml"), None)
            if ncx:
                toc = _toc_from_ncx(_xml(archive, ncx["href"]), posixpath.dirname(ncx["href"]))

        docs = {href: _parse_xhtml(archive.read(href)) for href in spine if href in names}

        # Where each TOC entry starts: (spine position, paragraph index).
        starts: dict[tuple[int, int], _TocEntry] = {}
        for entry in toc:
            if entry.href not in docs:
                continue
            doc = docs[entry.href]
            para = doc.anchors.get(entry.fragment, 0) if entry.fragment else 0
            starts.setdefault((spine.index(entry.href), para), entry)

        sections: list[Section] = []
        current: Section | None = None
        for position, href in enumerate(spine):
            doc = docs.get(href)
            if doc is None:
                continue
            breaks = sorted(p for (s, p) in starts if s == position)
            if not breaks or breaks[0] != 0:
                # Text before the file's first TOC entry continues the previous section,
                # or starts an untitled one at the beginning of the book.
                breaks = [0, *breaks]
            for i, begin in enumerate(breaks):
                end = breaks[i + 1] if i + 1 < len(breaks) else len(doc.paragraphs)
                entry = starts.get((position, begin))
                if entry is not None or current is None:
                    current = Section(paragraphs=[], title=entry.title if entry else None,
                                      level=entry.level if entry else 1)
                    if current.title:
                        current.skip = next((reason for pattern, reason in _SKIP_TITLES
                                             if pattern.search(current.title)), None)
                    sections.append(current)
                current.paragraphs.extend(doc.paragraphs[begin:end])

        cover, cover_ext = _find_cover(archive, opf, manifest)

    meta = {k: v for k, v in (("publisher", dc("publisher")), ("date", dc("date")),
                              ("description", re.sub(r"<[^>]+>", "", dc("description"))))
            if v}
    return Book(title=dc("title") or path.stem, author=dc("creator"),
                language=normalize_language(dc("language")),
                sections=[s for s in sections if s.paragraphs or s.title],
                format="epub", meta=meta, cover=cover, cover_ext=cover_ext)


def _find_cover(archive: zipfile.ZipFile, opf: ET.Element, manifest: dict) -> tuple[bytes | None, str]:
    item = next((m for m in manifest.values() if "cover-image" in m["props"]), None)
    if item is None:
        meta = opf.find("opf:metadata/opf:meta[@name='cover']", NS)
        if meta is not None:
            item = manifest.get(meta.get("content"))
    if item is None or not item["type"].startswith("image/"):
        return None, ".jpg"
    ext = ".png" if item["type"] == "image/png" else ".jpg"
    try:
        return archive.read(item["href"]), ext
    except KeyError:
        return None, ext
