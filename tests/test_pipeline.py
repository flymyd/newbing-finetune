import io
import json
import time

import httpx
import pytest
from PIL import Image
from jsonschema import Draft202012Validator

from newbing.cleaning import project_messages,validate_sharegpt
from newbing.core import atomic,BudgetExceeded
from newbing.llm import ModelResultError,output_text
from newbing.network import Fetcher,FetchError,allowed_url,run_downloads
from newbing.sources import reddit_media,twitter_date


def test_gallery_order_and_html_entities():
    post={'url':'https://i.redd.it/wrong.jpg','gallery_data':{'items':[{'media_id':'b'},{'media_id':'a'}]},
          'media_metadata':{'a':{'s':{'u':'https://i.redd.it/a.jpg?x=1&amp;y=2'}},'b':{'s':{'u':'https://i.redd.it/b.jpg'}}}}
    assert reddit_media(post)==['https://i.redd.it/b.jpg','https://i.redd.it/a.jpg?x=1&y=2']


def test_url_subdomain_boundary_and_credentials():
    assert allowed_url('https://preview.redd.it/a',['redd.it'])
    assert not allowed_url('https://redd.it.evil.example/a',['redd.it'])
    assert not allowed_url('https://x:y@i.redd.it/a',['redd.it'])
    assert not allowed_url('file:///etc/passwd',['redd.it'])


def test_source_request_budget_is_persistent(project):
    project.policy['budgets']['max_source_http_attempts']=1
    project.reserve_http('https://i.redd.it/a','image',10)
    with pytest.raises(BudgetExceeded):project.reserve_http('https://i.redd.it/b','image',10)


def test_model_reservation_includes_inflight(project):
    project.policy['budgets']['max_reported_total_tokens']=100
    project.reserve_model('a','test',60)
    with pytest.raises(BudgetExceeded):project.reserve_model('b','test',50)


def test_task_idempotence_and_lease_recovery(project):
    a=project.enqueue('download',{'url':'u'})
    assert a==project.enqueue('download',{'url':'u'})
    project.execute("UPDATE tasks SET status='leased',lease_until=? WHERE id=?",(time.time()-1,a))
    assert project.recover()==1
    assert project.recover()==0
    assert len(project.rows('SELECT * FROM tasks'))==1


def test_download_png_end_to_end_and_cached(project):
    image=Image.new('RGB',(20,10),'white');buffer=io.BytesIO();image.save(buffer,format='PNG')
    calls=[]
    def handler(request):
        calls.append(request)
        assert 'authorization' not in request.headers
        return httpx.Response(200,headers={'content-type':'image/png'},content=buffer.getvalue())
    f=Fetcher(project,httpx.Client(transport=httpx.MockTransport(handler)))
    first=f.asset('source',0,'https://i.redd.it/test.png')
    assert first==f.asset('source',0,'https://i.redd.it/test.png')
    assert len(calls)==1
    assert project.rows('SELECT * FROM assets')[0]['width']==20
    assert project.rows('SELECT * FROM http_attempts')[0]['reserved']==0


def test_bad_image_not_registered(project):
    f=Fetcher(project,httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,content=b'<html>denied</html>'))))
    with pytest.raises(Exception):f.asset('s',0,'https://i.redd.it/not.png')
    assert not project.rows('SELECT * FROM assets')


def test_redirect_to_unlisted_host_not_followed(project):
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(302,headers={'location':'https://unlisted.invalid/a'})
    f=Fetcher(project,httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(FetchError,match='redirect domain'):f.fetch('https://i.redd.it/a')
    assert len(calls)==1


@pytest.mark.parametrize('status',[401,403,404,410])
def test_terminal_http_not_retried(project,status):
    calls=[]
    def handler(request):
        calls.append(request);return httpx.Response(status)
    with pytest.raises(FetchError):Fetcher(project,httpx.Client(transport=httpx.MockTransport(handler))).fetch('https://i.redd.it/a')
    assert len(calls)==1


def test_download_dryrun_no_http(project):
    project.enqueue('download',{'source_id':'s','ordinal':0,'url':'https://i.redd.it/a'})
    assert run_downloads(project,10,dry_run=True)['queued']==1
    assert not project.rows('SELECT * FROM http_attempts')


def test_response_output_items_and_refusal():
    response={'status':'completed','output':[{'type':'reasoning'},{'type':'message','content':[{'type':'output_text','text':'a'}]}, {'type':'message','content':[{'type':'output_text','text':'b'}]}]}
    assert output_text(response)=='a\nb'
    with pytest.raises(ModelResultError,match='refusal'):
        output_text({'status':'completed','output':[{'content':[{'type':'refusal','refusal':'no'}]}]})
    with pytest.raises(ModelResultError,match='incomplete'):
        output_text({'status':'incomplete','output':[]})


def valid_sample():
    return {'id':'s','conversations':[{'from':r,'value':v} for r,v in [('human','hi'),('gpt','hello 😊'),('human','more'),('gpt','```\na<image>\n```')]]}


def test_sharegpt_semantics_and_literal_code(project):
    good=valid_sample()
    assert not validate_sharegpt(good)
    schema=json.loads((project.root/'schemas/sharegpt.sample.schema.json').read_text())
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(good)
    for bad in [dict(good,system='invented'),dict(good,conversations=good['conversations'][:2]),
                dict(good,conversations=good['conversations'][:-1])]:
        assert validate_sharegpt(bad)
    good['conversations'][2]={'from':'gpt','value':'wrong role'}
    assert validate_sharegpt(good)


def test_ui_does_not_create_user_and_missing_prompt_not_invented():
    raw=[{'role':'assistant','content':[{'type':'suggestions','items':['not sent']}]},
         {'role':'assistant','content':[{'type':'text','text':'Visible answer 😊'}]}]
    turns,transforms,issues=project_messages(raw)
    assert len(turns)==1 and turns[0]['from']=='gpt'
    assert turns[0]['value']=='Visible answer 😊'
    assert validate_sharegpt({'id':'x','conversations':[{k:v for k,v in t.items() if k!='source_message_index'} for t in turns]})


def test_visual_gap_is_flagged_not_silently_removed():
    raw=[{'role':'user','content':[{'type':'image','source':'upload'}]},
         {'role':'assistant','content':[{'type':'text','text':'I see a cat.'}]}]
    turns,transforms,issues=project_messages(raw)
    assert any(i['reason']=='empty_textual_turn_or_media_gap' for i in issues)
    assert not any(t['from']=='human' for t in turns)


def test_truncated_code_and_unreadable_flagged():
    turns,_,issues=project_messages([{'role':'assistant','meta':{'truncated':'screenshot'},'content':[{'type':'code_block','text':'x=1\n  y=2'},{'type':'text','text':'⟦unreadable⟧'}]}])
    assert 'x=1\n  y=2' in turns[0]['value']
    assert {i['reason'] for i in issues}=={'message_truncated','unreadable_text'}


def test_twitter_date_not_model_self_claim():
    assert twitter_date('1625520707768659968').startswith('2023-02-14')
