"""Punctuation clean-up (TXT-7): full-width punctuation in Chinese prose, half-width in
English and inside numbers.

Books converted from other formats mix the two freely (「他说:好吧,走!」, 「ＡＢＣ１２３」,
the small and vertical compatibility forms ﹐︰﹗). The TTS reads them all, but sentence
splitting, title detection and dialogue splitting see a cleaner text. What counts as
Chinese punctuation comes from `zhon` (a curated list of the marks used in Chinese
text); the compatibility forms are unfolded with Unicode NFKC.

Rules, applied to Chinese books:
    - full-width letters and digits become ASCII (ＡＢＣ１２３ -> ABC123)
    - compatibility forms of CJK punctuation become the standard full-width marks
    - an ASCII , ? ! : ; ( ) next to Chinese becomes full-width; a period only when it
      ends a Chinese sentence. Inside numbers (3.5, 3:2, 1,000) and English words
      (Mr. Smith) nothing changes
    - ..., 。。。 and a lone … become ……; -- becomes ——
Quotes are left alone: dialogue splitting handles every quote style (TXT-6).

English books only get the full-width ASCII forms turned back into ASCII.
"""

from __future__ import annotations

import re
import unicodedata

from zhon import hanzi

# Half-width -> full-width, for marks next to Chinese text.
_WIDE = {",": "，", "?": "？", "!": "！", ":": "：", ";": "；", "(": "（", ")": "）", ".": "。"}
# Compatibility forms whose NFKC is ASCII but which were written as CJK marks.
_COMPAT_RANGES = ((0xFE10, 0xFE1F), (0xFE30, 0xFE4F), (0xFE50, 0xFE6F))
_CJK_PUNCT = set(hanzi.punctuation) - {"　"}
# Vertical forms that unfold to several ASCII characters: ︰ -> "..", ︙ -> "...".
_MULTI = {"..": "：", "...": "……"}


def _is_compat(ch: str) -> bool:
    return any(lo <= ord(ch) <= hi for lo, hi in _COMPAT_RANGES)


_HAN = re.compile(f"[{hanzi.characters}]")


def _is_chinese(ch: str) -> bool:
    """A Han character or a Chinese punctuation mark."""
    return bool(ch) and (ch in _CJK_PUNCT or _HAN.match(ch) is not None)


def _unfold(text: str) -> str:
    """Full-width ASCII -> ASCII; compatibility punctuation -> the standard CJK mark."""
    out = []
    for ch in text:
        code = ord(ch)
        if 0xFF10 <= code <= 0xFF19 or 0xFF21 <= code <= 0xFF3A or 0xFF41 <= code <= 0xFF5A:
            out.append(chr(code - 0xFEE0))                 # ０-９ Ａ-Ｚ ａ-ｚ
        elif _is_compat(ch):
            plain = unicodedata.normalize("NFKC", ch)
            out.append(_WIDE.get(plain, plain) if len(plain) == 1 else _MULTI.get(plain, ch))
        else:
            out.append(ch)
    return "".join(out)


_ELLIPSIS = re.compile(r"\.{3,}|。{3,}|…+|(?:\.\s){2,}\.")


def _neighbour(text: str, i: int, step: int) -> str:
    j = i + step
    while 0 <= j < len(text) and text[j] == " ":
        j += step
    return text[j] if 0 <= j < len(text) else ""


def _widen(text: str) -> str:
    chars = list(text)
    for i, ch in enumerate(chars):
        if ch not in _WIDE:
            continue
        before, after = _neighbour(text, i, -1), _neighbour(text, i, 1)
        if before.isascii() and before.isalnum() and after.isascii() and after.isalnum():
            continue                                        # 3.5, 3:2, 1,000, e.g, Mr.Smith
        if ch == "(":
            chinese = _is_chinese(after) or _is_chinese(before)
        elif ch == ")":
            chinese = _is_chinese(before) or _is_chinese(after)
        elif ch == ".":
            # Only a sentence end: after Chinese, and followed by the end, a quote, a
            # bracket or more Chinese; never after a Latin letter or digit (Mr., 3.).
            chinese = _is_chinese(before) and before not in _CJK_PUNCT and (after == "" or _is_chinese(after)
                                                                           or after in "\"'”’)")
        else:
            chinese = _is_chinese(before) or _is_chinese(after)
        if chinese:
            chars[i] = _WIDE[ch]
    return "".join(chars).replace("（ ", "（").replace(" ）", "）")


def normalize(text: str, language: str) -> str:
    original = text
    if language != "zh":
        text = "".join(chr(ord(c) - 0xFEE0) if 0xFF01 <= ord(c) <= 0xFF5E else c for c in text)
        return re.sub(r"([,;:!?])(?=[A-Za-z])", r"\1 ", text) if text != original else text
    text = _unfold(text)
    if not any(_is_chinese(c) for c in text):
        return text                                         # an English line inside a Chinese book
    text = _ELLIPSIS.sub("……", text)
    text = re.sub(r"(?<![-—])--(?![-—])", "——", text)
    text = _widen(text)
    # Spaces left around full-width marks by the conversion ("好 ， 走") carry no meaning.
    return re.sub(r" *([，。？！：；、]) *", r"\1", text)
