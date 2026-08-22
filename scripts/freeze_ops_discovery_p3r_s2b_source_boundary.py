"""Create a minimal digest-bound S2B source snapshot without population execution."""
from __future__ import annotations

import argparse, hashlib, json, os, sqlite3, sys, time
from pathlib import Path

SURFACES = (
    ('token_analysis', ('mint', 'pf_ws_creator'), 'mint ASC'),
    ('pumpfun_migration_verification', ('mint',), 'mint ASC'),
)


def canon(row: tuple) -> bytes:
    return (json.dumps(row, separators=(',', ':'), ensure_ascii=True) + '\n').encode()


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp=path.with_suffix(path.suffix+'.tmp'); tmp.write_text(json.dumps(value,indent=2,sort_keys=True)+'\n'); os.replace(tmp,path)


def digest_surface(conn: sqlite3.Connection, table: str, fields: tuple[str, ...], order: str) -> tuple[int, str]:
    digest=hashlib.sha256(); count=0
    for row in conn.execute(f"SELECT {','.join(fields)} FROM {table} ORDER BY {order}"):
        digest.update(canon(row)); count+=1
    return count,digest.hexdigest()


def main() -> int:
    ap=argparse.ArgumentParser()
    ap.add_argument('--source-db', default='database/flex_complete_database.db')
    ap.add_argument('--snapshot-db', required=True)
    ap.add_argument('--audit-path', required=True)
    ap.add_argument('--wall-seconds', type=float, default=300.0)
    ap.add_argument('--max-progress-calls', type=int, default=500000)
    args=ap.parse_args(); start=time.monotonic(); audit=Path(args.audit_path); snapshot=Path(args.snapshot_db)
    cleanup_reserve_seconds=30.0; deadline=start+args.wall_seconds; execution_deadline=deadline-cleanup_reserve_seconds
    base={'milestone':'OPS-DISCOVERY-P3R-S2B-SOURCE-SNAPSHOT','provider_calls_made':0,'production_writes':0,
          'source_db':args.source_db,'read_mode':'sqlite_uri_mode_ro_query_only_single_transaction','bounds':{'wall_seconds':args.wall_seconds,'cleanup_reserve_seconds':cleanup_reserve_seconds,'execution_deadline_seconds_from_start':args.wall_seconds-cleanup_reserve_seconds,'max_progress_calls':args.max_progress_calls,'progress_opcode_interval':1000},
          'canonicalization':{'encoding':'utf-8','row_format':'json array compact separators','field_order':'SURFACES declaration','row_order':'primary key mint ASC','null_representation':'JSON null','digest':'SHA-256 over newline-delimited canonical rows'},
          'population_query_executed':False,'identities_selected_or_frozen':False,'cohort_formed':False}
    telemetry={'stages':{},'final_materialization':{'surfaces':{}}}
    def checkpoint(stage: str, state: str, **details: object) -> None:
        now=time.monotonic()
        entry=telemetry['stages'].setdefault(stage,{})
        entry[state+'_elapsed_seconds']=round(now-start,6)
        entry.update(details)
        atomic_json(audit,{**base,'status':'STARTED','telemetry':telemetry})
    atomic_json(audit,{**base,'status':'STARTED','telemetry':telemetry})
    calls=0
    def progress():
        nonlocal calls
        calls+=1
        return int(calls>args.max_progress_calls or time.monotonic()>=execution_deadline)
    def require_budget(stage: str, minimum_remaining_seconds: float) -> None:
        if execution_deadline-time.monotonic()<minimum_remaining_seconds:
            raise TimeoutError(f'BOUND_BUDGET_EXCEEDED:{stage}')
    def guard(conn: sqlite3.Connection) -> None:
        conn.set_progress_handler(progress,1000)
    try:
        checkpoint('source_capture','started')
        source=sqlite3.connect(f'file:{Path(args.source_db).resolve()}?mode=ro',uri=True,timeout=5)
        source.execute('pragma query_only=on'); source.execute('begin')
        guard(source)
        identity=os.stat(args.source_db)
        source_meta={'device':identity.st_dev,'inode':identity.st_ino,'size_bytes':identity.st_size,'mtime_ns':identity.st_mtime_ns,'schema_version':source.execute('pragma schema_version').fetchone()[0], 'data_version':source.execute('pragma data_version').fetchone()[0]}
        snapshot.parent.mkdir(parents=True,exist_ok=True)
        tmp=snapshot.with_suffix(snapshot.suffix+'.tmp'); tmp.unlink(missing_ok=True)
        stage=snapshot.with_suffix(snapshot.suffix+'.capture.sqlite'); stage.unlink(missing_ok=True)
        capture=sqlite3.connect(stage)
        capture.execute('PRAGMA journal_mode=OFF'); capture.execute('PRAGMA synchronous=OFF')
        capture.executescript('CREATE TABLE token_analysis (mint TEXT, pf_ws_creator TEXT); CREATE TABLE pumpfun_migration_verification (mint TEXT);')
        capture.execute('BEGIN')
        high_waters={}
        for table,fields,order in SURFACES:
            batch=[]
            for row in source.execute(f"SELECT {','.join(fields)} FROM {table}"):
                batch.append(row)
                if len(batch)>=5000:
                    capture.executemany(f"INSERT INTO {table} ({','.join(fields)}) VALUES ({','.join('?' for _ in fields)})",batch); batch=[]
            if batch: capture.executemany(f"INSERT INTO {table} ({','.join(fields)}) VALUES ({','.join('?' for _ in fields)})",batch)
            high_waters[table]=source.execute(f'SELECT max(rowid) FROM {table}').fetchone()[0]
        capture_rows=sum(capture.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] for table,_,_ in SURFACES)
        capture.commit(); source.close(); capture.close(); checkpoint('source_capture','completed',rows_captured=capture_rows)
        require_budget('final_materialization',180.0)
        checkpoint('final_materialization','started')
        out=sqlite3.connect(tmp)
        out.execute('PRAGMA synchronous=OFF'); out.execute('PRAGMA cache_size=-262144'); guard(out)
        out.executescript('CREATE TABLE token_analysis (mint TEXT PRIMARY KEY, pf_ws_creator TEXT); CREATE INDEX idx_ta_pf_ws_creator ON token_analysis(pf_ws_creator); CREATE TABLE pumpfun_migration_verification (mint TEXT PRIMARY KEY);')
        staged=sqlite3.connect(f'file:{stage.resolve()}?mode=ro',uri=True); staged.execute('pragma query_only=on'); guard(staged)
        total={}
        for table,fields,order in SURFACES:
            surface_started=time.monotonic(); committed_rows=0; commits=0
            telemetry['final_materialization']['surfaces'][table]={'started_elapsed_seconds':round(surface_started-start,6),'committed_rows':0,'commits':0}
            checkpoint('final_materialization','progress',active_surface=table)
            digest=hashlib.sha256(); count=0; batch=[]
            for row in staged.execute(f"SELECT {','.join(fields)} FROM {table} ORDER BY {order}"):
                digest.update(canon(row)); count+=1; batch.append(row)
                if len(batch)>=5000:
                    out.executemany(f"INSERT INTO {table} ({','.join(fields)}) VALUES ({','.join('?' for _ in fields)})",batch); out.commit(); committed_rows+=len(batch); commits+=1; batch=[]
                    if commits % 20 == 0:
                        telemetry['final_materialization']['surfaces'][table].update({'committed_rows':committed_rows,'commits':commits,'last_commit_elapsed_seconds':round(time.monotonic()-start,6)})
                        checkpoint('final_materialization','progress',active_surface=table)
            if batch:
                out.executemany(f"INSERT INTO {table} ({','.join(fields)}) VALUES ({','.join('?' for _ in fields)})",batch); out.commit(); committed_rows+=len(batch); commits+=1
            telemetry['final_materialization']['surfaces'][table].update({'completed_elapsed_seconds':round(time.monotonic()-start,6),'committed_rows':committed_rows,'commits':commits,'elapsed_seconds':round(time.monotonic()-surface_started,6)})
            checkpoint('final_materialization','progress',active_surface=table)
            total[table]={'columns':list(fields),'row_count':count,'sha256':digest.hexdigest(),'source_rowid_high_water':high_waters[table]}
        staged.close(); out.close(); os.replace(tmp,snapshot); checkpoint('final_materialization','completed')
        require_budget('replay_and_boundary',30.0)
        checkpoint('replay','started')
        replay=sqlite3.connect(f'file:{snapshot.resolve()}?mode=ro',uri=True); replay.execute('pragma query_only=on'); guard(replay)
        replay_results={}
        for table,fields,order in SURFACES:
            count,digest=digest_surface(replay,table,fields,order); replay_results[table]={'row_count':count,'sha256':digest}
        replay.close(); checkpoint('replay','completed')
        identical=all(total[t]['row_count']==replay_results[t]['row_count'] and total[t]['sha256']==replay_results[t]['sha256'] for t,_,_ in SURFACES)
        require_budget('snapshot_hash_and_audit',15.0)
        checkpoint('digest_and_audit','started')
        boundary={'source_meta':source_meta,'surfaces':total,'snapshot_path':str(snapshot),'snapshot_sha256':hashlib.sha256(snapshot.read_bytes()).hexdigest()}
        boundary_digest=hashlib.sha256(json.dumps(boundary,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        checkpoint('digest_and_audit','completed')
        result={**base,'status':'COMPLETE' if identical else 'HOLD','source_identity_at_read_open':source_meta,'surfaces':total,'replay_surfaces':replay_results,'replay_identical':identical,'boundary_sha256':boundary_digest,'snapshot_path':str(snapshot),'snapshot_sha256':boundary['snapshot_sha256'],'progress_calls':calls,'elapsed_seconds':round(time.monotonic()-start,6),'telemetry':telemetry}
        atomic_json(audit,result); print(json.dumps(result,sort_keys=True)); return 0 if identical else 2
    except Exception as exc:
        bounded=isinstance(exc,TimeoutError) or 'interrupted' in str(exc).lower()
        result={**base,'status':'HOLD','failure_reason':'BOUND_EXCEEDED' if bounded else 'EXECUTION_ERROR','exception_type':type(exc).__name__,'exception':str(exc),'progress_calls':calls,'elapsed_seconds':round(time.monotonic()-start,6),'telemetry':telemetry}
        atomic_json(audit,result); print(json.dumps(result,sort_keys=True),file=sys.stderr); return 3

if __name__=='__main__': raise SystemExit(main())
