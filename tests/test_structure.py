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
    label = next(b for b in built.script.blocks if b["text"] == "注释")
    assert (label["type"], label.get("reason")) == ("skip", "notes")          # notes skipped by default
    read = build(_book(lines), read_notes=True)
    assert next(b for b in read.script.blocks if b["text"] == "注释")["type"] == "heading"
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
    assert block["say"] == "進學：明清科舉制度，童生經過縣考初試。" and block["text"].startswith("お")
    assert check(built.script, built.text) == []


def test_annotation_sections_are_skipped_unless_asked():
    story = "某君昆仲，今隐其名，皆余昔日在中学时良友；分隔多年，消息渐阙。" * 25
    lines = ["狂人日记", story, "一九一八年四月。", "□注釋", "ぇ本篇最初發表于一九一八年五月。", "え另一条注释。",
             "孔乙己", story]
    built = build(_book(lines))
    reasons = {b["text"][:4]: (b["type"], b.get("reason")) for b in built.script.blocks}
    assert reasons["□注釋"] == ("skip", "notes") and reasons["ぇ本篇最"] == ("skip", "notes")
    assert reasons["孔乙己"] == ("chapter", None)          # the next story ends the notes
    read = build(_book(lines), read_notes=True)
    assert {b["type"] for b in read.script.blocks if b["text"].startswith("ぇ本篇")} == {"narration"}
    assert check(built.script, built.text) == []


# ---- punctuation (TXT-7) --------------------------------------------------------------------


def test_punctuation_is_full_width_in_chinese_and_left_alone_in_numbers_and_english():
    from huashuo.punct import normalize

    zh = {
        "他说:好吧,走!": "他说：好吧，走！",
        "你确定?他问.": "你确定？他问。",
        "ＡＢＣ１２３号房间": "ABC123号房间",
        "价格是3.5元,比分3:2,共1,000人.": "价格是3.5元，比分3:2，共1,000人。",
        "Mr. Smith说:你好.": "Mr. Smith说：你好。",
        "他﹐笑了﹗进学︰明清": "他，笑了！进学：明清",
        "等等...还有。。。和…": "等等……还有……和……",
        "他--沉默了": "他——沉默了",
        "(注:见上文)第一章": "（注：见上文）第一章",
        "“你好,”他说.": "“你好，”他说。",
        "好 , 走 .": "好，走。",
        "网址 www.example.com 可以访问": "网址 www.example.com 可以访问",
        "Hello, world!": "Hello, world!",              # an English line in a Chinese book
        "他说：“来了！”": "他说：“来了！”",               # already clean
    }
    for raw, clean in zh.items():
        assert normalize(raw, "zh") == clean, raw
        assert normalize(clean, "zh") == clean, clean    # idempotent
    assert normalize("Ｈｅｌｌｏ，ｗｏｒｌｄ！", "en") == "Hello, world!"
    assert normalize("It's 3.5, isn't it?", "en") == "It's 3.5, isn't it?"


def test_punctuation_is_cleaned_before_structure_and_dialogue():
    built = build(_book(["第一章:风雪", "他说:\"好吧,走!\"然后走了."]))
    texts = [b["text"] for b in built.script.blocks if b["type"] != "huaben"]
    assert "第一章：风雪" in texts[0]
    assert "“好吧，走！”" in "".join(texts) or "\"好吧，走！\"" in "".join(texts)
    assert "".join(texts).endswith("然后走了。") and built.text.count("：") == 2


# ---- web-novel noise (TXT-5) ---------------------------------------------------------------


def test_web_novel_noise_lines_are_kept_but_not_read():
    from huashuo.punct import normalize
    from huashuo.structure import is_noise

    noise = ["求月票！求推荐票！", "跪求收藏~", "本章完", "（未完待续。）", "（本章未完，请翻页）", "第十章完",
             "PS：今天加更三章，感谢大家！", "作者有话说：", "www.example-novel.com 最新章节免费阅读",
             "天才一秒记住本站地址", "求订阅求打赏", "www.biquge.cc"]
    story = ["他求了三天，才借到一张月票。", "本章完全是他的回忆。", "“求求你，别走！”", "未完待续的故事总让人牵挂。",
             "PS4是他最喜欢的游戏机。", "他打开了www.baidu.com。"]
    assert all(is_noise(normalize(t, "zh"), "zh") for t in noise)
    assert not any(is_noise(normalize(t, "zh"), "zh") for t in story)
    assert not is_noise("Chapter end", "en")


def test_noise_in_a_web_novel_is_skipped_and_counted():
    from helpers import WEBNOVEL_TXT
    from huashuo.ingest import Book, Section

    paras = [Paragraph(t.strip("　")) for t in WEBNOVEL_TXT.split("\n") if t.strip()]
    built = build(Book("剑来长安", "青衫客", "zh", [Section(paras)], "txt"))
    noise = [b["text"] for b in built.script.blocks if b.get("reason") == "noise"]
    assert noise == ["求月票！求推荐票！", "本章完", "（本章未完，请翻页）", "www.example-novel.com 最新章节免费阅读"]
    assert all(t in built.text for t in noise)             # kept in the text, just not read


def test_numbers_the_tts_misreads_get_a_reading():
    from huashuo.structure import speech_cleanup

    assert speech_cleanup("今天最低-5℃。", "zh") == "今天最低零下5℃。"
    assert speech_cleanup("温度是-3.5度", "zh") == "温度是零下3.5度"
    assert speech_cleanup("电话010-12345678。", "zh") == "电话010 12345678。"
    assert speech_cleanup("他是No.1。", "zh") == "他是第1。"
    for fine in ("要走3-5天", "日期2026-09-25", "Piano.1", "他-说"):
        assert speech_cleanup(fine, "zh") is None
