# 话本（Script IR）格式设计

| 项目 | 内容 |
|---|---|
| 状态 | v1.4：与代码同步（M5 完成：读音词典、网文噪声、开场结尾、故事集篇名，2026-09-25）；§10 有待定项 |
| 日期 | 2026-09-24（2026-09-25 更新） |
| 格式版本 | `huaben` 1 |
| 文档作用 | 定义剧本中间表示「话本」、选角表与每本书工作目录的格式。需求见 [requirements.md](requirements.md)，设计背景见 [design-and-research.md](design-and-research.md) §8.3 |

---

## 1. 目的

流水线是 **文档 → 话本 → 音频**。话本是两半之间唯一的接口：

- **前半**（导入、清洗、章节识别、剧本化）只负责写出话本
- **后半**（选角、合成、后期、封装）只读话本，不回头看原始文档

因此话本必须同时满足：

1. **人能读、能手改**：说话人判错、读音不对、想跳过某段，都改这个文件（SCR-1、SCR-10）
2. **机器能可靠解析、能校验**：任何时候都能检查「原文每个字是否都被读到且只读一次」（SCR-7）
3. **与引擎无关**：不出现任何 TTS 模型的参数（SYN-2）
4. **向后可扩展**：播客、网页、论文的 block type 可以加进来而不改已有字段（requirements §1.2 路线图）

格式选择已定：JSONL，一行一条记录，扩展名 `*.huaben.jsonl`（requirements §9.1 Q5）。选角表单独存放（Q12）。

---

## 2. 工作目录

每本书一个工作目录（CLI-4），默认在输入文件旁边，名为 `<书名文件名>.huashuo/`，可用 `--workdir` 指定。成品 M4B 默认写在输入文件旁边（CLI-2）。

```
红楼梦.epub
红楼梦.m4b                       成品
红楼梦.sample.m4b                试听（--sample）；--chapters 3,8 写成 红楼梦.chapters-3_8.m4b，都不覆盖成品
红楼梦.huashuo/
  text.txt                       清洗后的全文，话本 src 偏移的参照（UTF-8）
  script.huaben.jsonl            话本（用户可编辑）
  cast.json                      选角表（用户可编辑）
  pron.txt                       读音词典（用户可编辑，PRON-1）：每行「词语 = 读法」，导入时生成带说明的模板
  review.txt                     说话人未知或把握不高的对白，供优先审阅（SCR-11；没有时不生成）
  cover.jpg / cover.png          封面：用户 --cover 提供的、EPUB 自带的，或打包时生成的文字封面
  state/                         机器状态，用户无需查看
    script.auto.jsonl            上一次机器生成的话本（三方合并的基准，§7）
    cast.auto.json               上一次机器生成的选角表（§8）
    ingest.json                  导入参数（编码、语言、是否读注释、--title / --author）、程序版本、封面来源、
                                 上次用的 LLM 模型，以及已同意发送原文的 LLM 地址
    llm-cache/<key>.json         LLM 的每次回答，按提示词、模型与提示词版本缓存：重新导入不再付费
    run.json                     合成与封装的选项（音色、模型、章节标题、情绪、开场结尾、响度、停顿），
                                 package / redo 沿用
    rerolls.json                 `huashuo redo` 重做过的单元及次数
    orphaned-edits.jsonl         重新导入时放不回去的用户修改（§7），只追加，不丢
  cache/
    units/<key>.wav              合成单元的音频缓存（§6）
    units/<key>.json             该单元的文本、音色、seed、时长、校验结果、ASR 转写与字错率、
                                 所属 block，以及后期分析（有效区间、响度、真峰值）
  logs/
    run-<时间>.log
```

约定：

- **用户只需要碰三个文件**：`script.huaben.jsonl`、`cast.json` 与 `pron.txt`。其余都可以删掉重建（删 `cache/` 等于重新合成，`huashuo clean` 就是删它）
- 目录名、文件名允许任意 Unicode（IN-6）；程序内部不对书名做「文件名清洗」（设计文档附录 B 的陷阱）
- 所有写入都是「临时文件 + 原子改名」（NFR-6）

---

## 3. 话本文件结构

```
第 1 行      头记录（type = "huaben"）
第 2 行起    block 记录，按朗读顺序排列
```

写出规则（SCR-1）：

- UTF-8，不转义非 ASCII 字符（`ensure_ascii=False`）
- 一行一条记录，行内不换行
- 字段顺序固定：block 为 `id`、`type`、`text`，然后是其他字段，`src` 最后；头记录为 `type`、`version`、`title`、`author`、`language`，然后是其他字段。便于肉眼扫读和 diff
- 空行与以 `//` 开头的行被忽略，用户可以在文件里写注释

### 3.1 头记录

```json
{"type": "huaben", "version": 1, "title": "红楼梦", "author": "曹雪芹", "language": "zh", "source": {"path": "../红楼梦.epub", "format": "epub", "sha256": "…"}, "text": "text.txt", "text_sha256": "…", "cover": "cover.jpg", "meta": {"publisher": "…", "date": "…", "description": "…"}}
```

| 字段 | 必需 | 说明 |
|---|---|---|
| `type` | ✅ | 固定为 `"huaben"` |
| `version` | ✅ | 格式版本，整数。读取方遇到比自己新的版本时拒绝并提示升级 |
| `title` / `author` | ✅ | M4B 元数据（M4B-3），可手改 |
| `language` | ✅ | `zh` 或 `en`（IN-5） |
| `source` | ✅ | 原始文件的相对路径、格式与哈希（目前只记录，尚不比较） |
| `text` / `text_sha256` | ✅ | 清洗后全文的文件名与哈希；`src` 偏移以它为准。每次导入都重写 |
| `cover` | | 封面文件名（用户或 EPUB 提供时写入；打包时生成的文字封面不记录） |
| `meta` | | 其他元数据。写入 M4B 的只有 `description` 与 `date`；`publisher` 等只保存在这里 |

### 3.2 block 记录

每条 block 是一段要朗读（或明确跳过）的文字。下例中的偏移仅为示意。

```json
{"id": "c000", "type": "chapter", "text": "楔子", "level": 1, "src": [0, 2]}
{"id": "c000.p0001", "type": "narration", "text": "那一年冬天，雪下得特别大。", "src": [3, 16]}
{"id": "c001", "type": "chapter", "text": "第一章 风雪山神庙", "level": 1, "src": [17, 26]}
{"id": "c001.p0001", "type": "narration", "text": "雪下了整整一夜。林渊推开客栈的木门。", "src": [27, 45]}
{"id": "c001.p0002.01", "type": "dialogue", "text": "“店家，来一壶热酒。”", "speaker": "林渊", "src": [46, 56]}
{"id": "c001.p0002.02", "type": "narration", "text": "他把斗篷上的雪抖落在地。", "src": [56, 68]}
{"id": "c001.p0003", "type": "skip", "text": "ぇ本篇最初發表于……", "reason": "notes", "say": "本篇最初發表于……", "src": [69, 80]}
```

**公共字段**

| 字段 | 必需 | 说明 |
|---|---|---|
| `id` | ✅ | 全文唯一，见 §3.3 |
| `type` | ✅ | 见 §4 |
| `text` | ✅ | **原文切片**，必须等于 `text.txt[src[0]:src[1]]`。不要改它；想改读法用 `say` |
| `src` | ✅* | `[起, 止)`，`text.txt` 中的字符偏移（Unicode 码位，不是字节）。*用户手工插入、原文中没有的 block（如补一句报幕）可以不写 `src`：照常朗读，但不参与 I3–I5 |
| `say` | | 实际朗读的文字，缺省时读 `text`。读音修正（PRON-1）、数字读法（PRON-2）都落在这里。导入时程序也会自动写入：中文正文里零星的日文假名（有些转换版本用它标注释号）和占位方框（□■）不读 |
| `pause_after` | | 该 block 之后的停顿秒数，覆盖默认规则（POST-3）；带它的 block 总是结束一个合成单元 |

**类型专属字段**见 §4。未知字段一律原样保留，不报错（便于扩展与用户自己加批注）。

### 3.3 id 规则

`c<章序号 3 位>[.p<段序号 4 位>[.<段内序号 2 位>]]`

- 章节按出现顺序从 `c000` 起编号；段落在章内从 `p0001` 起编号；一段被切成旁白 / 对白 / 旁白时：`c001.p0012.01`、`.02`、`.03`；整段只是一句引语时保留原 id
- 第一个章节标题之前若有内容（序、题记、无标题的开篇），它们成为 `c000`，后面的章节依次后移：文件开头若有一行正好是书名，就用它作 `c000` 的标题，否则把书名作为一行写进 `text.txt`。开篇章节总是顶层（`level: 1`），不算作「卷」
- id 只在一次导入内稳定：原文或清洗规则变化后，同一段落可能换了 id。**合并不依赖 id**，而是按文字内容对齐（§7），所以 id 变化不会让修改落到别的段落上
- 用户手工插入的 block 可以用任意不重复的 id（如 `c001.p0012.x1`）

---

## 4. block 类型

| type | 读不读 | 专属字段 | 说明 | 第一阶段 |
|---|---|---|---|---|
| `chapter` | 读（章节标题朗读，POST-5，`--no-titles` 可关） | `level`：1 = 顶层，2 = 卷下的章 | **每个 chapter block 开始一个 M4B 章节**（M4B-2） | ✅ |
| `heading` | 读 | `level` | 章内小标题，不产生 M4B 章节。EPUB 的 h1–h6 取其级别；「一」「（二）」这类节号为 3 | ✅ |
| `narration` | 读 | — | 旁白 / 叙述，用旁白音色 | ✅ |
| `dialogue` | 读 | `speaker`、`emotion`、`conf` | 对白：导入时在引号处切分（`“”`「」『』与直引号，支持嵌套；段末未闭合的引语延续到段尾），再由 LLM 标注说话人与情绪 | ✅ M2；`emotion` M3 |
| `skip` | **不读** | `reason` | 保留在话本里但不朗读，保证对账时原文仍被完整覆盖（TXT-4、TXT-5）。程序写入的 `reason`：`toc`（目录）、`copyright`（版权页）、`license`（Project Gutenberg 首尾的声明）、`notes`（注释段落，`--read-notes` 时改为朗读）、`duplicate`（紧接着重复一遍的标题）、`noise`（网文噪声行：求票、「本章完」、作者附言 PS、站点水印，TXT-5）、`author`（TXT 开头的「作者：某某」行，由开场白读出，POST-6）。用户自己跳过时可写任意 `reason`，如 `user` | ✅ |
| `break` | 不读，插入停顿 | — | 场景分隔（`***`、`◇◇◇` 等），对应 POST-3 的场景停顿 | ✅ |
| `equation` / `figure` / `table` / `footnote` | — | — | **预留**给论文与网页场景，第一阶段不产生、遇到时按 `skip` 处理 | 预留 |

`dialogue` 的专属字段：

| 字段 | 说明 |
|---|---|
| `speaker` | 选角表中的角色规范名，或 `"unknown"`（按旁白音色朗读，SCR-5）。切分后先是 `"unknown"`；LLM 判定为不是说话的引语（书名、招牌、引用的词句、心里的想法）改为 `narration` |
| `emotion` | 可选，语气提示，原样作为 TTS 的 `instruct`（SCR-12）。取值是自然语言短语，用户可手写任意描述（如 `"压低声音、惊恐地说"`），删掉即按平常语气读。程序只写下表中的 7 种：TTS 能明显读出来的基本情绪（设计文档 §5.9）。合成时 `--no-emotions` 整体忽略此字段 |
| `conf` | 可选，0–1 的归属置信度；低于阈值的进入审阅清单（SCR-11）。EXP-2 发现便宜模型的自报置信度不可靠（设计文档 §7.2.1） |

程序写入的 `emotion`（说话人标注时由 LLM 从固定标签中选择，只在原文有明确依据时填写；设计文档 §5.9）：

| 标签 | 中文书写入的 `emotion` | 英文书 |
|---|---|---|
| 高兴 / happy | 用高兴的语气说 | Speak happily |
| 生气 / angry | 用生气的语气说 | Speak angrily |
| 悲伤 / sad | 用悲伤的语气说 | Speak sadly |
| 害怕 / afraid | 用害怕的语气说 | Speak fearfully |
| 惊讶 / surprised | 用惊讶的语气说 | Speak in a surprised tone |
| 低声 / hushed | 压低声音说 | Speak in a lowered voice |
| 严厉 / stern | 用严厉的语气说 | Speak sternly |

`chapter` 的 `level`：书中既有卷又有章时，卷为 1、章为 2；否则所有章节都是 1。`level: 2` 的 M4B 章节名前缀最近的 `level: 1` 标题，如「第一卷 · 第三章 风起」（TXT-3）。卷标题后面紧跟章标题时，卷标题不单独成一个 M4B 章节；只含被跳过内容的章节也不产生 M4B 章节。`huashuo import` 列出的章节编号就是 `--chapters` 用的编号。

---

## 5. 不变式与对账

以下性质任何时候都可以用 `huashuo check` 检查；合成前自动检查，违反则拒绝合成并指出行号。涉及整个文件的问题（I5、I8）排在最前面：

| # | 不变式 |
|---|---|
| I1 | 第一行是头记录，且只有一条头记录 |
| I2 | 所有 `id` 唯一 |
| I3 | `src` 合法，且按文件顺序严格递增、互不重叠 |
| I4 | 每个 block 的 `text` 等于 `text.txt` 对应切片 |
| I5 | **`text.txt` 中每一个非空白字符都落在某个 block 的 `src` 内**（含 `skip`）——这就是「原文每个字都被读到或被明确跳过」（requirements §1.3 第 2 条、SCR-7） |
| I6 | 第一个**可朗读**的 block 是 `chapter`（之前可以有 `skip`，如被跳过的版权页） |
| I7 | `dialogue` 的 `speaker` 在选角表中存在，或为 `"unknown"` |
| I8 | 头记录的 `text_sha256` 与 `text.txt` 一致 |

另有两项检查：`type` 必须是已知类型；可朗读的 block 不能没有可读的文字（`say` 或 `text` 为空）。

I4 + I5 合起来保证「原文逐字对得上」。用户想改读法时改 `say` 而不是 `text`，所以这两条不会因为正常编辑而被破坏；如果用户确实改了 `text`，`check` 会指出并给出原文。

---

## 6. 从 block 到合成单元

block 是**语义单位**，合成单元（unit）是**送进 TTS 的一次调用**。两者分开，是因为原型的经验：太短、缺少上下文的文本读出来仓促，而切在段落中间的边界听得出来（`experiments/novel_tts.py` 的 `build_chunks()`）。

**分组规则**（由程序决定，不写进话本）：

1. 相邻、可朗读、**音色与 `emotion` 都相同**的 block 合成一个单元（中文直接拼接，英文以空格拼接）
2. 单元不跨越：`chapter`、`heading`、`break`、`skip`、音色或情绪的变化，以及带 `pause_after` 的 block（`--no-emotions` 时情绪不参与分组）
3. 单元文字上限默认 400 字（原型的实测值）；超出时在 block 边界切，单个 block 超长时在句末、再在分句标点处切，仍超长的分句硬切
4. `chapter` 与 `heading` 各自单独成一个单元（便于单独控制前后停顿）

**单元之后的停顿**（POST-3，秒；单元首尾的静音先被裁掉）：

| 边界 | 停顿 | 何时 |
|---|---|---|
| `sentence` | 0.45 | 一段过长被切开的地方；同一段内因长度切开 |
| `turn` | 0.45 | 同一段内换了音色（他说 / “好。” / 他走了）；同一人只换了语气时按 `sentence` |
| `paragraph` | 0.9 | 段落之间 |
| `heading` | 1.0 | 小标题之后 |
| `title` | 1.2 | 章节标题之后 |
| `break` | 1.6 | 场景分隔 |
| `chapter_end` | 2.0 | 一章结束 |
| `end` | 1.5 | 全书结束 |

`sentence`、`turn`、`paragraph` 原为 0.3、0.35、0.7 秒；试听《在桥上》后调整（2026-09-25）：旁白音色在单元内部的停顿中位数约 0.4–0.9 秒，0.7 秒的段落停顿与普通停顿区分不开（设计文档 §5.11）。

`pause_after` 覆盖该单元之后的停顿；全书开头另有 0.5 秒。各类停顿的默认值可用 `--pause KIND=SECONDS` 修改，响度目标用 `--loudness`（POST-1、POST-3），记在 `state/run.json`，只影响封装、不需要重新合成。

**开场与结尾**（POST-6）：规划时在最前面加一个旁白单元「《书名》，作者某某。」（英文 "Title, by Author."），属于第一个 M4B 章节；最后加「全书完。」（"The End."），`--credit` 时再加一句「本有声书由话说 Huashuo 生成。」。`--no-opening`、`--no-closing` 关闭。它们不写进话本。

**读音词典**（PRON-1）：规划的最后一步，把 `pron.txt` 中的词语替换成读法（同音字，或带声调符号的拼音；声调数字自动换成符号）。替换只影响送给引擎的文字：话本与 `text.txt` 不变，缓存键随文字变化（只有含这些词的单元重新合成），ASR 校验仍与原文比对。

**缓存键**：`sha256(缓存版本, 引擎身份, 音色身份, instruct, 语言, 实际朗读文字)` 取前 20 位十六进制。引擎身份包括引擎名、模型、temperature、最大 token 数与 mlx-audio 版本；库音色的音色身份是 `library:zh/<id>@<向量文件的哈希>`，所以库里的音色一旦重做，用到它的单元自动失效（CAST-8），预置音色就是它的名字。

- 改一个 block，只有它所在的单元失效（SYN-3）
- **随机种子由缓存键派生**（`int(key[:8], 16) + 100 × 重做次数 + 重试序号`），而不是由单元序号派生：在前面插入一段不会改变后面所有单元的种子，缓存照样命中；重跑结果逐字节一致（SYN-5）。`huashuo redo` 让某个单元从一组新的种子重新开始，重做次数记在 `state/rerolls.json`，所以清空缓存后重建仍得到重做后的版本
- 文字、音色完全相同的单元共享同一份缓存（例如每卷都有的「第一章」），重做其中一个会同时改变它们
- 单元的停顿、响度调整在后期阶段处理，不进入缓存键（改停顿不需要重新合成）

---

## 7. 编辑与重跑（SCR-10）

用户会手改话本，而机器阶段（重新导入、说话人标注）也会重写它。规则是**三方合并**，不需要用户做任何标记：

- 基准：`state/script.auto.jsonl`，上一次机器写出的版本
- 当前：`script.huaben.jsonl`，可能被用户改过
- 新结果：这次机器算出的版本

**按文字对齐，而不是按 id**：基准和新结果的 block 按 `text` 序列对齐（difflib），所以上游插入或删除一段不会让修改落到别的段落上。对齐之后逐字段合并：**当前值 ≠ 基准值** → 视为用户修改，保留当前值；否则采用新结果。`id` 和 `src` 总是取新结果。

| 情形 | 处理 |
|---|---|
| 段落原样存在（可能换了 id） | 逐字段合并；报告里注明新 id |
| 一段被切成几块（旁白与对白分开） | 修改带到各块上：改了 `type`（如设为 `skip`）→ 每块；`pause_after` → 最后一块；其他新增字段 → 第一块；`say` 无法拆分 → 放不回去 |
| 用户删掉的 block | 保持删除 |
| 用户新增的 block（id 不在基准里） | 保留，放在它在用户文件中跟随的那个 block 之后 |
| 机器不再产生、用户也没改过的 block | 去掉 |
| **放不回去的修改**（该段文字已不在书中，或无法拆分的 `say`） | **绝不套到别的段落上**：列入报告，并追加到 `state/orphaned-edits.jsonl` |

没有基准（`state/` 被删）时，保留现有的 `script.huaben.jsonl` 不动。合并后写回 `script.huaben.jsonl`，并把新结果存为新的基准；头记录的 `text_sha256` 总是取新值。

---

## 8. 选角表 `cast.json`

```json
{
  "version": 1,
  "narrator": {"voice": "library:zh/narrator_female"},
  "characters": {
    "林渊": {"aliases": ["林公子"], "gender": "male", "age": "young_adult", "description": "青年剑客，沉稳寡言", "lines": 42, "voice": "library:zh/young_man_deep"},
    "苏晚晴": {"aliases": ["苏姑娘"], "gender": "female", "age": "young_adult", "description": "…", "lines": 31, "voice": "library:zh/young_woman_cool"}
  }
}
```

| 字段 | 说明 |
|---|---|
| `narrator.voice` | 旁白音色；章节标题、开场结尾报幕同样使用（CAST-4）。导入时按书选择：第一人称按叙述者性别、否则按明显的主角性别，中文为 `library:zh/narrator_female` 或 `library:zh/narrator_male`，英文 `preset:ryan`；`--narrator` 或手改覆盖，手改的在重新导入后保留 |
| `characters` | 以规范名为键。`aliases`、`gender`、`age`、`description`、`lines` 由剧本化阶段写入（SCR-3） |
| `voice` | 音色引用：`preset:<名称>`（CustomVoice 预置）、`library:<语言>/<音色 id>`（内置音色库，CAST-1）；以后可加 `custom:<路径>`（CAST-12、CAST-13）。`huashuo voices --library` 列出可选的音色及其描述，`huashuo audition` 用每个角色的一句真实台词试听 |

`age` 取值：`child` / `teen` / `young_adult` / `middle_aged` / `elderly`（剧本化阶段也可能写 `unknown`）。`gender`：`male` / `female` / `unknown`。

读取时会校验：JSON 格式、`narrator.voice` 必须存在、`characters` 必须是以名字为键的对象、`voice` 必须是非空字符串；出错时给出文件位置与修改建议，不抛出程序异常。缺少 `cast.json` 时按书的语言使用默认旁白。

选角表由导入阶段生成，走与话本相同的三方合并（基准为 `state/cast.auto.json`），**按角色逐个合并**：用户改过的字段（如 `voice`）保留，用户新增或删除的角色保持原样，其余取机器的新结果（CAST-10）。

**自动选角**（M3，设计文档 §8.7）：每次导入都为每个角色重新选一次音色。与基准不同的 `voice` 视为用户的选择，原样保留，选角绕开它；旁白用 `cast.json` 里当前的 `narrator.voice`，角色不会分到旁白的音色。第一人称的「我」/ "I" 用旁白音色（SCR-9）。改了某个角色的 `voice`，只有这个角色的单元需要重新合成。

M1 只使用 `narrator`。

---

## 9. 各阶段使用的子集

| | M1 | M2 | M3 |
|---|---|---|---|
| block 类型 | `chapter`、`heading`、`narration`、`skip`、`break` | 加上 `dialogue` | 同 M2 |
| 字段 | 公共字段；`pause_after` 由用户手写；`say` 可手写，导入时也会为假名与占位方框自动生成 | 加上 `speaker`、`conf` | 加上 `emotion` |
| 选角表 | 只有 `narrator` | 加上 `characters`（别名、性别、年龄、描述、台词数） | 每个角色加上 `voice`（自动选角） |
| 合成 | 全书旁白音色 | 仍是旁白音色，但话本已标明谁在说话 | 每个角色用自己的音色，对白带语气提示 |

说话人标注的调用方式（模型、缓存、预算、同意）见 requirements §3.9 与设计文档 §7.2.1。

---

## 10. 待定

| # | 问题 | 当前倾向 |
|---|---|---|
| S1 | 单元内多个段落拼接时，段落之间是否插入换行或停顿标记？原型是直接拼接，听感可接受，但段落停顿因此交给模型决定，不受 POST-3 控制 | M1 沿用原型，试听未发现问题；若日后发现段落停顿不明显，改为「每段一个单元，短段合并到至少 N 字」 |
| S2 | `level` 是否需要第 3 层（部 / 卷 / 章） | 否，第一阶段两层足够 |
| S3 | 是否把 `text.txt` 的内容直接放进头记录，省掉一个文件 | 否：30 万字的单行 JSON 不利于手工查看，`text.txt` 本身也是有用的中间产物 |
| S4 | ~~故事集里的篇名后面紧跟编号章节（如「阿Q正傳」后接「第一章序」）时，章节名不带篇名前缀~~ | ✅ M5：这样的篇名成为顶层章节，其下的编号章节带前缀（「阿Q正傳 · 第一章序」）；有编号卷的书不受影响 |
