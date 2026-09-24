import numpy as np

from huashuo.audio import LIMITER_CEILING_DB, limit
from huashuo.engines.fake import FakeEngine
from huashuo.huaben import Script
from huashuo.m4b import BookInfo, ffmetadata
from huashuo.post import LEAD_IN, PAUSES, layout, unit_at
from huashuo.synth import synthesize, unit_key
from huashuo.units import plan
from huashuo.workdir import Workdir

CAST = {"version": 1, "narrator": {"voice": "preset:serena"},
        "characters": {"林渊": {"voice": "preset:uncle_fu"}}}


def test_limiter_only_touches_spikes():
    sr = 24000
    t = np.arange(sr) / sr
    speech = 0.3 * np.sin(2 * np.pi * 200 * t).astype(np.float32)
    spiky = speech.copy()
    spiky[12000:12010] = 1.2
    out = limit(spiky, sr)
    assert np.abs(out).max() <= 10 ** (LIMITER_CEILING_DB / 20) + 1e-6
    assert np.allclose(out[:9000], spiky[:9000])           # untouched away from the spike
    assert limit(speech, sr) is speech                      # nothing to do, no copy


def _timeline(tmp_path, blocks):
    script = Script({"type": "huaben", "version": 1, "language": "zh"}, blocks)
    p = plan(script, CAST, "zh")
    wd = Workdir(tmp_path / "book.huashuo")
    engine = FakeEngine()
    synthesize(p.units, engine, wd, "zh", show_progress=False)
    keys = [unit_key(u, engine.identity(), "zh") for u in p.units]
    return p, layout(p, keys, wd)


def test_pauses_follow_boundaries_and_overrides(tmp_path):
    p, tl = _timeline(tmp_path, [
        {"id": "c001", "type": "chapter", "text": "第一章", "level": 1},
        {"id": "c001.p0001.01", "type": "narration", "text": "他推开门，说："},
        {"id": "c001.p0001.02", "type": "dialogue", "text": "“店家，来一壶热酒。”", "speaker": "林渊"},
        {"id": "c001.p0002", "type": "narration", "text": "雪下了整整一夜。", "pause_after": 2.5},
        {"id": "c002", "type": "chapter", "text": "第二章", "level": 1},
        {"id": "c002.p0001", "type": "narration", "text": "天亮了。"}])
    assert [u.after for u in p.units] == ["title", "turn", "paragraph", "chapter_end", "title", "end"]
    sr = tl.sample_rate
    gaps = [(b.start - (a.start + a.trim[1] - a.trim[0])) / sr for a, b in zip(tl.placed, tl.placed[1:])]
    assert gaps[0] == PAUSES["title"] and gaps[1] == PAUSES["turn"] and gaps[2] == PAUSES["paragraph"]
    assert abs(gaps[3] - 2.5) < 1e-3                        # pause_after overrides "chapter_end"
    assert tl.chapters[0][1] == 0 and tl.chapters[0][2] == tl.chapters[1][1] == tl.placed[4].start
    assert tl.chapters[1][2] == tl.total
    assert tl.placed[0].start == int(LEAD_IN * sr)


def test_unit_at_maps_times_to_units(tmp_path):
    _, tl = _timeline(tmp_path, [
        {"id": "c001", "type": "chapter", "text": "第一章", "level": 1},
        {"id": "c001.p0001", "type": "narration", "text": "第一段。", "pause_after": 1.0},  # its own unit
        {"id": "c001.p0002", "type": "narration", "text": "第二段。"}])
    sr = tl.sample_rate
    assert unit_at(tl, 0.0) == 0                             # the lead-in belongs to the first unit
    second = tl.placed[1]
    in_pause = (second.start + second.trim[1] - second.trim[0]) / sr + 0.1
    assert unit_at(tl, in_pause) == 1                        # a pause belongs to the unit before it
    assert unit_at(tl, tl.total / sr - 0.01) == 2
    assert unit_at(tl, tl.total / sr + 1) is None and unit_at(tl, -1) is None


def test_ffmetadata_escapes_special_characters():
    info = BookInfo(title="书名=一;二#三\\四", author="作者\n换行", language="zh")
    text = ffmetadata(info, [("第一章 = 开始; #1", 0, 24000)], 24000)
    assert r"title=书名\=一\;二\#三\\四" in text
    assert "artist=作者\\\n换行" in text
    assert r"title=第一章 \= 开始\; \#1" in text and "TIMEBASE=1/24000" in text


def test_fades_silence_both_ends_and_leave_the_middle():
    from huashuo.audio import fade

    sr = 24000
    audio = np.ones(sr, dtype=np.float32)
    out = fade(audio, sr)
    assert out[0] == 0.0 and out[-1] == 0.0 and out[sr // 2] == 1.0
    assert out[int(0.005 * sr)] == 1.0 and out[-int(0.015 * sr) - 1] == 1.0
    assert np.all(np.diff(out[: int(0.005 * sr)]) > 0) and audio[0] == 1.0      # input untouched
    assert len(fade(np.ones(10, dtype=np.float32), sr)) == 10                  # shorter than a fade
