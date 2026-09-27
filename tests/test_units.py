from huashuo.huaben import Script
from huashuo.units import plan, split_long

CAST = {"version": 1, "narrator": {"voice": "preset:serena"}, "characters": {}}
HEADER = {"type": "huaben", "version": 1, "language": "zh"}


def script(*specs):
    blocks = []
    for n, (kind, text, *extra) in enumerate(specs):
        block = {"id": f"b{n}", "type": kind, "text": text}
        if extra:
            block.update(extra[0])
        blocks.append(block)
    return Script(HEADER, blocks)


def test_a_unit_is_at_most_one_paragraph_and_never_crosses_boundaries():
    """Paragraphs are never joined: the pause between them is ours, not the model's
    (一条被洗澡水拍死的鱼: a six-paragraph unit rushed, its paragraph pauses vanishing)."""
    s = script(("chapter", "第一章", {"level": 1}), ("narration", "甲" * 60 + "。"),
               ("narration", "乙" * 60 + "。"), ("narration", "丙" * 60 + "。"),
               ("break", "***"), ("narration", "丁。"), ("skip", "广告"), ("narration", "戊。"))
    p = plan(s, CAST, "zh")
    kinds = [(u.kind, len(u.text), u.after) for u in p.units]
    assert kinds == [("title", 3, "title"), ("body", 61, "paragraph"), ("body", 61, "paragraph"),
                     ("body", 61, "break"), ("body", 2, "paragraph"), ("body", 2, "end")]
    assert p.units[1].block_ids == ["b1"]


def test_long_paragraph_splits_at_sentences_then_clauses():
    text = "一二三四五。" * 30 + "长" * 50 + "，" + "句" * 50 + "。"
    pieces = split_long(text, 60, "zh")
    assert all(len(p) <= 60 for p in pieces) and "".join(pieces) == text
    p = plan(script(("chapter", "章", {"level": 1}), ("narration", text)), CAST, "zh", max_chars=60)
    assert [u.after for u in p.units[1:]][-2:] == ["sentence", "end"]



def test_split_pieces_are_balanced_not_a_scrap_at_the_end():
    text = "这是一句二十个字左右的普通叙述文字啊。" * 8 + "好。"          # 162 characters
    pieces = split_long(text, 150, "zh")
    assert len(pieces) == 2 and min(map(len, pieces)) >= 60 and "".join(pieces) == text


def test_a_paragraph_in_one_voice_is_cut_at_sentence_ends_not_where_a_quote_starts():
    """With one voice (--single-voice, or a narrator quoting themselves) the narration and
    quotes of a paragraph are one group; the cut must not fall at 「……笑道，」|「“……”」."""
    c = "c001.p0001"
    s = script(("chapter", "章", {"level": 1}),
               ("narration", "他在门口站了很久，雨一直没停。" * 9 + "他回头看了一眼，笑道，", {"id": f"{c}.01"}),
               ("dialogue", "“你们先走，我随后就到。”", {"id": f"{c}.02", "speaker": "林渊"}),
               ("narration", "说完他转身进了屋。", {"id": f"{c}.03"}))
    cast = {**CAST, "characters": {"林渊": {"voice": "library:zh/young_man"}}}
    body = [u for u in plan(s, cast, "zh", single_voice=True).units if u.kind == "body"]
    assert len({u.voice for u in body}) == 1 and all(len(u.text) <= 150 for u in body)
    assert all(u.text.endswith(("。", "”")) for u in body)                    # whole sentences
    assert not any(u.text.endswith("笑道，") for u in body)
    multi = [u for u in plan(s, cast, "zh").units if u.kind == "body"]
    assert [u.after for u in multi][-3:] == ["turn", "turn", "end"]           # voices still take turns

def test_volume_title_folds_into_its_first_chapter():
    s = script(("chapter", "第一卷 起", {"level": 1}), ("chapter", "第一章 甲", {"level": 2}),
               ("narration", "正文。"), ("chapter", "第二章 乙", {"level": 2}), ("narration", "正文。"))
    p = plan(s, CAST, "zh")
    assert [(c.title, c.first_unit) for c in p.chapters] == [("第一卷 起 · 第一章 甲", 0), ("第一卷 起 · 第二章 乙", 3)]
    assert [u.text for u in p.units[:2]] == ["第一卷 起", "第一章 甲"]


def test_chapter_with_nothing_to_read_is_dropped():
    s = script(("chapter", "书名", {"level": 1}), ("skip", "版权所有", {"reason": "copyright"}),
               ("chapter", "第一章", {"level": 1}), ("narration", "正文。"))
    p = plan(s, CAST, "zh")
    assert [c.title for c in p.chapters] == ["第一章"] and p.units[0].text == "第一章"
    assert all(u.chapter == 0 for u in p.units)


def test_say_voice_override_titles_off_and_pause_after():
    s = script(("chapter", "第一章", {"level": 1}), ("narration", "单于来了。", {"say": "禅于来了。", "pause_after": 3}),
               ("narration", "下一段。"))
    p = plan(s, CAST, "zh", read_titles=False, voice_override="preset:vivian")
    assert [u.text for u in p.units] == ["禅于来了。", "下一段。"]
    assert p.units[0].pause_override == 3.0 and {u.voice for u in p.units} == {"preset:vivian"}
    assert p.chapters[0].first_unit == 0


def test_english_joins_with_spaces():
    s = Script({**HEADER, "language": "en"}, [
        {"id": "c", "type": "chapter", "text": "Chapter 1", "level": 1},
        {"id": "c1.p1.01", "type": "narration", "text": "It rained,"},
        {"id": "c1.p1.02", "type": "narration", "text": "he left."}])
    assert plan(s, CAST, "en").units[1].text == "It rained, he left."


def test_voice_change_inside_a_paragraph_is_a_turn():
    cast = {**CAST, "characters": {"林渊": {"voice": "preset:uncle_fu"}}}
    s = script(("chapter", "第一章", {"level": 1}),
               ("narration", "他推开门，说：", {"id": "c1.p1.01"}),
               ("dialogue", "“来一壶热酒。”", {"id": "c1.p1.02", "speaker": "林渊"}),
               ("narration", "他坐下了。", {"id": "c1.p1.03"}),
               ("narration", "下一段。", {"id": "c1.p2"}))
    p = plan(s, cast, "zh")
    assert [u.after for u in p.units] == ["title", "turn", "turn", "paragraph", "end"]
    assert p.units[2].voice == "preset:uncle_fu" and p.units[3].text == "他坐下了。" and p.units[4].text == "下一段。"


def test_emotion_becomes_instruct_and_can_be_switched_off():
    cast = {**CAST, "characters": {"林渊": {"voice": "preset:uncle_fu"}}}
    s = script(("chapter", "第一章", {"level": 1}),
               ("dialogue", "“来一壶热酒。”", {"id": "c1.p1.01", "speaker": "林渊"}),
               ("dialogue", "“快点！”", {"id": "c1.p1.02", "speaker": "林渊", "emotion": "用不耐烦的语气说"}),
               ("narration", "下一段。", {"id": "c1.p2"}))
    p = plan(s, cast, "zh")
    assert [(u.text, u.instruct) for u in p.units[1:3]] == [("“来一壶热酒。”", None), ("“快点！”", "用不耐烦的语气说")]
    assert p.units[1].after == "sentence"                 # same speaker: not a turn of voice
    off = plan(s, cast, "zh", emotions=False)
    assert off.units[1].text == "“来一壶热酒。”“快点！”" and off.units[1].instruct is None
