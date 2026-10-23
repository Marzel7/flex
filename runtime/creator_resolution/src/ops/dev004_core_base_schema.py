"""DEV004 core-base.v1: explicit disposable-only metadata bootstrap. No runtime hook."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import sqlite3
import stat
import time

VERSION = "dev004.core-base.v1"
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_MAIN_BYTES = 32 * 1024 * 1024
MAX_MARKER_BYTES = 4096
COLUMNS = {"first_observed_at": "INTEGER", "create_tx_signature": "TEXT"}
PEAK_SQL = 'CREATE TABLE token_market_cap_peaks (\n  mint TEXT PRIMARY KEY,\n  peak_market_cap REAL DEFAULT 0,\n  peak_market_cap_at INTEGER DEFAULT 0, raw_peak_mc REAL DEFAULT 0, effective_peak_mc REAL DEFAULT 0, candidate_peak_mc REAL, candidate_peak_count INTEGER DEFAULT 0, raw_peak_mc_at INTEGER,\n  FOREIGN KEY(mint) REFERENCES tracked_tokens(mint)\n)'
PEAK_COLUMNS = [[0, 'mint', 'TEXT', 0, None, 1, 0], [1, 'peak_market_cap', 'REAL', 0, '0', 0, 0], [2, 'peak_market_cap_at', 'INTEGER', 0, '0', 0, 0], [3, 'raw_peak_mc', 'REAL', 0, '0', 0, 0], [4, 'effective_peak_mc', 'REAL', 0, '0', 0, 0], [5, 'candidate_peak_mc', 'REAL', 0, None, 0, 0], [6, 'candidate_peak_count', 'INTEGER', 0, '0', 0, 0], [7, 'raw_peak_mc_at', 'INTEGER', 0, None, 0, 0]]
PEAK_FK = [[0, 0, 'tracked_tokens', 'mint', 'mint', 'NO ACTION', 'NO ACTION', 'NONE']]
PEAK_INDEX = [[0, 0, 'mint', 0, 'BINARY', 1], [1, -1, None, 0, 'BINARY', 0]]
CONTRACT = hashlib.sha256(json.dumps([VERSION,COLUMNS,PEAK_SQL,PEAK_COLUMNS,PEAK_FK,PEAK_INDEX],sort_keys=True).encode()).hexdigest()

class BootstrapHold(RuntimeError):
    pass

def _hold(message):
    raise BootstrapHold(message)

def _sql(value):
    return re.findall(r"[a-z_][a-z_0-9]*|[0-9]+|[^\s]", value.lower().replace("if not exists", ""))

def _check_peak(conn):
    row=conn.execute("SELECT type,sql FROM sqlite_schema WHERE name='token_market_cap_peaks'").fetchone()
    if row is None:return False
    if row[0]!='table' or _sql(row[1])!=_sql(PEAK_SQL):_hold('INCOMPATIBLE_PEAK_DEFINITION')
    if [list(r) for r in conn.execute('PRAGMA table_xinfo(token_market_cap_peaks)')]!=PEAK_COLUMNS:_hold('INCOMPATIBLE_PEAK_COLUMNS')
    if [list(r) for r in conn.execute('PRAGMA foreign_key_list(token_market_cap_peaks)')]!=PEAK_FK:_hold('INCOMPATIBLE_PEAK_FK')
    indexes=list(conn.execute('PRAGMA index_list(token_market_cap_peaks)'))
    if len(indexes)!=1 or tuple(indexes[0][1:])!=('sqlite_autoindex_token_market_cap_peaks_1',1,'pk',0):_hold('INCOMPATIBLE_PEAK_INDEXES')
    if [list(r) for r in conn.execute('PRAGMA index_xinfo(sqlite_autoindex_token_market_cap_peaks_1)')]!=PEAK_INDEX:_hold('INCOMPATIBLE_PEAK_PK')
    if conn.execute("SELECT 1 FROM sqlite_schema WHERE type='trigger' AND tbl_name='token_market_cap_peaks'").fetchone():_hold('UNQUALIFIED_PEAK_TRIGGER')
    return True

def _declarations(sql):
    tokens=_sql(sql);start=tokens.index('(')+1;depth=0;parts=[];part=[]
    for token in tokens[start:]:
        if token==')' and depth==0:
            parts.append(part);break
        if token==',' and depth==0:parts.append(part);part=[];continue
        part.append(token)
        if token=='(':depth+=1
        elif token==')':depth-=1
    return parts


def _check_columns(conn):
    row=conn.execute("SELECT type,sql FROM sqlite_schema WHERE name='token_analysis'").fetchone()
    if row is None or row[0]!='table' or not row[1].upper().startswith('CREATE TABLE') or len(row[1])>262144:_hold('BASE_TOKEN_ANALYSIS_OWNER_REQUIRED')
    columns={r[1]:tuple(r[2:]) for r in conn.execute('PRAGMA table_xinfo(token_analysis)')}
    for name,kind in COLUMNS.items():
        if name in columns:
            if columns[name]!=(kind,0,None,0,0):_hold('INCOMPATIBLE_COLUMN:'+name)
            declarations=_declarations(row[1])
            if [part for part in declarations if part and part[0]==name]!=[[name,kind.lower()]]:_hold('INCOMPATIBLE_COLUMN_CONSTRAINT:'+name)
            if any(name in part and part[0]!=name and any(k in part for k in ['check','unique','foreign']) for part in declarations):_hold('INCOMPATIBLE_COLUMN_CONSTRAINT:'+name)
    for index in conn.execute('PRAGMA index_list(token_analysis)').fetchall():
        if index[2] and index[3]!='pk':
            safe=index[1].replace('"','""')
            if any(r[2] in COLUMNS for r in conn.execute('PRAGMA index_info("'+safe+'")')):_hold('INCOMPATIBLE_COLUMN_UNIQUENESS')
    return [name for name in COLUMNS if name not in columns]

def initialize(core: str | Path, *, disposable_root: str | Path,
               allow_disposable_v2: bool = False, fault=lambda stage:None):
    """Atomic schema DDL; post-commit marker can be recovered by revalidation.

    No scans, user_version override, default backfill, observation writes or providers.
    Caller must impose the OS file bound; CLI does this before opening SQLite.
    """
    if not allow_disposable_v2:_hold('EXPLICIT_DEV_AUTHORIZATION_REQUIRED')
    root=Path(disposable_root).absolute();path=Path(core).absolute()
    if root!=root.resolve() or not root.is_relative_to(Path('/private/tmp')) or not root.name.startswith('dev004-'):_hold('DISPOSABLE_ROOT_REQUIRED')
    if path.parent!=root or path!=path.resolve() or path.suffix!='.sqlite':_hold('EXPLICIT_DISPOSABLE_CORE_REQUIRED')
    info=path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_size>MAX_MAIN_BYTES:_hold('DISPOSABLE_FILE_IDENTITY_OR_BUDGET')
    soft,_=resource.getrlimit(resource.RLIMIT_FSIZE)
    if soft==resource.RLIM_INFINITY or soft>MAX_FILE_BYTES:_hold('QUALIFIED_OS_FILE_LIMIT_REQUIRED')
    marker=path.with_name(path.name+'.'+VERSION+'.json');temp=marker.with_suffix(marker.suffix+'.tmp')
    for p in [marker,temp,path.with_name(path.name+'-wal'),path.with_name(path.name+'-shm'),path.with_name(path.name+'-journal')]:
        if p.exists() or p.is_symlink():
            st=p.lstat()
            limit=MAX_MARKER_BYTES if p in [marker,temp] else MAX_MAIN_BYTES
            if not stat.S_ISREG(st.st_mode) or st.st_nlink!=1 or st.st_size>limit:_hold('SIDECAR_IDENTITY_OR_BUDGET')
    prior=None
    if marker.exists():
        try:prior=json.loads(marker.read_text())
        except (ValueError,OSError):_hold('INVALID_VERSION_MARKER')
        if not isinstance(prior,dict):_hold('INVALID_VERSION_MARKER')
        if prior.get('version')!=VERSION or prior.get('contract')!=CONTRACT or prior.get('identity')!=[info.st_dev,info.st_ino]:_hold('INCOMPATIBLE_VERSION_MARKER')
    conn=sqlite3.connect(path.as_uri()+'?mode=rw',uri=True,timeout=1,isolation_level=None)
    try:
        conn.execute('PRAGMA busy_timeout=1000');conn.execute('PRAGMA foreign_keys=ON')
        # An existing accepted owner supplies tracked_tokens; this step never invents it.
        parent={r[1]:r for r in conn.execute('PRAGMA table_xinfo(tracked_tokens)')}
        if 'mint' not in parent or parent['mint'][2]!='TEXT' or parent['mint'][5]!=1:_hold('TRACKED_TOKENS_OWNER_REQUIRED')
        if sum(bool(r[5]) for r in parent.values())!=1:_hold('INCOMPATIBLE_TRACKED_TOKENS_PK')
        conn.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
        deadline=time.monotonic()+2
        conn.execute('BEGIN IMMEDIATE')
        missing=_check_columns(conn);present=_check_peak(conn)
        if prior is not None and (missing or not present):_hold('VERSION_MARKER_SCHEMA_MISMATCH')
        fault('before_ddl')
        for name in missing:
            conn.execute('ALTER TABLE token_analysis ADD COLUMN '+name+' '+COLUMNS[name]);fault('after_column:'+name)
        if not present:conn.execute(PEAK_SQL)
        fault('after_peak')
        if _check_columns(conn) or not _check_peak(conn):_hold('POSTCONDITION_FAILED')
        fault('before_commit');conn.execute('COMMIT');fault('after_commit')
        if prior is not None:return {'status':'ALREADY_INITIALIZED','version':VERSION,'contract':CONTRACT}
        payload={'version':VERSION,'contract':CONTRACT,'identity':[info.st_dev,info.st_ino]}
        encoded=(json.dumps(payload,sort_keys=True)+'\n').encode()
        if len(encoded)>MAX_MARKER_BYTES:_hold('VERSION_MARKER_BUDGET')
        fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'wb') as stream:stream.write(encoded);stream.flush();os.fsync(stream.fileno())
        fault('before_marker_replace');os.replace(temp,marker)
        directory=os.open(root,os.O_RDONLY)
        try:os.fsync(directory)
        finally:os.close(directory)
        return {'status':'INITIALIZED','version':VERSION,'contract':CONTRACT}
    except BaseException:
        if conn.in_transaction:conn.rollback()
        raise
    finally:conn.close()

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--core',required=True);parser.add_argument('--disposable-root',required=True)
    parser.add_argument('--allow-disposable-v2',action='store_true');args=parser.parse_args()
    soft,hard=resource.getrlimit(resource.RLIMIT_FSIZE)
    resource.setrlimit(resource.RLIMIT_FSIZE,(min(soft,MAX_FILE_BYTES) if soft!=resource.RLIM_INFINITY else MAX_FILE_BYTES,hard))
    print(json.dumps(initialize(args.core,disposable_root=args.disposable_root,allow_disposable_v2=args.allow_disposable_v2),sort_keys=True))

if __name__=='__main__':main()
