# 话说 Huashuo

话说，这是一个开源的中文有声书生成项目：用 LLM 把小说整理成多角色剧本，再用 Qwen3-TTS 在
Apple Silicon 上本地合成，封装成标准有声书。

An open-source, Chinese-first audiobook generator: an LLM turns a novel into a multi-voice
script, Qwen3-TTS renders it locally on Apple Silicon via MLX, and the result is packaged as
a standard audiobook.

**Status:** research and design. The pipeline does not exist yet.

- [docs/requirements.md](docs/requirements.md): first-stage requirements (novel → M4B),
  milestones, acceptance criteria and open questions. Start here.
- [docs/design-and-research.md](docs/design-and-research.md): the design doc, covering
  research, measurements, architecture decisions and naming.
- [docs/script-ir.md](docs/script-ir.md): the 话本 (Script IR) format, the cast table and
  the per-book work directory.
- [experiments/](experiments/): working prototypes. `novel_tts.py` is a single-voice
  text-to-MP3 CLI that the real pipeline will grow from.

License: Apache-2.0 (planned).
