import io
import json

from PIL import Image
import pytest

from newbing.core import atomic,digest,now
from newbing.cleaning import derive_continuous_segments,project_messages
from newbing.corrections import import_correction,restore_corrections
from newbing.exporter import export_release,validate_release
from newbing.verification import verification_contract,evidence_identity


def message(role,text,index,**kwargs):
    return {'role':role,'content':[{'type':'text','text':text}],'message_id':f'm{index}','evidence':[],**kwargs}


def parent(messages):
    turns,_,_=project_messages(messages)
    return {'conversation_id':'original','source_ids':['s'],'messages':messages,
            'projected':{'id':'original','conversations':[{k:m[k] for k in ('from','value')} for m in turns]},
            'automatic_exclusions':['source_conversation_truncated'],'date_bucket':'early','date_confidence':'probable',
            'date':'2023-02-15','truncated_start':True,'truncated_end':False,'transforms':[],
            'asset_ids':[],'source':{},'message_projection_map':list(range(len(messages)))}


def test_contiguous_segment_keeps_two_real_rounds(project):
    c=parent([message('assistant','unseen question reply',0),message('user','new question',1),message('assistant','answer',2),message('user','follow up',3),message('assistant','next answer',4)])
    project.execute('INSERT INTO conversations VALUES (?,?,?,?)',('original','s',json.dumps(c),now()))
    assert derive_continuous_segments(project)['derived_continuous_multiturn_segments']==1
    d=json.loads(project.rows("SELECT payload FROM conversations WHERE id LIKE 'segment:%'")[0]['payload'])
    assert len(d['projected']['conversations'])==4
    assert d['derived_from']=='original' and d['segment_context_gap']
    assert d['projected']['conversations'][0]['value']=='new question'


def test_segments_do_not_join_across_unreadable_middle(project):
    c=parent([message('user','a',0),message('assistant','b',1),message('user','⟦unreadable⟧',2),message('assistant','d',3),message('user','e',4),message('assistant','f',5)])
    project.execute('INSERT INTO conversations VALUES (?,?,?,?)',('original','s',json.dumps(c),now()))
    assert derive_continuous_segments(project)['derived_continuous_multiturn_segments']==0


def test_correction_replay_order_and_idempotence(project):
    c=parent([message('user','hi',0),message('assistant','old',1)]);c['asset_ids']=['asset'];c['automatic_exclusions']=[]
    project.execute('INSERT INTO conversations VALUES (?,?,?,?)',('original','s',json.dumps(c),now()))
    for old,new,date in [('old','middle','2026-01-01'),('middle','final','2026-01-02')]:
        d={'conversation_id':'original','reviewer_type':'model_evidence_correction','reviewed_at':date,
           'patches':[{'message_id':'m1','old':old,'new':new,'asset_id':'asset','region':[0,0,1,1]}]}
        file=project.run/(date+'.json');atomic(file,d);import_correction(project,file)
    saved=json.loads(project.rows('SELECT payload FROM conversations')[0]['payload'])
    assert saved['projected']['conversations'][1]['value']=='final'
    assert all(x=='already_applied' for x in restore_corrections(project))
    project.execute('UPDATE conversations SET payload=?',(json.dumps(c),))
    restore_corrections(project)
    saved=json.loads(project.rows('SELECT payload FROM conversations')[0]['payload'])
    assert saved['projected']['conversations'][1]['value']=='final'


def seed_exportable(project):
    raw=project.root/'data/raw/test.json';atomic(raw,{'original':'evidence'})
    project.add_source('s','baseline',{},raw)
    image=Image.new('RGB',(12,12),'white');buffer=io.BytesIO();image.save(buffer,format='PNG');data=buffer.getvalue();aid=digest(data)
    path=project.root/'data/assets'/f'{aid}.png';atomic(path,data,raw=True)
    project.execute('INSERT INTO assets VALUES (?,?,?,?,?,?)',(aid,str(path.relative_to(project.root)),'image/png',len(data),12,12))
    c=parent([message('user','a',0),message('assistant','b',1),message('user','c',2),message('assistant','d',3)])
    c.update(automatic_exclusions=[],truncated_start=False,asset_ids=[aid],human_status='pending_human')
    for m in c['messages']:m['evidence']=[{'source_id':'s','asset_id':aid,'region':[0,0,1,1]}]
    project.execute('INSERT INTO conversations VALUES (?,?,?,?)',('original','s',json.dumps(c),now()))
    v={'conversation_id':'original','conversation_hash':digest(c),'contract_hash':digest(verification_contract(project)),
       'images':[{'image_id':aid,'path':str(path),'asset_id':aid,'box':[0,0,12,12]}],
       'evidence_hash':digest(evidence_identity(project,c)),
       'result':{'verdict':'pass','issues':[],'unresolved_questions':[],
        'message_checks':[{'message_id':m['message_id'],'requires_nontext_chat_context':False,
                          'text_faithful':True,'role_correct':True,'context_sufficient':True,'evidence':m['evidence']}
                         for m in c['messages']]}}
    path=project.root/'data/processed/verify.json';atomic(path,v)
    project.execute('INSERT INTO tasks(id,kind,payload,status,output_ref,updated) VALUES (?,?,?,?,?,?)',
        ('verify','verify',json.dumps({'conversation_id':'original'}),'succeeded',str(path.relative_to(project.root)),now()))
    return aid


def test_export_and_validate_actual_files_and_evidence(project):
    seed_exportable(project)
    result=export_release(project,'fixture-v1')
    assert result['samples']==1
    assert validate_release(project,'fixture-v1')['passed']
    files=list((project.root/'exports/fixture-v1/sharegpt').glob('*/*.json'))
    rows=[row for file in files for row in json.loads(file.read_text())]
    assert rows==[{'id':'original','conversations':[{'from':'human','value':'a'},{'from':'gpt','value':'b'},{'from':'human','value':'c'},{'from':'gpt','value':'d'}]}]
    with pytest.raises(ValueError,match='already frozen'):export_release(project,'fixture-v1')


def test_validator_detects_asset_tampering(project):
    aid=seed_exportable(project);export_release(project,'fixture-v1')
    image=project.root/project.rows('SELECT path FROM assets WHERE id=?',(aid,))[0]['path']
    image.write_bytes(b'tampered')
    with pytest.raises(ValueError,match='asset_hash_mismatch'):validate_release(project,'fixture-v1')


def test_zero_eligible_is_not_a_release(project):
    result=export_release(project,'empty')
    assert result['status']=='no_qualified_release_yet'
    assert not (project.root/'exports/empty/manifest.json').exists()


def test_retracted_release_rejected(project):
    seed_exportable(project);export_release(project,'fixture-v1')
    path=project.root/'exports/fixture-v1/manifest.json';m=json.loads(path.read_text());m['status']='retracted';atomic(path,m)
    with pytest.raises(ValueError,match='retracted'):validate_release(project,'fixture-v1')


def test_frozen_release_contains_reproduction_inputs_without_credentials(project):
    seed_exportable(project);export_release(project,'repro')
    root=project.root/'exports/repro/reproduction'
    assert (root/'config.json').is_file() and (root/'runtime.json').is_file()
    assert (root/'prompts/verify.md').is_file() and (root/'schemas/sharegpt.sample.schema.json').is_file()
    assert not (root/'.env.local').exists()
    assert all('local-test-dummy' not in p.read_text() for p in root.rglob('*') if p.is_file())


def test_unresolved_authenticity_claim_is_not_promoted_by_faithful_text_check(project):
    seed_exportable(project)
    project.execute('INSERT INTO relations VALUES (?,?,?,?)',('original','original','authenticity_review_required','{}'))
    result=export_release(project,'needs-source-review')
    assert result['status']=='no_qualified_release_yet'
    assert result['exclusion_counts']['authenticity_review_required']==1
