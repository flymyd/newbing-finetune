"""Freeze a deterministic, quota-aware manifest of genuinely new candidate groups."""
import json
from collections import Counter,defaultdict
from newbing.core import Project,atomic,digest,now

p=Project();novelty=json.loads((p.root/'reports/pilot-novelty-current.json').read_text())
existing_path=p.root/'reports/P3-pilot-manifest.json'
existing=json.loads(existing_path.read_text()) if existing_path.exists() else None
items=[]
for item in novelty['records']:
    if item['category']!='novel_candidate':continue
    record=p.rows('SELECT c.source_id,s.payload,s.raw_path,s.raw_hash,s.kind FROM candidates c JOIN sources s ON s.id=c.source_id WHERE c.id=?',(item['candidate_id'],))[0]
    source=json.loads(record['payload']);subreddit=(source.get('post') or {}).get('subreddit','').lower()
    if record['kind']=='reddit_comment':
        parent=p.rows('SELECT payload FROM sources WHERE id=?',(source.get('post_source_id'),))
        if parent:subreddit=(json.loads(parent[0]['payload']).get('post') or {}).get('subreddit','').lower()
    layer={'bing':'bing','bingchat':'bingchat','chatgpt':'chatgpt','freesydney':'freesydney_and_singularity','singularity':'freesydney_and_singularity'}.get(subreddit,'author_archive_and_web')
    date=source.get('posted_at') or '9999'
    tasks=p.rows("SELECT kind,status,output_ref FROM tasks WHERE json_extract(payload,'$.candidate_id')=? ORDER BY updated DESC",(item['candidate_id'],))
    items.append({**item,'source_id':record['source_id'],'layer':layer,'posted_at':source.get('posted_at'),
                  'raw_path':record['raw_path'],'raw_sha256':record['raw_hash'],'tasks':tasks,
                  'priority':0 if '2023-02-07'<=date[:10]<='2023-02-16' else 1 if date[:10]<='2023-02-28' else 2})
items.sort(key=lambda x:(x['priority'],x['posted_at'] or '9999',x['candidate_id']))
quotas=p.policy['pilot']['quotas'];chosen=[];selected=set();by_layer=defaultdict(list)
for item in items:by_layer[item['layer']].append(item)
shortfalls={}
for layer,quota in quotas.items():
    part=by_layer[layer][:quota];chosen.extend(part);selected.update(x['candidate_id'] for x in part)
    if len(part)<quota:shortfalls[layer]=quota-len(part)
target=p.policy['pilot']['target_novel_candidate_groups']
for item in items:
    if len(chosen)>=target:break
    if item['candidate_id'] not in selected:chosen.append(item);selected.add(item['candidate_id'])
chosen.sort(key=lambda x:(x['priority'],x['posted_at'] or '9999',x['candidate_id']))
if existing and existing.get('selected_groups')==target:
    frozen_ids=[x['candidate_id'] for x in existing['candidate_records']]
    current={x['candidate_id']:x for x in items}
    previous={x['candidate_id']:x for x in existing['candidate_records']}
    chosen=[current.get(cid,{**previous[cid],'novelty_recheck_required':True}) for cid in frozen_ids]
    shortfalls=existing['quota_shortfalls_reallocated']
report={'created_at':now(),'target':target,'selected_groups':len(chosen),'available_classified_novel_groups':len(items),
        'by_layer':dict(Counter(x['layer'] for x in chosen)),'by_priority':dict(Counter(x['priority'] for x in chosen)),
        'quota_shortfalls_reallocated':shortfalls,'candidate_records':chosen,'config_hash':digest(p.config),
        'cohort_created_at':existing.get('cohort_created_at',existing['created_at']) if existing else now(),
        'cohort_hash':digest([x['candidate_id'] for x in chosen]),
        'limitation':'This establishes candidate-level novelty and evidence availability only. Transcription, conversation-level deduplication and ShareGPT acceptance are separate gates.'}
atomic(p.root/'reports/P3-pilot-manifest.json',report)
atomic(p.root/'reports/P3-pilot-discovery.md','# P3 试点候选清单\n\n'+f"时间 {report['created_at']}。从已分类的新候选中按来源配额、核心期优先及实际不足重新分配，选择 {len(chosen)}/{target} 个候选组。分母是独立候选组，非图片或最终会话。\n\n"+'```json\n'+json.dumps({k:v for k,v in report.items() if k not in ('candidate_records',)},ensure_ascii=False,indent=2)+'\n```\n\n逐组来源/原文哈希/处理状态见 P3-pilot-manifest.json；新颖性排除已知基线原帖及全图完全重复，但还不证明会话内容不重复。剩余模型处理和质量验收必须继续。\n',raw=True)
print({k:v for k,v in report.items() if k not in ('candidate_records',)})
