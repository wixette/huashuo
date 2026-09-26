"""Traditional and Simplified Chinese (OpenCC, Apache-2.0).

Qwen3-TTS misreads many Traditional characters that it reads correctly in Simplified form:
the same modern short story, 熱包子 / 热包子, read 雜 裏 髮 纔 賀 wrongly only in the
Traditional copy (design doc §5.12). So the text sent to the engine is converted when a
book is written in Traditional characters; the book's own text, the script and the chapter
titles stay as they are. OpenCC's plain t2s is used: it converts characters (with the
phrases that decide a character, 瞭解 -> 了解 but 瞭望 stays), never vocabulary, unlike the
regional-phrase modes (tw2sp would turn 注销 into 登出).
"""

from __future__ import annotations

import re
from functools import lru_cache

# A Traditional text changes about a quarter of its characters under t2s (熱包子: 24.6%),
# a Simplified one almost none (0.00%).
TRADITIONAL_SHARE = 0.05
_HAN = re.compile(r"[一-鿿]")


@lru_cache(maxsize=1)
def _converter():
    import opencc
    return opencc.OpenCC("t2s")


def to_simplified(text: str) -> str:
    return _converter().convert(text)


def is_traditional(text: str, sample: int = 20000) -> bool:
    """Whether a Chinese text is written in Traditional characters."""
    han = _HAN.findall(text[:sample])
    if not han:
        return False
    simplified = to_simplified("".join(han))
    changed = sum(a != b for a, b in zip(han, simplified))
    return changed >= TRADITIONAL_SHARE * len(han)
