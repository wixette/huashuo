"""Optional speech-recognition check of synthesized units (SYN-10).

Each unit is transcribed and compared with the text it was asked to read. EXP-1 showed
this catches what the duration check cannot: a dropped or barely audible word, a misread
character, a repeated phrase (design doc §5.6).
"""

from __future__ import annotations

import re

import numpy as np

DEFAULT_ASR_MODEL = "mlx-community/Qwen3-ASR-1.7B-8bit"
# On full 400-character units the recognizer's own mistakes (homophones, classical
# phrasing) cost up to ~5% (M1, 《吶喊》); a dropped sentence or runaway repetition costs
# far more. A single dropped word on a long unit is below any usable threshold.
DEFAULT_MAX_CER = 0.10
_LANGUAGES = {"zh": "Chinese", "en": "English"}


# Characters that stay distinct after traditional-to-simplified conversion but that the
# recognizer uses interchangeably (it writes 着 where the book has 著, and so on).
_FOLD = str.maketrans({"著": "着", "裏": "里", "於": "于", "祇": "只", "纔": "才",
                       "么": "吗", "麽": "吗",   # a final 么 is heard as 吗; folding both sides is harmless
                       "罷": "吧", "罢": "吧", "啦": "了", "師": "师", "傅": "父"})
_converter = None


def _to_simplified(text: str) -> str:
    """The recognizer always answers in simplified characters, so traditional-character
    books are compared after conversion (OpenCC, Apache-2.0)."""
    global _converter
    if _converter is None:
        import opencc
        _converter = opencc.OpenCC("t2s")
    return _converter.convert(text)


_CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "幺": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9}
_CN_UNITS = {"十": 10, "百": 100, "千": 1000}
_CN_BIG = {"万": 10 ** 4, "亿": 10 ** 8}
_CN_NUMBER = re.compile(r"[零〇一幺二两三四五六七八九十百千万亿]+(?:点[零〇一幺二两三四五六七八九]+)?")


def _cardinal(run: str) -> int:
    total = section = digit = 0
    for ch in run:
        if ch in _CN_DIGITS:
            digit = _CN_DIGITS[ch]
        elif ch in _CN_UNITS:
            section += (digit or 1) * _CN_UNITS[ch]
            digit = 0
        else:                                               # 万, 亿
            total = (total + section + digit) * _CN_BIG[ch]
            section = digit = 0
    return total + section + digit


def _arabic(match: re.Match) -> str:
    """一千二百三十四 -> 1234 (a cardinal), 一九九八 / 一三八幺 -> 1998 / 1381 (digit by
    digit), 十二点五 -> 12.5."""
    whole, _, fraction = match.group(0).partition("点")
    if all(ch in _CN_BIG for ch in whole):
        return match.group(0)                               # 万 / 亿 on their own (1.5万, 万一)
    if any(ch in _CN_UNITS or ch in _CN_BIG for ch in whole):
        value = str(_cardinal(whole))
    else:
        value = "".join(str(_CN_DIGITS[ch]) for ch in whole)
    return value + ("." + "".join(str(_CN_DIGITS[ch]) for ch in fraction) if fraction else "")


# Symbols the TTS reads out in words, written the same way before comparing.
_SPOKEN_SYMBOLS = [
    (re.compile(r"(\d{4})-0?(\d{1,2})-0?(\d{1,2})"), r"\1年\2月\3日"),    # 2026-09-25
    (re.compile(r"(\d{1,2}):30(?!\d)"), r"\1点半"),                          # 8:30, read 八点半
    (re.compile(r"(\d{1,2}):(\d{2})(?!\d)"), r"\1点\2"),                   # 20:15
    (re.compile(r"(\d+):(\d+)"), r"\1比\2"),                               # 3:2
    (re.compile(r"(\d+)/(\d+)"), r"\2分之\1"),                             # 1/3
    (re.compile(r"(\d)-(\d)"), r"\1到\2"),                                 # 3-5
    (re.compile(r"[¥￥](\d+(?:\.\d+)?)"), r"\1元"),
    (re.compile(r"\$(\d+(?:\.\d+)?)"), r"\1美元"),
    (re.compile(r"(?<=\d)\s*(?:℃|°C)"), "摄氏度"),
    (re.compile(r"(?<=\d)\s*km\b", re.I), "公里"),
    (re.compile(r"(?<=\d)\s*kg\b", re.I), "公斤"),
    (re.compile(r"(?<=\d)\s*cm\b", re.I), "厘米"),
]


def normalize(text: str, language: str) -> str:
    """Drop punctuation and spaces; compare Chinese in simplified form, with
    interchangeable variants folded together and numbers as Arabic numerals.

    The TTS reads a number however suits it (1998年 as 一九九八, 1234个 as 一千两百三十四,
    5% as 百分之五) and the recognizer writes what it hears, so both sides are brought to
    digits: a Chinese numeral with 十/百/千/万 is a cardinal, one without is read digit by
    digit."""
    if language == "zh":
        text = _to_simplified(text).translate(_FOLD)
        for pattern, spoken in _SPOKEN_SYMBOLS:
            text = pattern.sub(spoken, text)
        text = text.replace("点三十", "点半")                  # 八点三十 = 八点半
        text = re.sub(r"百分之(?=[零〇一幺二两三四五六七八九十百千万亿])", "", text)
        text = _CN_NUMBER.sub(_arabic, text)
        return re.sub(r"[^\w]|_", "", text)
    return re.sub(r"[^a-z0-9]", "", text.lower())


def cer(reference: str, hypothesis: str, language: str) -> float:
    ref, hyp = normalize(reference, language), normalize(hypothesis, language)
    previous = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        current = [i]
        for j, h in enumerate(hyp, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (r != h)))
        previous = current
    return previous[-1] / max(len(ref), 1)


def lost_ending(reference: str, hypothesis: str, language: str) -> bool:
    """True when the last word of the text is missing from the end of the transcript.

    Some voices sometimes stop in the middle of the final syllable, or say it so softly
    it is barely audible (M3 listening: old_woman_stern's 「……应该告诉你」). On a
    20-character line that is a 5% error rate, under the threshold, but it is the most
    noticeable kind of mistake. The recognizer may add a trailing particle, so the last
    word only has to appear among the transcript's last few.
    """
    if language == "zh":
        ref, hyp = normalize(reference, language), normalize(hypothesis, language)
    else:
        ref = re.sub(r"[^a-z0-9 ]", "", reference.lower()).split()
        hyp = re.sub(r"[^a-z0-9 ]", "", hypothesis.lower()).split()
    return bool(ref) and ref[-1] not in hyp[-3:]


def judge(reference: str, hypothesis: str, language: str, max_cer: float) -> tuple[float, str | None]:
    """Character error rate, and the problem to report if the unit fails the check."""
    rate = cer(reference, hypothesis, language)
    if rate > max_cer:
        return rate, f"ASR mismatch ({rate:.0%}): heard 「{hypothesis}」"
    if lost_ending(reference, hypothesis, language):
        return rate, f"ASR mismatch (lost ending): heard 「{hypothesis}」"
    return rate, None


class AsrChecker:
    def __init__(self, model: str = DEFAULT_ASR_MODEL, max_cer: float = DEFAULT_MAX_CER) -> None:
        self.model_id = model
        self.max_cer = max_cer
        self._model = None

    def transcribe(self, audio: np.ndarray, sample_rate: int, language: str) -> str:
        if self._model is None:
            from mlx_audio.stt import load
            self._model = load(self.model_id)
        import mlx.core as mx

        if sample_rate != 16000:  # the model takes in-memory audio as 16 kHz
            from math import gcd

            from scipy.signal import resample_poly
            g = gcd(16000, sample_rate)
            audio = resample_poly(audio, 16000 // g, sample_rate // g).astype(np.float32)
        result = self._model.generate(mx.array(audio), language=_LANGUAGES.get(language, "Chinese"))
        return result.text

    def close(self) -> None:
        self._model = None
