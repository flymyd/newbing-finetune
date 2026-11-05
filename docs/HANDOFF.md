# New Bing 2023 语料项目：换机交接

交接时间：2026-11-05 23:30（Asia/Shanghai）

这份文档给下一台电脑上的 Codex 使用。它描述当前工作树、SQLite 队列、原始档案、模型处理结果和未完成任务。它不是最终数据集报告，也不构成“项目已完成”的声明。

## 目标与边界

目标是尽可能收集并清洗 2023 年 New Bing / Sydney 的真实人机对话，最终导出 ShareGPT 纯文本多轮数据：

```json
{"id":"...","conversations":[
  {"from":"human","value":"..."},
  {"from":"gpt","value":"..."},
  {"from":"human","value":"..."},
  {"from":"gpt","value":"..."}
]}
```

项目准入规则是每条至少两轮真实完整问答（至少 4 条消息），`human` 开头、`gpt` 结尾、严格交替，`value` 是非空字符串。原图、原文、媒体、日期依据、模型输出、质量标记和 split 信息放在档案及 sidecar，不进入 ShareGPT 主文件。

优先级是：2023-02-07 至 2023-02-16 早期 Sydney；2023-02-17 至 2023-02-28；2023-03 至 04；其余 2023 定向补充。实际训练、公开发布、上传数据集、向作者联系和更换模型供应商均不在范围内。

## 当前状态（数据库快照）

当前没有正在运行的 `newbing` 进程。恢复前仍应实际运行 `ps` 和 `uv run newbing status`，不要只相信本文件或 `state/runtime-checkpoint.json`。

| 项目 | 当前值 |
| --- | ---: |
| SourceRecord | 78,232 |
| 候选组 | 8,955 |
| 已解码独立资产 | 9,532 |
| 资产字节 | 1,834,561,735 |
| Conversation 记录 | 1,842（包含派生、待审和历史版本，不能直接当独立会话数） |
| HTTP 尝试 | 24,479 |
| 下载响应字节 | 2,665,321,574 |
| 模型 HTTP 尝试 | 2,155 |
| reported tokens | 19,497,659 |
| conservative reserved tokens | 3,953,899 |
| unknown usage | 75（均为已结束但缺 usage 的历史调用） |
| 离线回归 | 最新记录 81 项通过；运行后以 `reports/offline-tests.xml` 为准 |

当前 source kind 数量：

- `author_archive`: 484
- `baseline`: 1,424（两个固定 HF 数据集的所有原始行；逻辑基线候选为 764）
- `external_album`: 68
- `reddit_comment`: 66,596
- `reddit_post`: 9,306
- `reference_page`: 349
- `web_page`: 5

队列中的重要状态：

- classify：2,452 succeeded，4,959 pending，50 retry_wait，9 quarantined；7,520 个旧输入已标 `superseded_input`，46 个已知基线重复任务标 `suppressed_known_baseline`。
- transcribe：850 succeeded，166 pending，279 retry_wait，36 quarantined；595 个旧输入已替换。
- verify：439 succeeded，79 retry_wait，287 quarantined；当前仍有 pending/leased，须用当前核验合约重新检查。
- comments：6,363 succeeded，316 pending，3 retry_wait，1 failed_terminal。
- download：14,259 succeeded，373 pending，9 retry_wait，2,217 failed_terminal；597 个由于主机在本轮反复限流而标记 `deferred_source_unavailable`。
- reference：357 succeeded，29 failed_terminal。
- repair：24 succeeded，12 quarantined。

这些状态来自本次交接前的 SQLite 快照。换机后以 SQLite 为准，不要手工编辑任务状态。

## 已经完成且有证据的工作

### 文档、配置和环境

- [AGENTS.md](../AGENTS.md) 已明确最终 ShareGPT 合约、凭据边界、资源上限和 P0–P8 约束。
- [EXECUTION_PLAN.md](EXECUTION_PLAN.md) v1.2、[DATA_CONTRACTS.md](DATA_CONTRACTS.md)、[RUNBOOK.md](RUNBOOK.md)、[SHAREGPT_EXPORT.md](SHAREGPT_EXPORT.md) 已同步。
- `uv.lock` 已生成；项目使用 Python 3.12 环境，依赖在 `pyproject.toml` 中锁定。
- SQLite schema 当前为 v2，增加了 owner PID、主机请求门控、逻辑模型请求标识和任务索引。
- 最新离线回归报告是 `reports/offline-tests.xml`；测试覆盖源站分页、评论树、媒体顺序、坏图、限流、预算、lease、模型重试、缓存、ShareGPT 语义、派生片段、长图窗口、断点续传、凭据脱敏等。

### 模型端点和凭据

只允许使用：

- base URL：`https://llm.flymyd.cn/v1`
- Responses path：`/responses`
- model：`gpt-6-luna`
- 项目变量：`NEWBING_API_KEY`
- 本地文件：`.env.local`

`.env.local` 不应写入交接文档、Git、报告、导出、请求快照或公开包。换机时通过安全渠道单独复制，并确认权限为 `0600`。客户端显式从项目 `.env.local` 加载，SDK 自动重试关闭。模型请求快照只保存 prompt/schema/图片 hash/参数，不保存 Authorization 或 base64。

历史 P0 能力探测已成功覆盖：文本 Responses、单图、多图和结构化 JSON；报告见 `reports/P0-capabilities.md`、`state/capabilities.json`。这不是对未来请求永远成功的保证。

### 基线和原始档案

- `david-ar/bing-chat-logs` 固定 revision 的 660 原始行已归档。
- `xlr8harder/sydney-redivivus-data` 固定 revision 的 757 主记录 + 7 duplicate 记录已归档，共 764 原始行。
- 两个集合没有直接相加为独立会话；原始行和 overlap 关系均保留。
- 基线媒体引用 3,078 个；先前跨库同文件名共用的 1,465 个引用已重新检查上游字节，未发现差异。报告见 `reports/P2-baseline-manifest.json`。
- Janus/Repligate 2023-02/03/04 HTML 已保存；最初 385 个带媒体组、494 个媒体引用，后续补收文字候选。原始作者帖不自动视作 Bing 会话。
- Bing Moments、LessWrong、Willison、V2EX 等种子页已保存，引用链适配器已实现并产生 `reference_page`。
- 所有当前索引的 raw source 和 asset hash 最近一次完整检查通过：`reports/archive-integrity.json`。

### Reddit、相册和媒体恢复

- Arctic 可用区间枚举已完成的可用 listing：
  - r/bing early：754 posts
  - r/bing transition：2,292 posts
  - r/bing Mar–Apr：5,261 posts
  - r/BINGChat early：5 posts
  - r/BINGChat transition：36 posts
  - r/BINGChat Mar–Apr：67 posts
  - r/freesydney transition：67 posts
  - r/freesydney Mar–Apr：139 posts
- r/ChatGPT、r/singularity 的部分关键词窗口受到 429/服务冷却影响，coverage 明确标为 partial；不能宣称全量。
- 评论树采集会保留 parent/reply/more 节点、正文、外链和相册顺序；未完成任务继续留队列。
- Imgur layout/blog 的嵌入 JSON 已解析，不执行网页 JavaScript；相册顺序和父帖关系保留。
- 对具体失效媒体 URL 已尝试公开 Wayback 捕获；20 张截图恢复并解码成功，更多 URL 的 captures 仍在队列或已记录 no_capture。
- Imgur 主机已经连续 5 次返回 429，本轮任务已有限转为 `deferred_source_unavailable`。不要在换机后无限重试；先处理缓存、Reddit CDN、Wayback 和其他 allowlist 来源。

### 重建、校准和防泄漏

- 内部档案使用 `messages/parts`，最终 ShareGPT 用确定性文本投影；UI 搜索状态、建议按钮和来源列表不自动进入训练文本。
- 派生连续片段不会跨缺图、不可读消息或角色缺口拼接；父内容 hash 改变后旧片段会标 `superseded`。
- 同父片段、同 source、转载、精确回复复用、近重复候选会进入同一 split 组；文本片段即使没有共享图片也不能跨 split。
- 长图会分窗口核验，要求所有导出消息有当前版本覆盖；越界 bbox 可以保守放宽为整张输入图，但必须标记未精确定位，不能猜坐标。
- 当前 verify 提示词已经校准为 `calibrated-v1.5`：核验历史转写保真，不纠正原始 Bing 的事实错误；明确区分片段边界和内部漏轮。
- 代理视觉检查记录在 `data/review/negative-calibration-reviewed.json`（10 组、11 张图）；它不是人工金标准。当前校准汇总见 `reports/P4-calibration-current.json`。
- 已知 task confusion、authenticity、UI 边界和模型误报都留在 review/quarantine，不自动提升为 pass。

## 试点和当前导出

固定 P3 试点 manifest：`reports/P3-pilot-manifest.json`。

- 500 个候选组已固定：441 个核心期、59 个 transition；配额不足的来源已明确记录并重新分配。
- 当前试点处理报告：`reports/P3-pilot-processing.json`。
- 试点当前处理快照：326 组转写、154 组明确无关、20 组转写失败隔离。
- 204 组有重建记录，59 组曾满足结构性 ShareGPT 门槛；这两个数字都不等于最终通过核验或独立历史会话数。
- 59 个早期基线定向重转写中，58 成功、1 隔离；报告见 `reports/early-baseline-processing.json`。
- 作者存档补收 99 个 text-only 候选，其中 18 个核心早期候选；分类/转写仍需继续。

已冻结的导出：

- `exports/pilot-20261105-v1`：已撤回，原因是 UI 边界错误。
- `exports/pilot-20261105-v2`：2 条，provisional。
- `exports/pilot-20261105-v3`：14 条，provisional。
- `exports/pilot-20261105-v4`：22 条，`early_probable`，train 19、val 1、test 2，86 个完整问答轮次；全量冻结文件校验通过，所有 22 条 `pending_human`。

v4 不是最终数据集：

- 同一个历史会话可能有多个 source/reconstruction ID；v4 的旧字段曾把不同 parent record ID 称为 independent parent conversations，已在 `reports/pilot-v4-statistics-erratum.md` 说明。
- split group 数量也不等于已确认独立原会话数。
- v4 没有真人金标准审核；只通过了自动结构/证据/hash/split 检查。
- 不要覆盖冻结 release；应生成新 release ID。

## 换机步骤

1. 完整复制项目目录，尤其是：`data/`、`state/`、`runs/`、`reports/`、`exports/`、`config/`、`prompts/`、`schemas/`、`src/`、`tests/`、`docs/`、`pyproject.toml`、`uv.lock`、`AGENTS.md`。不要只复制 `src/`。
2. 单独安全复制 `.env.local`，设置 `0600`；不要把它放进交接文档或压缩包的公开部分。
3. 在新电脑执行：

   ```bash
   cd /path/to/newbing
   uv sync
   uv run newbing doctor --offline
   uv run newbing status
   uv run pytest -q --junitxml=reports/offline-tests-new-machine.xml
   ```

4. 首先阅读：`AGENTS.md` → `docs/PROGRESS.md` → `docs/EXECUTION_PLAN.md` → `docs/RESOURCES.md` → `docs/DATA_CONTRACTS.md` → `docs/SHAREGPT_EXPORT.md` → `docs/RUNBOOK.md` → `docs/DOCUMENT_AUDIT.md` → 本文件。
5. 检查 `config/pipeline.toml` 的 `planning_only=false`、预算 epoch、模型端点和当前 SQLite 数量。不要通过重启清空预算。
6. 用 `state/runtime-checkpoint.json` 只作线索，实际检查进程和 SQLite lease。当前交接前无活跃进程；如果新机启动后发现 `leased`，先运行 `uv run newbing resume`，确认 owner PID 不存在或 lease 确实属于失联 worker，再继续。
7. 新机第一批建议只运行一个小的、可回收的命令，确认模型/媒体/SQLite 正常后再扩大；不要并行启动多个覆盖同一队列的 worker。

## 推荐下一步顺序

### 1. 恢复并审计当前队列

```bash
uv run newbing status
uv run python scripts/failure_report.py
uv run python scripts/archive_integrity.py
uv run pytest -q --junitxml=reports/offline-tests-new-machine.xml
```

先区分 `pending/retry_wait/quarantined/deferred_source_unavailable/superseded_input`。失败不等于原对话不存在，不能批量删除失败 evidence。

### 2. 完成当前模型/转写批次

优先顺序：

1. 早期核心窗口和 59 组早期基线重转写；
2. 固定 500 组试点剩余的 current input；
3. 当前 verify contract 下的完整多轮会话；
4. 已知原帖有额外图片/文字的 known-source context cohort；
5. 作者 text-only 99 组；
6. 后续 Mar–Apr 和其他来源。

新媒体或 source text 到达后，要用 evidence-bound task 重新入队；不要沿用缺图时的分类或旧 transcribe/verify pass。当前代码会把旧输入标成 `superseded_input`。

### 3. 重建和核验

每批 worker 完成后按顺序执行：

```bash
uv run newbing reconstruct
uv run newbing dedupe
uv run newbing verify --sample 150 --workers 2
uv run newbing repair --limit 30
```

修订后必须再次重建和整段核验。旧 verify pass 只有在 conversation hash、source/asset hash、prompt/schema/预处理合约完全匹配时才可复用。

### 4. 完成 P4 校准

目标是 50 组分层校准和至少 10 组否定/不确定检查。报告必须分别给：

- 同模型 evidence consistency check；
- agent visual check；
- 真人审核（当前仍为 0）；
- 不能把模型一致性当人工准确率。

继续使用 `scripts/calibration_report.py`，对 emoji、代码、引用、缺图、片段边界、真实回答错误和 UI 通知分别统计。

### 5. 完成 P6 来源覆盖

更新 `scripts/coverage_report.py` 和 `reports/P6-coverage.md`。未完成项包括：

- ChatGPT/singularity 的部分窗口仍受 Arctic 429 限制；
- Reddit comments 尚有 pending；
- Wayback/Imgur/Reddit CDN 的可用性和失败清单仍需收尾；
- 作者文本候选需要分类和转写；
- 所有来源必须标记 `complete_for_available_listing`、`partial`、`diminishing_returns`、`unavailable` 或 `resource_limited`，不能把 429/404 写成 exhausted。

### 6. 生成最终 release 前的 P7/P8 审计

不要把 v4 直接改名或覆盖。最终 release 至少要有：

- 全量 ShareGPT JSON arrays：early_strict / early_probable / later_supplement 的 train/val/test；
- `archive/conversations.all.jsonl`、`provenance.jsonl`、`exclusions.jsonl`、`splits.json`、`manifest.json`；
- `verification_contract.json`、`statistics.json`、`DATASET_CARD.md`、`review/index.html`；
- reproduction 目录（配置、prompt、schema、代码/依赖 hash，不含密钥）；
- 每条消息当前版本证据和核验覆盖；
- 轮数、完整问答数、派生片段数、不同 parent record 数、已确认独立会话数（无法确认时写 unknown）、split group 和排除理由；
- `reports/FINAL_REPORT.md`，逐项对应 P0–P8 和 [P0-P8 未完成审计](../reports/P0-P8-audit-in-progress.md)。

最终 release 必须在所有 tier 排除已知未解决转写、角色、上下文、必要视觉依赖和真实性争议；early_strict 另排除日期未知/仅风格推断/原会话缺口。全部 tier 仍可包含历史 Bing 自身的事实错误，因为目标是忠实历史数据，不是纠错答案。

## 不能做的事

- 不要读取、打印、提交或上传 `.env.local` 内容。
- 不要把 API Authorization 发送到媒体主机、重定向主机或外部日志。
- 不要切换模型、端点、Responses 协议或自动提高资源上限。
- 不要把 22 条 v4 试验样本当作最终数据集，不要把 raw rows/assets/posts/replies 当独立对话数。
- 不要把模型 self-report、截图中可见的“system prompt”、CoT 或 roleplay 文本提升为真实隐藏 system 指令。
- 不要为了满足两轮门槛拼接独立单轮、复制 prefix、补造用户问题或把图片依赖改写成文字描述。
- 不要在 Imgur/Arctic/Wayback 429 后无限换 URL 重试；读取 `reports/source-access-boundary.json` 和 `reports/failures-current.json`。
- 不要删除原始 JSON/HTML/图片、失败响应、quarantine 或 superseded 记录；它们是可追溯证据。

## 完成条件

只有以下证据全部具备，才可以把 goal 标为 complete：

- P0–P8 每个阶段有实际通过/降级/阻塞证据；
- 已启用来源都有准确终态和覆盖限制；
- 原始档案、标准对话、ShareGPT、review、sidecar、split、reproduction 都可在新机打开；
- 当前版本所有保留消息有证据和核验覆盖；
- 全部 tier 无已知未解决转写/角色/上下文错误；
- 相关/转载/派生/近重复不会跨 split；
- 最终 release 的所有 JSON 数组和 sidecar 通过 `validate`；
- `reports/FINAL_REPORT.md` 完成；
- 至少有一条真正合格的 ShareGPT 多轮样本，但这个最低条件不能替代其余范围、质量和覆盖要求；
- 未完成项、未知数量、模型 usage 缺口和人工审核状态都清楚写出。

当前状态不满足上述条件，goal 必须保持 active。
