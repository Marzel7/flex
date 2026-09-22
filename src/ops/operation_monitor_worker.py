"""Durable Monitor queue -> provider -> shared writer -> ACK."""
from __future__ import annotations
import hashlib,json,os,sqlite3,time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any,Callable
from src.core.db_write_queue import WriteItem
from src.core.db_writer import DurableWriteReceipt,commit_write_and_wait
from src.evidence.queue import EvidenceIntakeQueue
from src.ops.operation_price_research_orchestrator import adapter_for
from src.ops.token_data_provider_bindings import BirdeyeProductionBinding,build_birdeye_ohlcv_request
from src.ops.birdeye_rate_limit import wait_seconds
from src.ops.byzantine_monitor_entry import derive_monitor_entry
from src.ops.watchtower_terminal_ath_finalizer import ProviderCapacityBackoff, WatchtowerTerminalAthFinalizer
CONTRACT='operation-monitor.v1'; CONCURRENCY=1; MAX_BYTES=10_000_000
def _h(x:Any)->str:return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':'),default=str).encode()).hexdigest()


@contextmanager
def _read_only_connection(path: str, *, timeout: float = 5):
 """Own and close one read-only SQLite connection.

 ``sqlite3.Connection.__exit__`` commits or rolls back but does not close a
 native connection.  Monitor uses URI mode=ro, which can bypass the project's
 path-based connection wrapper, so closure must be explicit here.
 """
 con=sqlite3.connect(f'file:{Path(path).resolve()}?mode=ro',uri=True,timeout=timeout)
 try:
  yield con
 finally:
  con.close()


_OPERATION_IDS={'04265d9f-6eb2-568c-a49e-9253091a4dbb':'watchtower','d8ee4d7a-fcd6-5a5b-b897-24f6ab56e334':'byzantine'}
def canonical_operation_id(value: str) -> str:
 return _OPERATION_IDS.get(str(value).lower(),str(value).lower())
@dataclass
class MonitorQueue:
 root:Path; enabled:bool=False
 def __post_init__(self):self.queue=EvidenceIntakeQueue(self.root,enabled=self.enabled,max_messages=500,max_bytes=MAX_BYTES,max_attempts=3)
 def enqueue_after_assignment(self,*,mint,operation_id,assignment,canonical_birth):
  operation_id=canonical_operation_id(operation_id)
  a=adapter_for(operation_id); ident=_h({'operation_id':operation_id,'mint':mint,'assignment':_h(assignment),'contract':CONTRACT})
  if a['method']=='ENTRY_METHOD_UNQUALIFIED':return {'status':'NOT_ENQUEUED_ENTRY_UNQUALIFIED'}
  # Identity is assignment-only; entry evidence is downstream mutable state.
  payload={'mint':mint,'operation_id':operation_id,'assignment':assignment,'assignment_digest':_h(assignment),'birth':canonical_birth,'scenario_d_evidence':canonical_birth.get('scenario_d_evidence'),'entry_method':a['method'],'entry_timestamp':canonical_birth.get('entry_timestamp'),'entry_mc_usd':canonical_birth.get('entry_mc_usd'),'candle_resolution':a['candle_resolution'],'monitor_state':'WAITING_FOR_ENTRY_REFERENCE','cohort':'PROSPECTIVE_MONITOR_COHORT','contract':CONTRACT}
  return {'status':'ENQUEUED','job_id':self.queue.enqueue(payload,message_id=ident)}
 def enqueue_recovery(self,*,dead_letter_id,mint,operation_id,assignment,entry_timestamp,entry_mc_usd):
  """Explicit new identity; retains immutable linkage to a bounded dead-letter."""
  a=adapter_for(operation_id)
  payload={'mint':mint,'operation_id':operation_id,'assignment':assignment,'entry_method':a['method'],'entry_timestamp':entry_timestamp,'entry_mc_usd':entry_mc_usd,'candle_resolution':a['candle_resolution'],'cohort':'PROSPECTIVE_MONITOR_COHORT','contract':CONTRACT,'recovery_of':dead_letter_id}
  ident=_h({'recovery_of':dead_letter_id,'entry_timestamp':entry_timestamp,'contract':CONTRACT})
  return {'status':'ENQUEUED_RECOVERY','job_id':self.queue.enqueue(payload,message_id=ident)}
 def enqueue_terminal_ath_finalization(self, fact, *, provenance='POST_COMMIT_TERMINAL_COLLAPSE'):
  """Durable post-commit work for one collapsed prospective Watchtower lifecycle."""
  if (fact.get('operation_id')!='watchtower' or fact.get('cohort_class')!='PROSPECTIVE_MONITOR_COHORT' or
      fact.get('monitor_state')!='PRICE_MONITOR_COMPLETE_COLLAPSED' or fact.get('next_observation_at') is not None or
      fact.get('final_proven_ath_mc') is not None): return {'status':'NOT_ENQUEUED'}
  ident=WatchtowerTerminalAthFinalizer.logical_job_identity(fact)
  envelope={'work_type':'WATCHTOWER_TERMINAL_ATH_FINALIZATION','operation_id':'watchtower','mint':fact['mint'],
            'cohort':'PROSPECTIVE_MONITOR_COHORT','entry_method':fact['entry_method'],
            'entry_timestamp':int(fact['entry_timestamp']),'entry_mc_usd':float(fact['entry_mc_usd']),
            'terminal_timestamp':int(fact['monitor_completed_at']),'resolution':'15m',
            'finalizer_contract':'watchtower-terminal-ath.v1','logical_identity':ident,'provenance':provenance}
  return {'status':'ENQUEUED_TERMINAL_ATH','job_id':self.queue.enqueue(envelope,message_id=ident)}
 def _backoff_path(self): return self.queue.root/'provider_backoff.json'
 def provider_backoff(self):
  try: return json.loads(self._backoff_path().read_text())
  except (OSError,ValueError): return None
 def provider_eligible(self, *, now=None):
  state=self.provider_backoff() or {}; return int(state.get('backoff_until') or 0)<=int(time.time() if now is None else now)
 def _record_provider_backoff(self, *, eligible, error):
  payload={'provider':'BIRDEYE','provider_backoff_reason':error,'backoff_until':int(eligible),'updated_at':int(time.time())}
  path=self._backoff_path(); path.parent.mkdir(parents=True,exist_ok=True)
  temporary=path.parent/(f'.{path.name}.{os.getpid()}.tmp')
  temporary.write_text(json.dumps(payload,sort_keys=True,separators=(',',':'))+'\n'); os.replace(temporary,path); self.queue._fsync_directory(path.parent)
 def defer_rate_limited(self,claimed,*,retry_after=None,error='HTTP_429'):
  """Persist shared Birdeye capacity backoff without consuming queue retry budget."""
  payload=dict(claimed.payload); envelope=dict(payload.get('envelope') or {})
  number=int(envelope.get('provider_capacity_backoff_count') or 0)+1
  delay=wait_seconds(retry_after,number); eligible=int(time.time())+delay
  envelope.update({'monitor_state':'PROVIDER_BACKOFF','provider_backoff_reason':error,
                   'provider_capacity_backoff_count':number,'backoff_until':eligible,
                   'next_eligible_dispatch_at':eligible,'last_provider_status':429})
  payload['envelope']=envelope;payload['last_error']=error;payload['last_attempt_at']=int(time.time())
  self._record_provider_backoff(eligible=eligible,error=error)
  self.queue._replace_payload(claimed.path,payload)
  target=self.queue.root/'retry'/claimed.path.name;os.replace(claimed.path,target)
  self.queue._fsync_directory(claimed.path.parent);self.queue._fsync_directory(target.parent)
 def recover_due(self,*,now=None):
  """The service invokes this timer; it never sleeps or holds a DB connection."""
  timestamp=int(time.time() if now is None else now); moved=0
  for path in sorted((self.queue.root/'retry').glob('*.json')):
   try:
    payload=json.loads(path.read_text()); e=payload.get('envelope') or {}
    # Retrofitted legacy 429 messages receive the first shared-policy deadline.
    if e.get('provider_backoff_reason')!='HTTP_429' and 'HTTP_429' not in str(payload.get('last_error','')): continue
    eligible=int(e.get('next_eligible_dispatch_at') or (int(payload.get('last_attempt_at') or timestamp)+wait_seconds(None,1)))
    if eligible>timestamp: continue
    e.update({'monitor_state':'ENTRY_REFERENCE_QUALIFIED' if e.get('entry_timestamp') else 'WAITING_FOR_ENTRY_REFERENCE','next_eligible_dispatch_at':None});payload['envelope']=e
    self.queue._replace_payload(path,payload);target=self.queue.root/'pending'/path.name;os.replace(path,target);self.queue._fsync_directory(path.parent);self.queue._fsync_directory(target.parent);moved+=1
   except (OSError,ValueError,TypeError): continue
  return moved
def production_queue():
 infrastructure=os.getenv('MONITOR_INFRASTRUCTURE_ENABLED','false').lower()=='true'; live=os.getenv('OPERATIONS_MODE','OFF').upper()=='MONITOR'
 return MonitorQueue(Path(os.getenv('OPERATION_MONITOR_QUEUE_PATH','database/evidence_platform/operation_monitor_jobs')),enabled=live or infrastructure)
def reconcile_byzantine_assignment_admissions(db_path:str,q:MonitorQueue|None=None)->dict[str,int]:
 """Repair only post-activation Byzantine memberships missing Monitor admission.

 Membership is read from the authoritative operations database.  The queue is
 the existing EvidenceIntakeQueue and identity remains assignment-derived.  A
 legacy dead-letter caused solely by a missing Scenario-D producer is restored
 as the same logical waiting job, never copied into a second identity.
 """
 q=q or production_queue(); result={'examined':0,'enqueued':0,'restored_waiting':0,'already_present':0}
 if not q.enabled:return result
 with _read_only_connection(db_path) as con:
  con.row_factory=sqlite3.Row
  activation=con.execute("SELECT MAX(effective_at) FROM operation_monitor_mode_transitions WHERE mode='MONITOR'").fetchone()[0]
  if activation is None:return result
  assignments=[dict(r) for r in con.execute("SELECT m.mint,m.assigned_at,m.event_id FROM operator_launch_membership m WHERE m.operator_id=? AND m.assigned_at>=? ORDER BY m.assigned_at",('d8ee4d7a-fcd6-5a5b-b897-24f6ab56e334',int(activation)))]
 for assignment in assignments:
  result['examined']+=1; existing=None
  for state in q.queue.STATES:
   for path in (q.queue.root/state).glob('*.json'):
    try:
     payload=json.loads(path.read_text()); envelope=payload.get('envelope') or {}
     if canonical_operation_id(envelope.get('operation_id',''))=='byzantine' and envelope.get('mint')==assignment['mint']:
      existing=(state,path,payload,envelope);break
    except (OSError,ValueError,TypeError):continue
   if existing:break
  if not existing:
   q.enqueue_after_assignment(mint=assignment['mint'],operation_id='byzantine',assignment=assignment,canonical_birth={'mint':assignment['mint'],'admission_provenance':'MISSED_POST_COMMIT_BYZANTINE_MONITOR_RECONCILIATION'})
   result['enqueued']+=1;continue
  state,path,payload,envelope=existing
  if state=='dead_letter' and str(envelope.get('entry_evaluation_result','')).startswith('INSUFFICIENT_EVIDENCE') and not envelope.get('scenario_d_evidence'):
   envelope.update({'monitor_state':'WAITING_FOR_ENTRY_REFERENCE','entry_evaluation_result':'WAITING_FOR_ENTRY_REFERENCE','entry_evidence_work_type':'BYZANTINE_ENTRY_EVIDENCE_DUE','missing_entry_evidence':['committed Scenario-D 12/13 envelope'],'admission_provenance':'MISSED_POST_COMMIT_BYZANTINE_MONITOR_RECONCILIATION','next_entry_evaluation_at':None})
   payload['envelope']=envelope;payload.pop('last_error',None);q.queue._replace_payload(path,payload)
   target=q.queue.root/'pending'/path.name;os.replace(path,target);q.queue._fsync_directory(path.parent);q.queue._fsync_directory(target.parent);result['restored_waiting']+=1
  else:result['already_present']+=1
 return result
class MonitorBirdeyeTransport:
 def __init__(self,binding=None):self.binding=binding or BirdeyeProductionBinding()
 def __call__(self,p):
  now=int(time.time()); start=p.get('last_observation_at') or p.get('entry_timestamp'); interval=p.get('candle_resolution')
  if not start or not interval: raise ValueError('WAITING_FOR_ENTRY_REFERENCE')
  start=int(start)
  if start > now: raise ValueError('INSUFFICIENT_EVIDENCE:ENTRY_AFTER_NOW')
  built=build_birdeye_ohlcv_request(address=p['mint'],interval=interval,time_from=start,time_to=now); params=built['request_parameters']
  result=self.binding(built)
  if result.status_code!=200:
   # Bounded non-secret diagnostic: provider code/message only, never raw body.
   body=result.payload if isinstance(result.payload,dict) else {}
   data=body.get('data') if isinstance(body.get('data'),dict) else {}
   code=body.get('code',data.get('code',''))
   message=body.get('message',body.get('msg',data.get('message',data.get('msg',''))))
   diagnostic={'http_status':result.status_code,'code':str(code)[:80],'message':str(message)[:240],'shape':sorted(body)[:20]}
   raise ConnectionError(f'{result.error_state or "HTTP_"+str(result.status_code)}:{json.dumps(diagnostic,sort_keys=True,separators=(",",":"))}')
  items=((result.payload or {}).get('data') or {}).get('items') or []
  candles=[{'timestamp':int(x.get('unixTime',x.get('unix_time',x.get('timestamp',0)))),'open':float(x.get('o',x.get('open',x.get('c',0))) or 0),'high':float(x.get('h',x.get('high',x.get('c',0))) or 0),'low':float(x.get('l',x.get('low',x.get('c',0))) or 0),'mc':float(x.get('c',x.get('close',x.get('value',0))) or 0)} for x in items]
  return {'candles':[x for x in candles if x['timestamp'] and x['mc']>0],'resolution':params['type'],'http_status':200,'request':params,'request_manifest':built}
 def acquire_watchtower_entry(self,p,plan):
  """Acquire only the frozen two-second entry window after retained evidence is audited."""
  built=build_birdeye_ohlcv_request(address=p['mint'],interval='1s',time_from=plan['time_from'],time_to=plan['time_to'])
  result=self.binding(built)
  if result.status_code != 200:
   raise ConnectionError('ENTRY_ACQUISITION_'+str(result.error_state or result.status_code))
  items=((result.payload or {}).get('data') or {}).get('items') or []
  candles=sorted(({'timestamp':int(x.get('unixTime',x.get('unix_time',0))),'mc':float(x.get('c',x.get('close',0)) or 0)} for x in items),key=lambda x:x['timestamp'])
  first=next((x for x in candles if x['timestamp']>plan['migration_timestamp'] and x['mc']>0),None)
  if not first: raise ValueError('INSUFFICIENT_EVIDENCE:FIRST_FULL_POST_MIGRATION_SECOND_MC_NOT_RETURNED')
  return first,built

def _watchtower_entry_inventory(db_path: str, mint: str) -> dict[str,Any]:
 """Read-only retained-evidence inventory; this function never owns a write transaction."""
 con=sqlite3.connect(f'file:{Path(db_path).resolve()}?mode=ro',uri=True)
 try:
  try:
   row=con.execute('SELECT migration_tx,migration_time,pool FROM migrated_tokens WHERE mint=?',(mint,)).fetchone()
   if not row:
    row=con.execute('SELECT migration_signature,migration_time,NULL FROM wt_launch_audit WHERE mint=?',(mint,)).fetchone()
  except sqlite3.OperationalError: row=None
 finally: con.close()
 if not row or not row[1]:
  return {'result':'WAITING_FOR_ENTRY_REFERENCE','reason':'ENTRY_EVENT_NOT_OCCURRED','missing_evidence':['migration_timestamp','migration_signature'],'next_entry_evaluation_at':int(time.time())+60}
 signature,timestamp,pool=row
 return {'result':'ENTRY_EVIDENCE_ACQUISITION_DUE','reason':'RETAINED_EVIDENCE_MISSING','migration_signature':signature,'migration_timestamp':int(timestamp),'expected_pool':pool,'missing_evidence':['FIRST_FULL_POST_MIGRATION_SECOND_MC'],'entry_acquisition':{'provider':'Birdeye','endpoint':'/defi/v3/ohlcv','interval':'1s','time_from':int(timestamp),'time_to':int(timestamp)+2,'migration_timestamp':int(timestamp)},'next_entry_evaluation_at':int(time.time())}

def _entry_inventory(db_path: str,p:dict[str,Any])->dict[str,Any]:
 if p['operation_id'].lower()=='watchtower': return _watchtower_entry_inventory(db_path,p['mint'])
 result=derive_monitor_entry(p.get('scenario_d_evidence'))
 result['next_entry_evaluation_at']=None
 return result
class MonitorWorker:
 def __init__(self,q,transport=None,persist=None,*,db_path=None,before_ack=None,terminal_finalizer_factory=None):
  self.q=q;self.transport=transport or MonitorBirdeyeTransport();self.db_path=db_path or os.getenv('DATABASE_PATH','database/flex_complete_database.db')
  if persist is None:self.persist=lambda item:commit_write_and_wait(self.db_path,item)
  else:
   # Injection is retained solely for offline tests; production never takes it.
   def test_persist(item):
    result=persist(item)
    return result if isinstance(result,DurableWriteReceipt) else DurableWriteReceipt(bool(result),time.time() if result else None,len(item.statements),'TEST_PERSIST_FAILED' if not result else None)
   self.persist=test_persist
  self.before_ack=before_ack;self.heartbeat=None;self.last_receipt=None;self.last_ack_timestamp=None
  self.terminal_finalizer_factory=terminal_finalizer_factory or WatchtowerTerminalAthFinalizer
 def _persist_waiting_entry_fact(self,p:dict[str,Any],evaluation:dict[str,Any])->None:
  """Durably expose assignment-first Monitor admission without a price fact.

  This is intentionally a single shared-writer operation after retained-evidence
  evaluation.  It does not open a DB connection around provider work (and this
  branch does no provider work at all).
  """
  now=int(time.time());assignment=p.get('assignment') or {'digest':p.get('assignment_digest')}
  state=evaluation['result']; evidence_status=evaluation.get('reason') or state
  values=(p['operation_id'],p['mint'],p.get('cohort','PROSPECTIVE_MONITOR_COHORT'),assignment.get('assigned_at',now),_h(assignment),p.get('entry_method'),None,None,state,'UNQUALIFIED',state,now,evidence_status,_h({'assignment':assignment,'evaluation':evaluation}),now,now)
  sql='''INSERT INTO operation_monitor_facts(operation_id,mint,cohort_class,assignment_timestamp,assignment_provenance,entry_method,entry_timestamp,entry_mc_usd,entry_status,entry_exactness,monitor_state,next_observation_at,evidence_status,provenance_digest,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(operation_id,mint) DO UPDATE SET monitor_state=excluded.monitor_state,next_observation_at=excluded.next_observation_at,evidence_status=excluded.evidence_status,provenance_digest=excluded.provenance_digest,updated_at=excluded.updated_at WHERE operation_monitor_facts.entry_status!='QUALIFIED' '''
  receipt=self.persist(WriteItem('enrichment','operation-monitor-waiting-entry',[(sql,values)],_h({'waiting_entry':p['operation_id'],'mint':p['mint'],'evaluation':evaluation})))
  if not receipt or not receipt.committed: raise RuntimeError('MONITOR_WAITING_FACT_UNCOMMITTED')
 def reconcile_retained_watchtower_facts(self):
  """Re-reduce retained prospective observations; no provider call or DB lease spans work."""
  with _read_only_connection(self.db_path) as con:
   con.row_factory=sqlite3.Row
   facts=[dict(x) for x in con.execute("SELECT * FROM operation_monitor_facts WHERE operation_id='watchtower' AND monitor_state='MONITORING_ACTIVE'")]
   reduced=[]
   for fact in facts:
    obs=[dict(x) for x in con.execute('SELECT observation_timestamp,mc_usd,high_mc_usd FROM operation_monitor_observations WHERE operation_id=? AND mint=? ORDER BY observation_timestamp',(fact['operation_id'],fact['mint']))]
    if not obs: continue
    entry=float(fact['entry_mc_usd']); latest=obs[-1]; peak=max([{'timestamp':int(fact['entry_timestamp']),'mc':entry}]+[{'timestamp':int(x['observation_timestamp']),'mc':float(x['high_mc_usd'] if x['high_mc_usd'] is not None else x['mc_usd'])} for x in obs],key=lambda x:(x['mc'],-x['timestamp']))
    drawdown=(peak['mc']-float(latest['mc_usd']))*100/peak['mc']; terminal=drawdown>=85
    reduced.append((fact,latest,peak,drawdown,terminal))
  now=int(time.time()); statements=[]
  for fact,latest,peak,drawdown,terminal in reduced:
   sql='UPDATE operation_monitor_facts SET latest_mc_usd=?,latest_mc_timestamp=?,current_multiple=?,running_peak_mc_usd=?,running_peak_timestamp=?,running_peak_multiple=?,drawdown_percent=?,monitor_state=?,last_observation_at=?,next_observation_at=?,monitor_completed_at=?,updated_at=? WHERE operation_id=? AND mint=? AND monitor_state=\'MONITORING_ACTIVE\''
   statements.append((sql,(float(latest['mc_usd']),int(latest['observation_timestamp']),float(latest['mc_usd'])/float(fact['entry_mc_usd']),peak['mc'],peak['timestamp'],peak['mc']/float(fact['entry_mc_usd']),drawdown,'PRICE_MONITOR_COMPLETE_COLLAPSED' if terminal else 'MONITORING_ACTIVE',int(latest['observation_timestamp']),None if terminal else fact['next_observation_at'],now if terminal else None,now,fact['operation_id'],fact['mint'])))
  if statements:
   receipt=self.persist(WriteItem('enrichment','operation-monitor-reducer',statements,_h({'reduced':[(x[0]['mint'],x[2],x[3]) for x in reduced]})))
   if not receipt or not receipt.committed: raise RuntimeError('MONITOR_REDUCER_WRITE_UNCOMMITTED')
  return len(reduced)
 def reconcile_terminal_ath_jobs(self):
  """Read-only repair inventory; queue writes happen only after terminal commits."""
  with _read_only_connection(self.db_path) as con:
   con.row_factory=sqlite3.Row
   facts=[dict(x) for x in con.execute("SELECT * FROM operation_monitor_facts WHERE operation_id='watchtower' AND cohort_class='PROSPECTIVE_MONITOR_COHORT' AND monitor_state='PRICE_MONITOR_COMPLETE_COLLAPSED' AND next_observation_at IS NULL AND final_proven_ath_mc IS NULL")]
  return sum(self.q.enqueue_terminal_ath_finalization(fact,provenance='LEGACY_PROSPECTIVE_WATCHTOWER_ATH_REPAIR')['status']=='ENQUEUED_TERMINAL_ATH' for fact in facts)
 def _process_terminal_ath(self,c):
  p=c.payload['envelope']; finalizer=self.terminal_finalizer_factory(self.db_path); fact=finalizer.freeze(p['mint'])
  if finalizer.logical_job_identity(fact)!=p.get('logical_identity'):
   raise ValueError('TERMINAL_ATH_IDENTITY_MISMATCH')
  result=finalizer.finalize(p['mint'],interval='15m',watchtower_price_fact_contract=True)
  if result['state'] not in {'FINALIZED','ALREADY_FINALIZED'}: raise RuntimeError('TERMINAL_ATH_UNCOMMITTED')
  self.q.queue.ack(c); self.last_ack_timestamp=time.time(); return result
 def process_once(self):
  if not self.q.provider_eligible(): return 0
  claimed=self.q.queue.claim(CONCURRENCY)
  for c in claimed:
   try:
    p=c.payload['envelope'];self.heartbeat=time.time()
    if p.get('work_type')=='WATCHTOWER_TERMINAL_ATH_FINALIZATION':
     self._process_terminal_ath(c); continue
    # Assignment-first monitoring deliberately has no generic live window.
    # Retained evidence is evaluated first; only an operation-specific bounded plan may consume capacity.
    if not p.get('entry_timestamp') or not p.get('entry_mc_usd') or not p.get('candle_resolution'):
     if p.get('monitor_state')=='WAITING_FOR_ENTRY_REFERENCE' and int(p.get('next_entry_evaluation_at') or 0)>int(time.time()):
      target=self.q.queue.root/'pending'/c.path.name;os.replace(c.path,target);self.q.queue._fsync_directory(c.path.parent);self.q.queue._fsync_directory(target.parent);continue
     evaluation=_entry_inventory(self.db_path,p); p['entry_evaluation_last_run']=int(time.time());p['entry_evaluation_result']=evaluation['result'];p['missing_entry_evidence']=evaluation.get('missing_evidence',[]);p['next_entry_evaluation_at']=evaluation.get('next_entry_evaluation_at');p['monitor_state']=evaluation['result'];p['entry_acquisition_request']=evaluation.get('entry_acquisition')
     if evaluation['result']=='ENTRY_EVIDENCE_ACQUISITION_DUE':
      first,manifest=self.transport.acquire_watchtower_entry(p,evaluation['entry_acquisition'])
      p.update({'entry_timestamp':first['timestamp'],'entry_mc_usd':first['mc'],'entry_exactness':'FIRST_FULL_POST_MIGRATION_SECOND_MC','entry_provenance':_h({'entry_acquisition':evaluation['entry_acquisition'],'request':manifest['request_parameters'],'entry':first}),'entry_evaluation_result':'ENTRY_REFERENCE_QUALIFIED','monitor_state':'ENTRY_REFERENCE_QUALIFIED','entry_acquisition_request_identity':_h(manifest['request_parameters'])})
     elif evaluation['result']=='ENTRY_REFERENCE_QUALIFIED':
      p.update({k:evaluation[k] for k in ('entry_timestamp','entry_mc_usd','entry_exactness','entry_provenance')})
      p.update({'entry_method':evaluation['entry_method'],'entry_evaluation_result':'ENTRY_REFERENCE_QUALIFIED','monitor_state':'ENTRY_REFERENCE_QUALIFIED'})
     elif evaluation['result']=='INSUFFICIENT_EVIDENCE':
      # Durable visible terminal state: never invisibly spin or use a generic substitute.
      self.q.queue._replace_payload(c.path,{**c.payload,'envelope':p})
      target=self.q.queue.root/'dead_letter'/c.path.name; os.replace(c.path,target); self.q.queue._fsync_directory(c.path.parent); self.q.queue._fsync_directory(target.parent)
      continue
     elif evaluation['result']=='WAITING_FOR_ENTRY_REFERENCE':
      # Scenario-D production evidence is a separate post-commit producer.
      # Missing producer input cannot hide a durable Byzantine assignment.
      p['next_entry_evaluation_at']=int(time.time())+60
      evaluation['next_entry_evaluation_at']=p['next_entry_evaluation_at']
      if p['operation_id'].lower()=='byzantine': self._persist_waiting_entry_fact(p,evaluation)
     c.payload['envelope']=p
     self.q.queue._replace_payload(c.path,c.payload)
     if not p.get('entry_timestamp') or not p.get('entry_mc_usd'):
      target=self.q.queue.root/'pending'/c.path.name
      os.replace(c.path,target); self.q.queue._fsync_directory(c.path.parent); self.q.queue._fsync_directory(target.parent)
      continue
    # Freeze redacted request identity before provider activity; retained in retry/dead-letter.
    dispatch=int(time.time()); built=build_birdeye_ohlcv_request(address=p['mint'],interval=p.get('candle_resolution',''),time_from=int(p.get('last_observation_at') or p.get('entry_timestamp') or 0),time_to=dispatch)
    p['request_manifest']={**built,'dispatch_time':dispatch}; c.payload['envelope']=p; self.q.queue._replace_payload(c.path,c.payload)
    response=self.transport(p) # provider call has no DB handle/transaction
    candles=response.get('candles',[]);assert len(json.dumps(candles))<=8192
    latest=max(candles,key=lambda x:x['timestamp']) if candles else {};mc=float(latest.get('mc',0));ts=int(latest.get('timestamp',time.time()));entry=float(p.get('entry_mc_usd') or mc)
    # A fresh readonly lookup occurs only after provider parsing.  Entry is a
    # prospective state boundary and therefore participates in the peak.
    try:
     with _read_only_connection(self.db_path) as prior:
      old=prior.execute('SELECT running_peak_mc_usd,running_peak_timestamp FROM operation_monitor_facts WHERE operation_id=? AND mint=?',(p['operation_id'],p['mint'])).fetchone()
    except sqlite3.Error: old=None
    candidates=[(entry,int(p.get('entry_timestamp') or ts)),(float(old[0]),int(old[1] or ts))] if old else [(entry,int(p.get('entry_timestamp') or ts))]
    candidates += [(float(x.get('high') or x['mc']),int(x['timestamp'])) for x in candles]
    peak,peak_ts=max(candidates,key=lambda x:(x[0],-x[1]));dd=(peak-mc)*100/peak if peak else 0;terminal=p['operation_id'].lower()=='watchtower' and dd>=85;now=int(time.time());assignment=p.get('assignment') or {'digest':p.get('assignment_digest')}
    f={'operation_id':p['operation_id'],'mint':p['mint'],'cohort_class':p['cohort'],'assignment_timestamp':assignment.get('assigned_at',now),'assignment_provenance':_h(assignment),'entry_method':p['entry_method'],'entry_timestamp':p.get('entry_timestamp',ts),'entry_mc_usd':entry,'entry_status':'QUALIFIED','entry_exactness':'ADAPTER_FROZEN','latest_mc_usd':mc,'latest_mc_timestamp':ts,'current_multiple':mc/entry if entry else None,'running_peak_mc_usd':peak,'running_peak_timestamp':peak_ts,'running_peak_multiple':peak/entry if entry else None,'drawdown_percent':dd,'reached_2x':int(peak>=entry*2),'reached_5x':int(peak>=entry*5),'reached_10x':int(peak>=entry*10),'monitor_state':'PRICE_MONITOR_COMPLETE_COLLAPSED' if terminal else 'MONITORING_ACTIVE','monitor_started_at':now,'last_observation_at':ts,'next_observation_at':None if terminal else ts+60,'monitor_completed_at':now if terminal else None,'provider_call_count':1,'candles_retained':len(candles),'candle_resolution':response.get('resolution'),'evidence_status':'QUALIFIED','provenance_digest':_h({'request':response.get('request'),'response':response})}
    cols=list(f)+['created_at','updated_at'];vals=tuple(f[x] for x in f)+(now,now);updates=','.join(f'{x}=excluded.{x}' for x in cols if x not in {'operation_id','mint','created_at','assignment_timestamp','assignment_provenance','entry_method','entry_timestamp','entry_mc_usd','entry_status','entry_exactness'})
    sql=f"INSERT INTO operation_monitor_facts({','.join(cols)}) VALUES({','.join('?' for _ in cols)}) ON CONFLICT(operation_id,mint) DO UPDATE SET {updates},running_peak_mc_usd=MAX(operation_monitor_facts.running_peak_mc_usd,excluded.running_peak_mc_usd),provider_call_count=operation_monitor_facts.provider_call_count+1"
    request_id=_h(response.get('request_manifest') or p.get('request_manifest') or response.get('request'))
    obs_sql='INSERT OR IGNORE INTO operation_monitor_observations(operation_id,mint,observation_timestamp,mc_usd,resolution,source,request_identity,provenance_digest,created_at,open_mc_usd,high_mc_usd,low_mc_usd,close_mc_usd) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)'
    observations=[(obs_sql,(p['operation_id'],p['mint'],int(x['timestamp']),float(x['mc']),response.get('resolution'),'BIRDEYE_OHLCV',request_id,_h(x),now,float(x.get('open') or x['mc']),float(x.get('high') or x['mc']),float(x.get('low') or x['mc']),float(x['mc']))) for x in candles]
    self.last_receipt=self.persist(WriteItem('enrichment','operation-monitor',observations+[(sql,vals)],_h({'job':c.message_id,'fact':f})))
    if not self.last_receipt or not self.last_receipt.committed:raise ConnectionError(getattr(self.last_receipt,'error','WRITER_UNAVAILABLE'))
    if terminal:
     self.q.enqueue_terminal_ath_finalization(f,provenance='POST_COMMIT_TERMINAL_COLLAPSE')
    if self.before_ack:self.before_ack()
    self.q.queue.ack(c);self.last_ack_timestamp=time.time();assert self.last_receipt.commit_timestamp<self.last_ack_timestamp
   except Exception as e:
    # Preserve provider capacity/backoff as a visible queue state; retry ownership remains EvidenceIntakeQueue.
    if isinstance(e,ProviderCapacityBackoff):
     self.q.defer_rate_limited(c,retry_after=e.retry_after,error='HTTP_429')
     continue
    if 'HTTP_429' in str(e) or 'Too many requests' in str(e):
     self.q.defer_rate_limited(c,error='HTTP_429')
     continue
    self.q.queue.nack(c,str(e))
  return len(claimed)
