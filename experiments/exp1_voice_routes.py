#!/usr/bin/env python3
"""EXP-1: compare routes for turning a VoiceDesign voice into a reusable library voice.

A voice is designed once with VoiceDesign (the reference clip), then reused on new lines
through one of the routes in design doc §5.6:

    A  Base + ICL        reference audio + transcript on every call
    B  Base + x-vector   only the speaker vector extracted from the reference
    C  CustomVoice + injected vector
                         the same vector placed where CustomVoice looks up a preset
                         speaker; this also allows `instruct`

Also renders the nearest preset (`uncle_fu`) on one line, so route C can be checked for
collapsing onto the preset instead of keeping the designed voice.

Output: numbered WAVs for listening, plus a table of duration, speaking rate and
speaker-vector similarity. Similarity is cosine after subtracting the mean of the nine
preset vectors; raw cosine is ~0.93 between any two voices and says little (§5.6).

Usage (from the repository root):
    .venv/bin/python experiments/exp1_voice_routes.py OUT_DIR
"""

from __future__ import annotations

import gc
import re
import sys
import time
import wave
from pathlib import Path

import mlx.core as mx
import numpy as np
from mlx_audio.tts.utils import load_model

REPO = "mlx-community/Qwen3-TTS-12Hz-1.7B-{}-8bit"
LANGUAGE = "chinese"

VOICE_NAME = "elder"
VOICE_DESCRIPTION = "一位六十多岁的老年男性客栈掌柜，声音略带沙哑，语速偏慢，语气热情又带几分警惕。"
REF_TEXT = "老朽在这条官道上开了四十年客栈，南来北往的客人见得多了。今夜风雪太大，山路怕是走不得了，客官还是早些歇息吧。"

LINES = {
    "1": "这场雪一下就是三天，山路早就封了。客官要是不嫌弃，就在小店多住几日，等雪停了再走也不迟。",
    "2": "你……你可千万别往后山去！二十年前，我亲眼看见那口井里爬出来过东西，第二天，整个村子的狗都不叫了。",
    "3": "哈哈，老头子我什么样的人没见过？你那把剑，可不是寻常人家用得起的。说吧，你到底是什么人？",
}
INSTRUCT_LINE = "2"
INSTRUCT = "用惊恐、压低声音的语气说"
PRESET_BASELINE = "uncle_fu"

# CustomVoice's embedding table has 3072 rows; presets sit at 2861-3066. Rows from 3000
# up are unused (3000 is the slot Qwen's official fine-tuning script writes a new speaker
# into), so injected speakers take 3000, 3001, ...
INJECTED_SPK_ID = 3000


def save_wav(path: Path, audio: mx.array, sr: int) -> float:
    pcm = np.clip(np.array(audio, dtype=np.float32), -1.0, 1.0)
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(sr)
        f.writeframes((pcm * 32767).astype(np.int16).tobytes())
    return len(pcm) / sr


def read_wav(path: Path) -> mx.array:
    with wave.open(str(path)) as f:
        pcm = np.frombuffer(f.readframes(f.getnframes()), np.int16)
    return mx.array(pcm.astype(np.float32) / 32767)


def spoken_chars(text: str) -> int:
    return len(re.sub(r"[\s，。！？、；：…—“”‘’（）《》,.!?;:\"'()]", "", text))


def load(variant: str):
    t = time.time()
    model = load_model(REPO.format(variant))
    print(f"[{variant}] loaded in {time.time() - t:.1f}s")
    return model


def free(model) -> None:
    del model
    gc.collect()
    mx.clear_cache()


def as_vector(x: mx.array) -> np.ndarray:
    return np.array(x.astype(mx.float32)).reshape(-1)


def inject_speakers(model, voices: dict[str, mx.array]) -> None:
    """Make each name a CustomVoice speaker whose embedding is the given vector (route C).

    The embedding table is quantized, so instead of editing it we intercept the lookup
    while the prompt is built. Generation steps use the real table untouched.
    """
    table = model.talker.get_input_embeddings()
    injected = {}
    for offset, (name, vector) in enumerate(voices.items()):
        spk_id = INJECTED_SPK_ID + offset
        assert spk_id not in model.config.talker_config.spk_id.values(), spk_id
        injected[spk_id] = vector.reshape(1, 1, -1)
        model.config.talker_config.spk_id[name] = spk_id
        model.supported_speakers.append(name)

    def lookup(ids: mx.array) -> mx.array:
        if ids.shape == (1, 1) and (key := int(ids[0, 0])) in injected:
            return injected[key]
        return table(ids)

    prepare = model._prepare_generation_inputs

    def prepare_with_injection(*args, **kwargs):
        model.talker.get_input_embeddings = lambda: lookup
        try:
            return prepare(*args, **kwargs)
        finally:
            del model.talker.get_input_embeddings

    model._prepare_generation_inputs = prepare_with_injection


class Renderer:
    def __init__(self, out: Path):
        self.out = out
        self.rows: list[dict] = []

    def render(self, label: str, route: str, text: str, gen, seed: int) -> Path:
        mx.random.seed(seed)
        t = time.time()
        results = list(gen)
        audio = mx.concatenate([r.audio for r in results])
        path = self.out / f"{label}.wav"
        secs = save_wav(path, audio, results[0].sample_rate)
        took = time.time() - t
        self.rows.append(dict(label=label, route=route, path=path, secs=secs,
                              rate=spoken_chars(text) / secs, rtf=secs / took))
        print(f"  {label:<24} {secs:5.1f}s  {spoken_chars(text) / secs:4.1f} 字/s  RTF {secs / took:4.2f}x")
        return path


def main() -> None:
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    r = Renderer(out)

    # Design the voice once. This clip is the only thing the routes get to see.
    m = load("VoiceDesign")
    ref_path = r.render("00_ref_voicedesign", "ref", REF_TEXT, m.generate_voice_design(
        text=REF_TEXT, instruct=VOICE_DESCRIPTION, language=LANGUAGE), seed=0)
    free(m)
    ref_audio = read_wav(ref_path)

    base = load("Base")
    xvec = base.extract_speaker_embedding(ref_audio)
    for key, text in LINES.items():
        r.render(f"A{key}_base_icl", "A", text, base.generate(
            text=text, lang_code=LANGUAGE, ref_audio=ref_audio, ref_text=REF_TEXT), seed=int(key))
    for key, text in LINES.items():
        r.render(f"B{key}_base_xvector", "B", text, base.generate(
            text=text, lang_code=LANGUAGE, ref_audio=ref_audio), seed=int(key))

    cv = load("CustomVoice")
    presets = {n: as_vector(cv.talker.get_input_embeddings()(mx.array([[i]])))
               for n, i in cv.config.talker_config.spk_id.items()}
    inject_speakers(cv, {VOICE_NAME: xvec})
    for key, text in LINES.items():
        r.render(f"C{key}_customvoice_injected", "C", text, cv.generate_custom_voice(
            text=text, speaker=VOICE_NAME, language=LANGUAGE), seed=int(key))
    r.render(f"C{INSTRUCT_LINE}i_customvoice_injected_instruct", "C+instruct", LINES[INSTRUCT_LINE],
             cv.generate_custom_voice(text=LINES[INSTRUCT_LINE], speaker=VOICE_NAME,
                                      language=LANGUAGE, instruct=INSTRUCT), seed=int(INSTRUCT_LINE))
    r.render(f"U1_preset_{PRESET_BASELINE}", "preset", LINES["1"], cv.generate_custom_voice(
        text=LINES["1"], speaker=PRESET_BASELINE, language=LANGUAGE), seed=1)
    free(cv)

    # Similarity of every clip to the reference voice and to the nearest preset.
    center = np.mean(list(presets.values()), axis=0)

    def centered_cos(a: np.ndarray, b: np.ndarray) -> float:
        a, b = a - center, b - center
        return float(a @ b / np.linalg.norm(a) / np.linalg.norm(b))

    ref_vec = as_vector(xvec)
    print(f"\n{'clip':<40}{'route':<12}{'秒':>6}{'字/s':>6}{'RTF':>6}{'≈ref':>7}{'≈' + PRESET_BASELINE:>11}")
    for row in r.rows:
        v = as_vector(base.extract_speaker_embedding(read_wav(row["path"])))
        print(f"{row['label']:<40}{row['route']:<12}{row['secs']:6.1f}{row['rate']:6.1f}{row['rtf']:6.2f}"
              f"{centered_cos(v, ref_vec):7.2f}{centered_cos(v, presets[PRESET_BASELINE]):11.2f}")
    print(f"\nref vector vs {PRESET_BASELINE} preset: {centered_cos(ref_vec, presets[PRESET_BASELINE]):.2f}")
    print(f"wav files: {out}")


if __name__ == "__main__":
    main()
