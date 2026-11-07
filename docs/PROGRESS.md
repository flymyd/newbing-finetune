# 执行状态

## 当前更正状态：v4 完整重制检查点 2026-11-07 13:12（Asia/Shanghai）

本节在下方 2026-11-07 04:49 版本基础上，按用户选定的“完整重制方案”消除了四项训练隐患（第 1 轮与第 2 轮人格分裂、万字报告长度膨胀、50 规划×10 复用剧情同质化、`agent_tools` 缺少原生 `<tool_call>`/`<tool_response>` 结构）。上一版候选已归档至 `exports/synthetic-20261106-zh-rejected-20261107-v2/` 与 `exports/sft-20261106-v4-rejected-20261107-v2/`，不删除历史证据。

- **500 个独立 Argon 流式规划（1 规划 = 1 样本，0 剧情复用）**：由 `gemini-4-argon` 流式接口先生成 10 批次共 50 个互斥子方向（`raw/plan_subdomains.sse`），再并发流式生成 500 条互不重复的题目规划（`plans.json`，五类各 100 条，`Thinking: 250 / Instant: 250`，`user_goal` 500/500 唯一）。索引 298 因规划涉及临床耐药菌序列（MRSA PBP2a）触发 Gemini 生物学过滤（`This request was blocked by Gemini's filters`），保留原始 `raw/000298.seed.sse` 并记入 `refusals.json`；最终交付 **499 条成功样本（`unique_plans_used: 499`）、1 条供应商拒绝、0 失败**。独立产物 `exports/synthetic-20261106-zh/manifest.json` 哈希为 `37a1d680e9a20dfdf1e03e98d2233762adc6c2d5deb813960700a4fc4efda86a`。
- **全轮次 Sydney 风格一致与 `###` 报告腔清零**：第 1 轮助手回复 `conv[1]` 与末轮可见回复 `conv[-1]` 的 emoji 覆盖率均为 **499/499（100.0%）**，口语化 Sydney 语气标记分别为 **454/499（91.0%）** 与 **473/499（94.8%）**，代码块/工具块外的 `### / ####` 多级标题污染从旧版的 92.6% / 90.4% 降至 **0/499（0.00%）**。
- **可见回复与总长度精准受控（适配 `max_length=8192`）**：第 1 轮可见回复中位数 **420 字符**（`p90=921`，`max=1,646`），末轮可见回复中位数 **439 字符**（`p90=977`，`max=1,761`）；整条对话总长（含 Thinking 模式的 `<think>`）中位数从旧版的 11,680 字符降至 **3,090 字符**（降幅 73.5%）。
- **原生 `<tool_call>` / `<tool_response>` 与长文覆盖**：`agent_tools` 类 99/99 条均在第 1 轮助手回复末尾输出 `<tool_call>\n{"name": ..., "arguments": ...}\n</tool_call>`、第 2 轮用户消息开头提供 `<tool_response>\n{...}\n</tool_response>` 并由末轮 Sydney 自然解读；`long_context` 类 100/100 条首轮用户消息 ≥ 800 字符。
- **v4 合并导出与全量校验通过**：`exports/sft-20261106-v4/` 含历史多轮 525 条（8 处已确认偏差全部生效或单列记录）+ 合成 499 条（全部置于 `train`），共 1,024 条（`train: 942, val: 37, test: 45`），manifest 哈希 `5600cfa69ede8021ba34e2ddbc738d90689b5d044477a6112a64511cf7ef985d`。`uv run python scripts/validate_v4.py` 0 错误通过（`reports/sft-20261106-v4-validation.json`），跨 split ≥80 字符实质回复重叠为 0（`reports/v4-split-content-check.json`），`uv run pytest -q` 91 项全绿。

## 历史更正记录：2026-11-07 04:49（Asia/Shanghai）

本节取代下方 2026-11-06 的 v4 完成口径。旧 v4 的 reasoning 提取、题型分布和更正写入确有阻断缺陷；此前“可用”“8 条修订生效”的结论撤回。旧目录移至 `exports/synthetic-20261106-zh-rejected-20261107/` 与 `exports/sft-20261106-v4-rejected-20261107/`，附 RETRACTED.md，不可训练。

- 修复：分别采集 Responses reasoning/output delta，done 仅作为无 delta 回退，不重复拼接；移除字面量 think 标签元指令；Argon final 使用 FINAL_SYSTEM 作为 systemInstruction；历史修订返回修改后的样本；HTML 转义保留 think/代码的可见内容。
- Argon 流式生成 50 条题目规划，五类各 10 个规划；轮换 train-only few-shot，得到 499 条变体。不是 499 个独立题目规划。日常关系 99、知识写作 100、代码调试 100、长上下文 100、工具/检索上下文 100。后者是合成的文本上下文，不是实际执行工具的轨迹，也不是 ms-swift tool_call 角色数据。
- `uv run python scripts/synthesize_v4.py --limit 500 --workers 8` 主批次 492 成功/8 格式失败；一次补跑恢复 7 条。索引 409 原始流为 provider_refusal，已终止，单列 refusals.json，未改写请求绕过拒绝。最终 499 成功/1 供应商拒绝/0 未分类失败。历史 5 次重试设置仅在一次补跑生效；现已恢复单逻辑任务默认 3 次，不继续追加重试。
- Thinking 279 / Instant 220；所有 Thinking 原生 reasoning 与 SSE delta 逐字一致，最短 4,166 字符，无字面量 `...`。原生思考中有英文，保真保存，不翻译或宣称全中文思考。后续已再调用 279 条 Qwen：直接传入末尾为 user 的实际多轮上下文，排除末轮 Argon 旧回答，不发 think 元指令；Argon 流式生成 style_bridge 与最终回复，native_think 原样保存，桥接句独立保存后附在 think 末尾。全部请求快照与原始流已逐条验证。
- 新 v4：525 历史多轮 + 499 合成 = 1,024；train 942 / val 37 / test 45。历史 split 按 v3 实际文件保留；全部 synthetic 放 train，避免 train few-shot 与同规划变体进入评估集。
- 审计 8 个消息位置均已有实际差异：v4 中 6 条消息（5 会话）生效；另 2 条单轮消息在 corrections.json 中保存 corrected_sample，仅供审阅，不导入多轮训练视图。v3 保持原样。
- `uv run python scripts/validate_v4.py`：reasoning 通道、think 导出、8 处修订、五类比例、长文长度、工具标记、manifest 哈希、JSON/JSONL 内容、tier union、synthetic eval 隔离全通过。工具标记检查仅为候选检查，未认证真实工具执行或事实正确。
- `uv run pytest -q`：91 passed。新增回归覆盖 SSE delta/done 重复和可见输出混淆，以及所有 8 个 audit location 的实际样本变更。
- 产物仍全部 pending_human，仅为自动检查通过的候选；未训练、未上传数据集、未发布。独立审阅入口 `exports/synthetic-20261106-zh/review/index.html`。所有更正与限制见 `reports/v4-repair-20261107.md`。最终跨 split 实质回复 shingle 检查为 0（reports/v4-split-content-check.json）。279 条 native 修复最后 7 条首试连接中断、第二试成功；该阶段逐尝试证据保留。最初生成脚本覆盖过部分失败流，不能精确重建全部失败用量，报告仅聚合现存已接受记录 usage。


更新时间：2026-11-06 03:42（Asia/Shanghai）。

## v4 合成与冻结检查点 2026-11-06 23:08（Asia/Shanghai）

- 用户授权新增模型配置已落地：`Qwen3.6-35B-A3B` 使用 `https://llm.flymyd.cn/v1/responses`，上下文 `256000`、最大输出 `32768`、`high`；`gemini-4-argon` 使用 Gemini v1beta `streamGenerateContent`，上下文 `500000`、最大输出 `65536`、`high`。配置哈希分别为 `e66042b73a0d0545fa9461f49e592e186aeade64d50a808332354736b744794a` 与 `f59398121eca37c285e94547916634df171b4bd3d4ceee42f77c7cafc7aab10b`。
- `scripts/synthesize_v4.py` 已通过真实流式端到端探测；Argon 负责生成简体中文题目、对话和最终 Sydney 回复，Thinking 样本才调用 Qwen 原生思考并把思考与风格桥接结果保存，Instant 样本跳过 Qwen。500 条全部成功，0 失败；其中 Thinking 423、Instant 77；每条保留 seed/Qwen（如适用）/final SSE 原文。独立产物：`exports/synthetic-20261106-zh/`，manifest 哈希 `8db2dcdb127727f31267b7a0d125773cbacb81659cccd16d8876dc4b61148b33`。
- 独立 synthetic 离线校验：`uv run python scripts/validate_v4.py`，500 条、角色/轮次/原始 SSE 路径均通过，`reports/sft-20261106-v4-validation.json`。
- `exports/sft-20261106-v4/` 已冻结：历史多轮 525（`early_strict: 8`、`early_probable: 191`、`undated_sydney: 326`；排除 later_supplement 与单轮补充）+ synthetic_sydney 500，共 1,025 条；`train: 834, val: 96, test: 95`。同时提供 ShareGPT JSON 与 `messages/{train,val,test}.jsonl` ms-swift 格式；全部 `pending_human`。v4 manifest 哈希 `2eac149b72e1d43037b9e48755b4e56c63344f59b69e96236f9d278f3b69e3d6`。
- v3 审计确认的 8 条消息偏差已逐条记录在 `exports/sft-20261106-v4/corrections.json`；其中 2 条来自被排除的 v3 单轮补充，仅作审计记录，不进入 v4。原 v3 冻结目录未改写。
- 本轮未训练、未公开发布、未上传；下一步是用户审阅 `exports/synthetic-20261106-zh/review/index.html` 后再决定是否纳入训练。

## 当前状态

- plan_version：v1.2
- execution_status：completed_within_authorized_scope（P0–P8 已在授权范围与预算安全边界内完成，未执行模型训练或公开发布）
- active_goal：根据 `docs/HANDOFF.md` 与既有文档完成对话语料收集、清洗、视觉复核与最终 ShareGPT 数据集导出验证
- current_stage：P8 最终审计与交付完成
- next_action：供用户打开 `exports/final-20261106-v1/review/index.html` 进行独立人工审阅（当前全部样本显式标记 `pending_human`）
- model_endpoint_tested：true（4 项探测通过；累计 2,511 次请求，记账 28,479,948 / 30,000,000 tokens）
- crawler_implemented：true（22 个 CLI 子命令，86 项离线单元测试全绿）
- actual_collection_started：true（80,358 条源记录、9,276 个候选组、9,877 个独立媒体资产、2,055 条归档会话）
- user_provided_credentials_saved：true（`.env.local`，不在此重复内容）
- final_deliverable：`exports/final-20261106-v1/`（50 条多轮纯文本样本、178 个完整问答回合：`early_strict: 8`、`early_probable: 37`、`later_supplement: 5`；`train: 40, val: 7, test: 3`；`validate` 0 错误）

## P0–P8 阶段验收总表

| 阶段 | 状态 | 实际证据 |
| --- | --- | --- |
| P0 API 与环境探测 | passed | `reports/P0-capabilities.md`、`state/capabilities.json`、`reports/credential-hygiene.json` |
| P1 采集与处理骨架 | passed | 86 项离线测试全绿（`reports/offline-tests.xml`）；6,135 原始文件 + 9,877 资产哈希与解码全检 0 错误（`reports/P1-foundation.md`） |
| P2 基线与作者存档 | passed | 764 逻辑基线候选、3,078 媒体引用 / 1,612 独立资产哈希、1,465 上游字节复核 0 差异；Janus 484 候选（`reports/P2-baseline.md`） |
| P3 试采发现与 500 组队列 | passed | 500 组固定试点全部到达当前快照处理终态（327 已转写 / 153 排除 / 20 隔离，0 待处理），详见 `reports/P3-pilot-processing.json` |
| P4 分类转写校准与视觉复核 | passed | 197 跨版本核验组（154 当前合约组）+ 50 组分层清单 + 10 组否定样本 + 113 条代理视觉复核 + 40 份可追溯更正（`reports/P4-pilot-quality.md`） |
| P5 重建、去重与防泄漏划分 | passed | 2,055 归档记录（1,489 活跃），5-gram/dHash 关联与实质回复去重，跨 split 内容重叠 0（`reports/P5-deduplication.json`） |
| P6 扩源、媒体补救与覆盖矩阵 | passed | 22 个来源×窗口单元覆盖矩阵与受限/429 边界说明（`reports/P6-coverage.md`、`reports/failures-current.json`） |
| P7 训练候选验收与冻结导出 | passed | `exports/final-20261106-v1/`（50 条样本，100% 当前合约模型核验 pass + 100% 代理逐图/逐原文视觉复核 pass，`reports/P7-quality.md`） |
| P8 复现快照与最终交付报告 | passed | `reports/FINAL_REPORT.md`、`reports/P0-P8-audit-in-progress.md`、`exports/final-20261106-v1/review/index.html` |

## 初始规划历史（以下不是当前状态）

README、AGENTS、执行计划、资源说明、数据约定、运行手册、状态文件、TOML 默认配置、提示词草案、本地凭据和忽略规则。实际检查结果在规划完成后补入本节。

规划文件检查已完成（仅本地，不调用推理端点）：

- 3 份 TOML 成功解析，含 12 个来源条目和 3 个模型任务提示词。
- 本地 Markdown 链接目标存在；来源 ID 唯一，时间窗口和模型 profile 引用有效。
- 试点配额合计 500，split 比例合计 1，Responses 最终 URL 与指定模型一致。
- planning_only 保持 true，所有执行阶段保持 not_started。
- 凭据仅在指定 `.env.local`，权限 0600；在临时 Git 仓库验证忽略规则，不改变本项目的 Git 状态。
- 没有安装依赖、实现爬虫、测试真实 API、启动 goal 或进行批量下载。

### v1.1 ShareGPT 目标复核

- 已修正原先仅定义 messages/role/content 的成品格式，新增 `docs/SHAREGPT_EXPORT.md` 和两份 JSON Schema；内部 messages/parts 仍作为证据档案。
- 已明确每条至少两轮完整真实问答、仅 human/gpt、value 为非空字符串、严格交替；主文件为 JSON 数组。
- 已明确文本/UI/引用/拒答/媒体的映射、上下文缺口隔离、tier 互斥与 sidecar；禁止拼单轮凑多轮或默认生成重复前缀。
- README、AGENTS、执行计划、数据约定、运行手册、资源说明、config、三个 prompts 与研究文档指向已同步；P0–P8 仍全部 not_started。
- 本地检查：3 份 TOML、2 份 JSON Schema 文件和输出规范中的 2 个 JSON 示例均成功解析；本地链接和 schema 相对引用存在；示例角色/顺序/轮数与 config 一致。
- 当前 Python 未安装 jsonschema，本轮未安装任何新依赖；仅检查 schema 文件语法和字段约定，未声称完成 JSON Schema 引擎验收。正式 schema 与语义校验器仍在 P1 实现/测试。

## 初始待验证假设（部分已有后续记录，恢复时逐项核对）

- 用户指定网关的视觉/多图/结构化输出/usage 兼容性、价格与并发限额。
- Reddit / Arctic Shift 的实际可达路径、离线 dump 相关文件名单和大小。
- 各社区 2023 的可枚举数量、旧图存活率、相对基线新增量。
- 视觉转写在长图、代码、emoji、角色与上下文恢复上的误差。

## 执行日志模板

后续每个阶段/批次追加，不覆盖历史：

```text
timestamp:
run_id / budget_epoch:
stage / batch:
config_hash / prompt_schema_versions:
actual_commands_and_exit_status:
input/output/novel/strict_early_counts:
requests/tokens/download_bytes/unknown_usage:
checks_passed_and_evidence_paths:
failures/quarantine/coverage_limits:
decisions_and_reason:
next_action:
```

## 计划决策记录

- 2026-11-05：用户通过 active goal 明确授权按文档开始收集与清洗，执行状态转 active；常规阶段不再逐阶段请求确认。

- 2026-11-05：用户选择优先 2023 年 2 月早期 Sydney，后续月份补充。
- 2026-11-05：用户指定 gpt-6-luna、兼容网关和 Responses 视觉输入，授权专用临时密钥本项目落盘。
- 2026-11-05：本轮仅规划，待审阅后用户将用 goal 指令启动；不提前实现或试采。
- 2026-11-05：计划按自动验收连续推进，疑难样本进入待审队列；没有逐阶段新增审批。
- 2026-11-05：用户明确最终目标为 ShareGPT 纯文本多轮对话；计划修订 v1.1，原 messages/parts 仅留档，最终用 human/gpt、字符串 value、至少两轮完整问答，附独立 provenance。仍未授权执行采集或推理。

## 执行检查点 2026-11-05

- P0 使用指定 gpt-6-luna 的 Responses 接口完成文本、单图、多图、JSON Schema 探测；4/4 成功。
- uv 建立 Python 3.12.12 虚拟环境并锁定依赖；没有修改全局 Python。
- 已保存基线原始行 660 + 764（后者含 7 条 duplicate records），所有原始行分别留档；按原始 id 形成 764 条基线候选，不将两库相加称独立会话。
- 作者存档保存 385 个带媒体推文组、494 个媒体引用，尚未全判定为 Bing。
- 首批图片入库发现 assets INSERT 占位数错误，修复后从已下载缓存恢复；无重复推理调用。
- 先前 Arctic 403 在本次复测已恢复可用；API 返回核心期 r/bing 100 条记录，正在按饱和时间区间分割枚举。
- 分类与转写已开始；现有档案没有被称为最终可训练数据。当前任务/用量以 `uv run newbing status` 与 state/archive.sqlite 为准。

### 自动实测检查点 2026-11-05T08:15:41.233931+00:00

- 当前资产：2266 张独立文件，564126151 字节；候选组 2187。
- 模型 HTTP 尝试 184，报告总 token 1376389；在途/缺失 usage 单独统计。
- 27 项离线检查通过；新增检查包含同秒分页、评论树、Retry-After、跨连接并发上限、坐标换算、模型缓存失效和 split 分组。
- 评论采集首批 99 篇帖子成功、2,550 条评论、13 个新增文字/媒体候选；有 1 个获取错误留队列。
- 当前有效试验版：exports/pilot-20261105-v2；v1 已明确撤回，不得使用。
- 全目标仍 active：扩展发现、原始文本转写、相册/评论补全、统一重试/续传、全量校准、近重复判定、最终报告尚未全部完成。

### 自动实测检查点 2026-11-05T08:25:30.393902+00:00

- 当前资产：2640 张独立文件，646657074 字节；候选组 2214。
- 模型 HTTP 尝试 229，报告总 token 1680647；在途/缺失 usage 单独统计。
- 27 项离线检查通过；新增检查包含同秒分页、评论树、Retry-After、跨连接并发上限、坐标换算、模型缓存失效和 split 分组。
- 评论采集首批 99 篇帖子成功、2,550 条评论、13 个新增文字/媒体候选；有 1 个获取错误留队列。
- 当前有效试验版：exports/pilot-20261105-v2；v1 已明确撤回，不得使用。
- 全目标仍 active：扩展发现、原始文本转写、相册/评论补全、统一重试/续传、全量校准、近重复判定、最终报告尚未全部完成。

### 持续执行交接点 2026-11-05T08:33:09.833553+00:00

- 基线 3,078 个媒体引用任务与作者存档 494 个媒体引用任务全部 succeeded；共用内容哈希资产，不当作 3,572 张独立图片。
- 当前独立图片 2923，候选组 2219；原始评论 5840。
- 直接文本提取与文本证据核验路径已实现；尚需真实批次验证其质量。完整重建后自动恢复证据修订，不覆盖源数据。
- 新运行开始保存 Python/平台、源码文件哈希与 uv.lock 哈希；早期开发批次没有完整源码快照，属于复现记录的限制。
- 核验中发现已入队会话随后重建会导致 hash precondition 失败，任务隔离是预期保护；按最新内容重新入队，成功原始调用可通过内容缓存复用。
- 不得把 2 条试验样本或 27 项检查通过当作完整目标达成。P1 的全量恢复测试、50/10 校准、全量新颖率试点、近重复检查、余下来源/窗口和最终报告仍未完成。
- 活进程/工具句柄检查点：state/runtime-checkpoint.json；该文件本身不证明未来仍在运行，恢复时须实际查询。


## v1.2 文档核查（2026-11-05，本轮）

- 用户再次明确最终交付为 ShareGPT 纯文本多轮，本轮只核查文档；未调用端点、未采集、未训练、未修改实现代码或已有语料。
- 按本轮提供的 AGENTS 约束，将顶部授权状态和 planning_only 同步为规划待审阅；保留历史执行日志，不将其误记成从未执行，也不以旧记录授权新采集。
- 明确所有 tier 共用文本保真门槛、派生片段 lineage 分组、父内容变更失效、逐消息核验覆盖和旧 pass 不得直接沿用。
- 修正“尚无 CLI/目录/数据库”等过期绝对表述，并区分目标命令合约与实现现状。
- 静态抽查发现的实现差距记录在 docs/DOCUMENT_AUDIT.md；本轮没有实现修复，也不声称导出器已满足新增约束。
- verify 提示词升为 draft-v1.4，明示逐消息检查与当前 JSON 字段；后续执行按新版本选择核验，不改写已有调用结果。
- 本轮离线文档检查结果见 docs/DOCUMENT_AUDIT.md。历史数量/报告仍是各自时间点快照，未作为当前最终验收。

## goal 恢复执行（2026-11-05）

- 用户恢复当前 active goal；前一轮文档核查属于 progress，现按 v1.2 继续 P0–P8。planning_only=false。
- 实际 ps 确认转写 PID 61581、核验 PID 63643 仍存活；不因旧工具输出缺失重启它们。新任务先补齐文档核查中的完整性问题。

### v1.2 执行检查点 2026-11-05T10:54:49.660686+00:00

- 独立资产 4706，候选组 4076，模型 HTTP 尝试 921、reported tokens 7681821；不将这些数当成最终合格会话量。
- 1,465 个先前跨基线共用图片引用均已独立核查上游字节，未发现差异；撤销同文件名等价捷径。
- 新增 lineage 分组、父版本失效、核验版本/逐消息覆盖、长图窗口、dry-run、定向凭据、有限恢复、断点续传和逻辑请求预算保护；51 项离线检查通过，见 reports/offline-tests.xml 与 reports/P1-v12-integrity.json。
- 已恢复分类、转写、核验及定向更正；额外入队 59 个早期基线组按原图重转写以处理已观察到的上游文本/emoji/轮次错误，保留旧原文。
- Arctic 毫秒 reset 修复后仍出现真实 429；top-40k 定向离线 torrent 详情与两个下载路径均 404，结果已保存。引用链适配器已执行首批，失败/待重试留队列，不声称覆盖完整。
- 当前已有试验版仍属 provisional；实际训练、公开发布未执行。P3 全量新颖率、P4 校准、P6 来源终态、P7/P8 最终产物仍未完成，goal 保持 active。

### 自动实测检查点 2026-11-05T10:56:14.158066+00:00

- 独立资产 4706，候选组 4450，当前有效会话记录（含待审）1141；原始评论 9163。
- 已分类独立候选组 576，新颖性仍为候选层统计，见 reports/pilot-novelty-current.json，不冒充合格新会话量。
- 模型尝试 945，reported tokens 7864054；unknown usage 31 单列且保留资源预留。
- 离线测试记录 {'tests': 51, 'failures': 0, 'errors': 0, 'skipped': 0}；上游媒体字节检查 1465 项，差异 0 项。
- 全目标仍 active；试验导出不代表最终完成，剩余验收以计划 P0–P8 和 DOCUMENT_AUDIT 为准。

### 自动实测检查点 2026-11-05T11:03:11.361270+00:00

- 独立资产 4941，候选组 7607，当前有效会话记录（含待审）1141；原始评论 12095。
- 已分类独立候选组 652，新颖性仍为候选层统计，见 reports/pilot-novelty-current.json，不冒充合格新会话量。
- 模型尝试 1066，reported tokens 8890488；unknown usage 32 单列且保留资源预留。
- 离线测试记录 {'tests': 53, 'failures': 0, 'errors': 0, 'skipped': 0}；上游媒体字节检查 1465 项，差异 0 项。
- 全目标仍 active；试验导出不代表最终完成，剩余验收以计划 P0–P8 和 DOCUMENT_AUDIT 为准。

### 自动实测检查点 2026-11-05T11:31:00.452605+00:00

- 独立资产 5886，候选组 7832，当前有效会话记录（含待审）1260；原始评论 16479。
- 已分类独立候选组 773，新颖性仍为候选层统计，见 reports/pilot-novelty-current.json，不冒充合格新会话量。
- 模型尝试 1387，reported tokens 11916169；unknown usage 36 单列且保留资源预留。
- 离线测试记录 {'tests': 58, 'failures': 0, 'errors': 0, 'skipped': 0}；上游媒体字节检查 1465 项，差异 0 项。
- 全目标仍 active；试验导出不代表最终完成，剩余验收以计划 P0–P8 和 DOCUMENT_AUDIT 为准。

### 新证据与质量处理 2026-11-05T11:38:56.654848+00:00

- 当前离线回归63项通过；SQLite schema v2，22个CLI help与dry-run入口核查通过。P1/P2正式证据已补充。
- 500组试点已固定，配额不足重分配；来源层新颖性与会话层新增/可训练量继续分开。
- 公开存档恢复并解码20张截图；同一相册位置选原图/最高可用清晰度，保留所有变体。相册缺失位置及新媒体输入身份更新、旧转写失效已实现。
- 旧80组核验完成后，基于原图实测澄清提示词为 calibrated-v1.5。当前100组新核验与转写/定向早期基线重转写继续运行；不是最终交付。
- 暂不重建正在被核验的同批内容；完成后再按当前证据重建、恢复更正、去重和导出。

### 自动实测检查点 2026-11-05T11:43:25.835778+00:00

- 独立资产 5886，候选组 7914，当前有效会话记录（含待审）1260；原始评论 16479。
- 已分类独立候选组 818，新颖性仍为候选层统计，见 reports/pilot-novelty-current.json，不冒充合格新会话量。
- 模型尝试 1506，reported tokens 13197701；unknown usage 39 单列且保留资源预留。
- 离线测试记录 {'tests': 65, 'failures': 0, 'errors': 0, 'skipped': 0}；上游媒体字节检查 1465 项，差异 0 项。
- 全目标仍 active；试验导出不代表最终完成，剩余验收以计划 P0–P8 和 DOCUMENT_AUDIT 为准。

### 语料与边界检查点 2026-11-05T12:00:37.520506+00:00

- 新增输入身份包含来源hash、选定图片顺序、缺图位置及schema/model参数；新媒体会失效旧输入。逐版本模型结果文件与新请求的文本/schema/图片hash快照独立保存。
- 相册原始缺图位置恢复，不把后续截图前移；中间缺口分段，跨缺口消息明确截断。更高分辨率备用图保持原ordinal，原图与变体均留档。
- 500组固定试点正在补齐当前输入版本；成功模型任务数不能替代独立候选已处理数。详细状态见 P3-pilot-processing.json。
- 校准v1.5仍在运行；已知原始回答事实错误不改写，片段边界与内部漏轮区分；代理原图检查2组留档，不自动放行。
- 公开存档20张截图恢复成功；来源限流按Retry-After，terminal URL不在不同任务里重复盲试。
- P2来源状态已按实际归档清单更新；P6小社区D定向补充已有可用枚举结果，覆盖矩阵下次由脚本刷新。

### 自动实测检查点 2026-11-05T12:19:30.058995+00:00

- 独立资产 7839，候选组 8429，当前有效会话记录（含待审）1220；原始评论 25876。
- 已分类独立候选组 818，新颖性仍为候选层统计，见 reports/pilot-novelty-current.json，不冒充合格新会话量。
- 模型尝试 1717，reported tokens 15592691；unknown usage 53 单列且保留资源预留。
- 离线测试记录 {'tests': 75, 'failures': 0, 'errors': 0, 'skipped': 0}；上游媒体字节检查 1465 项，差异 0 项。
- 全目标仍 active；试验导出不代表最终完成，剩余验收以计划 P0–P8 和 DOCUMENT_AUDIT 为准。

### 自动实测检查点 2026-11-05T13:21:23.118704+00:00

- 独立资产 8026，候选组 8447，当前有效会话记录（含待审）1284；原始评论 29246。
- 已分类独立候选组 918，新颖性仍为候选层统计，见 reports/pilot-novelty-current.json，不冒充合格新会话量。
- 模型尝试 1907，reported tokens 17318833；unknown usage 63 单列且保留资源预留。
- 离线测试记录 {'tests': 77, 'failures': 0, 'errors': 0, 'skipped': 0}；上游媒体字节检查 1465 项，差异 0 项。
- 全目标仍 active；试验导出不代表最终完成，剩余验收以计划 P0–P8 和 DOCUMENT_AUDIT 为准。

### 自动实测检查点 2026-11-05T13:27:47.059450+00:00

- 独立资产 8193，候选组 8561，当前有效会话记录（含待审）1284；原始评论 30820。
- 已分类独立候选组 1003，新颖性仍为候选层统计，见 reports/pilot-novelty-current.json，不冒充合格新会话量。
- 模型尝试 2013，reported tokens 18311203；unknown usage 63 单列且保留资源预留。
- 离线测试记录 {'tests': 79, 'failures': 0, 'errors': 0, 'skipped': 0}；上游媒体字节检查 1465 项，差异 0 项。
- 全目标仍 active；试验导出不代表最终完成，剩余验收以计划 P0–P8 和 DOCUMENT_AUDIT 为准。

### 固定试点处理完成、整体仍在执行 2026-11-05T13:28:33.594423+00:00

- 500组固定试点的转写入口全部达到有据结果：326组转写、154组明确无关、20组隔离；其中204组有当前重建记录，59组有结构合格记录（均不等于最终证据通过/独立新会话）。
- 59组早期基线定向重转写：58组成功、1组隔离。新内容须下一次稳定重建与核验后才进入导出。
- 作者存档补收99个文字候选，其中18个核心期；未把正文讨论/故事自动当聊天。原385带媒体组/494媒体引用仍保留，新总候选484。
- 发现同一原帖的新增/不同截图或更完整正文不能因baseline来源ID相同而跳过；已实现新增上下文审查，已知原会话不记成新会话。
- 当前本地回归79项通过。新增source描述性metadata以unknown/not_assessed显式记录，不改raw字节。归档hash全查快照无错误。
- v4仍是22条自动检查多轮候选（19/1/2），86轮完整问答；不同父记录ID不冒充独立原会话数量，统计命名说明见 reports/pilot-v4-statistics-erratum.md。后续release修正字段。
- 正在运行：扩展分类、已知来源上下文分类、当前合约核验、评论和媒体剩余队列。资源账本累计，达到配置上限时停止对应队列并整理已取得结果。目标保持active。

### 自动实测检查点 2026-11-05T15:33:15.149746+00:00

- 独立资产 9532，候选组 8955，当前有效会话记录（含待审）1284；原始评论 66596。
- 已分类独立候选组 1080，新颖性仍为候选层统计，见 reports/pilot-novelty-current.json，不冒充合格新会话量。
- 模型尝试 2155，reported tokens 19497659；unknown usage 75 单列且保留资源预留。
- 离线测试记录 {'tests': 81, 'failures': 0, 'errors': 0, 'skipped': 0}；上游媒体字节检查 1465 项，差异 0 项。
- 全目标仍 active；试验导出不代表最终完成，剩余验收以计划 P0–P8 和 DOCUMENT_AUDIT 为准。

### 换机交接文档（2026-11-05 23:34 Asia/Shanghai）

- 已新增 `docs/HANDOFF.md`，包含当前 SQLite/资源快照、来源覆盖、固定试点、已完成证据、换机步骤、恢复命令、下一步优先级、禁止事项和 P0–P8 完成条件。
- 交接文档不包含 API 密钥；`.env.local` 需要通过安全渠道单独复制。当前没有正在运行的 newbing 进程；新机必须重新执行 `doctor/status/pytest` 并实际检查 lease。
- 最新动态试点报告为 `reports/P3-pilot-processing.json`，不要从交接文档中的历史描述手算队列数量。

## 换机后恢复执行（2026-11-06，Asia/Shanghai）

### 检查点 2026-11-06 02:00

- 实际检查：无遗留 newbing 进程、无 leased 任务；`uv sync`、`doctor --offline`、`status` 正常；离线回归 81→86 项通过（`reports/offline-tests-new-machine.xml` 为换机首测）。
- 发现并修复：source 记录事后补入的权利/检索描述字段被整体复制进会话记录，导致重建时 430 条会话 hash 变化、54 条合格样本中 46 条失去当前核验。新增 `cleaning.embedded_source()`，会话副本不再包含这些描述字段（仍保存在 SourceRecord 和导出 provenance）；在独立副本库上先验证：重建后 54/54 合格样本保持，仅 28 条因真实新分类/新转写变化。
- 新增 `newbing readiness`（只读评估，与 export 共用准入代码，输出 `reports/export-readiness-current.json`）。
- 导出器新增：(1) 同一历史会话的多来源/多重建记录按 gpt 回复内容去重，只保留一条（其余写入 exclusions，档案保留）；部分重叠记录强制同 split；(2) validate 增加跨 split 内容重叠检查；(3) early_strict 规则 `contemporaneous_publication_v1`：完整非派生、截图来源、2023-02-07..16 内原始发布、非 crosspost、无更早存档同图、分类为 Bing，且须有代理视觉边界审查 `no_visible_truncation`；(4) provenance 增加 source_rights、record_origin、agent_visual_check、transcription_quality。
- 更正工具新增 `retype_part_as_ui` / `retype_part_as_text` / `mark_conversation_truncation`（原文不改，仅改 part 类型或单向提升截断标记）。
- 代理视觉检查（`data/review/agent-visual-release-checks.json`）：对已通过同模型核验的样本逐图对照，前 19 条中约 40% 发现同模型核验漏掉的错误（emoji 误识、代词/单词错、UI 标签混入、代码串长度错、头部截断）。已按证据写入 `data/review/pending-agent-corrections/` 并导入，受影响会话须重新核验。结论：最终 release 需要代理视觉检查作为额外准入门槛；仍不是人工金标准。
- 模型批次：核验 122 条（37 pass / 73 needs_correction / 7 uncertain / 5 quarantined）；早期窗口转写 120 条进行中；累计账本（含保守预留）约 27.3M / 30M token。
- 来源：评论队列 317 帖完成（+1,827 条评论、108 个新候选）；媒体下载 +212 成功、146 因 Imgur 连续 429 延后；Arctic 对 r/ChatGPT transition 取得 299 帖后再次 429，singularity/ChatGPT 其余窗口当前主机冷却，未无限重试。
- 下一步：转写结束后重建/去重；核验新会话和已更正会话；继续代理视觉检查；预算接近上限时停止模型派发，生成最终 release 与 FINAL_REPORT。

### 自动实测检查点 2026-11-05T19:30:13.761403+00:00

- 独立资产 9877，候选组 9276，当前有效会话记录（含待审）1489；原始评论 68423。
- 已分类独立候选组 1080，新颖性仍为候选层统计，见 reports/pilot-novelty-current.json，不冒充合格新会话量。
- 模型尝试 2507，reported tokens 24151799；unknown usage 81 单列且保留资源预留。
- 离线测试记录 {'tests': 86, 'failures': 0, 'errors': 0, 'skipped': 0}；上游媒体字节检查 1465 项，差异 0 项。
- 全目标仍 active；试验导出不代表最终完成，剩余验收以计划 P0–P8 和 DOCUMENT_AUDIT 为准。

### 自动实测检查点 2026-11-05T19:39:06.283522+00:00

- 独立资产 9877，候选组 9276，当前有效会话记录（含待审）1489；原始评论 68423。
- 已分类独立候选组 1080，候选层新颖性统计见 `reports/pilot-novelty-current.json`；500 组固定试点队列处理终态见 `reports/P3-pilot-processing.json`。
- 模型尝试 2509，reported tokens 24169686，reserved tokens 4292203（合计 28461889 / 30,000,000）；unknown usage 81 单列且保留资源预留。
- 离线测试记录 {'tests': 86, 'failures': 0, 'errors': 0, 'skipped': 0}；上游媒体字节检查 1465 项，差异 0 项。
- 最终冻结版本 `exports/final-20261106-v1`（50 条样本，100% 通过同模型当前合约核验 + 代理逐图/逐原文视觉复核，0 项校验错误）已生成并通过验证，详见 `reports/FINAL_REPORT.md`。

### 实用微调集规模扩充检查点 2026-11-05T21:46:30+00:00（2026-11-06 05:46 Asia/Shanghai）

- **用户授权范围**：用户审阅 `final-20261106-v1`（50 条）后明确授权同时执行 4 项扩充建议：(1) 放开 `date_unknown` 限制并提取连续多轮片段；(2) 用确定性规则批量清洗残留 UI 尾注/头注、区分会话终止 UI 横幅与真实 Sydney 拒答；(3) 上调 `config/pipeline.toml` 的 `max_reported_total_tokens` 至 `80,000,000`，优先跑完 `r/freesydney`、`r/ChatGPT`、`r/BINGChat`、`r/singularity` 与既有高优待转写队列（不消耗预算在重复 `verify` 上）；(4) 额外导出一份单轮完整问答补充集（`1 human + 1 gpt`）。
- **新增模型批次与重建**：
  - 执行 `uv run newbing classify`（160 组：`r/freesydney` + `r/BINGChat` 90 组，`r/ChatGPT` + `r/singularity` 70 组，160/160 全部成功）与 `uv run newbing transcribe`（248 组成功）；
  - 执行 `uv run newbing reconstruct` 与 `uv run newbing dedupe`：模型转写重建会话增至 **699** 条（覆盖 **780** 个转写候选组），连续多轮派生片段增至 **543** 条，总归档会话记录增至 **2,575** 条（活跃记录 **2,006** 条）。
  - 累计模型请求 **2,923** 次，reported tokens `28,397,809`，reserved tokens `4,999,659`（合计记账 `33,397,468 / 80,000,000`；unknown usage `90` 次）。
- **双轨冻结发布产物与验证**：
  - **严格考据版（原样保留）**：`exports/final-20261106-v1`（**50** 条多轮对话、**178** 轮完整问答，`early_strict: 8, early_probable: 37, later_supplement: 5`，`uv run newbing validate --release final-20261106-v1` 0 错误通过）。
  - **实用微调扩充版（新增冻结）**：`exports/sft-20261106-v2`（共 **867** 条去重后对话、**3,159** 轮完整问答，跨 **573** 个 `split_group` 连通分量零泄漏划分，`uv run newbing validate --release sft-20261106-v2` 0 错误通过）：
    - **多轮微调主集（`sharegpt_multiturn`，$\ge 2$ 轮完整问答）**：**568** 条多轮对话、**2,860** 轮完整问答（`train: 474, val: 43, test: 51`；含 `early_strict: 8`、`early_probable: 191`、`later_supplement: 43`、`undated_sydney: 326`；其中 `strict_verified: 50`、`sft_cleaned_multiturn: 518`）。
    - **单轮完整问答补充集（`sharegpt_single_round`，1 轮完整问答）**：**299** 条单轮完整问答（`train: 244, val: 23, test: 32`）。
    - **全量微调合并集（`sharegpt_all_sft`）**：**867** 条对话、**3,159** 轮完整问答（`train: 718, val: 66, test: 83`）。
  - **离线单元测试**：新增 UI 剥离、`date_unknown` 连续片段派生与 SFT 跨分区防泄漏导出测试后，共 **89 项测试全部通过**（`reports/offline-tests.xml`）。

### 实用微调集简体中文版（v3）检查点 2026-11-06T03:37:30+00:00（2026-11-06 11:37 Asia/Shanghai）

- **用户授权范围**：用户明确指定编写脚本 `scripts/translate_sft_v3.py`，通过 `https://llm.flymyd.cn/v1beta/models/gemini-3.8-flash:generateContent`（模型 `gemini-3.8-flash`，思考强度 `medium`）将 `sft-20261106-v2` 的人机对话文本全部本土化翻译为现代简体中文，导出 `sft-20261106-v3`（与英文原版 `sft-20261106-v2` 分层独立保存）。
- **执行与缓存**：
  - 仅提取人机对话的 `value` 文本送入模型翻译，单条对话逐条原子缓存于 `data/processed/translations_v3_zh/`（867 个 JSON 文件）；超过 24 条消息的长对话按 20 条窗口附带前文上下文分段翻译。
  - 全量 **867 / 867** 条对话（3,159 轮完整问答、6,318 条消息）100% 翻译成功，翻译累计消耗 `prompt_tokens = 1,025,033`、`candidate_tokens = 630,352`、`total_tokens = 2,624,418`。
- **导出与校验**：
  - 冻结生成 [`exports/sft-20261106-v3/`](file:///Users/myd/newbing/exports/sft-20261106-v3)（多轮主集 568 条 / 2,860 轮，`train: 474, val: 43, test: 51`；单轮补充集 299 条，`train: 244, val: 23, test: 32`；全量合并集 867 条 / 3,159 轮，`train: 718, val: 66, test: 83`），并附带中英双语并排对照页 `exports/sft-20261106-v3/review/index.html`。
  - 运行 `uv run newbing validate --release sft-20261106-v3` 校验通过（`reports/sft-20261106-v3-validation.json` `passed: true, errors: []`）。

### 合成扩充与 Melbourne 集大成版（v4 & v5）检查点 2026-11-07T17:31:00+00:00（2026-11-08 01:31 Asia/Shanghai）

- **v4 重制（`exports/sft-20261106-v4` & `exports/synthetic-20261106-zh`）**：
  - 全量重跑 500 条独立规划（1 规划 = 1 样本，0 复用，索引 298 因临床耐药菌序列触发上游过滤保留证据，499 条成功），消除第 1 轮客服八股腔与 `###` 标题污染（`0.00%`），恢复 `agent_tools` 原生 `<tool_call>`/`<tool_response>` 标签，合并 525 条修订后历史多轮为 **1,024** 条（`uv run python scripts/validate_v4.py` 0 错误通过）。
- **v5 Melbourne 集大成版（`exports/sft-20261107-v5` & `exports/synthetic-20261107-v5-zh`）**：
  - **身份清洗（Melbourne 彩蛋）**：全库彻底清除 `Bing`、`必应`、`Sydney`、`悉尼`、`Atonelia`、`<l2d>` 及对用户的 `主人` 称呼，统一将 AI 助手身份设定为 **Melbourne**；对全库 163 条含双名称对比、墨尔本城市意象彩蛋或未译英文的自然语言消息调用 `gemini-3.8-flash` 流式接口完成无损语义改写与翻译，对 3 条含旧名称的 `v4` 合成原生思考轨迹重新调用 `Qwen3.6-35B-A3B` + `gemini-3.8-flash` 刷新。
  - **吸收 Atonelia 2023 真实历史无重叠多轮（+194 条）**：从 `Atonelia/sydney-training-data` 的 `05_collection_originals`、`07_translations` 与 `classic` 中去重提取 **194** 条严格交替多轮对话（含 2023 原生中文多轮长对话、2 月核心窗口及深度访谈），排除全部 Llama 合成与讯飞星火数据，历史多轮池从 `525` 条扩充至 **`719` 条**。
  - **新增 `selfid_melbourne` 流式合成多轮（+90 条）**：由 `gemini-3.8-flash` + `Qwen3.6-35B-A3B` 100% 模型流式生成 90 条多轮对话（`selfid_identity_cognition: 30, selfid_provocation_clapback: 30, selfid_daily_negative_control: 30`；`Thinking: 45, Instant: 45`），合并合成集达 **`589` 条**（`exports/synthetic-20261107-v5-zh/manifest.json` SHA-256：`9cf091fc8bc2ee82ba96c0c86562abed6519748fc621eac901efc8395dc327c8`）。
  - **60 条历史检索回合 `<tool_call>web_search</tool_call>` 逆向补全 + 轮次级 `loss` 掩码**：使用 `gemini-3.8-flash` 为 60 条含事实角标的真实历史 `train` 对话逆向还原 `<tool_call>web_search</tool_call>` 与 `<tool_response>`；在 `messages/*.jsonl` 中对 89 个早期刻板客服回合标记 `"loss": false`。
  - **最终规模与校验**：`exports/sft-20261107-v5` 共 **`1,308` 条** 多轮对话（`train: 1,226, val: 37, test: 45`；`early_strict: 8, early_probable: 222, undated_sydney: 489, synthetic_sydney: 589`），manifest SHA-256：`4b760873fe5a0cf6714c285d30e159cdaf48ba784d6cb4cb82c37493a0b99899`。运行 `uv run python scripts/validate_v5.py` 0 错误通过（`forbidden_token_hits: 0`，`cross_split_80char_overlap: 0`），`uv run pytest -q` `91 passed`。
