from __future__ import annotations

import html
import json
from pathlib import Path
import time
import copy
import importlib
import types
from collections import Counter

from .core import atomic,digest,now,ModelConcurrencyBusy,BudgetExceeded
from .llm import ModelClient,output_text,ModelResultError
from .model_tasks import obj,enum,STR,BOOL,REGION,STRINGS,prepare_images,normalize_evidence


VERIFY=obj({
    'verdict':enum(['pass','needs_correction','uncertain']),
    'message_checks':{'type':'array','items':obj({'message_id':STR,'text_faithful':BOOL,'role_correct':BOOL,
        'context_sufficient':BOOL,'requires_nontext_chat_context':BOOL,
        'evidence':{'type':'array','items':obj({'image_id':STR,'region':REGION})},'note':STR})},
    'issues':{'type':'array','items':obj({'message_id':{'type':['string','null']},'issue_type':STR,
        'severity':enum(['minor','major','critical']),'reason':STR,'proposed_patch':{'type':['string','null']}})},
    'unresolved_questions':STRINGS,
})
TEXT_VERIFY=copy.deepcopy(VERIFY)
TEXT_VERIFY['properties']['message_checks']['items']['properties']['evidence']={
    'type':'array','items':obj({'source_id':STR,'quote':STR,'start':{'type':'integer'},'end':{'type':'integer'}})}


def verification_contract(project):
    """Version only inputs that can change evidence checking or text projection."""
    return {
        'version':'evidence-verification-v2',
        'prompt_hash':digest((project.root/'prompts/verify.md').read_bytes()),
        'schema_hash':digest([VERIFY,TEXT_VERIFY]),
        'projection_code_hash':function_hash('cleaning.py',('part_text','project_messages','validate_sharegpt')),
        'verifier_code_hash':function_hash('verification.py',('verify_one','aggregate_checks','image_windows','evidence_identity')),
        'preprocessor_code_hash':function_hash('model_tasks.py',('prepare_images','normalize_evidence')),
        'images':project.policy['images'],
        'profile':{k:v for k,v in project.config['models']['profiles']['flymyd'].items()
                   if k not in ('api_key_env','env_file')},
        'max_output_tokens':project.config['models']['tasks']['verify']['max_output_tokens'],
    }


def function_hash(filename,names):
    # Fingerprint the functions actually loaded by this worker. Files can be edited
    # while another batch runs; reading them here would mislabel old in-memory code.
    module=importlib.import_module('.'+Path(filename).stem,__package__)
    def constant_signature(value):
        if isinstance(value,types.CodeType):return code_signature(value)
        if isinstance(value,(set,frozenset)):return sorted((constant_signature(v) for v in value),key=repr)
        if isinstance(value,(tuple,list)):return [constant_signature(v) for v in value]
        return repr(value)
    def code_signature(code):
        return {'bytecode':code.co_code.hex(),'names':code.co_names,'variables':code.co_varnames,
                'constants':[constant_signature(v) for v in code.co_consts]}
    selected={name:{'code':code_signature(getattr(module,name).__code__),
                    'defaults':repr(getattr(module,name).__defaults__)} for name in names}
    return digest(selected)


def evidence_identity(project,conversation):
    sources=[]
    for sid in sorted(conversation.get('source_ids',[])):
        rows=project.rows('SELECT raw_path,raw_hash FROM sources WHERE id=?',(sid,))
        sources.append({'id':sid,**(rows[0] if rows else {'missing':True})})
    return {'conversation_hash':digest(conversation),'sources':sources,
            'asset_hashes':conversation.get('asset_ids',[])}


def verify_queue(project,sample=50,conversation_id=None,calibration=False,bucket=None):
    eligible=[];contract=verification_contract(project)
    contract_hash=digest(contract)
    for task in project.rows("SELECT id,payload FROM tasks WHERE kind='verify' AND status IN ('pending','retry_wait')"):
        payload=json.loads(task['payload'])
        rows=project.rows('SELECT payload FROM conversations WHERE id=?',(payload['conversation_id'],))
        stale=payload.get('contract_hash')!=contract_hash or not rows or digest(json.loads(rows[0]['payload']))!=payload['conversation_hash']
        if stale:
            project.execute("UPDATE tasks SET status='superseded_input',lease_until=NULL,error='Verification input or contract superseded before dispatch',updated=? WHERE id=?",(now(),task['id']))
    for row in project.rows('SELECT id,payload FROM conversations ORDER BY id'):
        c=json.loads(row['payload'])
        if conversation_id and row['id']!=conversation_id:continue
        if bucket and c['date_bucket']!=bucket:continue
        if c.get('superseded') or not c.get('messages'):continue
        if (c['automatic_exclusions'] and not calibration) or (not c.get('asset_ids') and not c.get('text_source')):continue
        if len(c['asset_ids'])<c.get('expected_asset_count',0):continue
        payload={'conversation_id':row['id'],'conversation_hash':digest(c),
                 'contract_hash':digest(contract),'evidence_hash':digest(evidence_identity(project,c))}
        if calibration:payload['purpose']='calibration'
        previous=project.rows("SELECT status FROM tasks WHERE kind='verify' AND input_hash=?",(digest(payload),))
        if previous:continue  # Failed tasks remain explicit; finite retry is owned by the task/client.
        eligible.append((c,row['id'],payload))
    eligible.sort(key=lambda item:(item[0]['date_bucket']!='early',bool(item[0].get('derived_from')),
                                   not bool(item[0].get('transcription_result')),item[0].get('date') or '9999',item[1]))
    for c,cid,payload in eligible[:sample]:project.enqueue('verify',payload)
    return {'available_eligible_conversations':len(eligible),'queued':min(sample,len(eligible))}


def image_windows(project,images):
    cfg=project.config['models']['profiles']['flymyd'];windows=[];start=0
    while start<len(images):
        window=images[start:start+cfg['max_input_images']]
        while len(window)>1 and sum(Path(i['path']).stat().st_size for i in window)>cfg['max_raw_image_bytes_per_request']:
            window.pop()
        if Path(window[0]['path']).stat().st_size>cfg['max_raw_image_bytes_per_request']:
            raise ValueError('single verification crop exceeds byte limit; preprocessing required')
        windows.append(window)
        start+=len(window) if start+len(window)>=len(images) else max(1,len(window)-1)
    return windows or [[]]


def aggregate_checks(conversation,windows):
    expected=[m['message_id'] for m in conversation['messages']];found={mid:[] for mid in expected}
    issues=[];questions=[]
    for window in windows:
        result=window['result'];seen=set()
        issues+=result['issues'];questions+=result['unresolved_questions']
        if result['verdict']!='pass' and not result['issues'] and not result['unresolved_questions']:
            questions.append('Window '+str(window['index'])+' did not pass evidence checking.')
        for check in result['message_checks']:
            mid=check['message_id']
            if mid not in found or mid in seen:raise ValueError('unknown or duplicate verification message id')
            seen.add(mid)
            if not check['evidence']:raise ValueError('verification message missing source evidence')
            found[mid].append(check)
    merged=[]
    for mid,checks in found.items():
        if not checks:
            questions.append('No complete source evidence check covers message '+mid)
            continue
        check=copy.deepcopy(checks[0])
        for name in ('text_faithful','role_correct','context_sufficient'):
            check[name]=all(c[name] for c in checks)
        check['requires_nontext_chat_context']=any(c['requires_nontext_chat_context'] for c in checks)
        check['evidence']=[e for c in checks for e in c['evidence']]
        check['note']=' | '.join(dict.fromkeys(c['note'] for c in checks))
        merged.append(check)
        if not all(check[name] for name in ('text_faithful','role_correct','context_sufficient')) or check['requires_nontext_chat_context']:
            questions.append('Unresolved fidelity, role or context check for '+mid)
    verdict='needs_correction' if issues else 'uncertain' if questions else 'pass'
    return {'verdict':verdict,'message_checks':merged,'issues':issues,'unresolved_questions':list(dict.fromkeys(questions))}


def verify_one(project,payload):
    row=project.rows('SELECT payload FROM conversations WHERE id=?',(payload['conversation_id'],))[0]
    conversation=json.loads(row['payload']);contract=verification_contract(project)
    if digest(conversation)!=payload['conversation_hash']:raise ValueError('conversation changed since verification was queued')
    if payload.get('contract_hash')!=digest(contract):raise ValueError('verification contract changed since queued')
    identity=evidence_identity(project,conversation)
    if payload.get('evidence_hash')!=digest(identity):raise ValueError('source evidence changed since queued')
    assets=[]
    for aid in conversation['asset_ids']:
        assets+=project.rows('SELECT * FROM assets WHERE id=?',(aid,))
    # Validate actual source bytes, not merely recorded paths/hashes.
    for asset in assets:
        if digest((project.root/asset['path']).read_bytes())!=asset['id']:raise ValueError('asset hash mismatch')
    for source in identity['sources']:
        if source.get('missing') or digest((project.root/source['raw_path']).read_bytes())!=source['raw_hash']:
            raise ValueError('raw source hash mismatch')
    images=prepare_images(project,assets);windows=image_windows(project,images);results=[]
    base_prompt=(project.root/'prompts/verify.md').read_text()
    for index,window in enumerate(windows):
        prompt=base_prompt+'\n\n'+json.dumps({
            'conversation_id':payload['conversation_id'],'segment_extraction':conversation.get('derived_from'),
            'window_index':index,'window_count':len(windows),
            'context_note':'Extracted fragment: pass context_sufficient only when independent of unseen earlier turns.' if conversation.get('derived_from') else '',
            'messages':[{'message_id':m['message_id'],'role':m.get('role'),'parts':m.get('content',m.get('parts'))} for m in conversation['messages']],
            'proposed_sharegpt':conversation['projected'],
            'images':[{k:v for k,v in image.items() if k!='path'} for image in window]},ensure_ascii=False)
        if len(windows)>1:
            prompt+='\nThis is one overlapping evidence window of a long conversation. ALL candidate messages are provided for textual context, but only some have original evidence in this window. Return message_checks ONLY for messages whose visible source is actually present here. Never invent evidence for unseen messages. Flag partial or clipped source content as uncertain rather than treating a partial sentence as a complete check. The executor unions coverage across windows and cannot pass missing messages. Check ordering and boundary continuity against visible evidence. A message absent from this window is not itself a defect; do not list absent messages as issues.\n'
        else:prompt+='\nReturn exactly one message_check for EVERY supplied message_id. Do not omit uncertain messages.\n'
        schema=VERIFY
        if not images and conversation.get('text_source'):
            schema=TEXT_VERIFY
            prompt+='\nDIRECT TEXT EVIDENCE: check exact source quotes, using source_id/quote/start/end instead of image regions.\n'+json.dumps(conversation['text_source'],ensure_ascii=False)
        response,cached=ModelClient(project).call('verify',prompt,[(i['image_id'],i['path']) for i in window],schema,
                                                max_output=project.config['models']['tasks']['verify']['max_output_tokens'])
        result=json.loads(output_text(response));conversions=[]
        if window:
            conversions=normalize_evidence({'conversations':[{'messages':result['message_checks']}]},window)
        else:
            source=conversation['text_source'];body=source['text']
            for msg in result['message_checks']:
                for e in msg['evidence']:
                    if e['source_id']!=source['source_id'] or not e['quote'] or e['quote'] not in body:raise ValueError('text verification quote not found')
                    if body[e['start']:e['end']]!=e['quote']:
                        lo=body.find(e['quote']);conversions.append({'type':'text_offset','given':[e['start'],e['end']],'canonical':[lo,lo+len(e['quote'])]})
                        e.update(start=lo,end=lo+len(e['quote']))
        record={'index':index,'result':result,'images':window,'coordinate_transforms':conversions,'response_id':response.get('id')}
        results.append(record)
        atomic(project.root/'data/processed/verification_windows'/f"{digest([payload,index])}.json",record)
    result=aggregate_checks(conversation,results)
    out=project.root/'data/processed/verifications'/f"{digest(payload)}.json"
    atomic(out,{'conversation_id':payload['conversation_id'],'conversation_hash':payload['conversation_hash'],
               'contract':contract,'contract_hash':digest(contract),'evidence_identity':identity,'evidence_hash':digest(identity),
               'result':result,'response_id':results[-1]['response_id'],'windows':results,'images':images,
               'coverage':{'expected_message_ids':[m['message_id'] for m in conversation['messages']],
                           'checked_message_ids':[m['message_id'] for m in result['message_checks']],
                           'image_ids':[i['image_id'] for i in images]},
               'verification_method':'same_model_evidence_consistency_check','human_status':'pending_human'})
    return out


def run_verify(project,sample=50,workers=2,dry_run=False,conversation_id=None,calibration=False,bucket=None):
    from concurrent.futures import ThreadPoolExecutor
    queued=verify_queue(project,sample,conversation_id,calibration,bucket)
    contract_hash=digest(verification_contract(project))
    sql="SELECT * FROM tasks WHERE kind='verify' AND status IN ('pending','retry_wait') AND (lease_until IS NULL OR lease_until<?) AND json_extract(payload,'$.contract_hash')=? AND coalesce(json_extract(payload,'$.purpose'),'')=?"
    params=[time.time(),contract_hash,'calibration' if calibration else '']
    if conversation_id:
        sql+=" AND json_extract(payload,'$.conversation_id')=?";params.append(conversation_id)
    if bucket:
        sql+=" AND EXISTS(SELECT 1 FROM conversations c WHERE c.id=json_extract(tasks.payload,'$.conversation_id') AND json_extract(c.payload,'$.date_bucket')=?)";params.append(bucket)
    sql+=' ORDER BY rowid LIMIT ?';params.append(sample)
    jobs=project.rows(sql,params)
    if dry_run:return {**queued,'dry_run':True}
    def work(job):
        claimed=project.claim_task(job['id'],1800)
        if not claimed:return 'not_claimed'
        try:
            out=verify_one(project,json.loads(job['payload']))
            project.execute("UPDATE tasks SET status='succeeded',output_ref=?,lease_until=NULL,updated=? WHERE id=?",(str(out.relative_to(project.root)),now(),job['id']))
            return json.loads(out.read_text())['result']['verdict']
        except Exception as exc:
            if isinstance(exc,ModelResultError) and exc.status=='model_circuit_cooldown':
                state=project.rows("SELECT payload FROM source_status WHERE id='model_circuit'")
                until=json.loads(state[0]['payload']).get('retry_at',time.time()+300) if state else time.time()+300
                project.execute("UPDATE tasks SET status='retry_wait',lease_until=?,error=?,updated=? WHERE id=?",(until,str(exc),now(),job['id']))
                return 'retry_wait'
            if isinstance(exc,BudgetExceeded):
                project.execute("UPDATE tasks SET status='deferred_resource',lease_until=NULL,error=?,updated=? WHERE id=?",(str(exc),now(),job['id']))
                return 'deferred_resource'
            if isinstance(exc,ModelConcurrencyBusy):
                project.execute("UPDATE tasks SET status='pending',lease_until=NULL,error=?,updated=? WHERE id=?",(str(exc),now(),job['id']))
                return 'pending_concurrency'
            project.execute("UPDATE tasks SET status='quarantined',error=?,lease_until=NULL,updated=? WHERE id=?",(type(exc).__name__+': '+str(exc)[:400],now(),job['id']))
            return 'quarantined'
    result=Counter()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for i,state in enumerate(pool.map(work,jobs),1):
            result[state]+=1
            if i%5==0:print(json.dumps({'verified':i,**result}),flush=True)
    return {**queued,'results':dict(result)}


def build_review(project):
    rows=[]
    for r in project.rows("SELECT output_ref FROM tasks WHERE kind='verify' AND status='succeeded'"):
        d=json.loads((project.root/r['output_ref']).read_text())
        c=json.loads(project.rows('SELECT payload FROM conversations WHERE id=?',(d['conversation_id'],))[0]['payload'])
        turns=''.join(f"<p><strong>{html.escape(t['from'])}</strong></p><pre>{html.escape(t['value'])}</pre>" for t in c['projected']['conversations'])
        imgs=''.join(f'<img loading="lazy" src="{html.escape(str(Path(i["path"]).resolve()))}" alt="{i["image_id"]}">' for i in d['images'])
        if c.get('text_source'):imgs+='<h3>Original text evidence</h3><pre>'+html.escape(c['text_source']['text'])+'</pre>'
        rows.append(f'<section><h2>{html.escape(d["conversation_id"])}</h2><p>model verdict: {d["result"]["verdict"]}; pending human</p><div class="row"><div>{turns}</div><div>{imgs}</div></div><pre>{html.escape(json.dumps(d["result"],ensure_ascii=False,indent=2))}</pre></section>')
    document='<!doctype html><meta charset="utf-8"><title>Bing evidence review</title><style>body{font:16px system-ui;margin:24px;background:#f6f4ef}section{border-top:2px solid #888;padding:16px 0}.row{display:grid;grid-template-columns:1fr 1fr;gap:24px}pre{white-space:pre-wrap;overflow-wrap:anywhere}img{max-width:100%;display:block;margin:12px 0}</style><h1>原图与转写对照：模型核验，等待人工审阅</h1>'+''.join(rows)
    path=project.root/'data/review/index.html';atomic(path,document,raw=True)
    return {'review_page':str(path),'conversations':len(rows)}
