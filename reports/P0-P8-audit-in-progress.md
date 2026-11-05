# P0–P8 未完成审计

本表用于追踪实际范围，不是完成声明。具体计数见 execution-checkpoint.json；所有模型检查与人工检查分别报告。当前 goal active，未训练/上传。

| 阶段/要求 | 已有直接证据 | 仍需证据或工作 |
| --- | --- | --- |
| P0 指定 Responses 文本/单图/多图/schema、版本/usage | P0-capabilities.md；state/capabilities.json；原始4次调用；后续恢复分类成功 | 最终报告引用实际兼容能力与未知价格，不重新泛化供应商保证 |
| P1 uv/锁定依赖/CLI/SQLite/原子写入 | pyproject.toml、uv.lock、runtime/config快照、doctor/CLI、离线测试 | 阶段完整报告与命令合约逐项核对 |
| P1 配置上限/并发/幂等/lease/重试/断点续传 | offline-tests.xml；新版 logical_key、owner PID、ETag/Range；dry-run 只读 | 跨连接下载主机门控、异常 Range 和更广故障恢复的针对性证明 |
| P2 两个固定HF基线与媒体 | 660+764原始行留档、764逻辑基线组；所有3078引用下载终态；额外1465上游字节实核无差异 | 按原始文件/媒体引用生成正式哈希与缺失清单，阶段报告与数据库数量对齐 |
| P2 Janus与种子页 | 3个月原始HTML、385带媒体组、494媒体引用；5个种子网页 | 保存引用链状态，说明并非所有媒体都是Bing；网页许可/来源字段完整性核查 |
| P3 Reddit A/B及小社区/评论/相册 | r/bing A754、B2292 可用索引枚举；BINGChat A5/B36、freesydney B67；评论树与Imgur相册；实际API状态已记录 | ChatGPT/singularity 部分关键词/窗口限流缺口；相关定向离线torrent已知地址404；持续补全评论 |
| P3 500新增候选组与配额 | P3-pilot-manifest.json：500组；441核心期、59过渡期；不足配额明确重新分配 | 完成这些组转写/重建/核验，报告分母与会话层实际新增量，不能停在候选数 |
| P4 50组+10否定/疑难校准 | 多版本原图/文本核验原始结果；negative-calibration-reviewed.json 10组代理视觉检查 | 冻结50组校准manifest，按来源分层、明确检查者；已知模型任务混淆线索定向重核；严重错误排除/更正证明 |
| P4 提示词/schema/预处理冻结与受影响缓存 | verify draft-v1.4、版本合约、逐消息覆盖；原文和模型输出分层 | 本轮已观察到“核验事实正确性而非忠实转写”的少数结果，不能直接放行；处理后再冻结适用版本 |
| P5 原文/证据/日期/parts重建 | conversations.all.jsonl；像素到原图映射；日期源分离；证据修订留档 | 继续处理新增转写、59组定向早期重转写；检查最新转写版本替换与所有消息证据路径 |
| P5 去重/lineage/split | 精确hash；5gram/dHash候选；纯文本同父分组与旧片段失效；测试 | 最终语料上重跑，人工/模型判断近似关系与保守同split分别列；报告完整会话/派生片段数量 |
| P6 按A/B→C→D补充及引用链/失效媒体 | r/bing C5261帖子枚举；公开引用链适配器；Wayback具体URL捕获补救 | 所有启用来源/窗口有有据终态，剩余关键词/小社区窗口、D定向线索、媒体及引用队列处理；收益或资源停止条件实证 |
| P7 ShareGPT结构、纯文本、至少两轮、所有目标上下文 | pilot-v3：14样本、early_probable、无校验错误；schema+语义+证据+版本+split验证 | 继续扩大高质量语料并最终验收，不能用14条试验样本替代范围要求；未解决字符/角色/上下文错误全部排除 |
| P7 冻结sidecar/manifest/review/dataset card | v3含冻结核验、provenance/exclusions/splits、逐文件hash、框架映射、对照HTML | 最终版轮数/完整问答/独立会话/排除原因、源分布、split簇/占比、自动与真人质量、最终分层抽样报告 |
| P8 重跑/恢复/本地产物索引与最终报告 | 原始档案、SQLite队列、runtime-checkpoint、运行快照、PROGRESS | 清理或明确所有旧任务终态；FINAL_REPORT.md、最终README产物入口与全要求审计；只有证据齐全才能goal complete |
