import pytest

from huashuo.cast import CastError, default_cast, load_cast, merge_cast


def cast(**characters):
    return {"version": 1, "narrator": {"voice": "preset:serena"}, "characters": characters}


def test_default_follows_language():
    assert default_cast("zh")["narrator"]["voice"] == "library:zh/narrator_female"   # the packaged library's narrator
    assert default_cast("en")["narrator"]["voice"] == "preset:ryan"


def test_characters_merge_one_by_one():
    base = cast(林渊={"gender": "male", "voice": "library:zh/v5"}, 苏晚晴={"gender": "female"}, 老者={"gender": "male"})
    current = cast(林渊={"gender": "male", "voice": "preset:uncle_fu"},   # user changed the voice
                   老者={"gender": "male"},                               # user removed 苏晚晴
                   路人={"gender": "unknown"})                            # user added 路人
    new = cast(林渊={"gender": "male", "voice": "library:zh/v5", "lines": 42},
               苏晚晴={"gender": "female"}, 老者={"gender": "male", "age": "elderly"}, 店小二={"gender": "male"})
    merged, report = merge_cast(base, current, new)
    chars = merged["characters"]
    assert chars["林渊"] == {"gender": "male", "voice": "preset:uncle_fu", "lines": 42}
    assert "苏晚晴" not in chars and chars["老者"]["age"] == "elderly"
    assert "路人" in chars and "店小二" in chars
    assert any("removal" in line for line in report)


def test_unchanged_cast_is_not_wiped_by_a_reimport():
    full = cast(林渊={"voice": "library:zh/v5"})
    merged, _ = merge_cast(full, full, full)
    assert merged["characters"] == full["characters"]


def test_narrator_voice_edit_survives():
    base, new = cast(), cast()
    current = cast(); current["narrator"]["voice"] = "preset:vivian"
    assert merge_cast(base, current, new)[0]["narrator"]["voice"] == "preset:vivian"


@pytest.mark.parametrize("content, message", [
    ("{not json", "not valid JSON"),
    ('{"characters": {}}', "narrator"),
    ('{"narrator": {"voice": "preset:serena"}, "characters": []}', "keyed by character name"),
    ('{"narrator": {"voice": "preset:serena"}, "characters": {"甲": {"voice": 3}}}', "invalid voice"),
])
def test_bad_cast_files_give_readable_errors(tmp_path, content, message):
    path = tmp_path / "cast.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(CastError, match=message):
        load_cast(path, "zh")


def test_missing_cast_uses_the_language_default(tmp_path):
    assert load_cast(tmp_path / "cast.json", "en")["narrator"]["voice"] == "preset:ryan"
