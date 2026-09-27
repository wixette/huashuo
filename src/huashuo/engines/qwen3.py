"""Qwen3-TTS CustomVoice through mlx-audio, running locally on Apple Silicon (SYN-1)."""

from __future__ import annotations

import numpy as np

from huashuo.engines import EngineError, library_voice, library_voice_identity, parse_voice

DEFAULT_MODEL = "mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit"
DEFAULT_TEMPERATURE = 0.9

# The talker stops at max_tokens codec frames (12.5 per second): about 5.5 minutes, far
# beyond any unit (at most 150 characters), so hitting it means the model ran away.
MAX_TOKENS = 4096
CODEC_FRAME_RATE = 12.5
MAX_UNIT_SECONDS = MAX_TOKENS / CODEC_FRAME_RATE

LANGUAGES = {"zh": "chinese", "en": "english"}

# Free GPU buffers every so often so a multi-hour run does not accumulate memory.
_CLEAR_CACHE_EVERY = 20


class Qwen3Engine:
    name = "qwen3"
    max_unit_seconds = MAX_UNIT_SECONDS

    def __init__(self, model: str = DEFAULT_MODEL, temperature: float = DEFAULT_TEMPERATURE) -> None:
        self.model_id = model
        self.temperature = temperature
        self._model = None
        self._calls = 0
        self._injector = None
        self.sample_rate = 24000

    def identity(self) -> dict:
        try:
            from mlx_audio.version import __version__ as backend
        except ImportError:
            backend = None
        return {"engine": self.name, "model": self.model_id, "temperature": self.temperature,
                "max_tokens": MAX_TOKENS, "mlx_audio": backend}

    def _load(self):
        if self._model is None:
            try:
                from mlx_audio.tts.utils import load_model
            except ImportError as exc:
                raise EngineError("mlx-audio is not installed; Qwen3-TTS needs an Apple Silicon Mac "
                                  "with `pip install mlx-audio==0.5.5`") from exc
            try:  # silence a harmless "model of type qwen3_tts" warning on every load
                import transformers
                transformers.logging.set_verbosity_error()
            except ImportError:
                pass
            from huashuo.quiet import quiet_loading
            with quiet_loading(self.model_id):
                self._model = load_model(self.model_id)
            if getattr(self._model.config, "tts_model_type", None) != "custom_voice":
                raise EngineError(f"{self.model_id} is not a CustomVoice model")
            self.sample_rate = self._model.sample_rate
        return self._model

    def presets(self) -> list[str]:
        return [s.lower() for s in self._load().get_supported_speakers()]

    def voice_identity(self, voice: str) -> str:
        return library_voice_identity(voice)

    def check_voice(self, voice: str) -> None:
        kind, name = parse_voice(voice)
        if kind == "library":
            self._speaker(voice, library_voice(voice))   # registers it, checking the vector fits
            return
        if name.lower() not in self.presets():
            raise EngineError(f"{voice}: no such preset; available: {', '.join(self.presets())}")

    def _speaker(self, voice: str, entry=None) -> str:
        kind, name = parse_voice(voice)
        if kind != "library":
            return name
        if self._injector is None:
            from huashuo.engines.qwen3_voices import VoiceInjector
            self._injector = VoiceInjector(self._load())
        if entry is None:
            from huashuo.library import get
            entry = get(voice)
        return self._injector.speaker(voice, entry.vector())

    def synthesize(self, text: str, voice: str, language: str, instruct: str | None,
                   seed: int) -> np.ndarray:
        import mlx.core as mx

        speaker = self._speaker(voice)
        model = self._load()
        mx.random.seed(seed)
        pieces = [np.asarray(result.audio, dtype=np.float32)
                  for result in model.generate_custom_voice(
                      text=text, speaker=speaker, language=LANGUAGES.get(language, "auto"),
                      instruct=instruct, temperature=self.temperature, max_tokens=MAX_TOKENS,
                      verbose=False)]
        self._calls += 1
        if self._calls % _CLEAR_CACHE_EVERY == 0:
            mx.clear_cache()
        return np.concatenate(pieces) if pieces else np.zeros(0, dtype=np.float32)

    def close(self) -> None:
        if self._model is not None:
            import gc

            import mlx.core as mx

            self._model = None
            self._injector = None
            gc.collect()
            mx.clear_cache()
