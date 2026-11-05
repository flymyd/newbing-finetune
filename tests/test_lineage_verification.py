import copy
import io
import json
from types import SimpleNamespace

from PIL import Image
import httpx
import pytest

from newbing.core import atomic,digest,now
from newbing.cleaning import derive_continuous_segments
from newbing.exporter import grouping,export_release,validate_release
from newbing.verification import aggregate_checks,image_windows,verify_one,verification_contract,evidence_identity
from newbing.network import retry_delay
from newbing.llm import ModelClient
from test_exports import seed_exportable,parent,message


def test_text_only_siblings_group_even_if_parent_absent():
    rows=[{'conversation_id':cid,'asset_ids':[],'derived_from':'parent',
           'projected':{'conversations':[{'from':'human','value':cid}]}} for cid in ('left','right')]
    groups=grouping(rows)
    assert groups['left']==groups['right']


def test_parent_change_retires_old_fragment_and_rebuilds(project):
    c=parent([message('assistant','missing prompt',0),message('user','hi',1),message('assistant','a',2),message('user','next',3),message('assistant','b',4)])
    project.execute('INSERT INTO conversations VALUES (?,?,?,?)',('original','s',json.dumps(c),now()))
    derive_continuous_segments(project)
    old=project.rows("SELECT id,payload FROM conversations WHERE id LIKE 'segment:%'")[0]
    old_data=json.loads(old['payload']);assert old_data['parent_content_hash']==digest(c)
    c['messages'][2]['content'][0]['text']='corrected answer'
    project.execute('UPDATE conversations SET payload=? WHERE id=?',(json.dumps(c),'original'))
    result=derive_continuous_segments(project)
    assert result['retired_segments']==1
    stale=json.loads(project.rows('SELECT payload FROM conversations WHERE id=?',(old['id'],))[0]['payload'])
    assert stale['superseded'] and 'superseded_derived_segment' in stale['automatic_exclusions']
    active=[json.loads(r['payload']) for r in project.rows("SELECT payload FROM conversations WHERE id LIKE 'segment:%'") if not json.loads(r['payload']).get('superseded')]
    assert len(active)==1 and active[0]['parent_content_hash']==digest(c)
    assert active[0]['messages'][1]['content'][0]['text']=='corrected answer'
    assert derive_continuous_segments(project)['retired_segments']==0


def check(mid,**kw):
    return {'message_id':mid,'text_faithful':True,'role_correct':True,'context_sufficient':True,
            'requires_nontext_chat_context':False,'evidence':[{'image_id':'image','region':[0,0,1,1]}],'note':'visible',**kw}


def window(checks,index=0):
    return {'index':index,'result':{'verdict':'pass','issues':[],'unresolved_questions':[],'message_checks':checks}}


def test_long_verification_coverage_and_conflicts():
    c={'messages':[{'message_id':'one'},{'message_id':'two'}]}
    result=aggregate_checks(c,[window([check('one')]),window([check('two')],1)])
    assert result['verdict']=='pass' and len(result['message_checks'])==2
    assert aggregate_checks(c,[window([check('one')])])['verdict']=='uncertain'
    assert aggregate_checks(c,[window([check('one'),check('two')]),window([check('one',text_faithful=False)],1)])['verdict']=='uncertain'
    with pytest.raises(ValueError,match='duplicate'):aggregate_checks(c,[window([check('one'),check('one')])])


def test_long_verify_calls_every_window_and_binds_versions(project,monkeypatch):
    seed_exportable(project)
    c=json.loads(project.rows("SELECT payload FROM conversations WHERE id='original'")[0]['payload'])
    images=[]
    for n in range(13):
        file=project.root/f'crop{n}.png';Image.new('RGB',(10,10),(n,0,0)).save(file)
        images.append({'image_id':f'i{n}','path':str(file),'asset_id':f'a{n}','box':[0,0,10,10]})
    monkeypatch.setattr('newbing.verification.prepare_images',lambda *args:images)
    calls=[]
    def fake_call(self,stage,prompt,images,schema,**kwargs):
        calls.append([i for i,_ in images])
        checks=[check(m['message_id'],evidence=[{'image_id':images[0][0],'region':[0,0,1,1]}]) for m in c['messages']]
        result=window(checks)['result']
        response={'id':str(len(calls)),'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':json.dumps(result)}]}]}
        return response,False
    monkeypatch.setattr(ModelClient,'call',fake_call)
    payload={'conversation_id':'original','conversation_hash':digest(c),
             'contract_hash':digest(verification_contract(project)),'evidence_hash':digest(evidence_identity(project,c))}
    out=verify_one(project,payload);v=json.loads(out.read_text())
    assert v['result']['verdict']=='pass'
    assert len(calls)==3 and {i for call in calls for i in call}=={i['image_id'] for i in images}
    assert calls[0][-1]==calls[1][0] and calls[1][-1]==calls[2][0]
    assert v['contract_hash']==payload['contract_hash'] and len(v['coverage']['checked_message_ids'])==4


def test_new_contract_rejects_old_pass_but_frozen_release_remains_valid(project):
    seed_exportable(project);export_release(project,'before')
    prompt=project.root/'prompts/verify.md';prompt.write_text(prompt.read_text()+'\nnew evidence rule\n')
    assert export_release(project,'after')['status']=='no_qualified_release_yet'
    assert validate_release(project,'before')['passed']


def test_millisecond_reset_is_epoch_not_duration(monkeypatch):
    monkeypatch.setattr('newbing.network.time.time',lambda:1700000000)
    assert retry_delay(httpx.Headers({'X-RateLimit-Reset':'1700000030000'}),2)==30
    assert retry_delay(httpx.Headers({'X-RateLimit-Reset':'1700000030'}),2)==30
    assert retry_delay(httpx.Headers({'X-RateLimit-Reset':'50'}),2)==50


def test_explicit_project_credential_wins_over_process_environment(project,monkeypatch):
    monkeypatch.setenv('NEWBING_API_KEY','unrelated-environment-key')
    client=ModelClient(project)
    assert client.secret=='local-test-dummy'
    client.client.close()


def test_dry_run_does_not_construct_project_or_mutate_queue(project,monkeypatch,capsys):
    from newbing.cli import main,preview
    import argparse
    before=project.rows('SELECT * FROM tasks')
    args=argparse.Namespace(command='classify',dry_run=True,limit=50,workers=2)
    result=preview(args,project.root)
    assert result['writes']==result['network_requests']==result['model_requests']==0
    assert project.rows('SELECT * FROM tasks')==before
    monkeypatch.setattr('sys.argv',['newbing','verify','--sample','3','--dry-run'])
    def forbidden(*a,**kw):raise AssertionError('Project was constructed')
    monkeypatch.setattr('newbing.cli.Project',forbidden)
    monkeypatch.setattr('newbing.cli.preview',lambda args:preview(args,project.root))
    main()
    assert json.loads(capsys.readouterr().out)['writes']==0


def test_preview_recovery_is_finite_and_reports_only_new_tasks(project):
    from newbing.media_recovery import queue_preview_fallbacks
    raw=project.root/'data/raw/post.json';atomic(raw,{})
    source={'post':{'preview':{'images':[{'source':{'url':'https://preview.redd.it/a'},
                    'resolutions':[{'url':'https://preview.redd.it/b'}]}]}}}
    project.add_source('s','reddit_post',source,raw)
    original=project.enqueue('download',{'source_id':'s','ordinal':0,'url':'https://i.redd.it/dead.png'})
    project.execute("UPDATE tasks SET status='failed_terminal' WHERE id=?",(original,))
    assert queue_preview_fallbacks(project)['preview_fallback_tasks_queued']==2
    project.execute("UPDATE tasks SET status='failed_terminal'")
    assert queue_preview_fallbacks(project)['preview_fallback_tasks_queued']==0
    assert len(project.rows('SELECT * FROM tasks'))==3


def test_same_filename_is_not_proof_of_identical_upstream_bytes(project):
    from newbing.network import Fetcher
    raws=project.root/'data/raw/source.json';atomic(raws,{})
    for sid in ('one','two'):project.add_source(sid,'baseline',{'upstream_id':'same'},raws)
    calls=[]
    def handler(request):
        calls.append(str(request.url))
        im=Image.new('RGB',(10,10),'red' if '/one/' in request.url.path else 'blue')
        buffer=io.BytesIO();im.save(buffer,format='PNG')
        return httpx.Response(200,headers={'content-type':'image/png'},content=buffer.getvalue())
    fetcher=Fetcher(project,httpx.Client(transport=httpx.MockTransport(handler)))
    a=fetcher.asset('one',0,'https://i.redd.it/one/same.png')
    b=fetcher.asset('two',0,'https://i.redd.it/two/same.png')
    assert a!=b and len(calls)==2


@pytest.mark.parametrize('changed',[False,True])
def test_interrupted_download_resumes_only_with_matching_validator(project,monkeypatch,changed):
    from newbing.network import Fetcher
    monkeypatch.setattr('newbing.network.time.sleep',lambda seconds:None)
    class Interrupted(httpx.SyncByteStream):
        def __iter__(self):
            yield b'abc'
            raise httpx.ReadError('interrupted')
    requests=[]
    def handler(request):
        requests.append(request)
        if len(requests)==1:return httpx.Response(200,headers={'ETag':'"v1"'},stream=Interrupted())
        assert request.headers['range']=='bytes=3-' and request.headers['if-range']=='"v1"'
        if changed:return httpx.Response(200,headers={'ETag':'"v2"'},content=b'new-content')
        return httpx.Response(206,headers={'ETag':'"v1"','Content-Range':'bytes 3-5/6'},content=b'def')
    p,info=Fetcher(project,httpx.Client(transport=httpx.MockTransport(handler))).fetch('https://i.redd.it/resumable')
    assert p.read_bytes()==(b'new-content' if changed else b'abcdef')
    assert info['resumed_prefix_bytes']==(0 if changed else 3)
    assert sum(r['bytes'] for r in project.rows('SELECT bytes FROM http_attempts'))==(14 if changed else 6)
    assert not list((project.root/'data/derived/download_parts').glob('*.bin'))


def test_transient_model_failures_count_every_attempt_and_open_circuit(project,monkeypatch):
    from openai import InternalServerError
    from newbing.llm import ModelResultError
    monkeypatch.setattr('newbing.llm.time.sleep',lambda n:None)
    client=ModelClient(project);calls=[]
    def fail(**body):
        calls.append(body)
        raise InternalServerError('temporary',response=httpx.Response(503,request=httpx.Request('POST','https://llm.flymyd.cn/v1/responses')),body={})
    client.client.responses.create=fail
    with pytest.raises(InternalServerError):client.call('test','a')
    assert len(calls)==3 and len(project.rows('SELECT * FROM model_attempts'))==3
    with pytest.raises(ModelResultError,match='cooldown'):client.call('test','b')
    assert len(calls)==5
    assert project.rows("SELECT state FROM source_status WHERE id='model_circuit'")[0]['state']=='open'


def test_format_recovery_stays_in_one_persistent_logical_budget_and_cache(project):
    client=ModelClient(project);calls=[]
    schema={'type':'object','properties':{'ok':{'type':'boolean'}},'required':['ok'],'additionalProperties':False}
    def create(**body):
        calls.append(body)
        result={'id':'mock','status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':'invalid' if len(calls)==1 else '{"ok":true}'}]}],
                'usage':{'total_tokens':10}}
        return SimpleNamespace(model_dump=lambda **kw:result)
    client.client.responses.create=create
    assert client.call('test','preserve source',schema=schema)[1] is False
    assert client.call('test','preserve source',schema=schema)[1] is True
    rows=project.rows('SELECT logical_key FROM model_attempts')
    assert len(rows)==2 and rows[0]['logical_key']==rows[1]['logical_key']
    assert len(calls)==2


def test_invalid_config_fails_before_project_side_effects(project):
    from newbing.core import validate_config
    config=copy.deepcopy(project.config);config['pipeline']['export']['allow_single_turn']=True
    with pytest.raises(ValueError,match='ShareGPT'):validate_config(project.root,config)
    config=copy.deepcopy(project.config);config['pipeline']['split']['test_fraction']=0.4
    with pytest.raises(ValueError,match='split'):validate_config(project.root,config)


def test_reference_discovery_is_bounded_and_rejects_unlisted_hosts():
    from newbing.references import reference_links
    body='<main><a href="https://evil.invalid/bing">Bing</a><a href="/2023/bing-a">Bing A</a><a href="/2023/bing-b">Bing B</a><a href="/login">Sydney login</a></main>'
    assert reference_links(body,'https://simonwillison.net/start',['simonwillison.net'],1)==['https://simonwillison.net/2023/bing-a']


def test_oversized_lossless_crop_splits_with_original_coordinates(project):
    from newbing.model_tasks import prepare_images
    # Deterministic noncompressible RGB pixels ensure the byte cap causes geometric subdivision.
    import random
    data=random.Random(1).randbytes(160*160*3)
    image=Image.frombytes('RGB',(160,160),data);path=project.root/'data/assets/noisy.png';path.parent.mkdir(parents=True,exist_ok=True);image.save(path)
    project.config['models']['profiles']['flymyd']['max_raw_image_bytes_per_request']=30000
    result=prepare_images(project,[{'id':'original','path':str(path.relative_to(project.root)),'width':160,'height':160}])
    assert len(result)>1 and all(__import__('pathlib').Path(r['path']).stat().st_size<=30000 for r in result)
    assert all(r['asset_id']=='original' and 0<=r['box'][0]<r['box'][2]<=160 and 0<=r['box'][1]<r['box'][3]<=160 for r in result)
    assert min(r['box'][1] for r in result)==0 and max(r['box'][3] for r in result)==160


def test_media_variants_keep_one_canonical_slot_and_all_raw_refs(project):
    from newbing.assets import ordered_source_assets
    raw=project.root/'data/raw/source.json';atomic(raw,{})
    project.add_source('s','reddit_post',{'image_urls':['https://i.redd.it/original','https://i.redd.it/second']},raw)
    for aid,w,h,url,ordinal in [('original',10,10,'https://i.redd.it/original',0),('preview',50,50,'https://preview.redd.it/a',0),('second',20,20,'https://i.redd.it/second',1)]:
        project.execute('INSERT INTO assets VALUES (?,?,?,?,?,?)',(aid,'unused','image/png',100,w,h))
        project.execute('INSERT INTO source_assets VALUES (?,?,?,?)',('s',aid,ordinal,url))
    chosen=ordered_source_assets(project,'s')
    assert [a['asset_id'] for a in chosen]==['original','second']
    assert len(project.rows('SELECT * FROM source_assets'))==3


def test_expired_lease_of_live_owner_is_not_stolen(project):
    import time
    task=project.enqueue('transcribe',{'candidate_id':'still-active'})
    assert project.claim_task(task,1)
    project.execute('UPDATE tasks SET lease_until=? WHERE id=?',(time.time()-10,task))
    project.recover()
    assert project.rows('SELECT status FROM tasks WHERE id=?',(task,))[0]['status']=='leased'


def test_missing_gallery_slot_does_not_renumber_visible_screenshots(project):
    from newbing.assets import ordered_source_assets,missing_media_ordinals
    raw=project.root/'data/raw/gallery.json';atomic(raw,{})
    post={'gallery_data':{'items':[{'media_id':'missing'},{'media_id':'visible'}]},
          'media_metadata':{'visible':{'s':{'u':'https://i.redd.it/visible.png'}}}}
    project.add_source('s','reddit_post',{'post':post,'image_urls':['https://i.redd.it/visible.png']},raw)
    project.execute('INSERT INTO assets VALUES (?,?,?,?,?,?)',('a','unused','image/png',100,20,20))
    # Legacy ingestion compressed the source ordinal; the read projection restores it from raw metadata.
    project.execute('INSERT INTO source_assets VALUES (?,?,?,?)',('s','a',0,'https://i.redd.it/visible.png'))
    selected=ordered_source_assets(project,'s')
    assert selected[0]['ordinal']==1 and selected[0]['recorded_ordinal']==0
    assert missing_media_ordinals(project,'s',selected)==[0]


def test_album_missing_middle_splits_continuity_without_adding_text():
    from newbing.cleaning import mark_source_gaps
    conv={'truncated_start':False,'truncated_end':False,'messages':[
        {'evidence':[{'asset_id':'first'}],'context_gap_before':False},
        {'evidence':[{'asset_id':'third'}],'context_gap_before':False}]}
    mark_source_gaps(conv,{'first':0,'third':2},[1])
    assert conv['messages'][1]['context_gap_before']
    assert not conv['truncated_start'] and not conv['truncated_end']
    conv={'truncated_start':False,'truncated_end':False,'messages':[{'evidence':[{'asset_id':'third'}]}]}
    mark_source_gaps(conv,{'third':2},[0,1,3])
    assert conv['truncated_start'] and conv['truncated_end']


def test_new_media_changes_candidate_identity(project):
    from newbing.model_tasks import candidate_data,candidate_identity
    raw=project.root/'data/raw/post.json';atomic(raw,{})
    project.add_source('s','reddit_post',{'text':'quoted original'},raw)
    project.add_candidate('c','s',{})
    before=candidate_identity(project,candidate_data(project,'c'))
    project.execute('INSERT INTO assets VALUES (?,?,?,?,?,?)',('a','unused','image/png',100,20,20))
    project.execute('INSERT INTO source_assets VALUES (?,?,?,?)',('s','a',0,'https://i.redd.it/a'))
    assert before!=candidate_identity(project,candidate_data(project,'c'))


def test_current_candidate_task_replaces_pending_legacy_snapshot(project):
    from newbing.model_tasks import queue_current_task
    old=project.enqueue('classify',{'candidate_id':'c','prompt_hash':'same'})
    new=queue_current_task(project,'classify',{'candidate_id':'c','prompt_hash':'same','input_evidence':{'ordered_assets':['new']}})
    assert old!=new
    assert project.rows('SELECT status FROM tasks WHERE id=?',(old,))[0]['status']=='superseded_input'
    data=json.loads(project.rows('SELECT payload FROM tasks WHERE id=?',(new,))[0]['payload'])
    assert data['schema_hash'] and data['model_parameters_hash']
    assert queue_current_task(project,'classify',{'candidate_id':'c','prompt_hash':'same','input_evidence':{'ordered_assets':['new']}})==new


def test_latest_empty_transcription_retires_previous_conversation(project):
    from newbing.cleaning import reconstruct_transcriptions
    from newbing.model_tasks import candidate_data,candidate_identity
    raw=project.root/'data/raw/quoted.json';atomic(raw,{'text':'original quote'})
    project.add_source('s','reference_page',{'text':'original quote','posted_at':'2023-02-15'},raw)
    project.add_candidate('c','s',{})
    identity=candidate_identity(project,candidate_data(project,'c'))
    classification=project.root/'data/processed/classification.json'
    atomic(classification,{'input_payload':{'input_evidence':identity},'result':{'product':'bing','content_kind':'conversation','authenticity_status':'source_claim_only'}})
    project.execute('INSERT INTO tasks(id,kind,payload,status,output_ref,updated) VALUES (?,?,?,?,?,?)',('cl','classify',json.dumps({'candidate_id':'c'}),'succeeded',str(classification.relative_to(project.root)),'2026-01-01'))
    messages=[{'role':role,'parts':[{'type':'text','text':text}],'evidence':[{'source_id':'s','quote':text,'start':0,'end':1}],
               'truncated_start':False,'truncated_end':False,'context_gap_before':False} for role,text in [('user','a'),('assistant','b'),('user','c'),('assistant','d')]]
    def record(name,conversations,stamp):
        path=project.root/'data/processed'/f'{name}.json'
        atomic(path,{'candidate_id':'c','source_id':'s','windows':[{'images':[],'result':{'conversations':conversations,'visual_context_dependency':'absent'},'text_source':{'source_id':'s','text':'original quote','raw_path':str(raw.relative_to(project.root))}}]})
        project.execute('INSERT INTO tasks(id,kind,payload,status,output_ref,updated) VALUES (?,?,?,?,?,?)',(name,'transcribe',json.dumps({'candidate_id':'c'}),'succeeded',str(path.relative_to(project.root)),stamp))
    record('old',[{'messages':messages,'truncated_start':False,'truncated_end':False}],'2026-01-01')
    assert reconstruct_transcriptions(project)['reconstructed_model_conversations']==1
    record('new',[],'2026-01-02')
    result=reconstruct_transcriptions(project)
    assert result['retired_conversations']==1
    c=json.loads(project.rows('SELECT payload FROM conversations')[0]['payload'])
    assert c['superseded'] and 'superseded_transcription_version' in c['automatic_exclusions']


def test_download_host_concurrency_is_shared_between_connections(project):
    from concurrent.futures import ThreadPoolExecutor,TimeoutError
    from newbing.core import Project
    other=Project(project.root)
    other.policy['download']=copy.deepcopy(project.policy['download'])
    one=project.reserve_http('https://i.redd.it/one','image',1)
    two=project.reserve_http('https://i.redd.it/two','image',1)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(other.reserve_http,'https://i.redd.it/three','image',1)
        with pytest.raises(TimeoutError):future.result(timeout=0.05)
        project.execute('UPDATE http_attempts SET finished=?,reserved=0 WHERE id=?',(now(),one))
        three=future.result(timeout=2)
    assert three>two
    assert project.rows('SELECT count(*) n FROM http_attempts WHERE finished IS NULL')[0]['n']==2
    other.db.close()


def test_invalid_resume_validator_is_rejected(project,monkeypatch):
    from newbing.network import Fetcher,FetchError
    monkeypatch.setattr('newbing.network.time.sleep',lambda n:None)
    class Interrupted(httpx.SyncByteStream):
        def __iter__(self):
            yield b'abc'
            raise httpx.ReadError('interrupted')
    calls=[]
    def handler(request):
        calls.append(request)
        if len(calls)==1:return httpx.Response(200,headers={'ETag':'"old"'},stream=Interrupted())
        return httpx.Response(206,headers={'ETag':'"different"','Content-Range':'bytes 3-5/6'},content=b'def')
    with pytest.raises(FetchError,match='changed validator'):
        Fetcher(project,httpx.Client(transport=httpx.MockTransport(handler))).fetch('https://i.redd.it/invalid-resume')
    assert len(calls)==2 and not project.rows('SELECT * FROM assets')


def test_format_correction_cannot_reset_logical_attempt_budget(project):
    from newbing.core import BudgetExceeded
    client=ModelClient(project);calls=[]
    schema={'type':'object','properties':{'ok':{'type':'boolean'}},'required':['ok'],'additionalProperties':False}
    def create(**body):
        calls.append(body)
        response={'id':'mock','status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':'invalid'}]}],'usage':{'total_tokens':10}}
        return SimpleNamespace(model_dump=lambda **kw:response)
    client.client.responses.create=create
    with pytest.raises(json.JSONDecodeError):client.call('test','fixed-task',schema=schema)
    with pytest.raises(BudgetExceeded,match='logical'):client.call('test','fixed-task',schema=schema)
    assert len(calls)==3
    assert len({r['logical_key'] for r in project.rows('SELECT logical_key FROM model_attempts')})==1
    requests=list(project.run.glob('*.request.json'))
    assert len(requests)==3
    assert all('local-test-dummy' not in f.read_text() and 'Authorization' not in f.read_text() for f in requests)


def test_host_request_spacing_is_shared_between_connections(project):
    from newbing.core import Project
    from datetime import datetime
    project.policy['download']['initial_per_host_requests_per_second']=5
    first=project.reserve_http('https://i.redd.it/rate-one','page',1)
    other=Project(project.root);other.policy['download']['initial_per_host_requests_per_second']=5
    second=other.reserve_http('https://i.redd.it/rate-two','page',1)
    rows=project.rows('SELECT started FROM http_attempts WHERE id IN (?,?) ORDER BY id',(first,second))
    assert (datetime.fromisoformat(rows[1]['started'])-datetime.fromisoformat(rows[0]['started'])).total_seconds()>=0.15
    other.db.close()


def test_disjoint_reconstruction_parts_from_one_source_stay_in_one_split():
    rows=[{'conversation_id':cid,'source_ids':['same-post'],'asset_ids':[aid],
           'projected':{'conversations':[{'from':'human','value':cid}]}} for cid,aid in [('first','a'),('second','b')]]
    groups=grouping(rows)
    assert groups['first']==groups['second']


def test_source_503_honors_long_retry_after_without_blocking(project):
    from newbing.network import Fetcher,FetchError
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(503,headers={'Retry-After':'120'})
    with pytest.raises(FetchError) as error:
        Fetcher(project,httpx.Client(transport=httpx.MockTransport(handler))).fetch('https://i.redd.it/service-unavailable')
    assert error.value.status==503 and error.value.retry_at
    assert len(calls)==1


def test_model_503_honors_provider_retry_after(project):
    from openai import InternalServerError
    from newbing.llm import ModelResultError
    client=ModelClient(project);calls=[]
    def create(**body):
        calls.append(body)
        raise InternalServerError('temporary',response=httpx.Response(503,headers={'Retry-After':'120'},request=httpx.Request('POST','https://llm.flymyd.cn/v1/responses')),body={})
    client.client.responses.create=create
    with pytest.raises(ModelResultError,match='cooldown'):client.call('test','provider backoff')
    assert len(calls)==1
    state=project.rows("SELECT state,payload FROM source_status WHERE id='model_circuit'")[0]
    assert state['state']=='open' and json.loads(state['payload'])['retry_at']


def test_gateway_error_never_exposes_project_key_in_propagated_message(project):
    from openai import BadRequestError
    client=ModelClient(project)
    def fail(**body):
        raise BadRequestError('provider echoed local-test-dummy',response=httpx.Response(400,request=httpx.Request('POST','https://llm.flymyd.cn/v1/responses')),body={})
    client.client.responses.create=fail
    with pytest.raises(BadRequestError) as error:client.call('test','irrelevant input')
    assert 'local-test-dummy' not in str(error.value)
    assert all('local-test-dummy' not in f.read_text() for f in project.run.glob('*.json'))


def test_candidate_limit_is_atomic_across_connections(project):
    from newbing.core import Project,BudgetExceeded
    from concurrent.futures import ThreadPoolExecutor
    other=Project(project.root)
    project.policy['budgets']['max_candidate_groups']=1;other.policy['budgets']['max_candidate_groups']=1
    def add(p,cid):
        try:p.add_candidate(cid,'s',{});return 'added'
        except BudgetExceeded:return 'limited'
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda args:add(*args),[(project,'first'),(other,'second')]))
    assert sorted(results)==['added','limited']
    assert project.rows('SELECT count(*) n FROM candidates')[0]['n']==1
    other.db.close()


def test_late_post_does_not_turn_unknown_chat_date_into_later_chat():
    from newbing.cleaning import archive_date
    assert archive_date(None,'2023-03-12T12:00:00Z')[:3]==('unknown','unknown',None)
    assert archive_date(None,'2023-02-14T12:00:00Z')[:2]==('early','probable')
    assert archive_date('2023-02-13','2023-03-12T12:00:00Z')[:3]==('early','probable','2023-02-13')
    assert archive_date('2023-03-12','2023-02-13T12:00:00Z')[3]=='capture_claim_conflicts_with_post_upper_bound'


def test_invalid_transcription_box_can_keep_coarse_source_scope_but_not_claim_precision(tmp_path):
    from newbing.model_tasks import normalize_evidence
    path=tmp_path/'image.png';Image.new('RGB',(20,20)).save(path)
    result={'conversations':[{'messages':[{'evidence':[{'image_id':'known','region':[0,0,400,600]}]}]}]}
    changes=normalize_evidence(result,[{'image_id':'known','path':str(path)}],allow_full_image_fallback=True)
    assert result['conversations'][0]['messages'][0]['evidence'][0]['region']==[0,0,1,1]
    assert changes[0]['localization_status']=='unverified_no_precise_region_claim'
    result['conversations'][0]['messages'][0]['evidence'][0]['image_id']='invented'
    with pytest.raises(ValueError,match='invalid image evidence reference'):
        normalize_evidence(result,[{'image_id':'known','path':str(path)}],allow_full_image_fallback=True)


def test_verification_code_fingerprint_is_stable_across_process_hash_seeds():
    import os,subprocess,sys
    code='from newbing.verification import function_hash; print(function_hash("cleaning.py",("part_text","project_messages","validate_sharegpt")))'
    values=[]
    for seed in ('1','77'):
        result=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True,check=True,env={**os.environ,'PYTHONHASHSEED':seed})
        values.append(result.stdout.strip())
    assert values[0]==values[1]


def test_known_original_with_new_media_is_reviewed_for_added_context(project):
    from newbing.model_tasks import known_baseline_has_additional_context
    raw=project.root/'data/raw/source.json';atomic(raw,{})
    project.add_source('original','baseline',{},raw);project.add_candidate('baseline:original','original',{})
    project.add_source('post','reddit_post',{'text':'short title'},raw);project.add_candidate('post','post',{})
    project.execute('INSERT INTO relations VALUES (?,?,?,?)',('post','baseline:original','same_original_post','{}'))
    assert not known_baseline_has_additional_context(project,'post')
    project.execute('INSERT INTO assets VALUES (?,?,?,?,?,?)',('new','unused','image/png',100,20,20))
    project.execute('INSERT INTO source_assets VALUES (?,?,?,?)',('post','new',0,'https://i.redd.it/new'))
    assert known_baseline_has_additional_context(project,'post')


def test_obsolete_verification_wait_is_retired_without_model_request(project):
    from newbing.verification import verify_queue
    seed_exportable(project)
    old=project.enqueue('verify',{'conversation_id':'original','conversation_hash':'old','contract_hash':'old','evidence_hash':'old'})
    project.execute("UPDATE tasks SET status='retry_wait',lease_until=9999999999 WHERE id=?",(old,))
    report=verify_queue(project,10)
    assert project.rows('SELECT status FROM tasks WHERE id=?',(old,))[0]['status']=='superseded_input'
    assert report['queued']==1
    assert not project.rows('SELECT * FROM model_attempts')
