"""Persist failure denominators without conflating missing content with absent conversations."""
import json
from collections import Counter
from urllib.parse import urlparse
from newbing.core import Project,atomic,now
p=Project();categories=Counter();records=[]
for row in p.rows("SELECT id,kind,status,payload,error,attempts,output_ref,lease_until FROM tasks WHERE status NOT IN ('succeeded','superseded_input','suppressed_known_baseline') ORDER BY kind,id"):
 payload=json.loads(row['payload']);error=row.get('error') or ''
 label='pending' if row['status'] in ('pending','leased','retry_wait') else 'unknown'
 for token,name in [('refusal','model_refusal'),('incomplete','model_truncation'),('429','source_rate_limited'),('503','model_or_source_unavailable'),('404','missing_media_or_page'),('unreadable','unreadable'),('evidence','evidence_validation'),('context','context_review'),('budget','resource_limit'),('circuit','model_circuit_wait')]:
  if token in error.lower():label=name;break
 categories[(row['kind'],row['status'],label)]+=1
 records.append({'task_id':row['id'],'kind':row['kind'],'status':row['status'],'category':label,'attempts':row['attempts'],
                 'candidate_id':payload.get('candidate_id'),'source_id':payload.get('source_id'),'conversation_id':payload.get('conversation_id'),
                 'url':payload.get('url'),'host':urlparse(payload.get('url','')).hostname,'error':error,'retry_at':row['lease_until']})
report={'at':now(),'counts':[{ 'kind':k,'status':s,'category':c,'n':n} for (k,s,c),n in categories.items()],
        'records':records,'note':'A failed transcribe/verify/download does not prove the original conversation did not exist. Raw evidence and responses are preserved; pending counts are not final exclusions.'}
atomic(p.root/'reports/failures-current.json',report)
print({'non_success_current_records':len(records),'grouped_categories':len(categories)})
