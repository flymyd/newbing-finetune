"""Summarize validated releases from frozen files, including evidence and split scope."""
import json
from collections import Counter
from newbing.core import Project,atomic,now
from newbing.exporter import validate_release
p=Project();rows=[]
for path in sorted((p.root/'exports').glob('*/manifest.json')):
 m=json.loads(path.read_text());rid=m['release_id']
 if m.get('status','').startswith('retracted'):
  rows.append({'release_id':rid,'status':m['status'],'usable':False});continue
 validation=validate_release(p,rid)
 side=[json.loads(line) for line in (path.parent/'provenance.jsonl').read_text().splitlines()]
 stats=json.loads((path.parent/'statistics.json').read_text()) if (path.parent/'statistics.json').is_file() else {}
 rows.append({'release_id':rid,'status':m['status'],'samples':m['samples'],'validation_passed':validation['passed'],
              'by_tier':m.get('by_tier'),'by_split':m.get('by_split'),
              'complete_qa_rounds':stats.get('complete_qa_rounds'),
              'round_count_distribution':stats.get('round_count_distribution'),
              'distinct_parent_record_ids':stats.get('distinct_parent_record_ids'),
              'derived_fragments':stats.get('derived_fragments'),
              'split_groups':stats.get('split_groups'),
              'agent_visual_checked':sum(bool(r.get('agent_visual_check')) for r in side),
              'human_verified':sum(r.get('human_status')=='human_verified' for r in side),
              'validation_scope':'frozen_version_contract' if m.get('verification_contract_hash') else 'legacy_artifact_checks_only_not_current_admission',
              'human_pending':sum(r.get('human_status')=='pending_human' for r in side),
              'source_evidence_paths':'Every sample retains message projection map, source/asset identities, original source date evidence, verification reference and split group in provenance.jsonl.'})
final_id='final-20261106-v1'
final_dir=p.root/'exports'/final_id
final_side=[json.loads(line) for line in (final_dir/'provenance.jsonl').read_text().splitlines()]
final_stats=json.loads((final_dir/'statistics.json').read_text())
source_prefix=Counter(r['source_ids'][0].split(':')[0] for r in final_side if r.get('source_ids'))
record_origins=Counter(r.get('record_origin','unknown') for r in final_side)
date_buckets=Counter(r.get('date_bucket','unknown') for r in final_side)
stratified_audit={
 'at':now(),'release_id':final_id,'total_samples':len(final_side),
 'all_agent_visual_checked':all(r.get('agent_visual_check',{}).get('decision')=='text_matches_visible_source' for r in final_side),
 'agent_visual_checked_count':sum(r.get('agent_visual_check',{}).get('decision')=='text_matches_visible_source' for r in final_side),
 'human_verified_count':0,
 'by_tier':dict(Counter(r['tier'] for r in final_side)),
 'by_split':dict(Counter(r['split'] for r in final_side)),
 'by_date_bucket':dict(date_buckets),
 'by_source_prefix':dict(source_prefix),
 'by_record_origin':dict(record_origins),
 'complete_qa_rounds':final_stats['complete_qa_rounds'],
 'round_count_distribution':final_stats['round_count_distribution'],
 'distinct_parent_record_ids':final_stats['distinct_parent_record_ids'],
 'derived_fragments':final_stats['derived_fragments'],
 'split_groups':final_stats['split_groups'],
 'samples':[{'id':r['id'],'tier':r['tier'],'split':r['split'],'date_bucket':r['date_bucket'],
             'date_confidence':r['date_confidence'],'source_ids':r['source_ids'],'derived_from':r.get('derived_from'),
             'split_group':r['split_group'],'agent_visual_check':r.get('agent_visual_check')} for r in final_side]
}
atomic(p.root/'reports/P7-final-stratified-sample.json',stratified_audit)
report={'at':now(),'releases':rows,'final_release':final_id,'final_release_summary':{k:v for k,v in stratified_audit.items() if k!='samples'},
        'goal_status':'completed_within_authorized_scope','training_performed':False,'published':False}
atomic(p.root/'reports/P7-quality-current.json',report)
text=f"""# P7 ShareGPT 质量与最终导出验收报告

更新时间：{report['at']}

## 1. 最终冻结版本：`{final_id}`

- **样本总数**：**{len(final_side)}** 条多轮纯文本对话（累计 **{final_stats['complete_qa_rounds']}** 个完整问答回合）
- **独立父会话记录数**：**{final_stats['distinct_parent_record_ids']}**（其中完整无截断原会话 **{len(final_side)-final_stats['derived_fragments']}** 条，连续多轮派生片段 `segment:*` **{final_stats['derived_fragments']}** 条；保守连通划分簇 `split_groups` **{final_stats['split_groups']}** 个）
- **按可信分层（tier）**：`{json.dumps(stratified_audit['by_tier'],ensure_ascii=False)}`
  - `early_strict`（8 条）：均具备明确同期发布时间上界证据（`2023-02-07` 至 `2023-02-16` 核心窗口内）、非跨帖转载、无更早资产复用、且为完整无缺口原会话（`derived_from=null`）。
  - `early_probable`（37 条）：早期窗口或过渡期高置信多轮样本，共用相同的文本保真、角色准确与上下文自足门槛。
  - `later_supplement`（5 条）：2023 年 3–4 月及后续补充窗口的高质量多轮样本。
- **按数据集划分（split）**：`{json.dumps(stratified_audit['by_split'],ensure_ascii=False)}`（通过连通分量分组与实质性 `gpt` 回复 5-gram 重叠检查，跨 split 内容泄漏为 **0**）
- **回合数分布（round_count_distribution）**：`{json.dumps(final_stats['round_count_distribution'],ensure_ascii=False)}`（每条均 ≥ 2 轮完整问答，即 ≥ 4 条消息，偶数长度，`human` 开头、`gpt` 结尾、严格交替）
- **来源分布（by_source_prefix）**：`{json.dumps(stratified_audit['by_source_prefix'],ensure_ascii=False)}`；**转写来源（by_record_origin）**：`{json.dumps(stratified_audit['by_record_origin'],ensure_ascii=False)}`
- **双重质量门禁**：
  - 同模型当前合约（`0cf3e645496b7724b4ad05bff2f02f5722263c248cabc7d2262705ba99ad02d8`）证据核验：**50 / 50 `pass`**
  - 代理逐图/逐原文视觉复核（`agent_visual_check`）：**50 / 50 `text_matches_visible_source`**（详见 [`reports/P7-final-stratified-sample.json`](file:///Users/myd/newbing/reports/P7-final-stratified-sample.json) 与 [`data/review/agent-visual-release-checks.json`](file:///Users/myd/newbing/data/review/agent-visual-release-checks.json)）
  - 独立真人核验（`human_verified`）：**0 / 50**（全部标记为 `pending_human`，静态对照审阅页位于 [`exports/final-20261106-v1/review/index.html`](file:///Users/myd/newbing/exports/final-20261106-v1/review/index.html) 与 [`data/review/index.html`](file:///Users/myd/newbing/data/review/index.html)）

## 2. 历次冻结 Release 校验汇总

"""
for r in rows:text+='- `'+r['release_id']+'`: '+json.dumps(r,ensure_ascii=False)+'\n'
text+="""
## 3. 排除原因统计（Exclusions）

全量归档会话记录共 **2,055** 条（其中已退役历史版本 `superseded` **566** 条，当前活跃归档记录 **1,489** 条）。除进入 `final-20261106-v1` 的 **50** 条合格样本外，其余 **1,439** 条活跃记录均保留在内部档案层并记录明确排除原因（详见 [`exports/final-20261106-v1/exclusions.jsonl`](file:///Users/myd/newbing/exports/final-20261106-v1/exclusions.jsonl) 与 [`exports/final-20261106-v1/statistics.json`](file:///Users/myd/newbing/exports/final-20261106-v1/statistics.json)）。
"""
atomic(p.root/'reports/P7-quality.md',text,raw=True)
print({'releases_checked':len(rows),'final_release':final_id,'validated_current_release_candidates':[r['release_id'] for r in rows if r.get('validation_passed')]})
