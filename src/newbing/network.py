from __future__ import annotations

from collections import defaultdict
import io
import json
import os
from pathlib import Path
import random
import re
import threading
import time
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlparse

import httpx
from PIL import Image

from .core import Project, atomic, digest, now, BudgetExceeded

_host_locks = defaultdict(threading.Lock)
_host_last = defaultdict(float)


def allowed_url(url, domains):
    parsed = urlparse(url)
    if parsed.scheme != 'https' or parsed.username or parsed.password or not parsed.hostname:
        return False
    host = parsed.hostname.lower()
    return any(host == d or host.endswith('.' + d) for d in domains)


class FetchError(RuntimeError):
    def __init__(self, reason, status=None, retry_at=None):
        self.status = status
        self.retry_at=retry_at
        super().__init__(reason)


class SourceUnavailable(FetchError):
    pass


def retry_delay(headers,default=2):
    delays=[float(default)]
    for key in ('retry-after','x-ratelimit-reset'):
        value=headers.get(key)
        if value:
            try:
                number=float(value)
                if key=='x-ratelimit-reset' and number>1e9:
                    if number>1e11:number/=1000
                    number-=time.time()
                delays.append(number)
            except ValueError:
                try:delays.append(parsedate_to_datetime(value).timestamp()-time.time())
                except (ValueError,TypeError):pass
    value=headers.get('x-ratelimit-reset-at')
    if value:
        try:
            number=float(value)
            if number>1e11:number/=1000
            delays.append(number-time.time())
        except ValueError:pass
    return max(0,*delays)


class Fetcher:
    def __init__(self, project: Project, client=None):
        self.p = project
        self.domains = set(project.config['sources']['discovery']['allowed_page_domains'] +
                           project.config['sources']['discovery']['allowed_media_domains'])
        self.client = client or httpx.Client(timeout=project.policy['download']['timeout_seconds'], follow_redirects=False,
                                             headers={'User-Agent': 'NewBingHistoricalArchive/0.1 (public archival research)'})

    def fetch(self, url, *, kind='page', max_bytes=None):
        self.p.enabled()
        if not allowed_url(url, self.domains):
            raise FetchError('domain not allowlisted: ' + str(urlparse(url).hostname))
        key = digest(url)
        receipt = self.p.root / 'data/raw/net' / (key + '.receipt.json')
        if receipt.is_file():
            info = json.loads(receipt.read_text())
            path = self.p.root / info['path']
            if path.is_file() and digest(path.read_bytes()) == info['sha256']:
                return path, info
        limit = max_bytes or self.p.policy['download']['max_single_asset_bytes']
        partial_path=self.p.root/'data/derived/download_parts'/(key+'.bin')
        partial_receipt=partial_path.with_suffix('.json')
        current = url
        redirects = 0
        while True:
            if not allowed_url(current, self.domains):
                raise FetchError('redirect domain not allowlisted: ' + str(urlparse(current).hostname))
            attempts = self.p.rows('SELECT count(*) n FROM http_attempts WHERE url=?', (current,))[0]['n']
            previous=self.p.rows('SELECT status,error FROM http_attempts WHERE url=? ORDER BY id DESC LIMIT 1',(current,))
            if previous and previous[0]['status'] in (401,403,404,410):
                raise FetchError('known terminal HTTP for this exact URL; use an explicit alternate source',previous[0]['status'])
            if attempts >= self.p.policy['retry']['max_http_attempts_per_logical_task']:
                raise FetchError('URL attempts exhausted',previous[0]['status'] if previous else None)
            host = urlparse(current).hostname
            n=self.p.policy['retry']['max_host_consecutive_rate_limits']
            recent=self.p.rows('SELECT status FROM http_attempts WHERE host=? AND epoch=? AND finished IS NOT NULL ORDER BY id DESC LIMIT ?',(host,self.p.policy['budgets']['epoch'],n))
            if len(recent)==n and all(r['status']==429 for r in recent):
                self.p.source_state('host_access:'+host,'rate_limited_for_current_pass',consecutive_429=n,at=now())
                raise SourceUnavailable('host repeatedly rate-limited after bounded recovery; defer this source for current pass',429)
            cooldown=self.p.rows('SELECT payload FROM source_status WHERE id=?',('rate_limit:'+host,))
            if cooldown:
                until=json.loads(cooldown[0]['payload']).get('retry_at',0)
                if until>time.time():raise FetchError('host rate-limit cooldown',429,retry_at=until)
            with _host_locks[host]:
                delay = 1 / self.p.policy['download']['initial_per_host_requests_per_second'] - (time.monotonic() - _host_last[host])
                if delay > 0:
                    time.sleep(delay)
                _host_last[host] = time.monotonic()
            aid = self.p.reserve_http(current, kind, limit)
            received = 0;chunks=[];validator=None;resume=b'';resume_meta={}
            if partial_receipt.is_file() and partial_path.is_file():
                resume_meta=json.loads(partial_receipt.read_text());candidate=partial_path.read_bytes()
                if (resume_meta.get('url')==current and resume_meta.get('sha256')==digest(candidate)
                    and 0<len(candidate)<limit and resume_meta.get('validator')):
                    resume=candidate
            headers={'Accept-Encoding':'identity'}
            if resume:headers.update({'Range':f'bytes={len(resume)}-','If-Range':resume_meta['validator']})
            try:
                with self.client.stream('GET', current,headers=headers) as response:
                    status = response.status_code
                    if status in (301, 302, 303, 307, 308):
                        redirects += 1
                        if redirects > 8:
                            raise FetchError('redirect limit', status)
                        location = response.headers.get('location')
                        if not location:
                            raise FetchError('redirect without location', status)
                        current = urljoin(current, location)
                        self.p.execute('UPDATE http_attempts SET status=?,reserved=0,finished=? WHERE id=?', (status, now(), aid))
                        continue
                    if status==206:
                        match=re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)',response.headers.get('content-range',''))
                        actual_validator=response.headers.get('etag') or response.headers.get('last-modified')
                        if (not resume or not match or int(match[1])!=len(resume) or int(match[2])>=int(match[3])
                            or int(match[3])>limit or actual_validator!=resume_meta['validator']):
                            partial_receipt.unlink(missing_ok=True)
                            raise FetchError('invalid resume range or changed validator',status)
                    elif status==200:
                        resume=b''  # Server ignored Range or If-Range detected changed content.
                    if status not in (200,206):
                        # Bound error reads too; a non-200 response can also carry a huge body.
                        error_parts=[];error_size=0
                        for chunk in response.iter_bytes(chunk_size=4096):
                            allowed=16384-error_size
                            if allowed<=0:break
                            error_parts.append(chunk[:allowed]);error_size+=min(len(chunk),allowed)
                        error_bytes=b''.join(error_parts)
                        received = len(error_bytes)
                        atomic(self.p.root/'data/raw/http_errors'/f'{aid}.body',error_bytes,raw=True)
                        retry_at=None
                        has_retry_header=any(k in response.headers for k in ('retry-after','x-ratelimit-reset','x-ratelimit-reset-at'))
                        if status==429 or status in (500,502,503,504) and has_retry_header:
                            retry_at=time.time()+retry_delay(response.headers,default=300 if status==429 else 2)
                            self.p.source_state('rate_limit:'+host,'cooldown',retry_at=retry_at)
                        raise FetchError(f'HTTP {status}: {error_bytes.decode("utf-8",errors="replace")[:500]}', status,retry_at)
                    validator=response.headers.get('etag') or response.headers.get('last-modified')
                    if validator and validator.startswith('W/'):validator=response.headers.get('last-modified')
                    for chunk in response.iter_bytes():
                        received += len(chunk)
                        if len(resume)+received > limit:
                            raise FetchError('response byte limit', status)
                        chunks.append(chunk)
                    data = resume+b''.join(chunks)
                    if status==206 and len(data)!=int(match[3]):raise httpx.ReadError('resumed content incomplete')
                    path = self.p.root / 'data/raw/net' / (key + '.blob')
                    atomic(path, data, raw=True)
                    info = {'url': url, 'resolved_url': str(response.url), 'path': str(path.relative_to(self.p.root)),
                            'mime': response.headers.get('content-type', ''), 'bytes': len(data), 'sha256': digest(data),
                            'fetched_at': now(), 'etag': response.headers.get('etag'), 'last_modified': response.headers.get('last-modified'),
                            'resumed_prefix_bytes':len(resume)}
                    atomic(receipt, info)
                    partial_receipt.unlink(missing_ok=True);partial_path.unlink(missing_ok=True)
                    self.p.execute('UPDATE http_attempts SET status=?,bytes=?,reserved=0,finished=? WHERE id=?', (status,received, now(), aid))
                    return path, info
            except Exception as exc:
                if isinstance(exc,httpx.TransportError) and validator and chunks:
                    partial=resume+b''.join(chunks)
                    atomic(partial_path,partial,raw=True)
                    atomic(partial_receipt,{'url':current,'validator':validator,'sha256':digest(partial),'bytes':len(partial),'at':now()})
                status = getattr(exc, 'status', None)
                self.p.execute('UPDATE http_attempts SET status=?,bytes=?,reserved=0,error=?,finished=? WHERE id=?',
                               (status, received, str(exc)[:800], now(), aid))
                retryable = status in (429, 500, 502, 503, 504) or isinstance(exc, httpx.TransportError)
                if getattr(exc,'retry_at',None) and exc.retry_at-time.time()>60:
                    raise
                if retryable and attempts + 1 < self.p.policy['retry']['max_http_attempts_per_logical_task']:
                    wait = min(60, 2 ** (attempts + 1) + random.random())
                    if getattr(exc,'retry_at',None):wait=max(wait,exc.retry_at-time.time())
                    time.sleep(wait)
                    continue
                raise

    def asset(self, source_id, ordinal, url,original_url=None,recovery_method=None,verify_upstream_bytes=False):
        existing = self.p.rows('SELECT asset_id FROM source_assets WHERE source_id=? AND ordinal=? AND url=?', (source_id, ordinal, url))
        if existing and not verify_upstream_bytes:
            return existing[0]['asset_id']
        path, receipt = self.fetch(url, kind='image')
        with Image.open(path) as im:
            im.verify()
        with Image.open(path) as im:
            im.load()
            width, height, fmt = im.width, im.height, im.format
        mime = Image.MIME.get(fmt)
        if not mime or not mime.startswith('image/'):
            raise FetchError('unsupported image MIME')
        sha = receipt['sha256']
        ext = {'JPEG': '.jpg', 'PNG': '.png', 'GIF': '.gif', 'WEBP': '.webp'}.get(fmt, '.' + fmt.lower())
        out = self.p.root / 'data/assets/sha256' / sha[:2] / (sha + ext)
        out.parent.mkdir(parents=True, exist_ok=True)
        if not out.exists():
            os.link(path, out)
        self.p.execute('INSERT OR IGNORE INTO assets VALUES (?,?,?,?,?,?)',
                       (sha, str(out.relative_to(self.p.root)), mime, receipt['bytes'], width, height))
        self.p.execute('INSERT OR REPLACE INTO source_assets VALUES (?,?,?,?)', (source_id, sha, ordinal, url))
        if verify_upstream_bytes:
            atomic(self.p.root/'data/processed/upstream_asset_checks'/f'{digest([source_id,ordinal,url])}.json',
                   {'source_id':source_id,'ordinal':ordinal,'url':url,'asset_id':sha,
                    'previous_asset_ids':[r['asset_id'] for r in existing],
                    'same_bytes':bool(existing) and all(r['asset_id']==sha for r in existing),
                    'receipt_sha256':receipt['sha256'],'checked_at':now()})
        if recovery_method:
            atomic(self.p.root/'data/processed/media_recoveries'/f'{digest([source_id,ordinal,url])}.json',
                   {'source_id':source_id,'ordinal':ordinal,'asset_id':sha,'original_url':original_url,
                    'recovered_url':url,'method':recovery_method,'original_dimensions':[width,height]})
        return sha


def run_downloads(project, limit=100, workers=2, dry_run=False, source_kind=None,recovery_method=None):
    if recovery_method:
        jobs=project.rows("SELECT * FROM tasks WHERE kind='download' AND status IN ('pending','retry_wait') AND (lease_until IS NULL OR lease_until<?) AND json_extract(payload,'$.recovery_method')=? ORDER BY rowid LIMIT ?",(time.time(),recovery_method,limit))
    elif source_kind:
        jobs = project.rows("SELECT t.* FROM tasks t JOIN sources s ON s.id=json_extract(t.payload,'$.source_id') WHERE t.kind='download' AND t.status IN ('pending','retry_wait') AND (t.lease_until IS NULL OR t.lease_until<?) AND s.kind=? ORDER BY t.rowid LIMIT ?", (time.time(),source_kind,limit))
    else:
        jobs = project.rows("SELECT * FROM tasks WHERE kind='download' AND status IN ('pending','retry_wait') AND (lease_until IS NULL OR lease_until<?) ORDER BY rowid LIMIT ?", (time.time(),limit))
    if dry_run:
        return {'queued': len(jobs), 'dry_run': True}
    from concurrent.futures import ThreadPoolExecutor
    def work(job):
        with project.lock:
            claimed=project.claim_task(job['id'],300)
        if not claimed:
            return 'not_claimed'
        payload = json.loads(job['payload'])
        try:
            asset_id = Fetcher(project).asset(**payload)
            project.execute("UPDATE tasks SET status='succeeded',output_ref=?,lease_until=NULL,error=NULL,updated=? WHERE id=?", (asset_id, now(), job['id']))
            return 'succeeded'
        except BudgetExceeded as exc:
            project.execute("UPDATE tasks SET status='deferred_resource',lease_until=NULL,error=?,updated=? WHERE id=?", (str(exc), now(), job['id']))
            return 'deferred_resource'
        except Exception as exc:
            if isinstance(exc,SourceUnavailable):
                project.execute("UPDATE tasks SET status='deferred_source_unavailable',lease_until=NULL,error=?,updated=? WHERE id=?",(str(exc),now(),job['id']))
                return 'deferred_source_unavailable'
            if getattr(exc,'retry_at',None):
                project.execute("UPDATE tasks SET status='retry_wait',lease_until=?,error=?,updated=? WHERE id=?",(exc.retry_at,str(exc)[:800],now(),job['id']))
                return 'retry_wait'
            project.execute("UPDATE tasks SET status='failed_terminal',lease_until=NULL,error=?,updated=? WHERE id=?", (str(exc)[:800], now(), job['id']))
            return 'failed_terminal'
    from collections import Counter
    result = Counter()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for i, state in enumerate(pool.map(work, jobs), 1):
            result[state] += 1
            if i % 25 == 0:
                print(json.dumps({'processed': i, **result}), flush=True)
    return dict(result)
