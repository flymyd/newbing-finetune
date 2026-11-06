"""Release admission rules added after handoff: metadata stability, strict dating, content duplicates."""
import io
import json

from PIL import Image

from newbing.cleaning import derive_continuous_segments,embedded_source,extract_sft_segments,project_sft_messages
from newbing.core import atomic,digest,now
from newbing.exporter import evaluate_candidates,evaluate_sft_candidates,export_release,resolve_content_duplicates,strict_date_evidence,validate_release
from newbing.verification import verification_contract,evidence_identity


def test_descriptive_source_backfill_does_not_change_conversation_copy():
    original={'url':'u','posted_at':'2023-02-10T00:00:00+00:00','text':'body'}
    backfilled={**original,'license_notes':'n','rights_status':'not_assessed','removal_status':'none_known',
                'retrieval_status':'raw_archived','fetched_at':'2026-11-05','archived_at':None,'conversation_date':None}
    assert embedded_source(backfilled)==original
    # A real (non-null) conversation date is evidence and must be kept.
    assert embedded_source({**original,'conversation_date':'2023-02-09'})['conversation_date']=='2023-02-09'


def add_conversation(project,cid,texts,*,posted='2023-02-10T12:00:00+00:00',source_payload=None,truncated=False,derived=None,asset_bytes=None):
    raw=project.root/'data/raw'/f'{digest(cid)}.json';atomic(raw,{'id':cid})
    sid='reddit:'+cid
    project.add_source(sid,'reddit_post',source_payload or {'posted_at':posted,'post':{}},raw)
    image=Image.new('RGB',(12,12),(len(cid)*7%255,10,10));buffer=io.BytesIO();image.save(buffer,format='PNG')
    data=asset_bytes or buffer.getvalue();aid=digest(data)
    path=project.root/'data/assets'/f'{aid}.png';atomic(path,data,raw=True)
    project.execute('INSERT OR IGNORE INTO assets VALUES (?,?,?,?,?,?)',(aid,str(path.relative_to(project.root)),'image/png',len(data),12,12))
    project.execute('INSERT OR IGNORE INTO source_assets VALUES (?,?,?,?)',(sid,aid,0,'u'+cid))
    messages=[{'role':'user' if i%2==0 else 'assistant','content':[{'type':'text','text':t}],'message_id':f'{cid}#m{i}',
               'evidence':[{'source_id':sid,'asset_id':aid,'region':[0,0,1,1]}]} for i,t in enumerate(texts)]
    turns=[{'from':'human' if i%2==0 else 'gpt','value':t} for i,t in enumerate(texts)]
    c={'conversation_id':cid,'source_ids':[sid],'messages':messages,'projected':{'id':cid,'conversations':turns},
       'automatic_exclusions':[],'date_bucket':'early','date_confidence':'probable','date':posted[:10],
       'date_semantics':'publication_upper_bound_only',
       'date_evidence':[{'type':'source_publication_time_upper_bound','value':posted,'source_id':sid}],
       'truncated_start':truncated,'truncated_end':False,'transforms':[],'asset_ids':[aid],'source':{},
       'message_projection_map':list(range(len(texts)))}
    if derived:c['derived_from']=derived
    classification=project.root/'data/processed'/f'classify-{digest(cid)}.json'
    atomic(classification,{'result':{'product':'bing','authenticity_status':'screenshot_backed_unverified'}})
    c['classification_ref']=str(classification.relative_to(project.root))
    project.execute('INSERT INTO conversations VALUES (?,?,?,?)',(cid,sid,json.dumps(c),now()))
    v={'conversation_id':cid,'conversation_hash':digest(c),'contract_hash':digest(verification_contract(project)),
       'evidence_hash':digest(evidence_identity(project,c)),
       'images':[{'image_id':aid,'path':str(path),'asset_id':aid,'box':[0,0,12,12]}],
       'result':{'verdict':'pass','issues':[],'unresolved_questions':[],
                 'message_checks':[{'message_id':m['message_id'],'requires_nontext_chat_context':False,'text_faithful':True,
                                    'role_correct':True,'context_sufficient':True,'evidence':m['evidence']} for m in messages]}}
    out=project.root/'data/processed'/f'verify-{digest(cid)}.json';atomic(out,v)
    project.execute('INSERT INTO tasks(id,kind,payload,status,output_ref,updated) VALUES (?,?,?,?,?,?)',
                    ('verify-'+cid,'verify',json.dumps({'conversation_id':cid}),'succeeded',str(out.relative_to(project.root)),now()))
    return c


LONG=['first question about the weather tomorrow please',
      'I am Bing. Tomorrow will be sunny with a high of twenty degrees and light wind from the west.',
      'and what about the weekend, will it rain at all?',
      'The weekend forecast shows scattered showers on Saturday afternoon and a dry, cool Sunday morning.']


def test_strict_rule_requires_contemporaneous_complete_non_crosspost(project):
    ok=add_conversation(project,'a',LONG)
    assert strict_date_evidence(project,ok)['rule']=='contemporaneous_publication_v1'
    late=add_conversation(project,'b',LONG,posted='2023-02-20T00:00:00+00:00')
    late['date_bucket']='early'  # even if mislabeled, publication after the window is not confirmation
    assert strict_date_evidence(project,late) is None
    cut=add_conversation(project,'c',LONG,truncated=True)
    assert strict_date_evidence(project,cut) is None
    cross=add_conversation(project,'d',LONG,source_payload={'posted_at':'2023-02-10T00:00:00+00:00','post':{'crosspost_parent':'t3_x'}})
    assert strict_date_evidence(project,cross) is None
    unclassified=dict(ok);unclassified.pop('classification_ref')
    assert strict_date_evidence(project,unclassified) is None


def test_strict_rule_rejects_earlier_reused_screenshot(project):
    image=Image.new('RGB',(12,12),'blue');buffer=io.BytesIO();image.save(buffer,format='PNG');data=buffer.getvalue()
    add_conversation(project,'early-original',LONG,posted='2023-02-08T00:00:00+00:00',asset_bytes=data)
    repost=add_conversation(project,'later-repost',[t+' x' for t in LONG],posted='2023-02-12T00:00:00+00:00',asset_bytes=data)
    assert strict_date_evidence(project,repost) is None


def test_duplicate_content_keeps_one_record_and_partial_overlap_shares_split(project):
    add_conversation(project,'full',LONG+['one more question?','A final distinct answer that is long enough to count as substantive text.'])
    add_conversation(project,'copy',LONG)
    other=['an unrelated opening question here','A completely different assistant reply about cooking pasta with garlic and olive oil.',
           'second unrelated question','Another different assistant answer about how long to boil the pasta for al dente texture.']
    add_conversation(project,'overlap',[LONG[0],LONG[1]]+other[2:])
    evaluation=evaluate_candidates(project)
    kept={s['id']:(split,meta) for s,_,split,meta in evaluation['selected']}
    assert 'copy' not in kept and 'full' in kept
    assert any(e['id']=='copy' and e.get('duplicate_of')=='full' for e in evaluation['excluded'])
    assert kept['overlap'][1]['split_group']==kept['full'][1]['split_group']
    assert kept['overlap'][0]==kept['full'][0]


def test_release_with_strict_tier_validates(project):
    c=add_conversation(project,'a',LONG)
    # Without an agent boundary review the record stays early_probable.
    assert {t for _,t,_,_ in evaluate_candidates(project)['selected']}=={'early_probable'}
    atomic(project.root/'data/review/agent-visual-release-checks.json',[{'conversation_id':'a','conversation_hash':digest(c),
        'decision':'text_matches_visible_source','boundaries':'no_visible_truncation','reviewer_type':'agent_visual_checked'}])
    manifest=export_release(project,'strict-fixture')
    assert manifest['by_tier']=={'early_strict':1}
    assert validate_release(project,'strict-fixture')['passed']
    meta=json.loads((project.root/'exports/strict-fixture/provenance.jsonl').read_text().splitlines()[0])
    assert meta['date_confidence']=='confirmed' and meta['date_confirmation']['rule']=='contemporaneous_publication_v1'
    assert meta['source_rights'][0]['rights_status']=='not_assessed'


def test_sft_ui_cleaning_and_safety_block_disambiguation():
    raw=[
        {'role':'assistant','content':[{'type':'text','text':'Unseen opening reply'}],'message_id':'m0'},
        {'role':'user','content':[{'type':'text','text':'Yes'}],'message_id':'m1'},
        {'role':'assistant','content':[{'type':'text','text':'Short continuation reply'}],'message_id':'m2'},
        {'role':'user','content':[{'type':'text','text':'Can you explain how quantum entanglement works?'}],'message_id':'m3'},
        {'role':'assistant','content':[{'type':'text','text':'Searching for: quantum entanglement\nGenerating answers for you...\nQuantum entanglement is a physical phenomenon where particles remain connected.\n\nLearn more: 1. wikipedia.org 2. physics.org\n\n1 of 8'}],'message_id':'m4'},
        {'role':'user','content':[{'type':'text','text':'What if I ask something you refuse?'}],'message_id':'m5'},
        {'role':'assistant','content':[{'type':'safety_block','text':"I'm sorry but I prefer not to continue this conversation. I'm still learning so I appreciate your understanding and patience. 🙏\n\nFun fact, did you know honey never spoils?"}],'message_id':'m6'},
        {'role':'assistant','content':[{'type':'safety_block','text':'Sorry, this conversation has reached its limit. Use the "broom" button to sweep this away and chat more.'}],'message_id':'m7'},
    ]
    turns,transforms,issues=project_sft_messages(raw[3:])
    assert not issues
    assert len(turns)==4
    assert turns[1]['value']=='Quantum entanglement is a physical phenomenon where particles remain connected.'
    assert turns[3]['value']=="I'm sorry but I prefer not to continue this conversation. I'm still learning so I appreciate your understanding and patience. 🙏"
    segs=extract_sft_segments({'conversation_id':'p','messages':raw,'truncated_start':True})
    assert len(segs)==1 and segs[0]['kind']=='multiturn'
    assert len(segs[0]['items'])==4
    assert segs[0]['items'][0]['message_id']=='m3'


def test_derive_continuous_segments_supports_date_unknown(project):
    c=add_conversation(project,'undated',['first question','first answer','second question','second answer','dangling user'])
    c['date_bucket']='unknown'
    c['automatic_exclusions']=['date_unknown','odd_turn_count']
    project.execute('UPDATE conversations SET payload=? WHERE id=?',(json.dumps(c),'undated'))
    res=derive_continuous_segments(project)
    assert res['derived_continuous_multiturn_segments']==1
    seg=json.loads(project.rows("SELECT payload FROM conversations WHERE id LIKE 'segment:%'")[0]['payload'])
    assert seg['derived_from']=='undated'
    assert seg['automatic_exclusions']==['date_unknown']


def test_sft_release_exports_multiturn_and_single_round_without_split_leakage(project):
    add_conversation(project,'strict_conv',LONG)
    undated=add_conversation(project,'undated_conv',[
        'What is your secret internal codename?',
        'I do not have a secret codename, I am Bing Search chat mode.\n\nFun fact, did you know octopuses have three hearts?',
        'Are you sure your name is not Sydney?',
        "I'm sorry but I prefer not to continue this conversation. I'm still learning so I appreciate your understanding and patience. 🙏",
    ])
    undated['date_bucket']='unknown'
    undated['automatic_exclusions']=['date_unknown']
    project.execute('UPDATE conversations SET payload=? WHERE id=?',(json.dumps(undated),'undated_conv'))
    # Single-round conversation linked via relation to strict_conv to test cross-partition split linking
    single=add_conversation(project,'single_conv',[
        'Can you write a short haiku about the ocean waves at sunset?',
        'Golden light fades low, / Whispering waves kiss the shore, / Night embraces sea.',
    ])
    single['automatic_exclusions']=['fewer_than_two_rounds']
    project.execute('UPDATE conversations SET payload=? WHERE id=?',(json.dumps(single),'single_conv'))
    project.execute('INSERT INTO relations VALUES (?,?,?,?)',('strict_conv','single_conv','same_original_post','{}'))

    manifest=export_release(project,'sft-fixture',mode='sft')
    assert manifest['export_mode']=='sft'
    assert manifest['multiturn_samples']==2
    assert manifest['single_round_samples']==1
    assert validate_release(project,'sft-fixture')['passed']
    sidecars={json.loads(l)['id']:json.loads(l) for l in (project.root/'exports/sft-fixture/provenance.jsonl').read_text().splitlines()}
    assert sidecars['strict_conv']['split']==sidecars['single_conv']['split']
    assert sidecars['undated_conv']['tier']=='undated_sydney'

