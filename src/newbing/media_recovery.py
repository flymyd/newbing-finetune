from __future__ import annotations

from collections import Counter
import html
import json
import re
from urllib.parse import urlparse

from .core import atomic,digest,now
from .network import Fetcher,allowed_url


def imgur_album_images(body):
    """Read the site's embedded JSON as data; never execute page JavaScript."""
    decoder=json.JSONDecoder()
    for match in re.finditer(r'\bimage\s*:\s*(\{)',body):
        try:
            data,end=decoder.raw_decode(body[match.start(1):])
        except json.JSONDecodeError:continue
        album=data.get('album_images') or {}
        if not isinstance(album.get('images'),list):continue
        images=[]
        for item in album['images']:
            if not re.fullmatch(r'[A-Za-z0-9]+',item.get('hash','')):continue
            ext=item.get('ext','')
            if ext.lower() not in ('.jpg','.jpeg','.png','.gif','.webp'):continue
            images.append('https://i.imgur.com/'+item['hash']+ext)
        if images:return images,{'title':data.get('title'),'datetime':data.get('datetime'),'declared_count':album.get('count')}
    return [],{}


def recover_external_media(project,limit=100):
    fetcher=Fetcher(project);albums={}
    for row in project.rows("SELECT id,payload FROM sources WHERE kind IN ('reddit_post','reddit_comment','web_page')"):
        payload=json.loads(row['payload']);links=list(payload.get('links') or [])
        if payload.get('post',{}).get('url'):links.append(payload['post']['url'])
        for url in links:
            match=re.search(r'^https?://(?:www\.)?imgur\.com/(?:a|gallery)/([A-Za-z0-9]+)',url)
            if match:albums.setdefault(match.group(1),set()).add(row['id'])
    counts=Counter()
    for album_id,source_ids in list(albums.items())[:limit]:
        url=f'https://imgur.com/a/{album_id}/layout/blog'
        try:
            path,receipt=fetcher.fetch(url,kind='imgur_album')
            images,meta=imgur_album_images(path.read_text())
            if not images:
                counts['no_embedded_original_list']+=1
                project.source_state('imgur_album:'+album_id,'no_original_list',path=str(path.relative_to(project.root)))
                continue
            for sid in source_ids:
                # Albums form a distinct ordered group; related parent post remains separately recorded.
                asid='imgur:album:'+album_id
                payload={'url':f'https://imgur.com/a/{album_id}','archive_layout_url':url,'image_urls':images,
                         'posted_at':(meta.get('datetime') or '').replace(' ','T')+'Z' if meta.get('datetime') else None,
                         'text':meta.get('title') or '', 'parent_source_ids':sorted(source_ids),'discovery_method':'original_post_external_album',
                         'album_metadata':meta}
                project.add_source(asid,'external_album',payload,path)
                project.add_candidate(asid,asid,{'baseline':False,'source_config':'imgur_album','parent_source_id':sid})
                project.execute('INSERT OR IGNORE INTO relations VALUES (?,?,?,?)',(asid,sid.replace('reddit:post:','reddit:'),'same_original_post',json.dumps({'method':'post_linked_album'})))
            for index,image in enumerate(images):project.enqueue('download',{'source_id':asid,'ordinal':index,'url':image})
            counts['albums']+=1;counts['images_queued']+=len(images)
            project.source_state('imgur_album:'+album_id,'media_queued',images=len(images),path=str(path.relative_to(project.root)))
        except Exception as exc:
            counts['unavailable']+=1;project.source_state('imgur_album:'+album_id,'unavailable',error=str(exc)[:500])
            if getattr(exc,'status',None)==429:break
    report={'discovered_albums':len(albums),**counts,'created_at':now()}
    atomic(project.root/'reports/external-album-recovery.json',report)
    return report


def queue_preview_fallbacks(project,limit=500):
    tasks=project.rows("SELECT payload,error FROM tasks WHERE kind='download' AND status='failed_terminal' ORDER BY rowid LIMIT ?",(limit,))
    queued=0
    for task in tasks:
        job=json.loads(task['payload']);sources=project.rows('SELECT payload FROM sources WHERE id=?',(job['source_id'],))
        if job.get('recovery_method'):continue  # Never recursively recover a failed fallback URL.
        if not sources:continue
        source=json.loads(sources[0]['payload']);post=source.get('post') or {}
        alternatives=[];ordinal=job['ordinal']
        galleries=(post.get('gallery_data') or {}).get('items') or []
        for gallery_index,gallery in enumerate(galleries):
            original=((post.get('media_metadata') or {}).get(gallery.get('media_id'),{}).get('s') or {})
            if html.unescape(original.get('u') or original.get('gif') or '')==job['url']:
                ordinal=gallery_index;break
        if ordinal<len(galleries):
            metadata=(post.get('media_metadata') or {}).get(galleries[ordinal]['media_id']) or {}
            previews=metadata.get('p') or []
            alternatives += [html.unescape(p['u']) for p in sorted(previews,key=lambda p:p.get('x',0)*p.get('y',0),reverse=True) if p.get('u')]
        previews=(post.get('preview') or {}).get('images') or []
        if ordinal<len(previews):
            metadata=previews[ordinal]
            if (metadata.get('source') or {}).get('url'):alternatives.append(html.unescape(metadata['source']['url']))
            alternatives += [html.unescape(p['url']) for p in reversed(metadata.get('resolutions') or []) if p.get('url')]
        alternatives=[u for u in dict.fromkeys(alternatives) if u!=job['url'] and allowed_url(u,fetcher_domains(project))]
        for url in alternatives[:project.policy['retry']['max_alternative_recovery_paths']]:
            payload={'source_id':job['source_id'],'ordinal':ordinal,'url':url,
                'original_url':job['url'],'recovery_method':'original_metadata_preview_fallback'}
            exists=project.rows('SELECT 1 FROM tasks WHERE id=?',(digest(['download',payload]),))
            project.enqueue('download',payload)
            queued+=int(not exists)
    return {'preview_fallback_tasks_queued':queued}


def fetcher_domains(project):
    d=project.config['sources']['discovery']
    return d['allowed_page_domains']+d['allowed_media_domains']


def recover_archive_media(project,limit=100):
    """Try public captures for concrete failed URLs, within the same finite alternative cap."""
    from urllib.parse import urlencode
    fetcher=Fetcher(project);counts=Counter();attempted=0
    jobs=project.rows("SELECT payload FROM tasks WHERE kind='download' AND status='failed_terminal' ORDER BY rowid")
    for row in jobs:
        job=json.loads(row['payload'])
        if job.get('recovery_method') or job.get('verify_upstream_bytes'):continue
        key='wayback:'+digest(job['url'])
        if project.rows('SELECT 1 FROM source_status WHERE id=?',(key,)):continue
        alternatives=project.rows("SELECT count(*) n FROM tasks WHERE kind='download' AND json_extract(payload,'$.source_id')=? AND json_extract(payload,'$.ordinal')=? AND json_extract(payload,'$.recovery_method') IS NOT NULL",(job['source_id'],job['ordinal']))[0]['n']
        remaining=project.policy['retry']['max_alternative_recovery_paths']-alternatives
        if remaining<=0:continue
        if attempted>=limit:break
        attempted+=1
        url='https://web.archive.org/cdx/search/cdx?'+urlencode({'url':job['url'],'output':'json','filter':['statuscode:200','mimetype:image/.*'],'collapse':'digest','limit':remaining},doseq=True)
        try:
            path,receipt=fetcher.fetch(url,kind='specific_media_archive_lookup',max_bytes=1048576)
            records=json.loads(path.read_text());headers=records[0] if records else [];captures=[]
            for record in records[1:]:
                data=dict(zip(headers,record));timestamp=data.get('timestamp','')
                if not re.fullmatch(r'\d{14}',timestamp):continue
                original=data.get('original',job['url'])
                if not allowed_url(original,fetcher.domains):continue
                capture='https://web.archive.org/web/'+timestamp+'id_/'+original
                payload={'source_id':job['source_id'],'ordinal':job['ordinal'],'url':capture,
                         'original_url':job['url'],'recovery_method':'public_archive_specific_url'}
                project.enqueue('download',payload);captures.append(capture)
            project.source_state(key,'captures_queued' if captures else 'no_capture',original_url=job['url'],captures=captures,receipt=str(path.relative_to(project.root)))
            counts['captures_queued']+=len(captures);counts['urls_checked']+=1
        except Exception as exc:
            project.source_state(key,'unavailable',original_url=job['url'],error=str(exc)[:500])
            counts['unavailable']+=1
            if getattr(exc,'status',None) in (401,403,429):break
        atomic(project.root/'reports/specific-media-archive-recovery.json',{'attempted_urls':attempted,**counts,'at':now()})
    return {'attempted_urls':attempted,**counts}
