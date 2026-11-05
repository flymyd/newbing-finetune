"""Observe a frozen pilot cohort without treating archive/transcription as export success."""
import json
from collections import Counter
from newbing.core import Project,atomic,now
from newbing.model_tasks import candidate_data,candidate_identity
p=Project();cohort=json.loads((p.root/'reports/P3-pilot-manifest.json').read_text());groups=[];counts=Counter()
for record in cohort['candidate_records']:
 cid=record['candidate_id'];item=candidate_data(p,cid);identity=candidate_identity(p,item)
 tasks=p.rows("SELECT kind,status,output_ref,payload,error FROM tasks WHERE json_extract(payload,'$.candidate_id')=? ORDER BY updated DESC",(cid,))
 classification=None
 for task in tasks:
  if task['kind']!='classify' or task['status']!='succeeded':continue
  doc=json.loads((p.root/task['output_ref']).read_text())
  if doc.get('input_payload',{}).get('input_evidence')==identity:
   classification=doc;break
 # Legacy evidence is shown explicitly, not used as current proof.
 relevance=classification['result']['relevance'] if classification else 'current_classification_pending'
 transcribed=False
 for task in tasks:
  if task['kind']!='transcribe' or task['status']!='succeeded':continue
  doc=json.loads((p.root/task['output_ref']).read_text())
  given=list(dict.fromkeys(i['asset_id'] for window in doc['windows'] for i in window['images']))
  if given==[a['id'] for a in item['assets']]:transcribed=True;break
 errors=[{'kind':t['kind'],'status':t['status'],'error':t['error']} for t in tasks if t['status'] in ('quarantined','failed_terminal','deferred_resource')]
 status='excluded_clear' if relevance=='exclude_clear' else 'transcribed_pending_quality' if transcribed and classification else 'needs_transcription' if classification else 'needs_current_classification'
 if status=='needs_transcription' and errors and not any(t['kind']=='transcribe' and t['status'] in ('pending','leased','retry_wait') for t in tasks):status='quarantined_transcription'
 counts[status]+=1
 groups.append({'candidate_id':cid,'status':status,'relevance':relevance,'current_assets':len(item['assets']),'errors':errors})
report={'at':now(),'cohort_hash':cohort['cohort_hash'],'groups':len(groups),'counts':dict(counts),'records':groups,
 'note':'Only matching current source/image input counts as current. Transcription success is not ShareGPT eligibility.'}
atomic(p.root/'reports/P3-pilot-processing.json',report);print({k:v for k,v in report.items() if k!='records'})
