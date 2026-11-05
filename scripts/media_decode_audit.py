"""Check existing raw media decode and flag animated evidence needing frame coverage."""
from PIL import Image
from newbing.core import Project,atomic,now
p=Project();issues=[];animated=[];checked=0
for row in p.rows('SELECT * FROM assets ORDER BY id'):
    try:
        with Image.open(p.root/row['path']) as image:
            frames=getattr(image,'n_frames',1)
            image.load()
            if image.size!=(row['width'],row['height']):raise ValueError('dimensions differ')
            if frames>1:animated.append({'asset_id':row['id'],'path':row['path'],'frames':frames})
    except Exception as exc:issues.append({'asset_id':row['id'],'path':row['path'],'error':type(exc).__name__+': '+str(exc)[:200]})
    checked+=1
    if checked%500==0:atomic(p.root/'reports/media-decode-audit.json',{'at':now(),'checked':checked,'issues':issues,'animated_requires_frame_coverage':animated,'complete':False})
report={'at':now(),'checked':checked,'issues':issues,'animated_requires_frame_coverage':animated,'complete':True}
atomic(p.root/'reports/media-decode-audit.json',report)
print({'checked':checked,'decode_failures':len(issues),'animated':len(animated)})
