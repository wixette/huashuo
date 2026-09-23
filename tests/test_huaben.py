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


def test_merge_keeps_user_edits_additions_and_deletions():
    base = Script(dict(HEADER), blocks())
    current = Script(dict(HEADER, title="我改的书名"), blocks())
    current.blocks[1]["say"] = "风从北边吹来。"                          # edited field
    del current.blocks[2]                                               # deleted block
    current.blocks.insert(1, {"id": "c001.x1", "type": "narration", "text": "（插入）"})  # added
    new = Script(dict(HEADER), blocks())
    new.blocks[1]["text"] = new.blocks[1]["text"]      # machine output unchanged here
    new.blocks[0]["level"] = 2                          # machine changed a field the user did not touch

    merged, report = merge(base, current, new)
    ids = [b["id"] for b in merged.blocks]
    assert ids == ["c001", "c001.x1", "c001.p0001"]
    assert merged.blocks[2]["say"] == "风从北边吹来。"
    assert merged.blocks[0]["level"] == 2
    assert merged.header["title"] == "我改的书名"
    assert any("deletion" in line for line in report)


def test_merge_without_base_keeps_the_file():
    current = Script(dict(HEADER), blocks())
    current.blocks[1]["say"] = "x"
    merged, report = merge(None, current, Script(dict(HEADER), blocks()))
    assert merged.blocks[1]["say"] == "x" and report
