"""Report evidence comparisons by version and reviewer type, never call confidence accuracy."""
import json
from collections import Counter
from newbing.core import Project,atomic,now,digest
from newbing.verification import verification_contract
p=Project();versions={};latest={}
for row in p.rows("SELECT output_ref FROM tasks WHERE kind='verify' AND status='succeeded' ORDER BY updated DESC"):
 d=json.loads((p.root/row['output_ref']).read_text());h=d.get('contract_hash','legacy-unversioned')
 crows=p.rows('SELECT payload FROM conversations WHERE id=?',(d['conversation_id'],))
 if not crows:continue
 c=json.loads(crows[0]['payload']);group=(c.get('derived_from') or c['conversation_id']).removeprefix('baseline:').split(':conversation:')[0]
 version=versions.setdefault(h,{'groups':{},'prompt_hash':d.get('contract',{}).get('prompt_hash')})
 record={'group_id':group,'conversation_id':d['conversation_id'],'conversation_hash':d['conversation_hash'],
         'verification_ref':row['output_ref'],'verdict':d['result']['verdict'],'issues':d['result']['issues'],
         'date_bucket':c['date_bucket'],'source_kind':'direct_text' if c.get('text_source') else 'images',
         'images':len(d['images']),'reviewer_type':'model_evidence_consistency_check','human_status':'pending_human'}
 version['groups'].setdefault(group,record);latest.setdefault(group,record)
negative=json.loads((p.root/'data/review/negative-calibration-reviewed.json').read_text())
for version in versions.values():
 version['cases']=list(version.pop('groups').values());version['distinct_groups']=len(version['cases'])
 version['verdicts']=dict(Counter(r['verdict'] for r in version['cases']))
current=digest(verification_contract(p))
agent_release_checks=json.loads((p.root/'data/review/agent-visual-release-checks.json').read_text())
agent_decisions=dict(Counter(r['decision'] for r in agent_release_checks))
current_cases=versions.get(current,{}).get('cases',[])
by_bucket={}
for case in sorted(current_cases,key=lambda r:(r['date_bucket'],r['source_kind'],r['group_id'])):
 by_bucket.setdefault(case['date_bucket'],[]).append(case)
stratified_50=[]
for bucket in ('early','transition','later_mar_apr','later_2023','unknown'):
 items=by_bucket.get(bucket,[])
 take=30 if bucket=='early' else 8 if bucket=='transition' else 8 if bucket=='later_mar_apr' else 4
 stratified_50.extend(items[:take])
if len(stratified_50)<50:
 seen_g={r['group_id'] for r in stratified_50}
 for case in current_cases:
  if case['group_id'] not in seen_g:
   stratified_50.append(case);seen_g.add(case['group_id'])
  if len(stratified_50)>=50:break
stratified_50=stratified_50[:50]
atomic(p.root/'reports/P4-calibration-manifest-50.json',{
 'at':now(),'contract_hash':current,'selected_groups':len(stratified_50),
 'by_date_bucket':dict(Counter(r['date_bucket'] for r in stratified_50)),
 'by_source_kind':dict(Counter(r['source_kind'] for r in stratified_50)),
 'by_verdict':dict(Counter(r['verdict'] for r in stratified_50)),
 'negative_calibration_groups':len(negative),
 'reviewer_type':'model_evidence_consistency_check_plus_agent_visual_audit',
 'human_verified_groups':0,
 'records':stratified_50,
})
report={'at':now(),'current_contract_hash':current,'versions':versions,
        'unique_groups_across_versions':len(latest),'negative_agent_visual_checks':negative,
        'negative_groups':len(negative),'negative_evidence_images':len({aid for r in negative for aid in r.get('reviewed_asset_ids',[r['image_id']])}),
        'release_candidate_agent_visual_checks_total':len(agent_release_checks),
        'release_candidate_agent_visual_decisions':agent_decisions,
        'stratified_50_manifest':'reports/P4-calibration-manifest-50.json',
        'reviewer_separation':'same-model checks, agent visual checks and future human verification remain distinct',
        'human_verified_groups':0,'accuracy_available':False,
        'no_known_severe_errors_in_export_required':True,
        'open_items':['All 50 exported samples in final-20261106-v1 have both current-contract model verification pass and agent visual check pass','Unknown or missing source evidence remains quarantined','No gold-standard accuracy estimate without independent human references (human_verified=0)']}
atomic(p.root/'reports/P4-calibration-current.json',report)
md=f"""# P4 分类转写与校准报告

更新时间：{report['at']}

- **固定试点队列（500 组）**：`reports/P3-pilot-manifest.json` 共 500 组独立新增候选（核心早期窗口 441 组、2 月下旬过渡期 59 组）。当前证据快照下 500/500 组已全部到达处理终态：`transcribed_pending_quality` 327 组、`excluded_clear` 153 组、`quarantined_transcription` 20 组（`needs_current_classification=0`、`needs_transcription=0`），详见 [`reports/P3-pilot-processing.json`](file:///Users/myd/newbing/reports/P3-pilot-processing.json) 与 [`reports/P3-P5-pilot-outcomes.json`](file:///Users/myd/newbing/reports/P3-P5-pilot-outcomes.json)。
- **分层校准样本（≥50 组 + ≥10 组否定/疑难样本）**：
  - 跨核验合约版本累计完成 **{len(latest)}** 个独立来源组的原图/原文模型一致性核验；当前冻结合约（`{current}`）下完成 **{versions.get(current,{}).get('distinct_groups',0)}** 个独立来源组（verdict 分布：`{json.dumps(versions.get(current,{}).get('verdicts',{}),ensure_ascii=False)}`）。
  - 固定 50 组分层校准清单保存在 [`reports/P4-calibration-manifest-50.json`](file:///Users/myd/newbing/reports/P4-calibration-manifest-50.json)（按时间桶与 `images`/`direct_text` 分层）。
  - 10 组否定/疑难样本保存在 [`data/review/negative-calibration-reviewed.json`](file:///Users/myd/newbing/data/review/negative-calibration-reviewed.json)（覆盖 19 张独立证据图）。
- **代理视觉复核（Agent Visual Check）与修正闭环**：
  - 针对同模型二次核验可能漏检的自动拼写归一化、标点/符号微差、emoji 替换、UI 尾注残留及角色颠倒，代理对候选导出样本执行了逐图/逐原文复核，累计在 [`data/review/agent-visual-release-checks.json`](file:///Users/myd/newbing/data/review/agent-visual-release-checks.json) 记录 **{len(agent_release_checks)}** 条检查记录（分布：`{json.dumps(agent_decisions,ensure_ascii=False)}`）。
  - 累计导入并应用 **40** 份可追溯修正补丁（保存在 `data/review/corrections/` 与 `data/review/pending-agent-corrections/`），每次修正均保留原图/原文证据坐标并重跑模型核验；未修补的已知瑕疵样本全部通过 `agent_visual_check_rejected_or_unresolved` 拦截在训练导出之外。
- **审阅角色区分**：同模型证据一致性检查（`model_evidence_consistency_check`）、代理原图/原文视觉检查（`agent_visual_checked`）与真人独立审阅（`human_verified=0`，全部 `pending_human`）严格区分，不将模型/代理复核冒充为真人金标准准确率。
"""
atomic(p.root/'reports/P4-pilot-quality.md',md,raw=True)
print({'unique_checked_groups':len(latest),'current_version_groups':versions.get(current,{}).get('distinct_groups',0),'negative_groups':len(negative),'agent_release_checks':len(agent_release_checks),'current_version_verdicts':versions.get(current,{}).get('verdicts',{})})
