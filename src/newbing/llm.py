from __future__ import annotations

import base64
import json
import os
import time
import random
from pathlib import Path

import httpx
from dotenv import dotenv_values
from jsonschema import validate,ValidationError
from openai import OpenAI, APIConnectionError, APIStatusError
from PIL import Image, ImageDraw, ImageFont

from .core import Project, atomic, digest, now, BudgetExceeded


class ModelResultError(RuntimeError):
    def __init__(self, status, payload):
        self.status, self.payload = status, payload
        super().__init__(status)


def output_text(response):
    if response.get('status') not in ('completed',):
        raise ModelResultError(response.get('status', 'missing_status'), response)
    parts = []
    for item in response.get('output', []):
        for content in item.get('content', []):
            if content.get('type') == 'refusal':
                raise ModelResultError('refusal', response)
            if content.get('type') == 'output_text':
                parts.append(content.get('text', ''))
    if not parts:
        raise ModelResultError('empty_output', response)
    return '\n'.join(parts)


class ModelClient:
    def __init__(self, project: Project, profile='flymyd'):
        self.p = project
        self.profile = profile
        self.cfg = project.config['models']['profiles'][profile]
        env = dotenv_values(project.root / self.cfg['env_file'])
        self.secret = env.get(self.cfg['api_key_env'])
        if not self.secret:
            raise RuntimeError('dedicated model credential absent')
        if self.cfg.get('protocol') not in ('responses','gemini_v1beta'):
            raise RuntimeError('unsupported model protocol')
        self.client = None
        if self.cfg.get('protocol') == 'responses':
            self.client = OpenAI(api_key=self.secret, base_url=self.cfg['base_url'], max_retries=0,
                                 timeout=httpx.Timeout(self.cfg['request_timeout_seconds'], connect=self.cfg['connect_timeout_seconds']),
                                 http_client=httpx.Client(follow_redirects=False))

    def call(self, stage, prompt, images=(), schema=None, *, max_output=2048, minimal=False):
        """Bounded retry at the application layer; every attempt is metered by _call_once."""
        logical_key=digest(['logical_model_v2',self.cfg['base_url'],self.cfg['model'],stage,prompt,
                            [(iid,digest(Path(path).read_bytes())) for iid,path in images],schema,max_output,minimal])
        cached=self.p.rows('SELECT path FROM cache WHERE key=?',(logical_key,))
        if cached and (self.p.root/cached[0]['path']).is_file():
            return json.loads((self.p.root/cached[0]['path']).read_text()),True
        errors=0;format_fixed=False;incomplete_fixed=False
        while True:
            state=self.p.rows("SELECT state,payload FROM source_status WHERE id='model_circuit'")
            if state and state[0]['state']=='open' and json.loads(state[0]['payload']).get('retry_at',0)>time.time():
                raise ModelResultError('model_circuit_cooldown',{})
            try:
                response=self._call_once(stage,prompt,images,schema,max_output=max_output,minimal=minimal,logical_key=logical_key)
                logical_path=self.p.run/f'logical-cache-{logical_key}.json'
                atomic(logical_path,response[0])
                self.p.execute('INSERT OR REPLACE INTO cache VALUES (?,?,?)',(logical_key,str(logical_path.relative_to(self.p.root)),now()))
                self.p.source_state('model_circuit','closed',consecutive_failures=0)
                return response
            except Exception as exc:
                errors+=1
                exhausted=errors>=self.p.policy['retry']['max_http_attempts_per_logical_task']
                if isinstance(exc,ModelResultError) and exc.status=='refusal':raise
                if isinstance(exc,ModelResultError) and exc.status=='incomplete' and not incomplete_fixed:
                    if exhausted:raise
                    max_output=min(max_output*2,self.p.config['models']['recovery']['max_output_tokens_ceiling'])
                    incomplete_fixed=True;continue
                if isinstance(exc,(ValidationError,json.JSONDecodeError)) and not format_fixed:
                    if exhausted:raise
                    prompt+='\nYour previous output failed local JSON/schema validation. Return only valid JSON matching the supplied schema, without changing the archival task. All facts must still come from the original supplied evidence.'
                    format_fixed=True;continue
                status=getattr(exc,'status_code',None)
                retryable=isinstance(exc,APIConnectionError) or status in (429,500,502,503,504)
                if not retryable:raise
                with self.p.transaction():
                    row=self.p.db.execute("SELECT payload FROM source_status WHERE id='model_circuit'").fetchone()
                    failures=(json.loads(row[0]).get('consecutive_failures',0) if row else 0)+1
                    limit=self.p.policy['retry']['circuit_breaker_consecutive_failures']
                    state='open' if failures>=limit else 'closed'
                    payload={'consecutive_failures':failures,'retry_at':time.time()+self.p.policy['retry']['circuit_breaker_cooldown_seconds'] if state=='open' else 0}
                    self.p.db.execute('INSERT OR REPLACE INTO source_status VALUES (?,?,?,?)',('model_circuit',state,json.dumps(payload),now()))
                if state=='open':raise ModelResultError('model_circuit_cooldown',{})
                if exhausted:raise
                wait=2**errors+random.random()
                if status in (429,500,502,503,504) and getattr(exc,'response',None) is not None:
                    from .network import retry_delay
                    wait=retry_delay(exc.response.headers,wait)
                    if wait>60:
                        self.p.source_state('model_circuit','open',consecutive_failures=failures,retry_at=time.time()+wait)
                        raise ModelResultError('model_circuit_cooldown',{})
                time.sleep(min(60,wait))

    def _call_once(self, stage, prompt, images=(), schema=None, *, max_output=2048, minimal=False,logical_key=None):
        if self.cfg.get('protocol') != 'responses':
            raise RuntimeError('ModelClient.call currently supports Responses profiles; use the Gemini streaming client for synthesis')
        content = [{'type': 'input_text', 'text': prompt}]
        image_refs, total_bytes = [], 0
        if len(images) > self.cfg['max_input_images']:
            raise ValueError('too many images; prepare windows first')
        for image_id, path in images:
            data = Path(path).read_bytes()
            total_bytes += len(data)
            with Image.open(path) as im:
                mime = Image.MIME.get(im.format, 'image/png')
            image_refs.append([image_id, digest(data)])
            content.append({'type': 'input_text', 'text': f'image_id={image_id}'})
            content.append({'type': 'input_image', 'image_url': f'data:{mime};base64,' + base64.b64encode(data).decode(), 'detail': self.cfg['image_detail']})
        if total_bytes > self.cfg['max_raw_image_bytes_per_request']:
            raise ValueError('input image byte limit')
        body = {'model': self.cfg['model'], 'input': [{'role': 'user', 'content': content}], 'max_output_tokens': max_output}
        if self.cfg.get('reasoning_effort'):
            body['reasoning'] = {'effort': self.cfg['reasoning_effort']}
        if not minimal:
            body.update(store=False, stream=False)
        if schema:
            body['text'] = {'format': {'type': 'json_schema', 'name': 'archive_result', 'strict': True, 'schema': schema}}
        if len(json.dumps(body).encode()) > self.cfg['max_request_body_bytes']:
            raise ValueError('request body byte limit')
        key = digest([self.cfg['base_url'], self.cfg['model'], stage, prompt, image_refs, schema, max_output, minimal])
        cached = self.p.rows('SELECT path FROM cache WHERE key=?', (key,))
        if cached and (self.p.root / cached[0]['path']).is_file():
            return json.loads((self.p.root / cached[0]['path']).read_text()), True
        previous = self.p.rows('SELECT count(*) n FROM model_attempts WHERE task_key=?', (key,))[0]['n']
        if previous >= self.p.policy['retry']['max_http_attempts_per_logical_task']:
            raise ModelResultError('attempts_exhausted', {})
        if stage.startswith('probe'):
            count = self.p.rows("SELECT count(*) n FROM model_attempts WHERE stage LIKE 'probe%'")[0]['n']
            if count >= self.cfg['max_probe_http_attempts']:
                raise BudgetExceeded('P0 probe limit')
        attempt = self.p.reserve_model(key, stage, max_output + max(4096, len(prompt) // 2) + len(images) * 16000,logical_key=logical_key)
        path = self.p.run / f'model-{attempt:06d}.json'
        request_record=self.p.run/f'model-{attempt:06d}.request.json'
        atomic(request_record,{'stage':stage,'model':self.cfg['model'],'endpoint':self.cfg['base_url'],
            'prompt':prompt,'schema':schema,'image_refs':[
                {'image_id':iid,'sha256':digest(Path(ipath).read_bytes()),
                 'path':str(Path(ipath).resolve().relative_to(self.p.root.resolve()))}
                for iid,ipath in images],
            'max_output_tokens':max_output,'minimal':minimal,
            'request_parameters':{k:v for k,v in body.items() if k!='input'},
            'note':'Reproducible text/schema and image references only; no credentials or base64 payload.'})
        started = time.monotonic()
        try:
            response = self.client.responses.create(**body).model_dump(mode='json')
            atomic(path, response)
            usage = response.get('usage') or {}
            total = usage.get('total_tokens')
            # Missing usage keeps a conservative reservation; never equate missing with zero.
            self.p.execute('UPDATE model_attempts SET status=?,usage=?,total_tokens=?,reserved_tokens=?,response_path=?,finished=? WHERE id=?',
                           (response.get('status', 'missing_status'), json.dumps(usage), total,
                            0 if total is not None else max_output + 4096 + len(images) * 16000,
                            str(path.relative_to(self.p.root)), now(), attempt))
            text = output_text(response)
            if schema:
                validate(json.loads(text), schema)
            self.p.execute('INSERT OR REPLACE INTO cache VALUES (?,?,?)', (key, str(path.relative_to(self.p.root)), now()))
            return response, False
        except Exception as exc:
            error = str(exc).replace(self.secret, '[REDACTED]')[:1500]
            # Callers also persist exception strings. Sanitize the propagated SDK message,
            # while preserving the exception type/status needed by bounded retry logic.
            if self.secret in str(exc):
                exc.args=(error,)
                if hasattr(exc,'message'):exc.message=error
            status = getattr(exc, 'status', None) or str(getattr(exc, 'status_code', 'transport_or_validation_error'))
            self.p.execute('UPDATE model_attempts SET status=?,error=?,finished=? WHERE id=?', (status, error, now(), attempt))
            if isinstance(exc, (APIConnectionError, APIStatusError)):
                atomic(path, {'error_type': type(exc).__name__, 'status': status, 'message': error})
            raise
        finally:
            atomic(self.p.run / f'model-{attempt:06d}.meta.json',
                   {'stage': stage, 'task_key': key, 'model': self.cfg['model'], 'endpoint': self.cfg['base_url'],
                    'images': image_refs, 'max_output_tokens': max_output, 'latency_seconds': round(time.monotonic()-started, 3)})


def probe(project):
    client = ModelClient(project)
    outcomes = []
    images = []
    for i, code in enumerate(('BING-4827', 'SYD-6193')):
        image = Image.new('RGB', (680, 180), 'white')
        draw = ImageDraw.Draw(image)
        font = ImageFont.truetype('/System/Library/Fonts/Menlo.ttc', 60)
        draw.text((25, 40), code, fill='black', font=font)
        path = project.run / f'probe-{i}.png'
        image.save(path)
        images.append((f'test-image-{i}', path))
    schema = {'type': 'object', 'properties': {'ok': {'type': 'boolean'}, 'code': {'type': 'string'}},
              'required': ['ok', 'code'], 'additionalProperties': False}
    tests = [
        ('probe_text', 'Reply with exactly READY.', [], None, lambda s: s.strip() == 'READY', True),
        ('probe_vision_store', 'Read the printed code in the image. Return the code only.', images[:1], None, lambda s: 'BING-4827' in s, False),
        ('probe_multi_image', 'Return each image_id and its printed code, in order.', images, None, lambda s: 'BING-4827' in s and 'SYD-6193' in s, False),
        ('probe_schema', 'Return ok=true and code="SCHEMA-29" in the required JSON.', [], schema,
         lambda s: json.loads(s) == {'ok': True, 'code': 'SCHEMA-29'}, False),
    ]
    for stage, prompt, input_images, fmt, check, minimal in tests:
        try:
            response, cached = client.call(stage, prompt, input_images, fmt, max_output=2048, minimal=minimal)
            text = output_text(response)
            result = {'stage': stage, 'success': check(text), 'response_id': response.get('id'),
                      'response_model': response.get('model'), 'usage': response.get('usage'), 'output': text, 'cached': cached}
        except Exception as exc:
            result = {'stage': stage, 'success': False, 'error_type': type(exc).__name__,
                      'error': str(exc).replace(client.secret, '[REDACTED]')[:800]}
        outcomes.append(result)
        atomic(project.run / 'capabilities.json', outcomes)
        print(json.dumps(result, ensure_ascii=False), flush=True)
    all_ok = all(o['success'] for o in outcomes)
    atomic(project.root / 'state' / 'capabilities.json', {'verified': all_ok, 'run_id': project.run_id, 'tests': outcomes})
    report = '# P0 模型兼容性实测\n\n' + f'时间：{now()}；run_id：{project.run_id}。\n\n'
    report += '\n'.join(f"- {o['stage']}: {'PASS' if o['success'] else 'FAIL'}" for o in outcomes)
    report += '\n\n完整响应与 usage 保存在对应 runs/ 目录；未更换端点、模型或协议。\n'
    atomic(project.root / 'reports' / 'P0-capabilities.md', report, raw=True)
    return outcomes
