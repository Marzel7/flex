"""Benchmark final-snapshot stages against an existing local capture artifact only."""
from __future__ import annotations

import argparse, json, os, sqlite3, time
from pathlib import Path

SURFACES=(('token_analysis',('mint','pf_ws_creator')),('pumpfun_migration_verification',('mint',)))

def main() -> int:
    ap=argparse.ArgumentParser()
    ap.add_argument('--capture-db',required=True)
    ap.add_argument('--output-dir',required=True)
    ap.add_argument('--token-row-limit',type=int,required=True)
    ap.add_argument('--variants',default='qualified,baseline',help='comma-separated: qualified,baseline,qualified_guarded')
    args=ap.parse_args()
    capture=Path(args.capture_db).resolve(); outdir=Path(args.output_dir).resolve(); outdir.mkdir(parents=True,exist_ok=True)
    source=sqlite3.connect(f'file:{capture}?mode=ro',uri=True); source.execute('pragma query_only=on')
    available=source.execute('select count(*) from token_analysis').fetchone()[0]
    if args.token_row_limit<=0 or args.token_row_limit>available: raise SystemExit('token-row-limit must be within capture row count')
    variants={'qualified':(-262144,'OFF',False),'baseline':(None,'FULL',False),'qualified_guarded':(-262144,'OFF',True)}
    selected=args.variants.split(',')
    if not selected or any(name not in variants for name in selected): raise SystemExit('unknown benchmark variant')
    results=[]
    for name in selected:
        cache_size,synchronous,guarded=variants[name]
        target=outdir/(name+'.sqlite'); target.unlink(missing_ok=True)
        progress_calls=0; guard_deadline=time.monotonic()+3600.0
        def progress():
            nonlocal progress_calls
            progress_calls+=1
            return int(progress_calls>500000 or time.monotonic()>=guard_deadline)
        dest=sqlite3.connect(target); dest.execute(f'PRAGMA synchronous={synchronous}')
        if cache_size is not None: dest.execute(f'PRAGMA cache_size={cache_size}')
        if guarded:
            source.set_progress_handler(progress,1000); dest.set_progress_handler(progress,1000)
        schema_start=time.monotonic(); dest.executescript('CREATE TABLE token_analysis (mint TEXT PRIMARY KEY, pf_ws_creator TEXT); CREATE INDEX idx_ta_pf_ws_creator ON token_analysis(pf_ws_creator); CREATE TABLE pumpfun_migration_verification (mint TEXT PRIMARY KEY);'); schema_seconds=time.monotonic()-schema_start
        timing={'variant':name,'schema_seconds':schema_seconds,'surfaces':{}}
        for table,fields in SURFACES:
            limit=' LIMIT '+str(args.token_row_limit) if table=='token_analysis' else ''
            read_seconds=insert_seconds=commit_seconds=0.0; rows=commits=0; batch=[]
            cur=source.execute(f"SELECT {','.join(fields)} FROM {table} ORDER BY mint ASC"+limit)
            while True:
                read_start=time.monotonic(); chunk=cur.fetchmany(5000); read_seconds+=time.monotonic()-read_start
                if not chunk: break
                insert_start=time.monotonic(); dest.executemany(f"INSERT INTO {table} ({','.join(fields)}) VALUES ({','.join('?' for _ in fields)})",chunk); insert_seconds+=time.monotonic()-insert_start
                commit_start=time.monotonic(); dest.commit(); commit_seconds+=time.monotonic()-commit_start
                rows+=len(chunk); commits+=1
            timing['surfaces'][table]={'rows':rows,'commits':commits,'ordered_read_seconds':read_seconds,'insert_seconds':insert_seconds,'commit_seconds':commit_seconds}
        dest.close()
        if guarded: source.set_progress_handler(None,0)
        timing['progress_calls']=progress_calls; timing['file_size_bytes']=target.stat().st_size; results.append(timing)
    source.close()
    result={'milestone':'OPS-DISCOVERY-P3R-S2B-FINAL-MATERIALIZATION-QUALIFICATION','capture_db':str(capture),'capture_read_only':True,'token_row_limit':args.token_row_limit,'provider_calls_made':0,'production_writes':0,'variants':results}
    (outdir/'result.json').write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
    print(json.dumps(result,sort_keys=True)); return 0

if __name__=='__main__': raise SystemExit(main())
