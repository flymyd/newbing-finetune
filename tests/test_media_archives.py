import json
import httpx
from newbing.core import atomic
from newbing.media_recovery import recover_archive_media,imgur_album_images


def test_embedded_album_json_preserves_order_without_executing_script():
    body='window.bad = () => steal(); image: '+json.dumps({'album_images':{'count':2,'images':[{'hash':'B','ext':'.png'},{'hash':'A','ext':'.jpg'}]}})
    images,meta=imgur_album_images(body)
    assert images==['https://i.imgur.com/B.png','https://i.imgur.com/A.jpg']


def test_specific_archive_recovery_queues_once_and_counts_alternative_cap(project,monkeypatch):
    raw=project.root/'data/raw/cdx.json'
    atomic(raw,[['timestamp','original','statuscode','mimetype'],['20230215000000','https://i.redd.it/dead.png','200','image/png']])
    calls=[]
    def fake(self,url,**kwargs):
        calls.append(url);return raw,{}
    monkeypatch.setattr('newbing.media_recovery.Fetcher.fetch',fake)
    task=project.enqueue('download',{'source_id':'s','ordinal':0,'url':'https://i.redd.it/dead.png'})
    project.execute("UPDATE tasks SET status='failed_terminal' WHERE id=?",(task,))
    assert recover_archive_media(project,10)['captures_queued']==1
    assert recover_archive_media(project,10)['attempted_urls']==0
    assert len(calls)==1
    rows=project.rows("SELECT payload FROM tasks WHERE json_extract(payload,'$.recovery_method') IS NOT NULL")
    assert len(rows)==1
    job=json.loads(rows[0]['payload'])
    assert job['original_url']=='https://i.redd.it/dead.png' and '20230215000000id_' in job['url']


def test_terminal_url_is_not_retried_by_another_task(project):
    from newbing.network import Fetcher,FetchError
    import pytest
    calls=[]
    def handler(request):calls.append(request);return httpx.Response(404)
    client=httpx.Client(transport=httpx.MockTransport(handler))
    for _ in range(2):
        with pytest.raises(FetchError):Fetcher(project,client).fetch('https://i.redd.it/gone.png')
    assert len(calls)==1


def test_sharegpt_rejects_nontext_and_bad_role_sequences():
    from newbing.cleaning import validate_sharegpt
    import copy
    valid={'id':'x','conversations':[{'from':r,'value':v} for r,v in [('human','first'),('gpt','answer'),('human','next'),('gpt','final')]]}
    variants=[]
    for value in ('', '   ', {}, [{'type':'image','image_url':'x'}],None):
        bad=copy.deepcopy(valid);bad['conversations'][1]['value']=value;variants.append(bad)
    for role in ('assistant','system','tool','unknown'):
        bad=copy.deepcopy(valid);bad['conversations'][1]['from']=role;variants.append(bad)
    bad=copy.deepcopy(valid);bad['conversations'][0]['from']='gpt';variants.append(bad)
    bad=copy.deepcopy(valid);bad['conversations'][-1]['from']='human';variants.append(bad)
    bad=copy.deepcopy(valid);bad['conversations'][1]['images']=['asset.png'];variants.append(bad)
    assert all(validate_sharegpt(bad) for bad in variants)


def test_image_with_valid_header_but_broken_pixels_is_not_registered(project):
    from PIL import Image
    import io,pytest
    from newbing.network import Fetcher
    image=Image.new('RGB',(50,50),'red');buffer=io.BytesIO();image.save(buffer,format='JPEG')
    broken=buffer.getvalue()[:-20]
    with Image.open(io.BytesIO(broken)) as header:header.verify()
    client=httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(200,content=broken)))
    with pytest.raises(OSError):Fetcher(project,client).asset('s',0,'https://i.redd.it/broken.jpg')
    assert not project.rows('SELECT * FROM assets')


def test_author_archive_keeps_text_only_transcript_candidates(project,monkeypatch):
    from newbing.sources import discover_author
    raw=project.root/'data/raw/author.html'
    long_quote='User: Tell me about Sydney.\nBing: '+('Visible historical text. '*16)+'\nUser: More?\nBing: Another visible answer.'
    raw.parent.mkdir(parents=True,exist_ok=True)
    raw.write_text('<div class="tweet"><a href="https://twitter.com/repligate/status/1625520707768659968">post</a><pre>'+long_quote+'</pre></div>')
    monkeypatch.setattr('newbing.sources.Fetcher.fetch',lambda *a,**kw:(raw,{'bytes':raw.stat().st_size}))
    result=discover_author(project)
    assert result['images']==0
    candidate=project.rows('SELECT * FROM candidates')
    assert len(candidate)==1
    source=json.loads(project.rows('SELECT payload FROM sources')[0]['payload'])
    assert long_quote in source['text'] and source['image_urls']==[]


def test_repeated_host_rate_limit_defers_new_url_without_network(project):
    from newbing.network import Fetcher,SourceUnavailable
    from newbing.core import now
    import pytest
    threshold=project.policy['retry']['max_host_consecutive_rate_limits']
    for n in range(threshold):
        project.execute('INSERT INTO http_attempts(url,kind,status,started,finished,epoch,host) VALUES (?,?,?,?,?,?,?)',
                        (f'https://i.imgur.com/{n}','image',429,now(),now(),project.policy['budgets']['epoch'],'i.imgur.com'))
    calls=[]
    client=httpx.Client(transport=httpx.MockTransport(lambda request:calls.append(request)))
    with pytest.raises(SourceUnavailable):Fetcher(project,client).fetch('https://i.imgur.com/new.png')
    assert not calls
    assert project.rows("SELECT state FROM source_status WHERE id='host_access:i.imgur.com'")[0]['state']=='rate_limited_for_current_pass'
