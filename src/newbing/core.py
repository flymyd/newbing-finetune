from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import threading
import time
import tomllib
import sys
import platform
from contextlib import contextmanager
from datetime import datetime, timezone
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    data = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    return hashlib.sha256(data).hexdigest()


def atomic(path: Path, value, *, raw=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = value if raw else json.dumps(value, ensure_ascii=False, indent=2).encode()
    if isinstance(data, str):
        data = data.encode()
    temp = path.with_name(path.name + f'.{os.getpid()}.{threading.get_ident()}.part')
    with temp.open('wb') as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    temp.replace(path)


class BudgetExceeded(RuntimeError):
    pass


class ModelConcurrencyBusy(RuntimeError):
    pass


def validate_config(root,config):
    """Reject unsupported contract settings before creating state or issuing requests."""
    p=config['pipeline'];model=config['models']['profiles']['flymyd'];sources=config['sources']
    if model['base_url']!='https://llm.flymyd.cn/v1' or model['model']!='gpt-6-luna' or model['protocol']!='responses':
        raise ValueError('unsupported model endpoint or protocol')
    if model['sdk_max_retries']!=0:raise ValueError('SDK retries must be disabled')
    if model['responses_path']!='/responses' or model['env_file']!='.env.local' or model['api_key_env']!='NEWBING_API_KEY':
        raise ValueError('unexpected endpoint path or dedicated credential reference')
    e=p['export']
    required={'format':'sharegpt','serialization':'json_array','min_complete_rounds':2,
              'user_role':'human','assistant_role':'gpt','role_field':'from','text_field':'value',
              'conversations_field':'conversations','allow_single_turn':False,'allow_system_field':False,
              'allow_multimodal_fields':False,'prefix_augmentation':False,'text_only':True}
    if any(e.get(k)!=v for k,v in required.items()):raise ValueError('unsupported ShareGPT contract configuration')
    if e['write_jsonl_mirror']:raise ValueError('JSONL mirror is not implemented; use the required JSON arrays')
    if p['download']['cross_host_authorization_forwarding']:raise ValueError('cross-host Authorization forwarding is forbidden')
    if e['tiers']!=['early_strict','early_probable','later_supplement']:raise ValueError('unsupported tiers')
    for field in ('sample_schema','dataset_schema'):
        if not (Path(root)/e[field]).is_file():raise ValueError('missing export schema')
    if any(p['budgets'][k]<=0 for k in ('max_candidate_groups','max_model_http_attempts','max_reported_total_tokens','max_source_http_attempts','max_download_response_bytes','min_free_disk_bytes')):
        raise ValueError('resource caps must be finite positive values')
    if p['budgets']['allow_automatic_increase']:raise ValueError('automatic budget increases are not supported')
    if not (0<p['images']['crop_overlap_px']<p['images']['crop_height_px']):raise ValueError('invalid crop geometry')
    fractions=[p['split'][k] for k in ('train_fraction','validation_fraction','test_fraction')]
    if any(v<0 or v>1 for v in fractions) or abs(sum(fractions)-1)>1e-10:raise ValueError('invalid split fractions')
    if not 1<=model['initial_concurrency']<=model['max_concurrency']<=6:raise ValueError('invalid model concurrency')
    if model['max_input_images']<1 or model['max_raw_image_bytes_per_request']<1:raise ValueError('invalid image limits')
    ids=[s['id'] for s in sources['sources']]
    if len(ids)!=len(set(ids)):raise ValueError('duplicate source id')
    for source in sources['sources']:
        if any(w not in p['windows'] for w in source.get('windows',[])):raise ValueError('unknown source window')
    for task in config['models']['tasks'].values():
        if not (Path(root)/task['prompt']).is_file():raise ValueError('missing task prompt')
        if task['profile'] not in config['models']['profiles']:raise ValueError('unknown model profile')


class Project:
    def __init__(self, root=ROOT):
        self.root = Path(root)
        self.config = {name: tomllib.loads((self.root / 'config' / f'{name}.toml').read_text())
                       for name in ('pipeline', 'models', 'sources')}
        validate_config(self.root,self.config)
        self.policy = self.config['pipeline']
        self.lock = threading.RLock()
        (self.root / 'state').mkdir(exist_ok=True)
        self.db = sqlite3.connect(self.root / self.policy['paths']['state_db'], timeout=60, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
        PRAGMA journal_mode=WAL;
        PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY,kind TEXT,payload TEXT,raw_path TEXT,raw_hash TEXT,created TEXT);
        CREATE TABLE IF NOT EXISTS assets(id TEXT PRIMARY KEY,path TEXT,mime TEXT,bytes INTEGER,width INTEGER,height INTEGER);
        CREATE TABLE IF NOT EXISTS source_assets(source_id TEXT,asset_id TEXT,ordinal INTEGER,url TEXT,
          PRIMARY KEY(source_id,ordinal,url));
        CREATE TABLE IF NOT EXISTS candidates(id TEXT PRIMARY KEY,source_id TEXT,payload TEXT,created TEXT);
        CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,kind TEXT,input_hash TEXT,payload TEXT,status TEXT,
          attempts INTEGER DEFAULT 0,lease_until REAL,output_ref TEXT,error TEXT,updated TEXT);
        CREATE TABLE IF NOT EXISTS http_attempts(id INTEGER PRIMARY KEY,url TEXT,kind TEXT,status INTEGER,
          bytes INTEGER DEFAULT 0,reserved INTEGER DEFAULT 0,error TEXT,started TEXT,finished TEXT,epoch TEXT);
        CREATE TABLE IF NOT EXISTS model_attempts(id INTEGER PRIMARY KEY,task_key TEXT,stage TEXT,status TEXT,
          usage TEXT,total_tokens INTEGER,reserved_tokens INTEGER DEFAULT 0,response_path TEXT,error TEXT,
          started TEXT,finished TEXT,epoch TEXT);
        CREATE TABLE IF NOT EXISTS cache(key TEXT PRIMARY KEY,path TEXT,created TEXT);
        CREATE TABLE IF NOT EXISTS relations(lhs TEXT,rhs TEXT,kind TEXT,payload TEXT,PRIMARY KEY(lhs,rhs,kind));
        CREATE TABLE IF NOT EXISTS conversations(id TEXT PRIMARY KEY,source_id TEXT,payload TEXT,updated TEXT);
        CREATE TABLE IF NOT EXISTS source_status(id TEXT PRIMARY KEY,state TEXT,payload TEXT,updated TEXT);
        CREATE INDEX IF NOT EXISTS tasks_candidate ON tasks(kind,json_extract(payload,'$.candidate_id'),status);
        CREATE INDEX IF NOT EXISTS tasks_source ON tasks(kind,json_extract(payload,'$.source_id'),status);
        CREATE INDEX IF NOT EXISTS tasks_kind_status ON tasks(kind,status,lease_until);
        CREATE INDEX IF NOT EXISTS model_logical_request ON model_attempts(task_key);
        CREATE INDEX IF NOT EXISTS http_url ON http_attempts(url);
        ''')
        self.db.execute("INSERT OR IGNORE INTO meta VALUES ('schema_version','1')")
        columns={r[1] for r in self.db.execute('PRAGMA table_info(model_attempts)')}
        if 'owner_pid' not in columns:
            self.db.execute('ALTER TABLE model_attempts ADD COLUMN owner_pid INTEGER')
        if 'logical_key' not in columns:
            self.db.execute('ALTER TABLE model_attempts ADD COLUMN logical_key TEXT')
        http_columns={r[1] for r in self.db.execute('PRAGMA table_info(http_attempts)')}
        if 'host' not in http_columns:
            self.db.execute('ALTER TABLE http_attempts ADD COLUMN host TEXT')
            for row in self.db.execute('SELECT id,url FROM http_attempts').fetchall():
                self.db.execute('UPDATE http_attempts SET host=? WHERE id=?',(urlparse(row['url']).hostname,row['id']))
        if 'owner_pid' not in http_columns:
            self.db.execute('ALTER TABLE http_attempts ADD COLUMN owner_pid INTEGER')
        task_columns={r[1] for r in self.db.execute('PRAGMA table_info(tasks)')}
        if 'owner_pid' not in task_columns:self.db.execute('ALTER TABLE tasks ADD COLUMN owner_pid INTEGER')
        self.db.execute("UPDATE meta SET value='2' WHERE key='schema_version'")
        self.db.commit()
        self.run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + str(os.getpid())
        self.run = self.root / 'runs' / self.run_id
        self.run.mkdir(parents=True, exist_ok=True)
        atomic(self.run / 'config.json', self.config)
        for prompt in (self.root/'prompts').glob('*.md'):
            atomic(self.run/'prompts'/prompt.name,prompt.read_bytes(),raw=True)
        atomic(self.run/'runtime.json',{'python':sys.version,'platform':platform.platform(),
            'source_hashes':{str(path.relative_to(self.root)):digest(path.read_bytes()) for path in (self.root/'src').rglob('*.py')},
            'lock_hash':digest((self.root/'uv.lock').read_bytes()) if (self.root/'uv.lock').exists() else None})

    def execute(self, sql, params=()):
        with self.lock:
            result = self.db.execute(sql, params)
            self.db.commit()
            return result

    def rows(self, sql, params=()):
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, params).fetchall()]

    @contextmanager
    def transaction(self):
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                yield
                self.db.commit()
            except BaseException:
                self.db.rollback()
                raise

    def source_config(self, name):
        return next(s for s in self.config['sources']['sources'] if s['id'] == name)

    def enabled(self):
        if self.policy['planning_only']:
            raise RuntimeError('planning_only=true; execution not enabled')

    def source_state(self, sid, state, **payload):
        self.execute('INSERT OR REPLACE INTO source_status VALUES (?,?,?,?)', (sid, state, json.dumps(payload), now()))

    def add_source(self, sid, kind, payload, raw_path):
        raw_path = Path(raw_path)
        payload=dict(payload)
        for key,value in {'license_notes':'Source-specific terms not yet assessed; raw source retained.',
                          'rights_status':'not_assessed','removal_status':'none_known',
                          'retrieval_status':'raw_archived','fetched_at':now(),
                          'conversation_date':None,'archived_at':None}.items():payload.setdefault(key,value)
        self.execute('INSERT OR IGNORE INTO sources VALUES (?,?,?,?,?,?)',
                     (sid, kind, json.dumps(payload, ensure_ascii=False), str(raw_path.relative_to(self.root)),
                      digest(raw_path.read_bytes()), now()))

    def add_candidate(self, cid, sid, payload):
        with self.transaction():
            count = self.db.execute('SELECT count(*) FROM candidates').fetchone()[0]
            exists = self.db.execute('SELECT 1 FROM candidates WHERE id=?', (cid,)).fetchone()
            if not exists and count >= self.policy['budgets']['max_candidate_groups']:
                raise BudgetExceeded('candidate group limit')
            self.db.execute('INSERT OR IGNORE INTO candidates VALUES (?,?,?,?)',
                            (cid, sid, json.dumps(payload, ensure_ascii=False), now()))

    def enqueue(self, kind, payload):
        key = digest([kind, payload])
        self.execute('INSERT OR IGNORE INTO tasks(id,kind,input_hash,payload,status,updated) VALUES (?,?,?,?,?,?)',
                     (key, kind, digest(payload), json.dumps(payload), 'pending', now()))
        return key

    def reserve_http(self, url, kind, reserve_bytes):
        self.enabled()
        epoch = self.policy['budgets']['epoch']
        host=urlparse(url).hostname;key='host_next:'+str(host)
        while True:
            with self.transaction():
                active=self.db.execute('SELECT count(*) FROM http_attempts WHERE host=? AND finished IS NULL',(host,)).fetchone()[0]
                previous=self.db.execute('SELECT value FROM meta WHERE key=?',(key,)).fetchone()
                next_start=float(previous[0]) if previous else 0
                clock=time.time()
                if active<self.policy['download']['per_host_concurrency'] and next_start<=clock:
                    n, used = self.db.execute('SELECT count(*),coalesce(sum(bytes+reserved),0) FROM http_attempts WHERE epoch=?', (epoch,)).fetchone()
                    b = self.policy['budgets']
                    if n >= b['max_source_http_attempts'] or used + reserve_bytes > b['max_download_response_bytes']:
                        raise BudgetExceeded('source request/download byte budget')
                    if shutil.disk_usage(self.root).free < b['min_free_disk_bytes'] + reserve_bytes:
                        raise BudgetExceeded('minimum disk space')
                    c = self.db.execute('INSERT INTO http_attempts(url,kind,reserved,started,epoch,host,owner_pid) VALUES (?,?,?,?,?,?,?)',
                                        (url, kind, reserve_bytes, now(), epoch,host,os.getpid()))
                    self.db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)',(key,str(clock+1/self.policy['download']['initial_per_host_requests_per_second'])))
                    return c.lastrowid
            # Independent processes share this gate. Sleep only a short interval before checking again.
            time.sleep(min(1,max(0.05,next_start-time.time())) if active<self.policy['download']['per_host_concurrency'] else 0.2)

    def reserve_model(self, key, stage, tokens,logical_key=None):
        self.enabled()
        epoch = self.policy['budgets']['epoch']
        with self.transaction():
            active=self.db.execute('SELECT count(*) FROM model_attempts WHERE finished IS NULL').fetchone()[0]
            if active>=self.config['models']['profiles']['flymyd']['max_concurrency']:
                raise ModelConcurrencyBusy('global model concurrency limit; retry later')
            if logical_key:
                previous=self.db.execute('SELECT count(*) FROM model_attempts WHERE logical_key=?',(logical_key,)).fetchone()[0]
                if previous>=self.policy['retry']['max_http_attempts_per_logical_task']:
                    raise BudgetExceeded('logical model task attempt limit')
            n, used = self.db.execute('SELECT count(*),coalesce(sum(coalesce(total_tokens,0)+reserved_tokens),0) FROM model_attempts WHERE epoch=?', (epoch,)).fetchone()
            b = self.policy['budgets']
            if n >= b['max_model_http_attempts'] or used + tokens > b['max_reported_total_tokens']:
                raise BudgetExceeded('model request/reported token budget')
            c = self.db.execute('INSERT INTO model_attempts(task_key,stage,status,reserved_tokens,started,epoch,owner_pid,logical_key) VALUES (?,?,?,?,?,?,?,?)',
                                (key, stage, 'in_flight', tokens, now(), epoch,os.getpid(),logical_key))
            self.db.commit()
            return c.lastrowid

    def recover(self):
        def dead(pid):
            if not pid:return False  # Old entries without an owner require external evidence, never guess.
            try:os.kill(pid,0);return False
            except ProcessLookupError:return True
            except PermissionError:return False
        for row in self.rows('SELECT id,owner_pid,reserved FROM http_attempts WHERE finished IS NULL'):
            if dead(row['owner_pid']):
                self.execute("UPDATE http_attempts SET bytes=bytes+reserved,reserved=0,error='interrupted_unknown_bytes_charged_conservatively',finished=? WHERE id=?",(now(),row['id']))
        for row in self.rows('SELECT id,owner_pid FROM model_attempts WHERE finished IS NULL'):
            if dead(row['owner_pid']):
                self.execute("UPDATE model_attempts SET status='interrupted_unknown_outcome',error='owner process no longer exists; token reservation retained',finished=? WHERE id=?",(now(),row['id']))
        for row in self.rows("SELECT id,owner_pid FROM tasks WHERE status='leased'"):
            if dead(row['owner_pid']):
                self.execute("UPDATE tasks SET status='pending',owner_pid=NULL,lease_until=NULL,error='owner process exited',updated=? WHERE id=?",(now(),row['id']))
        # An expired timestamp alone is not proof that a known owner exited.
        # Legacy rows without owner IDs can be recovered after their lease, while live owners stay protected.
        result = self.execute("UPDATE tasks SET status='pending',lease_until=NULL,updated=? WHERE status='leased' AND owner_pid IS NULL AND lease_until<?", (now(), time.time()))
        return result.rowcount

    def claim_task(self,task_id,seconds=1800):
        return self.execute("UPDATE tasks SET status='leased',lease_until=?,owner_pid=?,attempts=attempts+1,updated=? WHERE id=? AND status IN ('pending','retry_wait') AND (lease_until IS NULL OR lease_until<?)",
                            (time.time()+seconds,os.getpid(),now(),task_id,time.time())).rowcount

    def status(self):
        return {
            'sources': self.rows('SELECT kind,count(*) n FROM sources GROUP BY kind'),
            'candidates': self.rows('SELECT count(*) n FROM candidates')[0]['n'],
            'assets': self.rows('SELECT count(*) n,coalesce(sum(bytes),0) bytes FROM assets')[0],
            'conversations': self.rows('SELECT count(*) n FROM conversations')[0]['n'],
            'tasks': self.rows('SELECT kind,status,count(*) n FROM tasks GROUP BY kind,status'),
            'http': self.rows('SELECT count(*) attempts,coalesce(sum(bytes),0) downloaded_bytes FROM http_attempts')[0],
            'model': self.rows('SELECT count(*) attempts,coalesce(sum(total_tokens),0) reported_tokens,coalesce(sum(reserved_tokens),0) reserved_tokens, sum(CASE WHEN total_tokens IS NULL THEN 1 ELSE 0 END) unknown_usage, sum(CASE WHEN total_tokens IS NULL AND finished IS NOT NULL THEN 1 ELSE 0 END) finished_unknown_usage, sum(CASE WHEN finished IS NULL THEN 1 ELSE 0 END) in_flight FROM model_attempts')[0],
            'source_status': self.rows('SELECT * FROM source_status'),
        }
