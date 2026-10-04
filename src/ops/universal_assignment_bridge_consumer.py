"""Provider-free durable canonical-assignment service; all external work is injected."""
from __future__ import annotations
import hashlib, json, os, sqlite3, time
from dataclasses import dataclass
from typing import Callable, Iterable, Mapping

ROLE="universal-assignment-bridge-consumer-v1"; VERSION="UNIVERSAL_ASSIGNMENT_BRIDGE_CONSUMER_V1"
TARGETED_PEPEINU="264b63c1-6b6f-56d5-a0e7-36e331167fd5"
REGISTRY={"watchtower":"FIRST_AVAILABLE","byzantine":"BYZANTINE_ACTUAL_ENTRY_V2"}
def _id(kind,event): return hashlib.sha256((kind+":"+event).encode()).hexdigest()
def projection_id(event): return _id("universal-assignment-projection-v1",event)
def handoff_id(event): return _id("universal-assignment-handoff-v1",event)

def ensure_schema(db):
 db.execute("PRAGMA max_page_count=16384") # hard 64 MiB file ceiling; no raw payload storage
 db.executescript('''CREATE TABLE IF NOT EXISTS universal_assignment_projection(projection_id TEXT PRIMARY KEY,assignment_event_id TEXT UNIQUE NOT NULL,operation_id TEXT NOT NULL,mint TEXT NOT NULL,assigned_at INTEGER NOT NULL,source_authority TEXT NOT NULL,destination_authority TEXT NOT NULL,adapter TEXT,state TEXT NOT NULL,opening_json TEXT,handoff_identity TEXT,consumer_instance TEXT NOT NULL,consumer_version TEXT NOT NULL,created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL,held_reason TEXT);CREATE TABLE IF NOT EXISTS universal_assignment_cursor(name TEXT PRIMARY KEY,assigned_at INTEGER NOT NULL DEFAULT 0,event_id TEXT NOT NULL DEFAULT '',initial_boundary INTEGER NOT NULL,last_consumed_outbox_id INTEGER);CREATE TABLE IF NOT EXISTS universal_assignment_lease(singleton_identity TEXT PRIMARY KEY,instance_id TEXT NOT NULL,pid INTEGER NOT NULL,acquired_at INTEGER NOT NULL,heartbeat_at INTEGER NOT NULL);CREATE TABLE IF NOT EXISTS universal_assignment_status(name TEXT PRIMARY KEY,value TEXT NOT NULL,updated_at INTEGER NOT NULL);''')
 cols={r[1] for r in db.execute('PRAGMA table_info(universal_assignment_cursor)')}
 if 'last_consumed_outbox_id' not in cols: db.execute('ALTER TABLE universal_assignment_cursor ADD COLUMN last_consumed_outbox_id INTEGER')
 db.commit()

@dataclass(frozen=True)
class Authorities:
 source:str; destination:str; qualified_source:str; qualified_destination:str
 def validate(self):
  if (self.source,self.destination)!=(self.qualified_source,self.qualified_destination): raise RuntimeError("AUTHORITY_CONTRACT_MISMATCH")

class Consumer:
 """Cursor is canonical `outbox_id`; legacy tuple columns remain only for safe schema compatibility.
 Held events are durable and advance the forward cursor, preventing a poison busy loop.
 """
 def __init__(self,db,authorities,instance_id,initial_boundary,pinned_head,qualified_head):
  ensure_schema(db);authorities.validate()
  if pinned_head!=qualified_head: raise RuntimeError("UNQUALIFIED_HEAD")
  self.db,self.authorities,self.instance_id,self.initial_boundary,self.pinned_head=db,authorities,instance_id,initial_boundary,pinned_head
 def acquire_lease(self,now=None):
  now=int(time.time()) if now is None else now; ident=f"{ROLE}:{self.authorities.source}:{self.authorities.destination}"
  row=self.db.execute("select instance_id from universal_assignment_lease where singleton_identity=?",(ident,)).fetchone()
  if row and row[0]!=self.instance_id: raise RuntimeError("CONSUMER_SINGLETON_HELD")
  with self.db:self.db.execute("insert into universal_assignment_lease values(?,?,?,?,?) on conflict(singleton_identity) do update set instance_id=excluded.instance_id,pid=excluded.pid,heartbeat_at=excluded.heartbeat_at",(ident,self.instance_id,os.getpid(),now,now))
 def cursor(self):
  row=self.db.execute("select last_consumed_outbox_id from universal_assignment_cursor where name='forward'").fetchone()
  return int(self.initial_boundary) if not row or row[0] is None else int(row[0])
 def has_cursor(self):
  return self.db.execute("select 1 from universal_assignment_cursor where name='forward'").fetchone() is not None
 def _advance(self,e):
  with self.db:self.db.execute("insert into universal_assignment_cursor(name,assigned_at,event_id,initial_boundary,last_consumed_outbox_id) values('forward',?,?,?,?) on conflict(name) do update set last_consumed_outbox_id=excluded.last_consumed_outbox_id",(0,'',self.initial_boundary,int(e['outbox_id'])))
 def _project(self,e,now):
  p=projection_id(str(e['outbox_id']));row=self.db.execute("select * from universal_assignment_projection where projection_id=?",(p,)).fetchone()
  if row:return row
  op=str(e['operation_id']).lower()
  with self.db:self.db.execute("insert into universal_assignment_projection values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(p,str(e['outbox_id']),op,e['mint'],int(e.get('assigned_at') or e['created_at']),self.authorities.source,self.authorities.destination,REGISTRY.get(op),'PROJECTED',None,None,self.instance_id,VERSION,now,now,None))
  return self.db.execute("select * from universal_assignment_projection where projection_id=?",(p,)).fetchone()
 def _set(self,eid,state,now,opening=None,held=None,handoff=None):
  with self.db:self.db.execute("update universal_assignment_projection set state=?,opening_json=coalesce(?,opening_json),held_reason=coalesce(?,held_reason),handoff_identity=coalesce(?,handoff_identity),updated_at=? where assignment_event_id=?",(state,json.dumps(opening,sort_keys=True) if opening is not None else None,held,handoff,now,eid))
 def process(self,e,adapter_executor,universal_handoff,now=None,fault=None,targeted=False):
  now=int(time.time()) if now is None else now
  if e.get('source_authority')!=self.authorities.source:raise RuntimeError("NONCANONICAL_EVENT")
  # Before the first persisted cursor, the explicit installation boundary is
  # exclusive: no historical event at or before it may be auto-projected.
  if not targeted and int(e['outbox_id'])<=self.cursor():return 'ALREADY_BEHIND_CURSOR'
  if fault=='before_projection':raise RuntimeError('INJECTED_CRASH_BEFORE_PROJECTION')
  row=self._project(e,now);state,adapter=row[8],row[7]
  if state=='UNIVERSAL_HANDED_OFF':
   if not targeted:self._advance(e)
   return state
  if state in {'ADAPTER_UNAVAILABLE','AMBIGUOUS_ADAPTER','FAILED_CLOSED'}:
   if not targeted:self._advance(e)
   return state
  if fault=='after_projection':raise RuntimeError('INJECTED_CRASH_AFTER_PROJECTION')
  if not adapter:
   self._set(e['outbox_id'],'ADAPTER_UNAVAILABLE',now,held='NO_REGISTERED_ADAPTER')
   if not targeted:self._advance(e)
   return 'ADAPTER_UNAVAILABLE'
  opening=json.loads(row[9]) if row[9] else None
  if opening is None:
   try: opening=dict(adapter_executor(adapter,e))
   except TimeoutError:
    self._set(e['outbox_id'],'AMBIGUOUS_ADAPTER',now,held='ADAPTER_EXECUTION_AMBIGUOUS')
    if not targeted:self._advance(e)
    return 'AMBIGUOUS_ADAPTER'
   if not opening.get('qualified') or opening.get('timestamp') is None or opening.get('mc_usd') is None:
    self._set(e['outbox_id'],'FAILED_CLOSED',now,held='OPENING_NOT_QUALIFIED')
    if not targeted:self._advance(e)
    return 'FAILED_CLOSED'
   self._set(e['outbox_id'],'OPENING_QUALIFIED',now,opening=opening)
   if fault=='after_opening':raise RuntimeError('INJECTED_CRASH_AFTER_OPENING')
  hid=handoff_id(str(e['outbox_id']));universal_handoff(e,opening,hid);self._set(e['outbox_id'],'UNIVERSAL_HANDED_OFF',now,handoff=hid)
  if fault=='after_handoff':raise RuntimeError('INJECTED_CRASH_AFTER_HANDOFF')
  if not targeted:self._advance(e)
  return 'UNIVERSAL_HANDED_OFF'
 def run_once(self,read_events,adapter_executor,universal_handoff,now=None):
  self.acquire_lease(now);return [self.process(e,adapter_executor,universal_handoff,now) for e in read_events(self.cursor())]
 def run_forever(self,read_events,adapter_executor,universal_handoff,stop,wait_seconds=1):
  """Executable loop; authority-specific I/O remains entirely injected.

  A managed launcher supplies the read-only canonical reader, registered
  Opening adapter executor, and universal writer.  This function supplies no
  provider or operation-specific path of its own.
  """
  while not stop():
   self.run_once(read_events,adapter_executor,universal_handoff)
   time.sleep(wait_seconds)
 def reconcile_pepeinu(self,e,adapter_executor,universal_handoff,now=None):
  if e.get('canonical_event_id')!=TARGETED_PEPEINU:raise ValueError('TARGETED_RECONCILIATION_ID_REQUIRED')
  return self.process(e,adapter_executor,universal_handoff,now,targeted=True)
 def health(self):
  last=self.db.execute("select assignment_event_id,state from universal_assignment_projection order by updated_at desc limit 1").fetchone()
  return {'state':'READY','instance_id':self.instance_id,'version':VERSION,'head':self.pinned_head,'source_authority':self.authorities.source,'destination_authority':self.authorities.destination,'cursor':self.cursor(),'initial_forward_boundary':self.initial_boundary,'last_projection':None if not last else last[0],'last_state':None if not last else last[1]}
