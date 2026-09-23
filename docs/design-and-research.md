# 话说 Huashuo：中文有声书生成项目 —— 设计与调研文档

| 项目 | 内容 |
|---|---|
| 状态 | 调研完成，架构方向已确定，尚未立项 |
| 日期 | 2026-09-23 |
| 项目名称 | **话说 Huashuo**（`huashuo`，见 §8.6；早期测试代号 `novel-tts` 已弃用） |
| 目标许可证 | Apache-2.0 |
| 文档作用 | 记录调研结论、实测数据、技术选型与设计决策及其理由和出处。**要做什么、做到什么程度**见 [requirements.md](requirements.md) |

---

## 0. 快速摘要

我们计划新建一个开源的有声书生成项目 **话说（Huashuo）**，核心差异化是 **「LLM 做多角色剧本化 + 中文优先 + Apple Silicon 本地 TTS + 标准有声书封装」**。其中「LLM 多角色 + 有声书封装」已有项目在做（见 §4.2），**「中文优先 + Apple Silicon 本地推理」这一层目前没有人占据**。

五个已确定的关键决策：

1. **TTS 主力用 Qwen3-TTS 1.7B**，经 mlx-audio 在 macOS 本地运行（已实测，中文质量优秀）
2. **文本分析用云端 LLM API**（本地小模型中文理解不足），但改为**段落级批量标注**而非逐句调用（成本与调用次数降低约 40 倍）
3. **新建项目而非 fork**，采用 Apache-2.0（参考项目为 GPL-3.0）
4. **核心架构是「剧本」中间表示（Script IR）**：文档 → 剧本 → 音频
5. **项目命名为「话说 Huashuo」**（2026-09-23 确定，见 §8.6）
6. **角色声音来自内置音色库**：预先设计并固化 12–20 个与输入无关的音色，随项目发布（2026-09-23 确定；固化方式初步倾向「向量注入 CustomVoice」，EXP-1 进行中，见 §5.6）
7. **LLM 只走 OpenAI 兼容接口，经 pydantic-ai 接入**，不写提供商专用适配器（2026-09-23 确定，见 §6.5）

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
| pydub | MIT |
| ffmpeg / calibre | GPL/LGPL，但**以子进程调用**，不构成链接 |

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

#### EXP-1 初步结果（2026-09-23，路线 C 领先）

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
2. **注入的音色支持 `instruct`**，而且效果明显。这让 SCR-12（情绪提示）不再受模型能力限制
3. **运行时只需要 CustomVoice 一个模型**：预置旁白与库音色都走它。VoiceDesign 和 Base 只在**建库时**使用（设计声音、提取向量），合成一本书时不用加载
4. **路线 A 淘汰**：两轮都偏快、偏离新台词，且最慢
5. **音色向量相似度不足以单独作为关卡指标**：它能区分不同的人（0.92 vs 0.72），但分辨不出 A / B / C 在年龄感上的差别——关卡以盲听为主，向量相似度只作下限检查

**尚未验证（EXP-1 剩余工作）：**

- 只试了一个音色：还需儿童、少年、青年男女、中年女性等差异大的音色，尤其是女声与童声
- 稳定性：每个音色 30 句、多个 seed，以及 400 字长块（原型的分块上限）上是否漂移
- 多种 instruct（愤怒、哭腔、耳语……）下音色是否保持
- 多个注入音色之间两两可区分

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

**这是整个项目最贵的一个设计决定，改起来最痛，必须先定下来。**

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

**不发明新的基础格式**：话本文件是 JSONL（一行一个 block），扩展名 `*.huaben.jsonl`，任何 JSON 工具和编辑器都能直接处理。字段在 Script IR schema 设计文档（§10.2）中定义。

---

## 9. 当前代码资产

### 9.1 `/Users/ygwang/src/huashuo`（本项目）

已有一个可用的 TTS 原型 `experiments/novel_tts.py`（约 29k，单文件 CLI）。它是命名前的测试工具，保留原文件名，作为实验脚本放在 `experiments/` 下；`huashuo` 这个 CLI 名留给正式流水线。在仓库根目录运行：

```bash
.venv/bin/python experiments/novel_tts.py book.txt -o book.mp3
.venv/bin/python experiments/novel_tts.py book.txt --sample        # 试听开头
.venv/bin/python experiments/novel_tts.py book.txt --continue      # 断点续跑
```

**已经实现了新项目需要的几个关键能力**（可直接演进）：

- 按段落/句子边界分块（默认 400 字符上限）
- **分块磁盘缓存**，cache key 由文本内容 + 所有影响音频的参数派生
- **断点续跑**
- **按 chunk index 播种**，重跑产生逐字节相同的音频（续跑不会音色漂移）
- 流式送入 ffmpeg，不在内存里堆数小时音频
- 时长/字数校验（捕获空输出、token 上限截断、复读失控），失败两次的块保留并在最后报告

**v1 限制**：全书固定单音色，无角色分配；文本归一化极简。

### 9.2 `/Users/ygwang/src/audiobook-creator`（参考，只读）

上游原版，GPL-3.0。**仅作参考，不复制代码。**

### 9.3 `/Users/ygwang/src/landandan-audiobook-creator`（参考 + 实验）

中文 fork，GPL-3.0。本 session 在其上做了 §3.4 的修改并完成了 §3.2 的验收测试。**仅作参考与对拍基准，不复制代码。**

---

## 10. 待办与下一步

第一阶段的需求、里程碑与验收标准见 [requirements.md](requirements.md)；本节只列调研与设计层面的待办。

### 10.1 立即执行（P0）

1. **EXP-1 音色实验**（与 M1 并行，决定多角色能否进入第一阶段）：比较 §5.6 的路线 A / B / C。**进行中**：两轮初步结果显示路线 C 领先、A 淘汰；剩余工作见 §5.6 末尾
2. **EXP-2 批量标注对拍**：在本仓库中实现段落级批量标注（先作为 `experiments/` 下的实验脚本），与 landandan fork 的逐句实现**对拍**
   - 同一份中文文本，对比说话人归属准确率与 token 消耗
   - 输出天然就是 Script IR 的雏形——顺手验证剧本模型
3. EXP-2 数据出来后，决定是否正式立项

### 10.2 立项后

4. **先写 Script IR 的 schema 设计文档**（最贵的决定，改起来最痛）。格式已定为 JSONL、名为「话本」、扩展名 `*.huaben.jsonl`（§8.6）；字段设计参考 Alexandria 的剧本格式（§4.3）
5. ~~项目命名讨论~~ ✅ 已定为「话说 Huashuo」（§8.6）；仓库目录已改名为 `huashuo`，原型移入 `experiments/`。正式的 `huashuo` 包与 CLI 随流水线实现一起建立
6. 音频封装层重建（M4B / 章节 / 元数据 / 静音）
7. 角色 → 音色分配（携带完整画像，不走 gender_score），从内置音色库中选

### 10.3 待评估（有基准后）

8. Jev 在中文说话人归属上的**真实准确率**与**置信度校准质量**
9. 置信度分诊方案

### 10.4 社区回馈

10. 向 landandan 提 issue：`kokoro_zh` 方言音色 bug（§5.2）

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
