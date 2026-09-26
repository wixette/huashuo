"""The pronunciation dictionary (PRON-1, PRON-4): `pron.txt` in the work directory.

One entry per line, 「词语 = 读法」. The reading is either other characters that sound
right (重楼 = 虫楼) or pinyin with tone marks (单于 = chán yú), which Qwen3-TTS reads as
Chinese syllables (experiments/m5_reading_check.py). Tone digits are turned into marks,
because the TTS reads a digit as a number (ma3 -> 「马三」).

Readings are applied when units are planned, not written into the script: the book's
text never changes, an edit to pron.txt takes effect on the next synth without
re-importing, only the units containing the word are synthesized again (their text
changes, so does their cache key), and the ASR check still compares against the words on
the page.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

TEMPLATE = """\
# Pronunciation dictionary: one "word = reading" per line, for the whole book. A reading is
# other characters that sound right, or pinyin with tone marks (tone digits are converted).
# Synthesize again after editing: only the units containing these words are redone.
#
# 单于 = chán yú
# 尉迟恭 = yù chí gōng
# 重楼 = 虫楼
"""

_MARKS = {"a": "āáǎà", "e": "ēéěè", "i": "īíǐì", "o": "ōóǒò", "u": "ūúǔù", "ü": "ǖǘǚǜ"}
_SYLLABLE = re.compile(r"([a-zA-ZüÜvV]+)([1-5])")


def tone_marks(reading: str) -> str:
    """chan2 yu2 -> chán yú; lv4 -> lǜ; ma5 (neutral) -> ma."""
    def mark(match: re.Match) -> str:
        syllable = match.group(1).replace("v", "ü").replace("V", "Ü")
        tone = int(match.group(2))
        if tone == 5:
            return syllable
        lower = syllable.lower()
        if "a" in lower:
            at = lower.index("a")
        elif "e" in lower:
            at = lower.index("e")
        elif "ou" in lower:
            at = lower.index("o")
        else:
            at = max((i for i, ch in enumerate(lower) if ch in "iouü"), default=-1)
            if at < 0:
                return match.group(0)
        marked = _MARKS[lower[at]][tone - 1]
        return syllable[:at] + (marked.upper() if syllable[at].isupper() else marked) + syllable[at + 1:]
    return _SYLLABLE.sub(mark, reading)


@dataclass
class Dictionary:
    entries: dict[str, str] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)   # lines that could not be read

    def __post_init__(self) -> None:
        keys = sorted(self.entries, key=len, reverse=True)  # longest match first
        self._pattern = re.compile("|".join(map(re.escape, keys))) if keys else None

    def mapped(self, convert) -> "Dictionary":
        """The same entries with their words passed through `convert` too (a Traditional
        book read from Simplified matches entries written either way)."""
        entries = dict(self.entries)
        for word, reading in self.entries.items():
            entries.setdefault(convert(word), reading)
        return Dictionary(entries, self.problems)

    def apply(self, text: str) -> str:
        if self._pattern is None:
            return text
        return self._pattern.sub(lambda m: self.entries[m.group(0)], text)

    def matches(self, texts) -> dict[str, int]:
        """How often each entry occurs in the given texts (to spot typos)."""
        counts = {key: 0 for key in self.entries}
        if self._pattern is not None:
            for text in texts:
                for m in self._pattern.finditer(text):
                    counts[m.group(0)] += 1
        return counts


def load(path: Path) -> Dictionary:
    if not path.is_file():
        return Dictionary()
    entries, problems = {}, []
    for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = re.split(r"=|\t|→", line, maxsplit=1)
        word, reading = (parts[0].strip(), parts[1].strip()) if len(parts) == 2 else (line, "")
        if not word or not reading:
            problems.append(f"{path.name}:{number}: expected 'word = reading', got {line!r}")
            continue
        entries[word] = tone_marks(reading)
    return Dictionary(entries, problems)
