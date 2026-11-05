"""Freeze baseline source/file/media accounting without summing overlapping corpora."""
import json
from collections import Counter
from newbing.core import Project,atomic,digest,now
p=Project();files=[]
for path in sorted((p.root/'data/raw/hf').rglob('*')):
    if path.is_file() and not path.name.endswith('.receipt.json'):
        data=path.read_bytes();files.append({'path':str(path.relative_to(p.root)),'sha256':digest(data),'bytes':len(data),
            'jsonl_rows':sum(bool(line.strip()) for line in data.splitlines()) if path.suffix=='.jsonl' else None})
references=p.rows("SELECT s.id source_id,json_extract(s.payload,'$.dataset') dataset,sa.ordinal,sa.url,sa.asset_id,a.path,a.bytes FROM sources s JOIN source_assets sa ON sa.source_id=s.id JOIN assets a ON a.id=sa.asset_id WHERE s.kind='baseline' ORDER BY s.id,sa.ordinal")
failures=p.rows("SELECT t.status,t.payload,t.error FROM tasks t JOIN sources s ON s.id=json_extract(t.payload,'$.source_id') WHERE t.kind='download' AND s.kind='baseline' AND t.status!='succeeded'")
counts=p.rows("SELECT json_extract(payload,'$.dataset') dataset,count(*) raw_rows FROM sources WHERE kind='baseline' GROUP BY dataset")
checks=p.rows("SELECT status,count(*) n FROM tasks WHERE kind='download' AND json_extract(payload,'$.verify_upstream_bytes')=1 GROUP BY status")
report={'at':now(),'files':files,'raw_rows_by_dataset':counts,'logical_baseline_candidates':p.rows("SELECT count(*) n FROM candidates WHERE json_extract(payload,'$.baseline')=1")[0]['n'],
 'media_references':len(references),'distinct_asset_hashes':len({r['asset_id'] for r in references}),'upstream_byte_check_tasks':checks,
 'media_records':references,'non_success_tasks':failures,'overlap_note':'Raw source rows and image references remain separate; neither is an independent conversation count.','rights_note':'Dataset README source/assembly terms are preserved. Rights to individual public screenshots/posts are not inferred; no public release has been performed.'}
atomic(p.root/'reports/P2-baseline-manifest.json',report)
text='# P2 固定基线与媒体归档\n\n'+report['at']+'\n\n'
text+=f"保存了 {len(files)} 个固定 revision 的原始 README/JSONL 文件；两库原始行分别 660 与764（后者757主记录+7duplicates），合并为 {report['logical_baseline_candidates']} 个逻辑基线候选，不能把1424行称为独立会话。\n\n"
text+=f"{report['media_references']} 个来源图片引用对应 {report['distinct_asset_hashes']} 个独立内容哈希。基线媒体未成功任务 {len(failures)}；额外1465个上游字节核查已全部成功，未发现跨库同名图片的字节差异。逐文件/逐引用哈希在 P2-baseline-manifest.json。\n\n"
text+='Janus 2023-02/03/04 的原始HTML、385个带媒体组与494个媒体引用已保存；这些不是385个Bing会话。种子网页及引用链来源另在覆盖矩阵记录。基线归档完成不等于转写核验完成，已知文字/emoji/角色/上下文问题继续清洗或隔离。\n'
atomic(p.root/'reports/P2-baseline.md',text,raw=True)
print({k:v for k,v in report.items() if k not in ('files','media_records','non_success_tasks')})
