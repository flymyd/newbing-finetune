"""Apply explicit, evidence-checked text deltas to derived records; raw sources stay immutable."""
from __future__ import annotations

import json

from .core import atomic,digest,now
from .cleaning import project_messages


def apply_document(project,document):
    if document.get('reviewer_type') not in ('human','agent_visual_checked','model_evidence_correction'):
        raise ValueError('correction needs an explicit reviewer type')
    cid=document['conversation_id']
    records=project.rows('SELECT payload FROM conversations WHERE id=?',(cid,))
    if not records:raise KeyError(cid)
    c=json.loads(records[0]['payload'])
    correction_id=digest(document)
    if correction_id in c.get('applied_corrections',[]):return 'already_applied'
    for patch in document['patches']:
        if patch.get('operation')=='mark_conversation_truncation':
            # Reviewer saw visible evidence of unseen earlier/later turns. Flags can only be raised,
            # never cleared, so a review cannot promote a record into the strict tier.
            if patch['asset_id'] not in c['asset_ids'] or not patch.get('evidence_reason'):raise ValueError('truncation mark needs source evidence')
            for side in ('truncated_start','truncated_end'):
                if patch.get(side):c[side]=True
            c.setdefault('truncation_reviews',[]).append({'reason':patch['evidence_reason'],'asset_id':patch['asset_id'],'method':document['reviewer_type']})
            continue
        message=next(m for m in c['messages'] if m['message_id']==patch['message_id'])
        if patch['asset_id'] not in c['asset_ids']:raise ValueError('patch has no matching source asset')
        parts=message.get('content',message.get('parts',[]))
        if patch.get('operation')=='append_ui_part':
            part=patch['part']
            if part.get('type')!='ui_notice' or not part.get('text'):raise ValueError('only evidence-backed UI annotation can be appended')
            if part in parts:raise ValueError('UI part already present without correction marker')
            parts.append(part)
        elif patch.get('operation')=='retype_part_as_ui':
            # Archive text is kept verbatim; only the part type changes so UI labels (e.g. a citation
            # list header) are not projected into training text.
            if patch.get('new_type') not in ('citation','ui_notice','suggestion','search_query'):raise ValueError('retype target must be a UI part type')
            matches=[p for p in parts if p.get('type')=='text' and p.get('text')==patch['text']]
            if len(matches)!=patch.get('expected_count',1):raise ValueError('retype precondition does not match')
            for p in matches:p['type']=patch['new_type']
        elif patch.get('operation')=='retype_part_as_text':
            # Reverse case: text inside the assistant bubble that was typed as UI but is evidenced as part
            # of the reply (e.g. Bing later refers to it as something it said). Text stays verbatim.
            if not patch.get('evidence_reason'):raise ValueError('retype to text needs an evidence reason')
            matches=[p for p in parts if p.get('type')==patch.get('from_type') and p.get('text')==patch['text']]
            if patch.get('from_type') not in ('suggestion','ui_notice') or len(matches)!=patch.get('expected_count',1):
                raise ValueError('retype precondition does not match')
            for p in matches:p['type']='text'
        else:
            old,new=patch['old'],patch['new']
            occurrences=sum(p.get('text','').count(old) for p in parts)
            if not old or occurrences!=patch.get('expected_count',1):raise ValueError('patch precondition does not match')
            for part in parts:
                if isinstance(part.get('text'),str):part['text']=part['text'].replace(old,new)
        message.setdefault('evidence',[]).append({'source_id':c['source_ids'][0],'asset_id':patch['asset_id'],
                                                  'region':patch['region'],'method':document['reviewer_type'],'correction_id':correction_id})
    turns,transforms,issues=project_messages(c['messages'])
    c['projected']={'id':cid,'conversations':[{k:m[k] for k in ('from','value')} for m in turns]}
    combined=c['transforms']+transforms
    c['transforms']=list({digest(t):t for t in combined}.values())
    c.setdefault('applied_corrections',[]).append(correction_id)
    c['verification_status']='corrected_needs_reverification'
    c['transcription_correction_method']=document['reviewer_type']
    project.execute('UPDATE conversations SET payload=?,updated=? WHERE id=?',(json.dumps(c,ensure_ascii=False),now(),cid))
    return 'applied'


def import_correction(project,path):
    document=json.loads(path.read_text())
    result=apply_document(project,document)
    atomic(project.root/'data/review/decisions'/f'{digest(document)}.json',document)
    return {'correction_id':digest(document),'status':result,'conversation_id':document['conversation_id']}


def restore_corrections(project):
    results=[]
    documents=[json.loads(path.read_text()) for path in (project.root/'data/review/decisions').glob('*.json')]
    for document in sorted(documents,key=lambda d:(d.get('reviewed_at',''),digest(d))):
        results.append(apply_document(project,document))
    return results
