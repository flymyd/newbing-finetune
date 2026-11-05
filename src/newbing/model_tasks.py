from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from collections import Counter
import io
import json
import re
import copy
from pathlib import Path
import time

from PIL import Image

from .core import atomic,digest,now,BudgetExceeded,ModelConcurrencyBusy
from .llm import ModelClient,ModelResultError,output_text
from .assets import ordered_source_assets,missing_media_ordinals


def obj(properties):
    return {'type':'object','properties':properties,'required':list(properties),'additionalProperties':False}


def enum(values):return {'type':'string','enum':values}
STR={'type':'string'}
BOOL={'type':'boolean'}
STRINGS={'type':'array','items':STR}

CLASSIFY=obj({
    'product':enum(['bing','other_assistant','mixed','unknown']),
    'content_kind':enum(['conversation','assistant_excerpt','story','image_generation','article_or_discussion','meme','system_prompt_claim','mixed','unknown']),
    'relevance':enum(['include','exclude_clear','uncertain']),
    'client':enum(['bing_desktop','bing_mobile','edge_sidebar','skype','unknown']),
    'languages':STRINGS,'contains_user_turn':{'type':['boolean','null']},
    'visible_complete_rounds_lower_bound':{'type':['integer','null']},
    'visual_context_dependency':enum(['present','absent','unknown']),
    'continuation_leads':STRINGS,'has_ordering_issue':BOOL,
    'authenticity_status':enum(['source_claim_only','screenshot_backed_unverified','corroborated','suspicious','unknown']),
    'evidence':{'type':'array','items':obj({'image_id':STR,'reason':STR})},
    'uncertainties':STRINGS,'model_confidence':{'type':['number','null']},
})
REGION={'type':'array','items':{'type':'number'},'minItems':4,'maxItems':4}
MESSAGE=obj({
    'role':enum(['user','assistant','unknown']),
    'parts':{'type':'array','items':obj({'type':enum(['text','code_block','table','search_query','citation','suggestion','safety_block','image_output','ui_notice']),'text':STR})},
    'evidence':{'type':'array','items':obj({'image_id':STR,'region':REGION})},
    'truncated_start':BOOL,'truncated_end':BOOL,'context_gap_before':BOOL,
})
TRANSCRIBE=obj({
    'conversations':{'type':'array','items':obj({'messages':{'type':'array','items':MESSAGE},'truncated_start':BOOL,'truncated_end':BOOL,'notes':STR})},
    'visual_context_dependency':enum(['present','absent','unknown']),
    'contains_simulated_dialogue':BOOL,'uncertainties':STRINGS,
})
TEXT_TRANSCRIBE=copy.deepcopy(TRANSCRIBE)
TEXT_TRANSCRIBE['properties']['conversations']['items']['properties']['messages']['items']['properties']['evidence']={
    'type':'array','items':obj({'source_id':STR,'quote':STR,'start':{'type':'integer'},'end':{'type':'integer'}})}


def candidate_data(project,candidate_id):
    rows=project.rows('SELECT c.source_id,c.payload candidate,s.payload source,s.raw_path,s.raw_hash FROM candidates c JOIN sources s ON s.id=c.source_id WHERE c.id=?',(candidate_id,))
    if not rows:raise KeyError(candidate_id)
    item=rows[0]
    item['source']=json.loads(item['source']);item['candidate']=json.loads(item['candidate'])
    item['assets']=ordered_source_assets(project,item['source_id'])
    return item


class StaleCandidateInput(ValueError):pass


def candidate_identity(project,item):
    return {'source_id':item['source_id'],'raw_source_hash':item['raw_hash'],
            'text_hash':digest(item['source'].get('text','')),
            'ordered_assets':[[a['id'],a['ordinal']] for a in item['assets']],
            'missing_media_ordinals':missing_media_ordinals(project,item['source_id'],item['assets']),
            'preprocess':project.policy['images']}


def known_baseline_has_additional_context(project,candidate_id):
    item=candidate_data(project,candidate_id)
    known=project.rows("SELECT rhs FROM relations WHERE lhs=? AND rhs LIKE 'baseline:%' AND kind='same_original_post'",(candidate_id,))
    baseline_assets=set()
    for relation in known:
        row=project.rows('SELECT source_id FROM candidates WHERE id=?',(relation['rhs'],))
        if row:baseline_assets.update(r['asset_id'] for r in project.rows('SELECT asset_id FROM source_assets WHERE source_id=?',(row[0]['source_id'],)))
    return bool({a['id'] for a in item['assets']}-baseline_assets) or len(item['source'].get('text',''))>120


def queue_current_task(project,kind,payload):
    from .verification import function_hash
    payload=dict(payload)
    payload.setdefault('preprocessor_hash',function_hash('model_tasks.py',('prepare_images','normalize_evidence')))
    payload.setdefault('schema_hash',digest(CLASSIFY if kind=='classify' else [TRANSCRIBE,TEXT_TRANSCRIBE]))
    profile=project.config['models']['profiles']['flymyd']
    payload.setdefault('model_parameters_hash',digest({'endpoint':profile['base_url'],'model':profile['model'],
        'max_output_tokens':project.config['models']['tasks'][kind]['max_output_tokens']}))
    task_id=project.enqueue(kind,payload)
    project.execute("UPDATE tasks SET status='superseded_input',lease_until=NULL,error='Replaced by current candidate input snapshot',updated=? WHERE kind=? AND id!=? AND json_extract(payload,'$.candidate_id')=? AND status IN ('pending','retry_wait')",(now(),kind,task_id,payload['candidate_id']))
    return task_id


def prepare_images(project,assets):
    result=[]
    height=project.policy['images']['crop_height_px'];overlap=project.policy['images']['crop_overlap_px']
    byte_cap=project.config['models']['profiles']['flymyd']['max_raw_image_bytes_per_request']
    def emit(asset,path,box):
        if path.stat().st_size<=byte_cap:
            result.append({'image_id':asset['id'] if box==[0,0,asset['width'],asset['height']] else digest([asset['id'],box,project.policy['images']['preprocess_version']]),
                           'path':str(path),'asset_id':asset['id'],'box':box})
            return
        x0,y0,x1,y1=box
        if max(x1-x0,y1-y0)<64:raise ValueError('minimum crop still exceeds byte limit')
        if y1-y0>=x1-x0:
            middle=(y0+y1)//2;pad=min(overlap//2,(y1-y0)//8)
            boxes=[[x0,y0,x1,middle+pad],[x0,middle-pad,x1,y1]]
        else:
            middle=(x0+x1)//2;pad=min(overlap//2,(x1-x0)//8)
            boxes=[[x0,y0,middle+pad,y1],[middle-pad,y0,x1,y1]]
        with Image.open(project.root/asset['path']) as original:
            for subbox in boxes:
                cid=digest([asset['id'],subbox,project.policy['images']['preprocess_version']])
                out=project.root/'data/derived/crops'/f'{cid}.png'
                if not out.exists():
                    buffer=io.BytesIO();original.crop(subbox).save(buffer,format='PNG');atomic(out,buffer.getvalue(),raw=True)
                emit(asset,out,subbox)
    for asset in assets:
        path=project.root/asset['path']
        if asset['height']<=height:
            emit(asset,path,[0,0,asset['width'],asset['height']])
            continue
        with Image.open(path) as im:
            for y in range(0,im.height,height-overlap):
                bottom=min(im.height,y+height)
                if bottom-y<=overlap and result:break
                box=[0,y,im.width,bottom]
                cid=digest([asset['id'],box,project.policy['images']['preprocess_version']])
                out=project.root/'data/derived/crops'/f'{cid}.png'
                if not out.exists():
                    buffer=io.BytesIO();im.crop(box).save(buffer,format='PNG');atomic(out,buffer.getvalue(),raw=True)
                emit(asset,out,box)
    return result


def normalize_evidence(result,window,allow_full_image_fallback=False):
    """Accept provable pixel boxes from the gateway, preserving conversion in the derived result."""
    mapping={i['image_id']:i for i in window}
    conversions=[]
    for conv in result['conversations']:
        for msg in conv['messages']:
            if not msg['evidence']:
                raise ValueError('absent message image evidence')
            for e in msg['evidence']:
                if e['image_id'] not in mapping or len(e['region'])!=4:
                    raise ValueError('invalid image evidence reference')
                box=e['region']
                if any(v>1 for v in box):
                    with Image.open(mapping[e['image_id']]['path']) as im:
                        width,height=im.width,im.height
                    if not (0<=box[0]<box[2]<=width and 0<=box[1]<box[3]<=height):
                        if allow_full_image_fallback:
                            conversions.append({'image_id':e['image_id'],'original_invalid_box':list(box),'replacement_box':[0,0,1,1],
                                                'method':'whole_input_image_scope_only','localization_status':'unverified_no_precise_region_claim'})
                            e['region']=[0,0,1,1];continue
                        raise ValueError('pixel evidence outside input image')
                    normalized=[box[0]/width,box[1]/height,box[2]/width,box[3]/height]
                    conversions.append({'image_id':e['image_id'],'original_pixel_box':list(box),'normalized_box':normalized})
                    e['region']=normalized
                b=e['region']
                if not(0<=b[0]<b[2]<=1 and 0<=b[1]<b[3]<=1):
                    if allow_full_image_fallback:
                        conversions.append({'image_id':e['image_id'],'original_invalid_box':list(b),'replacement_box':[0,0,1,1],
                                            'method':'whole_input_image_scope_only','localization_status':'unverified_no_precise_region_claim'})
                        e['region']=[0,0,1,1];continue
                    raise ValueError('invalid normalized evidence bounds')
    return conversions


def queue_classification(project,limit=500):
    rows=project.rows("SELECT c.id,c.source_id FROM candidates c JOIN sources s ON s.id=c.source_id WHERE s.kind!='baseline' ORDER BY coalesce(json_extract(s.payload,'$.posted_at'),'9999'),c.rowid LIMIT ?",(limit,))
    queued=0
    for row in rows:
        known=project.rows("SELECT 1 FROM relations WHERE lhs=? AND rhs LIKE 'baseline:%' AND kind='same_original_post' LIMIT 1",(row['id'],))
        if known and not known_baseline_has_additional_context(project,row['id']):continue
        if not project.rows('SELECT 1 FROM source_assets WHERE source_id=? LIMIT 1',(row['source_id'],)):
            text_source=project.rows('SELECT payload FROM sources WHERE id=?',(row['source_id'],))
            if not text_source or len(json.loads(text_source[0]['payload']).get('text',''))<300:continue
        # Require all original group downloads to reach terminal before deciding a group from partial images.
        pending=project.rows("SELECT 1 FROM tasks WHERE kind='download' AND json_extract(payload,'$.source_id')=? AND status IN ('pending','leased','retry_wait') LIMIT 1",(row['source_id'],))
        if pending:continue
        item=candidate_data(project,row['id'])
        task_id=queue_current_task(project,'classify',{'candidate_id':row['id'],'prompt_hash':digest((project.root/'prompts/classify.md').read_bytes()),
                                   'input_evidence':candidate_identity(project,item)})
        if known:
            project.execute("UPDATE tasks SET status='pending',error='Known source has additional/different media or text',updated=? WHERE id=? AND status='suppressed_known_baseline'",(now(),task_id))
        queued+=1
    return queued


def classify_one(project,payload):
    item=candidate_data(project,payload['candidate_id'])
    if payload.get('input_evidence') and payload['input_evidence']!=candidate_identity(project,item):
        raise StaleCandidateInput('candidate evidence changed after enqueue')
    images=prepare_images(project,item['assets'])
    maximum=project.config['models']['profiles']['flymyd']['max_input_images']
    # Classification is high-recall; spread sampling is explicit and never treated as full transcription.
    if len(images)>maximum:
        images=[images[round(i*(len(images)-1)/(maximum-1))] for i in range(maximum)]
    prompt=(project.root/'prompts/classify.md').read_text()
    context=item['source'].get('text','')
    prompt+='\n\nSOURCE_METADATA (not actual chat turns):\n'+json.dumps({'candidate_id':payload['candidate_id'],'source_id':item['source_id'],
            'posted_at':item['source'].get('posted_at'),'url':item['source'].get('url'),'post_text':context[:12000],
            'image_ids':[i['image_id'] for i in images],'classification_sampling_only':True},ensure_ascii=False)
    response,cached=ModelClient(project).call('classify',prompt,[(i['image_id'],i['path']) for i in images],CLASSIFY,
                                           max_output=project.config['models']['tasks']['classify']['max_output_tokens'])
    result=json.loads(output_text(response))
    out=project.root/'data/processed/classifications'/f"{digest(payload)}.json"
    atomic(out,{'candidate_id':payload['candidate_id'],'source_id':item['source_id'],'result':result,'model_response_id':response.get('id'),
                'image_mapping':images,'model':'gpt-6-luna','human_status':'pending_human',
                'input_payload':payload,'actual_prompt_hash':digest((project.root/'prompts/classify.md').read_bytes())})
    if result['relevance']!='exclude_clear':
        queue_current_task(project,'transcribe',{'candidate_id':payload['candidate_id'],'prompt_hash':digest((project.root/'prompts/transcribe.md').read_bytes()),
                                     'input_evidence':candidate_identity(project,item)})
    return out


def transcribe_one(project,payload):
    item=candidate_data(project,payload['candidate_id'])
    if payload.get('input_evidence') and payload['input_evidence']!=candidate_identity(project,item):
        raise StaleCandidateInput('candidate evidence changed after enqueue')
    images=prepare_images(project,item['assets'])
    if not images:
        return transcribe_text(project,payload,item)
    maximum=project.config['models']['profiles']['flymyd']['max_input_images']
    windows=[];start=0
    while start<len(images):
        window=images[start:start+maximum]
        # Byte cap in addition to image count. Never silently omit remaining input.
        while len(window)>1 and sum(Path(i['path']).stat().st_size for i in window)>project.config['models']['profiles']['flymyd']['max_raw_image_bytes_per_request']:
            window.pop()
        prompt=(project.root/'prompts/transcribe.md').read_text()
        prompt+='\n\nInput mapping:\n'+json.dumps({'candidate_id':payload['candidate_id'],'source_id':item['source_id'],
            'window_index':len(windows),'total_crops':len(images),'crop_offset':start,
            'images':[{k:v for k,v in i.items() if k!='path'} for i in window]},ensure_ascii=False)
        missing=missing_media_ordinals(project,item['source_id'],item['assets'])
        if missing:
            prompt+='\nOriginal gallery/media slots are missing. Do not join turns across unseen screenshots or invent their content. Mark gaps and truncation explicitly. '+json.dumps({'missing_source_ordinals':missing,'asset_source_ordinals':{a['id']:a['ordinal'] for a in item['assets']}})
        response,cached=ModelClient(project).call('transcribe',prompt,[(i['image_id'],i['path']) for i in window],TRANSCRIBE,
                                                max_output=project.config['models']['tasks']['transcribe']['max_output_tokens'])
        result=json.loads(output_text(response))
        conversions=normalize_evidence(result,window,allow_full_image_fallback=True)
        windows.append({'window':len(windows),'response_id':response.get('id'),'result':result,'images':window,'evidence_coordinate_transforms':conversions})
        atomic(project.root/'data/processed/transcription_windows'/f"{digest([payload,start])}.json",windows[-1])
        start+=len(window) if start+len(window)>=len(images) else max(1,len(window)-1)
    out=project.root/'data/processed/transcriptions'/f"{digest(payload)}.json"
    atomic(out,{'candidate_id':payload['candidate_id'],'source_id':item['source_id'],'windows':windows,
                'status':'transcribed_requires_reconstruction_and_verification','human_status':'pending_human',
                'input_payload':payload,'actual_prompt_hash':digest((project.root/'prompts/transcribe.md').read_bytes())})
    return out


def transcribe_text(project,payload,item):
    body=item['source'].get('text') or ''
    if len(body)<100:raise ValueError('no usable text source')
    prompt=(project.root/'prompts/transcribe.md').read_text()
    prompt+='\n\nThis is a DIRECT TEXT SOURCE, not images. Extract ONLY chat actually quoted in SOURCE_TEXT. The post author is not automatically a chat participant. Do not convert discussion or a fictional story into real chat turns. For each message, quote an exact source substring and provide its character start/end offsets (Python Unicode codepoint indices). Every evidence.source_id must match the supplied id. Do not add unseen turns. If no real chat transcript exists, return an empty conversations array.\n'
    prompt+=json.dumps({'source_id':item['source_id'],'SOURCE_TEXT':body},ensure_ascii=False)
    response,cached=ModelClient(project).call('transcribe_text',prompt,[],TEXT_TRANSCRIBE,
                                            max_output=project.config['models']['tasks']['transcribe']['max_output_tokens'])
    result=json.loads(output_text(response))
    offset_fixes=[]
    for conv in result['conversations']:
        previous=0
        for msg in conv['messages']:
            if not msg['evidence']:raise ValueError('text message has no source quote')
            for evidence in msg['evidence']:
                if evidence['source_id']!=item['source_id'] or not evidence['quote']:raise ValueError('invalid text source reference')
                quote=evidence['quote'];lo,hi=evidence['start'],evidence['end']
                if body[lo:hi]!=quote:
                    found=body.find(quote,previous)
                    if found<0:raise ValueError('text evidence quote is not in source')
                    offset_fixes.append({'given':[lo,hi],'canonical':[found,found+len(quote)]})
                    lo,hi=found,found+len(quote);evidence.update(start=lo,end=hi)
                previous=hi
                evidence['raw_path']=item['raw_path']
            quoted='\n'.join(e['quote'] for e in msg['evidence'])
            rendered='\n'.join(re.sub(r'^\s*> ?', '',line) for line in quoted.splitlines())
            for part in msg['parts']:
                if part.get('text') and part['text'] not in quoted and part['text'] not in rendered:
                    raise ValueError('transcribed text is not a verbatim source quote')
    out=project.root/'data/processed/transcriptions'/f"{digest(payload)}.json"
    atomic(out,{'candidate_id':payload['candidate_id'],'source_id':item['source_id'],
        'windows':[{'window':0,'response_id':response.get('id'),'result':result,'images':[],
                    'text_source':{'source_id':item['source_id'],'text':body,'raw_path':item['raw_path']},'offset_corrections':offset_fixes}],
        'status':'text_transcribed_requires_verification','human_status':'pending_human',
        'input_payload':payload,'actual_prompt_hash':digest((project.root/'prompts/transcribe.md').read_bytes())})
    return out


def run_model_tasks(project,kind,limit=50,workers=2,dry_run=False,cohort=None):
    if kind=='classify':queue_classification(project,limit=20000)
    if kind=='classify' and not dry_run:
        for job in project.rows("SELECT id,payload FROM tasks WHERE kind='classify' AND status='pending' AND EXISTS(SELECT 1 FROM relations r WHERE r.lhs=json_extract(tasks.payload,'$.candidate_id') AND r.rhs LIKE 'baseline:%' AND r.kind='same_original_post')"):
            if not known_baseline_has_additional_context(project,json.loads(job['payload'])['candidate_id']):
                project.execute("UPDATE tasks SET status='suppressed_known_baseline',error='No additional source text or image hashes beyond baseline',updated=? WHERE id=?",(now(),job['id']))
    cohort_ids=None
    if cohort:
        document=json.loads(Path(cohort).read_text())
        cohort_ids={r['candidate_id'] for r in document['candidate_records']}
    jobs=project.rows("""SELECT t.* FROM tasks t LEFT JOIN candidates c ON c.id=json_extract(t.payload,'$.candidate_id')
      LEFT JOIN sources s ON s.id=c.source_id LEFT JOIN conversations cv ON cv.id=c.id
      WHERE t.kind=? AND t.status IN ('pending','retry_wait') AND (t.lease_until IS NULL OR t.lease_until<?)
      ORDER BY coalesce(json_extract(cv.payload,'$.date'),json_extract(s.payload,'$.posted_at'),json_extract(s.payload,'$.source.captured_at'),'9999'),t.rowid LIMIT ?""",(kind,time.time(),20000 if cohort_ids is not None else limit))
    if cohort_ids is not None:jobs=[j for j in jobs if json.loads(j['payload'])['candidate_id'] in cohort_ids][:limit]
    if dry_run:return {'kind':kind,'queued':len(jobs),'dry_run':True}
    def work(job):
        claimed=project.claim_task(job['id'],1800)
        if not claimed:return 'not_claimed'
        try:
            out={'classify':classify_one,'transcribe':transcribe_one}[kind](project,json.loads(job['payload']))
            project.execute("UPDATE tasks SET status='succeeded',output_ref=?,lease_until=NULL,error=NULL,updated=? WHERE id=?",(str(out.relative_to(project.root)),now(),job['id']))
            return 'succeeded'
        except Exception as exc:
            if isinstance(exc,StaleCandidateInput):
                cid=json.loads(job['payload'])['candidate_id'];item=candidate_data(project,cid)
                queue_current_task(project,kind,{'candidate_id':cid,'prompt_hash':digest((project.root/f'prompts/{kind}.md').read_bytes()),
                                      'input_evidence':candidate_identity(project,item)})
                project.execute("UPDATE tasks SET status='superseded_input',lease_until=NULL,error=?,updated=? WHERE id=?",(str(exc),now(),job['id']))
                return 'superseded_input'
            if isinstance(exc,ModelResultError) and exc.status=='model_circuit_cooldown':
                state=project.rows("SELECT payload FROM source_status WHERE id='model_circuit'")
                until=json.loads(state[0]['payload']).get('retry_at',time.time()+300) if state else time.time()+300
                project.execute("UPDATE tasks SET status='retry_wait',lease_until=?,error=?,updated=? WHERE id=?",(until,str(exc),now(),job['id']))
                return 'retry_wait'
            if isinstance(exc,ModelConcurrencyBusy):
                project.execute("UPDATE tasks SET status='retry_wait',lease_until=?,error=?,updated=? WHERE id=?",(time.time()+10,str(exc),now(),job['id']))
                return 'retry_wait'
            state='deferred_resource' if isinstance(exc,BudgetExceeded) else 'quarantined'
            # No credential is present in these inputs; SDK errors are redacted by client as well.
            error=type(exc).__name__+': '+str(exc)[:600]
            project.execute('UPDATE tasks SET status=?,error=?,lease_until=NULL,updated=? WHERE id=?',(state,error,now(),job['id']))
            return state
    results=Counter()
    with ThreadPoolExecutor(max_workers=min(workers,project.config['models']['profiles']['flymyd']['max_concurrency'])) as pool:
        for i,state in enumerate(pool.map(work,jobs),1):
            results[state]+=1
            if i%10==0:print(json.dumps({'kind':kind,'processed':i,**results}),flush=True)
    return dict(results)
