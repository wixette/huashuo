#!/usr/bin/env python3
"""M3 / SCR-12: do the emotion hints keep each voice recognizable and the words right?

For every label in attribution.EMOTIONS["zh"], one line that suits it is rendered by each
test voice twice with the same seed: plainly, and with the label's `instruct` phrase.
Three labels are also rendered with the stronger wording EXP-1 used, for comparison.

Measured on every clip:
    - timbre: speaker vector (Base encoder) vs the centroid of the voice's plain clips,
      mean-centered cosine as in exp1_stability.py; and whether the nearest centroid is
      still the voice's own
    - ASR character error rate, and the lost-ending rule (asr.lost_ending)
    - duration ratio emotion / plain (a hint that the delivery changed at all)

For listening, <voice>_all.wav holds plain / emotion pairs, a beep before each pair.

Usage (from the repository root):
    .venv/bin/python experiments/m3_emotion_check.py OUT_DIR
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import mlx.core as mx
import numpy as np

from exp1_voice_routes import as_vector, free, load, read_wav, save_wav
from exp1_stability import beep
from huashuo.asr import AsrChecker, cer, lost_ending
from huashuo.attribution import EMOTIONS
from huashuo.engines.qwen3 import Qwen3Engine

VOICES = ["preset:serena", "preset:uncle_fu", "library:zh/young_man_warm", "library:zh/mid_woman_calm",
          "library:zh/old_man_kind", "library:zh/teen_girl", "library:zh/old_woman_stern", "library:zh/boy"]
LINES = {
    "高兴": "太好了，今天总算把事情办成了。", "兴奋": "快来看，我们真的找到了！",
    "生气": "你怎么又把这件事忘了？我说了多少遍了。", "不耐烦": "行了行了，别再说了，我知道了。",
    "悲伤": "他走了以后，这个家就再也没有笑声了。", "害怕": "外面好像有人，你听见了吗？",
    "惊讶": "什么？你说他昨天就回来了？", "低声": "别出声，等他们走远了再说。",
    "温柔": "别怕，有我在，慢慢来。", "冷淡": "这件事跟我没有关系。",
    "讥讽": "哟，你可真是个大忙人啊。", "急切": "快走，再晚就来不及了！",
    "疑惑": "奇怪，这封信是谁放在这里的？", "严厉": "站住！谁让你进来的？",
}
STRONG = {"生气": "用非常愤怒的语气说", "悲伤": "带着哭腔、哽咽地说", "低声": "压低声音，像耳语一样说"}
SEED = 7
SR = 24000


def main() -> None:
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    phrases = EMOTIONS["zh"]
    jobs = [(label, "plain", None) for label in LINES] + [(label, "hint", phrases[label]) for label in LINES]
    jobs += [(label, "strong", phrase) for label, phrase in STRONG.items()]

    engine = Qwen3Engine()
    model = engine._load()
    center = np.mean([as_vector(model.talker.get_input_embeddings()(mx.array([[i]])))
                      for i in model.config.talker_config.spk_id.values()], axis=0)
    clips = []
    for voice in VOICES:
        for label, kind, instruct in jobs:
            audio = engine.synthesize(LINES[label], voice, "zh", instruct, SEED)
            path = out / f"{voice.split('/')[-1].replace(':', '_')}_{label}_{kind}.wav"
            save_wav(path, mx.array(audio), SR)
            clips.append(dict(voice=voice, label=label, kind=kind, instruct=instruct, text=LINES[label],
                              path=str(path), secs=len(audio) / SR))
        print(f"  {voice}: {len(jobs)} clips")
    engine.close()

    base = load("Base")
    for c in clips:
        c["vec"] = as_vector(base.extract_speaker_embedding(read_wav(Path(c["path"]))))
    free(base)
    asr = AsrChecker()
    for c in clips:
        audio = np.array(read_wav(Path(c["path"])))
        c["asr"] = asr.transcribe(audio, SR, "zh")
        c["cer"] = cer(c["text"], c["asr"], "zh")
        c["lost"] = lost_ending(c["text"], c["asr"], "zh")
    asr.close()

    def ccos(a, b):
        a, b = a - center, b - center
        return float(a @ b / np.linalg.norm(a) / np.linalg.norm(b))

    centroids = {v: np.mean([c["vec"] for c in clips if c["voice"] == v and c["kind"] == "plain"], axis=0)
                 for v in VOICES}
    plain_secs = {(c["voice"], c["label"]): c["secs"] for c in clips if c["kind"] == "plain"}
    for c in clips:
        c["sim"] = ccos(c["vec"], centroids[c["voice"]])
        c["nearest"] = max(centroids, key=lambda v: ccos(c["vec"], centroids[v]))
        c["dur_ratio"] = c["secs"] / plain_secs[(c["voice"], c["label"])]

    print(f"\n{'voice':<28}{'plain ≈own':>11}{'hint ≈own':>11}{'strong ≈own':>13}{'hint misID':>12}"
          f"{'CER plain/hint':>16}{'lost':>6}")
    for v in VOICES:
        by = {k: [c for c in clips if c["voice"] == v and c["kind"] == k] for k in ("plain", "hint", "strong")}
        print(f"{v:<28}{np.mean([c['sim'] for c in by['plain']]):>11.2f}{np.mean([c['sim'] for c in by['hint']]):>11.2f}"
              f"{np.mean([c['sim'] for c in by['strong']]):>13.2f}"
              f"{sum(c['nearest'] != v for c in by['hint']):>8}/{len(by['hint'])}"
              f"{np.mean([c['cer'] for c in by['plain']]):>10.1%} /{np.mean([c['cer'] for c in by['hint']]):>5.1%}"
              f"{sum(c['lost'] for c in by['hint'] + by['plain']):>6}")

    print(f"\n{'label':<8}{'hint ≈own mean/min':>20}{'misID':>7}{'dur ×':>7}   worst")
    for label in LINES:
        hc = [c for c in clips if c["label"] == label and c["kind"] == "hint"]
        worst = min(hc, key=lambda c: c["sim"])
        print(f"{label:<8}{np.mean([c['sim'] for c in hc]):>12.2f} / {min(c['sim'] for c in hc):.2f}"
              f"{sum(c['nearest'] != c['voice'] for c in hc):>7}{np.mean([c['dur_ratio'] for c in hc]):>7.2f}"
              f"   {worst['voice']} → {worst['nearest']}")
    for label in STRONG:
        sc = [c for c in clips if c["label"] == label and c["kind"] == "strong"]
        print(f"{label + '(强)':<8}{np.mean([c['sim'] for c in sc]):>12.2f} / {min(c['sim'] for c in sc):.2f}"
              f"{sum(c['nearest'] != c['voice'] for c in sc):>7}{np.mean([c['dur_ratio'] for c in sc]):>7.2f}")

    offsets = {}
    for v in VOICES:
        parts, t = [], 0.0
        name = v.split("/")[-1].replace(":", "_")
        for label in LINES:
            offsets[f"{name} {label}"] = f"{int(t // 60)}:{int(t % 60):02d}"
            for kind in ("plain", "hint"):
                clip = np.array(read_wav(out / f"{name}_{label}_{kind}.wav"))
                parts += [beep() if kind == "plain" else np.zeros(int(0.5 * SR)), clip]
                t += (len(parts[-2]) + len(clip)) / SR
        save_wav(out / f"{name}_all.wav", mx.array(np.concatenate(parts)), SR)
    report = [{k: val for k, val in c.items() if k != "vec"} for c in clips]
    (out / "report.json").write_text(json.dumps({"clips": report, "offsets": offsets}, ensure_ascii=False, indent=1))
    print(f"\nlistening files: {out}/<voice>_all.wav (plain, then with the hint)   details: {out}/report.json")


if __name__ == "__main__":
    main()
