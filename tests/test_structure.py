import pytest

from huashuo.huaben import check
from huashuo.ingest import Book, Paragraph, Section, read_book
from huashuo.structure import build, classify_heading, is_break, looks_like_title, unwrap


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


def _book(lines, sections=None):
    return Book("书", "", "zh", sections or [Section([Paragraph(t) if isinstance(t, str) else t for t in lines])], "txt")


def test_hard_wrapped_lines_are_rejoined():
    sentence = "他走了很远的路，" * 10 + "终于到了。"
    wrapped = [sentence[i:i + 30] for i in range(0, len(sentence), 30)] * 12
    out = unwrap([Paragraph(t) for t in wrapped], "zh")
    assert len(out) < len(wrapped) / 2
    assert all(p.text.endswith("。") for p in out[:-1])


def test_unwrapped_text_is_left_alone():
    lines = [Paragraph(f"第{i}句话很短。") for i in range(40)]
    assert unwrap(lines, "zh") == lines


def test_gutenberg_boilerplate_is_skipped():
    built = build(_book(["The Project Gutenberg eBook of 书", "*** START OF THE PROJECT GUTENBERG EBOOK 书 ***",
                         "Produced by Someone", "第一章 开始", "正文在这里。",
                         "*** END OF THE PROJECT GUTENBERG EBOOK 书 ***", "Section 1. General Terms"]))
    kinds = [(b["type"], b.get("reason")) for b in built.script.blocks]
    assert kinds.count(("skip", "license")) == 5
    assert [b["text"] for b in built.script.blocks if b["type"] != "skip"][-2:] == ["第一章 开始", "正文在这里。"]


def test_unnumbered_story_titles():
    story = "某君昆仲，今隐其名，皆余昔日在中学时良友；分隔多年，消息渐阙。" * 25
    lines = ["狂人日记", story, "一九一八年四月。", "注释", "这是一条很长的注释说明，交代了写作背景和发表经过。",
             "孔乙己", "“你怎么了？”", "短句", story, "明天", story]
    built = build(_book(lines))
    chapters = [b["text"] for b in built.script.blocks if b["type"] == "chapter"]
    # 「短句」 looks like a title and is followed by prose, but comes right after a chapter start.
    assert chapters == ["狂人日记", "孔乙己", "明天"]
    heading = next(b for b in built.script.blocks if b["text"] == "注释")
    assert heading["type"] == "heading"
    date = next(b for b in built.script.blocks if b["text"] == "一九一八年四月。")
    assert date["type"] == "narration"


@pytest.mark.parametrize("line", ["他想道︰", "说话：", "□注釋", "第二回忘記了那一年，總之是募集湖北水災捐。"])
def test_lead_ins_notes_and_sentences_are_not_titles(line):
    assert classify_heading(line, "zh") is None and not looks_like_title(line, "zh")


def test_markup_headings_must_look_like_titles():
    prose = Paragraph("面目；我要到N進K學堂去了，仿佛是想走異路，逃異地，去尋求別樣的人們。", "h5")
    built = build(_book([Paragraph("第一章 起", "h1"), prose, Paragraph("小标题", "h3")]))
    types = {b["text"][:4]: b["type"] for b in built.script.blocks}
    assert types["面目；我"] == "narration" and types["小标题"] == "heading"


def test_stray_kana_and_placeholders_are_not_read():
    from huashuo.structure import speech_cleanup
    assert speech_cleanup("ぇ本篇最初發表于一九一九年四月《新青年》。", "zh") == "本篇最初發表于一九一九年四月《新青年》。"
    assert speech_cleanup("□注釋", "zh") == "注釋"
    assert speech_cleanup("普通的句子。", "zh") is None
    japanese = "「ありがとうございます」と彼は言った。"
    assert speech_cleanup(japanese, "zh") is None          # real Japanese stays
    built = build(_book(["第一章 起", "お進學︰明清科舉制度，童生經過縣考初試。"]))
    block = built.script.blocks[-1]
    assert block["say"] == "進學︰明清科舉制度，童生經過縣考初試。" and block["text"].startswith("お")
    assert check(built.script, built.text) == []
