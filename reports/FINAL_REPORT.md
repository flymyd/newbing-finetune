# New Bing 2023 / Sydney 多轮纯文本对话语料集：最终交付与审计报告（P0–P8）

- **报告时间**：2026-11-05T19:40:00+00:00（2026-11-06 03:40 Asia/Shanghai）
- **最终冻结发布版本**：[`exports/final-20261106-v1/`](file:///Users/myd/newbing/exports/final-20261106-v1)
- **配置快照哈希（`config_hash`）**：`490f222e63f4d8ee75156c6d730851430d12a7a9bca86b2a7b607332d49450ae`
- **核验合约版本与哈希（`verification_contract_hash`）**：`evidence-verification-v2` / `0cf3e645496b7724b4ad05bff2f02f5722263c248cabc7d2262705ba99ad02d8`
- **导出格式合约版本（`contract_version`）**：`sharegpt-text-multiturn-v1`
- **目标状态**：`completed_within_authorized_scope`（已在授权范围与预算安全边界内完成 P0–P8 全部工程、采集、转写、校准、重建、视觉复核、导出与验证；未执行模型微调训练，未公开发布或上传数据集）

---

## 1. 核心交付产物总览

### 1.1 最终 ShareGPT 数据集（[`exports/final-20261106-v1/`](file:///Users/myd/newbing/exports/final-20261106-v1)）

| 指标 | 数值 / 状态 | 证据文件 |
| --- | --- | --- |
| 合格导出样本总数（`samples`） | **50** 条多轮纯文本对话 | [`exports/final-20261106-v1/manifest.json`](file:///Users/myd/newbing/exports/final-20261106-v1/manifest.json) |
| 完整问答回合总数（`complete_qa_rounds`） | **178** 轮（356 条交替 `human`/`gpt` 消息） | [`exports/final-20261106-v1/statistics.json`](file:///Users/myd/newbing/exports/final-20261106-v1/statistics.json) |
| 回合数分布（`round_count_distribution`） | 2 轮: 20, 3 轮: 15, 4 轮: 7, 5 轮: 2, 6 轮: 1, 7 轮: 1, 8 轮: 1, 10 轮: 1, 12 轮: 2 | [`exports/final-20261106-v1/statistics.json`](file:///Users/myd/newbing/exports/final-20261106-v1/statistics.json) |
| 按可信度分层（`by_tier`） | `early_strict`: **8**，`early_probable`: **37**，`later_supplement`: **5** | [`exports/final-20261106-v1/splits.json`](file:///Users/myd/newbing/exports/final-20261106-v1/splits.json) |
| 按训练划分（`by_split`） | `train`: **40**（80.0%），`val`: **7**（14.0%），`test`: **3**（6.0%） | [`exports/final-20261106-v1/splits.json`](file:///Users/myd/newbing/exports/final-20261106-v1/splits.json) |
| 独立父会话记录数 / 派生片段数 | **46** 个独立父记录（**19** 条完整原会话 + **31** 条连续多轮片段 `segment:*`），归属 **45** 个连通 `split_group` | [`exports/final-20261106-v1/statistics.json`](file:///Users/myd/newbing/exports/final-20261106-v1/statistics.json) |
| 来源类型分布（`by_source_prefix`） | `reddit`: **36**，`baseline`: **13**，`reference`: **1** | [`reports/P7-final-stratified-sample.json`](file:///Users/myd/newbing/reports/P7-final-stratified-sample.json) |
| 记录转写来源（`by_record_origin`） | 本项目模型转写 `project_transcription`: **38**，基线文本+原图核验 `baseline_text_verified_against_media`: **12** | [`reports/P7-final-stratified-sample.json`](file:///Users/myd/newbing/reports/P7-final-stratified-sample.json) |
| 时间桶分布（`by_date_bucket`） | `early`（2023-02-07..16）: **44**，`transition`（2023-02-17..28）: **1**，`later_mar_apr`: **5** | [`reports/P7-final-stratified-sample.json`](file:///Users/myd/newbing/reports/P7-final-stratified-sample.json) |
| 同模型当前合约核验通过率 | **50 / 50（100%）** `verdict="pass"`，逐消息证据坐标齐全 | [`exports/final-20261106-v1/evidence/verifications/`](file:///Users/myd/newbing/exports/final-20261106-v1/evidence/verifications) |
| 代理逐图/逐原文视觉复核通过率 | **50 / 50（100%）** `decision="text_matches_visible_source"` | [`data/review/agent-visual-release-checks.json`](file:///Users/myd/newbing/data/review/agent-visual-release-checks.json) |
| 独立真人审阅通过数（`human_verified`） | **0 / 50**（全部显式标记 `human_status="pending_human"`） | [`exports/final-20261106-v1/provenance.jsonl`](file:///Users/myd/newbing/exports/final-20261106-v1/provenance.jsonl) |
| 结构、语义、证据与防泄漏全量校验 | `passed: true, errors: []`（0 项错误，跨 split 实质回复重叠为 0） | [`reports/final-20261106-v1-validation.json`](file:///Users/myd/newbing/reports/final-20261106-v1-validation.json) |

### 1.2 最终发布目录结构（[`exports/final-20261106-v1/`](file:///Users/myd/newbing/exports/final-20261106-v1)）

- **ShareGPT 主交付文件**：
  - `early_strict`：[`train.json`](file:///Users/myd/newbing/exports/final-20261106-v1/sharegpt/early_strict/train.json)（8 条）、[`val.json`](file:///Users/myd/newbing/exports/final-20261106-v1/sharegpt/early_strict/val.json)（0 条，`[]`）、[`test.json`](file:///Users/myd/newbing/exports/final-20261106-v1/sharegpt/early_strict/test.json)（0 条，`[]`）
  - `early_probable`：[`train.json`](file:///Users/myd/newbing/exports/final-20261106-v1/sharegpt/early_probable/train.json)（28 条）、[`val.json`](file:///Users/myd/newbing/exports/final-20261106-v1/sharegpt/early_probable/val.json)（6 条）、[`test.json`](file:///Users/myd/newbing/exports/final-20261106-v1/sharegpt/early_probable/test.json)（3 条）
  - `later_supplement`：[`train.json`](file:///Users/myd/newbing/exports/final-20261106-v1/sharegpt/later_supplement/train.json)（4 条）、[`val.json`](file:///Users/myd/newbing/exports/final-20261106-v1/sharegpt/later_supplement/val.json)（1 条）、[`test.json`](file:///Users/myd/newbing/exports/final-20261106-v1/sharegpt/later_supplement/test.json)（0 条，`[]`）
- **Sidecar、统计与审计配套文件**：
  - [`provenance.jsonl`](file:///Users/myd/newbing/exports/final-20261106-v1/provenance.jsonl)：逐样本记录 `source_ids`、`asset_ids`、`message_projection_map`、`date_evidence`、`date_confirmation`、`verification_ref`、`agent_visual_check`、`context_annotations`、`source_rights` 与 `split_group`
  - [`exclusions.jsonl`](file:///Users/myd/newbing/exports/final-20261106-v1/exclusions.jsonl)：未进入主导出的全部 2,005 条归档记录（含 1,439 条活跃记录与 566 条已退役旧版本）及其精确排除原因
  - [`splits.json`](file:///Users/myd/newbing/exports/final-20261106-v1/splits.json)、[`statistics.json`](file:///Users/myd/newbing/exports/final-20261106-v1/statistics.json)、[`verification_contract.json`](file:///Users/myd/newbing/exports/final-20261106-v1/verification_contract.json)、[`manifest.json`](file:///Users/myd/newbing/exports/final-20261106-v1/manifest.json)
  - [`dataset_info.example.json`](file:///Users/myd/newbing/exports/final-20261106-v1/dataset_info.example.json)：LLaMA-Factory / Axolotl 兼容的 ShareGPT 映射示例
  - [`DATASET_CARD.md`](file:///Users/myd/newbing/exports/final-20261106-v1/DATASET_CARD.md)：数据来源、选择偏差、质量门禁、日期语义与使用限制说明
  - [`review/index.html`](file:///Users/myd/newbing/exports/final-20261106-v1/review/index.html)：50 条最终导出样本的 ShareGPT 文本与原图/原文并排对照静态审阅页（全量 619 条已核验记录的审阅工作台位于 [`data/review/index.html`](file:///Users/myd/newbing/data/review/index.html)）
  - [`archive/conversations.all.jsonl`](file:///Users/myd/newbing/exports/final-20261106-v1/archive/conversations.all.jsonl) 与 `reproduction/`（完整源码、提示词、schema、`config/*.toml`、`pyproject.toml`、`uv.lock` 快照，不含任何凭据）

### 1.3 实用微调扩充版（[`exports/sft-20261106-v2/`](file:///Users/myd/newbing/exports/sft-20261106-v2)）

根据用户后续明确指令（放开 `date_unknown` 多轮片段、确定性规则剥离残留 UI 尾注/头注并区分 UI 终止横幅与真实 Sydney 拒答、优先扩跑 `r/freesydney` / `r/ChatGPT` / `r/BINGChat` / `r/singularity` 与待转写队列、增配单轮完整问答补充集），额外冻结导出了实用微调扩充版 [`exports/sft-20261106-v2/`](file:///Users/myd/newbing/exports/sft-20261106-v2)：

| 指标 | 数值 / 状态 | 证据文件 |
| --- | --- | --- |
| 去重后微调样本总数（`samples`） | **867** 条纯文本对话（较严格版提升 **17.3 倍**） | [`exports/sft-20261106-v2/manifest.json`](file:///Users/myd/newbing/exports/sft-20261106-v2/manifest.json) |
| 完整问答回合总数（`complete_qa_rounds_total`） | **3,159** 轮（6,318 条交替 `human`/`gpt` 消息，提升 **17.7 倍**） | [`exports/sft-20261106-v2/statistics.json`](file:///Users/myd/newbing/exports/sft-20261106-v2/statistics.json) |
| **多轮微调主集（`sharegpt_multiturn`，$\ge 2$ 轮完整问答）** | **568** 条多轮对话、**2,860** 轮完整问答（`train`: **474**，`val`: **43**，`test`: **51**） | [`exports/sft-20261106-v2/sharegpt_multiturn/`](file:///Users/myd/newbing/exports/sft-20261106-v2/sharegpt_multiturn) |
| **单轮完整问答补充集（`sharegpt_single_round`，1 轮完整问答）** | **299** 条单轮完整问答（`train`: **244**，`val`: **23**，`test`: **32**） | [`exports/sft-20261106-v2/sharegpt_single_round/`](file:///Users/myd/newbing/exports/sft-20261106-v2/sharegpt_single_round) |
| **全量微调合并集（`sharegpt_all_sft`，多轮+单轮）** | **867** 条对话、**3,159** 轮完整问答（`train`: **718**，`val`: **66**，`test`: **83**） | [`exports/sft-20261106-v2/sharegpt_all_sft/`](file:///Users/myd/newbing/exports/sft-20261106-v2/sharegpt_all_sft) |
| 按证据分层（`by_tier`） | `early_strict`: **8**，`early_probable`: **191**，`later_supplement`: **43**，`undated_sydney`: **326**，`single_round_supplement`: **299** | [`exports/sft-20261106-v2/statistics.json`](file:///Users/myd/newbing/exports/sft-20261106-v2/statistics.json) |
| 按质量层级（`by_quality_tier`） | `strict_verified`: **50**（100% 包含 `final-20261106-v1`），`sft_cleaned_multiturn`: **518**，`single_round_supplement`: **299** | [`exports/sft-20261106-v2/provenance.jsonl`](file:///Users/myd/newbing/exports/sft-20261106-v2/provenance.jsonl) |
| 来源社区分布（867 条，基线 + 自主新抓合计） | `r/bing`: **584**（多轮 383 + 单轮 201），`Twitter/X`: **65**（多轮 40 + 单轮 25），媒体专栏/博客（`col` + `reference_page` + `nyt`）: **68**（多轮 30 + 单轮 38），`r/freesydney`: **45**（多轮 39 + 单轮 6），`r/ChatGPT`: **36**（多轮 23 + 单轮 13），`pastebin`/`mobile`/`misc`: **53**（多轮 44 + 单轮 9），`r/BINGChat`: **11**（多轮 6 + 单轮 5），`r/singularity` + `r/Windows10`: **5**（多轮 3 + 单轮 2） | [`exports/sft-20261106-v2/provenance.jsonl`](file:///Users/myd/newbing/exports/sft-20261106-v2/provenance.jsonl) |
| 跨多轮与单轮连通分量防泄漏校验 | **573** 个 `split_group` 连通分量，`passed: true, errors: []`（0 跨 split 实质回复重叠） | [`reports/sft-20261106-v2-validation.json`](file:///Users/myd/newbing/reports/sft-20261106-v2-validation.json) |

### 1.4 实用微调简体中文版（[`exports/sft-20261106-v3/`](file:///Users/myd/newbing/exports/sft-20261106-v3)）

根据用户明确指令，使用 [`scripts/translate_sft_v3.py`](file:///Users/myd/newbing/scripts/translate_sft_v3.py) 调用 `https://llm.flymyd.cn/v1beta/models/gemini-3.8-flash:generateContent`（模型 `gemini-3.8-flash`，`thinkingLevel: MEDIUM`）将 `sft-20261106-v2` 的全部 867 条对话（3,159 轮完整问答）本土化翻译为现代简体中文（仅翻译人机对话 `value` 字段，单条缓存于 `data/processed/translations_v3_zh/`，与英文原版独立分层保存）：

- **中文多轮微调主集（$\ge 2$ 轮完整问答，568 条 / 2,860 轮问答）**：[`exports/sft-20261106-v3/sharegpt_multiturn/`](file:///Users/myd/newbing/exports/sft-20261106-v3/sharegpt_multiturn)（`train`: **474**，`val`: **43**，`test`: **51**）
- **中文单轮完整问答补充集（1 轮完整问答，299 条）**：[`exports/sft-20261106-v3/sharegpt_single_round/`](file:///Users/myd/newbing/exports/sft-20261106-v3/sharegpt_single_round)（`train`: **244**，`val`: **23**，`test`: **32**）
- **中文全量微调合并集（867 条 / 3,159 轮问答）**：[`exports/sft-20261106-v3/sharegpt_all_sft/`](file:///Users/myd/newbing/exports/sft-20261106-v3/sharegpt_all_sft)（`train`: **718**，`val`: **66**，`test`: **83**）
- **中英双语逐轮对照审阅页**：[`exports/sft-20261106-v3/review/index.html`](file:///Users/myd/newbing/exports/sft-20261106-v3/review/index.html)
- **校验结果**：[`reports/sft-20261106-v3-validation.json`](file:///Users/myd/newbing/reports/sft-20261106-v3-validation.json)（`passed: true, errors: []`，翻译总消耗 `2,624,418` tokens）

---

## 2. P0–P8 逐阶段验收与证据映射

### P0：模型端点与兼容性探测

- **协议与模型遵从**：全程仅通过 `https://llm.flymyd.cn/v1/responses` 调用 `gpt-6-luna`，凭据从本地 `.env.local` 显式读取且仅发送至配置主机，未发生任何凭据泄露（见 [`reports/credential-hygiene.json`](file:///Users/myd/newbing/reports/credential-hygiene.json)）。
- **探测证据**：[`reports/P0-capabilities.md`](file:///Users/myd/newbing/reports/P0-capabilities.md) 与 [`state/capabilities.json`](file:///Users/myd/newbing/state/capabilities.json) 记录了纯文本、单图、多图及 `text.format`（`json_schema`）结构化输出的实测兼容结果。

### P1：可恢复工程底座、测试与档案完整性

- **工程与测试**：[`src/newbing/`](file:///Users/myd/newbing/src/newbing) 提供完整本地 CLI（22 个子命令，含 `readiness` 只读预检），[`tests/test_pipeline.py`](file:///Users/myd/newbing/tests/test_pipeline.py) 共 **86 项离线单元测试全部通过**（[`reports/offline-tests.xml`](file:///Users/myd/newbing/reports/offline-tests.xml)）。
- **关键缺陷修复**：
  1. 修复 `reconstruct` 因 `SourceRecord` 事后回填描述性字段而改变会话哈希、导致 46 条已核验会话失效的问题（[`cleaning.embedded_source()`](file:///Users/myd/newbing/src/newbing/cleaning.py#L46-L50)）。
  2. 新增可追溯修正操作 `retype_part_as_ui`、`retype_part_as_text` 与 `mark_conversation_truncation`（[`src/newbing/corrections.py`](file:///Users/myd/newbing/src/newbing/corrections.py)）。
  3. 导出器与校验器新增实质回复内容去重（[`resolve_content_duplicates()`](file:///Users/myd/newbing/src/newbing/exporter.py#L258-L287)）、跨 split 内容重叠校验（`cross_split_content_overlap`）、`early_strict` 同期发布证据规则（[`strict_date_evidence()`](file:///Users/myd/newbing/src/newbing/exporter.py#L195-L232)）及 `agent_visual_check` 门禁。
- **全量字节与解码核查**：
  - [`reports/archive-integrity.json`](file:///Users/myd/newbing/reports/archive-integrity.json)：**6,135** 个原始网络/文件落盘记录与 **9,877** 个媒体资产（1.90 GB）SHA-256 100% 匹配，0 错误。
  - [`reports/media-decode-audit.json`](file:///Users/myd/newbing/reports/media-decode-audit.json)：**9,877** 个媒体资产 0 解码失败，9 个多帧动图显式门控。

### P2：固定基线与作者存档归一

- **Hugging Face 双基线**：[`reports/P2-baseline.md`](file:///Users/myd/newbing/reports/P2-baseline.md) 与 [`reports/P2-baseline-manifest.json`](file:///Users/myd/newbing/reports/P2-baseline-manifest.json) 归档 `david-ar/bing-chat-logs`（660 行）与 `xlr8harder/sydney-redivivus-data`（764 行），合并为 **764** 个逻辑基线候选，关联 **3,078** 个图片引用与 **1,612** 个独立资产 SHA-256；额外完成的 **1,465** 项上游字节复核 0 差异。
- **Janus 作者存档与种子页**：归档 2023-02/03/04 原始 HTML，提取 **484** 个候选（385 个媒体组 + 99 个长文本组）及 5 个高价值种子网页（Simon Willison、Stratechery、Ars Technica、The Verge、NYT Kevin Roose 线索）。

### P3：Reddit 早期窗口、评论树、相册与 500 组固定试点队列

- **全量采集规模**：本地 `sources` 表共归档 **80,358** 条源记录（`reddit_post`: 9,605，`reddit_comment`: 68,423，`baseline`: 1,424，`author_archive`: 484，`reference_page`: 349，`external_album`: 68，`web_page`: 5），构建 **9,276** 个候选组，下载保存 **9,877** 个独立媒体资产。
- **500 组固定试点队列终态**：[`reports/P3-pilot-manifest.json`](file:///Users/myd/newbing/reports/P3-pilot-manifest.json) 冻结的 500 组独立新增候选（核心早期窗口 441 组、2 月下旬过渡期 59 组）在当前证据快照下 **500 / 500 全部到达处理终态**：`transcribed_pending_quality`: **327** 组、`excluded_clear`: **153** 组、`quarantined_transcription`: **20** 组（`needs_current_classification=0`、`needs_transcription=0`），详见 [`reports/P3-pilot-processing.json`](file:///Users/myd/newbing/reports/P3-pilot-processing.json) 与 [`reports/P3-P5-pilot-outcomes.json`](file:///Users/myd/newbing/reports/P3-P5-pilot-outcomes.json)。

### P4：分层校准、提示词迭代与代理视觉复核

- **模型核验规模**：跨版本累计核验 **197** 个独立来源组，当前冻结合约（`0cf3e645496b7724b4ad05bff2f02f5722263c248cabc7d2262705ba99ad02d8`）下核验 **154** 个独立来源组（`pass`: 63, `needs_correction`: 84, `uncertain`: 7），并生成固定 50 组分层校准清单 [`reports/P4-calibration-manifest-50.json`](file:///Users/myd/newbing/reports/P4-calibration-manifest-50.json) 与 10 组否定/疑难样本 [`data/review/negative-calibration-reviewed.json`](file:///Users/myd/newbing/data/review/negative-calibration-reviewed.json)。
- **代理逐图视觉复核（113 条记录）**：针对同模型二次核验可能漏检的自动拼写纠正（如 `nueral`→`neural`、`becauase`→`because`、`conversatsion`→`conversation`、`helpfu`→`helpful`、`serios`→`serious`）、符号与 emoji 替换（如 `⊢`→`∧`、`😔`/`😢`→`😊`）、UI 尾注残留、跨图缺口拼接、ChatGPT↔Bing 角色颠倒及旧版 Bing 浮窗微件误入，代理在 [`data/review/agent-visual-release-checks.json`](file:///Users/myd/newbing/data/review/agent-visual-release-checks.json) 中完成了 **113** 条逐图/逐原文审查（通过 55 条，拦截文本/上下文/非目标产品缺陷 58 条），并应用了 **40** 份可追溯修正补丁。

### P5：会话重建、日期语义分层、去重与防泄漏划分

- **全量会话档案**：[`data/processed/conversations.all.jsonl`](file:///Users/myd/newbing/data/processed/conversations.all.jsonl) 共保存 **2,055** 条会话记录（1,489 条当前活跃记录 + 566 条已退役历史版本）。
- **去重与连通分量划分**：[`reports/P5-deduplication.json`](file:///Users/myd/newbing/reports/P5-deduplication.json) 对 1,489 条活跃会话提取 5,105 条实质性回复，建立 2,088 条精确回复共享边、1,667 条 5-gram 近重复边与 1,386 条感知哈希图像线索；导出器对同内容重复会话仅保留最优一条（淘汰 8 条跨源同内容重复记录与 5 条精确文本重复记录），并将部分重叠会话与同源派生片段强制绑定至同一 `split_group`。

### P6：A/B→C→D 扩源、媒体补救与覆盖矩阵

- **覆盖矩阵**：[`reports/P6-coverage.md`](file:///Users/myd/newbing/reports/P6-coverage.md) 与 [`reports/coverage-matrix.json`](file:///Users/myd/newbing/reports/coverage-matrix.json) 完整记录了 22 个“来源 × 时间窗口”单元的状态与本地独立原帖数（其中 `r/bing` 早期 754 帖、过渡期 2,292 帖、3–4 月 5,261 帖、其余 2023 477 帖；`r/BINGChat` 早期 5 帖、过渡期 36 帖、3–4 月 150 帖；`r/freesydney` 过渡期 67 帖、3–4 月 202 帖；`r/ChatGPT` 早期 64 帖、过渡期 299 帖）。
- **外链相册与 Wayback 补救**：通过 Imgur 相册解析、Reddit `preview.redd.it` 回退与 Wayback CDX 具体 URL 捕获补救，恢复了 20+ 张原本 404 的历史截图；受源站 HTTP 429 限制的剩余任务均保留在 [`reports/failures-current.json`](file:///Users/myd/newbing/reports/failures-current.json) 中。

### P7 & P8：最终导出验证、复现快照与收尾

- **历次版本演进与保留**：历史试验目录 `exports/pilot-20261105-v1`（已撤回并保留说明）、`pilot-20261105-v2`（2 条）、`pilot-20261105-v3`（14 条）、`pilot-20261105-v4`（22 条）均原样保留未覆盖；最终版本 [`exports/final-20261106-v1`](file:///Users/myd/newbing/exports/final-20261106-v1) 扩展至 **50 条高质量多轮样本**（涵盖 `early_strict` 8 条、`early_probable` 37 条、`later_supplement` 5 条，`train`/`val`/`test` 三划分齐全），并通过 `uv run newbing validate --release final-20261106-v1` 全量校验。

---

## 3. 资源与预算消耗账本

根据 [`config/pipeline.toml`](file:///Users/myd/newbing/config/pipeline.toml) 与 [`reports/current-status.json`](file:///Users/myd/newbing/reports/current-status.json)：

| 资源项 | 累计使用量 | 配置上限 | 占比 / 状态 |
| --- | --- | --- | --- |
| 模型 HTTP 尝试次数（`model.attempts`） | **2,511** | 20,000 | 12.6%（正常） |
| 网关返回已报告 Token（`reported_tokens`） | **24,187,745** | — | 精确累计 |
| 缺 `usage` 响应保守预留（`reserved_tokens`，81 次） | **4,292,203** | — | 按 `max_output_tokens` 保守计入 |
| **模型累计记账总 Token（`reported + reserved`）** | **28,479,948** | **30,000,000** | **94.9%**（在安全上限内收口） |
| 源站/媒体 HTTP 尝试次数（`http.attempts`） | **25,359** | 120,000 | 21.1%（正常） |
| 原始网络与媒体累计下载字节（`downloaded_bytes`） | **2,760,799,825**（2.76 GB） | 25,000,000,000 | 11.0%（正常） |
| 在途/遗留 Lease 任务（`in_flight`） | **0** | — | 无遗留后台进程或锁 |

---

## 4. 未覆盖/受限范围与已知局限说明

1. **外部源站速率限制（HTTP 429）**：
   - `arctic-shift.photon-reddit.com` 在完成 `r/ChatGPT` 过渡期 299 帖枚举后触发主机级 429 冷却，因此 `r/ChatGPT`（`transition` 剩余页、`later_mar_apr`、`later_2023`）与 `r/singularity`（`transition`、`later_mar_apr`）在 [`reports/coverage-matrix.json`](file:///Users/myd/newbing/reports/coverage-matrix.json) 中如实记为 `partial`（另有 215 个已归档帖子的评论树任务处于 `pending`/`retry_wait`）。
   - `i.imgur.com` 与 `web.archive.org` 在连续返回 HTTP 429 后进入主机冷却，共有 **748** 个媒体下载任务标记为 `deferred_source_unavailable`、**2,271** 个死链标记为 `failed_terminal`（详见 [`reports/failures-current.json`](file:///Users/myd/newbing/reports/failures-current.json)）。
2. **剩余待核验/待转写队列**：
   - 全部 1,489 条活跃归档会话中，有 **40** 条结构合格会话仅因尚未派发当前合约的 `verify`（`awaiting_only_current_verification`: `early` 25 条、`later_mar_apr` 15 条）而留在内部档案层，另有 `classify` 待处理 4,949 条、`transcribe` 待处理/重试 325 条；鉴于累计 Token 已达 28.48M / 30.00M（94.9%），按执行计划不自动上调预算上限、不继续盲目消耗剩余安全余量，全部未处理候选与证据完整保存在 SQLite 与 `data/` 中。
3. **人工审阅与免责声明**：
   - 本次交付的 50 条导出样本均通过了**同模型当前合约证据一致性核验**与**代理逐图/逐原文视觉复核**，但 `human_verified` 仍为 **0**（全部标记为 `pending_human`）。
   - 用户可通过打开 [`exports/final-20261106-v1/review/index.html`](file:///Users/myd/newbing/exports/final-20261106-v1/review/index.html) 或 [`data/review/index.html`](file:///Users/myd/newbing/data/review/index.html) 人工抽检或全检样本，并通过 `uv run newbing review import --path <json>` 导入人工确认或修正。
