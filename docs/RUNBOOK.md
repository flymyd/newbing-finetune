# 运行、恢复与质量手册

以下是目标操作规格。**目录已有部分 CLI、数据库、测试和历史报告，完整符合性尚未验收。** 文档核查后用户已恢复 active goal，按已有证据继续执行。当前授权边界以 AGENTS 和 PROGRESS 顶部为准，历史命令/报告不自动授权恢复。

## 1. 启动与阶段衔接

用户授权执行后：

1. 阅读 PROGRESS 的 next_action，检查当前文件/配置，不重新做已经完成且有证据的下载或推理。
2. P0 用最小探测脚本建立 run 目录和脱敏 JSONL 调用账本；P1 成形后将 P0 账本幂等导入 SQLite。P0 不依赖尚未实现的完整 CLI。
3. 每阶段完成更新 PROGRESS 和 reports/Pn-*.md；达到自动验收条件就继续。每批次持久化，不等一个大阶段结束才写状态。
4. 一个来源不可达就按资源手册切换；人工待审样本隔离并继续其他任务。模型不可达时继续归档，不伪造分类结果。

## 2. 计划 CLI 合约

命令入口多数已存在；本表表示目标合约，不保证每个参数和恢复语义均已通过验收。后续执行前用 `--help`、离线 fixture 和实际输出核对，不能原样把本表当通过证明。

| 命令形状 | 职责 |
| --- | --- |
| `uv run newbing doctor --offline` | 配置、磁盘、schema、凭据存在性，默认不发网络 |
| `uv run newbing probe --profile flymyd` | P0 能力探测的可重复入口，真实请求有界 |
| `uv run newbing import-baseline --source <id>` | 固定 revision 元数据和资产清单 |
| `uv run newbing discover --source <id> --window <bucket>` | 保存原始索引/页面，发现候选与媒体 |
| `uv run newbing ingest-dump --path <file> --source <id>` | 流式导入离线 dump，按配置筛选 |
| `uv run newbing references --limit <n>` | 从种子/作者页发现有界引用链，保存来源与失败状态 |
| `uv run newbing download --limit <n>` | 执行媒体队列，不调用 LLM |
| `uv run newbing classify --limit <n>` | 分类候选，缓存命中不重复收费 |
| `uv run newbing transcribe --limit <n>` | 原图/裁片转写与校验 |
| `uv run newbing reconstruct --limit <n>` | 对齐重叠、建立会话与关系 |
| `uv run newbing verify --sample <n>` | 模型一致性/结构核验，另输出真人待审 |
| `uv run newbing review build` | 生成无外部依赖的本地审阅 HTML |
| `uv run newbing review import --path <decisions>` | 导入审阅决定，记录作者类型和证据 |
| `uv run newbing export --release <id>` | 按 config 导出 ShareGPT 纯文本多轮 JSON 数组，另存 archive/split/sidecar |
| `uv run newbing validate --release <id>` | 全量 ShareGPT schema、多轮语义、证据与泄漏检查 |
| `uv run newbing status` | 任务、来源、资源、失败分布 |
| `uv run newbing resume --run <id>` | 按项目恢复已失联/无主过期 lease；run 参数仅作审计标签，不限制到单个 run |

目标要求批处理命令支持不触网、不派发推理的 `--dry-run`。现已为所有 CLI 入口提供 --dry-run：先读取 TOML 和只读 SQLite 队列快照，直接返回，不构造 Project、不入队、不读密钥、不联网；离线检查覆盖此路径。预览不是完整发现结果或精确 token 预估。不得对未知命令试运行以探测行为。`comments`、`recover-baseline-posts`、`recover-media`、`dedupe`、`repair` 也已有入口，属于后续执行范围；repair 会调用模型，不能当成纯本地文本替换。带 limit 不绕过跨 run 累计预算。

## 3. 任务状态、缓存与恢复

状态：pending → leased → succeeded；可恢复错误进入 retry_wait，重试到时回 pending；不再重试的失败进入 failed_terminal；内容不确定/模型拒绝进入 quarantined；资源截止的未派发任务进入 deferred_resource。另有 suppressed_known_baseline 表示与已知基线来源相同、未重复分类的任务，不是成功转写或新增数据。禁止把 failed/quarantine/suppressed 计为成功转写。

- task identity = stage + logical input hash + schema/prompt/preprocess/model 参数；数据库唯一约束防重复入队。
- lease 到期只说明工作者失联，恢复前先查原子输出/回执是否已写；成功结果写完再改 succeeded。
- SQLite 单写入/短事务；数据文件先同目录临时文件，校验后 rename。不要跨卷假定 rename 原子。
- 大文件 Range 续传仅在 ETag/Last-Modified 与服务器支持匹配时进行；不匹配就重新开始并计入重下字节；哈希完成后才入资产库。
- 请求结果写入后按缓存键索引，修改提示词只重跑受影响阶段；原始 HTTP/模型输出不会被覆盖。
- 模型请求网络超时后可能已在上游执行；记录 unknown_outcome，有限重试，不能宣称绝对只计费一次。
- 新执行 run 读取同一项目累计资源账本；不能通过重启清零。用户明确开启新预算周期时记录 budget_epoch 与理由。

## 4. 错误处理

| 情况 | 动作 | 最大尝试 |
| --- | --- | --- |
| 网络临时错误、429、5xx | 指数退避 + jitter，尊重 Retry-After，降低并发 | 同逻辑请求共 3 次（含首次） |
| 401/403 | 记录身份/来源路径错误，不原样循环 | 原样请求 1 次；最多 2 个有意义替代路径 |
| 404/410、媒体占位图 | 记媒体失效，尝试具体 URL 的存档/转载 | 原地址 1 次，最多 2 条替代路径 |
| 400 参数不兼容 | capability 更新，明确移除不支持参数或改图像分组 | 同参数不重试 |
| 200 但 HTML 非图 | 解码/MIME 错误，不入原图库 | 不盲重试 |
| 无效 JSON/schema | 保存输出与错误，可作一次格式纠正 | 原推理后最多 1 次纠正 |
| incomplete / max_output_tokens | 一次提高输出上限或拆分输入 | 不超过 task 总尝试 3；仍失败隔离 |
| refusal | 单列拒绝，保留历史资料 | 不自动重复绕过 |
| 角色/顺序/日期矛盾 | evidence review | 一次核验仍不明就隔离 |
| 总预算/磁盘阈值 | 停止对应派发、保存检查点、继续可行整理 | 不自动扩额 |

退避基数 2 秒、上限 60 秒；供应商指定更久等待时保存 retry_at，不用长阻塞等待压住整个执行。连续 5 次模型临时错误打开断路器 5 分钟，期间做归档/整理；半开探测仍计账。不能因为退避结束就重置总尝试。

## 5. 图片处理与转写

1. 原图按字节保存；派生缩略图/裁片独立路径，有原图 hash 与坐标变换。
2. 默认长图每片高度 2,400 px、相邻重叠 240 px；宽度保留原字迹可读性。参数是初始启发式，P4 检查后调整并版本化。
3. 优先按气泡/消息边界裁切；不能在中间切开代码后直接拼成新消息。遇到超宽图分列/分块并保留几何关系。
4. 单任务最多 6 个图像输入；超限采用滑动窗口，记录 overlap，最终显式消重。模型看到的裁片顺序不等于已确认真实会话顺序。
5. 对低清、复杂表格、字符画、emoji 与代码细节优先保留“不确定”，不要用流畅句子替代模糊原文。
6. 模型核验任务必须再次提供图像/原始文本证据，不能仅让同一模型读自己的摘要然后说“正确”。

## 6. 质量检查与统计定义

每批统计下列分母，缺少分母不报百分比：

| 指标 | 定义 |
| --- | --- |
| index access rate | 成功获取的索引请求 / 索引请求尝试；另列唯一页面数 |
| media URL survival | 成功解码图片的唯一 URL / 已尝试的唯一图片 URL |
| candidate relevance | 确认为 Bing 候选组 / 已分类候选组，uncertain 独立列 |
| novelty | 相对基线/先前批次新增独立会话 / 本批已处理候选组 |
| early evidence yield | 新增 date confirmed 的 A 会话 / 本批已处理候选组 |
| usable reply count | 有充分上下文且目标完整的 assistant 回复数 |
| sharegpt eligible conversations | 纯文本投影后仍有至少两轮完整问答、上下文完整且通过当前版本核验的独立会话数，与 usable reply count 分开 |
| novel sharegpt yield | 相对基线及先前批次新增的 ShareGPT 合格独立原会话数 / 本批完成处理的候选组；derived 片段数量另列，不能当多个新增原会话 |
| unresolved sharegpt yield | 尚未完成转写/核验的候选组数，独立列出；不能计为不合格或据此宣称来源低收益 |
| multi-turn exclusion | 因不足两轮、上下文缺失、视觉依赖或其他准入问题未导出的会话数，理由可多标签 |
| transcription defect | 证据对照中出现错误的样本组 / 实际被检查的样本组 |
| duplication | 精确/近似/转载各自关系与去重后会话数量，不能混算 |
| inference load | HTTP 尝试数、逻辑任务数、cache hit、usage、unknown usage、延迟 |

P4 校准 50 组，其中至少 10 组覆盖分类否定或 uncertain（可用数据不足时如实报告）。P7 最终检查默认最多 100 组，分层覆盖 A/B/C/U、图片/文本、单图/长图、多来源和高风险项；小集全检。额外核验全部明显高风险候选，不能只随机抽样。

严重错误：编造看不见的文本/用户问题、角色反转、合错会话、把模拟对话当真实交互、错误日期确认。发现严重错误就隔离并查同类；修复后重跑受影响版本，不必无理由全库重复调用。

质量门槛：全量结构/证据检查 100% 通过；最终文件通过 [ShareGPT 输出规范](SHAREGPT_EXPORT.md) 的字段、纯文本、至少两轮及角色交替校验；全部 tier 无已知未解决转写错误；strict 另有日期和完整性要求；split 已知关联泄漏为 0。转写准确率只有存在参考真值才计算，绝不把模型 confidence 均值叫准确率。日期/来源证据不足时可降低相应 tier；文本或必要上下文未达标时必须更正复验或隔离，不为了通过率删除原始资料。

## 7. 主要离线测试

- Reddit gallery 以 gallery_data 顺序为准，HTML `&amp;` 地址正确解码，crosspost 和正文外链不遗漏。
- 时间分页边界、同秒多帖、UTC/原时区转换不漏记录；分页 token 循环能发现。
- 重复下载/任务重试/过期 lease/部分文件写入后的恢复，不产生虚假成功或重复推理任务。
- 429/5xx 退避、401/403 终止、200 非图、超大/损坏图片、路径与字节上限。
- Responses 多个 output item、refusal、incomplete、空输出、无 usage、结构化回退、缓存版本失效。
- 预算含重试和在途预留；中断重启仍计累计；日志不含凭据。
- 长图重叠正确去重、缺失 prompt 保持缺失、角色不明不猜、原始 emoji/代码不变造。
- 转帖/相同会话不跨 split，短模板句不会把无关会话误合并，合成数据不进入历史严格集。
- ShareGPT 结构与语义：单轮/空白 value/对象型 value/未知角色/错误开结尾/奇数长度/连续同角色必须被拒；仅靠结构 schema 不能验证角色顺序，须有语义检查。
- 媒体依赖、UI 去除后少于两轮、中间缺口不能跨越拼接；原文代码中的 `<image>` 字面词不误杀，新增媒体占位不得通过。
- export id 唯一、各 tier 互斥、所有消息有 sidecar 映射；未默认逐回复生成重复前缀；空 split 与全空 release 的结论区分。
- 无共享图片的同父纯文本片段仍同 split；完整父会话与其重叠片段不重复导出。父内容或投影变更后，旧片段和旧核验不能继续通过。
- 超过 6 张输入的长会话逐窗口核验，并检查所有导出消息的覆盖；不得静默跳过后声称全部候选已验收。
- 核验 pass 必须绑定实际文本/证据与 prompt/schema/投影版本；旧 pass、旧 pending 或矛盾结果不能替代当前版本核验。

## 8. 交付与恢复证据

每阶段报告包含：配置/代码/依赖版本，实际命令与退出状态，输入输出哈希，数量与分母，测试结果，未完成项，下一步。不要把计划 CLI 示意当实际命令记录。

最终包只含用户需要的产物、代码和配置引用；默认排除 `.env.local`、Authorization、cookies、base64 请求副本、临时缓存和虚假“已人工审阅”标记。本地原始档案仍保留在项目目录。公开上传和真正训练都留给后续指令。

当前 SQLite schema_version=2（新增 owner PID、主机门控和逻辑模型请求标识）；TOML 顶层 schema_version=1 是配置文件格式，两者独立。实际 named-schema 由任务 schema hash 与核验合约记录。新版模型任务把来源/图片顺序/缺图位置、prompt/schema/model 参数纳入输入身份；新证据到来会替换尚未派发的旧输入任务，原始资料与旧输出不删除。

源主机重复限流边界：当前预算周期内，某主机最近5次已结束请求均为429（`retry.max_host_consecutive_rate_limits`），说明经过有界尝试仍无法访问，当前轮该主机未派发媒体任务进入 `deferred_source_unavailable`，保留URL和原始失败证据，转向已缓存资料与有限替代来源；不每5分钟换一个URL无限重试。它不表示资料不存在，也不修改或清零请求账本。其他主机继续。新的预算/访问周期需用户明确启动，不自动扩额。
