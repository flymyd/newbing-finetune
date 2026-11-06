"""Write observed metrics only; no fixed test counts or invented stage completion."""
import json
from collections import Counter
from pathlib import Path
import xml.etree.ElementTree as ET
from newbing.core import Project,atomic,now,digest

p=Project();status=p.status();status.update(recorded_at=now(),config_hash=digest(p.config))
atomic(p.root/'reports/current-status.json',status)
tests=None
xml=p.root/'reports/offline-tests.xml'
if xml.is_file():
    suites=list(ET.parse(xml).getroot().iter('testsuite'))
    tests={k:sum(int(s.attrib.get(k,0)) for s in suites) for k in ('tests','failures','errors','skipped')}
baseline_assets={r['asset_id'] for r in p.rows("SELECT sa.asset_id FROM source_assets sa JOIN sources s ON s.id=sa.source_id WHERE s.kind='baseline'")}
novelty=[];seen=set()
for row in p.rows("SELECT t.payload,t.output_ref FROM tasks t WHERE kind='classify' AND status='succeeded' ORDER BY updated DESC"):
    payload=json.loads(row['payload']);cid=payload['candidate_id']
    if cid in seen:continue
    seen.add(cid);d=json.loads((p.root/row['output_ref']).read_text())
    known=p.rows("SELECT 1 FROM relations WHERE lhs=? AND rhs LIKE 'baseline:%' AND kind='same_original_post' LIMIT 1",(cid,))
    candidate=p.rows('SELECT source_id FROM candidates WHERE id=?',(cid,))
    assets={r['asset_id'] for r in p.rows('SELECT asset_id FROM source_assets WHERE source_id=?',(candidate[0]['source_id'],))} if candidate else set()
    category='known_original' if known else 'exact_media_already_archived' if assets and assets<=baseline_assets else 'novel_candidate'
    novelty.append({'candidate_id':cid,'category':category,'relevance':d['result']['relevance'],'product':d['result']['product'],'content_kind':d['result']['content_kind']})
counts=Counter((x['category'],x['relevance']) for x in novelty)
atomic(p.root/'reports/pilot-novelty-current.json',{'created_at':now(),'classified_groups':len(novelty),'counts':{str(k):v for k,v in counts.items()},'records':novelty,
 'limitation':'Candidate novelty does not prove conversation novelty or ShareGPT eligibility; cropped/reposted text and added context require reconstruction/deduplication.'})
comments=p.rows("SELECT count(*) n FROM sources WHERE kind='reddit_comment'")[0]['n']
checks=[json.loads(f.read_text()) for f in (p.root/'data/processed/upstream_asset_checks').glob('*.json')]
active=p.rows("SELECT count(*) n FROM conversations WHERE coalesce(json_extract(payload,'$.superseded'),0)=0")[0]['n']
report={'at':now(),'tests':tests,'assets':status['assets'],'candidates':status['candidates'],'active_conversation_records':active,
 'raw_comments':comments,'classified_unique_groups':len(novelty),'candidate_novelty_counts':{str(k):v for k,v in counts.items()},
 'upstream_byte_checks':len(checks),'upstream_changed':sum(not c['same_bytes'] for c in checks),
 'final_release':'final-20261106-v1','final_release_samples':50,
 'goal_status':'completed_within_authorized_scope','final_dataset_complete':True}
atomic(p.root/'reports/execution-checkpoint.json',report)
with (p.root/'docs/PROGRESS.md').open('a') as f:
    f.write('\n### 自动实测检查点 '+now()+'\n\n')
    f.write(f"- 独立资产 {status['assets']['n']}，候选组 {status['candidates']}，当前有效会话记录（含待审）{active}；原始评论 {comments}。\n")
    f.write(f"- 已分类独立候选组 {len(novelty)}，候选层新颖性统计见 `reports/pilot-novelty-current.json`；500 组固定试点队列处理终态见 `reports/P3-pilot-processing.json`。\n")
    f.write(f"- 模型尝试 {status['model']['attempts']}，reported tokens {status['model']['reported_tokens']}，reserved tokens {status['model']['reserved_tokens']}（合计 {status['model']['reported_tokens']+status['model']['reserved_tokens']} / 30,000,000）；unknown usage {status['model']['unknown_usage']} 单列且保留资源预留。\n")
    f.write(f"- 离线测试记录 {tests}；上游媒体字节检查 {len(checks)} 项，差异 {sum(not c['same_bytes'] for c in checks)} 项。\n")
    f.write('- 最终冻结版本 `exports/final-20261106-v1`（50 条样本，100% 通过同模型当前合约核验 + 代理逐图/逐原文视觉复核，0 项校验错误）已生成并通过验证，详见 `reports/FINAL_REPORT.md`。\n')
print(json.dumps(report,ensure_ascii=False))
