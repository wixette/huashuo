"""v0.2.0 issue 2: can the LLM tell, from context, how a short line should be read?

For every dialogue block of at most MAX_CHARS characters, send the previous CONTEXT blocks
and the next one, and ask for a delivery label (fixed set) and a short free-form note.
Calls go through huashuo's Caller: cached on disk, with a hard cost cap.

usage: .venv/bin/python experiments/v020_short_line_delivery.py WORKDIR OUT.json MAX_COST
"""
import json
import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from huashuo.attribution import Caller, llm_config

MAX_CHARS, CONTEXT, BATCH = 6, 3, 20
LABELS = ["询问", "疑惑", "惊讶", "应答", "迟疑", "感叹", ""]

INSTRUCTIONS = """你在为一部小说制作有声书。很短的台词（几个字）单独交给语音合成时，合成器不知道上下文，
同一句「什么？」可能读成平静的询问，也可能读成震惊。请根据上下文判断每句短台词应当怎么读。

对每个编号给出：
- label：从下列选一个；都不合适时留空字符串。
  询问：平静地问，想让对方说下去或说明（如「还有一点让我不解。」「什么？」）
  疑惑：没听懂或不明白对方为什么这么说，带着疑问反问
  惊讶：吃惊、难以置信
  应答：平淡地应答、附和、确认（如「嗯。」「是啊。」「好。」）
  迟疑：犹豫、话说一半、吞吞吐吐
  感叹：抒发感受（如「真热！」）
- note：给配音演员的一句提示，不超过 15 个字，描述语气，不复述台词。"""


class Delivery(BaseModel):
    index: int
    label: Literal[tuple(LABELS)]  # type: ignore[valid-type]
    note: str = Field(max_length=40)


class Deliveries(BaseModel):
    answers: list[Delivery]


def main():
    wd, out, max_cost = Path(sys.argv[1]), Path(sys.argv[2]), float(sys.argv[3])
    blocks = [json.loads(line) for line in (wd / "script.huaben.jsonl").read_text(encoding="utf-8").splitlines()][1:]
    blocks = [b for b in blocks if b.get("type") in ("narration", "dialogue")]
    targets = [i for i, b in enumerate(blocks)
               if b["type"] == "dialogue" and len(b["text"].strip("“”‘’")) <= MAX_CHARS]
    config = llm_config(max_cost=max_cost)
    caller = Caller(config, out.parent / "e2_llm_cache")

    def line(b):
        who = b.get("speaker", "旁白") if b["type"] == "dialogue" else "旁白"
        return f"{who}：{b['text']}"

    results = []
    for start in range(0, len(targets), BATCH):
        batch = targets[start:start + BATCH]
        parts = []
        for n, i in enumerate(batch):
            ctx = "\n".join("    " + line(b) for b in blocks[max(0, i - CONTEXT):i])
            nxt = "\n".join("    " + line(b) for b in blocks[i + 1:i + 2])
            parts.append(f"[{n}]\n  上文：\n{ctx}\n  台词：{line(blocks[i])}\n  下文：\n{nxt}")
        prompt = "\n\n".join(parts) + f"\n\n需要回答的编号：0 到 {len(batch) - 1}"
        answer = caller.call("speakers", INSTRUCTIONS, prompt, Deliveries)
        for a in answer.answers:
            if 0 <= a.index < len(batch):
                b = blocks[batch[a.index]]
                results.append({"id": b["id"], "speaker": b.get("speaker"), "text": b["text"],
                                "emotion": b.get("emotion"), "label": a.label, "note": a.note})
        print(f"batch {start // BATCH + 1}: ${caller.usage.cost:.4f} so far", flush=True)
    u = caller.usage
    summary = {"requests": u.requests, "cached": u.cached, "input_tokens": u.input_tokens,
               "output_tokens": u.output_tokens, "cost": round(u.cost, 4), "model": config.model}
    out.write_text(json.dumps({"usage": summary, "results": results}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(summary)
    caller.close()


if __name__ == "__main__":
    main()
