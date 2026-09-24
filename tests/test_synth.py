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
