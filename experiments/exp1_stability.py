#!/usr/bin/env python3
"""EXP-1 round 3b: stability of the chosen library voices through route C.

Every voice in exp1_voices.json is injected into CustomVoice from its chosen reference
clip (round 3a), then renders the same test set:

    N1-N4  narration                      E1-E4  emotional dialogue, emotion from text only
    D1-D4  calm dialogue                  I1-I4  lines with an `instruct` (anger, crying,
    R1-R3  one line, three more seeds            whisper, joy)
    L1     one ~400-character passage (the prototype's chunk ceiling)

Automatic checks on every clip:
    - ASR (Qwen3-ASR) transcript vs script: character error rate after stripping
      punctuation; catches dropped, repeated or misread words
    - speaker vector vs the voice's own reference (mean-centered cosine), and which of
      the six references plus five preset probes it is nearest to (identification)
    - speaking rate and pauses of 1.2 s or longer

For listening, each voice's clips are also joined into one <voice>_all.wav with a short
beep before each clip, in the order above.

Usage (from the repository root):
    .venv/bin/python experiments/exp1_stability.py ROUND3A_DIR OUT_DIR
"""

from __future__ import annotations

import json
import re
import sys
import time
import wave
from pathlib import Path

import mlx.core as mx
import numpy as np
from mlx_audio.stt import load as load_stt

from exp1_voice_routes import as_vector, free, inject_speakers, load, read_wav, save_wav, spoken_chars

SPEC = Path(__file__).with_name("exp1_voices.json")
ASR_MODEL = "mlx-community/Qwen3-ASR-1.7B-8bit"
SR = 24000

LINES: list[tuple[str, str, str | None]] = [  # (clip id, text, instruct)
    ("N1", "天刚蒙蒙亮，镇子口的石桥上已经有了挑担子的人，吆喝声一阵高过一阵。", None),
    ("N2", "他在门口站了很久，直到屋里的灯一盏接一盏地熄灭，才转身走进雨里。", None),
    ("N3", "那封信被压在箱子最底下，纸已经发黄，字迹却还清清楚楚。", None),
    ("N4", "一九八七年的冬天格外冷，河面结了厚厚的冰，孩子们在上面追来追去。", None),
    ("D1", "你先坐下，喝口热茶，慢慢说。", None),
    ("D2", "这件事我想了很久，还是觉得应该告诉你。", None),
    ("D3", "明天一早我们就出发，路上要走三天，你把东西收拾好。", None),
    ("D4", "别担心，我认得这条路，天黑之前一定能到。", None),
    ("E1", "你凭什么替我做决定？这是我的事，轮不到你来管！", None),
    ("E2", "我等了他整整十年……十年啊，他怎么就不回来了呢？", None),
    ("E3", "真的吗？太好了！我就知道你一定能做到！", None),
    ("E4", "嘘——别出声，外面有人。你听，脚步声越来越近了。", None),
    ("I1", "我已经说过多少遍了，不许再去那个地方！", "用非常愤怒的语气说"),
    ("I2", "对不起，都是我不好，我不该丢下你一个人。", "带着哭腔、哽咽地说"),
    ("I3", "别回头，有人在跟着我们，到了前面的路口再说。", "压低声音，像耳语一样说"),
    ("I4", "快来看，下雪了！今年的第一场雪！", "用欢快、兴奋的语气说"),
    ("L1", "那一年的秋天来得特别早。九月刚过，山上的枫叶就红了一大片，远远看去，像是整座山都烧了起来。"
           "镇上的人说，这是好兆头，今年的收成一定不错。可是住在村东头的老周却整天愁眉苦脸，谁跟他说话，"
           "他都只是叹气。有人问他怎么了，他摇摇头，说：“你们不懂，枫叶红得太早，不是什么好事。”"
           "大家都笑他杞人忧天，没有人放在心上。直到十月初的一个夜里，一场大风从北边刮过来，"
           "吹倒了半个村子的篱笆，也吹灭了家家户户的灯。第二天早上，人们推开门，发现河水涨到了门槛边上，"
           "田里的稻子全都泡在水里。老周站在桥头，望着浑浊的河水，一句话也没有说。后来，村里的老人们常常"
           "提起那一年，说要是当初听了老周的话，早点把粮食收进仓里，也不至于饿了整整一个冬天。", None),
]
REPEAT_LINE = "D2"
REPEAT_SEEDS = [101, 102, 103]
LONG_PAUSE = 1.2
CER_FLAG = 0.05
DIGITS = str.maketrans("0123456789", "零一二三四五六七八九")


def pauses(pcm: np.ndarray, sr: int = SR) -> list[float]:
    """Silent stretches inside the speech (20 ms frames more than 40 dB below the peak)."""
    hop = int(sr * 0.02)
    frames = pcm[: len(pcm) // hop * hop].reshape(-1, hop)
    db = 20 * np.log10(np.sqrt((frames ** 2).mean(1)) + 1e-9)
    voiced = np.flatnonzero(db > db.max() - 40)
    runs, n = [], 0
    for silent in db[voiced[0]: voiced[-1] + 1] <= db.max() - 40:
        if silent:
            n += 1
        elif n:
            runs.append(n * 0.02)
            n = 0
    return runs


def normalize(text: str) -> str:
    return re.sub(r"[^\w]|_", "", text.translate(DIGITS))


def cer(ref: str, hyp: str) -> float:
    ref, hyp = normalize(ref), normalize(hyp)
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i]
        for j, h in enumerate(hyp, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h)))
        prev = cur
    return prev[-1] / max(len(ref), 1)


def beep(sr: int = SR) -> np.ndarray:
    t = np.arange(int(sr * 0.15)) / sr
    tone = 0.1 * np.sin(2 * np.pi * 880 * t) * np.hanning(len(t))
    return np.concatenate([np.zeros(int(sr * 0.4)), tone, np.zeros(int(sr * 0.35))])


def main() -> None:
    refs_dir, out = Path(sys.argv[1]), Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    spec = json.loads(SPEC.read_text())
    lang = spec["language"]
    voices = [v["id"] for v in spec["voices"]]
    ref_wav = {v["id"]: refs_dir / f"{v['id']}_s{v['chosen_seed']}.wav" for v in spec["voices"]}
    probe_wav = {f"preset_{n}": refs_dir / f"preset_{n}.wav" for n in spec["chinese_presets"]}

    base = load("Base")
    ref_mx = {v: base.extract_speaker_embedding(read_wav(p)) for v, p in ref_wav.items()}
    anchors = {v: as_vector(x) for v, x in ref_mx.items()}
    anchors |= {n: as_vector(base.extract_speaker_embedding(read_wav(p))) for n, p in probe_wav.items()}
    free(base)

    jobs = [(cid, text, instruct, i + 1) for i, (cid, text, instruct) in enumerate(LINES)]
    repeat_text = dict((c, t) for c, t, _ in LINES)[REPEAT_LINE]
    jobs += [(f"R{k}", repeat_text, None, seed) for k, seed in enumerate(REPEAT_SEEDS, 1)]

    cv = load("CustomVoice")
    center = np.mean([as_vector(cv.talker.get_input_embeddings()(mx.array([[i]])))
                      for i in cv.config.talker_config.spk_id.values()], axis=0)
    inject_speakers(cv, ref_mx)
    clips = []
    for v in voices:
        t0 = time.time()
        for cid, text, instruct, seed in jobs:
            mx.random.seed(seed)
            results = list(cv.generate_custom_voice(text=text, speaker=v, language=lang, instruct=instruct))
            path = out / f"{v}_{cid}.wav"
            secs = save_wav(path, mx.concatenate([r.audio for r in results]), results[0].sample_rate)
            clips.append(dict(voice=v, clip=cid, text=text, instruct=instruct, path=path, secs=secs))
        audio = sum(c["secs"] for c in clips if c["voice"] == v)
        print(f"  {v:<16} {len(jobs)} clips, {audio:6.1f}s audio in {time.time() - t0:5.1f}s")
    free(cv)

    def ccos(a: np.ndarray, b: np.ndarray) -> float:
        a, b = a - center, b - center
        return float(a @ b / np.linalg.norm(a) / np.linalg.norm(b))

    base = load("Base")
    asr = load_stt(ASR_MODEL)
    for c in clips:
        pcm = np.array(read_wav(c["path"]))
        vec = as_vector(base.extract_speaker_embedding(mx.array(pcm)))
        c["sim"] = ccos(vec, anchors[c["voice"]])
        c["nearest"] = max(anchors, key=lambda a: ccos(vec, anchors[a]))
        c["rate"] = spoken_chars(c["text"]) / c["secs"]
        c["long_pauses"] = [round(p, 1) for p in pauses(pcm) if p >= LONG_PAUSE]
        c["asr"] = asr.generate(str(c["path"]), language="Chinese").text
        c["cer"] = cer(c["text"], c["asr"])
        c["vec"] = vec
    free(asr)
    free(base)

    # One listening file per voice.
    for v in voices:
        parts = []
        for c in (c for c in clips if c["voice"] == v):
            parts += [beep(), np.array(read_wav(c["path"]))]
        save_wav(out / f"{v}_all.wav", mx.array(np.concatenate(parts)), SR)

    print(f"\n{'voice':<16}{'≈ref mean/min':>14}{'identified':>12}{'CER mean':>10}{'字/s':>7}"
          f"{'instruct ≈ref':>15}{'R1-R3 ≈each other':>20}")
    flags = []
    for v in voices:
        vc = [c for c in clips if c["voice"] == v]
        ok = sum(c["nearest"] == v for c in vc)
        inst = [c["sim"] for c in vc if c["instruct"]]
        reps = [c["vec"] for c in vc if c["clip"].startswith("R")]
        rep_sims = [ccos(reps[i], reps[j]) for i in range(len(reps)) for j in range(i + 1, len(reps))]
        print(f"{v:<16}{np.mean([c['sim'] for c in vc]):8.2f} /{min(c['sim'] for c in vc):4.2f}"
              f"{ok:>8}/{len(vc)}{np.mean([c['cer'] for c in vc]):>9.1%}"
              f"{np.median([c['rate'] for c in vc]):>7.1f}{np.mean(inst):>12.2f}   "
              f"{' '.join(f'{s:.2f}' for s in rep_sims):>18}")
        for c in vc:
            why = []
            if c["nearest"] != v:
                why.append(f"nearest is {c['nearest']} (≈own ref {c['sim']:.2f})")
            if c["cer"] > CER_FLAG:
                why.append(f"CER {c['cer']:.0%}: ASR「{c['asr']}」")
            if c["long_pauses"]:
                why.append(f"pauses {c['long_pauses']} s")
            if why:
                flags.append(f"{v}_{c['clip']}: " + "; ".join(why))

    print("\nFlagged clips:" if flags else "\nNo flagged clips.")
    for f in flags:
        print("  " + f)

    report = [{k: (str(val) if isinstance(val, Path) else val) for k, val in c.items() if k != "vec"}
              for c in clips]
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    print(f"\nlistening files: {out}/<voice>_all.wav   details: {out}/report.json")


if __name__ == "__main__":
    main()
