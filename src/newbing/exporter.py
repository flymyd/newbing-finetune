from __future__ import annotations

from collections import Counter,defaultdict
import json
from pathlib import Path
import re
import html

from jsonschema import Draft202012Validator

from .cleaning import validate_sharegpt,extract_sft_segments
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


def export_release(project,release_id,mode='strict'):
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._-]{0,79}',release_id):raise ValueError('invalid release id')
    out=project.root/'exports'/release_id
    if (out/'manifest.json').exists():raise ValueError('release already frozen; choose a new release id')
    if mode=='sft':
        return export_sft_release(project,release_id,out)
    evaluation=evaluate_candidates(project)
    return write_release(project,release_id,out,evaluation)


def evaluate_candidates(project):
    """Read-only admission decision for every archived conversation (no files written)."""
    from .verification import verification_contract,evidence_identity
    from .assets import ordered_source_assets,missing_media_ordinals
    contract=verification_contract(project)
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
    agent_path=project.root/'data/review/agent-visual-release-checks.json'
    agent_checks={(r['conversation_id'],r['conversation_hash']):{k:r[k] for k in ('reviewer_type','reviewed_at','decision','boundaries','note') if k in r}
                  for r in (json.loads(agent_path.read_text()) if agent_path.is_file() else [])
                  if r.get('decision')=='text_matches_visible_source' and r.get('conversation_hash')}
    agent_rejections={(r['conversation_id'],r['conversation_hash']) for r in (json.loads(agent_path.read_text()) if agent_path.is_file() else [])
                      if r.get('conversation_hash') and str(r.get('decision','')).startswith(('reject','unresolved'))}
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
        if (cid,digest(c)) in agent_rejections:reasons.append('agent_visual_check_rejected_or_unresolved')
        sample=c['projected'];reasons+=validate_sharegpt(sample)
        if reasons:
            excluded.append({'id':cid,'reasons':sorted(set(reasons))});continue
        validator.validate(sample)
        strict_evidence=strict_date_evidence(project,c)
        agent=agent_checks.get((cid,digest(c)))
        if strict_evidence:
            # Completeness of a screenshot conversation needs an evidence-level boundary review.
            if not agent or agent.get('boundaries')!='no_visible_truncation':strict_evidence=None
            else:strict_evidence['checks'].append('agent_visual_boundary_review_no_visible_truncation')
        if c['date_bucket']=='early':
            tier='early_strict' if strict_evidence else 'early_probable'
        else:tier='later_supplement'
        exact=digest(sample['conversations'])
        if exact in seen_text:
            excluded.append({'id':cid,'reasons':['exact_text_duplicate_in_release']});continue
        seen_text.add(exact)
        group=groups[cid]
        selected.append((sample,tier,None,{'id':cid,'conversation_id':cid,'source_ids':c['source_ids'],
            'date':c.get('date'),'date_bucket':c['date_bucket'],
            'date_confidence':'confirmed' if strict_evidence else c['date_confidence'],
            'date_confirmation':strict_evidence,
            'conversation_date':c.get('conversation_date'),'date_semantics':c.get('date_semantics'),
            'discovery_date_bucket':c.get('discovery_date_bucket'),
            'date_evidence':c.get('date_evidence',[]),'tier':tier,'split':None,'split_group':group,
            'source':c.get('source'),'source_rights':source_rights(project,c['source_ids']),
            'record_origin':'project_transcription' if c.get('transcription_result') else 'upstream_baseline_text',
            'asset_ids':c['asset_ids'],'message_projection_map':c['message_projection_map'],
            'projected_message_ids':[c['messages'][i]['message_id'] for i in c['message_projection_map']],
            'message_evidence':[m['evidence'] for m in c['messages']],
            'transform_log':c['transforms'],'corrections':c.get('applied_corrections',[]),
            'derived_from':c.get('derived_from'),
            'parent_content_hash':c.get('parent_content_hash'),
            'parent_message_ids':c.get('parent_message_ids'),
            'context_annotations':context_annotations(project,c),
            'verification_contract_hash':digest(contract),'conversation_hash':digest(c),
            'verification_ref':verification[0],'verification_method':'same_model_evidence_consistency_check',
            'agent_visual_check':agent_checks.get((cid,digest(c))),
            'transcription_quality':'agent_visual_checked' if agent_checks.get((cid,digest(c))) else 'model_checked',
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
    kept=resolve_content_duplicates(project,kept,excluded)
    return {'contract':contract,'all_conversations':all_conversations,'selected':kept,'excluded':excluded}


STRICT_RULE='contemporaneous_publication_v1'
PUBLICATION_EVIDENCE=('source_publication_time_upper_bound','original_post_timestamp_upper_bound','tweet_snowflake_post_timestamp_upper_bound')


def strict_date_evidence(project,c):
    """Return explicit evidence when an early record meets the early_strict date rule, else None.

    Rule (documented in DATASET_CARD/FINAL_REPORT): complete, non-derived record whose source was
    itself published inside the core window (New Bing public preview began 2023-02-07, so a New Bing
    screenshot published by 2023-02-16 was captured inside the window); not a Reddit crosspost; none of
    its screenshot hashes appear in an archived source published earlier; product not contradicted by
    classification. Upstream capture-date labels alone stay probable.
    """
    if c.get('date_bucket')!='early' or c.get('derived_from') or c.get('truncated_start') or c.get('truncated_end'):return None
    if c.get('segment_context_gap') or not c.get('asset_ids'):return None  # typed text claims stay probable
    published=[e for e in c.get('date_evidence',[]) if e.get('type') in PUBLICATION_EVIDENCE and e.get('value')]
    if not published:return None
    when=min(str(e['value']) for e in published)
    if not ('2023-02-07'<=when[:10]<='2023-02-16'):return None
    if c.get('date_semantics')=='upstream_capture_date_claim' and not ('2023-02-07'<=str(c.get('date') or '')[:10]<=when[:10]):return None
    post_ids=set(c.get('source_ids',[]))
    for e in published:
        if e.get('source_id'):post_ids.add(e['source_id'])
    for sid in post_ids:
        rows=project.rows('SELECT payload FROM sources WHERE id=?',(sid,))
        if not rows:continue
        payload=json.loads(rows[0]['payload']);post=payload.get('post') if isinstance(payload.get('post'),dict) else {}
        if post.get('crosspost_parent') or post.get('crosspost_parent_list'):return None
    for aid in c.get('asset_ids',[]):
        earlier=project.rows("""SELECT s.id FROM source_assets sa JOIN sources s ON s.id=sa.source_id
            WHERE sa.asset_id=? AND json_extract(s.payload,'$.posted_at') IS NOT NULL AND json_extract(s.payload,'$.posted_at')<? LIMIT 1""",(aid,when))
        if earlier:return None
    if c.get('classification_ref'):
        path=project.root/c['classification_ref']
        result=json.loads(path.read_text())['result'] if path.is_file() else {}
        if result.get('product')!='bing' or result.get('authenticity_status')=='suspicious':return None
    elif not c['conversation_id'].startswith('baseline:'):return None
    return {'rule':STRICT_RULE,'published_at':when,'publication_evidence':published,
            'lower_bound':'2023-02-07 New Bing public preview launch',
            'checks':['complete_non_derived_record','not_crosspost','no_earlier_archived_asset_reuse',
                      'classification_product_bing' if c.get('classification_ref') else 'curated_bing_baseline_source',
                      'current_evidence_verification_pass'],
            'limitation':'Publication timing bounds capture time; it does not identify the backend version. Model checks are not human review.'}


def source_rights(project,source_ids):
    result=[]
    for sid in source_ids:
        rows=project.rows('SELECT payload FROM sources WHERE id=?',(sid,))
        payload=json.loads(rows[0]['payload']) if rows else {}
        result.append({'source_id':sid,**{k:payload.get(k) for k in ('license_notes','rights_status','removal_status','retrieval_status','fetched_at')}})
    return result


def substantive_replies(sample):
    from .dedupe import normalize
    return [normalize(t['value']) for t in sample['conversations'] if t['from']=='gpt' and len(normalize(t['value']))>=30]


def reply_match(a,b):
    from .dedupe import shingles
    if a==b:return True
    sa,sb=shingles(a),shingles(b)
    if not sa or not sb:return False
    inter=len(sa&sb)
    return inter/len(sa|sb)>=0.6 or inter/min(len(sa),len(sb))>=0.85


def resolve_content_duplicates(project,entries,excluded):
    """Keep one training record per duplicated historical conversation; tie overlapping records to one split.

    A record whose substantive gpt replies all match replies of an already kept (longer) record is the same
    content from another source/reconstruction and is excluded (it stays in the archive). Records sharing only
    some replies are kept, but forced into the same split group to prevent leakage.
    """
    tier_rank={'early_strict':0,'early_probable':1,'later_supplement':2}
    order=sorted(entries,key=lambda e:(-len(e[0]['conversations']),bool(e[3].get('derived_from')),tier_rank[e[1]],
                                       e[3].get('record_origin')!='project_transcription',e[0]['id']))
    kept=[];replies={};links=Groups()
    for entry in order:
        sample,tier,_,meta=entry;mine=substantive_replies(sample);duplicate=None;overlaps=[]
        for other in kept:
            theirs=replies[other[0]['id']]
            matched=[r for r in mine if any(reply_match(r,t) for t in theirs)]
            if mine and len(matched)==len(mine):duplicate=other[0]['id'];break
            if matched:overlaps.append(other)
        if duplicate:
            excluded.append({'id':sample['id'],'reasons':['duplicate_content_of_selected_record'],'duplicate_of':duplicate});continue
        for other in overlaps:links.union(meta['split_group'],other[3]['split_group'])
        meta['content_overlap_with']=[o[0]['id'] for o in overlaps]
        replies[sample['id']]=mine;kept.append(entry)
    final=[]
    for sample,tier,_,meta in sorted(kept,key=lambda e:e[0]['id']):
        group=links.root(meta['split_group']);meta['split_group']=group
        value=int(digest([project.policy['split']['seed'],group])[:16],16)/16**16
        split='train' if value<project.policy['split']['train_fraction'] else 'val' if value<project.policy['split']['train_fraction']+project.policy['split']['validation_fraction'] else 'test'
        meta['split']=split;final.append((sample,tier,split,meta))
    return final


def readiness(project):
    """Summarize current admission without freezing a release."""
    evaluation=evaluate_candidates(project)
    selected,excluded=evaluation['selected'],evaluation['excluded']
    by_id={c['conversation_id']:c for c in evaluation['all_conversations']}
    active=[e for e in excluded if not by_id.get(e['id'],{}).get('superseded')]
    only_verification=[e['id'] for e in active if set(e['reasons'])<={'no_current_evidence_verification'}]
    rounds=Counter(len(s['conversations'])//2 for s,_,_,_ in selected)
    report={'at':now(),'archived_conversation_records':len(by_id),
            'superseded_records':sum(bool(c.get('superseded')) for c in by_id.values()),
            'eligible_samples':len(selected),
            'eligible_by_tier':dict(Counter(t for _,t,_,_ in selected)),
            'eligible_by_split':dict(Counter(p for _,_,p,_ in selected)),
            'eligible_complete_rounds':sum(k*v for k,v in rounds.items()),
            'eligible_round_distribution':dict(sorted(rounds.items())),
            'eligible_distinct_parent_records':len({m.get('derived_from') or s['id'] for s,_,_,m in selected}),
            'eligible_derived_fragments':sum(bool(m.get('derived_from')) for _,_,_,m in selected),
            'eligible_agent_visual_checked':sum(bool(m.get('agent_visual_check')) for _,_,_,m in selected),
            'eligible_not_agent_checked_ids':[s['id'] for s,_,_,m in selected if not m.get('agent_visual_check')],
            'active_excluded':len(active),
            'active_exclusion_reason_counts':dict(Counter(r for e in active for r in {re.sub(r':\d+$','',x) for x in e['reasons']}).most_common()),
            'awaiting_only_current_verification':len(only_verification),
            'awaiting_only_current_verification_by_bucket':dict(Counter(by_id[i]['date_bucket'] for i in only_verification)),
            'eligible_ids':[s['id'] for s,_,_,_ in selected],
            'note':'Read-only evaluation using the same admission code as export; not a frozen release.'}
    atomic(project.root/'reports/export-readiness-current.json',report)
    return {k:v for k,v in report.items() if k!='eligible_ids'}


def write_release(project,release_id,out,evaluation):
    contract=evaluation['contract'];all_conversations=evaluation['all_conversations']
    selected=evaluation['selected'];excluded=evaluation['excluded']
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
    if manifest.get('export_mode')=='sft':
        return validate_sft_release(project,release_id,out,manifest)
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
    # Content-level leakage: substantive gpt replies shared across different split partitions.
    samples=[]
    for path in sorted((out/'sharegpt').glob('*/*.json')):
        for sample in json.loads(path.read_text()):
            if sample.get('id') in sidecars:samples.append((sample,sidecars[sample['id']]['split'],substantive_replies(sample)))
    for i,(a,split_a,ra) in enumerate(samples):
        for b,split_b,rb in samples[i+1:]:
            if split_a!=split_b and any(reply_match(x,y) for x in ra for y in rb):
                errors.append('cross_split_content_overlap:'+a['id']+'|'+b['id'])
    for meta in sidecars.values():
        if meta['tier']=='early_strict' and (meta.get('date_confidence')!='confirmed' or not meta.get('date_confirmation') or meta.get('derived_from')):
            errors.append('strict_without_confirmation:'+meta['id'])
    report={'release_id':release_id,'samples':len(ids),'errors':errors,'passed':not errors,'checked_at':now()}
    atomic(project.root/'reports'/f'{release_id}-validation.json',report)
    if errors:raise ValueError('release validation failed: '+str(errors[:5]))
    return report


SFT_MULTITURN_TIERS = ('early_strict', 'early_probable', 'later_supplement', 'undated_sydney', 'synthetic_sydney')
FATAL_SFT_EXCLUSIONS = frozenset({
    'not_conversation_kind',
    'bing_product_not_confirmed_by_classification',
    'classification_not_actual_conversation',
    'suspicious_source_authenticity',
    'missing_product_classification',
    'visual_context_dependency_review',
})


def validate_sharegpt_sft(sample, min_rounds=1):
    errors = []
    if set(sample) != {'id', 'conversations'}:
        errors.append('top_level_fields')
    if not isinstance(sample.get('id'), str) or not sample['id'].strip():
        errors.append('invalid_id')
    turns = sample.get('conversations')
    if not isinstance(turns, list):
        return errors + ['conversations_not_array']
    if len(turns) < 2 * min_rounds:
        errors.append('fewer_than_min_rounds')
    if len(turns) % 2:
        errors.append('odd_turn_count')
    for index, turn in enumerate(turns):
        if not isinstance(turn, dict) or set(turn) != {'from', 'value'}:
            errors.append(f'turn_fields:{index}')
            continue
        if turn['from'] != ('human' if index % 2 == 0 else 'gpt'):
            errors.append(f'role_sequence:{index}')
        if not isinstance(turn['value'], str) or not turn['value'].strip():
            errors.append(f'non_text_or_empty:{index}')
    return errors


def _precomputed_replies(sample):
    from .dedupe import normalize, shingles
    result = []
    for t in sample['conversations']:
        if t['from'] == 'gpt':
            norm = normalize(t['value'])
            if len(norm) >= 30:
                result.append((norm, shingles(norm)))
    return result


def _fast_reply_match(a, b):
    na, sa = a
    nb, sb = b
    if na == nb:
        return True
    if not sa or not sb:
        return False
    inter = len(sa & sb)
    return inter / len(sa | sb) >= 0.6 or inter / min(len(sa), len(sb)) >= 0.85


def evaluate_sft_candidates(project):
    """Evaluate practical SFT multi-turn (>=2 rounds) and single-round (1 round) candidates."""
    strict_eval = evaluate_candidates(project)
    contract = strict_eval['contract']
    all_conversations = strict_eval['all_conversations']
    by_id = {c['conversation_id']: c for c in all_conversations}
    media_audit_path = project.root / 'reports/media-decode-audit.json'
    media_audit = json.loads(media_audit_path.read_text()) if media_audit_path.is_file() else {}
    invalid_assets = {r['asset_id'] for r in media_audit.get('issues', [])}
    unreviewed_animations = {r['asset_id'] for r in media_audit.get('animated_requires_frame_coverage', [])}
    relations = project.rows(
        "SELECT lhs,rhs,kind FROM relations WHERE kind IN ('same_original_post','same_conversation','repost','near_duplicate_confirmed','exact_reply_shared','near_duplicate_candidate')"
    )
    groups = grouping(all_conversations, relations)

    # Identify known bad messages or role-inverted / non-Bing conversations from agent review & verifications
    agent_path = project.root / 'data/review/agent-visual-release-checks.json'
    agent_rows = json.loads(agent_path.read_text()) if agent_path.is_file() else []
    hard_reject_decisions = {
        'reject_role_reversal',
        'reject_role_error',
        'reject_not_bing_chat',
        'reject_merged_sessions',
        'reject_context_gap_or_truncation',
        'reject_incomplete_context',
    }
    hard_reject_ids = {r['conversation_id'] for r in agent_rows if r.get('decision') in hard_reject_decisions}
    hard_reject_parents = set()
    for r in agent_rows:
        if r.get('decision') in ('reject_role_reversal', 'reject_role_error', 'reject_not_bing_chat', 'reject_merged_sessions'):
            cid = r['conversation_id']
            hard_reject_parents.add(cid)
            if cid in by_id and by_id[cid].get('derived_from'):
                hard_reject_parents.add(by_id[cid]['derived_from'])

    bad_message_ids = set()
    seen_verify_conv = set()
    for t in project.rows("SELECT output_ref FROM tasks WHERE kind='verify' AND status='succeeded' ORDER BY updated DESC"):
        d = json.loads((project.root / t['output_ref']).read_text())
        cid = d['conversation_id']
        if cid in seen_verify_conv:
            continue
        seen_verify_conv.add(cid)
        for m in d.get('result', {}).get('message_checks', []):
            if m.get('role_correct') is False or m.get('requires_nontext_chat_context') is True:
                bad_message_ids.add(m['message_id'])
                if cid in by_id and by_id[cid].get('derived_from'):
                    parent_cid = by_id[cid]['derived_from']
                    suffix = m['message_id'].split('#', 1)[-1] if '#' in m['message_id'] else ''
                    if suffix:
                        bad_message_ids.add(f'{parent_cid}#{suffix}')

    candidates = []
    excluded = []
    covered_parent_messages = defaultdict(set)

    # 1. Keep all strict-verified multi-turn samples with highest priority
    for sample, tier, split, meta in strict_eval['selected']:
        meta_copy = dict(meta)
        meta_copy['quality_tier'] = 'strict_verified'
        meta_copy['sft_partition'] = 'multiturn'
        parent_id = meta_copy.get('derived_from') or sample['id']
        covered_parent_messages[parent_id].update(meta_copy.get('parent_message_ids') or meta_copy.get('projected_message_ids') or [])
        candidates.append((sample, tier, None, meta_copy))

    # 2. Extract SFT cleaned multi-turn and single-round segments from non-superseded parent conversations
    for p in all_conversations:
        cid = p['conversation_id']
        if p.get('superseded') or cid.startswith('segment:'):
            continue
        reasons = []
        if cid in hard_reject_ids or cid in hard_reject_parents:
            reasons.append('hard_rejected_by_visual_audit')
        review_ids = [cid, *p.get('source_ids', [])]
        if any(project.rows("SELECT 1 FROM relations WHERE lhs=? AND kind='authenticity_review_required' LIMIT 1", (target,)) for target in review_ids):
            reasons.append('authenticity_review_required')
        if invalid_assets.intersection(p.get('asset_ids', [])):
            reasons.append('source_asset_decode_failure')
        if unreviewed_animations.intersection(p.get('asset_ids', [])):
            reasons.append('animated_evidence_requires_complete_frame_coverage')
        for sid in p.get('source_ids', []):
            pending = project.rows(
                "SELECT 1 FROM tasks WHERE kind='download' AND json_extract(payload,'$.source_id')=? AND json_extract(payload,'$.verify_upstream_bytes')=1 AND status!='succeeded' LIMIT 1",
                (sid,),
            )
            if pending:
                reasons.append('upstream_asset_bytes_not_verified')
        fatal = FATAL_SFT_EXCLUSIONS.intersection(p.get('automatic_exclusions', []))
        if fatal:
            reasons.extend(sorted(fatal))
        if reasons:
            excluded.append({'id': cid, 'reasons': sorted(set(reasons))})
            continue

        segments = extract_sft_segments(p, bad_message_ids=bad_message_ids)
        if not segments:
            if cid not in covered_parent_messages:
                excluded.append({'id': cid, 'reasons': ['no_clean_sft_segment_extracted']})
            continue

        for seg_idx, seg in enumerate(segments):
            items = seg['items']
            msg_ids = [it['message_id'] for it in items]
            if covered_parent_messages[cid].intersection(msg_ids):
                continue
            turns = [{'from': it['turn']['from'], 'value': it['turn']['value']} for it in items]
            is_full = (
                items[0]['source_index'] == 0
                and items[-1]['source_index'] == len(p.get('messages', [])) - 1
                and not p.get('truncated_start')
                and not p.get('truncated_end')
                and len(segments) == 1
            )
            sample_id = cid if is_full else f"sft:{digest([cid, msg_ids])[:24]}"
            sample = {'id': sample_id, 'conversations': turns}
            if seg['kind'] == 'multiturn':
                err = validate_sharegpt(sample)
                if err:
                    excluded.append({'id': sample_id, 'reasons': err})
                    continue
                if p['date_bucket'] == 'early':
                    tier = 'early_probable'
                elif p['date_bucket'] in ('transition', 'later_mar_apr', 'later_2023'):
                    tier = 'later_supplement'
                else:
                    tier = 'undated_sydney'
                quality_tier = 'sft_cleaned_multiturn'
                sft_partition = 'multiturn'
            else:
                err = validate_sharegpt_sft(sample, min_rounds=1)
                if err or not _precomputed_replies(sample):
                    excluded.append({'id': sample_id, 'reasons': err or ['single_round_reply_too_short']})
                    continue
                tier = 'single_round_supplement'
                quality_tier = 'single_round_supplement'
                sft_partition = 'single_round'

            sft_ops = [
                {'message_index': it['source_index'], 'operation': op}
                for it in items
                for op in it['ops']
            ]
            if not is_full:
                sft_ops.append({
                    'operation': 'extract_sft_contiguous_segment',
                    'parent': cid,
                    'start_message': items[0]['source_index'],
                    'end_message_inclusive': items[-1]['source_index'],
                })
            covered_parent_messages[cid].update(msg_ids)
            meta = {
                'id': sample_id,
                'conversation_id': cid,
                'source_ids': p['source_ids'],
                'date': p.get('date'),
                'date_bucket': p['date_bucket'],
                'date_confidence': p.get('date_confidence', 'unknown'),
                'date_confirmation': None,
                'conversation_date': p.get('conversation_date'),
                'date_semantics': p.get('date_semantics'),
                'discovery_date_bucket': p.get('discovery_date_bucket'),
                'date_evidence': p.get('date_evidence', []),
                'tier': tier,
                'quality_tier': quality_tier,
                'sft_partition': sft_partition,
                'split': None,
                'split_group': groups.get(cid, cid),
                'source': p.get('source'),
                'source_rights': source_rights(project, p['source_ids']),
                'record_origin': 'project_transcription' if p.get('transcription_result') else 'upstream_baseline_text',
                'asset_ids': p.get('asset_ids', []),
                'message_projection_map': [it['source_index'] for it in items],
                'projected_message_ids': msg_ids,
                'message_evidence': [it['evidence'] for it in items],
                'transform_log': list(p.get('transforms', [])) + sft_ops,
                'corrections': p.get('applied_corrections', []),
                'derived_from': None if is_full else cid,
                'parent_content_hash': digest(p),
                'parent_message_ids': msg_ids,
                'context_annotations': context_annotations(project, p),
                'verification_contract_hash': digest(contract),
                'conversation_hash': digest(p),
                'verification_ref': None,
                'verification_method': 'rule_cleaned_sft_projection_v1',
                'agent_visual_check': None,
                'transcription_quality': 'sft_rule_cleaned',
                'human_status': 'pending_human',
            }
            candidates.append((sample, tier, None, meta))

    # 3. Deduplicate content and link overlapping split_groups across both multi-turn and single-round
    tier_rank = {
        'early_strict': 0,
        'early_probable': 1,
        'later_supplement': 2,
        'undated_sydney': 3,
        'single_round_supplement': 4,
    }
    order = sorted(
        candidates,
        key=lambda e: (
            e[3]['quality_tier'] != 'strict_verified',
            e[3]['sft_partition'] != 'multiturn',
            -len(e[0]['conversations']),
            tier_rank.get(e[1], 9),
            e[3].get('record_origin') != 'project_transcription',
            e[0]['id'],
        ),
    )
    kept = []
    replies_by_id = {}
    seen_exact = set()
    links = Groups()
    for entry in order:
        sample, tier, _, meta = entry
        exact = digest(sample['conversations'])
        if exact in seen_exact and meta['quality_tier'] != 'strict_verified':
            excluded.append({'id': sample['id'], 'reasons': ['exact_text_duplicate_in_release']})
            continue
        mine = _precomputed_replies(sample)
        duplicate = None
        overlaps = []
        for other in kept:
            theirs = replies_by_id[other[0]['id']]
            matched = [r for r in mine if any(_fast_reply_match(r, t) for t in theirs)]
            if mine and len(matched) == len(mine) and meta['quality_tier'] != 'strict_verified':
                duplicate = other[0]['id']
                break
            if matched:
                overlaps.append(other)
        if duplicate:
            excluded.append({'id': sample['id'], 'reasons': ['duplicate_content_of_selected_record'], 'duplicate_of': duplicate})
            continue
        seen_exact.add(exact)
        for other in overlaps:
            links.union(meta['split_group'], other[3]['split_group'])
        meta['content_overlap_with'] = [o[0]['id'] for o in overlaps]
        replies_by_id[sample['id']] = mine
        kept.append(entry)

    final = []
    for sample, tier, _, meta in sorted(kept, key=lambda e: e[0]['id']):
        group = links.root(meta['split_group'])
        meta['split_group'] = group
        value = int(digest([project.policy['split']['seed'], group])[:16], 16) / 16**16
        split = (
            'train'
            if value < project.policy['split']['train_fraction']
            else 'val'
            if value < project.policy['split']['train_fraction'] + project.policy['split']['validation_fraction']
            else 'test'
        )
        meta['split'] = split
        final.append((sample, tier, split, meta))
    return {'contract': contract, 'all_conversations': all_conversations, 'selected': final, 'excluded': excluded}


def export_sft_release(project, release_id, out):
    evaluation = evaluate_sft_candidates(project)
    contract = evaluation['contract']
    all_conversations = evaluation['all_conversations']
    selected = evaluation['selected']
    excluded = evaluation['excluded']
    multiturn = [(s, t, p, m) for s, t, p, m in selected if m['sft_partition'] == 'multiturn']
    single_round = [(s, t, p, m) for s, t, p, m in selected if m['sft_partition'] == 'single_round']
    if len(multiturn) < project.policy['export']['min_release_samples']:
        raise ValueError('no qualified multiturn samples for SFT release')

    for tier in SFT_MULTITURN_TIERS:
        for split in ('train', 'val', 'test'):
            atomic(out / 'sharegpt' / tier / f'{split}.json', [s for s, t, p, _ in multiturn if t == tier and p == split])
    for split in ('train', 'val', 'test'):
        atomic(out / 'sharegpt_multiturn' / f'{split}.json', [s for s, _, p, _ in multiturn if p == split])
        atomic(out / 'sharegpt_single_round' / f'{split}.json', [s for s, _, p, _ in single_round if p == split])
        atomic(out / 'sharegpt_all_sft' / f'{split}.json', [s for s, _, p, _ in selected if p == split])

    review_sections = []
    for sample, tier, split, meta in selected:
        if meta.get('verification_ref'):
            original_ref = meta['verification_ref']
            frozen = out / 'evidence/verifications' / f"{digest(sample['id'])}.json"
            verification = json.loads((project.root / original_ref).read_text())
            atomic(frozen, verification)
            meta['verification_original_ref'] = original_ref
            meta['verification_ref'] = str(frozen.relative_to(project.root))
        messages = ''.join('<p><b>' + html.escape(t['from']) + '</b></p><pre>' + html.escape(t['value']) + '</pre>' for t in sample['conversations'])
        review_sections.append(
            '<section><h2>'
            + html.escape(sample['id'])
            + '</h2><p>'
            + html.escape(f"{tier} / {meta['quality_tier']} / {split} / pending human")
            + '</p><div>'
            + messages
            + '</div></section>'
        )
    atomic(
        out / 'review/index.html',
        '<!doctype html><meta charset="utf-8"><title>New Bing SFT expanded review</title><style>body{font:16px system-ui;margin:24px}pre{white-space:pre-wrap;overflow-wrap:anywhere}section{border-top:2px solid #777}</style><h1>ShareGPT SFT 扩充版对照：待人工审阅</h1>'
        + ''.join(review_sections),
        raw=True,
    )
    atomic(out / 'archive/conversations.all.jsonl', ''.join(json.dumps(c, ensure_ascii=False) + '\n' for c in all_conversations), raw=True)
    atomic(out / 'provenance.jsonl', ''.join(json.dumps(meta, ensure_ascii=False) + '\n' for _, _, _, meta in selected), raw=True)
    atomic(out / 'exclusions.jsonl', ''.join(json.dumps(e, ensure_ascii=False) + '\n' for e in excluded), raw=True)
    atomic(
        out / 'splits.json',
        {s['id']: {'group': meta['split_group'], 'split': split, 'tier': tier, 'quality_tier': meta['quality_tier'], 'sft_partition': meta['sft_partition']} for s, tier, split, meta in selected},
    )
    atomic(out / 'verification_contract.json', contract)
    atomic(out / 'reproduction/config.json', project.config)
    atomic(out / 'reproduction/runtime.json', json.loads((project.run / 'runtime.json').read_text()))
    for folder, patterns in (('src', ['*.py']), ('prompts', ['*.md']), ('schemas', ['*.json']), ('config', ['*.toml'])):
        for pattern in patterns:
            for path in (project.root / folder).rglob(pattern):
                atomic(out / 'reproduction' / path.relative_to(project.root), path.read_bytes(), raw=True)
    for filename in ('pyproject.toml', 'uv.lock', 'AGENTS.md'):
        source = project.root / filename
        if source.is_file():
            atomic(out / 'reproduction' / filename, source.read_bytes(), raw=True)

    tags = {'role_tag': 'from', 'content_tag': 'value', 'user_tag': 'human', 'assistant_tag': 'gpt'}
    mapping = {
        f'newbing_{tier}_{split}': {
            'file_name': f'sharegpt/{tier}/{split}.json',
            'formatting': 'sharegpt',
            'columns': {'messages': 'conversations'},
            'tags': tags,
        }
        for tier in SFT_MULTITURN_TIERS
        for split in ('train', 'val', 'test')
    }
    for split in ('train', 'val', 'test'):
        mapping[f'newbing_sft_multiturn_{split}'] = {
            'file_name': f'sharegpt_multiturn/{split}.json',
            'formatting': 'sharegpt',
            'columns': {'messages': 'conversations'},
            'tags': tags,
        }
        mapping[f'newbing_sft_single_round_{split}'] = {
            'file_name': f'sharegpt_single_round/{split}.json',
            'formatting': 'sharegpt',
            'columns': {'messages': 'conversations'},
            'tags': tags,
        }
        mapping[f'newbing_sft_all_{split}'] = {
            'file_name': f'sharegpt_all_sft/{split}.json',
            'formatting': 'sharegpt',
            'columns': {'messages': 'conversations'},
            'tags': tags,
        }
    atomic(out / 'dataset_info.example.json', mapping)

    multi_rounds = Counter(len(s['conversations']) // 2 for s, _, _, _ in multiturn)
    all_rounds = Counter(len(s['conversations']) // 2 for s, _, _, _ in selected)
    stats = {
        'export_mode': 'sft',
        'conversations_total': len(selected),
        'multiturn_conversations': len(multiturn),
        'single_round_conversations': len(single_round),
        'complete_qa_rounds_total': sum(k * v for k, v in all_rounds.items()),
        'multiturn_qa_rounds': sum(k * v for k, v in multi_rounds.items()),
        'round_count_distribution': dict(sorted(all_rounds.items())),
        'by_tier': dict(Counter(t for _, t, _, _ in selected)),
        'by_quality_tier': dict(Counter(m['quality_tier'] for _, _, _, m in selected)),
        'by_split': dict(Counter(p for _, _, p, _ in selected)),
        'multiturn_by_split': dict(Counter(p for _, _, p, _ in multiturn)),
        'single_round_by_split': dict(Counter(p for _, _, p, _ in single_round)),
        'distinct_parent_record_ids': len({m['conversation_id'] for _, _, _, m in selected}),
        'split_groups': len({m['split_group'] for _, _, _, m in selected}),
        'exclusion_reason_counts': dict(Counter(r for e in excluded for r in e['reasons'])),
        'human_verified': 0,
        'human_pending': len(selected),
    }
    atomic(out / 'statistics.json', stats)

    card = (
        f'# New Bing / Sydney 2023 ShareGPT SFT Expanded Dataset ({release_id})\n\n'
        f'Created {now()}. Total SFT conversations: {len(selected)} ({len(multiturn)} multi-turn conversations with {stats["multiturn_qa_rounds"]} complete QA rounds + {len(single_round)} single-round complete Q&A conversations; {stats["complete_qa_rounds_total"]} total QA rounds).\n\n'
        '- `sharegpt_multiturn/{train,val,test}.json`: All multi-turn conversations (>= 2 complete rounds, >= 4 messages), combining `early_strict`, `early_probable`, `later_supplement`, and `undated_sydney`.\n'
        '- `sharegpt_single_round/{train,val,test}.json`: Single-round complete Q&A supplement (1 human + 1 gpt).\n'
        '- `sharegpt_all_sft/{train,val,test}.json`: Combined multi-turn + single-round ShareGPT SFT dataset.\n'
        '- `sharegpt/<tier>/{train,val,test}.json`: Multi-turn conversations partitioned by evidence tier.\n\n'
        'All splits enforce connected-component (`split_group`) and substantive-reply shingle anti-leakage across both multi-turn and single-round partitions. Every sample is documented in `provenance.jsonl` with its `quality_tier` (`strict_verified`, `sft_cleaned_multiturn`, or `single_round_supplement`) and remains `pending_human`.\n'
    )
    atomic(out / 'DATASET_CARD.md', card, raw=True)

    manifest = {
        'release_id': release_id,
        'export_mode': 'sft',
        'created_at': now(),
        'status': 'provisional_sft_expanded_release',
        'samples': len(selected),
        'multiturn_samples': len(multiturn),
        'single_round_samples': len(single_round),
        'by_tier': dict(Counter(t for _, t, _, _ in selected)),
        'by_quality_tier': dict(Counter(m['quality_tier'] for _, _, _, m in selected)),
        'by_split': dict(Counter(s for _, _, s, _ in selected)),
        'contract_version': project.policy['export']['contract_version'],
        'verification_contract_hash': digest(contract),
        'config_hash': digest(project.config),
        'files': {str(p.relative_to(out)): digest(p.read_bytes()) for p in out.rglob('*') if p.is_file()},
    }
    atomic(out / 'manifest.json', manifest)
    validate_release(project, release_id)
    return manifest


def validate_sft_release(project, release_id, out, manifest):
    errors = []
    ids = set()
    group_splits = {}
    exact_seen = {}
    sidecars = {d['id']: d for d in [json.loads(line) for line in (out / 'provenance.jsonl').read_text().splitlines()]}
    schema = json.loads((project.root / project.policy['export']['sample_schema']).read_text())
    validator = Draft202012Validator(schema)

    for filename, sha in manifest['files'].items():
        file = out / filename
        if not file.is_file() or digest(file.read_bytes()) != sha:
            errors.append('manifest_hash:' + filename)

    multiturn_by_split = defaultdict(list)
    for path in sorted((out / 'sharegpt').glob('*/*.json')):
        data = json.loads(path.read_text())
        if not isinstance(data, list):
            errors.append('not_array:' + str(path))
            continue
        for sample in data:
            errors += [sample.get('id', '?') + ':' + e for e in validate_sharegpt(sample)]
            errors += [sample.get('id', '?') + ':schema:' + e.message for e in validator.iter_errors(sample)]
            cid = sample['id']
            if cid in ids:
                errors.append('duplicate_id:' + cid)
            ids.add(cid)
            multiturn_by_split[path.stem].append(cid)
            if cid not in sidecars:
                errors.append('missing_sidecar:' + cid)
                continue
            meta = sidecars[cid]
            if meta['split'] != path.stem or meta['tier'] != path.parent.name or meta.get('sft_partition') != 'multiturn':
                errors.append('partition_sidecar_mismatch:' + cid)

    single_by_split = defaultdict(list)
    for path in sorted((out / 'sharegpt_single_round').glob('*.json')):
        data = json.loads(path.read_text())
        if not isinstance(data, list):
            errors.append('not_array:' + str(path))
            continue
        for sample in data:
            errors += [sample.get('id', '?') + ':' + e for e in validate_sharegpt_sft(sample, min_rounds=1)]
            if len(sample.get('conversations', [])) != 2:
                errors.append('not_single_round:' + sample.get('id', '?'))
            cid = sample['id']
            if cid in ids:
                errors.append('duplicate_id:' + cid)
            ids.add(cid)
            single_by_split[path.stem].append(cid)
            if cid not in sidecars:
                errors.append('missing_sidecar:' + cid)
                continue
            meta = sidecars[cid]
            if meta['split'] != path.stem or meta.get('sft_partition') != 'single_round':
                errors.append('partition_sidecar_mismatch:' + cid)

    for split in ('train', 'val', 'test'):
        mt_file = out / 'sharegpt_multiturn' / f'{split}.json'
        all_file = out / 'sharegpt_all_sft' / f'{split}.json'
        mt_ids = [s['id'] for s in json.loads(mt_file.read_text())] if mt_file.is_file() else []
        all_ids = [s['id'] for s in json.loads(all_file.read_text())] if all_file.is_file() else []
        if sorted(mt_ids) != sorted(multiturn_by_split[split]):
            errors.append(f'multiturn_union_mismatch:{split}')
        if sorted(all_ids) != sorted(multiturn_by_split[split] + single_by_split[split]):
            errors.append(f'all_sft_union_mismatch:{split}')

    all_samples = []
    for split in ('train', 'val', 'test'):
        for sample in json.loads((out / 'sharegpt_all_sft' / f'{split}.json').read_text()):
            cid = sample['id']
            if cid not in sidecars:
                continue
            meta = sidecars[cid]
            previous = group_splits.setdefault(meta['split_group'], split)
            if previous != split:
                errors.append('split_group_leak:' + cid)
            exact = digest(sample['conversations'])
            if exact in exact_seen:
                errors.append('exact_duplicate:' + cid)
            exact_seen[exact] = cid
            if len(meta['message_projection_map']) != len(sample['conversations']):
                errors.append('message_map_length:' + cid)
            for aid in meta['asset_ids']:
                assets = project.rows('SELECT path FROM assets WHERE id=?', (aid,))
                if not assets or not (project.root / assets[0]['path']).is_file():
                    errors.append('missing_asset:' + cid)
            for sid in meta['source_ids']:
                records = project.rows('SELECT raw_path,raw_hash FROM sources WHERE id=?', (sid,))
                if not records or not (project.root / records[0]['raw_path']).is_file():
                    errors.append('missing_raw_source:' + cid)
            all_samples.append((sample, split, _precomputed_replies(sample)))

    if len(ids) != manifest['samples']:
        errors.append('manifest_count')
    if ids != set(sidecars):
        errors.append('sidecar_id_set')

    for i, (a, split_a, ra) in enumerate(all_samples):
        for b, split_b, rb in all_samples[i + 1 :]:
            if split_a != split_b and any(_fast_reply_match(x, y) for x in ra for y in rb):
                errors.append('cross_split_content_overlap:' + a['id'] + '|' + b['id'])

    report = {
        'release_id': release_id,
        'export_mode': 'sft',
        'samples': len(ids),
        'multiturn_samples': sum(len(v) for v in multiturn_by_split.values()),
        'single_round_samples': sum(len(v) for v in single_by_split.values()),
        'errors': errors,
        'passed': not errors,
        'checked_at': now(),
    }
    atomic(project.root / 'reports' / f'{release_id}-validation.json', report)
    if errors:
        raise ValueError('release validation failed: ' + str(errors[:5]))
    return report

