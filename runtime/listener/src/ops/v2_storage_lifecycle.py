"""One explicit, bounded V2 disposable-data owner; no provider or import-time I/O.

Only qualified latest-value RPC cache and WSS telemetry may be retired. All
canonical/operation/price/recovery/unknown families are structurally excluded.
Seven days is a minimum; exact boundary and unexpired cache stay protected.
"""
from __future__ import annotations
import argparse,contextlib,fcntl,hashlib,json,math,os,pathlib,resource,shlex,sqlite3,time,uuid

VERSION=1
SEVEN_DAYS=7*86400
MAX_BATCH=64
MAX_STEPS=3
MAX_DELETE_BYTES=1000000
MAX_CONFIG_BYTES=32768
MAX_FILE_BYTES=499000000
MAX_DB_BYTES=480000000
MAX_WAL_START_BYTES=8000000
DISK_FLOOR=2000000000
DISK_RESERVE=2000000
MAX_VM_STEPS=100000
STATE='v2_storage_lifecycle_state'
FAMILIES={'core':('rpc_response_cache','wss_metrics'),'ops':('rpc_response_cache',)}
COLUMNS={'rpc_response_cache':('cache_key','response_json','method','cached_at','ttl_seconds','hit_count'),'wss_metrics':('id','ts','subscription','source_file','msg_count','est_bytes','note')}
STATE_SQL="CREATE TABLE v2_storage_lifecycle_state (family TEXT PRIMARY KEY CHECK(family IN ('rpc_response_cache','wss_metrics')),version INTEGER NOT NULL CHECK(version=1),generation TEXT NOT NULL CHECK(length(generation)<=128),cursor TEXT NOT NULL CHECK(length(cursor)<=256),upper_key TEXT NOT NULL CHECK(length(upper_key)<=256),examined INTEGER NOT NULL CHECK(examined BETWEEN 0 AND 9223372036854775807),deleted INTEGER NOT NULL CHECK(deleted BETWEEN 0 AND 9223372036854775807),cycles INTEGER NOT NULL CHECK(cycles BETWEEN 0 AND 9223372036854775807),last_cutoff INTEGER NOT NULL,last_batch_hash TEXT NOT NULL CHECK(length(last_batch_hash)<=64)) WITHOUT ROWID"

class Hold(RuntimeError):pass

def _digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def _physical(value):
 p=pathlib.Path(value)
 if not p.is_absolute() or str(p.resolve())!=str(p):raise Hold('PHYSICAL_PATH_REQUIRED')
 return p

class Lifecycle:
 def __init__(self,manifest_path,manifest_sha256):
  p=_physical(manifest_path)
  with p.open('rb') as f:raw=f.read(MAX_CONFIG_BYTES+1)
  if len(raw)>MAX_CONFIG_BYTES or hashlib.sha256(raw).hexdigest()!=manifest_sha256:raise Hold('MANIFEST_AUTHORITY')
  m=json.loads(raw)
  if set(m)!={'version','generation','core','ops','pair_manifest','pair_authority','pair_sha256','lock'} or m['version']!=VERSION:raise Hold('MANIFEST_VERSION')
  if not isinstance(m['generation'],str) or not 1<=len(m['generation'])<=128:raise Hold('MANIFEST_GENERATION')
  for key in ['core','ops','pair_manifest','pair_authority','lock']:_physical(m[key])
  if m['core']==m['ops'] or m['lock'] in [m['core'],m['ops'],m['pair_manifest'],m['pair_authority']]:raise Hold('DISTINCT_STORES_REQUIRED')
  if pathlib.Path(m['lock']).parent!=pathlib.Path(m['pair_manifest']).parent:raise Hold('LOCK_AUTHORITY')
  self.m=m
  # The scheduler has no V1/default destination or credential-loading path.
  os.environ.update(DB_PATH=m['core'],WT_OPS_DB_PATH=m['ops'],FLEX_DB_PAIR_MANIFEST=m['pair_manifest'],FLEX_DB_PAIR_AUTHORITY=m['pair_authority'],DEV004_PAIRED_BINDING_REQUIRED='1',FLEX_DB_PAIR_ROLE='runtime')
  from src.core import paired_binding_guard,database_write_service
  self.guard=paired_binding_guard;self.writer=database_write_service
  self._validate()

 def _validate(self):
  with open(self.m['pair_manifest'],'rb') as stream:pair=stream.read(MAX_CONFIG_BYTES+1)
  if len(pair)>MAX_CONFIG_BYTES or hashlib.sha256(pair).hexdigest()!=self.m['pair_sha256']:raise Hold('PAIR_MANIFEST_CHANGED')
  if self.guard.validate_pair(core_path=self.m['core'],ops_path=self.m['ops'],purpose='active')!=self.m['generation']:raise Hold('PAIR_GENERATION')
  soft,_=resource.getrlimit(resource.RLIMIT_FSIZE)
  if soft==resource.RLIM_INFINITY or not 0<soft<=MAX_FILE_BYTES:raise Hold('KERNEL_FILE_LIMIT_REQUIRED')

 def _pressure(self,path):
  s=os.statvfs(pathlib.Path(path).parent)
  if s.f_bavail*s.f_frsize<DISK_FLOOR+DISK_RESERVE:raise Hold('DISK_FLOOR')
  for suffix,limit in [('',MAX_DB_BYTES),('-wal',MAX_WAL_START_BYTES),('-journal',MAX_WAL_START_BYTES)]:
   p=pathlib.Path(path+suffix)
   if p.exists() and p.stat().st_size>limit:raise Hold('STORAGE_PRESSURE')

 @contextlib.contextmanager
 def _lease(self,role):
  self._validate();self._pressure(self.m[role])
  lease=self.writer.acquire_write_lease(role,self.m[role],str(uuid.uuid4()),'v2-seven-day-lifecycle',timeout=.05,priority=3)
  try:yield
  finally:self.writer.release_write_lease(lease)

 def _conn(self,role):
  c=self.writer._native_connect(pathlib.Path(self.m[role]).as_uri()+'?mode=rw',uri=True,timeout=.05,isolation_level=None)
  c.execute('PRAGMA foreign_keys=ON');c.execute('PRAGMA busy_timeout=50');c.execute('PRAGMA synchronous=FULL');c.execute('PRAGMA wal_autocheckpoint=32')
  page=c.execute('PRAGMA page_size').fetchone()[0];c.execute(f'PRAGMA max_page_count={MAX_DB_BYTES//page}')
  return c

 def initialize(self):
  for role in ['core','ops']:
   with self._lease(role):
    c=self._conn(role)
    try:
     c.execute('BEGIN IMMEDIATE');old=c.execute("SELECT sql FROM sqlite_master WHERE name=?",(STATE,)).fetchone()
     if old is None:c.execute(STATE_SQL)
     elif old[0]!=STATE_SQL:raise Hold('STATE_SCHEMA')
     for family in FAMILIES[role]:
      c.execute(f"INSERT OR IGNORE INTO {STATE} VALUES(?,1,?,'','',0,0,0,0,'')",(family,self.m['generation']))
     self._validate();self.writer.assert_database_writable(self.m[role]);c.execute('COMMIT')
    except BaseException:
     if c.in_transaction:c.execute('ROLLBACK')
     raise
    finally:c.close()

 def _source(self,c,family):
  info=c.execute('PRAGMA table_info('+family+')').fetchall()
  if not info:return False
  if tuple(row[1] for row in info)!=COLUMNS[family]:raise Hold('UNKNOWN_FAMILY_SCHEMA')
  key='cache_key' if family=='rpc_response_cache' else 'id'
  if [row[1] for row in info if row[5]]!=[key]:raise Hold('INDEXED_IDENTITY_REQUIRED')
  if c.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' AND tbl_name=? LIMIT 1",(family,)).fetchone():raise Hold('UNKNOWN_DELETE_DEPENDENCY')
  tables=c.execute("SELECT name FROM sqlite_master WHERE type='table' LIMIT 513").fetchall()
  if len(tables)>512:raise Hold('CATALOG_CAPACITY')
  for (table,) in tables:
   if not table.replace('_','').isalnum():raise Hold('UNKNOWN_SCHEMA_IDENTIFIER')
   if any(row[2]==family for row in c.execute('PRAGMA foreign_key_list("'+table+'")')):raise Hold('REFERENCED_FAMILY_PROTECTED')
  return True

 def step(self,role,family,*,now=None,batch=MAX_BATCH,crash=None):
  if role not in FAMILIES or family not in FAMILIES[role] or type(batch) is not int or not 1<=batch<=MAX_BATCH:raise Hold('UNQUALIFIED_FAMILY_OR_BATCH')
  now=int(time.time()) if now is None else now
  if type(now) is not int or now<=SEVEN_DAYS:raise Hold('CLOCK_AUTHORITY')
  cutoff=now-SEVEN_DAYS
  self._validate()
  inspect=self.writer._native_connect(pathlib.Path(self.m[role]).as_uri()+'?mode=ro',uri=True,timeout=.05)
  try:
   inspect.execute('PRAGMA query_only=ON')
   if not self._source(inspect,family):return {'family':family,'examined':0,'deleted':0,'status':'ABSENT_PROTECTED'}
   schema_version=inspect.execute('PRAGMA schema_version').fetchone()[0]
  finally:inspect.close()
  with self._lease(role):
   c=self._conn(role)
   try:
    c.execute('BEGIN IMMEDIATE')
    if c.execute('PRAGMA schema_version').fetchone()[0]!=schema_version:raise Hold('SOURCE_SCHEMA_CHANGED')
    schema=c.execute("SELECT sql FROM sqlite_master WHERE name=?",(STATE,)).fetchone()
    if not schema or schema[0]!=STATE_SQL:raise Hold('STATE_NOT_INITIALIZED')
    row=c.execute(f'SELECT * FROM {STATE} WHERE family=?',(family,)).fetchone()
    if not row or row[1]!=VERSION:raise Hold('STATE_VERSION')
    _,_,generation,cursor,upper,examined,deleted,cycles,_,_=row
    if generation!=self.m['generation']:cursor=upper='' # Only maintenance progress; never retry/budget state.
    key='cache_key' if family=='rpc_response_cache' else 'id'
    zero='' if family=='rpc_response_cache' else 0
    if not upper:
     value=c.execute(f'SELECT {key} FROM {family} ORDER BY {key} DESC LIMIT 1').fetchone()
     upper=str(value[0]) if value else ''
    args=(cursor if family=='rpc_response_cache' else int(cursor or 0),upper if family=='rpc_response_cache' else int(upper or 0),batch)
    columns='cache_key,cached_at,ttl_seconds,length(CAST(response_json AS BLOB))+256' if family=='rpc_response_cache' else 'id,ts,coalesce(length(CAST(note AS BLOB)),0)+coalesce(length(source_file),0)+coalesce(length(subscription),0)+128'
    sql=f'SELECT {columns} FROM {family} WHERE {key}>? AND {key}<=? ORDER BY {key} LIMIT ?'
    plan=' '.join(str(p[3]) for p in c.execute('EXPLAIN QUERY PLAN '+sql,args))
    if 'SEARCH' not in plan or ('INDEX' not in plan and 'PRIMARY KEY' not in plan):raise Hold('BOUNDED_INDEX_REQUIRED')
    instructions=[0]
    def progress():instructions[0]+=100;return instructions[0]>MAX_VM_STEPS
    c.set_progress_handler(progress,100)
    selected=c.execute(sql,args).fetchall() if upper else []
    allowed={STATE,family}
    def authorize(action,table,*args):
     if action in {sqlite3.SQLITE_DELETE,sqlite3.SQLITE_UPDATE,sqlite3.SQLITE_INSERT} and table not in allowed:return sqlite3.SQLITE_DENY
     return sqlite3.SQLITE_OK
    c.set_authorizer(authorize)
    removed=0;deleted_bytes=0;large_protected=0
    for item in selected:
     size=item[-1]
     if not isinstance(size,int) or size<0 or size>MAX_DELETE_BYTES:
      large_protected+=1;continue
     if deleted_bytes+size>MAX_DELETE_BYTES:continue
     stamp=item[1]
     if not isinstance(stamp,(int,float)) or not math.isfinite(stamp) or stamp<0 or stamp>=cutoff:continue
     if family=='rpc_response_cache':
      ttl=item[2]
      if not isinstance(ttl,(int,float)) or not math.isfinite(ttl) or ttl<0 or stamp+ttl>now:continue
      changed=c.execute('DELETE FROM rpc_response_cache WHERE cache_key=? AND cached_at=? AND ttl_seconds=? AND cached_at<? AND cached_at+ttl_seconds<=?',(item[0],stamp,ttl,cutoff,now)).rowcount
     else:changed=c.execute('DELETE FROM wss_metrics WHERE id=? AND ts=? AND ts<?',(item[0],stamp,cutoff)).rowcount
     removed+=changed;deleted_bytes+=size*changed
    new_cursor=str(selected[-1][0]) if selected else ''
    if len(new_cursor.encode())>256:raise Hold('CURSOR_CAPACITY')
    if not selected or new_cursor==upper:new_cursor='';upper='';cycles+=1
    digest=_digest({'family':family,'cutoff':cutoff,'ids':[v[0] for v in selected],'deleted':removed})
    c.execute(f'UPDATE {STATE} SET generation=?,cursor=?,upper_key=?,examined=?,deleted=?,cycles=?,last_cutoff=?,last_batch_hash=? WHERE family=?',(self.m['generation'],new_cursor,upper,examined+len(selected),deleted+removed,cycles,cutoff,digest,family))
    if crash:crash('before_commit')
    self._validate();self.writer.assert_database_writable(self.m[role]);c.execute('COMMIT')
    if crash:crash('after_commit')
    return {'family':family,'examined':len(selected),'deleted':removed,'estimated_deleted_bytes':deleted_bytes,'large_rows_protected':large_protected,'cycles':cycles,'status':'PASS'}
   except BaseException:
    if c.in_transaction:c.execute('ROLLBACK')
    raise
   finally:c.close()

 def run_once(self,*,now=None):
  if sum(len(v) for v in FAMILIES.values())>MAX_STEPS:raise Hold('RUN_BUDGET')
  lock=_physical(self.m['lock'])
  if lock.is_symlink():raise Hold('LOCK_AUTHORITY')
  fd=os.open(lock,os.O_WRONLY|os.O_CREAT|getattr(os,'O_NOFOLLOW',0),0o600)
  try:
   try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
   except BlockingIOError:raise Hold('DUPLICATE_CLEANUP_OWNER') from None
   result=[]
   for role in ['core','ops']:
    for family in FAMILIES[role]:result.append(self.step(role,family,now=now))
   return result
  finally:os.close(fd)

def render_schedule(python,package,manifest,digest):
 for p in [python,package,manifest]:_physical(p)
 if len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest):raise Hold('MANIFEST_DIGEST')
 command=' '.join(shlex.quote(str(v)) for v in [python,'-B','-m','src.ops.v2_storage_lifecycle','--manifest',manifest,'--sha256',digest,'--once','--quiet'])
 return '*/15 * * * * cd '+shlex.quote(str(package))+' && '+command+' >/dev/null 2>&1'

def main():
 p=argparse.ArgumentParser();p.add_argument('--manifest',required=True);p.add_argument('--sha256',required=True);p.add_argument('--initialize',action='store_true');p.add_argument('--once',action='store_true');p.add_argument('--quiet',action='store_true');a=p.parse_args()
 limit=resource.getrlimit(resource.RLIMIT_FSIZE)[1];limit=MAX_FILE_BYTES if limit==resource.RLIM_INFINITY else min(limit,MAX_FILE_BYTES);resource.setrlimit(resource.RLIMIT_FSIZE,(limit,limit))
 try:
  owner=Lifecycle(a.manifest,a.sha256)
  if a.initialize:owner.initialize();result={'status':'INITIALIZED'}
  elif a.once:result={'status':'PASS','batches':owner.run_once()}
  else:raise Hold('EXPLICIT_ACTION_REQUIRED')
  if not a.quiet:print(json.dumps(result,sort_keys=True))
  return 0
 except Exception as error:
  if not a.quiet:print(json.dumps({'status':'HOLD','error_type':type(error).__name__,'code':str(error)[:128]}))
  return 2
if __name__=='__main__':raise SystemExit(main())
