from __future__ import annotations

import html
import json
import re
from pathlib import Path
from urllib.parse import quote, urljoin

from bs4 import BeautifulSoup

from .core import Project, atomic, digest, now
from .network import Fetcher, allowed_url


def import_baseline(project: Project, source_name):
    cfg = project.source_config(source_name)
    fetcher = Fetcher(project)
    counts = {'rows': 0, 'download_tasks': 0}
    for filename in cfg['metadata_paths']:
        url = f"https://huggingface.co/datasets/{cfg['repo']}/resolve/{cfg['revision']}/{filename}"
        try:
            path, receipt = fetcher.fetch(url, kind='metadata')
        except Exception as exc:
            project.source_state(source_name + ':' + filename, 'unavailable', error=str(exc))
            continue
        out = project.root / 'data/raw/hf' / source_name / cfg['revision'] / filename
        atomic(out, path.read_bytes(), raw=True)
        atomic(out.with_suffix(out.suffix + '.receipt.json'), receipt)
        if not filename.endswith('.jsonl'):
            continue
        for line_number, line in enumerate(out.read_text().splitlines(), 1):
            if not line.strip():
                continue
            row = json.loads(line)
            counts['rows'] += 1
            sid = f"hf:{source_name}:{cfg['revision']}:{filename}:{line_number}"
            payload = {'dataset': cfg['repo'], 'revision': cfg['revision'], 'file': filename, 'line': line_number,
                       'upstream_id': row['id'], 'source': row.get('source', {}), 'record': row}
            project.add_source(sid, 'baseline', payload, out)
            # Do not add the same upstream conversation twice; all source rows remain separately archived.
            cid = 'baseline:' + row['id']
            project.add_candidate(cid, sid, {'baseline': True, 'upstream_id': row['id'], 'source_config': source_name})
            images = row.get('images') or row.get('images_file_name') or []
            for ordinal, asset_path in enumerate(images):
                if not isinstance(asset_path, str) or not any(asset_path.startswith(prefix) for prefix in cfg['asset_prefixes']):
                    continue
                asset_url = f"https://huggingface.co/datasets/{cfg['repo']}/resolve/{cfg['revision']}/{quote(asset_path)}"
                project.enqueue('download', {'source_id': sid, 'ordinal': ordinal, 'url': asset_url})
                counts['download_tasks'] += 1
        print(json.dumps({'source': source_name, 'file': filename, **counts}), flush=True)
    project.source_state(source_name, 'metadata_archived_media_queued', **counts, revision=cfg['revision'])
    return counts


def reddit_media(post):
    """Preserve gallery order; fallbacks do not replace available original media."""
    found = []
    meta = post.get('media_metadata') or {}
    for item in (post.get('gallery_data') or {}).get('items', []):
        medium = meta.get(item.get('media_id'), {})
        src = medium.get('s') or {}
        url = src.get('u') or src.get('gif')
        if url:
            found.append(html.unescape(url))
    if not found:
        url = post.get('url_overridden_by_dest') or post.get('url', '')
        if re.search(r'\.(png|jpe?g|webp|gif)(\?|$)', url, re.I):
            found.append(html.unescape(url))
    if not found:
        for item in (post.get('preview') or {}).get('images', []):
            url = (item.get('source') or {}).get('url')
            if url:
                found.append(html.unescape(url))
    if not found:
        for parent in post.get('crosspost_parent_list') or []:
            found.extend(reddit_media(parent))
    return list(dict.fromkeys(found))


def discover_author(project: Project, source_name='janus_archive'):
    cfg = project.source_config(source_name)
    fetcher = Fetcher(project)
    counts = {'pages': 0, 'groups': 0, 'images': 0}
    for url in cfg['pages']:
        try:
            path, receipt = fetcher.fetch(url)
        except Exception as exc:
            project.source_state(url, 'unavailable', error=str(exc))
            continue
        soup = BeautifulSoup(path.read_text(), 'html.parser')
        counts['pages'] += 1
        groups = soup.select('.tweet')
        for group in groups:
            links = [a.get('href','') for a in group.select('a[href]')]
            tweet_urls = [u for u in links if re.search(r'(?:twitter|x)\.com/[^/]+/status/\d+',u)]
            images = []
            for img in group.select('img'):
                src = img.get('data-src') or img.get('src', '')
                if 'tweets_media/' in src:
                    images.append(urljoin(cfg['media_base_url'],src))
            images = list(dict.fromkeys(images))
            group_text=group.get_text('\n',strip=True)
            quoted_text_candidate=len(group_text)>300 and bool(re.search(r'(?im)(?:^|\n)\s*(?:[>*]|\*\*)?\s*(?:user|human|assistant|bing|sydney|me|you)\s*[:：>]|bing|sydney',group_text))
            if not images and not quoted_text_candidate:
                continue
            tid = re.search(r'/status/(\d+)', tweet_urls[0]).group(1) if tweet_urls else digest(str(group))[:20]
            cid = f'author:repligate:{tid}'
            sid = f'web:{cid}'
            # Full tweet text plus actual links/image order; all images are candidates until classified.
            payload = {'url':tweet_urls[0] if tweet_urls else url,'archive_url':url,'text':group_text,
                       'links':links,'image_urls':images,'posted_at':twitter_date(tid),'discovery_method':'author_archive'}
            project.add_source(sid,'author_archive',payload,path)
            project.add_candidate(cid,sid,{'baseline':False,'source_config':source_name})
            baseline_ids=['baseline:janus:'+tid,'baseline:twitter:'+tid]
            for baseline_id in baseline_ids:
                if project.rows('SELECT 1 FROM candidates WHERE id=?',(baseline_id,)):
                    project.execute('INSERT OR IGNORE INTO relations VALUES (?,?,?,?)',(cid,baseline_id,'same_original_post',json.dumps({'tweet_id':tid})))
            for i,img_url in enumerate(images):
                project.enqueue('download',{'source_id':sid,'ordinal':i,'url':img_url})
                counts['images']+=1
            counts['groups']+=1
        project.source_state(url, 'archived', groups=len(groups), bytes=receipt['bytes'])
        print(json.dumps({'source':source_name,'page':url,**counts}),flush=True)
    project.source_state(source_name,'archive_media_queued',**counts)
    return counts


def twitter_date(native_id):
    from datetime import datetime, timezone
    try:
        return datetime.fromtimestamp(((int(native_id)>>22)+1288834974657)/1000,timezone.utc).isoformat()
    except (ValueError, OverflowError):
        return None


def discover_page(project, source_name):
    cfg=project.source_config(source_name)
    fetcher=Fetcher(project)
    results=[]
    for url in [cfg['entry_url']]+cfg.get('seed_urls',[]):
        try:
            path,receipt=fetcher.fetch(url)
            soup=BeautifulSoup(path.read_text(),'html.parser')
            sid='page:'+digest(url)
            payload={'url':url,'text':soup.get_text('\n',strip=True),'links':[urljoin(url,a['href']) for a in soup.select('a[href]')]}
            project.add_source(sid,'web_page',payload,path)
            project.add_candidate(sid,sid,{'baseline':False,'source_config':source_name})
            images=[]
            for img in soup.select('img[src]'):
                u=urljoin(url,img['src'])
                if allowed_url(u,fetcher.domains):
                    images.append(u)
            for i,u in enumerate(dict.fromkeys(images)):
                project.enqueue('download',{'source_id':sid,'ordinal':i,'url':u})
            results.append({'url':url,'images':len(images),'status':'archived'})
        except Exception as exc:
            results.append({'url':url,'status':'unavailable','error':str(exc)})
    project.source_state(source_name,'partial_seed_discovery',results=results)
    return results
