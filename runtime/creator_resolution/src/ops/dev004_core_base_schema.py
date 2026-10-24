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

def _production_profile(path, root, role, schema_authority):
    """Explicit frozen profile; never a fallback for an invalid DEV path."""
    import stat
    def fail(code):
        raise RuntimeError('PRODUCTION_BOOTSTRAP_' + code)
    def physical(value):
        if not isinstance(value, str) or not os.path.isabs(value) or str(Path(value).resolve()) != value or '..' in Path(value).parts:
            fail('PHYSICAL_PATH_REQUIRED')
        return Path(value)
    def read(value, cap):
        p = physical(value)
        info = p.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > cap:
            fail('METADATA_IDENTITY_OR_CAPACITY')
        raw = p.read_bytes()
        if len(raw) > cap or p.stat().st_ino != info.st_ino:
            fail('METADATA_CHANGED')
        return json.loads(raw), raw
    frozen_path = os.environ.get('DEV004_PRODUCTION_BOOTSTRAP_MANIFEST')
    frozen_hash = os.environ.get('DEV004_PRODUCTION_BOOTSTRAP_SHA256')
    if not frozen_path or not frozen_hash:
        fail('FROZEN_MANIFEST_REQUIRED')
    frozen, frozen_raw = read(frozen_path, 65536)
    if hashlib.sha256(frozen_raw).hexdigest() != frozen_hash:
        fail('FROZEN_HASH_MISMATCH')
    b = frozen.get('production_bootstrap')
    required = {'version','root','generation','pair_manifest_path','pair_manifest_sha256','authority_path','transfer_owner','stores','schema_authority'}
    if not isinstance(b, dict) or set(b) != required or type(b['version']) is not int or b['version'] != 1:
        fail('PROFILE_VERSION')
    root = physical(str(root)); path = physical(str(path))
    if str(root) != b['root'] or path.parent != root or path.suffix != '.sqlite' or not root.is_dir():
        fail('ROOT_OR_TARGET_MISMATCH')
    if not isinstance(b['stores'], dict) or set(b['stores']) != {'core','ops'} or not isinstance(b['schema_authority'], dict) or set(b['schema_authority']) != {'core','ops'} or b['schema_authority'][role] != schema_authority:
        fail('SCHEMA_AUTHORITY_MISMATCH')
    if os.environ.get('DEV004_PAIRED_BINDING_REQUIRED') != '1' or os.environ.get('FLEX_DB_PAIR_ROLE') != 'transfer' or not b['transfer_owner'] or os.environ.get('FLEX_DB_PAIR_OWNER') != b['transfer_owner']:
        fail('EXCLUSIVE_TRANSFER_OWNER_REQUIRED')
    if os.environ.get('FLEX_DB_PAIR_MANIFEST') != b['pair_manifest_path'] or os.environ.get('FLEX_DB_PAIR_AUTHORITY') != b['authority_path']:
        fail('PAIR_AUTHORITY_BINDING_MISMATCH')
    pair, pair_raw = read(b['pair_manifest_path'], 8192)
    owner, owner_raw = read(b['authority_path'], 8192)
    if hashlib.sha256(pair_raw).hexdigest() != b['pair_manifest_sha256'] or pair.get('generation') != b['generation'] or pair.get('stores') != b['stores'] or pair.get('predecessor') is None:
        fail('PAIRED_GENERATION_MISMATCH')
    if owner.get('phase') != 'TRANSFER' or owner.get('owner') != b['transfer_owner'] or owner.get('transfer_complete') is not False:
        fail('BOOTSTRAP_PHASE_REQUIRED')
    from src.core.paired_binding_guard import validate_pair, database_identity
    for kind in ['core','ops']:
        record = b['stores'][kind]
        p = physical(record.get('path'))
        info = p.lstat()
        if p.parent != root or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or database_identity(str(p), b['generation'], kind) != record:
            fail('DATABASE_IDENTITY_MISMATCH')
    if str(path) != b['stores'][role]['path'] or b['stores']['core']['path'] == b['stores']['ops']['path']:
        fail('WRONG_DATABASE_ROLE')
    if validate_pair(write_path=str(path), purpose='write') != b['generation']:
        fail('GENERATION_NOT_CURRENT')
    soft, hard = resource.getrlimit(resource.RLIMIT_FSIZE)
    if soft == resource.RLIM_INFINITY or hard == resource.RLIM_INFINITY or soft > 64*1024*1024 or hard >= 500000000 or path.stat().st_size >= 64*1024*1024:
        fail('KERNEL_FILE_LIMIT_REQUIRED')
    space = os.statvfs(root)
    if space.f_bavail * space.f_frsize < 2_000_000_000 + 16*1024*1024:
        fail('DISK_FLOOR')
    if read(frozen_path,65536)[1] != frozen_raw or read(b['authority_path'],8192)[1] != owner_raw:
        fail('AUTHORITY_CHANGED')
    return b['generation']

def _sql(value):
    return re.findall(r"[a-z_][a-z_0-9]*|[0-9]+|[^\s]", value.lower().replace("if not exists", ""))

def _check_peak(conn, parent="tracked_tokens"):
    expected_sql=PEAK_SQL.replace("REFERENCES tracked_tokens(mint)", "REFERENCES "+parent+"(mint)")
    expected_fk=[[0,0,parent,"mint","mint","NO ACTION","NO ACTION","NONE"]]
    row=conn.execute("SELECT type,sql FROM sqlite_schema WHERE name='token_market_cap_peaks'").fetchone()
    if row is None:return False
    if row[0]!='table' or _sql(row[1])!=_sql(expected_sql):_hold('INCOMPATIBLE_PEAK_DEFINITION')
    if [list(r) for r in conn.execute('PRAGMA table_xinfo(token_market_cap_peaks)')]!=PEAK_COLUMNS:_hold('INCOMPATIBLE_PEAK_COLUMNS')
    if [list(r) for r in conn.execute('PRAGMA foreign_key_list(token_market_cap_peaks)')]!=expected_fk:_hold('INCOMPATIBLE_PEAK_FK')
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
               allow_disposable_v2: bool = False, fault=lambda stage:None,
               price_identity_parent: str = "tracked_tokens"):
    """Atomic schema DDL; post-commit marker can be recovered by revalidation.

    No scans, user_version override, default backfill, observation writes or providers.
    Caller must impose the OS file bound; CLI does this before opening SQLite.
    """
    # Explicit fresh-V2 profile: historical price identity need not be monitored.
    # Default/installed bootstrap authority remains unchanged. Never reparent data.
    if price_identity_parent not in ("tracked_tokens","token_analysis"):_hold('UNSUPPORTED_PRICE_IDENTITY_PARENT')
    version=VERSION if price_identity_parent=="tracked_tokens" else VERSION+".canonical-price-identity"
    contract=CONTRACT if price_identity_parent=="tracked_tokens" else hashlib.sha256((CONTRACT+":token_analysis").encode()).hexdigest()
    peak_sql=PEAK_SQL.replace("REFERENCES tracked_tokens(mint)", "REFERENCES "+price_identity_parent+"(mint)")
    if not allow_disposable_v2:_hold('EXPLICIT_DEV_AUTHORIZATION_REQUIRED')
    root=Path(disposable_root).absolute();path=Path(core).absolute()
    production_profile = bool(os.environ.get('DEV004_PRODUCTION_BOOTSTRAP_MANIFEST') or os.environ.get('DEV004_PRODUCTION_BOOTSTRAP_SHA256'))
    authority = {'contract':contract,'owner_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    if production_profile:
        _production_profile(path, root, 'core', authority)
    elif root!=root.resolve() or not root.is_relative_to(Path('/private/tmp')) or not root.name.startswith('dev004-'):
        _hold('DISPOSABLE_ROOT_REQUIRED')
    if path.parent!=root or path!=path.resolve() or path.suffix!='.sqlite':_hold('EXPLICIT_DISPOSABLE_CORE_REQUIRED')
    info=path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_size>MAX_MAIN_BYTES:_hold('DISPOSABLE_FILE_IDENTITY_OR_BUDGET')
    soft,_=resource.getrlimit(resource.RLIMIT_FSIZE)
    if soft==resource.RLIM_INFINITY or soft>MAX_FILE_BYTES:_hold('QUALIFIED_OS_FILE_LIMIT_REQUIRED')
    marker=path.with_name(path.name+'.'+version+'.json');temp=marker.with_suffix(marker.suffix+'.tmp')
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
        if prior.get('version')!=version or prior.get('contract')!=contract or prior.get('identity')!=[info.st_dev,info.st_ino]:_hold('INCOMPATIBLE_VERSION_MARKER')
    conn=sqlite3.connect(path.as_uri()+'?mode=rw',uri=True,timeout=1,isolation_level=None)
    try:
        conn.execute('PRAGMA busy_timeout=1000');conn.execute('PRAGMA foreign_keys=ON')
        # Validate the selected existing identity owner; never synthesize a parent.
        parent={r[1]:r for r in conn.execute('PRAGMA table_xinfo('+price_identity_parent+')')}
        if 'mint' not in parent or parent['mint'][2]!='TEXT' or parent['mint'][5]!=1:_hold('TRACKED_TOKENS_OWNER_REQUIRED')
        if sum(bool(r[5]) for r in parent.values())!=1:_hold('INCOMPATIBLE_TRACKED_TOKENS_PK')
        conn.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
        deadline=time.monotonic()+2
        conn.execute('BEGIN IMMEDIATE')
        missing=_check_columns(conn);present=_check_peak(conn,price_identity_parent)
        if prior is not None and (missing or not present):_hold('VERSION_MARKER_SCHEMA_MISMATCH')
        fault('before_ddl')
        if production_profile:_production_profile(path,root,'core',authority)
        for name in missing:
            conn.execute('ALTER TABLE token_analysis ADD COLUMN '+name+' '+COLUMNS[name]);fault('after_column:'+name)
        if not present:conn.execute(peak_sql)
        fault('after_peak')
        if _check_columns(conn) or not _check_peak(conn,price_identity_parent):_hold('POSTCONDITION_FAILED')
        fault('before_commit')
        if production_profile:_production_profile(path,root,'core',authority)
        conn.execute('COMMIT');fault('after_commit')
        if production_profile:_production_profile(path,root,'core',authority)
        if prior is not None:return {'status':'ALREADY_INITIALIZED','version':version,'contract':contract}
        payload={'version':version,'contract':contract,'identity':[info.st_dev,info.st_ino]}
        encoded=(json.dumps(payload,sort_keys=True)+'\n').encode()
        if len(encoded)>MAX_MARKER_BYTES:_hold('VERSION_MARKER_BUDGET')
        fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'wb') as stream:stream.write(encoded);stream.flush();os.fsync(stream.fileno())
        fault('before_marker_replace');os.replace(temp,marker)
        directory=os.open(root,os.O_RDONLY)
        try:os.fsync(directory)
        finally:os.close(directory)
        return {'status':'INITIALIZED','version':version,'contract':contract}
    except BaseException:
        if conn.in_transaction:conn.rollback()
        raise
    finally:conn.close()

def initialize_creator_index(core, *, disposable_root, allow_disposable_v2=False):
    """Accepted startup column/index on a bounded disposable fixture.

    v1 schema marker remains unchanged. Index identity is independently revalidated.
    """
    result = initialize(core, disposable_root=disposable_root,
                        allow_disposable_v2=allow_disposable_v2)
    conn = sqlite3.connect(Path(core).absolute().as_uri() + '?mode=rw', uri=True, timeout=1)
    sql = 'CREATE INDEX idx_ta_pf_ws_creator ON token_analysis(pf_ws_creator)'
    try:
        conn.execute('BEGIN IMMEDIATE')
        columns = {r[1]: tuple(r[2:]) for r in conn.execute('PRAGMA table_xinfo(token_analysis)')}
        if 'bonding_curve_pda' in columns:
            if columns['bonding_curve_pda'] != ('TEXT', 0, None, 0, 0):
                _hold('INCOMPATIBLE_BONDING_CURVE_COLUMN')
            definition = conn.execute("SELECT sql FROM sqlite_schema WHERE name='token_analysis'").fetchone()[0]
            if [part for part in _declarations(definition) if part and part[0]=='bonding_curve_pda'] != [['bonding_curve_pda','text']]:
                _hold('INCOMPATIBLE_BONDING_CURVE_CONSTRAINT')
        else:
            conn.execute('ALTER TABLE token_analysis ADD COLUMN bonding_curve_pda TEXT')
        row = conn.execute("SELECT type,sql FROM sqlite_schema WHERE name='idx_ta_pf_ws_creator'").fetchone()
        if row:
            if row[0] != 'index' or _sql(row[1]) != _sql(sql):
                _hold('INCOMPATIBLE_CREATOR_INDEX')
        else:
            # Explicitly bound the index build; no historical database population.
            rows = conn.execute('SELECT 1 FROM token_analysis LIMIT 10001').fetchall()
            if len(rows) > 10000:
                _hold('CREATOR_INDEX_FIXTURE_ROW_BUDGET')
            conn.execute(sql)
        if [r[2] for r in conn.execute('PRAGMA index_info(idx_ta_pf_ws_creator)')] != ['pf_ws_creator']:
            _hold('INCOMPATIBLE_CREATOR_INDEX_COLUMNS')
        conn.commit()
        return dict(result, creator_index='VERIFIED')
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--core',required=True);parser.add_argument('--disposable-root',required=True)
    parser.add_argument('--allow-disposable-v2',action='store_true');args=parser.parse_args()
    soft,hard=resource.getrlimit(resource.RLIMIT_FSIZE)
    resource.setrlimit(resource.RLIMIT_FSIZE,(min(soft,MAX_FILE_BYTES) if soft!=resource.RLIM_INFINITY else MAX_FILE_BYTES,hard))
    print(json.dumps(initialize(args.core,disposable_root=args.disposable_root,allow_disposable_v2=args.allow_disposable_v2),sort_keys=True))

if __name__=='__main__':main()
