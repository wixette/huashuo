import json

import pytest

from helpers import DIALOGUE_TXT, ScriptedLLM
from huashuo.attribution import Caller, LLMConfig, attribute_script, estimate_cost, llm_config
from huashuo.huaben import check, read_script, write_script
from huashuo.ingest import Book, Paragraph, Section
from huashuo.pipeline import LLMOptions, PipelineError, import_book
from huashuo.structure import build
from huashuo.workdir import Workdir

LOCAL = LLMConfig("test-model", "http://localhost:9/v1", "x", 1.0, 1.0, max_cost=1.0)
REMOTE = LLMConfig("test-model", "https://llm.example.invalid/v1", "x", 1.0, 1.0, max_cost=1.0)


def script():
    paras = [Paragraph(t) for t in DIALOGUE_TXT.split("\n") if t.strip()]
    return build(Book("客栈", "", "zh", [Section(paras)], "txt"))


def speakers(blocks):
    return {b["text"]: b.get("speaker") for b in blocks if b["type"] == "dialogue"}


def test_speakers_cast_and_non_speech_quotes(tmp_path):
    llm = ScriptedLLM()
    built = script()
    result = attribute_script(built.script, "zh", Caller(LOCAL, tmp_path, model=llm.model()))
    assert speakers(result.blocks) == {"“店家，来一壶热酒。”": "林渊", "“客官面生得很，是从北边来的？”": "老者",
                                       "“从哪儿来不重要。”": "林渊"}
    plaque = next(b for b in result.blocks if b["text"] == "“宾至如归”")
    assert plaque["type"] == "narration" and "speaker" not in plaque          # not speech
    assert result.characters["林渊"]["lines"] == 2 and result.characters["老者"]["aliases"] == ["店家"]
    assert all(b.get("conf") == 0.95 for b in result.blocks if b["type"] == "dialogue")
    assert not result.stopped and not result.review
    assert check(type(built.script)(built.script.header, result.blocks), built.text,
                 {"characters": result.characters}) == []


def test_unanswered_quotes_are_asked_again(tmp_path):
    llm = ScriptedLLM(drop_first=2)
    result = attribute_script(script().script, "zh", Caller(LOCAL, tmp_path, model=llm.model()))
    assert "unknown" not in speakers(result.blocks).values()
    assert llm.calls["speakers"] == 1 + 2                     # one batch, then the two missing ones


def test_answers_are_cached(tmp_path):
    first = ScriptedLLM()
    attribute_script(script().script, "zh", Caller(LOCAL, tmp_path, model=first.model()))
    again = ScriptedLLM()
    caller = Caller(LOCAL, tmp_path, model=again.model())
    result = attribute_script(script().script, "zh", caller)
    assert again.calls == {"cast": 0, "speakers": 0} and caller.usage.cached > 0
    assert speakers(result.blocks)["“店家，来一壶热酒。”"] == "林渊"


def test_cache_only_mode_reuses_answers_and_never_calls(tmp_path):
    attribute_script(script().script, "zh", Caller(LOCAL, tmp_path, model=ScriptedLLM().model()))
    offline = Caller(LOCAL, tmp_path, offline=True)
    result = attribute_script(script().script, "zh", offline)
    assert speakers(result.blocks)["“客官面生得很，是从北边来的？”"] == "老者" and not result.stopped
    empty = attribute_script(script().script, "zh", Caller(LOCAL, tmp_path / "empty", offline=True))
    assert set(speakers(empty.blocks).values()) == {"unknown"} and empty.stopped


def test_budget_stops_before_spending_and_keeps_what_was_answered(tmp_path):
    llm = ScriptedLLM()
    poor = LLMConfig("test-model", "http://localhost:9/v1", "x", 1000.0, 1000.0, max_cost=0.01)
    result = attribute_script(script().script, "zh", Caller(poor, tmp_path, model=llm.model()))
    assert llm.calls == {"cast": 0, "speakers": 0}
    assert "cap" in result.stopped and set(speakers(result.blocks).values()) == {"unknown"}
    assert len(result.review) == 4                             # every quote is listed for review


def test_low_confidence_goes_to_review(tmp_path):
    result = attribute_script(script().script, "zh", Caller(LOCAL, tmp_path, model=ScriptedLLM(confidence=0.3).model()))
    assert {r["text"] for r in result.review} == {"“店家，来一壶热酒。”", "“客官面生得很，是从北边来的？”", "“从哪儿来不重要。”"}


def test_config_needs_a_key_and_known_prices(monkeypatch):
    assert llm_config() is None                                # conftest hides every key
    monkeypatch.setenv("HUASHUO_LLM_API_KEY", "sk-test-not-a-real-key")
    assert llm_config().model == "gpt-6-sol" and llm_config().price_in == 2.0
    from huashuo.attribution import LLMError
    with pytest.raises(LLMError, match="no price"):
        llm_config(model="some-new-model")
    assert llm_config(base_url="http://localhost:1234/v1", model="local-model").price_in == 0.0
    assert estimate_cost(300_000, llm_config()) == pytest.approx(3.21, abs=0.05)   # NFR-4: about $3


# ---- through import_book ------------------------------------------------------------


@pytest.fixture
def book(tmp_path):
    path = tmp_path / "客栈.txt"
    path.write_text(DIALOGUE_TXT, encoding="utf-8")
    return path


def test_import_asks_before_sending_the_book_away(book):
    wd = Workdir.for_input(book)
    llm = ScriptedLLM()
    with pytest.raises(PipelineError, match="--yes"):
        import_book(book, wd, llm=LLMOptions(config_override=REMOTE, model_override=llm.model()))
    assert llm.calls == {"cast": 0, "speakers": 0}
    asked = []
    import_book(book, wd, llm=LLMOptions(config_override=REMOTE, model_override=llm.model(),
                                         confirm=lambda m: asked.append(m) or True))
    assert "llm.example.invalid" in asked[0]
    record = json.loads(wd.ingest_record.read_text())
    assert record["llm_consent"] == ["https://llm.example.invalid/v1"] and record["llm_model"] == "test-model"
    import_book(book, wd, llm=LLMOptions(config_override=REMOTE, model_override=ScriptedLLM().model()))   # no second ask


def test_estimate_above_the_cap_stops_before_any_call(book):
    llm = ScriptedLLM()
    pricey = LLMConfig("test-model", "http://localhost:9/v1", "x", 1e6, 1e6, max_cost=1.0)
    with pytest.raises(PipelineError, match="above the"):
        import_book(book, Workdir.for_input(book), llm=LLMOptions(config_override=pricey, model_override=llm.model()))
    assert llm.calls == {"cast": 0, "speakers": 0}


def test_reimport_without_llm_keeps_speakers_and_user_edits(book):
    wd = Workdir.for_input(book)
    import_book(book, wd, llm=LLMOptions(config_override=LOCAL, model_override=ScriptedLLM().model()))
    cast = json.loads(wd.cast.read_text())
    assert set(cast["characters"]) == {"林渊", "老者"}
    script_ = read_script(wd.script)
    target = next(b for b in script_.blocks if b["text"] == "“从哪儿来不重要。”")
    target["speaker"] = "老者"                                   # the user disagrees
    write_script(wd.script, script_)
    result = import_book(book, wd, llm=LLMOptions(enabled=False, model="test-model"))   # --no-llm
    assert result.llm.mode == "cache"
    got = speakers(read_script(wd.script).blocks)
    assert got["“从哪儿来不重要。”"] == "老者" and got["“店家，来一壶热酒。”"] == "林渊"
    assert set(json.loads(wd.cast.read_text())["characters"]) == {"林渊", "老者"}


def test_unknown_speakers_are_written_to_review(book):
    wd = Workdir.for_input(book)
    import_book(book, wd, llm=LLMOptions(config_override=LOCAL,
                                         model_override=ScriptedLLM(answers={}).model()))
    review = (wd.root / "review.txt").read_text(encoding="utf-8")
    assert "“店家，来一壶热酒。”" in review and "unknown" in review
    import_book(book, wd, llm=LLMOptions(config_override=LOCAL, model_override=ScriptedLLM().model()))
    assert (wd.root / "review.txt").exists()        # cached answers are reused: still unknown


def test_without_a_key_the_book_is_read_by_the_narrator(book, capsys):
    from huashuo.cli import main
    assert main(["import", str(book)]) == 0
    assert "no LLM configured" in capsys.readouterr().out
    assert set(speakers(read_script(Workdir.for_input(book).script).blocks).values()) == {"unknown"}
    assert main(["import", str(book), "--no-llm"]) == 0
    assert "no LLM configured" not in capsys.readouterr().out


def test_one_event_loop_serves_every_call(tmp_path):
    """The model's HTTP client binds to the loop it first runs on; every call must share it
    (a real run failed with "Event loop is closed" on the second call before this)."""
    import asyncio

    from pydantic_ai.models.function import FunctionModel

    loops = []
    inner = ScriptedLLM()

    async def remember_loop(messages, info):      # async: runs on the caller's loop, not a thread
        loops.append(asyncio.get_running_loop())
        return inner(messages, info)

    result = attribute_script(script().script, "zh", Caller(LOCAL, tmp_path, model=FunctionModel(remember_loop)))
    assert len(loops) >= 2 and len(set(map(id, loops))) == 1 and not result.stopped
