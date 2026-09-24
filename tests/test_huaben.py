import pytest

from huashuo.huaben import HuabenError, Script, check, dumps, loads, merge, sha256_text

TEXT = "第一章 起风\n风从北边来。\n他走了出去。\n"
HEADER = {"type": "huaben", "version": 1, "title": "T", "author": "", "language": "zh",
          "text": "text.txt", "text_sha256": sha256_text(TEXT)}


def blocks():
    return [
        {"id": "c001", "type": "chapter", "text": "第一章 起风", "level": 1, "src": [0, 6]},
        {"id": "c001.p0001", "type": "narration", "text": "风从北边来。", "src": [7, 13]},
        {"id": "c001.p0002", "type": "narration", "text": "他走了出去。", "src": [14, 20]},
    ]


def test_round_trip_keeps_unknown_fields_and_field_order():
    script = Script(dict(HEADER), blocks())
    script.blocks[1]["note"] = "我的批注"
    script.blocks[1] = {"src": script.blocks[1]["src"], "note": "我的批注", "type": "narration",
                        "id": "c001.p0001", "text": "风从北边来。"}
    text = dumps(script)
    assert '"note": "我的批注"' in text                    # not ASCII-escaped
    line = text.splitlines()[2]
    assert line.index('"id"') < line.index('"type"') < line.index('"text"') < line.index('"src"')
    again = loads(text)
    assert again.blocks == script.blocks and again.header == script.header


def test_comments_and_blank_lines_are_ignored_and_lines_tracked():
    text = dumps(Script(dict(HEADER), blocks())).replace("\n", "\n\n// note\n", 1)
    script = loads(text)
    assert len(script.blocks) == 3 and script.lines[0] == 4


@pytest.mark.parametrize("content, message", [
    ('{"id": "x", "type": "narration"}\n', "first record must be the header"),
    ('{"type": "huaben", "version": 99}\n', "newer than this program"),
    ('{"type": "huaben", "version": 1}\n{"type": "huaben", "version": 1}\n', "second header"),
    ('{"type": "huaben", "version": 1}\nnot json\n', "not valid JSON"),
])
def test_unparseable_files(content, message):
    with pytest.raises(HuabenError, match=message):
        loads(content)


def test_sound_script_passes():
    assert check(Script(dict(HEADER), blocks()), TEXT) == []


def test_check_finds_each_invariant():
    bad = blocks()
    bad[2]["id"] = "c001.p0001"                       # I2 duplicate
    bad[1]["text"] = "风从南边来。"                     # I4 text changed
    bad[2].update(text="他走了", src=[14, 17])            # I5: 「出去。」 now in no block
    bad.insert(0, {"id": "x", "type": "narration", "text": "序"})  # I6: read before the chapter
    codes = {p.code for p in check(Script(dict(HEADER), bad), TEXT)}
    assert {"I2", "I4", "I5", "I6"} <= codes
    changed = check(Script(dict(HEADER), blocks()), TEXT.replace("风", "雨"))
    assert "I8" in {p.code for p in changed}


def test_say_changes_what_is_read_without_breaking_invariants():
    edited = blocks()
    edited[1]["say"] = "风从北边吹来。"
    assert check(Script(dict(HEADER), edited), TEXT) == []


def _script(texts, header=None):
    """A script whose blocks are numbered in order, as the builder numbers them."""
    blocks, offset = [], 0
    for n, (kind, text) in enumerate(texts):
        blocks.append({"id": "c000" if n == 0 else f"c000.p{n:04d}", "type": kind, "text": text,
                       "src": [offset, offset + len(text)]})
        offset += len(text) + 1
    return Script(dict(header or HEADER), blocks)


BOOK = [("chapter", "第一章"), ("narration", "甲段。"), ("narration", "乙段。"), ("narration", "丙段。")]


def test_merge_keeps_user_edits_additions_and_deletions():
    base = _script(BOOK)
    current = _script(BOOK, dict(HEADER, title="我改的书名"))
    current.blocks[1]["say"] = "甲段（改读）。"                          # edited field
    del current.blocks[3]                                               # deleted 丙段
    current.blocks.insert(2, {"id": "c000.x1", "type": "narration", "text": "（插入）"})  # added
    new = _script(BOOK)
    new.blocks[0]["level"] = 1                                          # machine adds a field

    result = merge(base, current, new)
    assert [b["text"] for b in result.script.blocks] == ["第一章", "甲段。", "（插入）", "乙段。"]
    assert result.script.blocks[1]["say"] == "甲段（改读）。" and result.script.blocks[0]["level"] == 1
    assert result.script.header["title"] == "我改的书名"
    assert any("deletion" in line for line in result.report) and not result.orphans


def test_edits_follow_their_paragraph_when_the_source_gains_or_loses_one():
    base = _script(BOOK)
    current = _script(BOOK)
    current.blocks[2]["say"] = "乙段（改读）。"                          # edit 乙段 (c000.p0002)
    grown = _script([BOOK[0], ("narration", "新插入的一段。"), *BOOK[1:]])
    result = merge(base, current, grown)
    by_text = {b["text"]: b for b in result.script.blocks}
    assert by_text["乙段。"].get("say") == "乙段（改读）。"                # not moved onto 甲段
    assert "say" not in by_text["甲段。"] and by_text["乙段。"]["id"] == "c000.p0003"
    assert "now c000.p0003" in " ".join(result.report)
    shrunk = _script([BOOK[0], BOOK[2], BOOK[3]])                        # 甲段 removed upstream
    result = merge(base, current, shrunk)
    assert {b["text"]: b.get("say") for b in result.script.blocks}["乙段。"] == "乙段（改读）。"


def test_edits_of_a_paragraph_that_disappeared_are_reported_not_misplaced():
    base, current = _script(BOOK), _script(BOOK)
    current.blocks[2]["say"] = "乙段（改读）。"
    changed = _script([BOOK[0], BOOK[1], ("narration", "乙段被作者改写了。"), BOOK[3]])
    result = merge(base, current, changed)
    assert all("say" not in b for b in result.script.blocks)
    (orphan,) = result.orphans
    assert orphan["edits"] == {"say": "乙段（改读）。"} and orphan["text"] == "乙段。"


def test_edits_carry_onto_a_paragraph_split_into_dialogue():
    para = "他说：“你好。”然后走了。"
    base = _script([BOOK[0], ("narration", para), ("narration", "下一段。")])
    current = _script([BOOK[0], ("narration", para), ("narration", "下一段。")])
    current.blocks[1].update(type="skip", reason="user", pause_after=2.0)
    new = Script(dict(HEADER), [dict(base.blocks[0]),
        {"id": "c000.p0001.01", "type": "narration", "text": "他说：", "src": [4, 7]},
        {"id": "c000.p0001.02", "type": "dialogue", "text": "“你好。”", "speaker": "他", "src": [7, 12]},
        {"id": "c000.p0001.03", "type": "narration", "text": "然后走了。", "src": [12, 17]},
        dict(base.blocks[2])])
    result = merge(base, current, new)
    pieces = [b for b in result.script.blocks if b["id"].startswith("c000.p0001.")]
    assert [b["type"] for b in pieces] == ["skip", "skip", "skip"]
    assert pieces[-1]["pause_after"] == 2.0 and pieces[0]["reason"] == "user"
    current.blocks[1] = {**base.blocks[1], "say": "改读"}
    result = merge(base, current, new)
    assert result.orphans and result.orphans[0]["edits"] == {"say": "改读"}


def test_untouched_blocks_the_machine_drops_go_away():
    base, current = _script(BOOK), _script(BOOK)
    result = merge(base, current, _script(BOOK[:3]))
    assert [b["text"] for b in result.script.blocks] == ["第一章", "甲段。", "乙段。"] and not result.orphans


def test_merge_without_base_keeps_the_file():
    current = Script(dict(HEADER), blocks())
    current.blocks[1]["say"] = "x"
    result = merge(None, current, Script(dict(HEADER), blocks()))
    assert result.script.blocks[1]["say"] == "x" and result.report
