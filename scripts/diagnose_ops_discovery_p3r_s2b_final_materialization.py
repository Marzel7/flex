"""Local-only attribution of the byte-identical S2B final materialization path."""
from __future__ import annotations
import argparse, hashlib, json, os, resource, sqlite3, statistics, time
from pathlib import Path

SURFACES=(('token_analysis',('mint','pf_ws_creator')),('pumpfun_migration_verification',('mint',)))
EXPECTED_REFERENCE_SHA256='c6d350f9fa15c4063a9d8bf89d96d27596a9a5e9ac06dc7fe282999ec3481525'

def percentile(values, p):
    if not values: return 0.0
    return sorted(values)[min(len(values)-1, int((len(values)-1)*p))]

def build(source, target, token_limit, with_index):
    target.unlink(missing_ok=True)
    src=sqlite3.connect(f'file:{source.resolve()}?mode=ro',uri=True); src.execute('pragma query_only=on')
    dst=sqlite3.connect(target); dst.execute('PRAGMA synchronous=OFF'); dst.execute('PRAGMA cache_size=-262144')
    progress_calls=0; last_progress=None; max_progress_gap=0.0
    def progress():
        nonlocal progress_calls,last_progress,max_progress_gap
        now=time.monotonic(); progress_calls+=1
        if last_progress is not None: max_progress_gap=max(max_progress_gap,now-last_progress)
        last_progress=now; return 0
    src.set_progress_handler(progress,1000); dst.set_progress_handler(progress,1000)
    schema='CREATE TABLE token_analysis (mint TEXT PRIMARY KEY, pf_ws_creator TEXT);'
    if with_index: schema+='CREATE INDEX idx_ta_pf_ws_creator ON token_analysis(pf_ws_creator);'
    schema+='CREATE TABLE pumpfun_migration_verification (mint TEXT PRIMARY KEY);'
    dst.executescript(schema)
    start_wall=time.monotonic(); start_cpu=time.process_time(); surfaces={}; commits=[]; sample_pages=[]
    for table,fields in SURFACES:
        limit=f' LIMIT {token_limit}' if table=='token_analysis' else ''
        execute_start=time.monotonic(); cur=src.execute(f"SELECT {','.join(fields)} FROM {table} ORDER BY mint ASC"+limit); order_wait=time.monotonic()-execute_start
        rows=fetch=insert=0.0; count=0; ordinal=0
        while True:
            fetch_start=time.monotonic(); batch=cur.fetchmany(5000); fetch+=time.monotonic()-fetch_start
            if not batch: break
            insert_start=time.monotonic(); dst.executemany(f"INSERT INTO {table} ({','.join(fields)}) VALUES ({','.join('?' for _ in fields)})",batch); insert+=time.monotonic()-insert_start
            commit_start=time.monotonic(); dst.commit(); elapsed=time.monotonic()-commit_start; ordinal+=1; count+=len(batch)
            commits.append({'surface':table,'ordinal':ordinal,'rows_committed':count,'seconds':elapsed})
            sample_pages.append({'surface':table,'rows_committed':count,'page_count':dst.execute('PRAGMA page_count').fetchone()[0],'database_size_bytes':target.stat().st_size})
        surfaces[table]={'rows':count,'order_wait_seconds':order_wait,'fetch_seconds':fetch,'insert_execution_seconds':insert,'rows_per_second':count/(order_wait+fetch+insert) if count else 0}
    cpu=time.process_time()-start_cpu; wall=time.monotonic()-start_wall
    settings={'journal_mode':dst.execute('PRAGMA journal_mode').fetchone()[0],'synchronous':dst.execute('PRAGMA synchronous').fetchone()[0],'cache_size':dst.execute('PRAGMA cache_size').fetchone()[0],'page_size':dst.execute('PRAGMA page_size').fetchone()[0],'page_count':dst.execute('PRAGMA page_count').fetchone()[0],'cache_spill':dst.execute('PRAGMA cache_spill').fetchone()[0],'temp_store':dst.execute('PRAGMA temp_store').fetchone()[0]}
    dst.close(); src.close(); data=target.read_bytes(); sha=hashlib.sha256(data).hexdigest()
    vals=[x['seconds'] for x in commits]
    return {'with_index_during_insert':with_index,'sha256':sha,'wall_seconds':wall,'cpu_seconds':cpu,'cpu_to_wall_ratio':cpu/wall if wall else 0,'progress_calls':progress_calls,'progress_opcode_interval':1000,'max_progress_gap_seconds':max_progress_gap,'surfaces':surfaces,'commit_summary':{'count':len(vals),'total_seconds':sum(vals),'min_seconds':min(vals),'median_seconds':statistics.median(vals),'p95_seconds':percentile(vals,.95),'max_seconds':max(vals),'samples':commits},'page_samples':sample_pages,'sqlite_settings':settings,'file_size_bytes':target.stat().st_size}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--capture-db',required=True); ap.add_argument('--output-dir',required=True); ap.add_argument('--token-row-limit',type=int,default=545000); args=ap.parse_args()
    capture=Path(args.capture_db).resolve(); out=Path(args.output_dir).resolve(); out.mkdir(parents=True,exist_ok=True)
    reference=build(capture,out/'reference.sqlite',args.token_row_limit,True)
    no_index=build(capture,out/'diagnostic_no_index.sqlite',args.token_row_limit,False)
    result={'milestone':'OPS-DISCOVERY-P3R-S2B-FINAL-MATERIALIZATION-ROOT-CAUSE-DIAGNOSIS','capture_db':str(capture),'capture_read_only':True,'token_row_limit':args.token_row_limit,'provider_calls_made':0,'production_writes':0,'reference':reference,'diagnostic_no_index':no_index,'reference_expected_sha256':EXPECTED_REFERENCE_SHA256,'reference_byte_identity_pass':reference['sha256']==EXPECTED_REFERENCE_SHA256,'index_maintenance_attribution_seconds':reference['surfaces']['token_analysis']['insert_execution_seconds']-no_index['surfaces']['token_analysis']['insert_execution_seconds'],'host_observation':{'ru_oublock':resource.getrusage(resource.RUSAGE_SELF).ru_oublock,'ru_inblock':resource.getrusage(resource.RUSAGE_SELF).ru_inblock}}
    (out/'result.json').write_text(json.dumps(result,indent=2,sort_keys=True)+'\n'); print(json.dumps(result,sort_keys=True))
    return 0 if result['reference_byte_identity_pass'] else 2
if __name__=='__main__': raise SystemExit(main())
