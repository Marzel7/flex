"""Compact durable state for universal external calls; no raw payload retention."""
from __future__ import annotations
import hashlib,json,sqlite3,time
STATES=('PLANNED','BUDGET_ADMITTED','PROVIDER_CALL_STARTED','PROVIDER_CALL_COMPLETED','RESULT_COMMITTED','AMBIGUOUS_PROVIDER_CALL','FAILED_CLOSED')
_NEXT={'PLANNED':{'BUDGET_ADMITTED','FAILED_CLOSED'},'BUDGET_ADMITTED':{'PROVIDER_CALL_STARTED','FAILED_CLOSED'},'PROVIDER_CALL_STARTED':{'PROVIDER_CALL_COMPLETED','AMBIGUOUS_PROVIDER_CALL'},'PROVIDER_CALL_COMPLETED':{'RESULT_COMMITTED','FAILED_CLOSED'}}
def ident(v):return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def ensure(conn):
 conn.executescript('''CREATE TABLE IF NOT EXISTS universal_provider_calls(provider_call_id TEXT PRIMARY KEY,work_id TEXT NOT NULL,operation_id TEXT NOT NULL,mint TEXT NOT NULL,provider_class TEXT NOT NULL,provider_method TEXT NOT NULL,request_kind TEXT NOT NULL,request_identity_hash TEXT NOT NULL,attempt_number INTEGER NOT NULL,budget_admission_id TEXT,state TEXT NOT NULL,planned_at INTEGER,budget_admitted_at INTEGER,call_started_at INTEGER,call_completed_at INTEGER,result_committed_at INTEGER,response_status_class TEXT,compact_result_reference TEXT,failure_class TEXT,ambiguity_reason TEXT,created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL,UNIQUE(work_id,request_kind,attempt_number));CREATE TABLE IF NOT EXISTS universal_provider_results(provider_call_id TEXT PRIMARY KEY,compact_json TEXT NOT NULL,created_at INTEGER NOT NULL);CREATE TABLE IF NOT EXISTS universal_dispatcher_instances(dispatcher_instance_id TEXT PRIMARY KEY,process_role TEXT NOT NULL,pid INTEGER NOT NULL,started_at INTEGER NOT NULL,code_head TEXT NOT NULL,authority_database TEXT NOT NULL,created_at INTEGER NOT NULL);''')
 columns={row[1] for row in conn.execute('PRAGMA table_info(universal_provider_calls)')}
 for name, typ in (("dispatcher_instance_id","TEXT"),("executor_role","TEXT"),("executor_pid","INTEGER"),("executor_started_at","INTEGER"),("executor_head","TEXT"),("authority_database","TEXT")):
  if name not in columns: conn.execute(f'ALTER TABLE universal_provider_calls ADD COLUMN {name} {typ}')
 conn.commit()
def register_dispatcher_instance(conn, caller):
 """Persist compact managed-process identity before it can dispatch work."""
 required=('dispatcher_instance_id','process_role','pid','started_at','code_head','authority_database')
 if any(caller.get(key) in (None,'') for key in required): raise ValueError('CALLER_PROVENANCE_REQUIRED')
 ensure(conn)
 conn.execute('INSERT OR IGNORE INTO universal_dispatcher_instances VALUES(?,?,?,?,?,?,?)',(caller['dispatcher_instance_id'],caller['process_role'],int(caller['pid']),int(caller['started_at']),caller['code_head'],caller['authority_database'],int(time.time())))
 conn.commit()
def plan(conn,**v):
 caller=v.get('caller') or {}
 required=('dispatcher_instance_id','process_role','pid','started_at','code_head','authority_database')
 if any(caller.get(key) in (None,'') for key in required): raise ValueError('CALLER_PROVENANCE_REQUIRED')
 register_dispatcher_instance(conn,caller)
 now=int(time.time()); i=ident({k:v[k] for k in ('work_id','provider_method','request_kind','attempt_number')});conn.execute('INSERT OR IGNORE INTO universal_provider_calls(provider_call_id,work_id,operation_id,mint,provider_class,provider_method,request_kind,request_identity_hash,attempt_number,state,planned_at,created_at,updated_at,dispatcher_instance_id,executor_role,executor_pid,executor_started_at,executor_head,authority_database) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(i,v['work_id'],v['operation_id'],v['mint'],v['provider_class'],v['provider_method'],v['request_kind'],v['request_identity_hash'],v.get('attempt_number',1),'PLANNED',now,now,now,caller['dispatcher_instance_id'],caller['process_role'],int(caller['pid']),int(caller['started_at']),caller['code_head'],caller['authority_database']));conn.commit();return i
def transition(conn,i,to,**fields):
 row=conn.execute('select state from universal_provider_calls where provider_call_id=?',(i,)).fetchone()
 if not row or to not in _NEXT.get(row[0],set()):raise ValueError('INVALID_PROVIDER_CALL_TRANSITION')
 now=int(time.time()); cols=['state','updated_at'];vals=[to,now]
 for k,v in fields.items():cols.append(k);vals.append(v)
 if to=='BUDGET_ADMITTED':cols+=['budget_admitted_at'];vals+=[now]
 if to=='PROVIDER_CALL_STARTED':cols+=['call_started_at'];vals+=[now]
 if to=='PROVIDER_CALL_COMPLETED':cols+=['call_completed_at'];vals+=[now]
 if to=='RESULT_COMMITTED':cols+=['result_committed_at'];vals+=[now]
 conn.execute('update universal_provider_calls set '+','.join(f'{x}=?' for x in cols)+' where provider_call_id=?',(*vals,i));conn.commit()
def store_result(conn,i,result):
 compact=json.dumps(result,sort_keys=True,separators=(',',':'));conn.execute('insert or replace into universal_provider_results values(?,?,?)',(i,compact,int(time.time())));conn.commit();transition(conn,i,'PROVIDER_CALL_COMPLETED',compact_result_reference=ident(result),response_status_class='SUCCESS')
def recover(conn):
 conn.execute("update universal_provider_calls set state='AMBIGUOUS_PROVIDER_CALL',ambiguity_reason='CRASH_AFTER_CALL_START',updated_at=? where state='PROVIDER_CALL_STARTED' and provider_call_id not in (select provider_call_id from universal_provider_results)",(int(time.time()),));conn.commit()
