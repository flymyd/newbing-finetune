import io
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

from newbing.core import Project,ModelConcurrencyBusy,atomic
from newbing.exporter import grouping
from newbing.llm import ModelClient
from newbing.model_tasks import normalize_evidence
from newbing.network import retry_delay,Fetcher,FetchError
from newbing.reddit import interval_pages,flatten_comments


def test_interval_split_preserves_same_second_posts():
    records=[{'id':str(i),'created_utc':t} for i,t in enumerate([1,1,2,5,7,8])]
    def fake(lo,hi,params):return [r for r in records if lo<=r['created_utc']<hi][:2],{'range':[lo,hi]}
    pages=list(interval_pages(fake,0,10,{},limit=2))
    assert {r['id'] for rows,_,_ in pages for r in rows}=={r['id'] for r in records}
    assert any(saturated for _,_,saturated in pages)  # exact saturation is honestly reported


def test_comment_tree_preserves_parent_and_collapsed_nodes():
    tree=[{'kind':'t1','data':{'id':'parent','replies':{'kind':'Listing','data':{'children':[
        {'kind':'t1','data':{'id':'child','parent_id':'t1_parent','replies':''}},
        {'kind':'more','data':{'children':['missing']}}]}}}}]
    rows=list(flatten_comments(tree))
    assert rows[0][1]['id']=='parent' and rows[1][1]['id']=='child'
    assert rows[2][0]=='more'


def test_retry_after_and_rate_reset():
    assert retry_delay(httpx.Headers({'Retry-After':'42','X-RateLimit-Reset':'50'}),2)==50


def test_429_sets_persistent_host_cooldown(project):
    client=httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(429,headers={'Retry-After':'300'},json={'error':'slow down'})))
    f=Fetcher(project,client)
    with pytest.raises(FetchError) as err:f.fetch('https://i.redd.it/a')
    assert err.value.status==429 and err.value.retry_at
    before=len(project.rows('SELECT * FROM http_attempts'))
    with pytest.raises(FetchError,match='cooldown'):f.fetch('https://i.redd.it/b')
    assert len(project.rows('SELECT * FROM http_attempts'))==before


def test_pixel_evidence_is_normalized_without_guessing(tmp_path):
    path=tmp_path/'image.png';Image.new('RGB',(400,300)).save(path)
    result={'conversations':[{'messages':[{'evidence':[{'image_id':'a','region':[20,30,200,150]}]}]}]}
    changes=normalize_evidence(result,[{'image_id':'a','path':str(path)}])
    assert result['conversations'][0]['messages'][0]['evidence'][0]['region']==[0.05,0.1,0.5,0.5]
    assert changes[0]['original_pixel_box']==[20,30,200,150]
    result['conversations'][0]['messages'][0]['evidence'][0]['region']=[0,0,500,500]
    with pytest.raises(ValueError,match='outside'):normalize_evidence(result,[{'image_id':'a','path':str(path)}])


def test_global_model_concurrency_across_connections(project):
    maximum=project.config['models']['profiles']['flymyd']['max_concurrency']
    for n in range(maximum):project.reserve_model(str(n),'test',1)
    other=Project(project.root)
    with pytest.raises(ModelConcurrencyBusy):other.reserve_model('extra','test',1)
    other.db.close()


def test_model_success_cache_and_prompt_invalidation(project):
    client=ModelClient(project);calls=[]
    def create(**body):
        calls.append(body)
        result={'id':'mock','status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':'ok'}]}],
                'usage':{'input_tokens':1,'output_tokens':1,'total_tokens':2}}
        return SimpleNamespace(model_dump=lambda **kw:result)
    client.client.responses.create=create
    assert client.call('test','version one')[1] is False
    assert client.call('test','version one')[1] is True
    assert client.call('test','version two')[1] is False
    assert len(calls)==2
    assert 'api_key' not in json.dumps(project.rows('SELECT * FROM model_attempts'))


def test_same_screenshot_or_exact_transcript_stays_in_one_split_group():
    a={'conversation_id':'a','asset_ids':['img'],'projected':{'conversations':[{'from':'human','value':'first'}]}}
    b={'conversation_id':'b','asset_ids':['img'],'projected':{'conversations':[{'from':'human','value':'different part'}]}}
    c={'conversation_id':'c','asset_ids':[],'projected':a['projected']}
    d={'conversation_id':'d','asset_ids':[],'projected':{'conversations':[{'from':'human','value':'unrelated'}]}}
    groups=grouping([a,b,c,d])
    assert groups['a']==groups['b']==groups['c']
    assert groups['d']!=groups['a']
