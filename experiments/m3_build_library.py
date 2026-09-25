#!/usr/bin/env python3
"""M3: turn the chosen voices into the built-in library (src/huashuo/voices/<lang>/).

For every voice in the spec with a `chosen_seed`, copies the speaker vector saved by
exp1_candidates.py (<id>_s<seed>.npy) and writes the entry's JSON: what it is (role,
tags used by casting), how it was made (description, reference text, seed, models), and
what the listener said. Re-running is safe; it rewrites the entries it knows.

Usage (from the repository root):
    .venv/bin/python experiments/m3_build_library.py CANDIDATES_DIR [experiments/m3_voices.json]
    .venv/bin/python experiments/m3_build_library.py NARRATOR_CANDIDATES_DIR experiments/narrator_voices_2.json
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIBRARY = ROOT / "src" / "huashuo" / "voices"

# Spec id -> (library id, gender, age, traits). Library ids are what users see in cast.json.
ENTRIES = {
    "v0_elder": ("old_man_hoarse", "male", "elderly", ["沙哑", "语速慢", "热情"]),
    "v1_girl": ("girl", "female", "child", ["清脆", "活泼", "撒娇"]),
    "v2_teen_boy": ("teen_boy", "male", "teen", ["清亮", "爽朗", "倔强"]),
    "v3_young_woman": ("young_woman_cool", "female", "young_adult", ["清冷", "沉静", "克制"]),
    "v4_mid_woman": ("mid_woman_brisk", "female", "middle_aged", ["响亮", "泼辣", "精明"]),
    "v5_young_man": ("young_man_deep", "male", "young_adult", ["低沉", "醇厚", "沉稳"]),
    "v6_boy": ("boy", "male", "child", ["清亮", "稚气", "好奇"]),
    "v7_teen_girl": ("teen_girl", "female", "teen", ["清脆", "活泼", "小脾气"]),
    "v8_young_man_warm": ("young_man_warm", "male", "young_adult", ["清朗", "温和", "书卷气"]),
    "v9_young_woman_warm": ("young_woman_warm", "female", "young_adult", ["温柔", "明快", "亲切"]),
    "v10_mid_man_steady": ("mid_man_steady", "male", "middle_aged", ["浑厚", "沉稳", "威严"]),
    "v11_mid_man_jovial": ("mid_man_jovial", "male", "middle_aged", ["洪亮", "爽快", "市井气"]),
    "v12_mid_woman_calm": ("mid_woman_calm", "female", "middle_aged", ["知性", "冷静"]),
    "v13_old_man_kind": ("old_man_kind", "male", "elderly", ["慈祥", "温和", "语速慢"]),
    "v14_old_woman_kind": ("old_woman_kind", "female", "elderly", ["苍老", "慈祥", "絮叨"]),
    "v15_old_woman_stern": ("old_woman_stern", "female", "elderly", ["中气足", "严厉", "干脆"]),
    # Narrators (experiments/narrator_voices_2.json): never cast to characters.
    "f1_calm_full": ("narrator_female", "female", "middle_aged", ["narrator", "沉稳", "浑厚", "温润"]),
    "m2_documentary": ("narrator_male", "male", "middle_aged", ["narrator", "沉稳", "醇厚", "平静"]),
}
# The narrator for new books in each language.
DEFAULT_NARRATORS = {"f1_calm_full"}
# Role labels shown to users; the EXP-1 roles named the wuxia test scenes they were designed for.
ROLES = {
    "v0_elder": "老年男，六十多岁，略沙哑，语速慢",
    "v1_girl": "女童，八岁左右，活泼",
    "v2_teen_boy": "少年男，十五六岁，爽朗",
    "v3_young_woman": "青年女，二十出头，清冷",
    "v4_mid_woman": "中年女，四十多岁，响亮泼辣",
    "v5_young_man": "青年男，二十七八岁，低沉",
}
LANGUAGES = {"chinese": "zh", "english": "en"}
MADE_WITH = {"voice_design": "mlx-community/Qwen3-TTS-12Hz-1.7B-VoiceDesign-8bit",
             "speaker_encoder": "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit",
             "for_model": "mlx-community/Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit",
             "mlx_audio": "0.5.5"}


def main() -> None:
    candidates = Path(sys.argv[1])
    spec_path = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "experiments" / "m3_voices.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    language = LANGUAGES[spec["language"]]
    out = LIBRARY / language
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for voice in spec["voices"]:
        if "chosen_seed" not in voice or voice["id"] not in ENTRIES:
            continue
        name, gender, age, traits = ENTRIES[voice["id"]]
        vector = candidates / f"{voice['id']}_s{voice['chosen_seed']}.npy"
        if not vector.is_file():
            sys.exit(f"missing {vector}; run exp1_candidates.py with this spec first")
        shutil.copyfile(vector, out / f"{name}.npy")
        entry = {"id": name, "role": ROLES.get(voice["id"], voice["role"]), "gender": gender, "age": age, "traits": traits,
                 "description": voice["description"], "ref_text": voice["ref_text"],
                 "seed": voice["chosen_seed"], "made_with": MADE_WITH}
        if voice.get("listening_notes"):
            entry["listening_notes"] = voice["listening_notes"]
        if voice["id"] in DEFAULT_NARRATORS:
            entry["default_narrator"] = True
        (out / f"{name}.json").write_text(json.dumps(entry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        written.append(name)
    print(f"{len(written)} voices in {out}: {', '.join(written)}")


if __name__ == "__main__":
    main()
