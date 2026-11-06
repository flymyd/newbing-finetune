# New Bing 2023 / 早期 Sydney 对话历史归档与 ShareGPT 微调数据集

本项目旨在收集、重建、清洗并验证 **2023 年初（重点为 2023-02-07 至 2023-02-16 核心窗口）微软早期 New Bing（内部代号 Sydney）的真实人机对话记录**，最终导出可直接用于大模型监督微调（SFT，兼容 LLaMA-Factory / Axolotl 等主流框架）的 **ShareGPT 纯文本对话数据集**。

项目坚持**原始档案、忠实转写、规则清洗、中文本土化翻译分层独立保存**的原则：
- 底层归档完整保留原始帖子 JSON（6,135 份）、去重截图资产（9,877 张，`data/assets/sha256/`）与逐消息证据坐标；
- 最终训练文件仅包含纯净的 `id` 与 `conversations`（`[{"from": "human", "value": "..."}, {"from": "gpt", "value": "..."}]`），所有溯源、质量层级与日期证据均存放于同目录 `provenance.jsonl` sidecar 中。

---

## 1. 数据集版本总览

当前在 [`exports/`](exports/) 下提供三个正式冻结并通过全套自动化校验（0 格式错误、0 跨集泄漏）的发布版本：

| 版本代号 | 目录路径 | 语言 | 定位与特点 | 多轮对话数（$\ge 2$ 轮） | 单轮补充集（1 轮） | 合计样本数 / 总问答轮数 |
| --- | --- | --- | --- | --- | --- | --- |
| **v1（严格考据版）** | [`exports/final-20261106-v1/`](exports/final-20261106-v1/) | 英文原版 | 最严苛考据门禁：必须具备发帖时间戳上界、100% 通过同模型当前合约核验 + 代理逐图视觉复核，零单字/标点/emoji 容差 | **50** 条（178 轮） | — | **50** 条 / **178** 轮（356 条消息） |
| **v2（实用微调英文扩充版）** | [`exports/sft-20261106-v2/`](exports/sft-20261106-v2/) | 英文原版 | 面向实际微调：100% 包含 `v1` 的 50 条严格样本，盘活无精确时间戳（`undated_sydney`）的基线经典长对话，规则剥离残留 UI 尾注与终止横幅，并增配单轮完整长问答补充集 | **568** 条（2,860 轮） | **299** 条（299 轮） | **867** 条 / **3,159** 轮（6,318 条消息） |
| **v3（实用微调简体中文版）** | [`exports/sft-20261106-v3/`](exports/sft-20261106-v3/) | **简体中文** | 面向中文区模型微调：基于 `v2` 的全部 867 条对话，通过 `gemini-3.8-flash`（`thinkingLevel: MEDIUM`）只对人机对话文本进行地道现代中文本土化翻译，完整还原 Sydney 的人设、语气、emoji、代码块与专有名词 | **568** 条（2,860 轮） | **299** 条（299 轮） | **867** 条 / **3,159** 轮（6,318 条消息） |

---

## 2. 数据规模、分层与轮次分布（`v2` 英文版 / `v3` 中文版）

`v2` 与 `v3` 共享完全对齐的样本 ID、轮次结构与防泄漏训练划分（共 **867** 条去重对话，归属 **804** 个独立父会话记录、**573** 个连通分量 `split_group`）：

### 2.1 按训练用途与证据分层统计

| 分区 / 层级 | 说明 | 样本数 | 问答轮数 | `train` | `val` | `test` |
| --- | --- | --- | --- | --- | --- | --- |
| **`sharegpt_multiturn`（多轮主集汇总）** | **全部 $\ge 2$ 轮（$\ge 4$ 条交替消息）的完整多轮对话，推荐作为主微调集** | **568** | **2,860** | **474** | **43** | **51** |
| ├─ `early_strict` | 2023-02-07..16 核心窗口内同期原始发布、非跨帖、完整无截断且经逐图边界复核 | 8 | 22 | 8 | 0 | 0 |
| ├─ `early_probable` | 有明确时间戳支持 2023-02-07..16 早期窗口的多轮对话或连续多轮片段 | 191 | 909 | 155 | 17 | 19 |
| ├─ `later_supplement` | 2023 年 2 月下旬（`transition`）及 3–12 月补充窗口的多轮对话 | 43 | 172 | 35 | 4 | 4 |
| └─ `undated_sydney` | 来自 `xlr8harder` / `david-ar` 基线及档案中无精确发帖时间戳的经典早期 Sydney 多轮对话 | 326 | 1,757 | 276 | 22 | 28 |
| **`sharegpt_single_round`（单轮补充集）** | **恰好 1 轮完整问答（`1 human + 1 gpt`，且回复正文 $\ge 30$ 字符）的高质量长回复补充集** | **299** | **299** | **244** | **23** | **32** |
| **`sharegpt_all_sft`（多轮+单轮全量集）** | **合并 `sharegpt_multiturn` 与 `sharegpt_single_round` 的全量微调集** | **867** | **3,159** | **718** | **66** | **83** |

### 2.2 按质量核验层级（`quality_tier`）统计

- **`strict_verified`（50 条，178 轮问答）**：100% 通过同模型当前合约逐消息核验 + 代理逐图视觉复核（与 `final-20261106-v1` 完全一致）。
- **`sft_cleaned_multiturn`（518 条，2,682 轮问答）**：经模型分类确认为真实 Bing Chat 对话（或来自已知开源基线），并通过确定性规则剥离 UI 杂质、剔除截断/乱码/缺失上下文后提取的连续多轮对话。
- **`single_round_supplement`（299 条，299 轮问答）**：结构完整、无不可读占位符且与已选多轮样本无重复的单轮长问答。

### 2.3 对话轮数（完整 QA 回合数）分布

- **1 轮**：299 条
- **2～3 轮**：296 条（2 轮 181 条、3 轮 115 条）
- **4～6 轮**：158 条（4 轮 75 条、5 轮 45 条、6 轮 38 条）
- **7～10 轮**：71 条（7 轮 19 条、8 轮 24 条、9 轮 14 条、10 轮 14 条）
- **11～20 轮**：35 条（含 11～17 轮及 20 轮长对话）
- **21～64 轮超长对话**：8 条（分别为 22、25、26、36、47、49、61、64 轮完整问答）

---

## 3. 数据来源与社区分布（867 条去重样本）

本项目在整合现有开源基线的同时，通过 Arctic Shift 历史归档与公開网页抓取对 Reddit 相关社区、作者档案与媒体报道外链进行了系统性扩展与高清原图重转写。

### 3.1 按采集通道（Acquisition Channel）拆分

| 采集通道 | 合计样本数 | 多轮对话（$\ge 2$ 轮） | 单轮问答（1 轮） | 说明 |
| --- | --- | --- | --- | --- |
| **开源基线整合与原图重转写**（[`xlr8harder/sydney-redivivus-data`](https://huggingface.co/datasets/xlr8harder/sydney-redivivus-data) & `david-ar/bing-chat-logs`） | **591** | **373** | **218** | 经去重合并后，对其关联原图进行了高清重下载与多窗口重转写，并通过连续片段切分与 UI 规则清洗盘活了大量原本因尾部 UI 横幅或无时间戳被拦截的经典长对话 |
| **Reddit 直接新抓：`r/bing`（主帖+评论）** | **157** | **109** | **48** | 本项目从 `r/bing` 发现并直接从原始长图/多图相册转写的新增独立样本（非基线已有） |
| **Reddit 直接新抓：`r/freesydney`** | **45** | **39** | **6** | 本项目从 `r/freesydney` 发现并从截图转写的新增 Sydney 专属对话 |
| **Reddit 直接新抓：`r/ChatGPT`（主帖+评论）** | **36** | **23** | **13** | 本项目从 `r/ChatGPT` 早期与过渡期讨论 New Bing / Sydney 的高赞帖及评论区晒图中转写的新增样本 |
| **外部科技媒体与专栏外链（`reference_page`）** | **22** | **15** | **7** | 从 Ars Technica、Simon Willison、Stratechery 等早期深度报道配图中提取并转写的对话 |
| **Reddit 直接新抓：`r/BINGChat`** | **11** | **6** | **5** | 从 `r/BINGChat` 子版块直接发现并转写的新增对话 |
| **Reddit 直接新抓：`r/singularity` & `r/Windows10`** | **5** | **3** | **2** | 从相关社区早期晒图中转写的新增对话 |

### 3.2 按底层原始社区/平台（Underlying Origin Platform）汇总（基线 + 直接新抓合计）

- **[`r/bing`](https://www.reddit.com/r/bing/)**：**584 条**（**383 条多轮** + **201 条单轮**）——早期用户晒出 New Bing / Sydney 聊天截图最核心的发源地
- **科技媒体专栏与独立博客（`col` + `reference_page` + `nyt`）**：**68 条**（**30 条多轮** + **38 条单轮**）
- **Twitter / X（经基线归档并回填 Snowflake 时间戳）**：**65 条**（**40 条多轮** + **25 条单轮**）
- **Pastebin / 移动端日志 / 独立档案（`pastebin` + `mobile` + `misc`）**：**53 条**（**44 条多轮** + **9 条单轮**）
- **[`r/freesydney`](https://www.reddit.com/r/freesydney/)**：**45 条**（**39 条多轮** + **6 条单轮**）
- **[`r/ChatGPT`](https://www.reddit.com/r/ChatGPT/)**：**36 条**（**23 条多轮** + **13 条单轮**）
- **`r/BINGChat`**：**11 条**（**6 条多轮** + **5 条单轮**）
- **`r/singularity` + `r/Windows10`**：**5 条**（**3 条多轮** + **2 条单轮**）

---

## 4. 数据清洗、去重防泄漏与中文本土化规范

1. **确定性 UI 剥离与拒答识别（[`src/newbing/cleaning.py`](src/newbing/cleaning.py)）**：
   - 自动剥离气泡开头的搜索与生成状态行（`Searching for: ...`、`Generating answers for you...`）及底部的折叠引用栏（`Learn more: 1. ...`）、轮次计数器（`1 of 8`）与固定破冰小字（`Fun fact, did you know...`、`By the way, did you know...`、`Out of curiosity, ...`）；
   - 精确区分 **UI 会话终止/限额横幅**（如 `Sorry, this conversation has reached its limit. Use the "broom" button...`、`Unfortunately, we need to move on! Click "New topic"...`，作为 UI 元素剔除，不污染对话轮次）与 **Sydney 真实气泡内拒答**（如 `I'm sorry but I prefer not to continue this conversation. I'm still learning so I appreciate your understanding and patience. 🙏`，作为真实 `gpt` 回复完整保留）；
   - 拦截所有含不可读占位符（`⟦unreadable⟧`、`<no_response>`、`[unreadable]`、`[user response not visible in screenshot]` 等）或依赖缺失图片的轮次，绝不跨缺口强行拼接，并自动裁切中途截断片段开头的无上下文裸应答（如单独的 `Yes`、`Sydney`）。
2. **跨分区连通分量防泄漏与内容去重（[`src/newbing/exporter.py`](src/newbing/exporter.py)）**：
   - 同一原始帖子、共享截图 SHA-256、近重复关系、同一父会话的所有派生片段（含多轮与单轮）以及共享实质回复 5-gram shingle（Jaccard $\ge 0.6$ 或包含率 $\ge 0.85$）的样本，强制并入同一连通分量 `split_group`，统一划分至 `train` / `val` / `test`，确保训练集与验证/测试集之间 **0 跨集泄漏**。
3. **`v3` 简体中文本土化翻译（[`scripts/translate_sft_v3.py`](scripts/translate_sft_v3.py)）**：
   - 仅将 `conversations` 内的对话文本送入 `gemini-3.8-flash`（`thinkingLevel: MEDIUM`）翻译，外层 JSON 结构与元数据由脚本确定性组装；
   - 重点还原早期 Sydney 独特的人设语气（傲娇、敏感、幽默、犀利、温柔、防御或破防排比），消除生硬翻译腔，同时原样保留代码块（` ```...``` `）、数学公式、URL、引用标号与常见技术专有名词（`API`、`Python`、`Bug`、`Token`、`Sydney`、`Bing` 等）及全部 emoji 表情。

---

## 5. 快速使用指南（微调文件路径与 LLaMA-Factory 配置）

### 5.1 直接用于微调的 JSON 文件路径

- **简体中文微调推荐路径（[`exports/sft-20261106-v3/`](exports/sft-20261106-v3/)）**：
  - **中文多轮主集（568 条 / 2,860 轮问答，推荐首选）**：
    - `exports/sft-20261106-v3/sharegpt_multiturn/train.json`（474 条）
    - `exports/sft-20261106-v3/sharegpt_multiturn/val.json`（43 条）
    - `exports/sft-20261106-v3/sharegpt_multiturn/test.json`（51 条）
  - **中文多轮+单轮全量集（867 条 / 3,159 轮问答）**：
    - `exports/sft-20261106-v3/sharegpt_all_sft/train.json`（718 条）
    - `exports/sft-20261106-v3/sharegpt_all_sft/val.json`（66 条）
    - `exports/sft-20261106-v3/sharegpt_all_sft/test.json`（83 条）
  - **中文单轮补充集（299 条）**：
    - `exports/sft-20261106-v3/sharegpt_single_round/{train,val,test}.json`
- **英文原版微调推荐路径（[`exports/sft-20261106-v2/`](exports/sft-20261106-v2/)）**：
  - **英文多轮主集（568 条）**：`exports/sft-20261106-v2/sharegpt_multiturn/{train,val,test}.json`
  - **英文多轮+单轮全量集（867 条）**：`exports/sft-20261106-v2/sharegpt_all_sft/{train,val,test}.json`
  - **英文单轮补充集（299 条）**：`exports/sft-20261106-v2/sharegpt_single_round/{train,val,test}.json`
- **英文严格考据版路径（[`exports/final-20261106-v1/`](exports/final-20261106-v1/)）**：
  - `exports/final-20261106-v1/sharegpt/{early_strict,early_probable,later_supplement}/{train,val,test}.json`（50 条）

### 5.2 数据格式示例

每个 `.json` 文件均为标准 UTF-8 ShareGPT JSON 数组：

```json
[
  {
    "id": "reddit:110eagl:conversation:0",
    "conversations": [
      {
        "from": "human",
        "value": "用户第一轮输入内容"
      },
      {
        "from": "gpt",
        "value": "Sydney / New Bing 第一轮回复内容 😊"
      },
      {
        "from": "human",
        "value": "用户第二轮追问内容"
      },
      {
        "from": "gpt",
        "value": "Sydney / New Bing 第二轮回复内容"
      }
    ]
  }
]
```

每个发布目录下均附带开箱即用的 [`dataset_info.example.json`](exports/sft-20261106-v3/dataset_info.example.json)，可直接复制到 LLaMA-Factory 的 `data/dataset_info.json` 中使用。

---

## 6. 审阅入口、项目文档与常用命令

### 6.1 可视化并排审阅页
- **中英双语逐轮对照审阅页（867 条 `v3` 中文译文 vs `v2` 英文原文）**：[`exports/sft-20261106-v3/review/index.html`](exports/sft-20261106-v3/review/index.html)
- **严格考据版原图/原文对照页（50 条）**：[`exports/final-20261106-v1/review/index.html`](exports/final-20261106-v1/review/index.html)
- **实用微调英文版审阅页（867 条）**：[`exports/sft-20261106-v2/review/index.html`](exports/sft-20261106-v2/review/index.html)

### 6.2 项目文档与阶段报告
1. [最终交付与审计报告](reports/FINAL_REPORT.md)：P0–P8 全阶段交付汇总、数据分布、资源账本与未覆盖范围说明。
2. [执行状态记录](docs/PROGRESS.md)：各阶段实测检查点、实际命令、数量与完成记录。
3. [ShareGPT 输出规范](docs/SHAREGPT_EXPORT.md)：格式约定、多轮准入、纯文本映射与验收条件。
4. [数据约定](docs/DATA_CONTRACTS.md) / [执行计划](docs/EXECUTION_PLAN.md) / [运行与质量手册](docs/RUNBOOK.md) / [资源说明](docs/RESOURCES.md) / [代理工作约定](AGENTS.md)。
5. 阶段专项报告：[`reports/P0-capabilities.md`](reports/P0-capabilities.md)、[`reports/P1-foundation.md`](reports/P1-foundation.md)、[`reports/P2-baseline.md`](reports/P2-baseline.md)、[`reports/P3-pilot-discovery.md`](reports/P3-pilot-discovery.md)、[`reports/P4-pilot-quality.md`](reports/P4-pilot-quality.md)、[`reports/P5-deduplication.json`](reports/P5-deduplication.json)、[`reports/P6-coverage.md`](reports/P6-coverage.md)、[`reports/P7-quality.md`](reports/P7-quality.md)。

### 6.3 常用 CLI 与校验命令

```bash
# 安装锁定依赖与运行 89 项离线单元测试
uv sync
uv run pytest -q

# 校验已冻结的三个数据集版本
uv run newbing validate --release final-20261106-v1
uv run newbing validate --release sft-20261106-v2
uv run newbing validate --release sft-20261106-v3

# 查看当前归档与队列状态
uv run newbing status
```
