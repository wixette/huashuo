<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/huashuo-logo-white.png">
    <img src="docs/assets/huashuo-logo-black.png" alt="话说 Huashuo" width="128">
  </picture>
</p>

<h1 align="center">话说 Huashuo</h1>

<p align="center">在 Mac 电脑上制作多角色有声书</p>

<p align="center">
  <a href="https://github.com/wixette/huashuo/actions/workflows/tests.yml"><img src="https://github.com/wixette/huashuo/actions/workflows/tests.yml/badge.svg" alt="tests"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-blue" alt="Apache-2.0"></a>
  <a href="https://github.com/wixette/huashuo/releases"><img src="https://img.shields.io/github/v/release/wixette/huashuo?include_prereleases&label=release" alt="release"></a>
</p>

<p align="center">简体中文 | <a href="README.en.md">English</a></p>

> Huashuo turns Chinese novels (TXT / EPUB) into multi-voice M4B audiobooks on an Apple
> Silicon Mac: an LLM finds who says each line, and Qwen3-TTS reads every character in a
> fitting voice, locally. Pre-release 0.1.0a1. [English README →](README.en.md)

## 试听

**《广告》**（半轻人，全文 6 分钟）：11 个说话人，11 种声音。

<!-- 视频：huashuo-demo-ad.mp4 -->

**《一条被洗澡水拍死的鱼》**（半轻人，节选 2 分钟）：第一人称的「我」与栖芒在书店里。

<!-- 视频：huashuo-demo-fish.mp4 -->

选角、标注都是自动的，《广告》只按试听对调了两个角色的音色。完整的有声书见
[v0.1.0a1 的附件](https://github.com/wixette/huashuo/releases/tag/v0.1.0a1)：
M4B（Apple Books）、MP3、按章 MP3。两篇小说 CC BY-NC-ND 4.0，经作者许可用于演示。

## 特点

- **多角色**：LLM 标出每句对白的说话人，再按性别、年龄、性格从 16 种专门设计的中文声音里选角
- **本地合成**：Qwen3-TTS 在 Apple Silicon 上运行（MLX）；只有文字会发给 LLM，声音不出本机
- **标准有声书**：M4B，带章节、封面与元数据；也可输出 MP3
- **自动把关**：逐句语音识别核对，漏读、错读、语速异常的自动重合成；听到问题可按时间点重做
- **可以修改**：说话人、音色、读音都能手改，重新导入时保留
- **费用可控**：先估算、有上限，回答全部缓存，中断后接着做

## 现状与限制

这是预发布版，给试用者和开发者：

- 只支持 Apple Silicon Mac（M1 起，建议 16 GB 内存以上），从源码安装，只有英文命令行
- 中文书多角色；英文书由旁白一种声音读全书
- 只在 Apple Books 上验证过
- 已知问题：偶尔句末最后一个字读得短促（用 `huashuo redo` 重做）；文言、半文半白的文字可能读错，偶有口音

问题与建议请提 [Issue](https://github.com/wixette/huashuo/issues)。

## 安装

```bash
brew install ffmpeg uv
git clone https://github.com/wixette/huashuo.git && cd huashuo
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e .
source .venv/bin/activate
```

首次合成时会自动下载两个模型（语音合成与语音识别，共约 5 GB）。

多角色需要一个 LLM：任何 OpenAI 兼容的接口，默认 `gpt-6-sol`。在运行 `huashuo` 的目录里建一个
`.env`（不会被提交）：

```bash
HUASHUO_LLM_API_KEY=你的密钥
# HUASHUO_LLM_MODEL=…      # 可选：其他模型
# HUASHUO_LLM_BASE_URL=…   # 可选：其他接口
```

没有密钥也能用：`--single-voice` 由旁白读全书。

## 快速开始

```bash
huashuo book.epub --dry-run        # 只导入：章节、跳过的文字、预计时长，不合成
huashuo book.epub --sample         # 先合成开头两分钟试听 -> book.sample.m4b
huashuo audition book.epub         # 每个角色一句台词，听听选角 -> book.audition.m4b
huashuo book.epub                  # 全书 -> book.m4b（可随时中断，再运行接着做）
huashuo redo book.epub --at 1:28   # 1:28 处听着不对？重新合成那一处
huashuo clean book.epub            # 听完了，删除音频缓存
```

导入时第一次把书发给 LLM 前，会先给出费用估算并征得同意。M1 Max 上合成用时约为音频时长的一半。

## 常用操作

工作目录 `book.huashuo/` 与书放在一起，下面的文件都可以手改，改完重新运行 `huashuo book.epub`，
只有受影响的部分会重做。

| 想要 | 做法 |
|---|---|
| 换某个角色的声音 | 改 `cast.json` 里该角色的 `voice`；`huashuo voices` 列出可选的声音，`huashuo audition book.epub --library` 逐一试听 |
| 纠正读音 | 在 `pron.txt` 里加一行 `单于 = chán yú`（拼音或同音字） |
| 纠正说话人 | 需要核对的句子列在 `review.txt`；改 `script.huaben.jsonl` 里该句的 `speaker` |
| 换旁白 | `--narrator female`、`male` 或某个声音；`auto` 交还给自动选择 |
| 旁白读全书 | `--single-voice`；`--multi-voice` 切回多角色 |
| 输出 MP3 | `--format mp3`（单个文件）或 `--format mp3-chapters`（每章一个文件） |
| 书名、作者、封面 | `--title`、`--author`、`--cover` |
| 音量与停顿 | `--loudness -16`、`--pause paragraph=0.8` |
| 不读开头的书名作者、结尾的「全书完」 | `--no-opening`、`--no-closing` |

这些选项都会记在工作目录里。全部命令与选项见 `huashuo --help` 与 `huashuo <命令> --help`；
话本格式见 [docs/script-ir.md](docs/script-ir.md)。

## LLM 费用

先估算再调用：短篇几美分，30 万字、角色众多的长篇约 10 美元。每次运行默认最多花 5 美元
（`--max-llm-cost`），超出前停下；回答随时缓存，重新导入不再付费，停下的书提高上限再运行，
只付剩下的部分。

## 开发

```bash
uv pip install --python .venv/bin/python -e ".[dev]"
pytest                 # 几秒钟；假的 TTS 引擎，不需要模型，从不调用付费接口
pytest -m model        # 可选：用真实的本地模型
```

- [docs/requirements.md](docs/requirements.md)：第一阶段需求、里程碑与决策记录（从这里开始）
- [docs/design-and-research.md](docs/design-and-research.md)：设计文档与实测记录
- [docs/script-ir.md](docs/script-ir.md)：话本（Script IR）、角色表与工作目录的格式
- [examples/](examples/)：两篇示例小说，也是测试的金标准
- [experiments/](experiments/)：有日期的实验记录

## 许可

代码 Apache-2.0，by [wixette](https://github.com/wixette)。字体、封面模板、标志与示例小说另有许可，见
[NOTICE](NOTICE)。基于 [Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS)、
[mlx-audio](https://github.com/Blaizzy/mlx-audio)、[OpenCC](https://github.com/BYVoid/OpenCC)
与 [ffmpeg](https://ffmpeg.org/)。
