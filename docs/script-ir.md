# 话本（Script IR）格式设计

| 项目 | 内容 |
|---|---|
| 状态 | v1，M1 已按此实现（§10 有待定项） |
| 日期 | 2026-09-24 |
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
4. **向后可扩展**：播客、网页、论文的 block type 可以加进来而不改已有字段（§1.2 路线图）

格式选择已定：JSONL，一行一条记录，扩展名 `*.huaben.jsonl`（requirements §9.1 Q5）。选角表单独存放（Q12）。

---

## 2. 工作目录

每本书一个工作目录（CLI-4），默认在输入文件旁边，名为 `<书名文件名>.huashuo/`，可用 `--workdir` 指定。成品 M4B 默认写在输入文件旁边（CLI-2）。

```
红楼梦.epub
红楼梦.m4b                       成品
红楼梦.huashuo/
  text.txt                       清洗后的全文，话本 src 偏移的参照（UTF-8）
  script.huaben.jsonl            话本（用户可编辑）
  cast.json                      选角表（用户可编辑）
  cover.jpg                      封面（从 EPUB 提取或用户提供）
  state/                         机器状态，用户无需查看
    script.auto.jsonl            上一次机器生成的话本（三方合并的基准，§7）
    ingest.json                  导入时的参数与程序版本
  cache/
    units/<key>.wav              合成单元的音频缓存（§6）
    units/<key>.json             该单元的校验结果、ASR 转写、耗时
  logs/
    run-<时间>.log
```

约定：

- **用户只需要碰两个文件**：`script.huaben.jsonl` 与 `cast.json`。其余都可以删掉重建（删 `cache/` 等于重新合成）
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
- 字段顺序固定：`id`、`type`、`text`，然后是其他字段，`src` 最后。便于肉眼扫读和 diff
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
| `source` | ✅ | 原始文件的相对路径、格式与哈希，用于判断原文是否变化 |
| `text` / `text_sha256` | ✅ | 清洗后全文的文件名与哈希；`src` 偏移以它为准 |
| `cover` | | 封面文件名 |
| `meta` | | 其他元数据（出版者、日期、简介……），原样写入 M4B |

### 3.2 block 记录

每条 block 是一段要朗读（或明确跳过）的文字。下例中的偏移仅为示意。

```json
{"id": "c001", "type": "chapter", "text": "第一章 风雪山神庙", "level": 1, "src": [0, 9]}
{"id": "c001.p0001", "type": "narration", "text": "雪下了整整一夜。林渊推开客栈的木门。", "src": [10, 28]}
{"id": "c001.p0002.01", "type": "dialogue", "text": "“店家，来一壶热酒。”", "speaker": "林渊", "src": [29, 39]}
{"id": "c001.p0002.02", "type": "narration", "text": "他把斗篷上的雪抖落在地。", "src": [39, 51]}
{"id": "c001.p0003", "type": "skip", "text": "（本章完，求月票！）", "reason": "noise", "src": [52, 62]}
```

**公共字段**

| 字段 | 必需 | 说明 |
|---|---|---|
| `id` | ✅ | 全文唯一，见 §3.3 |
| `type` | ✅ | 见 §4 |
| `text` | ✅ | **原文切片**，必须等于 `text.txt[src[0]:src[1]]`。不要改它；想改读法用 `say` |
| `src` | ✅* | `[起, 止)`，`text.txt` 中的字符偏移（Unicode 码位，不是字节）。*用户手工插入、原文中没有的 block（如补一句报幕）可以不写 `src`：照常朗读，但不参与 I3–I5 |
| `say` | | 实际朗读的文字，缺省时读 `text`。读音修正（PRON-1）、数字读法（PRON-2）都落在这里 |
| `pause_after` | | 该 block 之后的停顿秒数，覆盖默认规则（POST-3） |

**类型专属字段**见 §4。未知字段一律原样保留，不报错（便于扩展与用户自己加批注）。

### 3.3 id 规则

`c<章序号 3 位>[.p<段序号 4 位>[.<段内序号 2 位>]]`

- 章：`c001`；章内第 12 段：`c001.p0012`；该段被切成旁白 / 对白 / 旁白时：`c001.p0012.01`、`.02`、`.03`
- 第一章之前的内容（序、题记、无标题的开篇）归入 `c000`：文件开头若有一行正好是书名，就用它作 `c000` 的标题，否则把书名作为一行写进 `text.txt`。`c000` 总是顶层（`level: 1`），不算作「卷」。只有被跳过内容、没有可朗读内容的章节不产生 M4B 章节
- id 只在一次导入内稳定：同一份原文、同一版清洗规则，得到同样的 id。清洗规则变化导致重新导入时，按 id 尽力合并，对不上的用户修改会列出来（§7）
- 用户手工插入的 block 可以用任意不重复的 id（如 `c001.p0012.x1`）

---

## 4. block 类型

| type | 读不读 | 专属字段 | 说明 | 第一阶段 |
|---|---|---|---|---|
| `chapter` | 读（章节标题朗读，POST-5，可关） | `level`：1 = 顶层（卷 / 部 / 章），2 = 卷下的章 | **每个 chapter block 开始一个 M4B 章节**（M4B-2） | ✅ |
| `heading` | 读 | `level` | 章内小标题，不产生 M4B 章节 | ✅ |
| `narration` | 读 | — | 旁白 / 叙述，用旁白音色 | ✅ |
| `dialogue` | 读 | `speaker`、`emotion`、`conf` | 对白 | M2 起 |
| `skip` | **不读** | `reason`：`toc` / `copyright` / `noise` / `user` | 保留在话本里但不朗读，保证对账时原文仍被完整覆盖（TXT-4、TXT-5） | ✅ |
| `break` | 不读，插入停顿 | — | 场景分隔（`***`、`◇◇◇` 等），对应 POST-3 的场景停顿 | ✅ |
| `equation` / `figure` / `table` / `footnote` | — | — | **预留**给论文与网页场景，第一阶段不产生、遇到时按 `skip` 处理 | 预留 |

`dialogue` 的专属字段（M2 起使用）：

| 字段 | 说明 |
|---|---|
| `speaker` | 选角表中的角色规范名，或 `"unknown"`（按旁白音色朗读，SCR-5） |
| `emotion` | 可选，情绪描述，合成时作为 `instruct`（SCR-12）。取值是自然语言短语而非枚举，如 `"低声、惊恐"` |
| `conf` | 可选，0–1 的归属置信度；低于阈值的进入审阅清单（SCR-11） |

`chapter` 的 `level` 与 M4B 章节名：`level: 2` 的章节名前缀最近的 `level: 1` 标题，如「第一卷 · 第三章 风起」（TXT-3）。只有卷标题、后面紧跟章标题时，卷标题不单独成一个 M4B 章节。

---

## 5. 不变式与对账

以下性质任何时候都可以用 `huashuo check` 检查；合成前自动检查，违反则拒绝合成并指出行号：

| # | 不变式 |
|---|---|
| I1 | 第一行是头记录，且只有一条头记录 |
| I2 | 所有 `id` 唯一 |
| I3 | `src` 区间按文件顺序严格递增、互不重叠 |
| I4 | 每个 block 的 `text` 等于 `text.txt` 对应切片 |
| I5 | **`text.txt` 中每一个非空白字符都落在某个 block 的 `src` 内**（含 `skip`）——这就是「原文每个字都被读到或被明确跳过」（requirements §1.3 第 2 条、SCR-7） |
| I6 | 第一个**可朗读**的 block 是 `chapter`（之前可以有 `skip`，如被跳过的版权页） |
| I7 | `dialogue` 的 `speaker` 在选角表中存在，或为 `"unknown"` |
| I8 | 头记录的 `text_sha256` 与 `text.txt` 一致 |

I4 + I5 合起来保证「原文逐字对得上」。用户想改读法时改 `say` 而不是 `text`，所以这两条不会因为正常编辑而被破坏；如果用户确实改了 `text`，`check` 会指出并给出原文。

---

## 6. 从 block 到合成单元

block 是**语义单位**，合成单元（unit）是**送进 TTS 的一次调用**。两者分开，是因为原型的经验：太短、缺少上下文的文本读出来仓促，而切在段落中间的边界听得出来（`experiments/novel_tts.py` 的 `build_chunks()`）。

**分组规则**（由程序决定，不写进话本）：

1. 相邻、可朗读、**音色与 `emotion` 都相同**的 block 合成一个单元
2. 单元不跨越：`chapter`、`heading`、`break`、`skip`、音色或情绪的变化
3. 单元文字上限默认 400 字（原型的实测值）；超出时在段落边界切，单段超长时在句末、再在分句标点处切
4. `chapter` 与 `heading` 各自单独成一个单元（便于单独控制前后停顿）

**缓存键**：`sha256(格式版本, 引擎, 模型, 音色, instruct, 语言, 实际朗读文字, 采样参数)`。

- 改一个 block，只有它所在的单元失效（SYN-3）
- **随机种子由缓存键派生**，而不是由单元序号派生：在前面插入一段不会改变后面所有单元的种子，缓存照样命中；重跑结果逐字节一致（SYN-5）。重试时种子加上重试次数
- 单元的停顿、响度调整在后期阶段处理，不进入缓存键（改停顿不需要重新合成）

---

## 7. 编辑与重跑（SCR-10）

用户会手改话本，而机器阶段（如重新做说话人标注）也会重写它。规则是**三方合并**，不需要用户做任何标记：

- 基准：`state/script.auto.jsonl`，上一次机器写出的版本
- 当前：`script.huaben.jsonl`，可能被用户改过
- 新结果：这次机器算出的版本

逐 block（按 `id` 匹配）、逐字段：**当前值 ≠ 基准值** → 视为用户修改，保留当前值；否则采用新结果。合并后写回 `script.huaben.jsonl`，并把新结果存为新的基准。

用户新增的 block、删除的 block 同样按「与基准比较」识别并保留。无法对上的修改（例如重新导入后 id 变了）列入报告，不静默丢弃。

---

## 8. 选角表 `cast.json`

```json
{
  "version": 1,
  "narrator": {"voice": "preset:serena"},
  "characters": {
    "林渊": {"aliases": ["林公子"], "gender": "male", "age": "young_adult", "description": "青年剑客，沉稳寡言", "lines": 42, "voice": "library:zh/v5_young_man"},
    "苏晚晴": {"aliases": ["苏姑娘"], "gender": "female", "age": "young_adult", "description": "…", "lines": 31, "voice": "library:zh/v3_young_woman"}
  }
}
```

| 字段 | 说明 |
|---|---|
| `narrator.voice` | 旁白音色；章节标题、开场结尾报幕同样使用（CAST-4） |
| `characters` | 以规范名为键。`aliases`、`gender`、`age`、`description`、`lines` 由剧本化阶段写入（SCR-3） |
| `voice` | 音色引用：`preset:<名称>`（CustomVoice 预置）、`library:<语言>/<音色 id>`（内置音色库，CAST-1）；以后可加 `custom:<路径>`（CAST-12、CAST-13） |

`age` 取值：`child` / `teen` / `young_adult` / `middle_aged` / `elderly`。`gender`：`male` / `female` / `unknown`。

选角表同样走 §7 的三方合并（基准为 `state/cast.auto.json`）：用户改过的 `voice` 不会被自动选角覆盖（CAST-10）。

M1 只使用 `narrator`。

---

## 9. M1 使用的子集

- block 类型：`chapter`、`heading`、`narration`、`skip`、`break`
- 字段：公共字段全部（`say`、`pause_after` 可手写，程序在 M1 不自动生成）
- 选角表：只有 `narrator`
- 三方合并：M1 已经需要——重新导入（改了清洗参数）时保留用户对 `say`、`skip` 的修改

---

## 10. 待定

| # | 问题 | 当前倾向 |
|---|---|---|
| S1 | 单元内多个段落拼接时，段落之间是否插入换行或停顿标记？原型是直接拼接，听感可接受，但段落停顿因此交给模型决定，不受 POST-3 控制 | M1 先沿用原型；试听后若段落停顿不明显，改为「每段一个单元，短段合并到至少 N 字」 |
| S2 | `level` 是否需要第 3 层（部 / 卷 / 章） | 否，第一阶段两层足够 |
| S3 | 是否把 `text.txt` 的内容直接放进头记录，省掉一个文件 | 否：30 万字的单行 JSON 不利于手工查看，`text.txt` 本身也是有用的中间产物 |
