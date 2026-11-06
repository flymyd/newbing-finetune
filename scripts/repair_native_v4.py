#!/usr/bin/env python3
"""Replay existing synthetic contexts through Qwen, then Argon, preserving prior evidence."""
import json
import time
import runpy
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import httpx
from dotenv import dotenv_values
from newbing.core import ROOT, atomic, now

module = runpy.run_path(str(ROOT/'scripts/synthesize_v4.py'))


def repair(record):
    syn = ROOT/'exports/synthetic-20261106-zh'
    if record['mode'] != 'Thinking' or record.get('reasoning_input_mode') == 'native_conversation':
        return record
    key = dotenv_values(ROOT/'.env.local')['NEWBING_API_KEY']
    idx = record['id'].split(':')[-1]
    prefix = syn/'raw/native-repair'/idx
    atomic(syn/'pre-native-repair-records'/f'{idx}.json',record)
    error = None
    with httpx.Client(timeout=httpx.Timeout(900,connect=60),follow_redirects=False) as client:
        for attempt in range(1,4):
            try:
                qpath=Path(str(prefix)+f'.attempt-{int(time.time())}-{attempt}.qwen.sse')
                context=[{'role':'user' if m['from']=='human' else 'assistant','content':m['value']} for m in record['conversations'][:-1]]
                atomic(qpath.with_suffix('.request.json'),{'input':context,'model':'Qwen3.6-35B-A3B','reasoning':{'effort':'high'}})
                cached = sorted(syn.joinpath('raw/native-repair').glob(idx+'.*.qwen.parsed.json'))
                if cached:
                    parsed = json.loads(cached[-1].read_text())
                    qpath = cached[-1].with_suffix('').with_suffix('.sse')
                    think,usage,draft = parsed['native_think'],parsed['usage'],parsed['draft_answer']
                else:
                    think,usage=module['qwen_stream'](client,key,context,qpath)
                    draft=json.loads(qpath.with_suffix('.parsed.json').read_text())['draft_answer']
                final_prompt=json.dumps({'conversation_context':record['conversations'][:-1], 'native_think':think,'qwen_draft':draft,'instruction':'根据原生推理结论及草稿生成 Sydney 风格最终回复，并单独给出简短 style_bridge。用简体中文。'},ensure_ascii=False)
                fpath=Path(str(prefix)+f'.attempt-{int(time.time())}-{attempt}.final.sse')
                atomic(fpath.with_suffix('.request.json'),{'prompt':final_prompt,'system':module['FINAL_SYSTEM'],'model':'gemini-4-argon'})
                raw,fu=module['gemini_stream'](client,key,final_prompt,fpath,module['FINAL_SYSTEM'])
                obj=module['parse_json_text'](raw)
                answer,bridge=obj.get('answer'),obj.get('style_bridge')
                if not all(isinstance(v,str) and v.strip() for v in (answer,bridge)):
                    raise ValueError('missing answer or style_bridge')
                if '<think>' in think or '</think>' in think or '<think>' in bridge or '</think>' in bridge:
                    raise ValueError('nested reasoning delimiter')
                record['native_think']=think
                record['style_bridge']=bridge.strip()
                record['conversations'][-1]['value']='<think>\n'+think+'\n'+bridge.strip()+'\n</think>\n'+answer.strip()
                record['raw_files']=[p for p in record['raw_files'] if p.endswith('.seed.sse')]+[str(qpath.relative_to(syn)),str(fpath.relative_to(syn))]
                record['reasoning_input_mode']='native_conversation'
                record['model_trace']['qwen_usage']=usage
                record['model_trace']['final_usage']=fu
                record['repaired_at']=now()
                atomic(syn/'records'/f'{idx}.json',record)
                return record
            except Exception as exc:
                error=str(exc)
                atomic(Path(str(prefix)+f'.attempt-{attempt}.error.json'),{'error':error,'at':now()})
                if error=='provider_refusal':break
    raise RuntimeError(record['id']+': '+str(error))


def main():
    syn=ROOT/'exports/synthetic-20261106-zh'
    records=[json.loads(line) for line in (syn/'synthetic.jsonl').read_text().splitlines()]
    # Load already completed repairs on resume.
    records=[json.loads((syn/'records'/f"{r['id'].split(':')[-1]}.json").read_text()) for r in records]
    pending=[r for r in records if r['mode']=='Thinking' and r.get('reasoning_input_mode')!='native_conversation']
    failures=[]
    with ThreadPoolExecutor(max_workers=8) as pool:
        jobs={pool.submit(repair,r):r['id'] for r in pending}
        for job in as_completed(jobs):
            try:job.result();print(json.dumps({'id':jobs[job],'status':'repaired'}),flush=True)
            except Exception as exc:failures.append({'id':jobs[job],'error':str(exc)});print(json.dumps(failures[-1]),flush=True)
    atomic(syn/'native-repair-failures.json',failures)
    if failures:raise SystemExit(1)
    records=[json.loads((syn/'records'/f"{r['id'].split(':')[-1]}.json").read_text()) for r in records]
    atomic(syn/'synthetic.jsonl',''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in records),raw=True)
    module['build_review'](syn,records)
    print(json.dumps({'records':len(records),'native_repaired':sum(r['mode']=='Thinking' for r in records)}),flush=True)

if __name__=='__main__':main()
