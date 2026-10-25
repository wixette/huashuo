"""Characters the TTS model does not know, read through a homophone (PRON-5).

Qwen3-TTS reads characters directly, with no pinyin step, so it can only read a character
it met in training. Its tokenizer shows which ones it did not: a common character is one
token, an unknown one is split into several byte tokens. Split characters are read at
random: 笹 in 笹垣 (白夜行) came out as 33 different syllables in about 40 places, never
tì, while rare characters that have their own token (筱, 冢, 菅, 阪) were read right every
time. Written as a common character of the same sound (替垣), 19 of 24 takes said tì
(docs/plan-v0.2.0.md §2).

So every split character in the text is replaced, when units are planned, by a common
homophone: its reading from pypinyin (笹 -> tì), then the most frequent single-token
character with exactly that reading and no other (替). A reading with no such homophone is
written in pinyin with tone marks, which the TTS also reads (16 of 24). This works like an
entry in pron.txt, and an entry there wins (笹 = 笹 keeps a character as it is). The book's
text, the script and the ASR comparison are unchanged: the recognizer hears 替, which
sounds like the 笹 on the page.

The table of split characters and the homophones ship as qwen3_chars.json, generated from
the model's tokenizer (`python -m huashuo.rare TOKENIZER_DIR`); a test checks it against
the tokenizer when the model is downloaded.
"""

from __future__ import annotations

import json
import re
import sys
from functools import lru_cache
from pathlib import Path

TABLE = Path(__file__).with_name("qwen3_chars.json")
# The Han blocks the table covers: CJK Unified Ideographs, Extension A, the compatibility
# ideographs and Extensions B to F. Characters elsewhere are not Han and never replaced.
BLOCKS = ((0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xF900, 0xFAFF), (0x20000, 0x2EBEF))
_HAN = re.compile("[" + "".join(f"{chr(a)}-{chr(b)}" for a, b in BLOCKS) + "]")


@lru_cache(maxsize=1)
def _table() -> tuple[list[tuple[int, int]], dict[str, str]]:
    data = json.loads(TABLE.read_text(encoding="utf-8"))
    ranges = [tuple(int(x, 16) for x in (r.split("-") * 2)[:2]) for r in data["unknown"].split()]
    return ranges, data["homophones"]


def unknown(ch: str) -> bool:
    """Whether the TTS tokenizer splits this Han character."""
    import bisect

    ranges, _ = _table()
    code = ord(ch)
    i = bisect.bisect_right(ranges, (code, sys.maxunicode)) - 1
    return i >= 0 and ranges[i][0] <= code <= ranges[i][1]


def reading(ch: str) -> str | None:
    """The pypinyin reading of a character, with its tone mark (笹 -> tì), or None."""
    from pypinyin import Style, pinyin

    found = pinyin(ch, style=Style.TONE, errors=lambda s: [None] * len(s))[0][0]
    return found if found and found != ch else None


def homophone(ch: str) -> tuple[str, str] | None:
    """(what to read instead, the reading): 笹 -> (替, tì); without a homophone, the reading
    itself. None when pypinyin does not know the character either."""
    sound = reading(ch)
    if sound is None:
        return None
    return _table()[1].get(sound, sound), sound


def replacements(texts) -> dict[str, tuple[str, str, int]]:
    """Every unknown character in the texts: {character: (replacement, reading, count)}."""
    counts: dict[str, int] = {}
    for text in texts:
        for ch in _HAN.findall(text):
            counts[ch] = counts.get(ch, 0) + 1
    found = {}
    for ch, n in counts.items():
        if unknown(ch) and (sub := homophone(ch)) is not None:
            found[ch] = (*sub, n)
    return found


# --------------------------------------------------------------------------------------
# Generating the table from the tokenizer
# --------------------------------------------------------------------------------------


def _gb2312_level1(ch: str) -> bool:
    try:
        first = ch.encode("gb2312")[0]
    except UnicodeEncodeError:
        return False
    return 0xB0 <= first <= 0xD7


def build(tokenizer) -> dict:
    """The table for a Hugging Face tokenizer: the ranges of split characters, and for every
    toned syllable the GB2312 level-1 character that is one token, has that one reading in
    pypinyin and the lowest token id (BPE gives frequent strings the lower ids)."""
    from pypinyin import Style, pinyin

    def token_ids(ch: str) -> list[int]:
        return tokenizer.encode(ch, add_special_tokens=False)

    ranges: list[list[int]] = []
    for lo, hi in BLOCKS:
        for code in range(lo, hi + 1):
            if len(token_ids(chr(code))) > 1:
                if ranges and ranges[-1][1] == code - 1:
                    ranges[-1][1] = code
                else:
                    ranges.append([code, code])
    best: dict[str, tuple[int, str]] = {}
    for code in range(0x4E00, 0xA000):
        ch = chr(code)
        if not _gb2312_level1(ch):
            continue
        ids = token_ids(ch)
        sounds = pinyin(ch, style=Style.TONE, heteronym=True)[0]
        if len(ids) != 1 or len(sounds) != 1:
            continue
        if sounds[0] not in best or ids[0] < best[sounds[0]][0]:
            best[sounds[0]] = (ids[0], ch)
    unknown_text = " ".join(f"{a:X}" if a == b else f"{a:X}-{b:X}" for a, b in ranges)
    return {"model": getattr(tokenizer, "name_or_path", ""), "unknown": unknown_text,
            "homophones": {sound: ch for sound, (_, ch) in sorted(best.items())}}


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: python -m huashuo.rare TOKENIZER_DIR   (the TTS model's snapshot directory)")
        return 2
    from transformers import AutoTokenizer

    table = build(AutoTokenizer.from_pretrained(argv[0]))
    # A Hugging Face cache path names the model: .../models--mlx-community--Qwen3-TTS-.../snapshots/<hash>
    named = re.search(r"models--([^/]+?)--([^/]+)/snapshots", str(Path(argv[0]).resolve()))
    table["model"] = f"{named.group(1)}/{named.group(2)}" if named else table["model"]
    TABLE.write_text(json.dumps(table, ensure_ascii=False, indent=0) + "\n", encoding="utf-8")
    print(f"wrote {TABLE}: {len(table['unknown'].split())} ranges, {len(table['homophones'])} homophones")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
