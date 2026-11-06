"""Report observed coverage without promoting partial enumeration into completeness."""
import json
from datetime import datetime,timedelta,timezone
from newbing.core import Project,atomic,now
p=Project();matrix=[]
for cfg in p.config['sources']['sources']:
    windows=cfg.get('windows') or [None]
    for window in windows:
        sid=cfg['id']+(':'+window if window else '')
        rows=p.rows('SELECT state,payload,updated FROM source_status WHERE id=?',(sid,))
        state=rows[0]['state'] if rows else 'not_started'
        data=json.loads(rows[0]['payload']) if rows else {}
        observed=None
        if cfg['kind']=='reddit' and window:
            bounds=p.policy['windows'][window]
            start=bounds['start_date'];end=(datetime.fromisoformat(bounds['end_date_inclusive'])+timedelta(days=1)).strftime('%Y-%m-%d')
            observed=p.rows("SELECT count(*) n FROM sources WHERE kind='reddit_post' AND lower(json_extract(payload,'$.post.subreddit'))=? AND json_extract(payload,'$.posted_at')>=? AND json_extract(payload,'$.posted_at')<?",(cfg['subreddit'].lower(),start,end))[0]['n']
        matrix.append({'source':cfg['id'],'kind':cfg['kind'],'window':window,'status':state,'last_attempt':data,
                       'archived_distinct_posts_in_window':observed,'status_updated':rows[0]['updated'] if rows else None})
report={'at':now(),'goal_status':'completed_within_authorized_scope','all_sources_terminal':False,'coverage':matrix,
        'limits':['complete_for_available_listing means only the archived API listing and exact window, not all historical Reddit','Raw archived posts can exceed the most recent failed enumeration count','D uses targeted references; no global video/private collection','Final conversation novelty and ShareGPT eligibility are separate from post coverage']}
atomic(p.root/'reports/coverage-matrix.json',report)
text='# P6 范围扩展、媒体补救与覆盖矩阵报告\n\n更新时间：'+report['at']+'（已按有限资源与访问边界完成本轮覆盖，未将受限/429来源冒充为全网穷尽）。\n\n| 来源 | 窗口 | 当前状态 | 本地独立原帖数 |\n| --- | --- | --- | --- |\n'
for row in matrix:text+=f"| {row['source']} | {row['window'] or '-'} | {row['status']} | {row['archived_distinct_posts_in_window'] if row['archived_distinct_posts_in_window'] is not None else '-'} |\n"
text+='\n`last_attempt` 原始计数、HTTP 429 冷却记录、外链相册/Wayback 补救回执与限制详见 [`reports/coverage-matrix.json`](file:///Users/myd/newbing/reports/coverage-matrix.json)。429/不可达/部分枚举如实保留为 `partial` 或 `deferred_source_unavailable`，不写成来源耗尽；完整索引也不意味着截图均存活、文本均转写或全部合格。\n'
atomic(p.root/'reports/P6-coverage.md',text,raw=True)
print({'coverage_cells':len(matrix),'at':report['at']})
