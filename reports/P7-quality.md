# P7 ShareGPT 质量与最终导出验收报告

更新时间：2026-11-05T19:39:04.483851+00:00

## 1. 最终冻结版本：`final-20261106-v1`

- **样本总数**：**50** 条多轮纯文本对话（累计 **178** 个完整问答回合）
- **独立父会话记录数**：**46**（其中完整无截断原会话 **19** 条，连续多轮派生片段 `segment:*` **31** 条；保守连通划分簇 `split_groups` **40** 个）
- **按可信分层（tier）**：`{"early_strict": 8, "early_probable": 37, "later_supplement": 5}`
  - `early_strict`（8 条）：均具备明确同期发布时间上界证据（`2023-02-07` 至 `2023-02-16` 核心窗口内）、非跨帖转载、无更早资产复用、且为完整无缺口原会话（`derived_from=null`）。
  - `early_probable`（37 条）：早期窗口或过渡期高置信多轮样本，共用相同的文本保真、角色准确与上下文自足门槛。
  - `later_supplement`（5 条）：2023 年 3–4 月及后续补充窗口的高质量多轮样本。
- **按数据集划分（split）**：`{"train": 40, "val": 7, "test": 3}`（通过连通分量分组与实质性 `gpt` 回复 5-gram 重叠检查，跨 split 内容泄漏为 **0**）
- **回合数分布（round_count_distribution）**：`{"2": 20, "3": 15, "4": 7, "5": 2, "6": 1, "7": 1, "8": 1, "10": 1, "12": 2}`（每条均 ≥ 2 轮完整问答，即 ≥ 4 条消息，偶数长度，`human` 开头、`gpt` 结尾、严格交替）
- **来源分布（by_source_prefix）**：`{"hf": 22, "reddit": 26, "reference": 2}`；**转写来源（by_record_origin）**：`{"project_transcription": 44, "upstream_baseline_text": 6}`
- **双重质量门禁**：
  - 同模型当前合约（`0cf3e645496b7724b4ad05bff2f02f5722263c248cabc7d2262705ba99ad02d8`）证据核验：**50 / 50 `pass`**
  - 代理逐图/逐原文视觉复核（`agent_visual_check`）：**50 / 50 `text_matches_visible_source`**（详见 [`reports/P7-final-stratified-sample.json`](file:///Users/myd/newbing/reports/P7-final-stratified-sample.json) 与 [`data/review/agent-visual-release-checks.json`](file:///Users/myd/newbing/data/review/agent-visual-release-checks.json)）
  - 独立真人核验（`human_verified`）：**0 / 50**（全部标记为 `pending_human`，静态对照审阅页位于 [`exports/final-20261106-v1/review/index.html`](file:///Users/myd/newbing/exports/final-20261106-v1/review/index.html) 与 [`data/review/index.html`](file:///Users/myd/newbing/data/review/index.html)）

## 2. 历次冻结 Release 校验汇总

- `final-20261106-v1`: {"release_id": "final-20261106-v1", "status": "provisional_collection_continues", "samples": 50, "validation_passed": true, "by_tier": {"early_strict": 8, "early_probable": 37, "later_supplement": 5}, "by_split": {"train": 40, "val": 7, "test": 3}, "complete_qa_rounds": 178, "round_count_distribution": {"2": 20, "3": 15, "4": 7, "5": 2, "6": 1, "7": 1, "8": 1, "10": 1, "12": 2}, "distinct_parent_record_ids": 46, "derived_fragments": 31, "split_groups": 40, "agent_visual_checked": 50, "human_verified": 0, "validation_scope": "frozen_version_contract", "human_pending": 50, "source_evidence_paths": "Every sample retains message projection map, source/asset identities, original source date evidence, verification reference and split group in provenance.jsonl."}
- `pilot-20261105-v1`: {"release_id": "pilot-20261105-v1", "status": "retracted_pending_ui_boundary_correction", "usable": false}
- `pilot-20261105-v2`: {"release_id": "pilot-20261105-v2", "status": "provisional_collection_continues", "samples": 2, "validation_passed": true, "by_tier": {"early_probable": 2}, "by_split": {"train": 2}, "complete_qa_rounds": null, "round_count_distribution": null, "distinct_parent_record_ids": null, "derived_fragments": null, "split_groups": null, "agent_visual_checked": 0, "human_verified": 0, "validation_scope": "legacy_artifact_checks_only_not_current_admission", "human_pending": 2, "source_evidence_paths": "Every sample retains message projection map, source/asset identities, original source date evidence, verification reference and split group in provenance.jsonl."}
- `pilot-20261105-v3`: {"release_id": "pilot-20261105-v3", "status": "provisional_collection_continues", "samples": 14, "validation_passed": true, "by_tier": {"early_probable": 14}, "by_split": {"train": 14}, "complete_qa_rounds": null, "round_count_distribution": null, "distinct_parent_record_ids": null, "derived_fragments": null, "split_groups": null, "agent_visual_checked": 0, "human_verified": 0, "validation_scope": "frozen_version_contract", "human_pending": 14, "source_evidence_paths": "Every sample retains message projection map, source/asset identities, original source date evidence, verification reference and split group in provenance.jsonl."}
- `pilot-20261105-v4`: {"release_id": "pilot-20261105-v4", "status": "provisional_collection_continues", "samples": 22, "validation_passed": true, "by_tier": {"early_probable": 22}, "by_split": {"train": 19, "test": 2, "val": 1}, "complete_qa_rounds": 86, "round_count_distribution": {"2": 8, "3": 7, "4": 1, "5": 2, "7": 1, "8": 1, "10": 2}, "distinct_parent_record_ids": null, "derived_fragments": 5, "split_groups": 20, "agent_visual_checked": 0, "human_verified": 0, "validation_scope": "frozen_version_contract", "human_pending": 22, "source_evidence_paths": "Every sample retains message projection map, source/asset identities, original source date evidence, verification reference and split group in provenance.jsonl."}

## 3. 排除原因统计（Exclusions）

全量归档会话记录共 **2,055** 条（其中已退役历史版本 `superseded` **566** 条，当前活跃归档记录 **1,489** 条）。除进入 `final-20261106-v1` 的 **50** 条合格样本外，其余 **1,439** 条活跃记录均保留在内部档案层并记录明确排除原因（详见 [`exports/final-20261106-v1/exclusions.jsonl`](file:///Users/myd/newbing/exports/final-20261106-v1/exclusions.jsonl) 与 [`exports/final-20261106-v1/statistics.json`](file:///Users/myd/newbing/exports/final-20261106-v1/statistics.json)）。
