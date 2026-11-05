"""Relate frozen pilot groups to reconstructed conversations and release inclusion."""
import json
from collections import Counter
from newbing.core import Project,atomic,now
from newbing.cleaning import validate_sharegpt
p=Project();cohort=json.loads((p.root/'reports/P3-pilot-manifest.json').read_text());processing=json.loads((p.root/'reports/P3-pilot-processing.json').read_text());states={r['candidate_id']:r for r in processing['records']}
convs=[json.loads(r['payload']) for r in p.rows("SELECT payload FROM conversations WHERE coalesce(json_extract(payload,'$.superseded'),0)=0")]
counts=Counter();groups=[]
for record in cohort['candidate_records']:
 cid=record['candidate_id'];sources={record['source_id']}
 matches=[c for c in convs if sources.intersection(c.get('source_ids',[])) and (c['conversation_id'].startswith(cid+':conversation:') or c.get('derived_from','').startswith(cid+':conversation:'))]
 eligible=[c for c in matches if not c['automatic_exclusions'] and not validate_sharegpt(c['projected'])]
 reasons=Counter(reason for c in matches for reason in c['automatic_exclusions'])
 state=states[cid]['status'];counts[state]+=1
 groups.append({'candidate_id':cid,'source_id':record['source_id'],'processing_status':state,
                'reconstructed_conversation_records':len(matches),'structurally_eligible_records':len(eligible),
                'records':[{'id':c['conversation_id'],'date_bucket':c['date_bucket'],'complete_rounds':len(c['projected']['conversations'])//2,
                            'derived_from':c.get('derived_from'),'automatic_exclusions':c['automatic_exclusions']} for c in matches],
                'exclusion_reason_counts':dict(reasons)})
report={'at':now(),'cohort_hash':cohort['cohort_hash'],'groups':len(groups),'processing_counts':dict(counts),
 'groups_with_structurally_eligible_conversation':sum(bool(g['structurally_eligible_records']) for g in groups),
 'groups_with_reconstructed_records':sum(bool(g['reconstructed_conversation_records']) for g in groups),'outcomes':groups,
 'note':'Structural eligibility is not model evidence verification, authenticity, date confirmation or conversation novelty; final release provenance and relation graph provide those separate gates.'}
atomic(p.root/'reports/P3-P5-pilot-outcomes.json',report)
print({k:v for k,v in report.items() if k!='outcomes'})
