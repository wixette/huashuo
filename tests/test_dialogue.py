import pytest

from helpers import DIALOGUE_TXT
from huashuo.dialogue import quote_spans, split_block
from huashuo.huaben import check
from huashuo.ingest import Book, Paragraph, Section
from huashuo.structure import build


@pytest.mark.parametrize("text, quotes", [
    ("他说：“你好。”然后走了。", ["“你好。”"]),
    ("“后来呢？”“后来打折了腿了。”", ["“后来呢？”", "“后来打折了腿了。”"]),
    ("「他说『快跑』就跑了」她笑道。", ["「他说『快跑』就跑了」"]),
    ('"店家,来一壶热酒。"他把雪抖落。', ['"店家,来一壶热酒。"']),
    ("“这是一段很长的话，没有结束", ["“这是一段很长的话，没有结束"]),
    ("没有引号。", []),
])
def test_chinese_quote_spans(text, quotes):
    assert [text[a:b] for a, b in quote_spans(text, "zh")] == quotes


def test_english_quote_spans():
    text = 'He said, "Come in." She didn’t move. “Now,” he added.'
    assert [text[a:b] for a, b in quote_spans(text, "en")] == ['"Come in."', "“Now,”"]


def test_pieces_are_exact_slices_with_child_ids():
    block = {"id": "c001.p0002", "type": "narration", "text": "他说：“你好。” 然后走了。", "src": [100, 114], "pause_after": 2}
    pieces = split_block(block, "zh")
    assert [(p["id"], p["type"], p["text"]) for p in pieces] == [
        ("c001.p0002.01", "narration", "他说："), ("c001.p0002.02", "dialogue", "“你好。”"),
        ("c001.p0002.03", "narration", "然后走了。")]
    assert pieces[1]["speaker"] == "unknown" and pieces[1]["src"] == [103, 108]
    assert pieces[2]["src"] == [109, 114] and pieces[-1]["pause_after"] == 2


def test_a_paragraph_that_is_one_quote_keeps_its_id():
    block = {"id": "c001.p0003", "type": "narration", "text": "“整段都是话。”", "src": [0, 8]}
    assert split_block(block, "zh") == [{**block, "type": "dialogue", "speaker": "unknown"}]


def test_build_splits_and_keeps_every_character_covered():
    paras = [Paragraph(t) for t in DIALOGUE_TXT.split("\n") if t.strip()]
    built = build(Book("客栈", "", "zh", [Section(paras)], "txt"))
    kinds = [(b["type"], b["text"]) for b in built.script.blocks]
    assert ("dialogue", "“店家，来一壶热酒。”") in kinds and ("narration", "他说。") in kinds
    assert ("dialogue", "“宾至如归”") in kinds          # attribution decides it is not speech
    assert check(built.script, built.text) == []
    unsplit = build(Book("客栈", "", "zh", [Section(paras)], "txt"), split=False)
    assert not any(b["type"] == "dialogue" for b in unsplit.script.blocks)


def test_cleanup_applies_to_pieces():
    built = build(Book("书", "", "zh", [Section([Paragraph("第一章 起"), Paragraph("他说：“ぇ你好。”")])], "txt"))
    quote = next(b for b in built.script.blocks if b["type"] == "dialogue")
    assert quote["say"] == "“你好。”" and quote["text"] == "“ぇ你好。”"


def test_british_single_quotes_in_english_books():
    from huashuo.dialogue import quote_spans, split_blocks, uses_single_quotes

    t = "‘Hello,’ she said. ‘Don’t go!’"
    assert [t[a:b] for a, b in quote_spans(t, "en", single=True)] == ["‘Hello,’", "‘Don’t go!’"]
    assert quote_spans("The boys’ toys were O’Brien’s.", "en", single=True) == []
    t = "‘I said “no”,’ he replied, ‘and I meant it.’"
    assert [t[a:b] for a, b in quote_spans(t, "en", single=True)] == ["‘I said “no”,’", "‘and I meant it.’"]

    british = ["‘Where are you going?’ asked Tom.", "‘Home,’ said Ann. ‘It’s late.’", "The dogs’ barking stopped.",
               "‘Wait for me!’"]
    blocks = [{"id": f"c001.p{i:04d}", "type": "narration", "text": t, "src": [0, len(t)]} for i, t in enumerate(british)]
    assert uses_single_quotes(british)
    kinds = [(b["type"], b["text"]) for b in split_blocks(blocks, "en")]
    assert ("dialogue", "‘Home,’") in kinds and ("dialogue", "‘It’s late.’") in kinds
    assert ("narration", "The dogs’ barking stopped.") in kinds and ("dialogue", "‘Wait for me!’") in kinds

    american = ['"Where are you going?" asked Tom.', "“Home,” said Ann. “It’s late.”",
                "Ann’s ‘friend’ laughed."]
    assert not uses_single_quotes(american)                   # ‘ ’ stay scare quotes there
