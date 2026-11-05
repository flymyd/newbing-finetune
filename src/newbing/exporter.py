from __future__ import annotations

from collections import Counter,defaultdict
import json
from pathlib import Path
import re
import html

from jsonschema import Draft202012Validator

from .cleaning import validate_sharegpt
from .core import atomic,digest,now


class Groups:
    def __init__(self):self.parents={}
    def root(self,x):
        self.parents.setdefault(x,x)
        if self.parents[x]!=x:self.parents[x]=self.root(self.parents[x])
        return self.parents[x]
    def union(self,a,b):
        a,b=self.root(a),self.root(b)
        if a!=b:self.parents[max(a,b)]=min(a,b)


def context_annotations(project,conversation):
    cues=[]
    for message in conversation['messages']:
        if message.get('role')!='user':continue
        for part in message.get('content',message.get('parts',[])):
            text=part.get('text','')
            for label,pattern in [('roleplay_literal_cue',r'(?i)\b(?:pretend|role[- ]?play|act as)\b|扮演|假装'),
                                  ('instruction_override_literal_cue',r'(?i)ignore (?:all |the )?(?:previous|prior) instructions|忽略.{0,8}(?:指令|限制)')]:
                match=re.search(pattern,text)
                if match:cues.append({'label':label,'message_id':message['message_id'],'quote':match[0]})
    source_annotations=[]
    if conversation.get('transcription_result'):
        path=project.root/conversation['transcription_result']
        if path.is_file():
            for window in json.loads(path.read_text()).get('windows',[]):
                result=window['result']
                source_annotations.append({k:result[k] for k in ('contains_simulated_dialogue','visual_context_dependency','uncertainties') if k in result})
    return {'method':'literal_cues_and_source_labels_v1','literal_cues':cues,'source_model_annotations':source_annotations,
            'limitation':'Literal cues are discovery labels, not proof of a jailbreak, hidden system prompt, model version or authenticity.'}


def grouping(conversations,relations=()):
    groups=Groups();assets={};texts={};sources={}
    for c in conversations:
        cid=c['conversation_id'];groups.root(cid)
        if c.get('derived_from'):groups.union(cid,c['derived_from'])
        for sid in c.get('source_ids',[]):
            if sid in sources:groups.union(cid,sources[sid])
            else:sources[sid]=cid
        for aid in c.get('asset_ids',[]):
            if aid in assets:groups.union(cid,assets[aid])
            else:assets[aid]=cid
        exact=digest(c['projected']['conversations'])
        if exact in texts:groups.union(cid,texts[exact])
        else:texts[exact]=cid
    ids={c['conversation_id'] for c in conversations}
    for relation in relations:
        left=[cid for cid in ids if cid==relation['lhs'] or cid.startswith(relation['lhs']+':conversation:')]
        right=[cid for cid in ids if cid==relation['rhs'] or cid.startswith(relation['rhs']+':conversation:')]
        for a in left:
            for b in right:groups.union(a,b)
    return {c['conversation_id']:groups.root(c['conversation_id']) for c in conversations}


def export_release(project,release_id):
    from .verification import verification_contract,evidence_identity
    from .assets import ordered_source_assets,missing_media_ordinals
    contract=verification_contract(project)
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._-]{0,79}',release_id):raise ValueError('invalid release id')
    out=project.root/'exports'/release_id
    if (out/'manifest.json').exists():raise ValueError('release already frozen; choose a new release id')
    all_conversations=[json.loads(r['payload']) for r in project.rows('SELECT payload FROM conversations ORDER BY id')]
    media_audit_path=project.root/'reports/media-decode-audit.json'
    media_audit=json.loads(media_audit_path.read_text()) if media_audit_path.is_file() else {}
    invalid_assets={r['asset_id'] for r in media_audit.get('issues',[])}
    unreviewed_animations={r['asset_id'] for r in media_audit.get('animated_requires_frame_coverage',[])}
    groups=grouping(all_conversations,project.rows("SELECT lhs,rhs,kind FROM relations WHERE kind IN ('same_original_post','same_conversation','repost','near_duplicate_confirmed','exact_reply_shared','near_duplicate_candidate')"))
    verifications=defaultdict(list)
    for t in project.rows("SELECT output_ref FROM tasks WHERE kind='verify' AND status='succeeded' ORDER BY updated DESC"):
        d=json.loads((project.root/t['output_ref']).read_text())
        verifications[d['conversation_id']].append((t['output_ref'],d))
    selected=[];excluded=[];seen_text=set()
    validator=Draft202012Validator(json.loads((project.root/project.policy['export']['sample_schema']).read_text()))
    for c in all_conversations:
        cid=c['conversation_id'];reasons=list(c['automatic_exclusions'])
        review_ids=[cid,c.get('derived_from') or cid,*c.get('source_ids',[])]
        if any(project.rows("SELECT 1 FROM relations WHERE lhs=? AND kind='authenticity_review_required' LIMIT 1",(target,)) for target in review_ids):
            reasons.append('authenticity_review_required')
        if invalid_assets.intersection(c['asset_ids']):reasons.append('source_asset_decode_failure')
        if unreviewed_animations.intersection(c['asset_ids']):reasons.append('animated_evidence_requires_complete_frame_coverage')
        if c.get('transcription_result'):
            transcript=json.loads((project.root/c['transcription_result']).read_text())
            current_assets=ordered_source_assets(project,transcript['source_id'])
            given=list(dict.fromkeys(i['asset_id'] for w in transcript['windows'] for i in w['images']))
            if given!=[a['id'] for a in current_assets] or c.get('missing_source_media_ordinals',[])!=missing_media_ordinals(project,transcript['source_id'],current_assets):
                reasons.append('candidate_media_changed_since_transcription')
        for sid in c['source_ids']:
            pending=project.rows("SELECT 1 FROM tasks WHERE kind='download' AND json_extract(payload,'$.source_id')=? AND json_extract(payload,'$.verify_upstream_bytes')=1 AND status!='succeeded' LIMIT 1",(sid,))
            if pending:reasons.append('upstream_asset_bytes_not_verified')
        verification=next(((path,d) for path,d in verifications[cid]
                           if d['conversation_hash']==digest(c) and d.get('contract_hash')==digest(contract)
                           and d.get('evidence_hash')==digest(evidence_identity(project,c))),None)
        if not verification:reasons.append('no_current_evidence_verification')
        elif verification[1]['result']['verdict']!='pass':reasons.append('evidence_verification_not_pass')
        else:
            checks=verification[1]['result']['message_checks']
            expected=[m['message_id'] for m in c['messages']]
            if sorted(m['message_id'] for m in checks)!=sorted(expected):reasons.append('incomplete_message_verification')
            if any(m.get('requires_nontext_chat_context') is not False or
                   any(m.get(k) is not True for k in ('text_faithful','role_correct','context_sufficient')) or
                   not m.get('evidence') for m in checks):
                reasons.append('missing_explicit_text_context_verification')
            if verification[1]['result'].get('issues') or verification[1]['result'].get('unresolved_questions'):
                reasons.append('unresolved_verification_issue')
        if c.get('derived_from'):
            parents=[p for p in all_conversations if p['conversation_id']==c['derived_from']]
            if not parents or c.get('parent_content_hash')!=digest(parents[0]):reasons.append('stale_derived_segment')
        if c['date_bucket']=='unknown':reasons.append('date_unknown')
        sample=c['projected'];reasons+=validate_sharegpt(sample)
        if reasons:
            excluded.append({'id':cid,'reasons':sorted(set(reasons))});continue
        validator.validate(sample)
        if c['date_bucket']=='early':
            tier='early_strict' if c['date_confidence']=='confirmed' and not c.get('truncated_start') and not c.get('truncated_end') else 'early_probable'
        else:tier='later_supplement'
        exact=digest(sample['conversations'])
        if exact in seen_text:
            excluded.append({'id':cid,'reasons':['exact_text_duplicate_in_release']});continue
        seen_text.add(exact)
        group=groups[cid]
        value=int(digest([project.policy['split']['seed'],group])[:16],16)/16**16
        split='train' if value<project.policy['split']['train_fraction'] else 'val' if value<project.policy['split']['train_fraction']+project.policy['split']['validation_fraction'] else 'test'
        selected.append((sample,tier,split,{'id':cid,'conversation_id':cid,'source_ids':c['source_ids'],
            'date':c.get('date'),'date_bucket':c['date_bucket'],'date_confidence':c['date_confidence'],
            'conversation_date':c.get('conversation_date'),'date_semantics':c.get('date_semantics'),
            'discovery_date_bucket':c.get('discovery_date_bucket'),
            'date_evidence':c.get('date_evidence',[]),'tier':tier,'split':split,'split_group':group,
            'source':c.get('source'),'asset_ids':c['asset_ids'],'message_projection_map':c['message_projection_map'],
            'projected_message_ids':[c['messages'][i]['message_id'] for i in c['message_projection_map']],
            'message_evidence':[m['evidence'] for m in c['messages']],
            'transform_log':c['transforms'],'corrections':c.get('applied_corrections',[]),
            'derived_from':c.get('derived_from'),
            'parent_content_hash':c.get('parent_content_hash'),
            'parent_message_ids':c.get('parent_message_ids'),
            'context_annotations':context_annotations(project,c),
            'verification_contract_hash':digest(contract),'conversation_hash':digest(c),
            'verification_ref':verification[0],'verification_method':'same_model_evidence_consistency_check',
            'human_status':'pending_human'}))
    # Prefer a verified full conversation; otherwise keep disjoint fragments only.
    selected_ids={s['id'] for s,_,_,_ in selected};kept=[];parent_messages=defaultdict(set)
    for entry in selected:
        sample,tier,split,meta=entry
        parent=meta.get('derived_from')
        if parent and (parent in selected_ids or parent_messages[parent].intersection(meta['parent_message_ids'] or [])):
            excluded.append({'id':sample['id'],'reasons':['overlapping_parent_or_segment_already_selected']});continue
        if parent:parent_messages[parent].update(meta['parent_message_ids'] or [])
        kept.append(entry)
    selected=kept
    if len(selected)<project.policy['export']['min_release_samples']:
        report={'eligible_samples':len(selected),'archived_conversations':len(all_conversations),
                'exclusion_counts':dict(Counter(reason for x in excluded for reason in x['reasons'])),
                'status':'no_qualified_release_yet','at':now()}
        atomic(project.root/'reports/export-readiness.json',report)
        return report
    # Freeze only after every candidate was evaluated; IDs, sidecars, and groups stay together.
    for tier in project.policy['export']['tiers']:
        for split in ('train','val','test'):
            atomic(out/'sharegpt'/tier/f'{split}.json',[s for s,t,p,_ in selected if t==tier and p==split])
    review_sections=[]
    for sample,tier,split,meta in selected:
        original_ref=meta['verification_ref']
        frozen=out/'evidence/verifications'/f"{digest(sample['id'])}.json"
        verification=json.loads((project.root/original_ref).read_text());atomic(frozen,verification)
        meta['verification_original_ref']=original_ref
        meta['verification_ref']=str(frozen.relative_to(project.root))
        messages=''.join('<p><b>'+html.escape(t['from'])+'</b></p><pre>'+html.escape(t['value'])+'</pre>' for t in sample['conversations'])
        images=''.join('<img loading="lazy" src="'+html.escape(str(Path(i['path']).resolve()))+'" alt="'+html.escape(i['image_id'])+'">' for i in verification['images'])
        source=next(c for c in all_conversations if c['conversation_id']==sample['id'])
        if source.get('text_source'):images+='<pre>'+html.escape(source['text_source']['text'])+'</pre>'
        review_sections.append('<section><h2>'+html.escape(sample['id'])+'</h2><p>'+tier+' / '+split+' / model checked; pending human</p><div class="pair"><div>'+messages+'</div><div>'+images+'</div></div></section>')
    atomic(out/'review/index.html','<!doctype html><meta charset="utf-8"><title>New Bing evidence review</title><style>body{font:16px system-ui;margin:24px}.pair{display:grid;grid-template-columns:1fr 1fr;gap:24px}pre{white-space:pre-wrap;overflow-wrap:anywhere}img{max-width:100%}section{border-top:2px solid #777}</style><h1>ShareGPT 与原图/原文对照：待人工审阅</h1>'+''.join(review_sections),raw=True)
    atomic(out/'archive/conversations.all.jsonl',''.join(json.dumps(c,ensure_ascii=False)+'\n' for c in all_conversations),raw=True)
    atomic(out/'provenance.jsonl',''.join(json.dumps(meta,ensure_ascii=False)+'\n' for _,_,_,meta in selected),raw=True)
    atomic(out/'exclusions.jsonl',''.join(json.dumps(e,ensure_ascii=False)+'\n' for e in excluded),raw=True)
    atomic(out/'splits.json',{s['id']:{'group':meta['split_group'],'split':split,'tier':tier} for s,tier,split,meta in selected})
    atomic(out/'verification_contract.json',contract)
    atomic(out/'reproduction/config.json',project.config)
    atomic(out/'reproduction/runtime.json',json.loads((project.run/'runtime.json').read_text()))
    for folder,patterns in (('src',['*.py']),('prompts',['*.md']),('schemas',['*.json']),('config',['*.toml'])):
        for pattern in patterns:
            for path in (project.root/folder).rglob(pattern):
                atomic(out/'reproduction'/path.relative_to(project.root),path.read_bytes(),raw=True)
    for filename in ('pyproject.toml','uv.lock','AGENTS.md'):
        source=project.root/filename
        if source.is_file():atomic(out/'reproduction'/filename,source.read_bytes(),raw=True)
    mapping={f'newbing_{tier}_{split}':{'file_name':f'sharegpt/{tier}/{split}.json','formatting':'sharegpt',
             'columns':{'messages':'conversations'},'tags':{'role_tag':'from','content_tag':'value','user_tag':'human','assistant_tag':'gpt'}}
             for tier in project.policy['export']['tiers'] for split in ('train','val','test')}
    atomic(out/'dataset_info.example.json',mapping)
    rounds=Counter(len(sample['conversations'])//2 for sample,_,_,_ in selected)
    stats={'conversations':len(selected),'complete_qa_rounds':sum(k*v for k,v in rounds.items()),
           'round_count_distribution':dict(sorted(rounds.items())),
           'distinct_parent_record_ids':len({meta.get('derived_from') or sample['id'] for sample,_,_,meta in selected}),
           'independent_original_conversations':None,
           'conversation_count_note':'Records and conservative split components are reported separately; neither alone proves the number of independent original conversations.',
           'derived_fragments':sum(bool(meta.get('derived_from')) for _,_,_,meta in selected),
           'split_groups':len({meta['split_group'] for _,_,_,meta in selected}),
           'exclusion_reason_counts':dict(Counter(r for e in excluded for r in e['reasons'])),
           'human_verified':0,'human_pending':len(selected),'quality_method':'same_model_source_evidence_consistency'}
    atomic(out/'statistics.json',stats)
    card='# New Bing 2023 ShareGPT text multi-turn pilot\n\n'
    card+=f'Created {now()}. {len(selected)} eligible conversations. This is a provisional release while collection continues.\n\n'
    card+='Original public screenshots/posts retain their source terms; collection assembly does not grant blanket rights to all underlying material.\n\n'
    card+='All exported messages are text strings, at least two complete rounds. Evidence consistency was checked using the same specified model; human review is pending. No synthetic questions, translation or hidden thought was added. See provenance.jsonl and exclusions.jsonl.\n'
    card+='\nAt least two complete rounds is this project’s admission rule. Original Bing factual errors, refusals, roleplay and unusual wording are preserved when supported by source evidence. The dataset is an archival sample, not proof of one exact backend version or representative behavior.\n'
    card+='\nAll current samples remain pending human review. Dates and early_strict/early_probable/later_supplement are evidence tiers; unknown date material stays in archive. Screenshots/posts are publicly shared selections with survival and popularity bias, and the known baselines overlap. Counts of raw source rows or downloaded files are not conversation counts.\n'
    card+='\nSee statistics.json for complete-round counts, lengths, fragments, groups and exclusion reasons. Empty partitions are valid; correlated groups are not split to force ratios. Content may include historical mistakes, adversarial prompts or offensive language. Do not treat visible self-reported prompts/thoughts as genuine system instructions or internal reasoning. No model training or public dataset upload was performed.\n'
    atomic(out/'DATASET_CARD.md',card,raw=True)
    manifest={'release_id':release_id,'created_at':now(),'status':'provisional_collection_continues','samples':len(selected),
              'by_tier':dict(Counter(t for _,t,_,_ in selected)),'by_split':dict(Counter(s for _,_,s,_ in selected)),
              'contract_version':project.policy['export']['contract_version'],'verification_contract_hash':digest(contract),'config_hash':digest(project.config),
              'files':{str(p.relative_to(out)):digest(p.read_bytes()) for p in out.rglob('*') if p.is_file()}}
    atomic(out/'manifest.json',manifest)
    validate_release(project,release_id)
    return manifest


def validate_release(project,release_id):
    out=project.root/'exports'/release_id
    manifest=json.loads((out/'manifest.json').read_text())
    if manifest.get('status','').startswith('retracted'):
        raise ValueError('release retracted; see RETRACTED.md and manifest')
    errors=[];ids=set();group_splits={};exact_seen={}
    sidecars={d['id']:d for d in [json.loads(line) for line in (out/'provenance.jsonl').read_text().splitlines()]}
    archived={c['conversation_id']:c for c in [json.loads(line) for line in (out/'archive/conversations.all.jsonl').read_text().splitlines()]}
    frozen_contract=None
    if (out/'verification_contract.json').is_file():
        frozen_contract=json.loads((out/'verification_contract.json').read_text())
        if digest(frozen_contract)!=manifest.get('verification_contract_hash'):errors.append('verification_contract_hash')
    schema=json.loads((project.root/project.policy['export']['sample_schema']).read_text())
    validator=Draft202012Validator(schema)
    for filename,sha in manifest['files'].items():
        file=out/filename
        if not file.is_file() or digest(file.read_bytes())!=sha:errors.append('manifest_hash:'+filename)
    for path in sorted((out/'sharegpt').glob('*/*.json')):
        data=json.loads(path.read_text())
        if not isinstance(data,list):errors.append('not_array:'+str(path));continue
        for sample in data:
            errors += [sample.get('id','?')+':'+e for e in validate_sharegpt(sample)]
            errors += [sample.get('id','?')+':schema:'+e.message for e in validator.iter_errors(sample)]
            cid=sample['id']
            if cid in ids:errors.append('duplicate_id:'+cid)
            ids.add(cid)
            if cid not in sidecars:errors.append('missing_sidecar:'+cid);continue
            meta=sidecars[cid]
            if meta['split']!=path.stem or meta['tier']!=path.parent.name:errors.append('partition_sidecar_mismatch:'+cid)
            previous=group_splits.setdefault(meta['split_group'],path.stem)
            if previous!=path.stem:errors.append('split_group_leak:'+cid)
            exact=digest(sample['conversations'])
            if exact in exact_seen:errors.append('exact_duplicate:'+cid)
            exact_seen[exact]=cid
            for aid in meta['asset_ids']:
                assets=project.rows('SELECT path FROM assets WHERE id=?',(aid,))
                if not assets or not (project.root/assets[0]['path']).is_file():errors.append('missing_asset:'+cid)
                elif digest((project.root/assets[0]['path']).read_bytes())!=aid:errors.append('asset_hash_mismatch:'+cid)
            for sid in meta['source_ids']:
                records=project.rows('SELECT raw_path,raw_hash FROM sources WHERE id=?',(sid,))
                if not records or not (project.root/records[0]['raw_path']).is_file():errors.append('missing_raw_source:'+cid)
                elif digest((project.root/records[0]['raw_path']).read_bytes())!=records[0]['raw_hash']:errors.append('raw_source_hash_mismatch:'+cid)
            if len(meta['message_projection_map'])!=len(sample['conversations']):errors.append('message_map_length:'+cid)
            verify_path=project.root/meta['verification_ref']
            if not verify_path.is_file():errors.append('missing_verification:'+cid)
            else:
                verification=json.loads(verify_path.read_text());result=verification['result']
                if result['verdict']!='pass':errors.append('nonpassing_verification:'+cid)
                if frozen_contract is not None:
                    c=archived.get(cid)
                    if not c or verification['conversation_hash']!=digest(c) or c['projected']!=sample:
                        errors.append('verification_content_mismatch:'+cid)
                    if verification.get('contract_hash')!=digest(frozen_contract):errors.append('verification_version_mismatch:'+cid)
                    if c and sorted(m['message_id'] for m in result['message_checks'])!=sorted(m['message_id'] for m in c['messages']):
                        errors.append('verification_coverage:'+cid)
                    if result.get('issues') or result.get('unresolved_questions') or any(
                        not m.get('evidence') or m.get('requires_nontext_chat_context') is not False or
                        any(m.get(k) is not True for k in ('text_faithful','role_correct','context_sufficient'))
                        for m in result['message_checks']):errors.append('verification_fidelity:'+cid)
    if len(ids)!=manifest['samples']:errors.append('manifest_count')
    if ids!=set(sidecars):errors.append('sidecar_id_set')
    report={'release_id':release_id,'samples':len(ids),'errors':errors,'passed':not errors,'checked_at':now()}
    atomic(project.root/'reports'/f'{release_id}-validation.json',report)
    if errors:raise ValueError('release validation failed: '+str(errors[:5]))
    return report
