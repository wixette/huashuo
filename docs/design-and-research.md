# 话说 Huashuo：中文有声书生成项目 —— 设计与调研文档

| 项目 | 内容 |
|---|---|
| 状态 | M1（单音色 M4B）、M2（说话人标注）、M3（多角色音色）、M4（补齐）完成；EXP-1、EXP-2 完成；下一步 M5 打磨，之后第一阶段验收（§10，2026-09-25） |
| 日期 | 2026-09-23 |
| 项目名称 | **话说 Huashuo**（`huashuo`，见 §8.6；早期测试代号 `novel-tts` 已弃用） |
| 目标许可证 | Apache-2.0 |
| 文档作用 | 记录调研结论、实测数据、技术选型与设计决策及其理由和出处。**要做什么、做到什么程度**见 [requirements.md](requirements.md) |

---

## 0. 快速摘要

我们计划新建一个开源的有声书生成项目 **话说（Huashuo）**，核心差异化是 **「LLM 做多角色剧本化 + 中文优先 + Apple Silicon 本地 TTS + 标准有声书封装」**。其中「LLM 多角色 + 有声书封装」已有项目在做（见 §4.2），**「中文优先 + Apple Silicon 本地推理」这一层目前没有人占据**。

已确定的关键决策：

1. **TTS 主力用 Qwen3-TTS 1.7B**，经 mlx-audio 在 macOS 本地运行（已实测，中文质量优秀）
2. **文本分析用云端 LLM API**（本地小模型中文理解不足），但改为**段落级批量标注**而非逐句调用（成本与调用次数降低约 40 倍）
3. **新建项目而非 fork**，采用 Apache-2.0（参考项目为 GPL-3.0）
4. **核心架构是「剧本」中间表示（Script IR）**：文档 → 剧本 → 音频
5. **项目命名为「话说 Huashuo」**（2026-09-23 确定，见 §8.6）
6. **角色声音来自内置音色库**：预先设计并固化 12–20 个与输入无关的音色，随项目发布（2026-09-23 确定；2026-09-24 经 EXP-1 确定固化方式为「Base 提取向量 → 注入 CustomVoice」，见 §5.6）
7. **LLM 只走 OpenAI 兼容接口，经 pydantic-ai 接入**，不写提供商专用适配器（2026-09-23 确定，见 §6.5）；默认模型 gpt-6-sol（2026-09-24，EXP-2，见 §7.2.1）

第一阶段的需求与第一轮决策见 [requirements.md](requirements.md)。

---

## 1. 项目目标与范围

项目目标、场景路线图与第一阶段需求已移至 [requirements.md](requirements.md)（§1 目标，§1.2 路线图）。本节只保留影响设计决策的范围纪律。

### 1.3 范围纪律（重要）

**P1–P3 在当前阶段只体现在 Script IR 的 block type 设计中，不写任何实现代码。**

理由：这四个场景的**输入端解析**完全不同，共享的只有剧本之后的合成部分。若现在就抽象「万能前端」，会在真正要交付的小说场景上卡住（second-system syndrome）。

学术论文方向最远——数学公式朗读本身是研究级问题（`\int_0^1` 的读法取决于上下文与听众），图表朗读需要视觉理解。剧本模型让它**未来可达**，但不应影响当前任何决策。

---

## 2. 参考项目分析：audiobook-creator

| 项目 | [prakharsr/audiobook-creator](https://github.com/prakharsr/audiobook-creator) |
|---|---|
| 许可证 | **GPL-3.0** |
| 规模 | 528 stars / 47 forks / 约 6,640 行 Python |
| 本地副本 | `/Users/ygwang/src/audiobook-creator` |

### 2.1 它解决的关键问题

1. **说话人归属（speaker attribution）**——「这句引号里的话是谁说的」，多角色配音的前提
2. **角色别名归并**——"Vernon Dursley" / "Mr. Dursley" / "Uncle Vernon" 是同一人
3. **角色 → 音色映射**——把「性别 + 年龄」量化成 1–10 的 `gender_score` 再查表
4. **长文本 TTS 工程化**——并发、重试、内存、章节切分、静音插入、M4B 封装
5. **LLM 输出不可信**——大量代码在做输出校验与回滚

### 2.2 核心执行流程

```
① book_to_txt.py                 文本提取与清洗
     ↓ converted_book.txt
② identify_characters_...py      两遍式角色识别
     ↓ character_gender_map.json + speaker_attributed_book.jsonl
③ add_emotion_tags.py            情感标签（可选，仅 Orpheus）
     ↓ tag_added_lines_chunks.txt
④ generate_audiobook.py          音频生成与封装
     ↓ generated_audiobooks/*.m4b
```

**两遍式角色识别（本项目的皇冠明珠）：**

- **预处理**：`preprocess_text_into_lines()` 把 `He said, "Hello," and left.` 切成 narrator / dialogue / narrator 三个 item，下游统一成线性序列
- **Pass 1**（`extract_all_characters_from_full_text`）：按 token 预算分批扫全文，增量构建角色表。每个角色带显式 `operation` 枚举：`insert` / `update` / `merge`，`merge` 还要给出 `existing_name`——**别名归并被建模成显式状态机操作**
- **Pass 2**（`attribute_speakers_to_dialogue`）：角色表已完备，逐条对话只做**纯匹配**，附带前后文（带已归属的说话人标注）和「含对话标签的原始整行」

**关键洞察：Pass 1 负责「创造」，Pass 2 只负责「匹配」。** 这个解耦把 Pass 2 从开放式生成降级成了闭集分类，幻觉空间被物理压缩。这是整个项目最值得继承的思想。

### 2.3 技术栈

| 层 | 选型 |
|---|---|
| UI / 服务 | Gradio 5 + FastAPI + uvicorn，async generator 做进度流 |
| 电子书解析 | textract；Calibre `ebook-convert` / `ebook-meta` |
| LLM 调用 | OpenAI 兼容端点，`AsyncOpenAI` |
| 结构化输出 | **pydantic-ai** `Agent(output_type=...)` + Pydantic `Literal` 枚举，`retries=3` |
| Token 预算 | tiktoken `cl100k_base` |
| TTS | Kokoro / Orpheus，走 OpenAI 兼容的 `audio.speech` |
| 并发 | `asyncio.Semaphore` + `create_task` + `asyncio.wait(FIRST_COMPLETED)` |
| 音频 | pydub（片段级）+ ffmpeg/ffprobe（章节级、封装级） |

### 2.4 值得继承的设计（思想层面）

1. **两遍式 LLM 流水线**——创造与匹配解耦
2. **用类型系统替代提示词校验**——`Literal["insert","update","merge"]` + pydantic-ai 自动把校验错误回灌给模型重试。代码注释直言 *"No validation needed - Literal types guarantee valid values!"*
3. **对 LLM 故障做分类重试**——`is_context_overflow_error()` 专门识别「模型复读导致上下文溢出」，重试时递增 presence/frequency/repeat penalty；其他错误直接抛出
4. **输出后校验 + 整体回滚 + 降级**——`postprocess_emotion_tags()` 做五道检查（标签白名单、提示词结构泄漏、**剥离标签后逐字符比对原文**、行数一致、标签格式完整），任一失败则整块回退原文，再降级为逐行处理
5. **按需调用 LLM**——情感标签用正则先定位关键词窗口，只处理 10–30% 的文本
6. **中间产物落盘，每步可人工介入**
7. **内存策略分层**——短片段用 pydub，整章/整书用 ffmpeg `-c copy`（零解码零内存）
8. **容错优先于中断**——单片段 TTS 失败只跳过，整本仍能产出
9. **命令执行安全**——列表式 `subprocess`（无 `shell=True`）+ 命令名白名单 + 参数**正则白名单** + 拒绝路径穿越

### 2.5 结构性缺陷（新项目必须改掉的）

| 缺陷 | 说明 |
|---|---|
| **全局固定文件名** | `converted_book.txt` / `temp_audio/` / `generated_audiobooks/audiobook.m4b` 都是进程级共享路径，无法多任务并行 |
| **无中间表示** | 临时文件靠**行数对齐**隐式耦合。`apply_emotion_tags_to_multi_voice_data()` 用 `len(a) == len(b)` 匹配两个文件，任何空行处理不一致就整体失效 |
| **Pass 2 纯串行** | 逐行 LLM 调用且无并发，长篇需数小时 |
| **无断点续跑** | 中断即全部重来 |
| **age 被吞掉** | 抽出了 child/adult/elderly，但压成 `gender_score` 后音色选择只看得到数字（见 §3.3 实证） |
| **TTS 假设 HTTP** | 硬编码引擎分支，无法接入进程内库（如 mlx-audio） |
| **代码重复** | 单音色与多音色生成是两个近乎相同的 ~280 行函数 |
| **异常吞噬** | `extract_main_content()` 捕获异常后静默返回原文 |

---

## 3. 中文支持现状：landandan fork 评估

| 项目 | [landandan/audiobook-creator](https://github.com/landandan/audiobook-creator) |
|---|---|
| 与上游关系 | ahead 5 / behind 0（完全跟上上游最新） |
| 提交时间 | 2026-09-15 ~ 09-16 |
| 许可证 | GPL-3.0（继承） |
| 本地副本 | `/Users/ygwang/src/landandan-audiobook-creator` |

**这是 47 个 fork 中唯一做了中文支持的**（另一个有实质改动的 `chriswritescode-dev` 是英文向重构，已落后上游 53 个提交）。

### 3.1 fork 内容

新增 `utils/lang_config.py`（242 行）作为单一语言配置中枢，由 `BOOK_LANGUAGE` 环境变量驱动。设计说明明确写着 *"English behaviour is byte-for-byte identical to the original project."*

关键实现：

```python
# 章节识别（含中文数字转 int，支持「第一千三百四十五章」）
_ZH_CHAPTER_NUM = r'[零〇一二三四五六七八九十百千万两\d]+'
ZH_CHAPTER_HEADING_PATTERN = re.compile(
    rf'^\s*(第{_ZH_CHAPTER_NUM}[章回卷部节集]|楔子|序章|序言|尾声|终章|番外篇?)'
)

# 对话引号（三种样式）
ZH_DIALOGUE_PATTERN = re.compile(r'“[^”]+”|「[^」]+」|『[^』]+』|"[^"]+"')

# 全角标点集
ZH_PUNCTUATION = '，。！？；：、…—～·“”‘’（）《》〈〉【】「」『』〔〕％‰'
```

还处理了网文的无分隔符标题「第一章陨落的天才」，并用 `ZH_PROSE_CONTINUATION_PREFIXES` 前缀黑名单排除「第一章**的**内容很长」这类误判。

中文提示词质量很高，作者显然读过中文网文：

> 规范名优先级：全名 > 姓氏+称谓 > 职务称呼 > 绰号/昵称
> 注意：同姓通常是不同人（"萧战"与"萧炎"是两个人）
> 性别判断线索：代词（他/她）、称谓（公子/姑娘/小姐/夫人/少年/老者/前辈）

### 3.2 实测验收结果（2026-09-22）

用 40 行自造中文样本（含三种章节写法、`""` 与 `「」` 两种引号、三个带性别线索的角色）跑完整流程：

**说话人归属：11/11 全对**，其中四处是真有难度的用例：

| 台词 | 难点 | 结果 |
|---|---|---|
| 「这位公子，借个火。」 | **前向引用**——此处全名尚未出现，原文只说"一个清亮的女声" | ✅ 归到最终规范名 |
| "苏姑娘？" | **称呼陷阱**——句中出现她的名字，最易误判成她自己说的 | ✅ 正确归给林渊 |
| "你看过了？" | **后置标签**——说话人写在下一行（中文小说典型写法） | ✅ |
| "我等你很久了。"…"师父让我把这个交给你。" | **被旁白打断的连续对白** | ✅ |

**别名合并生效**：只喂前 5 行时抽出的是「苏姑娘」，跑完整篇后角色表里只有「苏晚晴」，无残留。

**跨章节信息整合**（超出预期）：老者的描述写成「…却在苏晚晴提醒客栈不干净后显得更加可疑」，把第二章苏晚晴的警告和老者咳嗽的细节联系起来了。

其他：角色数正确、未误抽取地点/物品、年龄判断正确（从「老者」二字推出 elderly）。

### 3.3 发现的缺陷：音色撞车（已验证）

把角色表代入音色映射：

```
narrator  score=0  female/adult    -> zh-CN-XiaoxiaoNeural
林渊       score=1  male/adult      -> zh-CN-YunyangNeural
苏晚晴     score=7  female/adult    -> zh-CN-XiaoyiNeural
客栈老者   score=1  male/elderly    -> zh-CN-YunyangNeural   ← 与林渊同音色
```

**一个青年男主角和一个白发老店家用了同一把嗓子，仅 3 个角色的样本就撞车。**

根因在 `calculate_gender_score()`：

```python
elif age == "adult":   gender_score = random.choice([1, 2, 3])   # 成年男
elif age == "elderly": gender_score = random.choice([1, 2])      # 老年男
```

**两个区间重叠**。更根本的是：**管线抽出了 age，却在压成 gender_score 时丢弃了它**，音色选择只看得到那个数字。这是上游的架构局限，不是 fork 引入的。

建议修法（未实施）：`adult → random([2,3])`、`elderly → 1`（女性同理 `elderly → 6`、`adult → random([7,8,9])`），并把 voice_map 的 1/2/3 按「沉稳/浑厚/年轻」拉开。

### 3.4 本 session 中对该 fork 做的修改（已实施，未提交）

| 文件 | 改动 |
|---|---|
| `static_files/voice_map.json` | 新增 `edge` 引擎条目；**修掉 `kokoro_zh` 的方言音色 bug**（见 §5.2） |
| `utils/check_if_audio_generator_api_is_up.py` | 探测音色改为从 voice_map 读取，不再硬编码引擎分支 |
| `generate_audiobook.py` | `AudioSegment.from_wav()` → `from_file()`（兼容返回 mp3 的引擎） |
| `utils/character_extraction_llm.py` | 新增 `OPENAI_REASONING_EFFORT` 环境变量与 `build_model_settings()`（见 §6.4） |

**待办**：方言音色 bug 应该单独提 issue 给 landandan —— 纯 bug 修复，无许可证纠缠。

---

## 4. 开源格局调研（2026-09）

### 4.1 中文相关项目

| 项目 | Star | 技术栈 | 自动说话人归属 | 主要短板 |
|---|---|---|---|---|
| [sdsds222/Unitale](https://github.com/sdsds222/Unitale) | 222 | 浏览器前端 + IndexTTS2 + Qwen3-TTS | ✅ LLM 拆剧本 + 情绪识别 | 定位是**广播剧**非有声书；无 M4B 无章节；IndexTTS 需云端部署 |
| [cosin2077/easyVoice](https://github.com/cosin2077/easyVoice) | 2.3k | TypeScript + Edge TTS | 有多角色但非自动归属 | 通用 TTS 工具，无 epub/章节/M4B |
| [LiberSonora](https://github.com/LiberSonora/LiberSonora) | 470 | Python | ❌ | 方向相反：给**已有**有声书做字幕提取/翻译，不生成音频；2025-07 停更 |
| [wu-boshi/B2A-Studio](https://github.com/wu-boshi/B2A-Studio) | 28 | Web UI，MIT | ✅ 拆剧本与演员表 + 试镜绑定音色 | 仅 TXT 输入，输出章节 MP3 + LRC；2026-06 后未更新 |
| [z443208468/youshengshu](https://github.com/z443208468/youshengshu) | 0 | LM Studio + CosyVoice | 部分 | 极早期，Windows 桌面，无 license |
| [zhhs-git/Voiceover](https://github.com/zhhs-git/Voiceover) | 0 | Python | ? | 极早期 |

### 4.2 英文大项目

| 项目 | Star | 自动说话人归属 | 中文 |
|---|---|---|---|
| [DrewThomasson/ebook2audiobook](https://github.com/DrewThomasson/ebook2audiobook) | **20.2k** | ❌ 只有手动 `[voice:...]` 标记，**完全不用 LLM** | 支持，但 roadmap 仍写着「改进中文断句和停顿」 |
| [santinic/audiblez](https://github.com/santinic/audiblez) | 8.6k | ❌ 单音色 | 靠 Kokoro zh |
| [aedocw/epub2tts](https://github.com/aedocw/epub2tts) | 960 | ❌ 单音色 | 一般 |
| [khimaros/autiobook](https://github.com/khimaros/autiobook) | 28 | ❌ | qwen3-tts / 任意 OpenAI 兼容端点，GPL-3 |
| [**Finrandojin/alexandria-audiobook**](https://github.com/Finrandojin/alexandria-audiobook) | **1.0k** | ✅ LLM 剧本标注 + 二次复核 + 角色画像 → VoiceDesign 音色 | 支持（多语言之一）。MIT，基于 Qwen3-TTS，导出分章 M4B / Audacity 多轨。**Apple Silicon 仅 CPU**（README：*"MPS acceleration is not currently supported. Functional but slow"*），主力部署是 NVIDIA + Docker |
| [dudarenok-maker/Castwright](https://github.com/dudarenok-maker/Castwright) | 16 | ✅ LLM 分配角色音色，跨系列保持一致 | 七种语言之一。Kokoro / Qwen3-TTS，M4B + Audiobookshelf 导出；活跃开发中 |

> Alexandria 与 Castwright 是 2026-09-23 命名调研时新发现的，本文档初版调研未覆盖。

### 4.3 结论

**20k star 的 ebook2audiobook 都没有自动说话人识别**，只能手工打标记。

但「**LLM 自动说话人归属 → 多角色音色 → 标准有声书封装**」这条链路**已不是空白**：Alexandria（1k★，2026-02 创建，同样基于 Qwen3-TTS）已经做到了相当完整的程度，Castwright、B2A-Studio 等也在同一方向上。

**仍然没有人占据的是「中文优先 + Apple Silicon 本地推理（MLX）」这一层**：Alexandria 把中文当作多语言之一，且在 Mac 上只能跑 CPU；中文项目则普遍依赖云端 TTS 或 NVIDIA GPU。本项目的差异化应收窄到这里，而不是宣称整条链路独一份。

**待办**：通读 Alexandria 的剧本 JSON 格式与「LLM Script Review」二次复核设计，作为 Script IR（§8.3）的参考输入（MIT 许可，可参考）。

---

## 5. TTS 选型

### 5.1 候选与实测结果

| 引擎 | 中文质量 | 本地 | 免费 | 实测结论 |
|---|---|---|---|---|
| **Qwen3-TTS 1.7B**（mlx-audio） | **优秀** | ✅ | ✅ | ✅ **主力选择** |
| Qwen3-TTS 0.6B | 良好 | ✅ | ✅ | 明显弱于 1.7B，但速度差距不大 |
| edge-tts | 良好（Azure 原版） | ❌ 需联网 | ✅ | 可用，但音色少、非官方接口 |
| Kokoro zh | 中下（规范但生硬） | ✅ | ✅ | 82M 参数的 Azure 蒸馏版，音质天花板低 |
| OpenAI TTS | **差** | ❌ | ❌ | ❌ **实测淘汰** |

### 5.2 关键发现：Kokoro 中文音色是 Azure 蒸馏版

Kokoro 的 8 个中文音色名与微软 Azure 的 zh-CN 神经网络语音**一一对应**：

```
zf_xiaoxiao / zf_xiaoyi / zf_xiaobei / zf_xiaoni
zm_yunjian / zm_yunxi  / zm_yunxia  / zm_yunyang
```

Kokoro 模型卡声明训练数据包含「来自主流闭源 TTS 厂商的合成音频」。**Kokoro 的中文本质上是 Azure 中文语音的 82M 参数蒸馏版。**

**其中两个是方言音色**（edge-tts 的元数据直接标注为 `Dialect`）：

| 音色 | Azure 全名 | 实际口音 | 已实测 |
|---|---|---|---|
| `zf_xiaobei` | `zh-CN-**liaoning**-XiaobeiNeural` | **辽宁（东北）口音** | ✅ 确认 |
| `zf_xiaoni` | `zh-CN-**shaanxi**-XiaoniNeural` | **陕西口音** | — |

landandan fork 的 `kokoro_zh` 映射里，这两个方言音色占了 5 个槽位，包括 `female_dialogue` 和分档 9、10——**绝大多数女性角色都会带方言口音**。作者大概率只是按名字凑满了分档表。已在本地修复。

### 5.3 OpenAI TTS 实测淘汰

用同一句中文测试 5–6 种音色，结果：「要么没有读全，要么就是浓重的外国腔调，比较好的也是浓重的台湾国语口音」。**不可用。**

### 5.4 edge-tts 实测

- 走微软 Edge「大声朗读」后端，**就是 Azure 神经网络语音本体，未蒸馏**
- 封装：[travisvn/openai-edge-tts](https://github.com/travisvn/openai-edge-tts)，OpenAI 兼容 `/v1/audio/speech`
- **可用标准普通话音色只有 6 个**（4 男 2 女）+ 2 个方言音色：

| 音色 | 性别 | 风格 |
|---|---|---|
| `zh-CN-XiaoxiaoNeural` | 女 | News, Novel / Warm |
| `zh-CN-XiaoyiNeural` | 女 | Cartoon, Novel / Lively |
| `zh-CN-YunjianNeural` | 男 | Sports, Novel / Passion |
| `zh-CN-YunxiNeural` | 男 | Novel / Lively, Sunshine |
| `zh-CN-YunxiaNeural` | 男 | Cartoon, Novel / Cute（少年音） |
| `zh-CN-YunyangNeural` | 男 | News / Professional, Reliable |

**已知限制：**
- 音质上限 **24kHz / 48kbps 单声道 MP3**（Edge 朗读服务的原生格式，改不了）
- Docker 镜像**不含 ffmpeg**，导致 `response_format` 完全失效——请求 wav/pcm/flac 返回的全是 MP3（`INSTALL_FFMPEG_ARG=true` 实测无效）
- 非官方接口，ToS 灰色地带，有速率限制，**不适合商用分发**
- 不支持 pitch/rate 变体，6 个音色是硬上限

**定位**：作为无 GPU 环境的备选，或英文场景的低成本选项。

### 5.5 ✅ Qwen3-TTS 实测数据（主力方案）

**环境**：M1 Mac（4–5 年前配置），mlx-audio，30 多万字中文小说，单音色

| 模型 | 耗时 | 产出音频 | 分块数 | 重试 | RTF |
|---|---|---|---|---|---|
| `Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit` | **6:20:51** | 18:54:16 | 1305 | 4 | ~2.98x |
| `Qwen3-TTS-12Hz-1.7B-CustomVoice-8bit` | **7:19:54** | 19:34:33 | 1305 | 3 | ~2.67x |

**结论：**

1. 在 4–5 年前的 M1 上即可获得**可接受的转换速度**
2. **中文效果非常好**，1.7B 明显优于 0.6B
3. **1.7B 仅比 0.6B 慢约 15%**，中文任务应**默认推荐 1.7B**
4. 英文表现虽未大规模测试，但应可接受——足以支撑一个中英双语项目

**许可证（全部宽松，Apache-2.0 路线畅通）：**

| 组件 | 许可证 |
|---|---|
| [Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS)（代码 + 权重） | **Apache-2.0** |
| [mlx-audio](https://github.com/Blaizzy/mlx-audio) | **MIT** |
| Qwen3-ASR、Qwen3-ForcedAligner（权重，ASR 校验与 EXP-3） | **Apache-2.0** |
| numpy、scipy | BSD-3-Clause |
| pyloudnorm（响度测量） | MIT |
| Pillow（生成文字封面） | MIT-CMU（HPND） |
| OpenCC（ASR 校验时繁转简） | Apache-2.0 |
| pydantic-ai（LLM 接入） | MIT |
| pypinyin（仅 EXP-3 实验脚本） | MIT |
| ffmpeg / calibre | GPL/LGPL，但**以子进程调用**，不构成链接 |

刻意避开的：EbookLib（AGPL，改为标准库解析 EPUB）、zhconv（GPL，改用 OpenCC）、audiobook-creator 的代码与提示词（GPL，§8.2）。

### 5.6 Qwen3-TTS 预置音色（2026-09-23 核查）

CustomVoice 模型（0.6B 与 1.7B 相同）的 `config.json` 中 `talker_config.spk_id` 列出 9 个预置音色，`spk_is_dialect` 标注了其中的方言音色：

| 音色 | 语言 | 方言 |
|---|---|---|
| `vivian`、`serena`、`uncle_fu` | 中文 | 否（标准普通话） |
| `dylan` | 中文 | **北京话**（`beijing_dialect`） |
| `eric` | 中文 | **四川话**（`sichuan_dialect`） |
| `ryan`、`aiden` | 英文 | 否 |
| `ono_anna` | 日文 | 否 |
| `sohee` | 韩文 | 否 |

**结论：标准普通话预置音色只有 3 个，英文只有 2 个，不足以支撑多角色选角。** 与 §5.2 的 Kokoro 同理，方言音色必须默认排除。

#### 预置音色的实现机制（读 mlx-audio 源码与官方微调脚本确认）

三个变体共用同一个 talker，**「音色」在输入端就是一个 2048 维向量**，放在 codec 前缀里的同一个位置（mlx-audio `qwen3_tts.py` 的 `_prepare_generation_inputs()`）：

| 变体 | 这个向量从哪来 | 其他能力 |
|---|---|---|
| **CustomVoice** | `spk_id` 查 talker 的 codec embedding 表的一行（如 `serena` → 第 3066 行） | 支持 `instruct`（情绪/语气）；**没有** speaker encoder |
| **Base** | 内置 speaker encoder 从参考音频提取（x-vector） | 另有 ICL 模式：参考音频的 codec 码 + 参考文本一起作为提示，相似度更高 |
| **VoiceDesign**（仅 1.7B） | 无固定向量，由 `instruct` 文字描述决定 | 每次生成的声音可能不同，**不能直接逐句使用** |

官方微调脚本 [`finetuning/sft_12hz.py`](https://github.com/QwenLM/Qwen3-TTS/blob/main/finetuning/sft_12hz.py) 印证了这一点：它用 Base 的 speaker encoder 从目标说话人的音频提取向量，**写进 `codec_embedding.weight[3000]`**，在 config 里注册 `spk_id = {name: 3000}`、把 `tts_model_type` 改为 `custom_voice`，保存时删掉 speaker encoder 权重。也就是说，**CustomVoice 的预置音色和 Base 克隆用的 x-vector 位于同一个向量空间**，区别在于微调过的 talker 学会了用好这一行。

#### 固化库音色的四条路线

内置音色库（[requirements.md](requirements.md) CAST-1，§9.1 Q2）的每个音色都先用 VoiceDesign 按描述生成、人工挑选一段满意的参考音频，然后用下面某种方式在所有书中复用：

| 路线 | 做法 | 优点 | 风险 / 未知 |
|---|---|---|---|
| **A. Base + ICL 克隆** | 每句都带参考音频 + 参考文本 | 相似度最高，mlx-audio 现成支持 | 每句多一段提示（10 秒参考约 125 帧）；mlx-audio 在 ICL 模式下把 repetition penalty 提到 1.5，可能影响韵律；不支持连续批处理 |
| **B. Base + x-vector** | 只存一个 2048 维向量（约 8 KB），不带参考文本 | 最轻；机制上与预置音色完全相同 | 只靠向量，相似度与稳定性可能不如 A |
| **C. 向量写入 CustomVoice** | 用 Base 的 speaker encoder 算出向量，追加到 CustomVoice 的 embedding 表并注册 `spk_id`（或给 mlx-audio 加一个直接传向量的入口） | **一个模型同时提供预置旁白与库音色**（无需加载两个模型）；**可能顺带获得 `instruct` 情绪控制**（SCR-12） | CustomVoice 只在 9 个音色上微调过，对没见过的向量能否泛化**完全未知**；官方做法是写入后还要微调 |
| **D. 微调** | 用 VoiceDesign 为每个音色生成语料，按官方 SFT 流程训练 | 真正的「内置音色」，质量上限最高 | 官方脚本一次只注册一个说话人，多音色需改造；训练基于 PyTorch/CUDA，不在 Mac 上；要发布派生的 1.7B 权重。**不在第一阶段范围** |

**EXP-1 音色实验的做法**（[requirements.md](requirements.md) §6）：用 VoiceDesign 试做 4–6 个中文音色，同一批测试句分别走 A、B、C，按关卡标准（稳定性、可懂度、区分度、口音、速度）比较。C 若可行则优先（单模型 + instruct），否则 A 或 B。无论哪条路线，库里保存的都是**参考音频 + 参考文本 + 向量 + 标签**，不绑定某一条路线。

**库音色与输入无关的好处**：质量由人工筛选兜底，所有书听到的都是同一批经过验收的声音；处理新书时不用花时间设计声音；VoiceDesign 生成的声音不属于任何真人，没有声音权属问题（NFR-10）。

#### EXP-1 结果（2026-09-23 至 09-24，路线 C，已通过关卡）

**环境**：M1 Max，mlx-audio `cd605ec`，三个 1.7B 模型均为 8bit；脚本 `experiments/exp1_voice_routes.py`。每句固定 seed，每条路线每句只生成一次，**只有一名听者**——以下是方向性结论，不是关卡结果。

**路线 C 的注入方法**：CustomVoice 只在构造提示词时查一次 speaker 表，因此不改 8bit 量化的 embedding 表，而是在 `_prepare_generation_inputs()` 期间拦截对第 3000 行（官方微调脚本使用的空行）的查询，返回 Base speaker encoder 从参考音频提取的向量；逐帧生成仍使用原表。这是对 mlx-audio 私有方法的 monkeypatch，正式实现应改为给 mlx-audio 提一个「直接传 speaker 向量」的接口（上游 MIT，适合回馈）。

**第一轮：简单样例**（参考音频是 CustomVoice `serena` 自己的输出）

| 片段 | 听感 |
|---|---|
| CustomVoice `serena` 读客栈老者的台词 | 同一个人，但「像是在模拟男人的样子说话」——**CustomVoice 会按台词内容调整表演** |
| A（ICL） | 年轻女声，语速偏快，情绪更接近参考音频而不是新台词（照搬参考的说法） |
| B（x-vector） | 「非常像」参考音频，语速、情绪、发音都好 |

**第二轮：困难样例**（VoiceDesign 设计的六十多岁、略沙哑、语速偏慢的客栈掌柜；参考音频 17 秒、两句话；三句测试台词：平静 / 惊恐 / 先笑后疑）

| 路线 | 听感 | 语速（字/s） | 与参考的音色相似度* | RTF |
|---|---|---|---|---|
| 参考音频（VoiceDesign） | 「非常好的老者声音」，语速、情绪好，重音这次也较准 | 2.9 | 1.00 | 2.65x |
| A Base + ICL | 老者，接近参考但**显得年轻些**，语速略快 | 3.6–3.8 | 0.92–0.93 | **1.5–1.9x** |
| B Base + x-vector | 比 A 稍差，**更年轻**，语速略快 | 3.4–3.7 | 0.92–0.94 | 2.8x |
| **C CustomVoice + 注入向量** | **「与参考很接近」**，语速好，情绪好 | 2.4–2.5 | 0.92 | 2.8x |
| C + instruct「用惊恐、压低声音的语气说」 | **准确体现了指令，比不加指令生动很多**，音色不变 | 2.7 | 0.88 | 2.8x |
| 预置 `uncle_fu`（对照） | 另一位老人，与 C 明显可区分 | 3.1 | 0.72 | 2.8x |

\* Base speaker encoder 提取的向量，减去 9 个预置向量的均值后求余弦（未中心化时任意两个音色都在 0.93 左右，没有区分度）。

**结论（初步）：**

1. **路线 C 在困难样例上最好**，且没有塌缩到最近的预置音色（C 与 `uncle_fu` 的相似度 0.33，与参考音频本身的 0.32 相同）。同一个向量，经 CustomVoice 渲染比经 Base 渲染更能保住年龄感——推测是 CustomVoice 的 talker 经过说话人条件微调，更善于使用这个向量
2. **注入的音色支持 `instruct`**，而且效果明显。这让 SCR-12（情绪提示）不再受模型能力限制，已由 P2 调为条件 P1（[requirements.md](requirements.md) §9.1 Q15）
3. **运行时只需要 CustomVoice 一个模型**：预置旁白与库音色都走它。VoiceDesign 和 Base 只在**建库时**使用（设计声音、提取向量），合成一本书时不用加载
4. **路线 A 淘汰**：两轮都偏快、偏离新台词，且最慢
5. **音色向量相似度不足以单独作为关卡指标**：它能区分不同的人（0.92 vs 0.72），但分辨不出 A / B / C 在年龄感上的差别——关卡以盲听为主，向量相似度只作下限检查

**第三轮：6 个音色的选定与稳定性**（脚本 `experiments/exp1_candidates.py`、`experiments/exp1_stability.py`；音色定义与听感记录在 `experiments/exp1_voices.json`）

*3a 选参考音频*：除已通过的老年男声外，新增女童、少年、青年女（清冷）、中年女（泼辣）、青年男（低沉）5 个音色，每个用 VoiceDesign 以 3 个 seed 各生成一段参考音频，由听者挑选。

- 同一描述、不同 seed 的差异因音色而异：女童几乎一致（向量相似度 0.93–0.94），少年差异很大（0.61–0.81，基本是三个不同的人）。**建库时每个音色都应生成多个候选再挑选**，听者的选择与向量指标无关，只能靠人耳
- 选定音色之间、与 5 个中文预置音色之间都没有过近（全部 < 0.85）；最近的一对是老年男与青年男（0.80），第三轮中两者并未混淆
- 同一 seed 重跑得到逐字节相同的音频：参考音频可由「描述 + 文本 + seed」完全复现

*3b 稳定性*：6 个音色注入同一个 CustomVoice，各合成 20 段——叙述 4、平静对白 4、情绪对白 4（仅靠文本）、instruct 4（愤怒 / 哭腔 / 耳语 / 欢快）、同一句换 3 个 seed、一段约 400 字的长段落。

| 音色 | 与参考相似度 均值/最低 | 被认出为自己* | ASR 字错率 | 字/s | instruct 下相似度 |
|---|---|---|---|---|---|
| 老年男 | 0.89 / 0.75 | 19/20 | 0.2% | 2.5 | 0.83 |
| 女童 | 0.87 / 0.80 | 20/20 | 0.2% | 3.7 | 0.84 |
| 少年男 | 0.84 / 0.68 | 18/20 | 0.4% | 4.2 | 0.74 |
| 青年女 | 0.88 / 0.73 | 19/20 | 0.2% | 3.6 | 0.81 |
| 中年女 | 0.82 / 0.69 | 20/20 | 1.8% | 3.6 | 0.75 |
| 青年男 | 0.88 / 0.64 | 19/20 | 0.2% | 3.5 | 0.81 |

\* 在 6 个参考音频 + 5 个预置音色中，与该片段向量最近的是否是它自己的参考。

- **文本准确**：长段落也几乎无漏字错字。唯一的问题是中年女音色两次把句末「告诉**你**」的「你」读得很弱（ASR 判为漏字，人耳能听到但很轻）——**ASR 能抓到这类问题，可作为合成校验的一部分**（已列为 [requirements.md](requirements.md) SYN-10，可选、默认开启）
- **5 次「认错」全部出在 instruct 片段**（愤怒 ×2 → `uncle_fu`，哭腔 ×2 → `eric` / `serena`，耳语 ×1 → 青年男）。但人耳判断：愤怒未变成 `uncle_fu`，哭腔无四川口音、只是略向 `serena` 靠近但仍明显不同，耳语反而「显得比其他片段老」。**向量相似度在情绪强烈时会误报**，人耳为准
- 长段落中 1.2–1.5 秒的停顿听感自然；RTF 约 2.7x，与预置音色相同

**听感总评**：75–80 分。「主要问题在于情绪变化时，音色也会有变化，但变化不算大。就是那种感觉说话者情绪变了，有点儿像换了人，但再想想，也可以理解是同一个人不同情绪的声音那种。可接受，很多地方不细听的话也听不出来这种变化。」

**结论：EXP-1 通过关卡**（[requirements.md](requirements.md) §9.1 Q16）。

1. 固化方式定为**路线 C**：VoiceDesign 生成参考音频（记录描述、文本、seed）→ Base speaker encoder 提取 2048 维向量 → 注入 CustomVoice。库中每个音色保存：描述、参考文本、seed、参考音频、向量、标签、听感记录
2. 情绪变化时的音色漂移是已知局限：instruct 默认只用程度适中的描述；根治只能靠微调（路线 D），不在第一阶段
3. 待办：给 mlx-audio 提「直接传 speaker 向量」的接口，替换当前对私有方法的 monkeypatch；M3 中补齐其余 10 个音色，每个都走「多 seed 候选 → 人工挑选 → 稳定性检验」
4. 英文音色库另做一轮同样的检验（CAST-2）

#### M3 音色库（2026-09-24 至 09-25）

**流程**：与第三轮相同。每个新音色写一段 VoiceDesign 描述与参考文本，以 3 个 seed 生成候选（`experiments/exp1_candidates.py`），听者挑一个；再做 20 句稳定性检验（`experiments/exp1_stability.py --spec experiments/m3_voices.json`），由听者按标注的时间点复核可疑片段。`experiments/m3_build_library.py` 把选定的向量与描述写入 `src/huashuo/voices/zh/<id>.{json,npy}`。每个音色的 JSON 记录描述、参考文本、seed、所用模型与听感记录，参考音频可由这些信息逐字节复现。

**16 个中文音色**（加粗为 M3 新增；另有 4 个标准普通话预置 `serena`、`vivian`、`uncle_fu` 可用于选角，`serena` 默认作旁白）：

| | 儿童 | 少年 | 青年 | 中年 | 老年 |
|---|---|---|---|---|---|
| 男 | **`boy`** | `teen_boy` | `young_man_deep`、**`young_man_warm`** | **`mid_man_steady`**、**`mid_man_jovial`** | `old_man_hoarse`、**`old_man_kind`** |
| 女 | `girl` | **`teen_girl`** | `young_woman_cool`、**`young_woman_warm`** | `mid_woman_brisk`、**`mid_woman_calm`** | **`old_woman_kind`**、**`old_woman_stern`** |

**稳定性**（新增 10 个，指标同第三轮；「被认出」在 16 个参考 + 5 个预置中判断）：

| 音色 | 与参考相似度 均值/最低 | 被认出为自己 | ASR 字错率 | 字/s | instruct 下相似度 |
|---|---|---|---|---|---|
| `boy` | 0.86 / 0.66 | 17/20 | 0.6% | 4.3 | 0.80 |
| `teen_girl` | 0.88 / 0.80 | 20/20 | 0.2% | 4.2 | 0.86 |
| `young_man_warm` | 0.84 / 0.47 | 19/20 | 0.2% | 4.3 | 0.71 |
| `young_woman_warm` | 0.85 / 0.75 | 16/20 | 0.6% | 4.5 | 0.78 |
| `mid_man_steady` | 0.87 / 0.69 | 17/20 | 0.2% | 3.5 | 0.82 |
| `mid_man_jovial` | 0.85 / 0.77 | 18/20 | 0.2% | 3.5 | 0.83 |
| `mid_woman_calm` | 0.87 / 0.78 | 16/20 | 0.5% | 3.7 | 0.82 |
| `old_man_kind` | 0.86 / 0.67 | 19/20 | 0.2% | 2.8 | 0.74 |
| `old_woman_kind` | 0.74 / 0.46 | 17/20 | 0.2% | 3.5 | 0.55 |
| `old_woman_stern` | 0.80 / 0.62 | 15/20 | 3.0% | 3.1 | 0.70 |

- **向量指标再次误报口音**：`mid_man_jovial` 的两段（叙述、耳语）最接近 `dylan`（北京话），`old_man_kind` 的愤怒片段最接近 `eric`（四川话）。听者复核：音色与其他片段有区别，但**都是标准普通话**；愤怒片段「更像年轻人」。与第三轮的结论一致——强烈情绪下向量相似度不可靠，口音以人耳为准
- **`old_woman_kind` 第一版被否**：听者认为「不够老，像五十岁左右」。改写描述后重新生成，第二版 s2 通过（s0 也可以）。它在情绪下的相似度最低（0.55），向少年音色靠近，是情绪提示需要措辞温和的又一个理由
- **`old_woman_stern` 容易丢句末字**：「……应该告诉你」四次中，一次「你」很轻但能听到，三次几乎听不到。这促成了 §5.10 的句末检查
- **超过十个库音色时注入会撞行**：预置音色在 speaker 表中的行号是分散的（`uncle_fu` 在 3010），从 3000 起顺序分配的第 11 个库音色会覆盖 `uncle_fu`。改为跳过所有已被占用的行（`engines/qwen3_voices.py`），并加了注册 20 个音色的回归测试

### 5.8 口音漂移（M1 实测，2026-09-24）

**现象**：《吶喊》两章的 M4B 中，1:28–2:52 一段突然变成**陕西口音**，前后都是标准普通话。这一段恰好是一个完整的合成单元（「伊伏在地上……」，83 秒）：漂移从单元开头开始、在单元结尾结束。

**排查**（脚本在当次会话的临时目录，结论如下）：

| 变体 | 听感 |
|---|---|
| 同一 seed 重跑 | 陕西口音；与缓存逐字节相同——对给定文本和 seed 是确定的 |
| seed + 1 / seed + 2 | 基本是普通话（一个偏慢、一个偏快） |
| 「伊」改为「她」，同一 seed | 基本是普通话 |
| instruct「用标准普通话朗读」，同一 seed | 基本是普通话，但明显变慢、长停顿多（16 处 > 0.8 秒） |
| temperature 0.9 → 0.7，同一 seed | 基本是普通话 |

**结论**：这是**随机的低概率漂移**，不是文本或参数的必然结果——只换 seed 就能避开。其他变体「有效」多半只是因为改动也改变了采样路径；每种变体只有一个样本，**不能说明 instruct 或降温能降低漂移概率**。模型本身没有陕西方言的语言 ID（只有北京话、四川话），我们请求的也是普通话，漂移完全是模型自发的。

**现有校验都抓不到**：

- ASR 语言识别：所有片段（包括北京话、四川话预置音色）都只报 `Chinese`
- 说话人向量：漂移单元与 serena 参考的相似度 0.90，和正常单元一样——它衡量「谁在说」，不衡量「怎么发音」
- ASR 字错率：漂移版本最高（7.6%，其他变体 4.9–6.9%），但差距不足以作判据

**影响**：在找到检测方法之前，漂移会静默通过所有校验。本书样本中 20 个单元出现 1 次，真实频率未知。

**与文本的关系（M2 之后的全书试用，2026-09-24）**：用整本《吶喊》（繁体、多古白话词语）试用时，错读与口音漂移都很频繁：简单字读错（ASR 转写出「吶喊」→「爹汉」、「《吶喊》自序」→「大喊自叙」），并且经常漂移到陕西口音。同一流程处理格非《春尽江南》（简体、现代白话，约 21 万字）的试听则是标准普通话，基本没有错音。推测 Qwen3-TTS 对繁体字与古代词语的训练不充分，而这类文本也更容易触发口音漂移。**结论**：第一阶段的「中文」优先保障简体中文、现代白话；繁体与古典作品不作为质量目标，也不作为测试依据（[requirements.md](requirements.md) §9.1 Q18）。

**是否每次合成都加「用普通话朗读」的 instruct**：暂不默认。唯一的实测（上表）中，加了指令的版本确实是普通话，但语速明显变慢、超过 0.8 秒的停顿从 1 处增加到 16 处；而且只有一个样本，不能说明它降低了漂移概率。在现代简体文本上目前没有观察到需要它的漂移。若日后在简体文本上也观察到漂移，再用数据比较「加指令」与「不加指令」的漂移率和听感。对繁体书，将来更直接的办法可能是把 `say` 转成简体（OpenCC 已是依赖），只改读法、不改原文。

**可行的方向**：

1. **人工重做**：听到问题时，按时间点定位到单元并换 seed 重合成（任何听感问题都适用，不限于口音）。✅ 已实现为 `huashuo redo BOOK --at 1:28`（requirements CLI-10）
2. **声调一致性检测**（研究性质）：强制对齐得到每个字的时间，pypinyin 给出普通话应有的声调，从基频曲线判断实际声调；陕西关中话与普通话的调值差异很大（如阴平 21 对 55、去声 44 对 51），漂移单元的声调一致率应明显下降。可顺带测出 temperature 0.9 与 0.7 下的真实漂移率
3. **降低 temperature**：理论上减少低概率路径，但可能让表达变平；需在有检测手段后用数据决定

#### EXP-3 第一种方案：声调一致性（2026-09-24，未成功）

脚本 `experiments/exp3_tone_check.py`。每个单元：Qwen3-ForcedAligner 给出逐字时间 → pypinyin（含变调）给出普通话应有声调 → YIN 基频 → 每个声调的平均归一化曲线（声调画像）→ 与参考画像的距离。测试集：12 个参考单元 + 12 个留出单元（均为普通话）、漂移单元的 6 个变体（1 个陕西口音）、serena 用模型的四川话 / 北京话语言设置朗读的 12 段合成口音样本。

**结果：分不开**（普通话最大距离 1.37，口音最小 0.40）。原因不在打分，而在测量：

- 已知普通话的单元里，四个声调的平均曲线几乎都是平的（-0.4 ~ -0.5 半音）；四声应下降、二声应上升。整体平移对齐时间（±300 ms）也得不到应有的形状，不是简单的时间偏移
- 用孤立音节「妈，麻，马，骂。」做受控测试：一声高平、三声低，正确；但四声测成上升，二声不明显上升；对齐器把「马」「骂」的时间给到了音频结束之后
- 连续语流中声调协同发音很强，即使测量准确，曲线也远不如教科书形状

要让这条路走通，需要更可靠的对齐、经过验证的基频提取、更好的声调模型——代价是数天且结果不确定。

**备选方案**（待定）：用 Qwen3-ASR 音频编码器的嵌入做口音分类。ASR 编码器在多方言数据上训练，嵌入里应含口音信息；训练数据可以无限生成（serena 的普通话 vs 模型的四川话 / 北京话设置），再在真实的陕西口音漂移上检验能否泛化。

### 5.9 情绪提示（SCR-12，M3 实测，2026-09-25）

**做法**：情绪与说话人在同一次调用里标注（SCR-6），不增加调用次数。LLM 只从固定标签中选择，并被要求「只在原文有明确依据时填写，拿不准就留空」；程序把标签换成 instruct（如「用生气的语气说」，完整对照见 [script-ir.md](script-ir.md) §4），写入 block 的 `emotion`。第一版有 14 个标签、措辞温和；试听后减为 7 个基本情绪（见下文「试听」）。用固定标签而不是让模型自由描述，是为了控制措辞强度、便于检验，也让不同书的表现一致。用户可以手写任意描述。

**缓存兼容**：不要情绪时的提示词与 M2 逐字节相同，所以 M2 缓存的回答仍然有效；没有密钥（或 `--no-llm`）重新导入旧书时，找不到带情绪的回答就退回旧回答，说话人不丢，只是没有情绪。有密钥时重新导入会重新标注（与首次导入的费用相同）。

**标注实测，第一版 14 个标签**（《春尽江南》前 2 万字，gpt-6-sol）：94 句对白中 32 句（34%）带情绪，以生气、疑惑、愉快、伤感居多。多数有据可查——「端午刻薄地讥讽道」→ 讥讽，「斩钉截铁地打断他」→ 严厉，「小声说道」→ 低声，「很不耐烦地打断了他」→ 不耐烦；少数可商榷，如带着讥诮的「笑道：日你妈妈！……」标为愉快。这 2 万字含角色表共 $0.21，按字数外推全书约 $3.1，与不带情绪时持平：多出的输出 token 在误差之内。

**音色检验，第一版 14 个标签**（`experiments/m3_emotion_check.py`）：8 个音色（2 个预置 + 6 个库音色）× 14 个标签，每句先平读、再加 instruct（同一 seed）；另有 3 个标签用 EXP-1 的强烈措辞对照。相似度是与该音色平读片段中心的中心化余弦。

| 音色 | 平读 | 加提示 | 强烈措辞 | 加提示后被认成别人 | 字错率 平读 / 加提示 |
|---|---|---|---|---|---|
| `serena` | 0.87 | 0.83 | 0.76 | 3/14 | 0.0% / 0.7% |
| `uncle_fu` | 0.88 | 0.86 | 0.85 | 0/14 | 2.6% / 0.7% |
| `young_man_warm` | 0.89 | 0.84 | 0.83 | 0/14 | 0.0% / 0.0% |
| `mid_woman_calm` | 0.90 | 0.87 | 0.89 | 0/14 | 0.0% / 0.9% |
| `old_man_kind` | 0.87 | 0.81 | 0.83 | 2/14 | 0.0% / 0.0% |
| `teen_girl` | 0.93 | 0.91 | 0.92 | 1/14 | 0.0% / 0.0% |
| `old_woman_stern` | 0.84 | 0.83 | 0.82 | 0/14 | 4.5% / 2.4% |
| `boy` | 0.91 | 0.87 | 0.89 | 2/14 | 0.7% / 1.6% |

- **提示确实改变了读法**：时长比（加提示 / 平读）悲伤 1.24、温柔 1.17、生气 0.79、严厉 0.88
- **音色基本保住**：相似度平均从 0.89 降到 0.85，112 句中 9 句离另一个音色更近；字错率没有变差
- **最弱的是老年男声的生气与严厉**（0.58、0.69，都靠向 `young_man_warm`），与稳定性检验中听者对 `old_man_kind` 愤怒片段「更像年轻人」的判断一致。其次是 `serena` 的温柔（靠向 `mid_woman_calm`）与 `boy` 的冷淡（靠向 `serena`）
- **温和措辞与强烈措辞在向量上差别不大**（同样三个标签：0.83–0.88 对 0.85），可见漂移主要来自情绪本身而不是措辞强度；仍保留温和措辞，因为人耳对强烈情绪下「像换了人」更敏感（EXP-1 第三轮）
- `old_woman_stern` 在 28 句中有 6 句丢了句末字，由 §5.10 的检查重试

**试听**（32 句带情绪的台词，每句先平读、再加提示）：听者认为**只有一半或不到一半能明确听出加了情绪更好**，其余两者差不多。生气、悲伤这类基本情绪效果明显；讥讽之类的复杂语气几乎没有效果。这与时长比一致：变化大的是悲伤、生气、严厉，讥讽、疑惑、冷淡、急切变化很小或没有变化。

**第二版：7 个基本情绪**——高兴、生气、悲伤、害怕、惊讶、低声、严厉（去掉兴奋、不耐烦、温柔、冷淡、讥讽、急切、疑惑）。读不出区别的标签并非无害：情绪变化会把单元切短（TTS 的上下文变少），每个提示都带一点音色漂移，标签越多模型的误标也越多。措辞改得更直接（「用有些生气的语气说」→「用生气的语气说」），因为上面的检验中强烈措辞的漂移并不比温和措辞大。提示词明确要求讥讽、疑惑、冷淡之类留空。同一段 2 万字重新标注：95 句中 12 句（13%）带情绪（生气 5、悲伤 2、低声 2、严厉 2、高兴 1），都是「斩钉截铁地打断他」「小声说道」这类有明确依据的；说话人标注部分 $0.13（角色表已缓存）。

**结论**：7 个标签，默认开启；`--no-emotions` 整体关闭（按合成选项记录在 `state/run.json`，改了之后只有带情绪的单元需要重新合成）。若日后听感上某个组合（如老年男声 + 生气）不可接受，可按音色屏蔽个别标签。

### 5.10 句末截断（M3 实测，2026-09-25）

**现象**：听者在 `old_woman_stern` 的稳定性片段里发现句末的「你」很轻或几乎听不到。ASR 抓到了（转写为「……应该告诉。」），但一句 20 字的话漏一个字只算 5% 的字错率，低于 10% 的阈值，SYN-10 的校验不会重试。

**原因**：模型在最后一个音节还没读完时就生成了结束符。排除过的解释：

- 裁静音：`trim_bounds()` 保留了这些尾音
- ASR 需要尾部静音：给音频补 0.5 秒静音后重新转写，6 个被判漏字的单元一个也没有恢复
- mlx-audio 的有效长度计算：它把第一码本为 0 的帧当作填充（`speech_tokenizer.py`，`(codes[..., 0] > 0).sum()`），理论上会从结尾多裁 80 ms；实测 12 次生成中没有出现过 0 号码，不是原因

截断的单元大多以 −17 到 −28 dB（相对峰值）的能量结束，也就是声音还没落下就停了。**不只是这一个音色**：两本书已合成的 102 个单元中，42% 以高于 −30 dB 的能量结束，12–14% 的尝试被 ASR 判为丢了最后一个字（`serena` 也有，如「店内外充满了快活的空气」转写为「……快活的空」）。

**处理**：

1. ASR 校验增加一条：**原文最后一个字（英文为最后一个词）不在转写的最后三个字里**，即使字错率未超限也判为失败并重试（`asr.lost_ending()`）。「最后三个字」是为了容忍 ASR 在句末多加语气词；句末「么」统一折叠为「吗」，避免同音误判。用真实模型复验：`old_woman_stern` 三句被截断的台词都在重试后完整读出。按每次尝试约 13% 的失败率，三次尝试后仍被截断的单元约 0.2%
2. 封装时每个单元加 5 ms 淡入、15 ms 淡出，声音戛然而止时不会在接下来的停顿前产生爆音
3. 关闭 ASR 校验（`--no-asr`）时没有这层保护；已缓存的单元在下次 `synth` 时按新规则重新判定，只报告不重合成，用 `huashuo redo` 重做

**已知问题（M5 处理，§10.3）**：句末是方言语气词时会误判。《春尽江南》里国舅的「你发个话唦！」，模型读成「来」「嘞」，ASR 也写不出「唦」，于是重试三次后带着警告保留；上海话台词（「侬格小赤佬……」）整句字错率超限，同样白白重试。

### 5.7 其他候选（备查）

| 方案 | 中文质量 | Mac 可行性 | 备注 |
|---|---|---|---|
| IndexTTS 2 | 高，时长/情感可控 | ⚠️ 基本需云端 GPU | Unitale 的主力 |
| CosyVoice 2 | 高，支持 18+ 方言 | ⚠️ Mac 支持一般 | 阿里 |
| GPT-SoVITS | 高 | ⚠️ | 需训练/参考音频，中文社区教程最多 |
| 付费 Azure TTS | 高 | ❌ 云端 | zh-CN 标准音色有十几个，**音色数量远超 edge-tts**，是音色不够时的升级路径 |

---

## 6. LLM 选型

### 6.1 云 API vs 本地

**结论：云 API。** 本地可运行的小模型（普通 macOS，30B 以内）对中文小说的理解不足以支撑高质量的角色识别与说话人归属。

代价是隐私——整本书会完整上传给第三方。对有版权或私密的内容需要单独判断。

### 6.2 接入约束

#### OpenAI：drop-in

项目只通过 `AsyncOpenAI` 和 pydantic-ai `OpenAIChatModel` 调 LLM，都是标准 OpenAI 协议，改 `.env` 即可。

已排除的一个疑虑：代码在 `model_settings` 里传了 `repeat_penalty`（llama.cpp 参数，非 OpenAI 参数）。查 pydantic-ai 1.18.0 源码，`_completions_create` 是**显式挑 key** 的：

```python
temperature=model_settings.get('temperature', OMIT),
presence_penalty=model_settings.get('presence_penalty', OMIT),
frequency_penalty=model_settings.get('frequency_penalty', OMIT),
```

未知 key 不透传，静默忽略 → 无害。

#### Anthropic：兼容层不可用

Anthropic 提供 OpenAI 兼容端点（`https://api.anthropic.com/v1/`），但[官方文档](https://platform.claude.com/docs/en/api/openai-sdk)明确说明它用于测试对比，非生产方案，且：

> - `response_format` **被忽略**
> - function calling 的 `strict` 参数**被忽略**，工具调用 JSON **不保证符合 schema**

而本项目的角色识别完全建立在 pydantic-ai 的结构化输出之上。后果：走 tool 模式会频繁触发重试（成本×N），走 native output 模式则每次解析失败。

**正确做法是走 pydantic-ai 的原生 Anthropic 支持**（`AnthropicModel` + `AnthropicProvider`），约 20–30 行改动，同时需去掉三个 penalty 参数（Anthropic 没有）。

> 2026-09-23 决定：本项目只支持 OpenAI 兼容接口，不专门为 Anthropic 写适配（§6.5）。上述结论保留作调研记录；若用 §6.5 建议的 pydantic-ai，日后要接 Claude 也只是换一个 Model 类，不是写适配器。

### 6.3 当前价格（OpenAI，2026-09）

| 模型 | 输入 $/MTok | 输出 $/MTok |
|---|---|---|
| gpt-6-astra | 10.00 | 50.00 |
| gpt-6-sol | 2.00 | 10.00 |
| **gpt-6-luna** | **0.10** | **0.50** |
| gpt-5.6-sol | 4.00 | 20.00 |
| gpt-5.6-terra | 2.00 | 12.00 |
| gpt-5.6-luna | 0.20 | 1.20 |
| gpt-5.4-mini | 0.75 | 4.50 |
| gpt-5.4-nano | 0.20 | 1.25 |
| **gpt-5-nano** | **0.05** | **0.40** |
| gpt-4o-mini | 0.15 | 0.60 |

**两个免费杠杆：**
- **Batch API 五折**——这是离线流水线，延迟无所谓，白拿 50%
- **缓存输入只要 10%**——角色表 + 系统提示词是完美的缓存前缀

Claude 侧参考价：Opus 5 `$5/$25`、Sonnet 5 `$2/$10`、Haiku 4.5 `$1/$5`。

### 6.4 已踩的坑：推理模型 + function tools

用 `gpt-5.6-luna` 时报错：

```
400 - Function tools with reasoning_effort are not supported for gpt-5.6-luna
in /v1/chat/completions. To use function tools, use /v1/responses or set
reasoning_effort to 'none'.
```

**根因**：pydantic-ai 并未发送 `reasoning_effort`（不设则 OMIT），是 **OpenAI 服务端给推理模型套了默认值**，而 `/v1/chat/completions` 不允许「推理 + function tools」共存。pydantic-ai 的结构化输出走的正是 function tools。

**现象特点**：健康检查能通过（`check_if_llm_is_up()` 是裸 chat 调用，不带 tools），一进 Pass 1 就崩。

**解法**：显式设 `openai_reasoning_effort: "none"`（`ReasoningEffort` 的合法值为 `none|minimal|low|medium|high|xhigh`）。

**更好的解法**：这是结构化抽取任务，不需要推理模型。换非推理模型既避开问题，又便宜得多。

### 6.5 LLM 接入库的选择（2026-09-23 确定：pydantic-ai）

**需求**（[requirements.md](requirements.md) §3.9）：只走 OpenAI 兼容的 Chat Completions，但要覆盖 OpenAI、OpenRouter、DeepSeek、阿里云百炼，以及 LM Studio / Ollama / llama.cpp / vLLM 等本地服务；要有**带类型校验和自动重试的结构化输出**；要能统计 token 与费用。

| 方案 | 结构化输出 + 校验重试 | 覆盖面 | 其他 |
|---|---|---|---|
| **pydantic-ai**（MIT） | ✅ 核心能力：`output_type` 用 Pydantic 模型，校验失败自动回灌重试（即 §2.4 第 2 条要继承的做法） | 任意 OpenAI 兼容端点（`OpenAIProvider(base_url=...)`），另有 DeepSeek、OpenRouter、Ollama 等现成 provider | 结构化输出可选 tool 模式或 `NativeOutput`（`response_format`），后者可绕开 §6.4 的「推理模型 + function tools」报错；返回 token usage |
| litellm（MIT） | ❌ 只统一调用接口，校验重试仍要自己写（或再叠 instructor 之类的库） | 最广，含本地服务 | 依赖很重，主要价值在代理网关与百家提供商，本项目用不上；**2026-03-24 PyPI 上的 1.82.7 / 1.82.8 被植入后门**（[官方说明](https://docs.litellm.ai/blog/security-update-march-2026)），虽已处理，但对一个只需要一种协议的项目来说是不必要的供应链面 |
| 官方 `openai` SDK + 自写 | 要自己写校验、错误分类与重试 | 同样覆盖所有 OpenAI 兼容端点 | 依赖最少；但会重写 pydantic-ai 已经做好的那部分 |

**结论：pydantic-ai**，只用它的 OpenAI 兼容路径。它正好提供了项目最需要的「类型即校验」能力，本地端点也能直接用；分类重试（如 §2.4 第 3 条的上下文溢出识别）和对账（§7.2 第 4 点）在其外面自己写。费用统计用 usage 中的 token 数乘以可配置的单价表。

---

## 7. 成本与性能分析

### 7.1 逐句架构的成本结构

**实测数据**（gpt-5.6-luna，5 行中文片段的 Pass 1 调用）：`1398 input / 458 output`

Pass 2 单次调用构成（40 个角色时）：

```
系统提示词        ~1,300 token   ← 每次都重发
完整角色表        ~2,000 token   ← 每次都重发
前文上下文        ~1,000 token
后文上下文        ~1,000 token
待判定的那句话      ~30 token   ← 真正的有效载荷
────────────────────────────
                  ~5,300 token
```

**有效载荷占 0.6%。** 系统提示词和角色表被原封不动重发 ~5000 遍，约 **1650 万 token 纯属重复**，占总消耗的 60%。

**30 万字小说的基线估算**：约 2700 万输入 / 100 万输出 token

| 模型 | 估算成本 |
|---|---|
| gpt-5.6-luna | ~$6.7 |
| Claude Sonnet 5 | ~$65 |
| Claude Opus 5 | ~$140 |

**真正的问题不是钱，是调用次数**：Pass 2 是 5000 次**串行**调用（代码里是纯 for 循环，无并发），累计 3–4 小时——与 TTS 的 7 小时同量级。分析环节本该是几分钟的事。

### 7.2 ✅ 方案一：段落级批量标注（已确定采用）

**核心设计：给输入编号，只输出索引 → 说话人。**

```
输入（state）：
  角色表：林渊(男/青年)、苏晚晴(女/青年)、客栈老者(男/老年)…

  [1] 雪下了整整一夜。林渊推开客栈的木门。
  [2] "店家，来一壶热酒。"
  [3] 他把斗篷上的雪抖落在地。
  [4] "客官面生得很，是从北边来的？"老者一边擦着酒碗一边问。
  ...

输出（只要这个）：
  {"2": "林渊", "4": "客栈老者", "9": "苏晚晴", ...}
```

**四个关键点，缺一个收益就打折：**

1. **绝不让模型回显原文**——既是输出 token 大头的来源，也是文本被悄悄改写的风险来源
2. **分块要重叠**——块边界的对话缺上下文，块间重叠 3–5 行，重叠区取前一块结果
3. **角色表放提示词最前并打 cache 断点**——Pass 2 期间完全不变，缓存命中后按 10% 计费
4. **必须做对账**——正则已知哪些行含对话（免费且可靠），据此得到「应答索引清单」；漏答或幻觉索引立刻可发现，漏掉的行单独补一次

**收益测算**（3000 token 一块，100 块）：

| | 现架构 | 批量标注 | 倍数 |
|---|---|---|---|
| 调用次数 | ~5,000 | **~100** | 50× |
| 输入 token | 26.5M | **0.65M** | 40× |
| 输出 token | 1.0M | **0.05M** | 20× |
| 耗时（串行） | 3–4 小时 | **~4 分钟** | 50× |
| 成本（gpt-5.6-luna） | ~$6.7 | **~$0.19** | 35× |

再叠加缓存与 Batch API，可降到 $0.05 量级。

**代价**：准确率可能略降（注意力稀释、索引漂移）。块别开太大（1500–3000 token 较稳），靠第 4 点对账兜底。

### 7.2.1 ✅ EXP-2 实测（2026-09-24）

脚本 `experiments/exp2_batch_tagging.py`，基准 `experiments/exp2_data/`：设计文档 §3.2 的 40 行样本（12 句引语）与鲁迅《孔乙己》（38 句，人工标注：无标签的轮流对话、不具名的一群酒客、不是说话的引语「上大人孔乙己」、第一人称叙述者开口）。pydantic-ai + OpenAI 兼容接口，结构化输出走 `response_format`，推理强度设为 none。

| 模型 · 方式 | 40 行样本 | 孔乙己 | 请求数（孔乙己） | token（孔乙己） | 费用（孔乙己） |
|---|---|---|---|---|---|
| gpt-6-luna · 批量 | 12/12 | **35/38**（三次运行结果完全相同） | 4 | 9.3k | $0.0013 |
| gpt-6-luna · 逐句 | 12/12 | 33/38 | 40 | 45k | $0.0050 |
| **gpt-6-sol · 批量** | 12/12 | **38/38** | 4 | 9.3k | $0.025 |
| gpt-6-sol · 逐句 | — | 38/38 | 40 | 45k | $0.100 |
| gpt-5.4-mini · 批量 | — | 35/38（与 luna 错法相同） | 4 | 9.6k | $0.011 |
| gpt-5.6-luna · 批量 | — | 31/38 | 4 | 10k | $0.003 |

EXP-2 总花费 $0.16（预算 $1；脚本内置预算闸门：每次调用前按最坏情况估算，超出上限即停）。

**结论：**

1. **批量优于逐句，不只是更便宜**：同一模型、同一提示词，批量的准确率更高（luna 35 vs 33），请求数少 10 倍、token 少 4.8 倍。看到整段轮流对话，模型更能跟住轮次。逐句方式在对白密集的《孔乙己》上仍然是 4.8 倍 token；对白稀疏、角色多的长篇，差距会更大（§7.1 估算为 40 倍）
2. **所有便宜模型都错在同一处**：酒客与掌柜无标签的一问一答（「後來怎麼樣？」「後來呢？」「打折了怎樣呢？」是掌柜问的，段末「掌櫃也不再問」可证）。luna 与 gpt-5.4-mini 都判给酒客，gpt-5.6-luna 把整段轮次判反。只有 gpt-6-sol 全对。这类需要推理的对白在小说中比例不高，但确实存在
3. **luna 的置信度没有校准**：三处错误的置信度为 0.89–0.99。SCR-11（低置信度清单）不能直接依赖便宜模型自报的置信度
4. **角色表（第一遍）各模型都做对了**：包括「我」作为会说话的叙述者、酒客作为群体角色。一处小错：luna 把「客官」（老者对林渊的称呼）记为老者的别名，不影响归属
5. **按 30 万字估算**（孔乙己正文约 2,600 字 → ×115）：gpt-6-luna 批量约 $0.15/本；gpt-6-sol 批量约 $3/本，超出 NFR-4 的 $1。角色表随书变大，第一遍的成本会略高于线性估算
6. **landandan fork 对拍未完成**：在同一模型（gpt-6-luna）上，fork 两次都在第一遍收到 HTTP 500、约 3 分钟重试后崩溃（异常被吞后解包空结果，即 §2.5 所说的缺陷）；同一模型经本项目的管线正常。推测是 fork 的请求参数与该模型不兼容，未深究。同模型的逐句基线已由本脚本覆盖

基准仍然很小（50 句引语），日后应补充更大、更多样的基准（现代小说、角色多、对白稀疏的章节）。

**决定（2026-09-24）**：默认模型为 **gpt-6-sol**，按实测约 3 美元 / 30 万字；成本目标相应调整为「约 3 美元、不超过 5 美元」；gpt-6-luna 作为经济档（[requirements.md](requirements.md) §9.1 Q17、NFR-4、LLM-9）。

**M2 移植到正式包之后的复核**（`experiments/m2_eval.py`，gpt-6-sol）：切分改由 `dialogue.py` 完成、提示词去掉了与基准重合的例子之后，两个基准仍为 **12/12、38/38**（$0.034）。在从未用于调试的《藥》上，角色表正确合并了「渾身黑色的人」与后文才点名的「康大叔」，不是说话的引语（招牌「古□亭口」、写出的「八」字、心里的想法、乌鸦的「啞——」）都判为旁白。经 CLI 全流程处理《孔乙己》《藥》约 7,200 字：$0.078，预估 $0.08；重新导入全部命中缓存，$0。

真实调用还暴露了一个模拟模型测不出的问题：每次调用用 `asyncio.run` 新建事件循环，而 OpenAI 客户端绑定在第一次的循环上，第二次调用即失败（"Event loop is closed"）。改为每个 `Caller` 共用一个事件循环，并加了回归测试。

### 7.3 方案二：Jev / System One 模型（暂缓，待 A/B 基准后评估）

[TypeSafe AI 的 Jev](https://typesafe.ai/blog/introducing-system-one-models-and-jev) 是一类「System One 模型」——只做结构化决策，不生成文本。

**规格：**

| 特性 | 参数 |
|---|---|
| 定价 | **输入 $0.042/MTok，输出免费** |
| 延迟 | 70–500ms（多数 ~100ms） |
| 上下文 | 64k / 请求（state + 最长 question 不超 32k） |
| 输出类型 | `choice`（选择，**基数上限 255**）/ `score`（评分）/ `noul`（是否，返回概率） |
| 关键特性 | **state 发一次，N 个问题并行回答**；返回**校准概率与置信度**；结构化输出错误率 0% |
| 接口 | **非 OpenAI 兼容**（自有 `state` + `questions` 结构）；可经 OpenRouter (`typesafe/jev-1.13`) 或 Vercel AI Gateway 调用 |
| 准确率 | 自家 4 项基准 67.8%，对比 gpt-5.6-terra 67.9% / Opus 5 73.1%；单案例成本 $0.0004 vs $0.03–0.18 |

**契合点**：「state 发一次 + N 问题并行 + 输出免费」正好是「一章文本 + 角色表发一次，然后问 50 次『第 N 句是谁说的』」。

**硬边界：Jev 没有字符串生成能力。**

| 任务 | Jev 能否胜任 |
|---|---|
| Pass 1 人物抽取（生成名字、描述、执行 merge） | ❌ **不能**，是生成任务 |
| Pass 2 说话人归属（从已知角色表中选择） | ✅ **能**，`choice` 类型，基数 ≤255 |
| 章节划分 | ⚠️ 能，但**正则已免费解决**，不该花钱 |
| 判断哪句是对话 | ⚠️ 能，但**正则已免费解决**（引号配对） |

**成本**：100 块 × 6,500 token × $0.042/M ≈ **$0.027 一本书**，输出免费，耗时不到一分钟。

**风险**：67.8% 是通用基准而非中文说话人归属；中文的隐含主语、后置标签、跨行指代很吃语言理解，必须实测。

### 7.4 🎯 未来方向：用 Jev 的置信度做分诊

Jev 返回**校准概率与置信度**——这是它区别于普通 LLM 的关键，也是最被低估的特性。

```
对每一句对话：
  Jev 判定（state = 本章文本 + 角色表，N 个 choice 问题并行）
    ├─ 置信度 ≥ 阈值  →  直接采纳          （预期 80–90%）
    └─ 置信度 <  阈值  →  升级给强模型重判   （预期 10–20%）
```

这把**成本和质量解耦**了：绝大多数对话有显式说话人标签，很好判；只有少数需要真正推理（如 §3.2 那句「这位公子，借个火」的前向引用）才值得花强模型的钱。

估算：85% 走 Jev（$0.027）+ 15% 升级 gpt-6-sol（750 句 × 5k token × $2/M ≈ $7.5）≈ **$7.5**，但**质量向强模型看齐**。

**前提**：置信度必须真的校准（高置信的那批确实更准）。这是必须实测的，不能靠厂商基准数字。

---

## 8. 新项目决策

### 8.1 为什么新建而不是 fork

**最强的理由不是许可证，而是架构已经分叉了：**

| 维度 | audiobook-creator | 我们要的 |
|---|---|---|
| LLM 调用粒度 | 每句一次（5000 次串行） | 段落级批量（100 次，可并发） |
| LLM 部署 | 假设本地 OpenAI 兼容端点 | 云端 API 优先 |
| TTS 接口 | 假设 HTTP，硬编码引擎分支 | 进程内插件（mlx-audio 是库不是服务） |
| 音色选择 | gender_score 单维度，age 被吞 | 完整角色画像 → 音色 |
| 中间产物 | 固定文件名 + 靠行数对齐耦合 | 显式的 Script IR |
| 并发/断点 | 无 | 分块并发 + 断点续跑 |

这些不是能靠打补丁修好的，**它们是那个代码库的形状**。

### 8.2 许可证

整条依赖链均为宽松许可（见 §5.5），Apache-2.0 完全走得通。唯一的 GPL 纠缠来源是 audiobook-creator 的代码本身。

**需守住的工程纪律**（非法律意见）：

- **不复制代码**。架构思想不受版权保护，具体表达受保护。「两遍式角色识别」这个**想法**可以用；`extract_character_info_from_batch()` 那 200 行**实现**不能抄
- **提示词必须重写**。landandan 的中文提示词是 GPL；而且批量标注的提示词形状本来就不同，顺势重写
- **ffmpeg 命令行知识可以用**（事实性知识，非受保护表达），但对着 ffmpeg 文档写，别对着那个文件写
- **README 明确致谢** audiobook-creator 与 landandan 的中文工作

### 8.3 核心架构：Script IR（剧本中间表示）

**这是整个项目最贵的一个设计决定，改起来最痛，必须先定下来。** 格式定义见 [script-ir.md](script-ir.md)；下面是最初的设想。

audiobook-creator 没有中间表示，只有一串靠行数对齐的临时文件。新项目应该有一个一等公民：

```
Script
├─ meta:   title / author / language / source
├─ cast:   角色 → 音色绑定（可手工覆写）
└─ blocks[]
     ├─ type:    chapter | narration | dialogue | heading
     │           | equation | figure | table | footnote | skip
     ├─ text
     ├─ speaker  (dialogue 专用)
     ├─ source_ref  (回溯原文位置)
     └─ hints:   emotion / emphasis / pause_after / 读音覆写
```

流水线变成：**文档 → 剧本 → 音频**。前半段是理解问题，后半段是合成问题，中间是一个人能看懂、能手改的文件。

**它一次性解决了所有未来方向：**

| 场景 | 映射 |
|---|---|
| 小说 | narration + dialogue + chapter |
| 播客 | 剧本本身就是播客的形式，blocks 全是 dialogue。单人多人都自然支持，**无需新管线** |
| 网页朗读 | heading / paragraph / list / code，导航栏与代码标 `skip` |
| 学术 PDF | equation / figure / table / citation 各是一种 block type。**「数学公式朗读」降级成给 equation block 写 renderer**，不是新流水线 |

**三个立刻兑现的好处：**

1. **人可以手改**——说话人判错、人名读音不对、想跳过某章，改剧本就行。这是所有失败模式的统一逃生口
2. **TTS 可按 block 缓存**——用内容哈希做 key，改一句只重跑一句。**断点续跑是免费得到的**（7 小时的活不能因一个错字重来）
3. **引擎可换**——剧本不知道 TTS 是谁

### 8.4 必须改掉的设计（对照 §2.5）

- 固定全局文件名 → **每本书一个工作目录**
- gender_score 吞掉 age → **完整角色画像传到音色分配**
- TTS 假设 HTTP → **插件接口**
- 无成本可见性 → **每步显示 token 消耗与预估费用**（云 API 项目的必需品）
- Pass 2 串行 → **分块并发**
- 无续跑 → **按 block 哈希缓存 + 断点续跑**

### 8.5 诚实的代价评估

要重写的东西里有很大一块是**不性感但坑很多**的，在 audiobook-creator 里约 900 行，代表作者踩过的所有坑：

- M4B 封装（章节时间戳、封面、元数据）
- ffmpeg 格式转换矩阵（8 种输出格式）
- 章节间静音——**必须按实际采样率/声道生成匹配的静音文件**，否则 `-c copy` 拼接会失败
- calibre 集成、编码检测、各种畸形 epub

**预计一到两周，而且会把同样的坑再踩一遍。** 这是这个决定真实的门票价格。

### 8.7 自动选角（M3，2026-09-25）

`casting.cast_voices()` 为每个角色选一个音色，完全确定（同样的输入总得到同样的结果）。可选音色是库音色加上标准普通话预置（`library.castable()`），排除方言预置与旁白的音色。

1. **用户的选择先定**：`cast.json` 里与上次机器结果不同的 `voice` 视为手改，原样保留
2. **第一人称**：「我」/ "I" 用旁白音色（SCR-9）——叙述就是这个人的声音
3. **主要角色**（按台词数排前 8 个）各用一个音色，互不相同。先看 LLM 的推荐：导入时多做一次调用，把主要角色的描述与每个音色的角色标签、特点、描述一起给模型，让它按性格挑音色（`attribution.suggest_voices()`，一次约 $0.006）。推荐只在「是可选音色、性别相符、尚未被其他主要角色占用」时采用；否则按下面的代价挑
4. **其余角色**按代价最小者共享音色：

| 代价项 | 值 | 理由 |
|---|---|---|
| 性别不符 | 100 | 几乎不应发生 |
| 年龄每差一档 | 20 | child → teen → young_adult → middle_aged → elderly |
| 年龄未知时用童声 / 少年 / 老年音色 | 80 / 50 / 35 | 年龄未知几乎总是成年人（孩子通常会标出来）：给「主任」「老师」配童声比共享音色糟糕得多 |
| 与直接对话的角色同声 | 每人 40 | 连续对话的两个人不能听起来是同一个人（CAST-7） |
| 用了主要角色的音色 | 45 | 比年龄差两档还糟：配角说话像主角会误导听者 |
| 已被使用的次数 | 每次 8，最多 32 | 让配角分散到合适的音色上，但上限低于「年龄不符」的代价，不会为了分散而配错年龄 |

代价相同时选用得少的音色。主要角色之间的「互不相同」是硬约束（代价 10000）。

**《春尽江南》实测**（101 个角色）：未加 LLM 推荐时，主角谭端午（内省的诗人）分到「洪亮、爽快、市井气」的 `mid_man_jovial`，年龄未知的吴宝强、鲍老师分到童声和少女；加了推荐与上述代价后，谭端午用 `uncle_fu`，庞家玉用 `mid_woman_calm`，徐吉士用 `young_man_warm`，年龄未知的配角都落在成年音色上。剩下的问题是结构性的：8 个主要角色独占 8 个音色后，同性别、同年龄段的成年音色只剩三个（`young_woman_cool`、`young_man_deep`、`mid_man_jovial`），90 多个配角大多落在它们上面；中年女性配角（宋蕙莲）因两个中年女声都被主要角色占用，只能用 `old_woman_stern`。可能的改进：只有一两句台词、且从不与某个主要角色同章出现的配角，允许借用这个主要角色的音色（M5 评估，§10.3）。

### 8.6 项目命名：话说 Huashuo（2026-09-23 确定）

| 用途 | 名称 |
|---|---|
| 中文名 | **话说** |
| 英文 / 拼音名 | **Huashuo** |
| GitHub 仓库、PyPI 包、CLI 命令 | `huashuo`（如 `huashuo book.epub`） |

**对外展示时始终写作「话说 Huashuo」**，不单独使用无声调拼音（理由见下文风险）。

#### 命名原则

1. **不绑死「小说 / 有声书」**——路线图含播客、网页、论文（[requirements.md](requirements.md) §1.2）
2. **不绑死技术**——不含 mlx / qwen / mac 等字样，TTS 引擎是插件
3. **拼音名可行**——jieba、pypinyin 等中文开源项目证明拼音名能传播
4. **有当代感**，而非仅是传统技艺
5. **CLI 要好敲**
6. 项目是开源公益性质，托管在 GitHub 即可，**域名不作为考量**

#### 为什么是「话说」

- **古今兼具**：「话说天下大势，分久必合……」是章回小说的标准开场白，是说书人开口的第一句；同时「话说……」也是当代网络日常口语
- **与核心问题同源**：「话」和「说」都指说话；本项目的核心难题叫「说话人归属」，两个字倒过来就是项目名
- **不局限于小说**：播客、访谈、网页朗读，开口都可以是「话说」
- **自带开场白**：README 可直接以「话说，……」起笔
- **可用性**（2026-09-23 核查）：PyPI、npm 均未被占用；GitHub 上无相关项目（仅零星个人仓库）

**风险与缓解：**

- 无声调拼音 `huashuo` 与「华硕」(ASUS, huáshuò) 同形，GitHub 搜索首位结果即为 ASUS ROG 相关仓库。缓解：对外始终以「话说 Huashuo」汉字 + 拼音并列出现
- 「话说」是高频词，中文搜索引擎里直接搜会有大量噪声；但在 GitHub / PyPI 上搜 `huashuo` 是干净的

#### 淘汰的候选（留档备查）

| 候选 | 淘汰理由 |
|---|---|
| 口技 Kouji | 意象很贴（「一人、一桌、一椅、一扇、一抚尺而已」，一人演全场），但偏民间技艺，缺少当代感；英文读者会读成日语 kōji |
| 话本 Huaben | 含义最贴（宋代说书人的底本 = 剧本），但与在营网文平台「话本小说」（ihuaben.com，主打对话式小说与角色扮演）领域几乎重叠。**适合留作 Script IR 的名字**，见下 |
| 朗读 Langdu | 最稳妥的备选，意思准确；但偏平淡，且 `lang-` 前缀易被联想为 LangChain 类 LLM 框架 |
| 朗读者 Langduzhe | 与央视《朗读者》品牌撞名 |
| 阅读者 Yueduzhe / Yueduer | 「阅读」是默读，语义不对；中文网文圈「阅读」几乎专指开源阅读器 [legado](https://github.com/gedoor/legado)（47k★） |
| 播音 Boyin | 无声调拼音与「波音」(Boeing) 同形 |
| 播音员 Boyinyuan | 过长；「播音员」是单人字正腔圆，与多角色定位相反 |
| 录音棚 Luyinpeng | 意象好（多演员进棚、后期出成品），但对英文读者过长、难读 |
| 广播剧 Guangboju | 只覆盖「剧」一种形态，Unitale 已占此定位；略有年代感 |
| 说书 Shuoshu / 评书 Pingshu / 醒木 Xingmu | 传统色彩过重；shuoshu 在 GitHub 上与 QQ「说说」类项目混杂 |
| 听书 Tingshu | GitHub 已有 1.2k★ 同名项目 |
| Castwright | 已是同类项目（多角色有声书生成器） |
| Dramatis / Troupe / Libretto / Raconteur / Tableread | PyPI 已被占用 |
| Personae | GitHub 有 1.4k★ 同名项目 |
| Bookcast | 可用，但过于直白，缺少文化辨识度 |

#### 已定：Script IR 命名为「话本」（2026-09-23）

剧本中间表示（§8.3）命名为 **话本**：项目叫「话说」，剧本叫「话本」，都来自说书传统，中文读者一看就明白两者关系——说书人照着话本开讲。

**不发明新的基础格式**：话本文件是 JSONL（一行一个 block），扩展名 `*.huaben.jsonl`，任何 JSON 工具和编辑器都能直接处理。字段在 Script IR 设计文档 [script-ir.md](script-ir.md) 中定义。

---

## 9. 当前代码资产

### 9.1 `/Users/ygwang/src/huashuo`（本项目）

**正式的 `huashuo` 包**（`src/huashuo/`，M1 完成并加固，2026-09-24）：

| 模块 | 作用 |
|---|---|
| `ingest/` | TXT（严格的编码识别）与 EPUB 2/3（标准库解析）读入为统一的 Book |
| `structure.py` | 生成 `text.txt` 与话本 block：章节 / 卷、场景分隔、各类跳过、硬换行合并、`say` 自动清理 |
| `huaben.py` | 话本读写、不变式检查、按文字对齐的三方合并 |
| `cast.py` | 选角表：默认值、校验、按角色合并 |
| `pipeline.py` | 导入阶段（`machine_output()` 是 M2 接入剧本化的位置）、检查、规划、估算 |
| `units.py` | block → 合成单元与 M4B 章节，边界类型决定停顿 |
| `synth.py` / `asr.py` | 缓存、种子、校验、重试、ASR 校验、重做 |
| `post.py` / `audio.py` / `m4b.py` | 裁静音、响度、限幅、停顿、流式编码为 M4B |
| `engines/` | Qwen3-TTS（mlx-audio）与测试用的假引擎；`qwen3_voices.py` 把库音色的向量注入 CustomVoice（路线 C） |
| `attribution.py` | LLM 调用（缓存、预算、并发）、角色表、说话人与情绪标注、主要角色的音色推荐 |
| `library.py` / `voices/` | 内置音色库：`voices/zh/<id>.{json,npy}` 与预置音色的标签（`voices/presets.json`） |
| `casting.py` | 自动选角（§8.7） |
| `cli.py` | `huashuo` 命令：make / import / check / synth / package / redo / audition / voices |

测试在 `tests/`（假引擎，无需模型，约 2 秒；`model` 标记的测试需显式开启），CI 在 GitHub Actions（Ubuntu，Python 3.10 / 3.12）。**测试一律不调用付费 API、不访问网络**：密钥从环境中移除、`.env` 不加载、pydantic-ai 拒绝真实请求、非本机的网络连接直接失败；LLM 相关代码用模拟模型测试。用真实 LLM 评估准确率的是手动运行、带 `--max-cost` 上限的脚本，不属于测试集。

**原型** `experiments/novel_tts.py`（单文件、单音色、输出 MP3）保留作参考；它的分块、缓存、续跑、校验思路已并入正式包（种子改为由缓存键派生，而不是按分块序号）。`experiments/` 下另有 EXP-1～3 的实验脚本。

### 9.2 `/Users/ygwang/src/audiobook-creator`（参考，只读）

上游原版，GPL-3.0。**仅作参考，不复制代码。**

### 9.3 `/Users/ygwang/src/landandan-audiobook-creator`（参考 + 实验）

中文 fork，GPL-3.0。本 session 在其上做了 §3.4 的修改并完成了 §3.2 的验收测试。**仅作参考与对拍基准，不复制代码。**

---

## 10. 阶段计划与设计要点

第一阶段的阶段划分、范围与完成标准以 [requirements.md](requirements.md) §6 为准（2026-09-25 重新划分，§9.1 Q19）；本节记录已完成的工作，以及未完成阶段的设计要点。

### 10.1 已完成

| 阶段 | 完成 | 结论与记录 |
|---|---|---|
| EXP-1 音色实验 | ✅ 2026-09-24 | 路线 C（Base 向量注入 CustomVoice），6 个中文音色（§5.6） |
| EXP-2 批量标注对拍 | ✅ 2026-09-24 | 段落级批量标注成立，默认模型 gpt-6-sol（§7.2.1） |
| EXP-3 口音检测 | 暂停 | 第一种方案（声调一致性）未成功（§5.8）；第二种方案（ASR 编码器嵌入做口音分类）可选，不在任何阶段的必做范围内 |
| M1 单音色 M4B | ✅ 2026-09-24 | 包结构、话本、缓存续跑、响度与停顿、M4B 封装（§9.1） |
| M2 剧本化 | ✅ 2026-09-24 | 对白切分、角色表、批量说话人标注、回答缓存、预算与同意（§7.2.1） |
| M3 多角色 | ✅ 2026-09-25 | 16 个中文库音色（§5.6）、自动选角与 LLM 推荐（§8.7）、试听、情绪提示（§5.9）、句末截断检查（§5.10） |
| M4 补齐 | ✅ 2026-09-25 | 元数据参数、标点规整、响度与停顿可配置、按章进度、英文书只用旁白（§10.2） |

另外：Script IR 设计文档 [script-ir.md](script-ir.md)；项目命名「话说 Huashuo」（§8.6）。

### 10.2 M4 补齐：设计要点（✅ 已实现）

**标点规整（TXT-7）**：目标是中文正文里的标点一律用全角，英文词、数字与英文书保持半角。

- 字符判断用标准库 `unicodedata`：`category()` 区分标点（P\*）与符号，`east_asian_width()` 区分全角与半角；兼容形式（竖排标点 U+FE10–FE1F、U+FE30–FE4F，小写变体 U+FE50–FE6F，全角 ASCII U+FF01–FF5E）先经 NFKC 还原为基本形式，再按上下文决定用全角还是半角
- 中文标点的全集采用第三方库 `zhon`（2.1，MIT，纯 Python、无依赖）的 `hanzi.punctuation`（82 个，含〖〗〝〞‧﹏ 等容易漏掉的）；汉字判断用它的 `hanzi.characters`（含扩展区）。实现在 `punct.py`
- 上下文规则：两侧（跳过空白）至少一侧是汉字的半角 `, . ? ! : ; ( )` 改为全角；数字内部的 `.` `:` `,`（3.5、3:2、1,000）不动；`...`、`。。。` 规整为 `……`，`--` 规整为 `——`；引号的归一化沿用 TXT-6 的现有规则
- 规整在清洗阶段完成，`text.txt` 与话本同时变化，逐字对账（I1）不受影响。代价：升级后第一次重新导入时文字有变化的段落按文字对齐可能对不上，用户在这些段落上的修改会进入 `state/orphaned-edits.jsonl`（script-ir §7），需要在发布说明里提示

**元数据参数（IN-4）**：`import` / `make` 增加 `--title`、`--author`，与 `--cover` 一样记在 `state/ingest.json`，之后每次导入都写进话本头记录，所以重新导入不需要重复参数；用户在头记录里手改的值仍按三方合并保留。

**响度与停顿（POST-1、POST-3）**：`--loudness LUFS` 与可重复的 `--pause KIND=SECONDS`（KIND 为 script-ir §6 的边界类型：sentence、turn、paragraph、heading、title、break、chapter_end、end）。它们只影响后期（增益与停顿不进入缓存键），所以改了只需重新封装。与 `--voice`、`--titles` 一样记在 `state/run.json`，`package`、`redo` 沿用。

**按章节的进度（SYN-7）**：进度行在整体进度之外显示「第 i / N 章」与本章完成比例；按章节合成（`--chapters`）时 N 是所选章节数。

**英文书只用旁白音色（Q19）**：规则定为「某种语言有库音色才自动选角」。英文目前没有库音色，因此英文书的角色一律不分配音色，对白由旁白朗读；主要角色的音色推荐也不调用。用户在 `cast.json` 里手动指定的音色（如 `preset:aiden`）照常生效。等英文音色库建成，这条规则自动放开，不需要额外开关。

**模拟测试文件**：带卷结构、GBK 编码、全半角混杂标点的网文风格 TXT，由测试代码生成，不放真实书籍。

### 10.3 M5 打磨：设计要点

- **读音词典（PRON-1、PRON-4）**：工作目录里一个用户编辑的文件（每行「词语 → 读法」），导入时对匹配的文字生成 `say` hint（script-ir §4 已有 `say` 字段与三方合并规则），原文不变、来源可追溯。读法的写法先做一次小实验：Qwen3-TTS 能否直接读带声调的拼音；不能则只支持同音字替换
- **数字、日期、单位（PRON-2）**：先实测 Qwen3-TTS 自带的读法（年份、序数、百分比、比分、日期、时刻、单位、电话号码），只为读错的类别写归一化规则，避免重复模型已经做对的事
- **网文噪声（TXT-5）**：一组保守的整行规则（求票、「本章完」、作者感言标记、站点水印与网址），命中的行标为 `skip`（reason `noise`），宁可漏标不误标；`--dry-run` 列出被标记的行
- **开场与结尾（POST-6）**：开场是全书第一个单元，旁白读「《书名》，作者，……」；结尾一句收束提示；可附加「本有声书由话说 Huashuo 生成」。都可关闭，开场不单独成一个 M4B 章节
- **清理缓存（CLI-6）**：`huashuo clean BOOK` 删除 `cache/` 下的音频，保留话本、选角表、LLM 回答缓存与重做记录
- **故事集篇名前缀**（script-ir §10 S4）、**英文单引号对白**（TXT-6，需区分撇号与引号）
- **方言句末误判**（§5.10）：比较句末时跳过方言语气词（唦、嘞、噻、咯……），方言占比高的台词只按字错率判定，不做句末检查
- **配角音色过于集中**（§8.7）：先在《春尽江南》上评估借用主要角色音色的规则，再决定是否采用

### 10.4 M6 发布准备（决定公开仓库之后）

1. PyPI 包（CLI-1）：音色库随包发布（16 个音色共约 130 KB），README 面向公开用户重写
2. 速度与内存实测（NFR-2、NFR-5）：库音色下的整书合成速度，16 GB 内存机器上的整书运行
3. Apple Books 以外的播放器（M4B-5：Audiobookshelf、BookPlayer、一款 Android 播放器）
4. 给 mlx-audio 提「直接传 speaker 向量」的接口，替换对 `_prepare_generation_inputs()` 的 monkeypatch，解除对 mlx-audio 0.5.5 的锁定（向上游提交前征得项目负责人同意）

### 10.5 第二阶段及以后

- **英文音色库**（CAST-2）：与 EXP-1 相同的流程；建成后英文书自动启用多角色（§10.2）
- 按书设计音色、克隆声音（CAST-12、CAST-13）
- Jev 在中文说话人归属上的**真实准确率**与**置信度校准质量**；置信度分诊方案（§7.3、§7.4）
- 其余 P2 需求（requirements §3 中标为 P2 的各项）

### 10.6 社区回馈

- 向 landandan 提 issue：`kokoro_zh` 方言音色 bug（§5.2）
- mlx-audio 的 speaker 向量接口（见 §10.4）

---

## 11. 参考资料

### 项目

- [prakharsr/audiobook-creator](https://github.com/prakharsr/audiobook-creator) — 参考原型，GPL-3.0
- [landandan/audiobook-creator](https://github.com/landandan/audiobook-creator) — 中文 fork
- [Blaizzy/mlx-audio](https://github.com/Blaizzy/mlx-audio) — MIT，Apple Silicon TTS 推理
- [QwenLM/Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS) — Apache-2.0
- [travisvn/openai-edge-tts](https://github.com/travisvn/openai-edge-tts) — edge-tts 的 OpenAI 兼容封装
- [remsky/Kokoro-FastAPI](https://github.com/remsky/Kokoro-FastAPI) — Kokoro 的 OpenAI 兼容封装
- [hexgrad/Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) — Apache-2.0，82M TTS

### 竞品

- [DrewThomasson/ebook2audiobook](https://github.com/DrewThomasson/ebook2audiobook) · [santinic/audiblez](https://github.com/santinic/audiblez) · [aedocw/epub2tts](https://github.com/aedocw/epub2tts) · [khimaros/autiobook](https://github.com/khimaros/autiobook)
- [Finrandojin/alexandria-audiobook](https://github.com/Finrandojin/alexandria-audiobook) · [dudarenok-maker/Castwright](https://github.com/dudarenok-maker/Castwright)
- [sdsds222/Unitale](https://github.com/sdsds222/Unitale) · [cosin2077/easyVoice](https://github.com/cosin2077/easyVoice) · [LiberSonora](https://github.com/LiberSonora/LiberSonora) · [wu-boshi/B2A-Studio](https://github.com/wu-boshi/B2A-Studio)

### 命名调研

- [话本小说（ihuaben.com）](https://www.ihuaben.com/app.html) — 「话本」淘汰理由
- [gedoor/legado](https://github.com/gedoor/legado) — 「阅读」淘汰理由

### 技术文档

- [Anthropic OpenAI SDK compatibility](https://platform.claude.com/docs/en/api/openai-sdk) — 兼容层限制
- [TypeSafe: Introducing System One Models & Jev](https://typesafe.ai/blog/introducing-system-one-models-and-jev)
- [Jev 1.13 on OpenRouter](https://openrouter.ai/typesafe/jev-1.13)
- [Azure Speech 语言与语音支持](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/language-support)
- [zh-CN-liaoning-XiaobeiNeural（方言音色佐证）](https://json2video.com/ai-voices/azure/voices/zh-cn-liaoning-xiaobeineural/)
- pydantic-ai v1.18.0 `models/openai.py` — `model_settings` 的显式挑 key 行为

---

## 附录 A：实测数据速查

| 项目 | 数值 | 来源 |
|---|---|---|
| Qwen3-TTS 0.6B，30 万字 | 6:20:51 → 18:54:16 音频（RTF ~2.98x） | M1 实测 |
| Qwen3-TTS 1.7B，30 万字 | 7:19:54 → 19:34:33 音频（RTF ~2.67x） | M1 实测 |
| 中文 token 密度 | 13 字 ≈ 12 token（tiktoken cl100k_base，接近 1:1） | 本地实测 |
| Pass 1 单批（5 行中文） | 1398 input / 458 output | gpt-5.6-luna 实测 |
| 说话人归属准确率（40 行样本） | 11/11 | landandan fork 实测 |
| edge-tts 输出格式 | 24kHz / 48kbps / 单声道 MP3（固定） | 实测 |
| edge-tts 标准普通话音色数 | 6（4 男 2 女）+ 2 方言 | `edge-tts --list-voices` |
| Kokoro-FastAPI 中文音色 | v0.2.1 与 v0.9.0 均含 8 个 zh 音色 | GitHub API 核查 |

---

## 附录 B：中文文本处理已知陷阱清单

以下是在 audiobook-creator 上实测出的中文陷阱。**新项目应在设计阶段就规避，而不是事后打补丁。**

| 陷阱 | 现象 | 实测证据 |
|---|---|---|
| **路径白名单拒绝非 ASCII** | 中文书名的文件直接被拒 | `validate_file_path_allowlist('/tmp/红楼梦.epub')` → `False` |
| **文件名清洗删光中文** | 章节音频文件名变空串，互相覆盖 | `sanitize_filename("第一章 风雪山神庙")` → `''` |
| **章节正则只认英文** | 整本书变成单章，M4B 无章节 | `check_if_chapter_heading("第一章 风雪山神庙")` → `False` |
| **对话切分只认 ASCII 引号** | 全角 `""` 经归一化后可用；**直角 `「」` 完全失效** | 实测切分结果 |
| **标点集不含中文** | 纯中文标点行被送去 TTS | `is_only_punctuation("。，！")` → `False` |
| **标题启发式用空格分词** | 中文无空格 → `len(line.split()) == 1` → **几乎每行都被判成标题** | 33 字叙述句被判为标题 |
| **补的是英文句号** | 中文文本混入半角 `.`，影响韵律 | — |
| **情感关键词是英文正则** | `\b(laugh\|sigh\|...)` 对中文完全无效，且 `\b` 对中文不可靠 | — |
| **提示词要求 lowercase** | 中文无大小写，规则空转 | — |
| **别名规则是英文命名体系** | `Mr. Dursley = Vernon Dursley` 的逻辑不适用于「贾宝玉/宝玉/宝二爷」 | — |
| **同姓误合并风险** | 英文提示词鼓励 substring 匹配 → 会把「萧战」并入「萧炎」 | landandan 已显式拦截 |
| **token 预算按英文设定** | 中文 token 密度高 3–4 倍，`MAX_BATCH_TOKENS=2000` 只装 2000 字 | 实测 |
| **方言音色混入** | Azure/Kokoro 的 `xiaobei`(辽宁) `xiaoni`(陕西) 是方言音色 | **实测确认为东北话** |
| **引号归一化顺序** | 两道归一化先后不一致，导致文本内混用 `"` 与 `""` | 实测输出 |

**已验证可用的中文章节正则：**

```python
r'^\s*(?:第\s*[0-9零〇一二三四五六七八九十百千万两]+\s*[章回卷部节集]|楔子|序章|序言|尾声|终章|番外篇?)'
```

测试通过：`第一章 风雪山神庙` ✓ / `第 12 回 大闹天宫` ✓ / `楔子` ✓ / `第三卷 风起` ✓ / `第三百零五章` ✓ / `番外篇：十年后` ✓；不误伤 `他第一章都没看` ✓
