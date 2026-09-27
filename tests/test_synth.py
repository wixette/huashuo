import json

import numpy as np

from huashuo.engines.fake import FakeEngine
from huashuo.synth import duration_problem, reroll, seed_for, synthesize, unit_key
from huashuo.units import Unit
from huashuo.workdir import Workdir


class FakeAsr:
    """Returns scripted transcripts in order; max_cer as the real checker has."""

    def __init__(self, transcripts, max_cer=0.10):
        self.transcripts, self.max_cer, self.calls = list(transcripts), max_cer, 0

    def transcribe(self, audio, sample_rate, language):
        self.calls += 1
        return self.transcripts.pop(0)


TEXT = "雪下了整整一夜，林渊推开客栈的木门。"


def unit(text=TEXT):
    return Unit("body", text, "preset:serena", None, ["c001.p0001"], 0)


def test_duration_checks():
    assert duration_problem(TEXT, 0.0, "zh", None) == "empty audio"
    assert "too slow" in duration_problem(TEXT, 30.0, "zh", None)
    assert "too fast" in duration_problem(TEXT, 1.0, "zh", None)
    assert "ceiling" in duration_problem(TEXT, 320.0, "zh", 327.0)
    assert duration_problem(TEXT, 4.0, "zh", None) is None
    assert duration_problem("好。", 5.0, "zh", None) is None       # too short to judge a rate


def test_asr_retries_keep_the_best_attempt(tmp_path):
    wd = Workdir(tmp_path)
    asr = FakeAsr(["雪下了一夜。", "雪下了整整一夜，林渊推开客栈的门。", "完全不同的一句话。"], max_cer=0.05)
    stats = synthesize([unit()], FakeEngine(), wd, "zh", asr=asr, show_progress=False)
    assert asr.calls == 3 and stats.retried == 2
    meta = json.loads(next(wd.units.glob("*.json")).read_text())
    assert meta["asr"] == "雪下了整整一夜，林渊推开客栈的门。"           # lowest error rate wins
    assert meta["problem"] and meta["problem"].startswith("ASR mismatch")


def test_passing_asr_stops_retrying(tmp_path):
    asr = FakeAsr([TEXT])
    stats = synthesize([unit()], FakeEngine(), Workdir(tmp_path), "zh", asr=asr, show_progress=False)
    assert asr.calls == 1 and stats.retried == 0 and not stats.warnings


def test_cached_units_are_rejudged_with_the_current_rule(tmp_path):
    wd = Workdir(tmp_path)
    synthesize([unit()], FakeEngine(), wd, "zh", asr=FakeAsr(["别的木门。"] * 3), show_progress=False)
    stats = synthesize([unit()], FakeEngine(), wd, "zh", asr=FakeAsr([], max_cer=1.0), show_progress=False)
    assert stats.cached == 1 and not stats.warnings        # a looser rule clears the stored flag
    stats = synthesize([unit()], FakeEngine(), wd, "zh", asr=FakeAsr([], max_cer=0.05), show_progress=False)
    assert len(stats.warnings) == 1                        # and a stricter one raises it again


def test_a_lost_ending_fails_even_under_the_error_threshold(tmp_path):
    text = "这件事我想了很久，还是觉得应该告诉你。"
    asr = FakeAsr(["这件事，我想了很久，还是觉得应该告诉。", text])
    stats = synthesize([unit(text)], FakeEngine(), Workdir(tmp_path), "zh", asr=asr, show_progress=False)
    assert asr.calls == 2 and stats.retried == 1 and not stats.warnings


def test_reroll_uses_fresh_seeds_and_survives_a_cache_wipe(tmp_path):
    wd, engine = Workdir(tmp_path), FakeEngine()
    key = unit_key(unit(), engine.identity(), "zh")
    synthesize([unit()], engine, wd, "zh", show_progress=False)
    assert reroll(wd, key) == 1 and not (wd.units / f"{key}.wav").exists()
    synthesize([unit()], engine, wd, "zh", show_progress=False)
    first = json.loads((wd.units / f"{key}.json").read_text())["seed"]
    assert first == seed_for(key, 0, 1) != seed_for(key, 0, 0)
    (wd.units / f"{key}.wav").unlink(); (wd.units / f"{key}.json").unlink()
    synthesize([unit()], engine, wd, "zh", show_progress=False)
    assert json.loads((wd.units / f"{key}.json").read_text())["seed"] == first


def test_same_input_same_audio(tmp_path):
    a, b = Workdir(tmp_path / "a"), Workdir(tmp_path / "b")
    for wd in (a, b):
        synthesize([unit()], FakeEngine(), wd, "zh", show_progress=False)
    wav = lambda wd: next(wd.units.glob("*.wav")).read_bytes()
    assert wav(a) == wav(b)


def test_asr_skips_units_too_short_to_judge(tmp_path):
    asr = FakeAsr(["爹汉。"] * 3)
    title = Unit("title", "吶喊", "preset:serena", None, ["c001"], 0)
    stats = synthesize([title], FakeEngine(), Workdir(tmp_path), "zh", asr=asr, show_progress=False)
    assert asr.calls == 0 and stats.retried == 0 and not stats.warnings


def test_progress_shows_the_chapter_being_synthesized(capsys):
    from huashuo.synth import Progress

    # Units of chapters 2, 2, 5 (a --chapters run): numbered 1 and 2 of 2.
    p = Progress(3, 30, enabled=False, logged=True, chapters=[(2, 10), (2, 10), (5, 10)])
    assert p.chapter_status() == "chapter 1/2   0%  "
    p.advance(10, 1.0, 1.0)
    assert p.chapter_status() == "chapter 1/2  50%  "
    p.advance(10, 1.0, 1.0)
    assert p.chapter_status() == "chapter 2/2   0%  "
    p.advance(10, 1.0, 1.0)
    out = capsys.readouterr().out
    assert "chapter 1/2 done" in out and "chapter 2/2 done" in out
    single = Progress(1, 10, enabled=False, logged=True, chapters=[(0, 10)])
    assert single.chapter_status() == ""


def test_titles_are_only_checked_for_a_lost_ending(tmp_path):
    title = Unit("title", "《剑来长安》，作者青衫客。", "preset:serena", None, ["opening"], 0)
    asr = FakeAsr(["剑来长安，作者青山客。"])                     # a homophone in a name
    stats = synthesize([title], FakeEngine(), Workdir(tmp_path), "zh", asr=asr, show_progress=False)
    assert asr.calls == 1 and not stats.warnings
    cut = Unit("title", "《剑来长安》，作者青衫客。", "preset:vivian", None, ["opening"], 0)
    asr = FakeAsr(["剑来长安，作者青山"] * 3)
    stats = synthesize([cut], FakeEngine(), Workdir(tmp_path), "zh", asr=asr, show_progress=False)
    assert asr.calls == 3 and "lost ending" in stats.warnings[0]["problem"]


def test_a_unit_that_keeps_losing_its_ending_is_made_in_two_parts(tmp_path):
    """一条被洗澡水拍死的鱼: long units lost their last syllable on every attempt, the last
    sentence on its own never did."""
    text = "我伸手想抓住飘逝的记忆，却只抓到冰冷的墙壁。请将修改过的场景存入大脑。谢谢。"
    cut = "我伸手想抓住飘逝的记忆，却只抓到冰冷的墙壁。请将修改过的场景存入大脑。"
    asr = FakeAsr([cut] * 3 + ["我伸手想抓住飘逝的记忆，却只抓到冰冷的墙壁。请将修改过的场景存入大脑。", "谢谢。"])
    wd = Workdir(tmp_path)
    stats = synthesize([unit(text)], FakeEngine(), wd, "zh", asr=asr, show_progress=False)
    meta = json.loads(next(wd.units.glob("*.json")).read_text())
    assert asr.calls == 5 and meta["split"] is True and meta["problem"] is None and not stats.warnings
    assert meta["asr"].endswith("谢谢。")
    from huashuo.synth import _split_last
    assert _split_last(text) == ("我伸手想抓住飘逝的记忆，却只抓到冰冷的墙壁。请将修改过的场景存入大脑。", "谢谢。")
    assert _split_last("“最高权限。谢谢。”") == ("“最高权限。", "谢谢。”")
    assert _split_last("只有一句话。") is None


def test_the_pace_check_knows_each_voices_usual_pace():
    from huashuo.synth import PaceBook

    book = PaceBook()
    text = "雪" * 60
    assert book.problem("v", text, 10.0, "zh") == (None, 1.0)               # not enough known units yet
    for seconds in (14, 15, 15, 16, 15, 14, 16, 15):                       # about 4 chars/s
        book.add("v", text, seconds, "zh")
    assert book.problem("v", text, 15.0, "zh")[0] is None
    assert "rushed" in book.problem("v", text, 11.0, "zh")[0]               # 5.5 chars/s, 1.36x
    assert "dragging" in book.problem("v", text, 22.0, "zh")[0]
    assert book.problem("other voice", text, 11.0, "zh")[0] is None
    assert book.problem("v", "雪" * 20, 2.0, "zh")[0] is None               # too short to judge


def test_a_rushed_take_is_tried_again(tmp_path):
    """The TTS sometimes reads a whole unit right but much too fast."""
    from huashuo.engines.fake import FakeEngine

    class Moody(FakeEngine):
        def synthesize(self, text, voice, language, instruct, seed):
            audio = super().synthesize(text, voice, language, instruct, seed)
            return audio[: int(len(audio) * 0.7)] if "赶" in text and self.calls == 9 else audio

    wd, engine = Workdir(tmp_path), Moody()
    units = [unit("雪" * 50 + f"{n}。") for n in range(8)] + [unit("赶" * 50 + "。")]
    stats = synthesize(units, engine, wd, "zh", show_progress=False)
    assert stats.retried == 1 and not stats.warnings and engine.calls == 10


def test_the_real_time_factor_counts_only_what_was_synthesized(capsys, monkeypatch):
    """Resuming a book: cached units add audio but took no time; they must not inflate the rtf."""
    from huashuo.synth import Progress

    p = Progress(2, 20, enabled=True, chapters=[(0, 10), (0, 10)])
    p.advance(10, 600.0, None)                   # from the cache: ten minutes of audio, no time
    p.advance(10, 30.0, 10.0)                    # synthesized: 30 s of audio in 10 s
    assert "rtf 3.00x" in capsys.readouterr().out
