import pytest

from huashuo.huaben import check
from huashuo.ingest import Book, Paragraph, Section, read_book
from huashuo.structure import build, classify_heading, is_break


@pytest.mark.parametrize("line", [
    "第一章 风雪山神庙", "第 12 回 大闹天宫", "楔子", "第三百零五章", "番外篇：十年后",
    "第一章陨落的天才", "第１２章 全角数字", "尾声",
])
def test_chinese_chapter_headings(line):
    assert classify_heading(line, "zh") == 2


@pytest.mark.parametrize("line", [
    "他第一章都没看", "第一章的内容很长", "第一章，他说。", "楔子是一种木工零件，用来固定榫头。",
    "第三章" + "很" * 50,
])
def test_chinese_prose_is_not_a_heading(line):
    assert classify_heading(line, "zh") is None


def test_volumes_and_english():
    assert classify_heading("第三卷 风起", "zh") == 1
    assert classify_heading("Chapter 12", "en") == 2
    assert classify_heading("CHAPTER IV. The Storm", "en") == 2
    assert classify_heading("Part Two", "en") == 1
    assert classify_heading("Chapters are long, he said.", "en") is None


@pytest.mark.parametrize("line, expected", [
    ("***", True), ("＊＊＊", True), ("◇ ◇ ◇", True), ("——", False), ("……他来了", False),
])
def test_breaks(line, expected):
    assert is_break(line) is expected


def test_txt_structure(sample_txt):
    built = build(read_book(sample_txt))
    blocks = built.script.blocks
    assert built.script.header["language"] == "zh"
    assert blocks[0] == {**blocks[0], "id": "c000", "type": "chapter", "text": "红楼梦"}
    chapters = [b["text"] for b in blocks if b["type"] == "chapter"]
    assert chapters == ["红楼梦", "第一回 甄士隐梦幻识通灵", "第二回 贾夫人仙逝扬州城"]
    assert any(b["type"] == "break" for b in blocks)
    assert blocks[-1]["text"] == "他第一章都没看完，就睡着了。" and blocks[-1]["type"] == "narration"
    assert "列位看官" in blocks[4]["text"] and not blocks[4]["text"].startswith("　")
    assert check(built.script, built.text) == []


def test_volume_levels_only_when_both_exist():
    paras = [Paragraph(t) for t in ["第一卷 起", "第一章 甲", "正文。", "第二章 乙", "正文。", "第二卷 承", "第三章 丙", "正文。"]]
    built = build(Book("书", "", "zh", [Section(paras)], "txt"))
    levels = [(b["text"], b["level"]) for b in built.script.blocks if b["type"] == "chapter"]
    assert levels == [("第一卷 起", 1), ("第一章 甲", 2), ("第二章 乙", 2), ("第二卷 承", 1), ("第三章 丙", 2)]
    flat = build(Book("书", "", "zh", [Section([Paragraph("第一章 甲"), Paragraph("正文。")])], "txt"))
    assert [b["level"] for b in flat.script.blocks if b["type"] == "chapter"] == [1]
