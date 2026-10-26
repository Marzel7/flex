"""Opt-in v1 prospective CREATE evidence. No providers, implicit DDL or writer."""
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import time

FLAG='FLEX_V2_CREATE_AUDIT_ENABLED'
VERSION=1
MAX_EVENTS=4096
MAX_BYTES=4194304
MAX_PAYLOAD=1024
MAX_BATCH=128
INDEX='dev_create_migration_time_v1'
INDEX_SQL=f'CREATE INDEX {INDEX} ON token_analysis(migrated_at,mint) WHERE migrated_at IS NOT NULL'
OBJECTS={
 'create_coverage_owner_v1':"CREATE TABLE create_coverage_owner_v1(id INTEGER PRIMARY KEY CHECK(id=1),version INTEGER NOT NULL CHECK(version=1),activated_utc_ns INTEGER NOT NULL,events INTEGER NOT NULL CHECK(events BETWEEN 0 AND 4096),bytes INTEGER NOT NULL CHECK(bytes BETWEEN 0 AND 4194304))",
 'create_coverage_commits_v1':"CREATE TABLE create_coverage_commits_v1(commit_order INTEGER PRIMARY KEY AUTOINCREMENT,writer_transaction TEXT NOT NULL UNIQUE,generation TEXT NOT NULL,staged_utc_ns INTEGER NOT NULL)",
 'create_coverage_events_v1':"CREATE TABLE create_coverage_events_v1(event_key TEXT PRIMARY KEY,kind TEXT NOT NULL CHECK(kind IN ('HANDOFF','ANCHOR')),mint TEXT NOT NULL,signature TEXT NOT NULL,commit_order INTEGER NOT NULL REFERENCES create_coverage_commits_v1(commit_order),payload TEXT NOT NULL CHECK(length(CAST(payload AS BLOB))<=1024),UNIQUE(kind,mint,signature))",
 'ix_create_coverage_commit':"CREATE INDEX ix_create_coverage_commit ON create_coverage_events_v1(commit_order)",
 'create_coverage_quota':"CREATE TRIGGER create_coverage_quota BEFORE INSERT ON create_coverage_events_v1 BEGIN SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM create_coverage_owner_v1 WHERE id=1) OR NOT EXISTS(SELECT 1 FROM create_coverage_commits_v1 WHERE commit_order=NEW.commit_order) THEN RAISE(ABORT,'CREATE_AUDIT_PARENT_REQUIRED') END; SELECT CASE WHEN (SELECT events FROM create_coverage_owner_v1 WHERE id=1)>=4096 OR (SELECT bytes FROM create_coverage_owner_v1 WHERE id=1)+length(CAST(NEW.payload AS BLOB))>4194304 THEN RAISE(ABORT,'CREATE_AUDIT_CAPACITY') END; END",
 'create_coverage_account':"CREATE TRIGGER create_coverage_account AFTER INSERT ON create_coverage_events_v1 BEGIN UPDATE create_coverage_owner_v1 SET events=events+1,bytes=bytes+length(CAST(NEW.payload AS BLOB)) WHERE id=1; END",
}
for table in ('events','commits'):
 for action in ('UPDATE','DELETE'):
  name=f'create_coverage_{table}_{action.lower()}'
  OBJECTS[name]=f"CREATE TRIGGER {name} BEFORE {action} ON create_coverage_{table}_v1 BEGIN SELECT RAISE(ABORT,'CREATE_AUDIT_IMMUTABLE'); END"
CONTRACT=hashlib.sha256(json.dumps(OBJECTS,sort_keys=True).encode()).hexdigest()
class Hold(RuntimeError):pass

def norm(s):return ''.join(s.lower().split())
def packed(x):return json.dumps(x,sort_keys=True,separators=(',',':'))
def event_key(kind,mint,signature):
 # Stable through authorized fresh-pair recovery. Origin generation is immutable
 # in its committed transaction; it is never relabelled to the replacement pair.
 return hashlib.sha256(packed([kind,mint,signature]).encode()).hexdigest()
def enabled():
 value=os.environ.get(FLAG,'0')
 if value not in ('0','1'):raise Hold('CREATE_AUDIT_FLAG_INVALID')
 return value=='1'
def guard(conn,role,*,write=False):
 from src.core.paired_binding_guard import validate_pair
 if os.environ.get('DEV004_PAIRED_BINDING_REQUIRED')!='1':raise Hold('PAIRED_AUTHORITY_REQUIRED')
 paths={k:os.environ.get(k) for k in ('DB_PATH','WT_OPS_DB_PATH','FLEX_DB_PAIR_MANIFEST','FLEX_DB_PAIR_AUTHORITY')}
 if any(not v or not os.path.isabs(v) or os.path.realpath(v)!=v for v in paths.values()):raise Hold('EXPLICIT_PHYSICAL_V2_BINDINGS_REQUIRED')
 path=next(r[2] for r in conn.execute('PRAGMA database_list') if r[1]=='main');expected=paths['DB_PATH' if role=='core' else 'WT_OPS_DB_PATH']
 if path!=expected or paths['DB_PATH']==paths['WT_OPS_DB_PATH']:raise Hold('DATABASE_ROLE_MISMATCH')
 generation=validate_pair(core_path=paths['DB_PATH'],ops_path=paths['WT_OPS_DB_PATH'],write_path=path if write else None,purpose='write' if write else 'active')
 p=Path(path);space=os.statvfs(p.parent)
 if space.f_bavail*space.f_frsize<2067108864:raise Hold('DISK_FLOOR')
 if p.stat().st_size>=499000000:raise Hold('FILE_CAP')
 wal=Path(path+'-wal')
 if wal.exists() and wal.stat().st_size>=48*1024*1024:raise Hold('WAL_PRESSURE')
 if write:
  from src.core.database_write_service import assert_database_writable
  assert_database_writable(path)
 return generation

def validate_schema(conn):
 for name,sql in OBJECTS.items():
  row=conn.execute('SELECT sql FROM sqlite_master WHERE name=?',(name,)).fetchone()
  if not row or norm(row[0])!=norm(sql):raise Hold('CREATE_AUDIT_SCHEMA_REQUIRED:'+name)
 row=conn.execute('SELECT version FROM create_coverage_owner_v1 WHERE id=1').fetchone()
 if not row or row[0]!=VERSION:raise Hold('CREATE_AUDIT_VERSION')

def initialize_ops(conn):
 if not enabled():raise Hold('EXPLICIT_AUDIT_OPT_IN_REQUIRED')
 guard(conn,'ops',write=True)
 if not conn.in_transaction:raise Hold('SCHEMA_OWNER_TRANSACTION_REQUIRED')
 for name,sql in OBJECTS.items():
  row=conn.execute('SELECT sql FROM sqlite_master WHERE name=?',(name,)).fetchone()
  if row and norm(row[0])!=norm(sql):raise Hold('INCOMPATIBLE_AUDIT_SCHEMA')
  if not row:conn.execute(sql)
 conn.execute('INSERT OR IGNORE INTO create_coverage_owner_v1 VALUES(1,1,?,0,0)',(time.time_ns(),));validate_schema(conn)

def initialize_core_index(conn):
 if not enabled():raise Hold('EXPLICIT_AUDIT_OPT_IN_REQUIRED')
 guard(conn,'core',write=True)
 if not conn.in_transaction:raise Hold('SCHEMA_OWNER_TRANSACTION_REQUIRED')
 row=conn.execute('SELECT sql FROM sqlite_master WHERE name=?',(INDEX,)).fetchone()
 if row and norm(row[0])!=norm(INDEX_SQL):raise Hold('INCOMPATIBLE_MIGRATION_INDEX')
 if not row:conn.execute(INDEX_SQL)

class Boundary:
 def __init__(self,conn):
  self.conn=conn;self.generation=guard(conn,'ops',write=True);validate_schema(conn)
  if not conn.in_transaction:conn.execute('BEGIN')
  self.token=getattr(conn,'_write_transaction_id',None)
  if not isinstance(self.token,str) or not self.token or len(self.token)>128:raise Hold('ACCEPTED_WRITER_BOUNDARY_REQUIRED')
 def record(self,kind,mint,signature,detail):
  if not isinstance(mint,str) or not 1<=len(mint)<=44 or not isinstance(signature,str) or not 1<=len(signature)<=88:raise Hold('AUDIT_IDENTITY')
  payload=packed(detail);key=event_key(kind,mint,signature)
  if len(payload.encode())>MAX_PAYLOAD:raise Hold('AUDIT_PAYLOAD_CAPACITY')
  old=self.conn.execute('SELECT payload,commit_order FROM create_coverage_events_v1 WHERE event_key=?',(key,)).fetchone()
  if old:
   if old[0]!=payload:raise Hold('AUDIT_EVIDENCE_CONFLICT')
   return old[1]
  row=self.conn.execute('SELECT commit_order FROM create_coverage_commits_v1 WHERE writer_transaction=?',(self.token,)).fetchone()
  if row:order=row[0]
  else:order=self.conn.execute('INSERT INTO create_coverage_commits_v1(writer_transaction,generation,staged_utc_ns) VALUES(?,?,?)',(self.token,self.generation,time.time_ns())).lastrowid
  if self.conn.execute('SELECT count(*) FROM create_coverage_events_v1 INDEXED BY ix_create_coverage_commit WHERE commit_order=?',(order,)).fetchone()[0]>=MAX_BATCH:raise Hold('AUDIT_TRANSACTION_CAPACITY')
  self.conn.execute('INSERT INTO create_coverage_events_v1 VALUES(?,?,?,?,?,?)',(key,kind,mint,signature,order,payload));return order
 def handoff(self,mint,*,existing=False):
  # Read committed Core only, explicitly bound to the same accepted pair.
  path=os.environ['DB_PATH'];core=sqlite3.connect(Path(path).as_uri()+'?mode=ro',uri=True,timeout=2)
  try:
   core.execute('PRAGMA query_only=ON');row=core.execute('SELECT migration_tx,migrated_at FROM token_analysis WHERE mint=?',(mint,)).fetchone()
   if existing:
    old=self.conn.execute('SELECT 1 FROM create_coverage_events_v1 WHERE event_key=?',(event_key('HANDOFF',mint,row[0] if row else None),)).fetchone()
    if old:return 'ALREADY_RECORDED'
    boundary=self.conn.execute('SELECT activated_utc_ns FROM create_coverage_owner_v1 WHERE id=1').fetchone()[0]
    intent=core.execute('SELECT state,migration_tx,migrated_at,created_at FROM migration_walkback_handoff WHERE mint=?',(mint,)).fetchone()
    if not row or not intent or intent[0] not in ('PENDING','RETRY') or intent[1]!=row[0] or intent[2]!=row[1] or type(row[1]) is not int or row[1]*1000000000<boundary or intent[3]*1000000000<boundary:return 'LEGACY_UNTIMED'
  finally:core.close()
  if not row or not row[0] or type(row[1]) is not int:return 'CANONICAL_IDENTITY_UNVERIFIABLE'
  return self.record('HANDOFF',mint,row[0],{'mint':mint,'migration_signature':row[0],'migrated_at':row[1]})
 def activated(self,proof):
  row=self.conn.execute('SELECT creator,create_anchor_signature,create_anchor_source,create_anchor_audit_state FROM wt_walkback_queue WHERE mint=?',(proof.mint,)).fetchone()
  if row is None or tuple(row)!=(proof.creator,proof.signature,'CORE_QUALIFIED_CREATE','VALID'):raise Hold('ACTIVATION_READBACK_MISMATCH')
  text,digest=proof.encode();detail={'proof':json.loads(text),'digest':digest}
  if proof.source=='PUMPPORTAL':detail['receipt']=receipt_evidence(proof.mint,proof.creator,proof.signature)
  return self.record('ANCHOR',proof.mint,proof.signature,detail)

def receipt_evidence(mint,creator,signature):
 identity=hashlib.sha256(packed({'source':'PUMPPORTAL_NEW_TOKEN','mint':mint,'supplied_creator':creator,'supplied_birth_signature':signature}).encode()).hexdigest();root=os.environ.get('PUMPPORTAL_COMPACT_BIRTH_RECEIPTS_PATH')
 if not root or not os.path.isabs(root) or os.path.realpath(root)!=root:return 'UNVERIFIED'
 for p in (Path(root)/(identity+'.json'),Path(root)/identity[:2]/(identity+'.json')):
  if not p.exists():continue
  if p.is_symlink() or p.stat().st_size>4096:return 'UNVERIFIED'
  try:r=json.loads(p.read_text())
  except (OSError,ValueError):return 'UNVERIFIED'
  if (r.get('receipt_identity')==identity and r.get('kind')=='PUMPPORTAL_BIRTH' and r.get('source')=='PUMPPORTAL_NEW_TOKEN' and r.get('persistence_outcome')=='COMMITTED' and r.get('accepted_at_utc_ns') and not r.get('canonical_conflict') and r.get('mint')==mint and r.get('supplied_creator')==creator and r.get('supplied_birth_signature')==signature):return identity
 return 'UNVERIFIED'

def capture_migrations(conn,start,end):
 guard(conn,'core');row=conn.execute('SELECT sql FROM sqlite_master WHERE name=?',(INDEX,)).fetchone()
 if not row or norm(row[0])!=norm(INDEX_SQL):raise Hold('MIGRATION_INDEX_REQUIRED')
 if conn.in_transaction or any(type(x) not in (int,float) or not -2**62<=x<=2**62 or not math.isfinite(x) for x in (start,end)) or start>=end:raise Hold('AUDIT_WINDOW')
 sql=f'SELECT mint,migrated_at FROM token_analysis INDEXED BY {INDEX} WHERE migrated_at>=? AND migrated_at<? ORDER BY migrated_at,mint LIMIT 4097';args=(math.ceil(start),math.ceil(end));plan=conn.execute('EXPLAIN QUERY PLAN '+sql,args).fetchall();rows=[];size=0;began=time.monotonic();conn.execute('BEGIN')
 try:
  cursor=conn.execute(sql,args)
  while True:
   batch=cursor.fetchmany(128)
   if not batch:break
   for mint,stamp in batch:
    item=conn.execute('SELECT mint,migrated_at,migration_tx,create_tx_signature,earliest_tx_creator,pf_ws_creator FROM token_analysis WHERE mint=?',(mint,)).fetchone()
    value=dict(zip(('mint','migrated_at','migration_tx','create_tx_signature','earliest_tx_creator','pf_ws_creator'),item));rows.append(value);size+=len(packed(value).encode())
    if len(rows)>4096 or size>MAX_BYTES:raise Hold('AUDIT_CAPTURE_CAPACITY')
   guard(conn,'core')
   if time.monotonic()-began>2:raise Hold('AUDIT_CAPTURE_TIME_BUDGET')
  return rows,plan
 finally:conn.rollback()

def read_coverage(core,ops,start,end):
 guard(ops,'ops');validate_schema(ops);rows,plan=capture_migrations(core,start,end);counts={k:0 for k in ('PUMPPORTAL_DIRECT','RPC_RECOVERED','OTHER_VERIFIED_SOURCE','ANCHOR_PRESENT_SOURCE_UNKNOWN','NO_QUALIFIED_ANCHOR')};timing={k:0 for k in ('BEFORE_HANDOFF','AT_HANDOFF','AFTER_HANDOFF','UNVERIFIABLE')};ops.execute('BEGIN')
 try:
  for row in rows:
   w=ops.execute('SELECT creator,create_anchor_signature,create_anchor_audit_state FROM wt_walkback_queue WHERE mint=?',(row['mint'],)).fetchone();category='NO_QUALIFIED_ANCHOR';relation='UNVERIFIABLE'
   if w and w[1] and w[2]=='VALID':
    category='ANCHOR_PRESENT_SOURCE_UNKNOWN';a=ops.execute('SELECT e.payload,e.commit_order,c.generation FROM create_coverage_events_v1 e JOIN create_coverage_commits_v1 c USING(commit_order) WHERE event_key=?',(event_key('ANCHOR',row['mint'],w[1]),)).fetchone()
    if a:
     payload=json.loads(a[0]);p=payload.get('proof',{});checksum=hashlib.sha256(packed(p).encode()).hexdigest()
     if checksum!=payload.get('digest') or p.get('mint')!=row['mint'] or p.get('creator')!=w[0] or p.get('signature')!=w[1]:raise Hold('AUDIT_EVIDENCE_CORRUPT')
     if checksum==payload.get('digest') and p.get('mint')==row['mint'] and p.get('creator')==w[0] and p.get('signature')==w[1] and row['create_tx_signature']==w[1] and all(not v or v==w[0] for v in (row['earliest_tx_creator'],row['pf_ws_creator'])):
      if (p.get('source'),p.get('qualification'))==('PUMPPORTAL','PUMPPORTAL_NEW_TOKEN') and payload.get('receipt') not in (None,'UNVERIFIED'):category='PUMPPORTAL_DIRECT'
      elif (p.get('source'),p.get('qualification'))==('STRICT_RPC','DEV074_STRICT_CREATE'):category='RPC_RECOVERED'
     h=ops.execute('SELECT e.commit_order,c.generation FROM create_coverage_events_v1 e JOIN create_coverage_commits_v1 c USING(commit_order) WHERE event_key=?',(event_key('HANDOFF',row['mint'],row['migration_tx']),)).fetchone() if row['migration_tx'] else None
     if h and a[2]==h[1]:relation='AT_HANDOFF' if a[1]==h[0] else 'BEFORE_HANDOFF' if a[1]<h[0] else 'AFTER_HANDOFF'
   counts[category]+=1;timing[relation]+=1
  return {'total_canonical_migrations':len(rows),'source':counts,'timing':timing,'complete':True,'core_plan':plan,'semantics':'Core membership snapshot plus OPS evidence snapshot; not cross-database atomic time. UTC commit instants not claimed.'}
 finally:ops.rollback()
