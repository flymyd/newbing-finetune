# New Bing 2023 / Sydney 数据采集项目

状态：**用户已恢复 active goal，按 v1.2 计划继续采集与清洗。** 目录已有历史代码、档案和试验导出；这些文件不代表计划已全部验收，也不构成本轮执行授权。历史阶段记录见 [执行状态](docs/PROGRESS.md)，本次修订与执行前待补事项见 [文档核查](docs/DOCUMENT_AUDIT.md)。

最终目标是整理出 **ShareGPT 格式的纯文本多轮对话微调数据集**。优先收集 2023 年 2 月早期 Sydney，先保存原图与原文、核验转写，再导出 `conversations` / `from` / `value` 格式；角色仅 `human` / `gpt`，每条至少两轮完整问答（4 条消息）。原图、元数据和证据保存在独立档案与 sidecar 中，训练文件只含样本 ID 和对话字符串。本轮范围不包括实际微调、部署或发布数据。

## 审阅入口

1. [执行计划](docs/EXECUTION_PLAN.md)：P0–P8 阶段、产物、验收、扩源顺序与完成条件。
2. [资源与 API](docs/RESOURCES.md)：指定端点、Responses 协议、本地凭据位置与兼容性探测。
3. [数据约定](docs/DATA_CONTRACTS.md)：来源、截图、对话、证据、去重与训练划分。
4. [运行与质量手册](docs/RUNBOOK.md)：恢复、重试、指标、人工审阅与 CLI 实现边界。
5. [执行状态](docs/PROGRESS.md)：下一步、实际完成证据、阻塞与决策记录。
6. [代理工作约定](AGENTS.md)：后续通过 goal 指令执行时必须遵守的规则。
7. [ShareGPT 输出规范](docs/SHAREGPT_EXPORT.md)：最终格式示例、多轮准入、纯文本映射与验收条件。
8. [本次文档核查](docs/DOCUMENT_AUDIT.md)：已修订规则、现有实现缺口和执行前验收清单。
9. [换机交接](docs/HANDOFF.md)：当前数据库快照、证据、恢复命令和下一步顺序。

[config/](config/) 保存运行配置；[prompts/](prompts/) 保存正在校准的分类、转写和核验提示词。使用 `uv sync` 安装锁定依赖、`uv run newbing --help` 查看已实现入口；不在帮助中出现的计划命令仍待实现。

原始调研保留在 [来源研究](research/new-bing-2023-plan.md) 和 [存档核查](research/archive-sources.md)。可执行计划优先于旧调研中的建议；来源可用性必须在实际执行时复测。

## 本地试验产物

- [试验版 v4 的 ShareGPT 文件](exports/pilot-20261105-v4/sharegpt/early_probable/train.json)：共22条 early_probable 多轮样本（train19、val1、test2），链接为train文件；仍为 provisional、待真人审阅。
- [原图/原文对照页](exports/pilot-20261105-v4/review/index.html)。
- [500 组试点清单](reports/P3-pilot-manifest.json) 与 [覆盖检查点](reports/P6-coverage.md)。
- [未完成审计](reports/P0-P8-audit-in-progress.md)：当前并未交付最终完整数据集。

## 后续启动方式

审阅后可使用类似指令：

> goal：按照 docs/EXECUTION_PLAN.md 执行 P0–P8，遵守 AGENTS.md、docs/SHAREGPT_EXPORT.md 和 config/ 中的资源限制，优先收集早期 Sydney，持续更新 docs/PROGRESS.md，交付 ShareGPT 纯文本多轮对话微调数据集及配套证据档案和覆盖率报告。来源不可用时按文档切换路径，不要求每阶段重新确认。

这段文字只是启动指令示例；本次不会创建 goal。自动验收可通过后续阶段连续推进；确需人工判断的疑难样本单独排队，不阻塞其他可完成工作。
