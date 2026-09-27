#!/usr/bin/env python3
"""Build an EPUB 3 from an example's plain-text source, so the same story exists in both
input formats huashuo reads (and the golden tests can check they give the same book).

The TXT is: a title line, an author line (「作者：…」), then paragraphs separated by blank
lines; a line with only a number starts a numbered section. Each section becomes one
EPUB chapter with a table-of-contents entry (EPUB 3 nav, plus an NCX for EPUB 2 readers).
The cover is drawn from huashuo's cover templates. The archive is reproducible: fixed
timestamps and a stable identifier, so rebuilding an unchanged text gives the same bytes.

Usage (from the repository root):
    .venv/bin/python examples/make_epub.py examples/fish/txt/一条被洗澡水拍死的鱼.txt \\
        --date 2016-04-01 --source https://ygwang.info/fictions/fish/ --rights "CC BY-NC-ND 4.0"
"""

from __future__ import annotations

import argparse
import html
import io
import re
import uuid
import zipfile
from pathlib import Path

STAMP = (2016, 1, 1, 0, 0, 0)


def parse(text: str) -> tuple[str, str, list[tuple[str, list[str]]]]:
    lines = [line.strip() for line in text.splitlines()]
    title = lines[0]
    author = re.sub(r"^作者[：:]\s*", "", lines[1])
    sections: list[tuple[str, list[str]]] = []
    for line in lines[2:]:
        if not line:
            continue
        if re.fullmatch(r"\d+", line):
            sections.append((line, []))
        else:
            if not sections:
                sections.append(("", []))
            sections[-1][1].append(line)
    return title, author, sections


def page(title: str, body: str) -> str:
    return (f'<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
            f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" '
            f'lang="zh-CN" xml:lang="zh-CN">\n<head><meta charset="utf-8"/><title>{html.escape(title)}</title></head>\n'
            f'<body>\n{body}\n</body>\n</html>\n')


def build(txt: Path, out: Path, date: str, source: str, rights: str, cover_size: int = 1600) -> Path:
    from PIL import Image

    from huashuo.cover import render

    title, author, sections = parse(txt.read_text(encoding="utf-8"))
    ident = f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, source or title)}"
    files: dict[str, bytes] = {}
    chapters = []
    for n, (name, paragraphs) in enumerate(sections, 1):
        heading = f"<h2>{html.escape(name)}</h2>\n" if name else ""
        body = heading + "\n".join(f"<p>{html.escape(p)}</p>" for p in paragraphs)
        files[f"OEBPS/text/s{n:02d}.xhtml"] = page(name or title, body).encode()
        chapters.append((f"text/s{n:02d}.xhtml", name or title))

    rendered = out.with_suffix(".cover.tmp.jpg")
    render(title, rendered)
    with Image.open(rendered) as image:
        buffer = io.BytesIO()
        image.resize((cover_size, cover_size), Image.LANCZOS).save(buffer, "JPEG", quality=88)
    rendered.unlink()
    files["OEBPS/images/cover.jpg"] = buffer.getvalue()
    files["OEBPS/text/cover.xhtml"] = page(title, f'<p><img src="../images/cover.jpg" alt="{html.escape(title)}"/></p>').encode()

    toc = "\n".join(f'<li><a href="{href}">{html.escape(name)}</a></li>' for href, name in chapters)
    files["OEBPS/nav.xhtml"] = page(title, f'<nav epub:type="toc" id="toc"><h1>目录</h1><ol>\n{toc}\n</ol></nav>').encode()
    points = "\n".join(f'<navPoint id="p{n}" playOrder="{n}"><navLabel><text>{html.escape(name)}</text></navLabel>'
                       f'<content src="{href}"/></navPoint>' for n, (href, name) in enumerate(chapters, 1))
    files["OEBPS/toc.ncx"] = (f'<?xml version="1.0" encoding="utf-8"?>\n<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" '
                              f'version="2005-1"><head><meta name="dtb:uid" content="{ident}"/></head>'
                              f'<docTitle><text>{html.escape(title)}</text></docTitle><navMap>\n{points}\n</navMap></ncx>\n').encode()
    manifest = ['<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
                '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
                '<item id="cover-image" href="images/cover.jpg" media-type="image/jpeg" properties="cover-image"/>',
                '<item id="cover" href="text/cover.xhtml" media-type="application/xhtml+xml"/>']
    manifest += [f'<item id="s{n:02d}" href="{href}" media-type="application/xhtml+xml"/>'
                 for n, (href, _) in enumerate(chapters, 1)]
    spine = ['<itemref idref="cover" linear="no"/>'] + [f'<itemref idref="s{n:02d}"/>' for n in range(1, len(chapters) + 1)]
    metadata = [f"<dc:identifier id=\"bookid\">{ident}</dc:identifier>", f"<dc:title>{html.escape(title)}</dc:title>",
                f"<dc:creator>{html.escape(author)}</dc:creator>", "<dc:language>zh-CN</dc:language>",
                f"<dc:date>{date}</dc:date>", f"<dc:rights>{html.escape(rights)}</dc:rights>",
                f"<dc:source>{html.escape(source)}</dc:source>", '<meta name="cover" content="cover-image"/>',
                f'<meta property="dcterms:modified">{date}T00:00:00Z</meta>']
    files["OEBPS/content.opf"] = ('<?xml version="1.0" encoding="utf-8"?>\n<package xmlns="http://www.idpf.org/2007/opf" '
                                  'version="3.0" unique-identifier="bookid" xml:lang="zh-CN">\n'
                                  '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n' + "\n".join(metadata) +
                                  '\n</metadata>\n<manifest>\n' + "\n".join(manifest) + '\n</manifest>\n'
                                  '<spine toc="ncx">\n' + "\n".join(spine) + '\n</spine>\n</package>\n').encode()
    files["META-INF/container.xml"] = ('<?xml version="1.0" encoding="utf-8"?>\n<container version="1.0" '
                                       'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
                                       '<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
                                       '</rootfiles></container>\n').encode()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr(zipfile.ZipInfo("mimetype", STAMP), b"application/epub+zip", zipfile.ZIP_STORED)
        for name in sorted(files):
            info = zipfile.ZipInfo(name, STAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, files[name])
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("txt", type=Path)
    parser.add_argument("--date", required=True)
    parser.add_argument("--source", default="")
    parser.add_argument("--rights", default="")
    args = parser.parse_args()
    out = build(args.txt, args.txt.with_suffix(".epub"), args.date, args.source, args.rights)
    print(f"wrote {out} ({out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
