"""Optional speech-recognition check of synthesized units (SYN-10).

Each unit is transcribed and compared with the text it was asked to read. EXP-1 showed
this catches what the duration check cannot: a dropped or barely audible word, a misread
character, a repeated phrase (design doc §5.6).
"""

from __future__ import annotations

import re

import numpy as np

DEFAULT_ASR_MODEL = "mlx-community/Qwen3-ASR-1.7B-8bit"
DEFAULT_MAX_CER = 0.05       # EXP-1: clean units scored 0-4%; homophone spellings cost ~3%
_LANGUAGES = {"zh": "Chinese", "en": "English"}
_DIGITS = str.maketrans("0123456789", "零一二三四五六七八九")


def normalize(text: str, language: str) -> str:
    """Drop punctuation and spaces; compare Chinese digit by digit (1987 -> 一九八七)."""
    if language == "zh":
        return re.sub(r"[^\w]|_", "", text.translate(_DIGITS))
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
