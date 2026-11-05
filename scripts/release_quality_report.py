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
 rows.append({'release_id':rid,'status':m['status'],'samples':m['samples'],'validation_passed':validation['passed'],
              'by_tier':m.get('by_tier'),'by_split':m.get('by_split'),'human_verified':sum(r.get('human_status')=='human_verified' for r in side),
              'validation_scope':'frozen_version_contract' if m.get('verification_contract_hash') else 'legacy_artifact_checks_only_not_current_admission',
              'human_pending':sum(r.get('human_status')=='pending_human' for r in side),
              'source_evidence_paths':'Every sample retains message projection map, source/asset identities, original source date evidence, verification reference and split group in provenance.jsonl.'})
report={'at':now(),'releases':rows,'final_release':None,'goal_status':'active','training_performed':False,'published':False}
atomic(p.root/'reports/P7-quality-current.json',report)
text='# P7 ShareGPT 质量检查点\n\n'+report['at']+'\n\n'
text+='已冻结试验版的全量结构、多轮、角色交替、证据/hash、核验版本与split检查如下。所有provisional仍是自动检查候选，不是完整目标完成或真人确认数据。\n\n'
for r in rows:text+='- '+json.dumps(r,ensure_ascii=False)+'\n'
text+='\n本项目至少两轮，即4条真实连续交替消息；不补问题、不拼独立单轮、不合成图片描述。所有tier排除已知未解决转写/角色/上下文问题，日期确认与转写质量分开。上游HF train/test未直接沿用，同源/派生/转载/近重复按组划分。\n\n'
text+='待完成：当前完整语料继续证据核验/更正、新来源会话层去重和最终分层抽样；生成最终release、报告准确轮数/问答数/独立原会话与派生片段/日期/来源分布/排除原因/未知范围。不能用小试验版或局部校验替代P0–P8完成审计。\n'
atomic(p.root/'reports/P7-quality.md',text,raw=True)
print({'releases_checked':len(rows),'validated_current_release_candidates':[r['release_id'] for r in rows if r.get('validation_passed')]})
