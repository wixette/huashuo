import pytest

from conftest import make_epub
from huashuo.huaben import check
from huashuo.ingest import IngestError, detect_language, read_book
from huashuo.ingest.txt import decode, title_author_from_name
from huashuo.structure import build

CHINESE = "第一回　甄士隐梦幻识通灵，“你好！”"


@pytest.mark.parametrize("encoding, expected", [
    ("utf-8", "utf-8"), ("gbk", "gb18030"), ("gb18030", "gb18030"), ("utf-16", "utf-16"),
])
def test_decode_detects_common_encodings(encoding, expected):
    text, used = decode(CHINESE.encode(encoding))
    assert text.lstrip("﻿") == CHINESE and used == expected


def test_decode_bom_and_override():
    assert decode(b"\xef\xbb\xbf" + CHINESE.encode())[1] == "utf-8-sig"
    assert decode("繁體中文".encode("big5"), "big5")[0] == "繁體中文"


def test_decode_refuses_to_guess():
    with pytest.raises(IngestError, match="--encoding"):
        decode(bytes(range(128, 256)) * 4)


@pytest.mark.parametrize("stem, title, author", [
    ("《红楼梦》作者：曹雪芹", "红楼梦", "曹雪芹"),
    ("红楼梦（作者：曹雪芹）", "红楼梦", "曹雪芹"),
    ("Pride and Prejudice by Jane Austen", "Pride and Prejudice", "Jane Austen"),
    ("三体", "三体", ""),
])
def test_title_author_from_file_name(stem, title, author):
    assert title_author_from_name(stem) == (title, author)


def test_language_detection():
    assert detect_language("雪下了整整一夜。He said hello.") == "zh"
    assert detect_language("It was the best of times, it was the worst of times.") == "en"


def test_epub_metadata_cover_and_fragment_chapters(tmp_path):
    book = read_book(make_epub(tmp_path / "测试之书.epub"))
    assert (book.title, book.author, book.language) == ("测试之书", "无名氏", "zh")
    assert book.meta["publisher"] == "示例出版社" and book.meta["description"] == "一本书。"
    assert book.cover.startswith(b"\xff\xd8")
    titles = [s.title for s in book.sections]
    assert titles == ["版权信息", "第一章 起风", "第二章 落雨"]
    assert book.sections[0].skip == "copyright"
    first = [p.text for p in book.sections[1].paragraphs]
    assert first[1] == "风从北边来，吹过山岗。"          # ruby annotation dropped
    assert first[2] == "他推开门 走了出去。"             # &nbsp; normalized


def test_epub_builds_a_sound_script(tmp_path):
    built = build(read_book(make_epub(tmp_path / "b.epub")))
    kinds = [(b["type"], b["text"]) for b in built.script.blocks]
    assert ("chapter", "第一章 起风") in kinds and ("chapter", "第二章 落雨") in kinds
    assert [k for k, _ in kinds].count("skip") == 2
    assert check(built.script, built.text) == []


def test_not_an_epub(tmp_path):
    path = tmp_path / "x.epub"
    path.write_text("nope")
    with pytest.raises(IngestError, match="not a valid EPUB"):
        read_book(path)
