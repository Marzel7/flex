"""Generic, provider-free post-commit membership-to-MonitorQueue bridge."""
from __future__ import annotations
import hashlib, json, sqlite3
from typing import Any

VERSION='monitor-live-admission.v1'; MAX_EVENT_BYTES=2048; NORMAL_BUDGET_BYTES=32*1024*1024; HARD_STOP_BYTES=48*1024*1024; MAX_PENDING=500; MAX_LOGICAL_RECORDS=12000; CLEANUP_MAX_ROWS=128
class ImmutableConflict(ValueError): pass
def _id(operation_id:str,mint:str,membership_id:str,committed_at:int,semantic_version:str,opening_id:str|None)->str:
 return hashlib.sha256(json.dumps([VERSION,operation_id,mint,membership_id,int(committed_at),semantic_version,opening_id],separators=(',',':')).encode()).hexdigest()
def ensure_schema(conn:sqlite3.Connection)->None:
 conn.execute("CREATE TABLE IF NOT EXISTS monitor_admission_outbox(event_id TEXT PRIMARY KEY,operation_id TEXT NOT NULL,mint TEXT NOT NULL,membership_id TEXT NOT NULL UNIQUE,committed_at INTEGER NOT NULL,semantic_version TEXT NOT NULL,opening_id TEXT,state TEXT NOT NULL DEFAULT 'PENDING',claimed_at INTEGER,delivered_at INTEGER)")
 conn.execute("CREATE INDEX IF NOT EXISTS ix_monitor_admission_outbox_pending ON monitor_admission_outbox(state,committed_at,event_id)")
 conn.execute("CREATE TABLE IF NOT EXISTS monitor_admission_conflicts(conflict_id TEXT PRIMARY KEY,membership_id TEXT NOT NULL,existing_event_id TEXT,new_event_id TEXT NOT NULL,reason TEXT NOT NULL,created_at INTEGER NOT NULL)")
def record_member(conn:sqlite3.Connection,*,operation_id:str,mint:str,membership_id:str,committed_at:int,semantic_version:str=VERSION,opening_id:str|None=None,max_pending:int=MAX_PENDING)->str:
 event_id=_id(operation_id,mint,membership_id,committed_at,semantic_version,opening_id)
 payload={'version':VERSION,'event_id':event_id,'operation_id':operation_id,'mint':mint,'membership_id':membership_id,'committed_at':int(committed_at),'semantic_version':semantic_version,'opening_id':opening_id}
 if len(json.dumps(payload,separators=(',',':')).encode())>MAX_EVENT_BYTES:raise ValueError('ENVELOPE_TOO_LARGE')
 existing=conn.execute("SELECT event_id,operation_id,mint,committed_at,semantic_version,opening_id FROM monitor_admission_outbox WHERE membership_id=?",(membership_id,)).fetchone()
 if existing:
  if tuple(existing)==(event_id,operation_id,mint,int(committed_at),semantic_version,opening_id): return event_id
  conflict_id=hashlib.sha256(json.dumps([membership_id,existing[0],event_id],separators=(',',':')).encode()).hexdigest()
  conn.execute("INSERT OR IGNORE INTO monitor_admission_conflicts VALUES(?,?,?,?,?,?)",(conflict_id,membership_id,existing[0],event_id,'IMMUTABLE_MEMBERSHIP_CONFLICT',int(committed_at)))
  raise ImmutableConflict('IMMUTABLE_MEMBERSHIP_CONFLICT')
 if conn.execute("SELECT count(*) FROM monitor_admission_outbox WHERE state='PENDING'").fetchone()[0]>=min(MAX_PENDING,max_pending):raise RuntimeError('OUTBOX_STORAGE_LIMIT')
 if conn.execute("SELECT count(*) FROM monitor_admission_outbox").fetchone()[0]>=MAX_LOGICAL_RECORDS:raise RuntimeError('OUTBOX_STORAGE_LIMIT')
 conn.execute("INSERT INTO monitor_admission_outbox(event_id,operation_id,mint,membership_id,committed_at,semantic_version,opening_id) VALUES(?,?,?,?,?,?,?)",(event_id,operation_id,mint,membership_id,int(committed_at),semantic_version,opening_id))
 return event_id
def commit_membership_and_outbox(conn:sqlite3.Connection,*,operation_id:str,mint:str,source_population_id:str,membership_id:str,committed_at:int,semantic_version:str=VERSION,opening_id:str|None=None)->str:
 """Same-source-transaction seam. Caller owns BEGIN/COMMIT; no destination access."""
 conn.execute("INSERT INTO operator_launch_membership(mint,operator_id,source_population_id,assigned_at,event_id) VALUES(?,?,?,?,?)",(mint,operation_id,source_population_id,int(committed_at),membership_id))
 from src.ops.canonical_membership_outbox import append_transition
 append_transition(conn,event_type='MEMBERSHIP_ASSIGNED',mint=mint,operator_id=operation_id,canonical_event_id=membership_id,assigned_at=int(committed_at),previous_operator_id=None,writer_identity='monitor_live_admission.commit_membership_and_outbox',created_at=int(committed_at))
 return record_member(conn,operation_id=operation_id,mint=mint,membership_id=membership_id,committed_at=committed_at,semantic_version=semantic_version,opening_id=opening_id)
def deliver_one(conn:sqlite3.Connection,queue:Any,*,now:int)->dict|None:
 row=conn.execute("SELECT event_id,operation_id,mint,membership_id,committed_at FROM monitor_admission_outbox WHERE state='PENDING' ORDER BY committed_at,event_id LIMIT 1").fetchone()
 if not row:return None
 event_id,operation_id,mint,membership_id,committed_at=row
 result=queue.enqueue_after_assignment(mint=mint,operation_id=operation_id,assignment={'event_id':membership_id,'assigned_at':committed_at},canonical_birth={'mint':mint,'monitor_admission_event_id':event_id})
 if result.get('status')!='ENQUEUED':return {'state':'DEFERRED','result':result}
 conn.execute("UPDATE monitor_admission_outbox SET state='DELIVERED',delivered_at=? WHERE event_id=? AND state='PENDING'",(int(now),event_id))
 return {'state':'DELIVERED','event_id':event_id,'job_id':result['job_id']}
def consume_once(source_db_path:str,queue:Any,*,now:int,claim_timeout:int=60,eligible=None)->dict|None:
 """Bounded consumer: source claim/commit, destination enqueue, source ack are separate transactions."""
 source=sqlite3.connect(source_db_path)
 try:
  # Probe before mutating: a truly idle or merely active-claimed outbox must
  # not acquire a SQLite write transaction on every bridge cadence tick.
  stale=source.execute("SELECT event_id FROM monitor_admission_outbox WHERE state='CLAIMED' AND claimed_at<? ORDER BY claimed_at,event_id LIMIT 1",(int(now)-int(claim_timeout),)).fetchone()
  if stale:
   source.execute("UPDATE monitor_admission_outbox SET state='PENDING',claimed_at=NULL WHERE event_id=? AND state='CLAIMED'",(stale[0],));source.commit()
  rows=source.execute("SELECT event_id,operation_id,mint,membership_id,committed_at FROM monitor_admission_outbox WHERE state='PENDING' ORDER BY committed_at,event_id LIMIT 128").fetchall()
  row=next((candidate for candidate in rows if eligible is None or eligible(candidate)),None)
  if not row:return None
  source.execute("UPDATE monitor_admission_outbox SET state='CLAIMED',claimed_at=? WHERE event_id=? AND state='PENDING'",(int(now),row[0]));source.commit()
 finally: source.close()
 event_id,operation_id,mint,membership_id,committed_at=row
 result=queue.enqueue_after_assignment(mint=mint,operation_id=operation_id,assignment={'event_id':membership_id,'assigned_at':committed_at},canonical_birth={'mint':mint,'monitor_admission_event_id':event_id})
 source=sqlite3.connect(source_db_path)
 try:
  if result.get('status')=='ENQUEUED':source.execute("UPDATE monitor_admission_outbox SET state='DELIVERED',delivered_at=? WHERE event_id=? AND state='CLAIMED'",(int(now),event_id));source.commit();return {'state':'DELIVERED','event_id':event_id,'job_id':result['job_id']}
  source.execute("UPDATE monitor_admission_outbox SET state='PENDING',claimed_at=NULL WHERE event_id=? AND state='CLAIMED'",(event_id,));source.commit();return {'state':'DEFERRED','result':result}
 finally: source.close()
def cleanup_acked(conn:sqlite3.Connection,*,limit:int=CLEANUP_MAX_ROWS)->int:
 """Bounded compaction preserves event identity by retaining delivered rows; no deletion in V1."""
 return 0
