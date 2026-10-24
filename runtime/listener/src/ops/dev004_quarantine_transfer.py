"""Bounded, restartable transfer of the existing quarantine relation only.

No provider, schema owner, daemon, archive, source write or implicit activation.
Production callers must hold the accepted retired-source/TRANSFER authority.
"""
from __future__ import annotations
import contextlib,fcntl,hashlib,json,os,sqlite3
from pathlib import Path

BATCH_ROWS=128
MAX_ROW_BYTES=65536
CHECKPOINT_BYTES=16384
MAX_MAIN_BYTES=480_000_000
MAX_FILE_BYTES=499_000_000
DISK_FLOOR=2_000_000_000
RESERVE=64*1024*1024
TABLE='wt_lineage_quarantine'
KEY='quarantine_id'

class TransferHold(RuntimeError):pass

def digest(row):
    raw=json.dumps(dict(row),sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
    if len(raw)>MAX_ROW_BYTES:raise TransferHold('ROW_CAPACITY')
    return hashlib.sha256(raw).hexdigest()

def advance(previous,row):
    return hashlib.sha256(bytes.fromhex(previous)+bytes.fromhex(digest(row))).hexdigest()

def physical(path):
    p=Path(path)
    if not p.is_absolute() or str(p.resolve())!=str(p) or '..' in p.parts or p.is_symlink():raise TransferHold('PHYSICAL_PATH_REQUIRED')
    return p

def fingerprint(path):
    p=physical(path);s=p.stat()
    if not p.is_file() or s.st_nlink!=1:raise TransferHold('DATABASE_IDENTITY')
    wal=Path(str(p)+'-wal')
    w=None
    if wal.exists():
        if wal.is_symlink():raise TransferHold('SOURCE_WAL_IDENTITY')
        z=wal.stat();w=[z.st_dev,z.st_ino,z.st_size,z.st_mtime_ns]
    return {'path':str(p),'device':s.st_dev,'inode':s.st_ino,'bytes':s.st_size,'mtime_ns':s.st_mtime_ns,'wal':w}

def source_connection(path):
    c=sqlite3.connect(physical(path).as_uri()+'?mode=ro',uri=True,timeout=1)
    c.row_factory=sqlite3.Row;c.execute('PRAGMA query_only=ON');return c

def schema(c):
    ddl=c.execute('SELECT sql FROM sqlite_schema WHERE type=\'table\' AND name=?',(TABLE,)).fetchone()
    columns=c.execute('PRAGMA table_info(wt_lineage_quarantine)').fetchall()
    if not ddl or [x[1] for x in columns if x[5]]!=[KEY]:raise TransferHold('QUARANTINE_SCHEMA_AUTHORITY')
    return {'ddl_sha256':hashlib.sha256(ddl[0].encode()).hexdigest(),'columns':[x[1] for x in columns]}

def write_checkpoint(path,value):
    raw=(json.dumps(value,sort_keys=True,separators=(',',':'))+'\n').encode()
    if len(raw)>CHECKPOINT_BYTES:raise TransferHold('CHECKPOINT_CAPACITY')
    p=physical(path);tmp=p.with_name(p.name+'.tmp')
    if p.is_symlink() or tmp.is_symlink():raise TransferHold('CHECKPOINT_SYMLINK')
    fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
    try:
        if os.write(fd,raw)!=len(raw):raise TransferHold('CHECKPOINT_SHORT_WRITE')
        os.fsync(fd)
    finally:os.close(fd)
    os.replace(tmp,p);fd=os.open(p.parent,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)

def capacity(target):
    import resource
    soft,hard=resource.getrlimit(resource.RLIMIT_FSIZE)
    if hard==resource.RLIM_INFINITY or hard>=500_000_000 or soft>MAX_FILE_BYTES:
        raise TransferHold('KERNEL_FILE_LIMIT_REQUIRED')
    p=physical(target);v=os.statvfs(p.parent)
    if v.f_bavail*v.f_frsize<DISK_FLOOR+RESERVE:raise TransferHold('DISK_FLOOR')
    for x in [p,Path(str(p)+'-journal'),Path(str(p)+'-wal')]:
        if x.exists() and x.stat().st_size>=MAX_FILE_BYTES:raise TransferHold('FILE_CAPACITY')
    if p.stat().st_size>MAX_MAIN_BYTES:raise TransferHold('DATABASE_CAPACITY')

def authorize_source(path,generation,production):
    if not production:return None
    marker=Path(str(path)+'.retired')
    if marker.is_symlink():raise TransferHold('SOURCE_RETIREMENT_REQUIRED')
    try:
        with marker.open('rb') as f:raw=f.read(2049)
        value=json.loads(raw)
    except (OSError,ValueError):raise TransferHold('SOURCE_RETIREMENT_REQUIRED') from None
    if len(raw)>2048 or value!={'version':1,'path':str(path),'generation':generation,'retired':True}:raise TransferHold('SOURCE_RETIREMENT_REQUIRED')
    return hashlib.sha256(raw).hexdigest()

def _initialize(source,target,checkpoint,*,generation,production=False,authorize=None):
    source=physical(source);target=physical(target);checkpoint=physical(checkpoint)
    if source==target or (source.stat().st_dev,source.stat().st_ino)==(target.stat().st_dev,target.stat().st_ino):raise TransferHold('DISTINCT_STORES_REQUIRED')
    if not production and not (str(target).startswith('/private/tmp/dev004-') and str(checkpoint).startswith('/private/tmp/dev004-')):raise TransferHold('DISPOSABLE_PATH_REQUIRED')
    if production and authorize is None:raise TransferHold('TRANSFER_OWNER_REQUIRED')
    if authorize:
        result=authorize()
        if production and not result:raise TransferHold('TRANSFER_OWNER_REQUIRED')
    import resource
    soft,hard=resource.getrlimit(resource.RLIMIT_FSIZE)
    if hard==resource.RLIM_INFINITY or hard>=500_000_000 or soft>MAX_FILE_BYTES:raise TransferHold('KERNEL_FILE_LIMIT_REQUIRED')
    capacity(target)
    src=source_connection(source)
    try:
        authority=schema(src)
        if src.execute('SELECT 1 FROM wt_lineage_quarantine WHERE quarantine_id IS NULL OR quarantine_id=\'\' LIMIT 1').fetchone():raise TransferHold('SOURCE_IDENTITY_REQUIRED')
    finally:src.close()
    t=source_connection(target)
    try:
        if schema(t)!=authority:raise TransferHold('SCHEMA_MISMATCH')
    finally:t.close()
    identity=fingerprint(source);target_id=fingerprint(target)
    expected={'version':1,'generation':generation,'source':identity,'source_retirement_sha256':authorize_source(source,generation,production),'target':{x:target_id[x] for x in ['path','device','inode']},'schema':authority,'production':production}
    if checkpoint.exists():
        value=load(checkpoint)
        if value['authority']!=expected:raise TransferHold('CHECKPOINT_GENERATION_MISMATCH')
        return value
    # An interrupted checkpoint must not be silently discarded or promoted.
    if checkpoint.with_name(checkpoint.name+'.tmp').exists():raise TransferHold('INTERRUPTED_CHECKPOINT')
    state={'authority':expected,'phase':'COPY','cursor':None,'rows':0,'digest':'00'*32,'verify_cursor':None,'verified_rows':0,'verified_digest':'00'*32,'batches':0}
    write_checkpoint(checkpoint,state);return state

def load(path):
    path=physical(path)
    if path.with_name(path.name+'.tmp').exists():raise TransferHold('INTERRUPTED_CHECKPOINT')
    with path.open('rb') as f:raw=f.read(CHECKPOINT_BYTES+1)
    if len(raw)>CHECKPOINT_BYTES:raise TransferHold('CHECKPOINT_CAPACITY')
    try:state=json.loads(raw)
    except ValueError:raise TransferHold('CHECKPOINT_INVALID') from None
    required={'authority','phase','cursor','rows','digest','verify_cursor','verified_rows','verified_digest','batches'}
    if set(state)!=required or state['phase'] not in {'COPY','VERIFY','COMPLETE'}:raise TransferHold('CHECKPOINT_INVALID')
    return state

def check_authority(state,authorize):
    a=state['authority'];p=Path(a['source']['path'])
    if a['production'] and authorize is None:raise TransferHold('TRANSFER_OWNER_REQUIRED')
    if authorize:
        result=authorize()
        if a['production'] and not result:raise TransferHold('TRANSFER_OWNER_REQUIRED')
    if fingerprint(p)!=a['source']:raise TransferHold('SOURCE_RECORD_OR_GENERATION_CHANGED')
    if authorize_source(p,a['generation'],a['production'])!=a['source_retirement_sha256']:raise TransferHold('SOURCE_RETIREMENT_CHANGED')
    target=fingerprint(a['target']['path'])
    if {x:target[x] for x in a['target']}!=a['target']:raise TransferHold('TARGET_IDENTITY_CHANGED')
    capacity(a['target']['path'])

def batch(c,cursor):
    sql='SELECT * FROM wt_lineage_quarantine'+(' WHERE quarantine_id>?' if cursor is not None else '')+' ORDER BY quarantine_id LIMIT ?'
    args=(cursor,BATCH_ROWS) if cursor is not None else (BATCH_ROWS,)
    plans=[r[3] for r in c.execute('EXPLAIN QUERY PLAN '+sql,args)]
    if any('TEMP B-TREE' in x or ('SCAN ' in x and 'USING INDEX' not in x) for x in plans):raise TransferHold('INDEXED_CONTINUATION_REQUIRED')
    rows=c.execute(sql,args).fetchmany(BATCH_ROWS)
    for row in rows:digest(row)
    return rows

def _step(checkpoint,*,authorize=None,after_commit=None):
    checkpoint=physical(checkpoint);state=load(checkpoint);check_authority(state,authorize)
    if state['phase']=='COMPLETE':return state
    a=state['authority'];src=source_connection(a['source']['path']);t=sqlite3.connect(a['target']['path'],timeout=1);t.row_factory=sqlite3.Row
    try:
        t.execute('PRAGMA foreign_keys=ON')
        if t.execute('PRAGMA journal_mode').fetchone()[0].lower()!='delete':raise TransferHold('BOUNDED_DELETE_JOURNAL_REQUIRED')
        page=t.execute('PRAGMA page_size').fetchone()[0];t.execute('PRAGMA max_page_count='+str(MAX_MAIN_BYTES//page))
        if schema(src)!=a['schema'] or schema(t)!=a['schema']:raise TransferHold('SCHEMA_CHANGED')
        if state['phase']=='COPY':
            rows=batch(src,state['cursor']);t.execute('BEGIN IMMEDIATE')
            for row in rows:
                values=dict(row);old=t.execute('SELECT * FROM wt_lineage_quarantine WHERE quarantine_id=?',(row[KEY],)).fetchone()
                if old is not None:
                    if dict(old)!=values:raise TransferHold('CONFLICTING_TARGET_RECORD:'+row[KEY])
                else:
                    cols=a['schema']['columns'];t.execute('INSERT INTO wt_lineage_quarantine('+','.join('"'+x+'"' for x in cols)+') VALUES('+','.join('?' for _ in cols)+')',tuple(row[x] for x in cols))
                actual=t.execute('SELECT * FROM wt_lineage_quarantine WHERE quarantine_id=?',(row[KEY],)).fetchone()
                if dict(actual)!=values:raise TransferHold('TARGET_STATE_MISMATCH')
                state['digest']=advance(state['digest'],row);state['rows']+=1;state['cursor']=row[KEY]
            check_authority(state,authorize);t.commit()
            if after_commit:after_commit()
            state['batches']+=1
            if len(rows)<BATCH_ROWS:state['phase']='VERIFY'
        else:
            rows=batch(t,state['verify_cursor'])
            for row in rows:
                actual=src.execute('SELECT * FROM wt_lineage_quarantine WHERE quarantine_id=?',(row[KEY],)).fetchone()
                if actual is None:raise TransferHold('MISSING_SOURCE_RECORD:'+row[KEY])
                if dict(actual)!=dict(row):raise TransferHold('TARGET_STATE_MISMATCH')
                state['verified_digest']=advance(state['verified_digest'],row);state['verified_rows']+=1;state['verify_cursor']=row[KEY]
            if len(rows)<BATCH_ROWS:
                if state['rows']!=state['verified_rows'] or state['digest']!=state['verified_digest']:raise TransferHold('INCOMPLETE_SOURCE_TARGET_COVERAGE')
                if t.execute('SELECT * FROM pragma_foreign_key_check LIMIT 1').fetchone():raise TransferHold('FOREIGN_KEY_INTEGRITY')
                state['phase']='COMPLETE'
        check_authority(state,authorize);write_checkpoint(checkpoint,state);return state
    except BaseException:
        t.rollback();raise
    finally:src.close();t.close()


@contextlib.contextmanager
def checkpoint_owner(path):
    path=physical(path);lock=path.with_name(path.name+'.lock')
    if lock.is_symlink():raise TransferHold('CHECKPOINT_SYMLINK')
    fd=os.open(lock,os.O_RDWR|os.O_CREAT|getattr(os,'O_NOFOLLOW',0),0o600)
    try:
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise TransferHold('CHECKPOINT_OWNER_BUSY') from None
        yield
    finally:os.close(fd)

def initialize(source,target,checkpoint,*,generation,production=False,authorize=None):
    with checkpoint_owner(checkpoint):
        return _initialize(source,target,checkpoint,generation=generation,production=production,authorize=authorize)

def step(checkpoint,*,authorize=None,after_commit=None):
    with checkpoint_owner(checkpoint):
        return _step(checkpoint,authorize=authorize,after_commit=after_commit)
