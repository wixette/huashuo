from collections import Counter

from huashuo.casting import cast_voices, conversations
from huashuo.library import Voice

NARRATOR = "preset:serena"
POOL = [Voice("preset:serena", "zh", "female", "young_adult"),
        Voice("lib:young_man", "zh", "male", "young_adult"), Voice("lib:young_man2", "zh", "male", "young_adult"),
        Voice("lib:old_man", "zh", "male", "elderly"), Voice("lib:girl", "zh", "female", "child"),
        Voice("lib:young_woman", "zh", "female", "young_adult"), Voice("lib:old_woman", "zh", "female", "elderly")]


def ch(gender, age, lines):
    return {"gender": gender, "age": age, "lines": lines}


def test_voices_match_gender_and_age_and_avoid_the_narrator():
    chars = {"林渊": ch("male", "young_adult", 40), "苏晚晴": ch("female", "young_adult", 30),
             "老者": ch("male", "elderly", 10), "小妹": ch("female", "child", 5)}
    voices = cast_voices(chars, NARRATOR, "zh", pool=POOL)
    assert voices == {"林渊": "lib:young_man", "苏晚晴": "lib:young_woman", "老者": "lib:old_man", "小妹": "lib:girl"}
    assert NARRATOR not in voices.values()


def test_main_characters_never_share():
    chars = {f"男{i}": ch("male", "young_adult", 50 - i) for i in range(3)}
    voices = cast_voices(chars, NARRATOR, "zh", pool=POOL, main=3)
    assert len(set(voices.values())) == 3                      # the third takes the nearest unused voice


def test_minor_characters_share_but_not_with_whom_they_talk():
    chars = {"主角": ch("male", "young_adult", 50), "甲": ch("male", "young_adult", 3), "乙": ch("male", "young_adult", 2)}
    blocks = [{"type": "chapter"}, {"type": "dialogue", "speaker": "甲"}, {"type": "narration"},
              {"type": "dialogue", "speaker": "乙"}, {"type": "dialogue", "speaker": "甲"}]
    talks = conversations(blocks)
    assert talks == Counter({frozenset(("甲", "乙")): 2})
    voices = cast_voices(chars, NARRATOR, "zh", talks, pool=POOL, main=1)
    assert voices["甲"] != voices["乙"] and voices["主角"] not in (voices["甲"], voices["乙"])


def test_first_person_narrator_keeps_the_narrator_voice():
    voices = cast_voices({"我": ch("male", "young_adult", 99), "他": ch("male", "young_adult", 5)}, NARRATOR, "zh", pool=POOL)
    assert voices["我"] == NARRATOR and voices["他"] == "lib:young_man"


def test_user_choices_are_fixed_and_not_reused_by_main_characters():
    chars = {"林渊": ch("male", "young_adult", 40), "程远": ch("male", "young_adult", 30)}
    voices = cast_voices(chars, NARRATOR, "zh", fixed={"程远": "lib:young_man"}, pool=POOL)
    assert voices["程远"] == "lib:young_man" and voices["林渊"] == "lib:young_man2"


def test_deterministic_and_graceful_when_voices_run_out():
    chars = {f"男{i}": ch("male", "young_adult", 20 - i) for i in range(12)}
    first = cast_voices(chars, NARRATOR, "zh", pool=POOL)
    assert first == cast_voices(dict(reversed(list(chars.items()))), NARRATOR, "zh", pool=POOL)
    assert set(first) == set(chars) and NARRATOR not in first.values()
    assert cast_voices(chars, NARRATOR, "zh", pool=[]) == {n: NARRATOR for n in chars}


def test_suggestions_decide_main_characters_within_the_rules():
    chars = {"诗人": ch("male", "middle_aged", 50), "老板": ch("male", "middle_aged", 30)}
    pool = [Voice("preset:serena", "zh", "female", "young_adult"),
            Voice("lib:jovial", "zh", "male", "middle_aged"), Voice("lib:bookish", "zh", "male", "young_adult"),
            Voice("lib:woman", "zh", "female", "middle_aged")]
    plain = cast_voices(chars, NARRATOR, "zh", pool=pool)
    assert plain["诗人"] == "lib:jovial"                       # by age alone
    picked = cast_voices(chars, NARRATOR, "zh", pool=pool, suggested={"诗人": "lib:bookish", "老板": "lib:jovial"})
    assert picked == {"诗人": "lib:bookish", "老板": "lib:jovial"}
    bad = cast_voices(chars, NARRATOR, "zh", pool=pool,       # wrong gender, the narrator, a duplicate
                      suggested={"诗人": "lib:woman", "老板": NARRATOR})
    assert bad["诗人"] != "lib:woman" and bad["老板"] != NARRATOR and bad["诗人"] != bad["老板"]
    user = cast_voices(chars, NARRATOR, "zh", pool=pool, fixed={"诗人": "lib:jovial"},
                       suggested={"诗人": "lib:bookish", "老板": "lib:jovial"})
    assert user["诗人"] == "lib:jovial" and user["老板"] != "lib:jovial"   # the user's choice wins


def test_unknown_age_means_an_adult_voice():
    pool = [Voice("preset:serena", "zh", "female", "young_adult"), Voice("lib:boy", "zh", "male", "child"),
            Voice("lib:teen", "zh", "male", "teen"), Voice("lib:man", "zh", "male", "middle_aged")]
    chars = {"主任": {"gender": "male", "age": "unknown", "lines": 3},
             "主任二": {"gender": "male", "age": "unknown", "lines": 2}}
    voices = cast_voices(chars, NARRATOR, "zh", pool=pool, main=0)
    assert set(voices.values()) == {"lib:man"}                 # shared, rather than a child's voice


def test_a_crowded_adult_voice_beats_a_wrong_age_one():
    pool = [Voice("lib:woman", "zh", "female", "young_adult"), Voice("lib:teen", "zh", "female", "teen"),
            Voice("lib:granny", "zh", "female", "elderly")]
    chars = {f"路人{i}": {"gender": "female", "age": "unknown", "lines": 20 - i} for i in range(12)}
    assert set(cast_voices(chars, NARRATOR, "zh", pool=pool, main=0).values()) == {"lib:woman"}


def test_equally_good_voices_share_the_crowd():
    pool = [Voice("lib:a", "zh", "male", "young_adult"), Voice("lib:b", "zh", "male", "middle_aged")]
    chars = {f"路人{i}": {"gender": "male", "age": "unknown", "lines": 30 - i} for i in range(20)}
    counts = Counter(cast_voices(chars, NARRATOR, "zh", pool=pool, main=0).values())
    assert counts == {"lib:a": 10, "lib:b": 10}
