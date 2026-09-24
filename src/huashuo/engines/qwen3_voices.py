"""Library voices in Qwen3-TTS CustomVoice: speaker-vector injection (route C, design doc §5.6).

CustomVoice looks up a preset speaker as one row of the talker's embedding table while it
builds the prompt. A library voice is a vector from the same space (the Base model's
speaker encoder), so it can stand in for that row. The table is quantized, so instead of
editing it we answer the lookup ourselves, only while the prompt is being built; the
generation steps use the real table untouched.

This reaches into mlx-audio internals (`_prepare_generation_inputs`, `talker_config.spk_id`,
`supported_speakers`) that are not a public API, so it is checked against the exact
version it was written for. The proper fix is an upstream option to pass a speaker vector
directly (design doc §10.2).
"""

from __future__ import annotations

import numpy as np

from huashuo.engines import EngineError

SUPPORTED_MLX_AUDIO = "0.5.5"
# Library voices take rows from 3000 up (the slot Qwen's own fine-tuning script writes a
# new speaker into), skipping the rows presets use: they are scattered over 2861-3066,
# e.g. uncle_fu is row 3010, so rows cannot simply be counted up from 3000.
FIRST_FREE_ROW = 3000


class VoiceInjector:
    def __init__(self, model) -> None:
        try:
            from mlx_audio.version import __version__ as version
        except ImportError:                      # pragma: no cover - mlx-audio always has it
            version = None
        if version != SUPPORTED_MLX_AUDIO:
            raise EngineError(f"library voices need mlx-audio {SUPPORTED_MLX_AUDIO} (installed: {version}); "
                              f"reinstall with `pip install mlx-audio=={SUPPORTED_MLX_AUDIO}`")
        import mlx.core as mx

        self.model = model
        self.rows: dict[int, object] = {}        # table row -> injected vector (mx.array)
        self.names: dict[str, str] = {}          # voice ref -> speaker name registered in the model
        self.width = int(model.talker.get_input_embeddings()(mx.array([[0]])).shape[-1])
        self._install()

    def _install(self) -> None:
        model, rows = self.model, self.rows
        table = model.talker.get_input_embeddings()
        prepare = model._prepare_generation_inputs

        def lookup(ids):
            if tuple(ids.shape) == (1, 1) and (row := int(ids[0, 0])) in rows:
                return rows[row]
            return table(ids)

        def prepare_with_library(*args, **kwargs):
            model.talker.get_input_embeddings = lambda: lookup
            try:
                return prepare(*args, **kwargs)
            finally:
                del model.talker.get_input_embeddings

        model._prepare_generation_inputs = prepare_with_library

    def speaker(self, ref: str, vector: np.ndarray) -> str:
        """The speaker name to pass to generate_custom_voice for this library voice."""
        if ref in self.names:
            return self.names[ref]
        if vector.shape[-1] != self.width:
            raise EngineError(f"{ref}: its vector has {vector.shape[-1]} values but this model expects "
                              f"{self.width}; library voices were made for the 1.7B model")
        import mlx.core as mx

        spk_ids = self.model.config.talker_config.spk_id
        taken = set(spk_ids.values()) | set(self.rows)
        size = int(getattr(self.model.config.talker_config, "vocab_size", 3072))
        row = next((r for r in range(FIRST_FREE_ROW, size) if r not in taken), None)
        if row is None:
            raise EngineError(f"no free row left in the speaker table for {ref}")
        name = f"huashuo_{len(self.rows)}"
        self.rows[row] = mx.array(vector.reshape(1, 1, -1))
        spk_ids[name] = row
        self.model.supported_speakers.append(name)
        self.names[ref] = name
        return name
