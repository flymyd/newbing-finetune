from __future__ import annotations

from datetime import datetime,timedelta,timezone
import html
import json
import re
from urllib.parse import urlencode,urljoin

from .core import atomic,digest,now
from .network import Fetcher,allowed_url
from .sources import reddit_media


def interval_pages(fetch,lo,hi,params,*,limit=100):
    """Split saturated time intervals instead of skipping same-second records at a cursor."""
    rows,receipt=fetch(lo,hi,params)
    if len(rows)>=limit and hi-lo>1:
        middle=(lo+hi)//2
        yield from interval_pages(fetch,lo,middle,params,limit=limit)
        yield from interval_pages(fetch,middle,hi,params,limit=limit)
    else:
        yield [r for r in rows if lo<=r.get('created_utc',lo)<hi],receipt,len(rows)>=limit


def ingest_post(project,post,raw_path,source_name):
    sid='reddit:post:'+post['id']
    canonical='https://www.reddit.com'+post.get('permalink',f"/r/{post.get('subreddit','')}/comments/{post['id']}/")
    images=reddit_media(post)
    body=post.get('selftext') or ''
    linked=[html.unescape(u) for u in re.findall(r'https?://[^\s\)<>\"]+',body)]
    for u in linked:
        if re.search(r'\.(png|jpe?g|gif|webp)(\?|$)',u,re.I) and u not in images:images.append(u)
    payload={'url':canonical,'posted_at':datetime.fromtimestamp(post['created_utc'],timezone.utc).isoformat(),
             'title':post.get('title'),'text':body,'links':linked,'image_urls':images,
             'post':post,'discovery_method':'arctic_interval_enumeration','source_config':source_name}
    project.add_source(sid,'reddit_post',payload,raw_path)
    # Preserve every enumerated post. Candidate selection excludes only evident non-chat groups.
    if images or len(body)>120 or post.get('is_gallery') or ('imgur.com' in post.get('url','')):
        cid='reddit:'+post['id']
        known=bool(project.rows('SELECT 1 FROM candidates WHERE id=?',('baseline:'+cid,)))
        project.add_candidate(cid,sid,{'baseline':False,'known_baseline_post':known,'source_config':source_name})
        if known:
            project.execute('INSERT OR IGNORE INTO relations VALUES (?,?,?,?)',(cid,'baseline:'+cid,'same_original_post',json.dumps({'post_id':post['id']})))
        for i,u in enumerate(images):
            if allowed_url(u,Fetcher(project).domains):
                project.enqueue('download',{'source_id':sid,'ordinal':i,'url':u})
        return True
    return False


def discover_reddit(project,source_name,window='early'):
    cfg=project.source_config(source_name);win=project.policy['windows'][window]
    if window not in cfg['windows']:
        return {'source':source_name,'window':window,'skipped':'outside configured source windows'}
    start=int(datetime.fromisoformat(win['start_date']).replace(tzinfo=timezone.utc).timestamp())
    end=int((datetime.fromisoformat(win['end_date_inclusive'])+timedelta(days=1)).replace(tzinfo=timezone.utc).timestamp())
    fetcher=Fetcher(project);base=project.config['sources']['reddit_archive']['online_api_base_url']
    def fetch(lo,hi,extra):
        params={'subreddit':cfg['subreddit'],'after':lo-1,'before':hi,'limit':100,'sort':'asc',**extra}
        url=base+'/posts/search?'+urlencode(params)
        try:
            path,receipt=fetcher.fetch(url,kind='reddit_index')
        except Exception as exc:
            # The archive uses 422 for server-side query timeouts; narrow the interval,
            # rather than treating an overloaded broad query as a permanent data gap.
            if getattr(exc,'status',None) in (422,502,503,504) and hi-lo>60:
                middle=(lo+hi)//2
                left,left_receipt=fetch(lo,middle,extra)
                right,right_receipt=fetch(middle,hi,extra)
                combined={r['id']:r for r in left+right}
                merged=list(sorted(combined.values(),key=lambda r:r.get('created_utc',0)))
                # Preserve all rows and the raw receipt chain in an immutable local merge.
                out=project.root/'data/raw/reddit/query_merges'/f'{digest([lo,hi,extra,cfg["subreddit"]])}.json'
                atomic(out,{'data':merged,'receipts':[left_receipt,right_receipt]})
                return merged,{'path':str(out.relative_to(project.root)),'url':url,'sha256':digest(out.read_bytes()),'already_partitioned':True}
            raise
        parsed=json.loads(path.read_text())
        if not isinstance(parsed.get('data'),list):raise ValueError('unexpected Arctic response schema')
        return parsed['data'],{'path':str(path.relative_to(project.root)),'url':url,'sha256':receipt['sha256']}
    queries=cfg.get('keywords_any') if 'keyword' in cfg.get('discovery','') else [None]
    counts={'unique_posts':0,'candidate_posts':0,'pages':0,'saturated_seconds':0};seen=set();errors=[]
    for keyword in queries:
        params={'query':keyword} if keyword else {}
        try:
            for rows,receipt,saturated in interval_pages(fetch,start,end,params):
                counts['pages']+=1;counts['saturated_seconds']+=int(saturated)
                for post in rows:
                    if post['id'] in seen:continue
                    seen.add(post['id']);counts['unique_posts']+=1
                    counts['candidate_posts']+=int(ingest_post(project,post,project.root/receipt['path'],source_name))
                atomic(project.root/'reports/discovery'/f'{source_name}-{window}.json',{'counts':counts,'errors':errors,'last_receipt':receipt,'keyword':keyword})
                if counts['pages']%5==0:print(json.dumps({'source':source_name,'window':window,**counts}),flush=True)
            query_record={'source':source_name,'window':window,'keyword':keyword,'completed_at':now(),
                          'coverage':'complete_for_available_listing_query','counts_so_far':dict(counts)}
            atomic(project.root/'reports/discovery_queries'/f'{digest([source_name,window,keyword])}.json',query_record)
        except Exception as exc:
            errors.append({'keyword':keyword,'error':str(exc)[:800]})
            # A provider-wide 403 is not cured by repeatedly substituting the query string.
            if getattr(exc,'status',None) in (401,403,429):break
    state='partial' if errors or counts['saturated_seconds'] else 'complete_for_available_listing'
    project.source_state(source_name+':'+window,state,**counts,errors=errors,method='arctic_interval_enumeration')
    return {'source':source_name,'window':window,'coverage':state,**counts,'errors':errors}


def ingest_dump(project,path,source_name,window):
    import io
    import zstandard
    cfg=project.source_config(source_name);win=project.policy['windows'][window]
    lo=int(datetime.fromisoformat(win['start_date']).replace(tzinfo=timezone.utc).timestamp())
    hi=int((datetime.fromisoformat(win['end_date_inclusive'])+timedelta(days=1)).replace(tzinfo=timezone.utc).timestamp())
    count=0
    with open(path,'rb') as raw:
        reader=zstandard.ZstdDecompressor().stream_reader(raw) if str(path).endswith('.zst') else raw
        for line in io.TextIOWrapper(reader,encoding='utf-8'):
            post=json.loads(line)
            if post.get('subreddit','').lower()!=cfg['subreddit'].lower() or not lo<=post.get('created_utc',0)<hi:continue
            # Save only matching records into immutable project evidence; don't point to an external mutable file.
            out=project.root/'data/raw/reddit/dump'/f"{post['id']}.json"
            atomic(out,post)
            ingest_post(project,post,out,source_name);count+=1
    return {'ingested':count,'window':window}


def flatten_comments(nodes):
    if isinstance(nodes,dict):
        nodes=(nodes.get('data') or {}).get('children',[]) if nodes.get('kind')=='Listing' else [nodes]
    for node in nodes or []:
        if node.get('kind')=='more':
            yield 'more',node
        elif node.get('kind')=='t1':
            data=node['data'];yield 'comment',data
            if isinstance(data.get('replies'),dict):
                yield from flatten_comments(data['replies'])


def discover_comments(project,limit=100):
    posts=project.rows("""SELECT s.id,s.payload FROM sources s JOIN candidates c ON c.source_id=s.id
       WHERE s.kind='reddit_post' ORDER BY json_extract(s.payload,'$.posted_at'),s.id""")
    for post in posts:
        project.enqueue('comments',{'source_id':post['id']})
    jobs=project.rows("SELECT * FROM tasks WHERE kind='comments' AND status IN ('pending','retry_wait') AND (lease_until IS NULL OR lease_until<?) ORDER BY rowid LIMIT ?",(__import__('time').time(),limit))
    counts={'posts':0,'comments':0,'text_or_media_candidates':0,'collapsed_groups':0,'errors':0}
    fetcher=Fetcher(project);base=project.config['sources']['reddit_archive']['online_api_base_url']
    for job in jobs:
        if not project.claim_task(job['id'],900):continue
        sid=json.loads(job['payload'])['source_id'];post=json.loads(project.rows('SELECT payload FROM sources WHERE id=?',(sid,))[0]['payload'])
        native=post['post']['id']
        try:
            path,receipt=fetcher.fetch(base+'/comments/tree?'+urlencode({'link_id':native,'limit':9999,'start_breadth':9999,'start_depth':100}),kind='reddit_comments')
            tree=json.loads(path.read_text())
            local_count=0;collapsed=0
            for kind,data in flatten_comments(tree['data']):
                if kind=='more':collapsed+=1;continue
                csid='reddit:comment:'+data['id'];body=data.get('body') or ''
                links=[html.unescape(u) for u in re.findall(r'https?://[^\s\)<>\"]+',body)]
                images=[u for u in links if re.search(r'\.(png|jpe?g|gif|webp)(\?|$)',u,re.I)]
                for media in (data.get('media_metadata') or {}).values():
                    u=(media.get('s') or {}).get('u')
                    if u:images.append(html.unescape(u))
                images=list(dict.fromkeys(images))
                payload={'url':'https://www.reddit.com'+data.get('permalink',''),
                         'posted_at':datetime.fromtimestamp(data['created_utc'],timezone.utc).isoformat(),
                         'text':body,'links':links,'parent_id':data.get('parent_id'),'post_source_id':sid,
                         'image_urls':images,'comment':data,'discovery_method':'comment_tree'}
                project.add_source(csid,'reddit_comment',payload,path);local_count+=1
                role_marker=bool(re.search(r'(?im)(?:^|\n)\s*(?:>|\*\*)?\s*(?:human|user|assistant|bing|sydney|me|you|我|必应)\s*[:：>]',body))
                if data.get('author') not in ('AutoModerator',) and (images or (len(body)>300 and role_marker)):
                    project.add_candidate(csid,csid,{'baseline':False,'source_config':'reddit_comments','parent_source_id':sid})
                    for i,u in enumerate(images):
                        if allowed_url(u,fetcher.domains):project.enqueue('download',{'source_id':csid,'ordinal':i,'url':u})
                    counts['text_or_media_candidates']+=1
            counts['posts']+=1;counts['comments']+=local_count;counts['collapsed_groups']+=collapsed
            project.execute("UPDATE tasks SET status='succeeded',output_ref=?,lease_until=NULL,error=NULL,updated=? WHERE id=?",(str(path.relative_to(project.root)),now(),job['id']))
        except Exception as exc:
            counts['errors']+=1
            state='retry_wait' if getattr(exc,'status',None) in (422,429,500,502,503,504) else 'failed_terminal'
            retry_at=getattr(exc,'retry_at',None) or (__import__('time').time()+300 if state=='retry_wait' else None)
            project.execute('UPDATE tasks SET status=?,lease_until=?,error=?,updated=? WHERE id=?',(state,retry_at,str(exc)[:600],now(),job['id']))
            if getattr(exc,'status',None)==429:break
        if counts['posts']%10==0:print(json.dumps({'comment_collection':counts}),flush=True)
    return counts


def recover_baseline_posts(project):
    """Resolve original Reddit records for baseline IDs; recover dates and missing context links."""
    needed=[]
    for row in project.rows("SELECT DISTINCT json_extract(payload,'$.upstream_id') id FROM sources WHERE kind='baseline'"):
        if row['id'].startswith('reddit:'):
            native=row['id'].split(':',1)[1]
            if not project.rows('SELECT 1 FROM sources WHERE id=?',('reddit:post:'+native,)):needed.append(native)
    base=project.config['sources']['reddit_archive']['online_api_base_url'];fetcher=Fetcher(project)
    counts={'requested_missing_ids':len(needed),'resolved':0,'unresolved':[],'errors':[]}
    for start in range(0,len(needed),100):
        batch=needed[start:start+100]
        try:
            path,_=fetcher.fetch(base+'/posts/ids?'+urlencode({'ids':','.join(batch)}),kind='reddit_baseline_recovery')
            rows=json.loads(path.read_text())['data'];resolved=set()
            for post in rows:
                ingest_post(project,post,path,'baseline_post_recovery');resolved.add(post['id'])
            counts['resolved']+=len(resolved);counts['unresolved']+=list(set(batch)-resolved)
        except Exception as exc:
            counts['errors'].append({'ids':batch,'error':str(exc)[:500]})
            if getattr(exc,'status',None)==429:break
        atomic(project.root/'reports/baseline-original-post-recovery.json',counts)
        print(json.dumps({'baseline_post_recovery':counts['resolved'],'requested':len(needed)}),flush=True)
    return counts
