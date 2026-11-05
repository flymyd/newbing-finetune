# P7 ShareGPT 质量检查点

2026-11-05T13:19:16.343295+00:00

已冻结试验版的全量结构、多轮、角色交替、证据/hash、核验版本与split检查如下。所有provisional仍是自动检查候选，不是完整目标完成或真人确认数据。

- {"release_id": "pilot-20261105-v1", "status": "retracted_pending_ui_boundary_correction", "usable": false}
- {"release_id": "pilot-20261105-v2", "status": "provisional_collection_continues", "samples": 2, "validation_passed": true, "by_tier": {"early_probable": 2}, "by_split": {"train": 2}, "human_verified": 0, "human_pending": 2, "source_evidence_paths": "Every sample retains message projection map, source/asset identities, original source date evidence, verification reference and split group in provenance.jsonl."}
- {"release_id": "pilot-20261105-v3", "status": "provisional_collection_continues", "samples": 14, "validation_passed": true, "by_tier": {"early_probable": 14}, "by_split": {"train": 14}, "human_verified": 0, "human_pending": 14, "source_evidence_paths": "Every sample retains message projection map, source/asset identities, original source date evidence, verification reference and split group in provenance.jsonl."}
- {"release_id": "pilot-20261105-v4", "status": "provisional_collection_continues", "samples": 22, "validation_passed": true, "by_tier": {"early_probable": 22}, "by_split": {"train": 19, "test": 2, "val": 1}, "human_verified": 0, "human_pending": 22, "source_evidence_paths": "Every sample retains message projection map, source/asset identities, original source date evidence, verification reference and split group in provenance.jsonl."}

本项目至少两轮，即4条真实连续交替消息；不补问题、不拼独立单轮、不合成图片描述。所有tier排除已知未解决转写/角色/上下文问题，日期确认与转写质量分开。上游HF train/test未直接沿用，同源/派生/转载/近重复按组划分。

待完成：当前完整语料继续证据核验/更正、新来源会话层去重和最终分层抽样；生成最终release、报告准确轮数/问答数/独立原会话与派生片段/日期/来源分布/排除原因/未知范围。不能用小试验版或局部校验替代P0–P8完成审计。
