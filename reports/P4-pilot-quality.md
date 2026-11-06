# P4 分类转写与校准报告

更新时间：2026-11-05T19:38:43.115999+00:00

- **固定试点队列（500 组）**：`reports/P3-pilot-manifest.json` 共 500 组独立新增候选（核心早期窗口 441 组、2 月下旬过渡期 59 组）。当前证据快照下 500/500 组已全部到达处理终态：`transcribed_pending_quality` 327 组、`excluded_clear` 153 组、`quarantined_transcription` 20 组（`needs_current_classification=0`、`needs_transcription=0`），详见 [`reports/P3-pilot-processing.json`](file:///Users/myd/newbing/reports/P3-pilot-processing.json) 与 [`reports/P3-P5-pilot-outcomes.json`](file:///Users/myd/newbing/reports/P3-P5-pilot-outcomes.json)。
- **分层校准样本（≥50 组 + ≥10 组否定/疑难样本）**：
  - 跨核验合约版本累计完成 **197** 个独立来源组的原图/原文模型一致性核验；当前冻结合约（`0cf3e645496b7724b4ad05bff2f02f5722263c248cabc7d2262705ba99ad02d8`）下完成 **154** 个独立来源组（verdict 分布：`{"pass": 63, "needs_correction": 84, "uncertain": 7}`）。
  - 固定 50 组分层校准清单保存在 [`reports/P4-calibration-manifest-50.json`](file:///Users/myd/newbing/reports/P4-calibration-manifest-50.json)（按时间桶与 `images`/`direct_text` 分层）。
  - 10 组否定/疑难样本保存在 [`data/review/negative-calibration-reviewed.json`](file:///Users/myd/newbing/data/review/negative-calibration-reviewed.json)（覆盖 19 张独立证据图）。
- **代理视觉复核（Agent Visual Check）与修正闭环**：
  - 针对同模型二次核验可能漏检的自动拼写归一化、标点/符号微差、emoji 替换、UI 尾注残留及角色颠倒，代理对候选导出样本执行了逐图/逐原文复核，累计在 [`data/review/agent-visual-release-checks.json`](file:///Users/myd/newbing/data/review/agent-visual-release-checks.json) 记录 **113** 条检查记录（分布：`{"text_matches_visible_source": 55, "defect_found_corrected_needs_reverification": 3, "unresolved_needs_review": 5, "reject_merged_sessions": 1, "skipped_high_risk_verbatim_code": 1, "reject_role_reversal": 2, "reject_text_error": 27, "reject_ui_artifact_in_text": 4, "reject_context_gap_or_truncation": 6, "reject_emoji_mismatch": 4, "reject_role_error": 1, "reject_incomplete_context": 3, "reject_not_bing_chat": 1}`）。
  - 累计导入并应用 **40** 份可追溯修正补丁（保存在 `data/review/corrections/` 与 `data/review/pending-agent-corrections/`），每次修正均保留原图/原文证据坐标并重跑模型核验；未修补的已知瑕疵样本全部通过 `agent_visual_check_rejected_or_unresolved` 拦截在训练导出之外。
- **审阅角色区分**：同模型证据一致性检查（`model_evidence_consistency_check`）、代理原图/原文视觉检查（`agent_visual_checked`）与真人独立审阅（`human_verified=0`，全部 `pending_human`）严格区分，不将模型/代理复核冒充为真人金标准准确率。
