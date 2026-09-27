"""Library voices through import, the engines, the cache key and the audition command."""

import json

import numpy as np
import pytest

from helpers import DIALOGUE_TXT, ScriptedLLM
from huashuo.attribution import LLMConfig
from huashuo.engines import EngineError
from huashuo.engines.fake import FakeEngine
from huashuo.pipeline import LLMOptions, import_book
from huashuo.synth import unit_key, unit_keys
from huashuo.units import Unit
from huashuo.workdir import Workdir

LOCAL = LLMConfig("test-model", "http://localhost:9/v1", "x", 1.0, 1.0, max_cost=1.0)


@pytest.fixture
def book(tmp_path):
    path = tmp_path / "客栈.txt"
    path.write_text(DIALOGUE_TXT, encoding="utf-8")
    return path


def _import(book, llm=None):
    return import_book(book, Workdir.for_input(book),
                       llm=LLMOptions(config_override=LOCAL, model_override=(llm or ScriptedLLM()).model()))


def _voices(book):
    cast = json.loads(Workdir.for_input(book).cast.read_text(encoding="utf-8"))
    return cast["narrator"]["voice"], {n: c["voice"] for n, c in cast["characters"].items()}


def test_import_casts_voices_by_profile(book, tiny_library):
    _import(book)
    narrator, voices = _voices(book)
    assert narrator == "preset:serena"
    assert voices == {"林渊": "library:zh/young_man", "老者": "library:zh/old_man"}


def test_a_voice_chosen_by_hand_survives_reimport(book, tiny_library):
    _import(book)
    wd = Workdir.for_input(book)
    cast = json.loads(wd.cast.read_text(encoding="utf-8"))
    cast["characters"]["老者"]["voice"] = "preset:uncle_fu"
    wd.cast.write_text(json.dumps(cast, ensure_ascii=False), encoding="utf-8")
    for _ in range(3):                                  # every re-import, not just the next one
        _import(book)
        assert _voices(book)[1] == {"林渊": "library:zh/young_man", "老者": "preset:uncle_fu"}


def test_casting_avoids_the_narrator_the_user_chose(book, tiny_library):
    _import(book)
    wd = Workdir.for_input(book)
    cast = json.loads(wd.cast.read_text(encoding="utf-8"))
    cast["narrator"]["voice"] = "library:zh/old_man"
    wd.cast.write_text(json.dumps(cast, ensure_ascii=False), encoding="utf-8")
    for _ in range(3):
        _import(book)
        narrator, voices = _voices(book)
        assert narrator == "library:zh/old_man" and narrator not in voices.values()


def test_llm_suggestions_cast_main_characters_within_the_rules(book, tiny_library):
    # The suggestions swap what the rules alone would pick (林渊 young_man, 老者 old_man).
    llm = ScriptedLLM(voice_choices={"林渊": "library:zh/old_man", "老者": "library:zh/young_man"})
    _import(book, llm)
    assert llm.calls["voices"] == 1
    assert _voices(book)[1] == {"林渊": "library:zh/old_man", "老者": "library:zh/young_man"}
    # Without a key the answer comes from the cache, and casting is the same.
    import_book(book, Workdir.for_input(book), llm=LLMOptions())
    assert _voices(book)[1] == {"林渊": "library:zh/old_man", "老者": "library:zh/young_man"}


def test_failed_suggestions_are_a_note_not_a_stopped_attribution(book, tiny_library):
    llm = ScriptedLLM(voice_choices={"林渊": "library:zh/nobody"})      # not an offered voice
    result = _import(book, llm)
    assert result.llm.stopped is None and result.llm.suggestions_failed
    assert _voices(book)[1] == {"林渊": "library:zh/young_man", "老者": "library:zh/old_man"}


def test_fake_engine_knows_library_voices(tiny_library):
    engine = FakeEngine()
    engine.check_voice("library:zh/old_man")
    assert engine.voice_identity("library:zh/old_man").startswith("library:zh/old_man@")
    assert engine.voice_identity("preset:serena") == "preset:serena"
    with pytest.raises(EngineError, match="unknown voice"):
        engine.check_voice("library:zh/nobody")


def test_cache_key_follows_the_library_vector_but_not_for_presets(tiny_library):
    engine = FakeEngine()
    preset = Unit("body", "你好。", "preset:serena", None, ["b"], 0)
    lib = Unit("body", "你好。", "library:zh/young_man", None, ["b"], 0)
    assert unit_keys([preset], engine, "zh") == [unit_key(preset, engine.identity(), "zh")]
    before = unit_keys([lib], engine, "zh")
    np.save(tiny_library / "zh" / "young_man.npy", np.zeros(2048, dtype=np.float32))
    assert unit_keys([lib], engine, "zh") != before


# ---- VoiceInjector, against a stand-in for the mlx-audio model ------------------------------


def test_injector_answers_only_its_rows_and_only_while_the_prompt_is_built(monkeypatch):
    mx = pytest.importorskip("mlx.core")
    pytest.importorskip("mlx_audio")
    import mlx_audio.version

    from huashuo.engines import qwen3_voices

    monkeypatch.setattr(qwen3_voices, "SUPPORTED_MLX_AUDIO", mlx_audio.version.__version__)
    width = 8

    class Talker:
        def get_input_embeddings(self):
            return lambda ids: mx.zeros((1, ids.shape[1], width))

    class Config:
        class talker_config:
            spk_id = {"serena": 3066}

    class Model:
        def __init__(self):
            self.talker, self.config, self.supported_speakers = Talker(), Config(), ["serena"]

        def _prepare_generation_inputs(self, row):
            return self.talker.get_input_embeddings()(mx.array([[row]]))

    model = Model()
    injector = qwen3_voices.VoiceInjector(model)
    vector = np.arange(width, dtype=np.float32)
    name = injector.speaker("library:zh/a", vector)
    assert name == "huashuo_0" and model.config.talker_config.spk_id[name] == 3000
    assert name in model.supported_speakers and injector.speaker("library:zh/a", vector) == name
    assert np.array_equal(np.array(model._prepare_generation_inputs(3000)).reshape(-1), vector)
    assert not np.array(model._prepare_generation_inputs(3066)).any()        # presets untouched
    assert not np.array(model.talker.get_input_embeddings()(mx.array([[3000]]))).any()  # only inside prepare
    with pytest.raises(EngineError, match="expects 8"):
        injector.speaker("library:zh/b", np.ones(4, dtype=np.float32))


def test_injector_skips_rows_that_presets_use(monkeypatch):
    """Presets are scattered over the table (uncle_fu is row 3010); a book with more than ten
    library voices once collided with it."""
    mx = pytest.importorskip("mlx.core")
    pytest.importorskip("mlx_audio")
    import mlx_audio.version

    from huashuo.engines import qwen3_voices

    monkeypatch.setattr(qwen3_voices, "SUPPORTED_MLX_AUDIO", mlx_audio.version.__version__)

    class Talker:
        def get_input_embeddings(self):
            return lambda ids: mx.zeros((1, ids.shape[1], 4))

    class Config:
        class talker_config:
            spk_id = {"uncle_fu": 3010, "other": 3003}
            vocab_size = 3072

    class Model:
        def __init__(self):
            self.talker, self.config, self.supported_speakers = Talker(), Config(), ["uncle_fu", "other"]

        def _prepare_generation_inputs(self, row):
            return self.talker.get_input_embeddings()(mx.array([[row]]))

    model = Model()
    injector = qwen3_voices.VoiceInjector(model)
    names = [injector.speaker(f"library:zh/v{i}", np.full(4, i, dtype=np.float32)) for i in range(20)]
    rows = [model.config.talker_config.spk_id[n] for n in names]
    assert len(set(rows)) == 20 and 3010 not in rows and 3003 not in rows and max(rows) < 3072
    assert model.config.talker_config.spk_id["uncle_fu"] == 3010


def test_injector_refuses_other_mlx_audio_versions(monkeypatch):
    pytest.importorskip("mlx.core")
    pytest.importorskip("mlx_audio")
    from huashuo.engines import qwen3_voices

    monkeypatch.setattr(qwen3_voices, "SUPPORTED_MLX_AUDIO", "0.0.0")
    with pytest.raises(EngineError, match="need mlx-audio 0.0.0"):
        qwen3_voices.VoiceInjector(object())


# ---- audition ------------------------------------------------------------------------------


@pytest.mark.ffmpeg
def test_audition_one_chapter_per_character(book, tiny_library, capsys):
    from huashuo.cli import main
    from huashuo.m4b import probe

    _import(book)
    assert main(["audition", str(book), "--engine", "fake"]) == 0
    chapters = [c["tags"]["title"] for c in probe(book.with_name("客栈.audition.m4b"))["chapters"]]
    assert chapters == ["林渊 (library:zh/young_man)", "老者 (library:zh/old_man)"]
    out = capsys.readouterr().out
    assert "“店家，来一壶热酒。”" in out or "“从哪儿来不重要。”" in out   # a real line of theirs
    assert main(["audition", str(book), "--engine", "fake", "--character", "老者"]) == 0
    assert len(probe(book.with_name("客栈.audition.m4b"))["chapters"]) == 1
    with pytest.raises(SystemExit, match="not in cast.json"):
        main(["audition", str(book), "--engine", "fake", "--character", "路人"])


@pytest.mark.ffmpeg
def test_audition_the_whole_library(book, tiny_library):
    from huashuo.cli import main
    from huashuo.m4b import probe

    _import(book)
    assert main(["audition", str(book), "--engine", "fake", "--library"]) == 0
    titles = [c["tags"]["title"] for c in probe(book.with_name("客栈.audition.m4b"))["chapters"]]
    assert len(titles) == 2 and not any("preset" in t for t in titles)          # the voices casting uses


def test_voices_lists_without_a_model(tiny_library, capsys):
    from huashuo.cli import main

    assert main(["voices"]) == 0
    out = capsys.readouterr().out
    assert "library:zh/old_man" in out and "preset:dylan" in out and "only when named in cast.json" in out


# ---- English books: the narrator reads everyone until there is an English library (Q19) ----

ENGLISH_TXT = """The Inn

Chapter 1
Snow fell all night. Lin pushed the door open.
"A jug of hot wine," he said.
The old man looked up. "You are not from around here, are you?"
"Where I come from does not matter," said Lin.
"""
ENGLISH_ANSWERS = {'"A jug of hot wine,"': "Lin", '"You are not from around here, are you?"': "Old man",
                   '"Where I come from does not matter,"': "Lin"}
ENGLISH_CAST = [{"op": "insert", "name": "Lin", "gender": "male", "age": "young_adult", "description": "swordsman"},
                {"op": "insert", "name": "Old man", "gender": "male", "age": "elderly", "description": "innkeeper"}]


def test_english_books_are_read_by_the_narrator(tmp_path, tiny_library):
    import json as _json

    (tiny_library / "presets.json").write_text(_json.dumps({
        "serena": {"language": "zh", "gender": "female", "age": "young_adult"},
        "ryan": {"language": "en", "gender": "male", "age": "young_adult", "traits": ["narrator"]},
        "aiden": {"language": "en", "gender": "male", "age": "young_adult"}}), encoding="utf-8")
    import huashuo.library as library
    library._load.cache_clear()
    path = tmp_path / "inn.txt"
    path.write_text(ENGLISH_TXT, encoding="utf-8")
    llm = ScriptedLLM(cast_ops=ENGLISH_CAST, answers=ENGLISH_ANSWERS, voice_choices={"Lin": "preset:aiden"})
    import_book(path, Workdir.for_input(path), llm=LLMOptions(config_override=LOCAL, model_override=llm.model()))
    wd = Workdir.for_input(path)
    cast = _json.loads(wd.cast.read_text(encoding="utf-8"))
    assert cast["narrator"]["voice"] == "preset:ryan"
    assert {n: c["voice"] for n, c in cast["characters"].items()} == {"Lin": "preset:ryan", "Old man": "preset:ryan"}
    assert llm.calls["voices"] == 0 and llm.calls["speakers"] >= 1      # speakers still attributed
    cast["characters"]["Old man"]["voice"] = "preset:aiden"              # a voice named by hand still counts
    wd.cast.write_text(_json.dumps(cast), encoding="utf-8")
    import_book(path, wd, llm=LLMOptions(config_override=LOCAL, model_override=ScriptedLLM(
        cast_ops=ENGLISH_CAST, answers=ENGLISH_ANSWERS).model()))
    cast = _json.loads(wd.cast.read_text(encoding="utf-8"))
    assert cast["characters"]["Old man"]["voice"] == "preset:aiden" and cast["characters"]["Lin"]["voice"] == "preset:ryan"


def test_english_opening_and_closing(tmp_path, tiny_library):
    from huashuo.pipeline import load_project, make_plan

    path = tmp_path / "inn.txt"
    path.write_text(ENGLISH_TXT, encoding="utf-8")
    import_book(path, Workdir.for_input(path), title="The Inn", author="Anon")
    p = make_plan(load_project(Workdir.for_input(path)), credit=True)
    assert p.units[0].text == "The Inn, by Anon." and p.units[-1].text == "The End. This audiobook was made with Huashuo."


def test_first_person_narrator_keeps_the_narrator_voice(tmp_path, tiny_library):
    from helpers import FIRST_PERSON_ANSWERS, FIRST_PERSON_CAST, FIRST_PERSON_TXT

    path = tmp_path / "归乡.txt"
    path.write_text(FIRST_PERSON_TXT, encoding="utf-8")
    llm = ScriptedLLM(cast_ops=FIRST_PERSON_CAST, answers=FIRST_PERSON_ANSWERS, voice_choices={"林默": "library:zh/young_man"})
    import_book(path, Workdir.for_input(path), llm=LLMOptions(config_override=LOCAL, model_override=llm.model()))
    narrator, voices = _voices(path)
    assert voices["林默"] == narrator and voices["周强"] != narrator


def test_the_chosen_title_names_the_opening_section(tmp_path, tiny_library):
    from huashuo.pipeline import load_project, make_plan

    path = tmp_path / "lane.txt"
    path.write_text("The Lane\n\nChapter 1\nThe rain had not stopped for three days.\n", encoding="utf-8")
    import_book(path, Workdir.for_input(path), title="The Lane", author="M. Hart")
    p = make_plan(load_project(Workdir.for_input(path)))
    assert [u.text for u in p.units][:2] == ["The Lane, by M. Hart.", "Chapter 1"]   # the title line is not read twice
    assert [c.title for c in p.chapters] == ["Chapter 1"]


# ---- choosing the narrator (packaged library) --------------------------------------------------


def _first_person_book(tmp_path):
    from helpers import FIRST_PERSON_TXT

    path = tmp_path / "归乡.txt"
    path.write_text(FIRST_PERSON_TXT, encoding="utf-8")
    return path


def _import_fp(path, **kwargs):
    from helpers import FIRST_PERSON_ANSWERS, FIRST_PERSON_CAST

    llm = ScriptedLLM(cast_ops=FIRST_PERSON_CAST, answers=FIRST_PERSON_ANSWERS)
    return import_book(path, Workdir.for_input(path), llm=LLMOptions(config_override=LOCAL, model_override=llm.model()),
                       **kwargs)


def test_narrator_is_chosen_per_book_and_the_users_choice_wins(tmp_path):
    path = _first_person_book(tmp_path)
    result = _import_fp(path)
    assert result.narrator == "library:zh/narrator_male" and "first-person narrator 林默" in result.narrator_reason
    assert _voices(path)[1]["林默"] == "library:zh/narrator_male"          # 我 reads with the narration
    # A narrator edited by hand is kept on re-import.
    wd = Workdir.for_input(path)
    cast = json.loads(wd.cast.read_text(encoding="utf-8"))
    cast["narrator"]["voice"] = "library:zh/mid_woman_calm"
    wd.cast.write_text(json.dumps(cast, ensure_ascii=False), encoding="utf-8")
    result = _import_fp(path)
    assert result.narrator == "library:zh/mid_woman_calm" and result.narrator_reason == "your choice in cast.json"
    voices = _voices(path)[1]
    assert voices["林默"] == "library:zh/mid_woman_calm" and voices["周强"] != "library:zh/mid_woman_calm"
    # --narrator given now replaces it, is remembered, and auto hands it back to the rule.
    assert _import_fp(path, narrator="female").narrator == "library:zh/narrator_female"
    assert _import_fp(path).narrator == "library:zh/narrator_female"
    assert _import_fp(path, narrator="auto").narrator == "library:zh/narrator_male"
    with pytest.raises(Exception, match="unknown voice"):
        _import_fp(path, narrator="library:zh/nobody")


def test_a_narrator_left_as_it_was_moves_to_the_new_default(tmp_path):
    """Books imported when serena was the default and never changed get the narrator rule."""
    path = _first_person_book(tmp_path)
    _import_fp(path)
    wd = Workdir.for_input(path)
    for f in (wd.cast, wd.cast_base):
        cast = json.loads(f.read_text(encoding="utf-8"))
        cast["narrator"]["voice"] = "preset:serena"                         # as an older version wrote it
        f.write_text(json.dumps(cast, ensure_ascii=False), encoding="utf-8")
    assert _import_fp(path).narrator == "library:zh/narrator_male"


# ---- --single-voice / --multi-voice -------------------------------------------------------

REMOTE = LLMConfig("test-model", "https://llm.example.invalid/v1", "x", 1.0, 1.0, max_cost=1.0)


def _plan_voices(book):
    from huashuo.pipeline import load_project, make_plan
    return {u.voice for u in make_plan(load_project(Workdir.for_input(book))).units}


def test_single_voice_needs_no_llm_and_the_narrator_reads_everything(book, tiny_library):
    llm, asked = ScriptedLLM(), []
    result = import_book(book, Workdir.for_input(book), voices="single",
                         llm=LLMOptions(config_override=REMOTE, model_override=llm.model(),
                                        confirm=lambda m: asked.append(m) or True))
    assert result.voices == "single" and not asked and llm.calls == {"cast": 0, "speakers": 0, "voices": 0}
    narrator, _ = _voices(book)
    assert _plan_voices(book) == {narrator}
    assert not (Workdir.for_input(book).root / "review.txt").exists()


def test_the_choice_is_remembered_and_switching_back_reuses_the_answers(book, tiny_library):
    _import(book)                                            # multi-voice: speakers found and cached
    narrator, characters = _voices(book)
    assert len(_plan_voices(book)) > 1
    cast = json.loads(Workdir.for_input(book).cast.read_text(encoding="utf-8"))
    cast["characters"]["老者"]["voice"] = "library:zh/old_man"        # a hand-picked voice
    Workdir.for_input(book).cast.write_text(json.dumps(cast, ensure_ascii=False), encoding="utf-8")

    llm = ScriptedLLM()
    import_book(book, Workdir.for_input(book), voices="single",
                llm=LLMOptions(config_override=LOCAL, model_override=llm.model()))
    assert _plan_voices(book) == {narrator} and sum(llm.calls.values()) == 0   # even the hand-picked one
    import_book(book, Workdir.for_input(book), llm=LLMOptions(config_override=LOCAL, model_override=llm.model()))
    assert _plan_voices(book) == {narrator}                  # remembered: no flag keeps it single

    import_book(book, Workdir.for_input(book), voices="multi",
                llm=LLMOptions(config_override=LOCAL, model_override=llm.model()))
    assert sum(llm.calls.values()) == 0                      # every answer came from the cache
    assert _voices(book)[1]["老者"] == "library:zh/old_man" and len(_plan_voices(book)) > 1


def test_make_takes_the_same_switch(tmp_path, capsys):
    from huashuo.cli import main

    path = tmp_path / "客栈.txt"
    path.write_text(DIALOGUE_TXT, encoding="utf-8")
    assert main(["make", str(path), "--single-voice", "--engine", "fake"]) == 0
    out = capsys.readouterr().out
    assert "single voice: the narrator reads everything" in out and "single voice, no LLM needed" in out
    with pytest.raises(SystemExit, match="single voice"):                      # nobody to audition
        main(["audition", str(path), "--engine", "fake"])


# ---- genders the text does not state ------------------------------------------------------------


def _unknown_gender_cast():
    from helpers import FIRST_PERSON_CAST
    return [{**op, "gender": "unknown"} if op["name"] == "林默" else op for op in FIRST_PERSON_CAST]


def test_an_unstated_gender_is_inferred_and_marked_as_a_guess(tmp_path, capsys):
    """林默, the first-person narrator, is never called 他: pass 1 says unknown. The gender
    call guesses male, so the book gets the male narrator, and cast.json says it was a guess."""
    from helpers import FIRST_PERSON_ANSWERS

    path = _first_person_book(tmp_path)
    llm = ScriptedLLM(cast_ops=_unknown_gender_cast(), answers=FIRST_PERSON_ANSWERS, genders={"林默": "male"})
    result = import_book(path, Workdir.for_input(path), llm=LLMOptions(config_override=LOCAL, model_override=llm.model()))
    assert llm.calls["genders"] == 1
    prompt = next(p for p in llm.prompts if "需要推断性别的角色" in p)
    cast_list, asked = prompt.split("需要推断性别的角色")
    assert "- 周强" in cast_list                                              # the relations, as context
    assert "- 林默" in asked and "- 周强" not in asked and "你怎么来了" in asked       # only the unknown, with a line
    character = json.loads(Workdir.for_input(path).cast.read_text(encoding="utf-8"))["characters"]["林默"]
    assert character["gender"] == "male" and character["gender_inferred"] is True
    assert result.narrator == "library:zh/narrator_male" and "(inferred)" in result.narrator_reason


def test_a_book_attributed_before_pays_only_for_the_gender_call(tmp_path):
    from helpers import FIRST_PERSON_ANSWERS

    path = _first_person_book(tmp_path)
    wd = Workdir.for_input(path)
    options = dict(cast_ops=_unknown_gender_cast(), answers=FIRST_PERSON_ANSWERS, genders={"林默": "male"})
    remote = LLMConfig("test-model", "https://llm.example.invalid/v1", "x", 1.0, 1.0, max_cost=1.0)
    first = ScriptedLLM(**options)
    import_book(path, wd, llm=LLMOptions(config_override=remote, model_override=first.model(), assume_yes=True))
    for cached in (wd.state / "llm-cache").glob("*.json"):                   # as if cached before this change
        if json.loads(cached.read_text(encoding="utf-8"))["stage"] == "genders":
            cached.unlink()
    again, asked = ScriptedLLM(**options), []
    result = import_book(path, wd, llm=LLMOptions(config_override=remote, model_override=again.model(),
                                                  confirm=lambda m: asked.append(m) or True))
    assert "genders the text does not state" in asked[0]
    assert again.calls == {"cast": 0, "speakers": 0, "voices": 0, "genders": 1}
    assert result.narrator == "library:zh/narrator_male"


def test_a_failed_gender_call_keeps_the_rest(tmp_path, monkeypatch):
    from helpers import FIRST_PERSON_ANSWERS
    from huashuo.attribution import LLMError

    def fails(*args, **kwargs):
        raise LLMError("genders call to test-model failed: timeout")
    monkeypatch.setattr("huashuo.attribution.infer_genders", fails)
    path = _first_person_book(tmp_path)
    llm = ScriptedLLM(cast_ops=_unknown_gender_cast(), answers=FIRST_PERSON_ANSWERS)
    result = import_book(path, Workdir.for_input(path), llm=LLMOptions(config_override=LOCAL, model_override=llm.model()))
    assert not result.llm.stopped and "timeout" in result.llm.genders_failed
    cast = json.loads(Workdir.for_input(path).cast.read_text(encoding="utf-8"))["characters"]
    assert cast["林默"]["gender"] == "unknown" and cast["周强"]["voice"]            # speakers and casting done


def test_narrator_auto_hands_a_hand_picked_narrator_back_to_the_rule(tmp_path):
    path = _first_person_book(tmp_path)
    _import_fp(path)
    wd = Workdir.for_input(path)
    cast = json.loads(wd.cast.read_text(encoding="utf-8"))
    cast["narrator"]["voice"] = "library:zh/mid_woman_calm"
    wd.cast.write_text(json.dumps(cast, ensure_ascii=False), encoding="utf-8")
    assert _import_fp(path).narrator == "library:zh/mid_woman_calm"          # kept
    result = _import_fp(path, narrator="auto")
    assert result.narrator == "library:zh/narrator_male" and "first-person" in result.narrator_reason
