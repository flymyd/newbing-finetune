from __future__ import annotations

import argparse
import json
import shutil

from .core import Project,ROOT


def preview(args,root=ROOT):
    """Read-only plan: no Project construction, task enqueue, credential read or network."""
    import sqlite3
    import tomllib
    from pathlib import Path
    root=Path(root);policy=tomllib.loads((root/'config/pipeline.toml').read_text())
    db_path=root/policy['paths']['state_db'];counts=[]
    if db_path.is_file():
        with sqlite3.connect(db_path.as_uri()+'?mode=ro',uri=True) as db:
            db.row_factory=sqlite3.Row
            counts=[dict(row) for row in db.execute('SELECT kind,status,count(*) count FROM tasks GROUP BY kind,status')]
    return {'dry_run':True,'command':args.command,'arguments':vars(args),'queue_counts':counts,
            'planning_only':policy['planning_only'],'network_requests':0,'model_requests':0,'writes':0,
            'scope':'Existing queue snapshot; discovery and eligibility are computed only during authorized execution.'}


def main():
    parser=argparse.ArgumentParser(description='Evidence-preserving Bing historical archive')
    sub=parser.add_subparsers(dest='command',required=True)
    doctor=sub.add_parser('doctor');doctor.add_argument('--offline',action='store_true')
    sub.add_parser('status')
    probe=sub.add_parser('probe');probe.add_argument('--profile',default='flymyd',choices=['flymyd'])
    baseline=sub.add_parser('import-baseline');baseline.add_argument('--source',required=True)
    discover=sub.add_parser('discover');discover.add_argument('--source',required=True);discover.add_argument('--window',default=None)
    dump=sub.add_parser('ingest-dump');dump.add_argument('--path',required=True);dump.add_argument('--source',required=True);dump.add_argument('--window',default='early')
    comments=sub.add_parser('comments');comments.add_argument('--limit',type=int,default=100)
    sub.add_parser('recover-baseline-posts')
    sub.add_parser('dedupe')
    references=sub.add_parser('references');references.add_argument('--limit',type=int,default=100)
    recovery=sub.add_parser('recover-media');recovery.add_argument('--limit',type=int,default=100)
    archive=sub.add_parser('recover-archive-media');archive.add_argument('--limit',type=int,default=100)
    repair=sub.add_parser('repair');repair.add_argument('--limit',type=int,default=20)
    download=sub.add_parser('download');download.add_argument('--limit',type=int,default=100);download.add_argument('--workers',type=int,default=2);download.add_argument('--dry-run',action='store_true')
    download.add_argument('--source-kind',default=None)
    download.add_argument('--recovery-method',default=None)
    for name in ('classify','transcribe'):
        command=sub.add_parser(name);command.add_argument('--limit',type=int,default=50);command.add_argument('--workers',type=int,default=2);command.add_argument('--dry-run',action='store_true')
        command.add_argument('--cohort',default=None)
    verify=sub.add_parser('verify');verify.add_argument('--sample',type=int,default=50);verify.add_argument('--workers',type=int,default=2);verify.add_argument('--dry-run',action='store_true')
    verify.add_argument('--conversation',default=None)
    verify.add_argument('--calibration',action='store_true')
    verify.add_argument('--bucket',choices=['early','transition','later_mar_apr','later_2023','unknown'],default=None)
    review=sub.add_parser('review');review.add_argument('action',choices=['build','import']);review.add_argument('--path',type=str)
    for name in ('export','validate'):
        command=sub.add_parser(name);command.add_argument('--release',required=True)
    rebuild=sub.add_parser('reconstruct');rebuild.add_argument('--limit',type=int,default=100000);rebuild.add_argument('--dry-run',action='store_true')
    resume=sub.add_parser('resume');resume.add_argument('--run',default=None)
    for command in sub.choices.values():
        if '--dry-run' not in command._option_string_actions:command.add_argument('--dry-run',action='store_true')
    args=parser.parse_args()
    if args.dry_run:
        print(json.dumps(preview(args),ensure_ascii=False,indent=2));return
    p=Project()
    if args.command=='doctor':
        from dotenv import dotenv_values
        cfg=p.config['models']['profiles']['flymyd']
        result={'planning_only':p.policy['planning_only'],'free_disk_bytes':shutil.disk_usage(p.root).free,
                'credential_present':bool(dotenv_values(p.root/cfg['env_file']).get(cfg['api_key_env'])),
                'schema_version':p.rows("SELECT value FROM meta WHERE key='schema_version'")[0]['value'],
                'network_requests':0}
    elif args.command=='status':
        result=p.status()
    elif args.command=='probe':
        from .llm import probe
        result=probe(p)
    elif args.command=='import-baseline':
        from .sources import import_baseline
        result=import_baseline(p,args.source)
    elif args.command=='discover':
        from .sources import discover_author,discover_page
        kind=p.source_config(args.source)['kind']
        if kind=='reddit':
            from .reddit import discover_reddit
            result=discover_reddit(p,args.source,args.window or 'early')
        else:
            result=discover_author(p,args.source) if kind=='author_archive' else discover_page(p,args.source)
    elif args.command=='ingest-dump':
        from .reddit import ingest_dump
        result=ingest_dump(p,args.path,args.source,args.window)
    elif args.command=='comments':
        from .reddit import discover_comments
        result=discover_comments(p,args.limit)
    elif args.command=='recover-baseline-posts':
        from .reddit import recover_baseline_posts
        result=recover_baseline_posts(p)
    elif args.command=='dedupe':
        from .dedupe import find_duplicates
        result=find_duplicates(p)
    elif args.command=='references':
        from .references import discover_references
        result=discover_references(p,args.limit)
    elif args.command=='recover-media':
        from .media_recovery import recover_external_media,queue_preview_fallbacks
        result={'albums':recover_external_media(p,args.limit),'previews':queue_preview_fallbacks(p)}
    elif args.command=='recover-archive-media':
        from .media_recovery import recover_archive_media
        result=recover_archive_media(p,args.limit)
    elif args.command=='repair':
        from .repair import repair
        result=repair(p,args.limit)
    elif args.command=='download':
        from .network import run_downloads
        result=run_downloads(p,args.limit,args.workers,args.dry_run,args.source_kind,args.recovery_method)
    elif args.command in ('classify','transcribe'):
        from .model_tasks import run_model_tasks
        result=run_model_tasks(p,args.command,args.limit,args.workers,args.dry_run,args.cohort)
    elif args.command=='verify':
        from .verification import run_verify
        result=run_verify(p,args.sample,args.workers,args.dry_run,args.conversation,args.calibration,args.bucket)
    elif args.command=='review':
        from .verification import build_review
        if args.action=='build':result=build_review(p)
        else:
            from pathlib import Path
            from .corrections import import_correction
            if not args.path:parser.error('review import needs --path')
            result=import_correction(p,Path(args.path))
    elif args.command in ('export','validate'):
        from .exporter import export_release,validate_release
        result=export_release(p,args.release) if args.command=='export' else validate_release(p,args.release)
    elif args.command=='reconstruct':
        from .cleaning import reconstruct_baselines,reconstruct_transcriptions,save_corpus,derive_continuous_segments
        if args.dry_run:result={'dry_run':True}
        else:
            from .corrections import restore_corrections
            result={'baseline':reconstruct_baselines(p,args.limit),'model':reconstruct_transcriptions(p),'restored_corrections':restore_corrections(p)}
            result['segments']=derive_continuous_segments(p)
            result['corpus_records']=save_corpus(p)
    else:
        result={'leases_recovered':p.recover(),'run_requested':args.run}
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
