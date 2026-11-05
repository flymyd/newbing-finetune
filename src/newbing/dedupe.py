from __future__ import annotations

from collections import Counter,defaultdict
from itertools import combinations
import json
import re
import unicodedata

from PIL import Image,ImageOps

from .core import atomic,digest,now


def normalize(text):
    return ' '.join(unicodedata.normalize('NFC',text).split())


def shingles(text,size=5):
    normalized=normalize(text).lower()
    tokens=re.findall(r'\w+|[^\w\s]',normalized,flags=re.UNICODE)
    # Chinese/Japanese text does not have word spaces; character shingles cover that case.
    if len(tokens)<10 and len(normalized)>60:tokens=list(normalized)
    return {tuple(tokens[i:i+size]) for i in range(max(0,len(tokens)-size+1))}


def image_dhash(path):
    with Image.open(path) as image:
        image=ImageOps.grayscale(image).resize((9,8))
        pixels=list(image.getdata())
    result=0
    for row in range(8):
        for col in range(8):result=(result<<1)|int(pixels[row*9+col]>pixels[row*9+col+1])
    return result


def find_duplicates(project):
    conversations=[json.loads(r['payload']) for r in project.rows("SELECT payload FROM conversations WHERE coalesce(json_extract(payload,'$.superseded'),0)=0")]
    replies=[];counts=Counter()
    for c in conversations:
        for index,t in enumerate(c['projected']['conversations']):
            if t['from']=='gpt' and len(normalize(t['value']))>=80:
                text=normalize(t['value']);counts[text]+=1
                replies.append((c['conversation_id'],index,text))
    candidates=defaultdict(set);features=[]
    for i,(_,_,text) in enumerate(replies):
        if counts[text]>=3 and len(text)<250:features.append(set());continue
        grams=shingles(text);features.append(grams)
        # Full shingle index gives recall; common grams (>80 replies) are not useful candidate seeds.
        for gram in grams:candidates[gram].add(i)
    pair_hits=Counter()
    for ids in candidates.values():
        if 2<=len(ids)<=80:
            for a,b in combinations(sorted(ids),2):
                if replies[a][0]!=replies[b][0]:pair_hits[(a,b)]+=1
    near=0;exact=0
    for (a,b),hits in pair_hits.items():
        aa,bb=features[a],features[b]
        if not aa or not bb or hits<10:continue
        intersection=len(aa&bb);jaccard=intersection/len(aa|bb);containment=intersection/min(len(aa),len(bb))
        if jaccard<0.6 and containment<0.85:continue
        ca,ia,ta=replies[a];cb,ib,tb=replies[b]
        kind='exact_reply_shared' if ta==tb else 'near_duplicate_candidate'
        project.execute('INSERT OR REPLACE INTO relations VALUES (?,?,?,?)',
            (min(ca,cb),max(ca,cb),kind,json.dumps({'method':'text_5gram_v1','message_indices':[ia,ib],
             'jaccard':jaccard,'containment':containment,'decision':'conservative_same_split_only_no_text_merge','created_at':now()})))
        if kind=='exact_reply_shared':exact+=1
        else:near+=1
    hashes=[]
    for asset in project.rows('SELECT id,path FROM assets ORDER BY id'):
        cache=project.root/'data/processed/image_hashes'/f"{asset['id']}.json"
        if cache.is_file():value=int(json.loads(cache.read_text())['dhash'],16)
        else:
            try:value=image_dhash(project.root/asset['path'])
            except Exception:continue
            atomic(cache,{'asset_id':asset['id'],'dhash':f'{value:016x}','method':'dhash_9x8_grayscale_v1'})
        hashes.append((asset['id'],value))
    # Image similarity produces review leads only; never directly merges or drops conversations.
    image_leads=[]
    for i,(aid,a) in enumerate(hashes):
        for bid,b in hashes[i+1:]:
            distance=(a^b).bit_count()
            if distance<=4 and a not in (0,2**64-1):
                image_leads.append({'lhs':aid,'rhs':bid,'distance':distance,'decision':'unverified_image_lead_only'})
    atomic(project.root/'data/processed/image-near-duplicate-leads.json',image_leads)
    report={'conversations':len(conversations),'substantive_replies':len(replies),'exact_reply_shared_links':exact,
            'near_text_links':near,'image_leads':len(image_leads),'method':'text_5gram_and_image_dhash_v1',
            'policy':'Text candidates conservatively share a split, without merging content. Image leads need corroboration.',
            'created_at':now()}
    atomic(project.root/'reports/P5-deduplication.json',report)
    return report
