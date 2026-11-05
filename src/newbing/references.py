"""Bounded public reference traversal; link discovery never implies chat authenticity."""
from __future__ import annotations
import json
import re
import time
from urllib.parse import urljoin,urlparse,urldefrag,urlencode
from bs4 import BeautifulSoup
from .core import atomic,digest,now,BudgetExceeded
from .network import Fetcher,allowed_url


def reference_links(body,url,domains,limit):
    soup=BeautifulSoup(body,'html.parser')
    content=soup.select_one('article,main,.markdown-body,.post-body') or soup
    found=[]
    for anchor in content.select('a[href]'):
        link=urldefrag(urljoin(url,anchor['href']))[0]
        if not allowed_url(link,domains) or link==url:continue
        text=anchor.get_text(' ',strip=True)
        if not re.search(r'bing|sydney|悉尼|必应|/status/\d+|reddit\.com/r/[^/]+/comments/',link+' '+text,re.I):continue
        if re.search(r'/(login|signup|share|intent|settings|search)(?:[/?#]|$)',urlparse(link).path):continue
        if link not in found:found.append(link)
        if len(found)>=limit:break
    return found


def queue_references(project):
    cfg=project.config['sources']['discovery'];queued=0
    for row in project.rows("SELECT * FROM sources WHERE kind IN ('web_page','reference_page','author_archive') ORDER BY created"):
        source=json.loads(row['payload']);depth=source.get('reference_depth',0)
        if depth>=cfg['max_reference_depth']:continue
        if row['kind']=='author_archive':
            if not (source.get('posted_at') or '').startswith('2023-02'):continue
            links=[urljoin(source.get('archive_url',''),u) for u in source.get('links',[])]
            links=[u for u in links if re.search(r'bing|sydney|reddit\.com/r/.+/comments/',u,re.I)]
        else:
            raw=project.root/row['raw_path']
            links=reference_links(raw.read_text(errors='replace'),source['url'],cfg['allowed_page_domains'],cfg['max_reference_links_per_page'])
        for link in links[:cfg['max_reference_links_per_page']]:
            if not allowed_url(link,cfg['allowed_page_domains']):continue
            if project.rows("SELECT 1 FROM sources WHERE json_extract(payload,'$.url')=? LIMIT 1",(link,)):continue
            payload={'url':link,'parent_source_id':row['id'],'depth':depth+1}
            # One URL job, while retaining every discovery edge separately.
            project.execute('INSERT OR IGNORE INTO relations VALUES (?,?,?,?)',(row['id'],'reference:'+digest(link),'discovery_reference',json.dumps(payload)))
            if project.rows("SELECT 1 FROM tasks WHERE kind='reference' AND json_extract(payload,'$.url')=? LIMIT 1",(link,)):continue
            project.enqueue('reference',payload);queued+=1
    return queued


def fetch_reference(project,payload):
    f=Fetcher(project);url=payload['url'];sid='reference:'+digest(url)
    post=re.search(r'reddit\.com/r/[^/]+/comments/([A-Za-z0-9]+)',url)
    if post:
        from .reddit import ingest_post
        native=post[1]
        if project.rows('SELECT 1 FROM sources WHERE id=?',('reddit:post:'+native,)):return {'existing_post':native}
        base=project.config['sources']['reddit_archive']['online_api_base_url']
        path,_=f.fetch(base+'/posts/ids?'+urlencode({'ids':native}),kind='reference_post_id')
        records=json.loads(path.read_text()).get('data',[])
        for record in records:ingest_post(project,record,path,'reference_link')
        return {'post_records':len(records)}
    path,receipt=f.fetch(url,kind='reference_page')
    soup=BeautifulSoup(path.read_text(errors='replace'),'html.parser')
    date=None
    for node in soup.select('meta[property="article:published_time"],meta[name="date"],time[datetime]'):
        value=node.get('content') or node.get('datetime') or ''
        if re.match(r'2023-\d\d-\d\d',value):date=value;break
    article=soup.select_one('article,main,.markdown-body,.post-body') or soup
    for node in article.select('script,style,nav,header,footer'):node.decompose()
    text=article.get_text('\n',strip=True)
    links=[urljoin(url,a['href']) for a in article.select('a[href]')]
    images=list(dict.fromkeys(urljoin(url,img.get('data-src') or img.get('src','')) for img in article.select('img[src],img[data-src]')))
    images=[u for u in images if allowed_url(u,f.domains)]
    source={'url':url,'text':text,'links':links,'image_urls':images,'posted_at':date,
            'date_evidence':[{'type':'page_publication_metadata','value':date}] if date else [],
            'discovery_method':'reference_link','discovery_parent':payload['parent_source_id'],'reference_depth':payload['depth']}
    project.add_source(sid,'reference_page',source,path)
    if len(text)>300 or images:
        project.add_candidate(sid,sid,{'baseline':False,'source_config':'reference_link'})
        for i,u in enumerate(images):project.enqueue('download',{'source_id':sid,'ordinal':i,'url':u})
    return {'source_id':sid,'characters':len(text),'images':len(images),'date':date}


def discover_references(project,limit=100):
    queued=queue_references(project);counts={};jobs=project.rows("SELECT * FROM tasks WHERE kind='reference' AND status IN ('pending','retry_wait') AND (lease_until IS NULL OR lease_until<?) ORDER BY rowid LIMIT ?",(time.time(),limit))
    for job in jobs:
        if not project.claim_task(job['id'],600):continue
        try:
            result=fetch_reference(project,json.loads(job['payload']))
            out=project.root/'data/processed/references'/f"{job['id']}.json";atomic(out,result)
            project.execute("UPDATE tasks SET status='succeeded',output_ref=?,lease_until=NULL,error=NULL,updated=? WHERE id=?",(str(out.relative_to(project.root)),now(),job['id']))
            state='succeeded'
        except Exception as exc:
            state='deferred_resource' if isinstance(exc,BudgetExceeded) else 'retry_wait' if getattr(exc,'status',None)==429 else 'failed_terminal'
            retry_at=getattr(exc,'retry_at',None) or (time.time()+300 if state=='retry_wait' else None)
            project.execute('UPDATE tasks SET status=?,lease_until=?,error=?,updated=? WHERE id=?',(state,retry_at,str(exc)[:500],now(),job['id']))
        counts[state]=counts.get(state,0)+1
        atomic(project.root/'reports/reference-discovery.json',{'queued_new':queued,'processed':counts,'at':now()})
        if sum(counts.values())%10==0:print(json.dumps(counts),flush=True)
    return {'queued_new':queued,'processed':counts}
