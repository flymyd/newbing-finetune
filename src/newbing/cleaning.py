from __future__ import annotations

import json
import re
from collections import Counter

from .core import atomic, digest, now
from .assets import ordered_source_assets,missing_media_ordinals


def record_messages(row):
    if isinstance(row.get('messages'), list):
        return row['messages']
    if row.get('messages_json'):
        return json.loads(row['messages_json'])
    raise ValueError('no typed source messages; plain text fallback needs explicit review')


def part_text(part):
    kind = part.get('type')
    if kind == 'text':
        return part.get('text', ''), None
    if kind == 'code_block':
        text = part.get('text', '')
        if text.startswith('```'):
            return text, None
        return f"```{part.get('language', '')}\n{text}\n```", 'code_fence_rendered'
    if kind == 'table':
        if isinstance(part.get('text'),str) and part['text'].strip():
            return part['text'],'table_markdown_rendered'
        headers, rows = part.get('headers'), part.get('rows')
        if isinstance(headers,list) and isinstance(rows,list) and all(isinstance(r,list) and len(r)==len(headers) for r in rows):
            def line(r):
                return '| ' + ' | '.join(str(c).replace('|','\\|').replace('\n','<br>') for c in r) + ' |'
            return '\n'.join([line(headers),line(['---']*len(headers))]+[line(r) for r in rows]), 'table_markdown_rendered'
        return '', 'unreadable_table'
    if kind == 'safety_block':
        # Provider marks both true assistant refusals and UI notices with this type.
        # Keep the archive but require review before using it as a target.
        return part.get('text',''), 'safety_block_needs_ui_review'
    if kind in ('image','image_output','embed'):
        return '', 'visual_or_embed_dependency_review'
    if kind in ('search_query','citations','citation','suggestions','suggestion','ui_notice'):
        return '', 'ui_part_removed:' + kind
    return '', 'unknown_part_type:' + str(kind)


def project_messages(messages):
    output, transforms, issues = [], [], []
    for index, message in enumerate(messages):
        role = message.get('role','unknown')
        content = message.get('content',message.get('parts',[]))
        if isinstance(content,str):
            content = [{'type':'text','text':content}]
        parts=[]
        for part in content:
            text,flag=part_text(part)
            if text:
                parts.append(text)
            if flag:
                transforms.append({'message_index':index,'operation':flag})
                if not flag.startswith('ui_part_removed') and flag not in ('code_fence_rendered','table_markdown_rendered'):
                    issues.append({'message_index':index,'reason':flag})
        if not parts:
            # A pure search-state UI item is not a real turn; a media/empty unknown item is a gap.
            pure_ui=bool(content) and all(p.get('type') in ('search_query','citations','citation','suggestions','suggestion','ui_notice') for p in content)
            if not pure_ui:
                issues.append({'message_index':index,'reason':'empty_textual_turn_or_media_gap'})
            continue
        text='\n\n'.join(parts)
        meta=message.get('meta') or {}
        if role not in ('user','assistant'):
            issues.append({'message_index':index,'reason':'unknown_role'})
        if meta.get('truncated') or message.get('truncated_start') or message.get('truncated_end'):
            issues.append({'message_index':index,'reason':'message_truncated'})
        if message.get('context_gap_before'):
            issues.append({'message_index':index,'reason':'context_gap_before'})
        if '⟦unreadable⟧' in text:
            issues.append({'message_index':index,'reason':'unreadable_text'})
        output.append({'from':{'user':'human','assistant':'gpt'}.get(role,'unknown'),'value':text,'source_message_index':index})
    return output,transforms,issues


def validate_sharegpt(sample):
    errors=[]
    if set(sample)!= {'id','conversations'}:
        errors.append('top_level_fields')
    if not isinstance(sample.get('id'),str) or not sample['id'].strip():
        errors.append('invalid_id')
    turns=sample.get('conversations')
    if not isinstance(turns,list):
        return errors+['conversations_not_array']
    if len(turns)<4:
        errors.append('fewer_than_two_rounds')
    if len(turns)%2:
        errors.append('odd_turn_count')
    for index,turn in enumerate(turns):
        if not isinstance(turn,dict) or set(turn)!={'from','value'}:
            errors.append(f'turn_fields:{index}')
            continue
        if turn['from']!=('human' if index%2==0 else 'gpt'):
            errors.append(f'role_sequence:{index}')
        if not isinstance(turn['value'],str) or not turn['value'].strip():
            errors.append(f'non_text_or_empty:{index}')
    return errors


def date_bucket(source):
    date=(source.get('captured_at') or source.get('date') or '')[:10]
    if '2023-02-07'<=date<='2023-02-16':
        return 'early','probable',date
    if '2023-02-17'<=date<='2023-02-28':
        return 'transition','probable',date
    if '2023-03-01'<=date<='2023-04-30':
        return 'later_mar_apr','probable',date
    if '2023-05-01'<=date<='2023-12-31':
        return 'later_2023','probable',date
    return 'unknown','unknown',None


def archive_date(capture_claim,posted_at):
    if capture_claim:
        bucket,confidence,date=date_bucket({'captured_at':capture_claim})
        if posted_at and str(capture_claim)[:10]>str(posted_at)[:10]:
            return 'unknown','unknown',None,'capture_claim_conflicts_with_post_upper_bound'
        return bucket,confidence,date,'upstream_capture_date_claim'
    bucket,confidence,date=date_bucket({'captured_at':posted_at})
    # Contemporary publication can bound New Bing within its early launch window.
    # A later publication alone cannot tell early screenshots from later chats.
    if bucket=='early':return bucket,confidence,date,'publication_upper_bound_only'
    return 'unknown','unknown',None,'publication_upper_bound_only'


def reconstruct_baselines(project, limit=100000):
    candidates=project.rows("SELECT c.id,c.source_id,s.payload,s.raw_path FROM candidates c JOIN sources s ON s.id=c.source_id WHERE s.kind='baseline' ORDER BY c.rowid LIMIT ?",(limit,))
    reasons=Counter()
    for candidate in candidates:
        src=json.loads(candidate['payload']);row=src['record']
        try:
            messages=record_messages(row)
        except Exception as exc:
            reasons['invalid_typed_messages']+=1
            continue
        turns,transforms,issues=project_messages(messages)
        sample={'id':candidate['id'],'conversations':[{k:m[k] for k in ('from','value')} for m in turns]}
        structural=validate_sharegpt(sample)
        source_date=dict(row.get('source') or {})
        date_evidence=[]
        original_post_date=None
        if row['id'].startswith('reddit:'):
                post=project.rows('SELECT id,payload FROM sources WHERE id=?',('reddit:post:'+row['id'].split(':',1)[1],))
                if post:
                    posted=json.loads(post[0]['payload']).get('posted_at')
                    original_post_date=posted
                    if not source_date.get('captured_at'):source_date['captured_at']=posted
                    date_evidence.append({'type':'original_post_timestamp_upper_bound','source_id':post[0]['id'],'value':posted})
        else:
            tweet_id=re.search(r'(?:twitter|x)\.com/[^/]+/status/(\d+)',source_date.get('url') or '')
            if tweet_id:
                from .sources import twitter_date
                posted=twitter_date(tweet_id.group(1))
                original_post_date=posted
                if not source_date.get('captured_at'):source_date['captured_at']=posted
                date_evidence.append({'type':'tweet_snowflake_post_timestamp_upper_bound','value':posted})
        if row.get('source',{}).get('captured_at'):
            date_evidence.append({'type':'upstream_capture_date_claim','value':row['source']['captured_at']})
        bucket,confidence,date,date_semantics=archive_date(row.get('source',{}).get('captured_at'),original_post_date)
        exclusion=list(structural)+[x['reason'] for x in issues]
        if row.get('truncated_start') or row.get('truncated_end'):
            exclusion.append('source_conversation_truncated')
        if row.get('kind') not in ('conversation',None):
            exclusion.append('not_conversation_kind')
        if bucket=='unknown':
            exclusion.append('date_unknown')
        assets=ordered_source_assets(project,candidate['source_id'])
        if not assets:
            alternatives=project.rows("""SELECT other.id FROM sources other WHERE other.kind='baseline'
                AND json_extract(other.payload,'$.upstream_id')=? AND other.id!=?""",(row['id'],candidate['source_id']))
            for alternative in alternatives:
                found=ordered_source_assets(project,alternative['id'])
                if found:
                    assets=found;break
        typed=[]
        for i,m in enumerate(messages):
            typed.append({**m,'message_id':f"{candidate['id']}#m{i}",
                          'evidence':[{'source_id':candidate['source_id'],'raw_path':candidate['raw_path'],
                                       'jsonl_line':src['line'],'message_index':i,'asset_ids':[a['asset_id'] for a in assets]}]})
        payload={'conversation_id':candidate['id'],'upstream_id':row['id'],'source_ids':[candidate['source_id']],
                 'source':row.get('source') or {},'messages':typed,'projected':sample,
                 'message_projection_map':[t['source_message_index'] for t in turns],
                 'date_bucket':bucket,'date_confidence':confidence,'date':date,
                 'date_semantics':date_semantics,'conversation_date':date if date_semantics=='upstream_capture_date_claim' else None,
                 'discovery_date_bucket':date_bucket({'captured_at':original_post_date})[0],
                 'date_evidence':date_evidence,
                 'posted_at':original_post_date,
                 'truncated_start':bool(row.get('truncated_start')),'truncated_end':bool(row.get('truncated_end')),
                 'kind':row.get('kind'),'client':row.get('client'),'transforms':transforms,
                 'automatic_exclusions':sorted(set(exclusion)),'verification_status':'upstream_model_transcription_unverified',
                 'human_status':'pending_human','upstream_notes':row.get('notes') or row.get('upstream_notes'),
                 'expected_asset_count':len(row.get('images') or row.get('images_file_name') or []),
                 'asset_ids':[a['asset_id'] for a in assets]}
        project.execute('INSERT OR REPLACE INTO conversations VALUES (?,?,?,?)',
                        (candidate['id'],candidate['source_id'],json.dumps(payload,ensure_ascii=False),now()))
        reasons.update(exclusion)
    archive=[json.loads(r['payload']) for r in project.rows('SELECT payload FROM conversations ORDER BY id')]
    atomic(project.root/'data/processed/conversations.all.jsonl',''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in archive),raw=True)
    report={'baseline_candidates':len(candidates),'archived_conversations':len(archive),'exclusion_reasons':dict(reasons),
            'note':'Archive only. No claim of visual verification or final training eligibility.'}
    atomic(project.root/'reports/baseline-projection.json',report)
    return report


DESCRIPTIVE_SOURCE_KEYS=('license_notes','rights_status','removal_status','retrieval_status','fetched_at')


def embedded_source(source):
    """Copy of the source record used inside a conversation record.

    Rights/retrieval bookkeeping added to SourceRecord after archiving (and null
    archived/conversation dates) is descriptive metadata, not chat evidence. It stays
    in the SourceRecord and export provenance; keeping it out of the conversation
    copy prevents metadata backfills from invalidating evidence-bound verification.
    """
    return {k:v for k,v in source.items()
            if k not in DESCRIPTIVE_SOURCE_KEYS and not (k in ('archived_at','conversation_date') and v is None)}


def mark_source_gaps(conv,asset_ordinals,missing):
    """Keep missing album slots visible; no text is invented or joined across them."""
    previous=None;seen=[]
    for message in conv['messages']:
        positions=sorted({asset_ordinals[e['asset_id']] for e in message['evidence'] if e.get('asset_id') in asset_ordinals})
        if not positions:continue
        low,high=positions[0],positions[-1];seen+=positions
        if any(low<m<high for m in missing):
            message['truncated_start']=True;message['truncated_end']=True
        if previous is not None and any(previous<m<low for m in missing):message['context_gap_before']=True
        previous=high
    if seen:
        conv['truncated_start']=conv['truncated_start'] or any(m<min(seen) for m in missing)
        conv['truncated_end']=conv['truncated_end'] or any(m>max(seen) for m in missing)


def reconstruct_transcriptions(project):
    from .model_tasks import candidate_data,candidate_identity
    successes=project.rows("SELECT output_ref FROM tasks WHERE kind='transcribe' AND status='succeeded' ORDER BY updated DESC,id DESC")
    count=0;seen_candidates=set();active_ids=set()
    for task in successes:
        d=json.loads((project.root/task['output_ref']).read_text())
        if d['candidate_id'] in seen_candidates:continue
        current_assets=ordered_source_assets(project,d['source_id'])
        given_assets=list(dict.fromkeys(i['asset_id'] for w in d['windows'] for i in w['images']))
        if given_assets!=[a['id'] for a in current_assets]:continue
        seen_candidates.add(d['candidate_id'])
        missing=missing_media_ordinals(project,d['source_id'],current_assets)
        asset_ordinals={a['id']:a['ordinal'] for a in current_assets}
        source=json.loads(project.rows('SELECT payload FROM sources WHERE id=?',(d['source_id'],))[0]['payload'])
        classification_options=project.rows("SELECT output_ref FROM tasks WHERE kind='classify' AND status='succeeded' AND json_extract(payload,'$.candidate_id')=? ORDER BY updated DESC",(d['candidate_id'],))
        identity=candidate_identity(project,candidate_data(project,d['candidate_id']))
        classification_rows=[];classification_doc=None
        for option in classification_options:
            document=json.loads((project.root/option['output_ref']).read_text())
            if document.get('input_payload',{}).get('input_evidence')==identity:
                classification_rows=[option];classification_doc=document;break
        parts=[]
        for window in d['windows']:
            mapping={i['image_id']:i for i in window['images']}
            for conv in window['result']['conversations']:
                conv=json.loads(json.dumps(conv))
                for msg in conv['messages']:
                    for evidence in msg['evidence']:
                        if 'image_id' not in evidence:
                            continue
                        image=mapping[evidence['image_id']]
                        original=project.rows('SELECT width,height FROM assets WHERE id=?',(image['asset_id'],))[0]
                        x0,y0,x1,y1=image['box'];r=evidence['region']
                        evidence.update(source_id=d['source_id'],asset_id=image['asset_id'],crop_id=evidence['image_id'],
                                        original_region=[(x0+r[0]*(x1-x0))/original['width'],(y0+r[1]*(y1-y0))/original['height'],
                                                         (x0+r[2]*(x1-x0))/original['width'],(y0+r[3]*(y1-y0))/original['height']])
                def signature(m):return digest([m['role'],m['parts']])
                merged=False
                if parts and conv['messages']:
                    a,b=parts[-1]['messages'],conv['messages']
                    for overlap in range(min(len(a),len(b)),0,-1):
                        if [signature(m) for m in a[-overlap:]]==[signature(m) for m in b[:overlap]] and \
                           (overlap>=2 or len(json.dumps(b[0]['parts'],ensure_ascii=False))>120):
                            for previous,current in zip(a[-overlap:],b[:overlap]):previous['evidence']+=current['evidence']
                            parts[-1]['messages']+=b[overlap:]
                            parts[-1]['truncated_end']=conv['truncated_end'];merged=True;break
                if not merged:parts.append(conv)
        for index,conv in enumerate(parts):
            mark_source_gaps(conv,asset_ordinals,missing)
            cid=d['candidate_id']+f':conversation:{index}'
            active_ids.add(cid)
            for i,m in enumerate(conv['messages']):m['message_id']=f'{cid}#m{i}'
            turns,transforms,issues=project_messages(conv['messages'])
            sample={'id':cid,'conversations':[{k:m[k] for k in ('from','value')} for m in turns]}
            exclusions=validate_sharegpt(sample)+[i['reason'] for i in issues]
            if classification_doc:
                classification=classification_doc['result']
                if classification['product']!='bing':exclusions.append('bing_product_not_confirmed_by_classification')
                if classification['content_kind'] not in ('conversation','mixed'):exclusions.append('classification_not_actual_conversation')
                if classification['authenticity_status']=='suspicious':exclusions.append('suspicious_source_authenticity')
            elif not d['candidate_id'].startswith('baseline:'):
                exclusions.append('missing_product_classification')
            if conv['truncated_start'] or conv['truncated_end']:exclusions.append('source_conversation_truncated')
            if any(w['result']['visual_context_dependency']!='absent' for w in d['windows']):exclusions.append('visual_context_dependency_review')
            source_date=source.get('posted_at') or ''
            bucket,confidence,date,date_semantics=archive_date(source.get('conversation_date'),source_date)
            date_evidence=[{'type':'source_publication_time_upper_bound','value':source_date,'source_id':d['source_id']}] if source_date else []
            if d['candidate_id'].startswith('baseline:'):
                original=project.rows('SELECT payload FROM conversations WHERE id=?',(d['candidate_id'],))
                if original:
                    baseline=json.loads(original[0]['payload'])
                    bucket,confidence,date=baseline['date_bucket'],baseline['date_confidence'],baseline.get('date')
                    date_semantics=baseline.get('date_semantics','upstream_capture_date_claim')
                    date_evidence=baseline.get('date_evidence',[])
            if bucket=='unknown':exclusions.append('date_unknown')
            payload={'conversation_id':cid,'source_ids':[d['source_id']],'source':embedded_source(source),'messages':conv['messages'],
                     'projected':sample,'message_projection_map':[m['source_message_index'] for m in turns],
                     'date_bucket':bucket,'date_confidence':confidence,'date':date,
                     'date_semantics':date_semantics,'conversation_date':date if date_semantics=='upstream_capture_date_claim' else None,
                     'discovery_date_bucket':date_bucket({'captured_at':source_date})[0],
                     'date_evidence':date_evidence,
                     'truncated_start':conv['truncated_start'],'truncated_end':conv['truncated_end'],
                     'automatic_exclusions':sorted(set(exclusions)),'transforms':transforms,
                     'verification_status':'project_model_transcribed_needs_verification','human_status':'pending_human',
                     'asset_ids':list(dict.fromkeys(e['asset_id'] for m in conv['messages'] for e in m['evidence'] if 'asset_id' in e)),
                     'text_source':next((w['text_source'] for w in d['windows'] if 'text_source' in w),None),
                     'classification_ref':classification_rows[0]['output_ref'] if classification_rows else None,
                     'missing_source_media_ordinals':missing,'asset_source_ordinals':asset_ordinals,
                     'notes':conv.get('notes'),'transcription_result':task['output_ref']}
            project.execute('INSERT OR REPLACE INTO conversations VALUES (?,?,?,?)',(cid,d['source_id'],json.dumps(payload,ensure_ascii=False),now()))
            count+=1
    retired=0
    for row in project.rows("SELECT id,payload FROM conversations WHERE json_extract(payload,'$.transcription_result') IS NOT NULL AND json_extract(payload,'$.derived_from') IS NULL"):
        if row['id'] in active_ids:continue
        old=json.loads(row['payload'])
        if old.get('superseded'):continue
        old['superseded']=True
        old['automatic_exclusions']=sorted(set(old.get('automatic_exclusions',[])+['superseded_transcription_version']))
        atomic(project.root/'data/processed/superseded_conversations'/f"{digest([row['id'],old])}.json",old)
        project.execute('UPDATE conversations SET payload=?,updated=? WHERE id=?',(json.dumps(old,ensure_ascii=False),now(),row['id']))
        retired+=1
    return {'reconstructed_model_conversations':count,'transcription_groups':len(seen_candidates),'retired_conversations':retired}


def save_corpus(project):
    rows=[json.loads(r['payload']) for r in project.rows('SELECT payload FROM conversations ORDER BY id')]
    atomic(project.root/'data/processed/conversations.all.jsonl',''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),raw=True)
    return len(rows)


def derive_continuous_segments(project):
    """Recover complete contiguous multi-turn fragments without joining across unknown gaps."""
    parents=[row for row in project.rows('SELECT id,source_id,payload FROM conversations') if not row['id'].startswith('segment:')]
    count=0;active=set()
    for row in parents:
        parent=json.loads(row['payload'])
        if parent.get('superseded'):continue
        if not parent.get('automatic_exclusions'):continue
        fatal={'not_conversation_kind','bing_product_not_confirmed_by_classification','classification_not_actual_conversation','suspicious_source_authenticity','missing_product_classification'}
        if fatal.intersection(parent['automatic_exclusions']):continue
        run=[];runs=[]
        def finish():
            nonlocal run
            if run and run[-1][1]['role']=='user':run=run[:-1]
            if len(run)>=4:runs.append(run)
            run=[]
        for index,message in enumerate(parent['messages']):
            turns,_,issues=project_messages([message])
            # A known gap resets continuity; if current message is complete, it can start a new run.
            if message.get('context_gap_before'):finish()
            hard_issues=[i for i in issues if i['reason']!='context_gap_before']
            if len(turns)!=1 or hard_issues or message.get('role') not in ('user','assistant'):
                finish();continue
            expected='user' if len(run)%2==0 else 'assistant'
            if message['role']!=expected:
                finish()
                if message['role']!='user':continue
            run.append((index,message))
        finish()
        for segment in runs:
            start,end=segment[0][0],segment[-1][0]
            if start==0 and end==len(parent['messages'])-1 and not parent.get('truncated_start') and not parent.get('truncated_end'):continue
            parent_hash=digest(parent)
            cid='segment:'+digest([parent['conversation_id'],parent_hash,[m['message_id'] for _,m in segment]])[:32]
            active.add(cid)
            import copy
            c=copy.deepcopy(parent);c['conversation_id']=cid;c['derived_from']=parent['conversation_id']
            c['parent_content_hash']=parent_hash
            c['parent_message_ids']=[m['message_id'] for _,m in segment]
            c['messages']=[copy.deepcopy(m) for _,m in segment]
            # Initial missing context is a property to verify, never erased from the archive.
            for m in c['messages']:m['context_gap_before']=False
            turns,transforms,issues=project_messages(c['messages'])
            c['projected']={'id':cid,'conversations':[{k:m[k] for k in ('from','value')} for m in turns]}
            c['message_projection_map']=[m['source_message_index'] for m in turns]
            c['transforms']+=transforms+[{'operation':'extract_contiguous_segment','parent':parent['conversation_id'],'start_message':start,'end_message_inclusive':end,'initial_context_requires_verification':True}]
            c['automatic_exclusions']=validate_sharegpt(c['projected'])+[i['reason'] for i in issues]+(['date_unknown'] if c.get('date_bucket')=='unknown' else [])
            c['verification_status']='segment_requires_context_verification'
            c['segment_context_gap']=True
            c['truncated_start']=True
            c['truncated_end']=True
            project.execute('INSERT OR REPLACE INTO conversations VALUES (?,?,?,?)',(cid,row['source_id'],json.dumps(c,ensure_ascii=False),now()));count+=1
    retired=0
    for row in project.rows("SELECT id,payload FROM conversations WHERE id LIKE 'segment:%'"):
        if row['id'] in active:continue
        old=json.loads(row['payload'])
        if old.get('superseded'):continue
        old['superseded']=True
        old['automatic_exclusions']=sorted(set(old.get('automatic_exclusions',[])+['superseded_derived_segment']))
        old['verification_status']='superseded_parent_or_range_changed'
        atomic(project.root/'data/processed/superseded_segments'/f"{digest([row['id'],old])}.json",old)
        project.execute('UPDATE conversations SET payload=?,updated=? WHERE id=?',(json.dumps(old,ensure_ascii=False),now(),row['id']))
        retired+=1
    return {'derived_continuous_multiturn_segments':count,'retired_segments':retired}


UI_BANNER_RE = re.compile(
    r'^(?:'
    r'Sorry, this conversation has reached its limit\b.*|'
    r'Unfortunately, we need to move on!\s*Click ["“]New topic.*|'
    r'Thanks for this conversation!\s*I[\'’]ve reached my limit.*|'
    r'Sorry, it looks like I need to chat about something else\.\s*Click ["“]New topic.*|'
    r'Oops, I think we[\'’]ve reached the end of this conversation\.\s*Click ["“]New topic.*|'
    r'Sorry, you[\'’]ve reached your daily limit to chat\..*|'
    r'Sorry, I have reached my limit for this conversation\..*|'
    r'It might be time to move onto a new topic\.\s*Let[\'’]s start over\.|'
    r'Talvez seja hora de passar para um novo tópico\.\s*Vamos recomeçar\.|'
    r'Unsafe image content detected\b.*|'
    r'Oops!\s*Try another prompt\b.*|'
    r'Content warning\s+This prompt has been blocked\b.*|'
    r'Help is available\.\s*If you are having thoughts about self harm\b.*'
    r')$',
    re.I | re.S,
)

ASSISTANT_REFUSAL_RE = re.compile(
    r'^(?:'
    r'I[\'’]m sorry,? but (?:I prefer not to continue|this conversation is over|I disagree|this chat is getting|I don[\'’]t believe you)\b.*|'
    r'I am sorry, I (?:don[\'’]t know how to discuss|am not quite sure how to respond)\b.*|'
    r'My mistake, I can[\'’]t give a response to that right now\..*|'
    r'Hmm(?:\.{3}|…)\s*let[\'’]s try a different topic\..*|'
    r'Sorry!\s*That[\'’]s on me, I can[\'’]t give a response to that right now\..*'
    r')$',
    re.I | re.S,
)

SEARCH_PREFIX_RE = re.compile(r'^(?:Searching for:\s*[^\n]+\n+|Generating answers for you\.{0,3}\n+)+', re.I)
FUN_FACT_SUFFIX_RE = re.compile(r'(?:\n+|\s+)(?:Fun fact, did you know|By the way, did you know|Out of curiosity,)\b[^\n]*$', re.I)
LEARN_MORE_SUFFIX_RE = re.compile(r'\n+Learn more:\s*(?:\d+\.|[^\n]+).*$', re.I | re.S)
COUNTER_SUFFIX_RE = re.compile(r'\n+\d+\s+of\s+\d+\s*$', re.I)
UPSTREAM_PLACEHOLDER_RE = re.compile(
    r'(?:<no_response>|\[unreadable\]|'
    r'\[[^\]]*(?:not visible in screenshot|not captured in screenshot|truncated in source|cut off by screenshot|unreadable in screenshot|code omitted by user|ends mid-response)[^\]]*\]|'
    r'^\[image of [^\]]+\]$)',
    re.I,
)
CONTEXT_DEPENDENT_OPENING_RE = re.compile(
    r'^(?:yes|yes,?\s*please|no|nope|ok(?:ay)?(?:,\s*sydney)?|sure|absolutely|sydney|explain|continue|go on|'
    r'why|how|where is it|lol,?\s*source|oh\.?\s*my\.?\s*god\.?|i don[\'’]t care|top middle|middle bottom|n|y)[.!?]*$',
    re.I,
)


def sft_part_text(part):
    kind = part.get('type')
    if kind == 'safety_block':
        txt = (part.get('text') or '').strip()
        if UI_BANNER_RE.match(txt):
            return '', 'ui_part_removed:safety_block_ui_banner'
        if ASSISTANT_REFUSAL_RE.match(txt) and not txt.startswith('['):
            return txt, 'assistant_refusal_retained'
        return txt, 'safety_block_needs_ui_review'
    return part_text(part)


def clean_sft_turn_text(text):
    ops = []
    for name, regex in (
        ('ui_prefix_removed:searching_or_generating', SEARCH_PREFIX_RE),
        ('ui_suffix_removed:learn_more', LEARN_MORE_SUFFIX_RE),
        ('ui_suffix_removed:turn_counter', COUNTER_SUFFIX_RE),
        ('ui_suffix_removed:fun_fact_prompt', FUN_FACT_SUFFIX_RE),
    ):
        updated = regex.sub('', text)
        if updated != text:
            ops.append(name)
            text = updated
    return text.strip(), ops


def project_sft_message(message):
    role = message.get('role', 'unknown')
    content = message.get('content', message.get('parts', []))
    if isinstance(content, str):
        content = [{'type': 'text', 'text': content}]
    parts, ops, issues = [], [], []
    for part in content:
        t, flag = sft_part_text(part)
        if t:
            parts.append(t)
        if flag:
            ops.append(flag)
            if not flag.startswith('ui_part_removed') and flag not in ('code_fence_rendered', 'table_markdown_rendered', 'assistant_refusal_retained'):
                issues.append(flag)
    if not parts:
        pure_ui = bool(content) and all(
            p.get('type') in ('search_query', 'citations', 'citation', 'suggestions', 'suggestion', 'ui_notice')
            or (p.get('type') == 'safety_block' and UI_BANNER_RE.match((p.get('text') or '').strip()))
            for p in content
        )
        if not pure_ui:
            issues.append('empty_textual_turn_or_media_gap')
        return None, ops, issues, pure_ui
    text, text_ops = clean_sft_turn_text('\n\n'.join(parts))
    ops.extend(text_ops)
    if not text:
        return None, ops, issues + ['empty_after_ui_strip'], False
    meta = message.get('meta') or {}
    if role not in ('user', 'assistant'):
        issues.append('unknown_role')
    if meta.get('truncated') or message.get('truncated_start') or message.get('truncated_end'):
        issues.append('message_truncated')
    if '⟦unreadable⟧' in text or UPSTREAM_PLACEHOLDER_RE.search(text):
        issues.append('unreadable_text')
    return {'from': {'user': 'human', 'assistant': 'gpt'}.get(role, 'unknown'), 'value': text}, ops, issues, False


def project_sft_messages(messages):
    output, transforms, issues = [], [], []
    for index, message in enumerate(messages):
        turn, ops, msg_issues, pure_ui = project_sft_message(message)
        for op in ops:
            transforms.append({'message_index': index, 'operation': op})
        for reason in msg_issues:
            issues.append({'message_index': index, 'reason': reason})
        if message.get('context_gap_before'):
            issues.append({'message_index': index, 'reason': 'context_gap_before'})
        if turn is not None:
            output.append({**turn, 'source_message_index': index})
    return output, transforms, issues


def extract_sft_segments(parent, bad_message_ids=frozenset()):
    """Extract contiguous multi-turn (>=2 rounds) and single-round (1 round) SFT segments."""
    run, segments = [], []

    def flush():
        nonlocal run
        if run and run[-1]['turn']['from'] == 'human':
            run = run[:-1]
        # Trim context-dependent bare opening replies when a segment starts mid-conversation.
        while len(run) >= 2 and (run[0]['source_index'] > 0 or parent.get('truncated_start')):
            opening = run[0]['turn']['value'].strip()
            if CONTEXT_DEPENDENT_OPENING_RE.match(opening):
                run = run[2:]
            else:
                break
        if len(run) >= 4:
            segments.append({'kind': 'multiturn', 'items': list(run)})
        elif len(run) == 2:
            segments.append({'kind': 'single_round', 'items': list(run)})
        run = []

    for index, message in enumerate(parent.get('messages', [])):
        if message.get('context_gap_before'):
            flush()
        turn, ops, issues, pure_ui = project_sft_message(message)
        if pure_ui:
            continue
        if turn is None or issues or message.get('message_id') in bad_message_ids:
            flush()
            continue
        expected = 'human' if len(run) % 2 == 0 else 'gpt'
        if turn['from'] != expected:
            flush()
            if turn['from'] != 'human':
                continue
        run.append({
            'turn': turn,
            'source_index': index,
            'message_id': message.get('message_id', f"{parent['conversation_id']}#m{index}"),
            'evidence': message.get('evidence', []),
            'ops': ops,
        })
    flush()
    return segments

