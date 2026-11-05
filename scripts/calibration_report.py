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
report={'at':now(),'current_contract_hash':current,'versions':versions,
        'unique_groups_across_versions':len(latest),'negative_agent_visual_checks':negative,
        'negative_groups':len(negative),'negative_evidence_images':len({aid for r in negative for aid in r.get('reviewed_asset_ids',[r['image_id']])}),
        'reviewer_separation':'same-model checks, agent visual checks and future human verification remain distinct',
        'human_verified_groups':0,'accuracy_available':False,
        'no_known_severe_errors_in_export_required':True,
        'open_items':['Current prompt calibration and correction/reverification still running','Unknown or missing source evidence remains quarantined','No gold-standard accuracy estimate without independent human references']}
atomic(p.root/'reports/P4-calibration-current.json',report)
print({'unique_checked_groups':len(latest),'current_version_groups':versions.get(current,{}).get('distinct_groups',0),'negative_groups':len(negative),'current_version_verdicts':versions.get(current,{}).get('verdicts',{})})
