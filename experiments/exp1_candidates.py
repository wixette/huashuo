#!/usr/bin/env python3
"""EXP-1 round 3a: generate reference candidates for each library voice.

For every voice in exp1_voices.json, VoiceDesign renders the voice's reference passage
once per seed. A listener then picks one candidate per voice (or rejects the voice).

To help the pick, every candidate's speaker vector is compared (mean-centered cosine, as
in exp1_voice_routes.py) against:
  - the Chinese presets, each rendered reading the same probe sentence. Clip vectors are
    compared with clip vectors: a clip's vector sits in a different region from the preset
    table rows, so clip-vs-row numbers are not comparable (design doc §5.6);
  - every candidate of every other voice.
A candidate is flagged when it is closer than FLAG_AT to a preset or to another voice.

Writes <out>/<voice>_s<seed>.wav and <out>/<voice>_s<seed>.npy (the speaker vector).

Usage (from the repository root):
    .venv/bin/python experiments/exp1_candidates.py OUT_DIR
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import mlx.core as mx
import numpy as np

from exp1_voice_routes import as_vector, free, load, read_wav, save_wav, spoken_chars

SPEC = Path(__file__).with_name("exp1_voices.json")
# Round 2 measured ~0.92-0.94 between renderings of one voice and 0.72 between two
# different old men, so anything above this is suspiciously close to being the same voice.
FLAG_AT = 0.85


def render(model_gen, seed: int, path: Path) -> float:
    mx.random.seed(seed)
    results = list(model_gen)
    return save_wav(path, mx.concatenate([r.audio for r in results]), results[0].sample_rate)


def main() -> None:
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    spec = json.loads(SPEC.read_text())
    lang = spec["language"]

    clips: dict[str, Path] = {}  # label -> wav
    owner: dict[str, str] = {}   # label -> voice id, or "preset"

    m = load("VoiceDesign")
    for v in spec["voices"]:
        for seed in v.get("seeds", spec["default_seeds"]):
            label = f"{v['id']}_s{seed}"
            t = time.time()
            secs = render(m.generate_voice_design(text=v["ref_text"], instruct=v["description"],
                                                  language=lang), seed, out / f"{label}.wav")
            print(f"  {label:<22} {secs:5.1f}s  {spoken_chars(v['ref_text']) / secs:4.1f} 字/s  "
                  f"({time.time() - t:.1f}s)")
            clips[label], owner[label] = out / f"{label}.wav", v["id"]
    free(m)

    cv = load("CustomVoice")
    presets = {n: as_vector(cv.talker.get_input_embeddings()(mx.array([[i]])))
               for n, i in cv.config.talker_config.spk_id.items()}
    for name in spec["chinese_presets"]:
        label = f"preset_{name}"
        render(cv.generate_custom_voice(text=spec["preset_probe_text"], speaker=name,
                                        language=lang), 0, out / f"{label}.wav")
        clips[label], owner[label] = out / f"{label}.wav", "preset"
    free(cv)

    center = np.mean(list(presets.values()), axis=0)
    base = load("Base")
    vecs = {}
    for label, path in clips.items():
        vecs[label] = as_vector(base.extract_speaker_embedding(read_wav(path)))
        if owner[label] != "preset":
            np.save(out / f"{label}.npy", vecs[label])
    free(base)

    def cos(a: str, b: str) -> float:
        x, y = vecs[a] - center, vecs[b] - center
        return float(x @ y / np.linalg.norm(x) / np.linalg.norm(y))

    print(f"\n{'candidate':<22}{'nearest preset':<22}{'nearest other voice':<30}{'same voice, other seeds'}")
    for label in clips:
        if owner[label] == "preset":
            continue
        p = max((l for l in clips if owner[l] == "preset"), key=lambda l: cos(label, l))
        others = [l for l in clips if owner[l] not in ("preset", owner[label])]
        o = max(others, key=lambda l: cos(label, l))
        siblings = [f"{cos(label, l):.2f}" for l in clips if owner[l] == owner[label] and l != label]
        flag = "  <-- close" if max(cos(label, p), cos(label, o)) >= FLAG_AT else ""
        print(f"{label:<22}{p.removeprefix('preset_'):<10}{cos(label, p):5.2f}       "
              f"{o:<22}{cos(label, o):5.2f}   {' '.join(siblings) or '-'}{flag}")
    print(f"\nwav + npy files: {out}")


if __name__ == "__main__":
    main()
