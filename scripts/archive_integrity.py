"""Verify all indexed raw and media files by content hash, retaining failures explicitly."""
import json
from collections import defaultdict
from newbing.core import Project,atomic,digest,now
p=Project();raws=defaultdict(set)
for row in p.rows('SELECT raw_path,raw_hash FROM sources'):raws[row['raw_path']].add(row['raw_hash'])
errors=[];checked_raw=0;checked_assets=0
for path,expected in raws.items():
 f=p.root/path
 if not f.is_file():errors.append({'type':'missing_raw','path':path})
 elif len(expected)!=1 or digest(f.read_bytes()) not in expected:errors.append({'type':'raw_hash_mismatch','path':path})
 checked_raw+=1
for row in p.rows('SELECT id,path FROM assets'):
 f=p.root/row['path']
 if not f.is_file():errors.append({'type':'missing_asset','path':row['path']})
 elif digest(f.read_bytes())!=row['id']:errors.append({'type':'asset_hash_mismatch','path':row['path'],'id':row['id']})
 checked_assets+=1
report={'at':now(),'raw_files':checked_raw,'asset_files':checked_assets,'errors':errors,'passed':not errors,
        'scope':'Every indexed source raw path/hash and every asset id/path at this snapshot; does not prove transcription correctness.'}
atomic(p.root/'reports/archive-integrity.json',report);print(report)
