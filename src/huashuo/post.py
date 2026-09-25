"""Turn cached units into one continuous, evenly loud programme with pauses (POST-1…7).

Two passes over the units. The first measures each one (audible span, loudness, peak)
and lays out the timeline, which fixes the chapter start times before encoding begins.
The second streams the processed PCM straight into the encoder, so a twenty-hour book
never has to fit in memory. Measurements are stored in each unit's sidecar and reused.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

import numpy as np

from huashuo.audio import TARGET_LUFS, fade, gain_db, limit, loudness, to_pcm16, trim_bounds, true_peak_db
from huashuo.synth import unit_audio
from huashuo.units import Plan
from huashuo.workdir import Workdir, read_json, write_json_atomic

# Pause after each kind of boundary, in seconds (POST-3). Units are trimmed of their own
# leading and trailing silence first, so these are the pauses the listener hears.
PAUSES = {"sentence": 0.45, "turn": 0.45, "paragraph": 0.9, "heading": 1.0, "title": 1.2,
          "break": 1.6, "chapter_end": 2.0, "end": 1.5}
LEAD_IN = 0.5


@dataclass
class Placed:
    key: str
    start: int          # sample offset of the unit's audio in the programme
    trim: tuple[int, int]
    gain: float         # linear
    pause: int          # samples of silence after the unit


@dataclass
class Timeline:
    sample_rate: int
    placed: list[Placed]
    chapters: list[tuple[str, int, int]]   # (title, start sample, end sample)
    total: int

    @property
    def seconds(self) -> float:
        return self.total / self.sample_rate


def _analysis(wd: Workdir, key: str) -> dict:
    """Trim bounds and loudness of one unit, cached in its sidecar."""
    path = wd.units / f"{key}.json"
    meta = read_json(path, {})
    if "post" not in meta:
        audio, sr = unit_audio(wd, key)
        start, end = trim_bounds(audio, sr)
        meta["post"] = {"trim": [int(start), int(end)], "lufs": loudness(audio[start:end], sr),
                        "true_peak": true_peak_db(audio[start:end]), "sample_rate": sr}
        write_json_atomic(path, meta)
    return meta["post"]


def layout(plan: Plan, keys: list[str], wd: Workdir, target_lufs: float = TARGET_LUFS,
           pauses: dict | None = None) -> Timeline:
    pauses = {**PAUSES, **(pauses or {})}
    analyses = [_analysis(wd, k) for k in keys]
    sample_rate = analyses[0]["sample_rate"] if analyses else 24000
    measured = [a["lufs"] for a in analyses if a["lufs"] is not None]
    fallback = float(np.median(measured)) if measured else None

    placed, cursor = [], int(LEAD_IN * sample_rate)
    unit_starts = []
    for unit, key, info in zip(plan.units, keys, analyses):
        start, end = info["trim"]
        gain = gain_db(info["lufs"], fallback, target_lufs)
        pause = unit.pause_override if unit.pause_override is not None else pauses[unit.after]
        placed.append(Placed(key, cursor, (start, end), 10 ** (gain / 20), int(pause * sample_rate)))
        unit_starts.append(cursor)
        cursor += (end - start) + placed[-1].pause

    chapters = []
    for index, chapter in enumerate(plan.chapters):
        begin = unit_starts[chapter.first_unit] if chapter.first_unit < len(unit_starts) else cursor
        if index == 0:
            begin = 0  # the first chapter owns the lead-in
        chapters.append([chapter.title, begin, cursor])
    for current, following in zip(chapters, chapters[1:]):
        current[2] = following[1]
    return Timeline(sample_rate, placed, [tuple(c) for c in chapters], cursor)


def unit_at(timeline: Timeline, seconds: float) -> int | None:
    """Index of the unit heard at `seconds` in the programme. A pause belongs to the unit
    before it; the lead-in belongs to the first unit."""
    sample = int(seconds * timeline.sample_rate)
    if not timeline.placed or not 0 <= sample < timeline.total:
        return None
    for index, item in enumerate(timeline.placed):
        end = item.start + (item.trim[1] - item.trim[0]) + item.pause
        if sample < end:
            return index
    return len(timeline.placed) - 1


def stream(timeline: Timeline, wd: Workdir, block: int = 1 << 16) -> Iterator[bytes]:
    """The whole programme as 16-bit PCM, one unit at a time."""
    yield bytes(2 * int(LEAD_IN * timeline.sample_rate))
    for item in timeline.placed:
        audio, _ = unit_audio(wd, item.key)
        segment = fade(limit(audio[item.trim[0]:item.trim[1]] * item.gain, timeline.sample_rate),
                       timeline.sample_rate)
        for offset in range(0, len(segment), block):
            yield to_pcm16(segment[offset:offset + block])
        yield bytes(2 * item.pause)
