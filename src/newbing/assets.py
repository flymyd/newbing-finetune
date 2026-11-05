"""Canonical media slots, retaining every raw variant and missing gallery position."""
import html
import json


def source_media_layout(project,source_id):
    sources=project.rows('SELECT payload FROM sources WHERE id=?',(source_id,))
    source=json.loads(sources[0]['payload']) if sources else {};post=source.get('post') or {}
    if not post.get('gallery_data'):
        post=next((p for p in post.get('crosspost_parent_list',[]) if p.get('gallery_data')),post)
    gallery=(post.get('gallery_data') or {}).get('items') or [];url_slots={};media_ids={}
    for ordinal,item in enumerate(gallery):
        native=item.get('media_id');media=(post.get('media_metadata') or {}).get(native) or {}
        src=media.get('s') or {};url=src.get('u') or src.get('gif')
        if url:url_slots[html.unescape(url)]=ordinal
        if native:media_ids[native]=ordinal
    extras=[u for u in source.get('image_urls',[]) if u not in url_slots]
    if gallery:
        for i,url in enumerate(extras):url_slots[url]=len(gallery)+i
        expected=set(range(len(gallery)+len(extras)))
    else:
        tasks=project.rows("SELECT DISTINCT json_extract(payload,'$.ordinal') ordinal FROM tasks WHERE kind='download' AND json_extract(payload,'$.source_id')=? AND json_extract(payload,'$.recovery_method') IS NULL",(source_id,))
        expected={r['ordinal'] for r in tasks if r['ordinal'] is not None}
        expected.update(range(len(source.get('image_urls',[]))))
        for i,url in enumerate(source.get('image_urls',[])):url_slots[url]=i
    recovered={r['url']:r['original'] for r in project.rows("SELECT json_extract(payload,'$.url') url,json_extract(payload,'$.original_url') original FROM tasks WHERE kind='download' AND json_extract(payload,'$.source_id')=? AND json_extract(payload,'$.original_url') IS NOT NULL",(source_id,))} if gallery else {}
    return source,expected,url_slots,media_ids,recovered


def ordered_source_assets(project,source_id):
    rows=project.rows('SELECT a.*,a.id asset_id,sa.ordinal,sa.url FROM source_assets sa JOIN assets a ON a.id=sa.asset_id WHERE sa.source_id=? ORDER BY sa.ordinal,sa.url',(source_id,))
    source,expected,url_slots,media_ids,recovered=source_media_layout(project,source_id)
    selected={}
    for row in rows:
        ordinal=row['ordinal'];original_url=recovered.get(row['url'],row['url'])
        if original_url in url_slots:ordinal=url_slots[original_url]
        elif media_ids:
            matches=[slot for native,slot in media_ids.items() if '/'+native+'.' in original_url or '/'+native+'?' in original_url]
            if len(matches)==1:ordinal=matches[0]
        original=next((url for url,slot in url_slots.items() if slot==ordinal),None)
        score=(row['url']==original,row['width']*row['height'],row['bytes'],row['asset_id'])
        row['recorded_ordinal']=row['ordinal'];row['ordinal']=ordinal
        if ordinal not in selected or score>selected[ordinal][0]:selected[ordinal]=(score,row)
    return [selected[key][1] for key in sorted(selected,key=lambda value:(value is None,value or 0))]


def missing_media_ordinals(project,source_id,assets=None):
    _,expected,_,_,_=source_media_layout(project,source_id)
    assets=ordered_source_assets(project,source_id) if assets is None else assets
    actual={a['ordinal'] for a in assets}
    return sorted(expected-actual)
