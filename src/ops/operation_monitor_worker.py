"""Durable Monitor queue -> provider -> shared writer -> ACK."""
from __future__ import annotations
import hashlib,json,os,sqlite3,time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any,Callable
from src.core.db_write_queue import WriteItem
from src.core.db_writer import DurableWriteReceipt,commit_write_and_wait
from src.evidence.queue import ClaimedMessage, EvidenceIntakeQueue
from src.ops.operation_price_research_orchestrator import adapter_for
from src.ops.token_data_provider_bindings import BirdeyeProductionBinding,build_birdeye_ohlcv_request
from src.ops.birdeye_rate_limit import wait_seconds
from src.ops.provider_rate_limit_gate import ProviderRateLimited,ProviderRateLimitMetadata
from src.ops.dev_provider_budget import DevProviderBudget,BudgetDenied
from src.ops.byzantine_monitor_entry import derive_monitor_entry
from src.ops.operation_monitor_capabilities import monitor_capability_for_operation
from src.ops.watchtower_terminal_ath_finalizer import ProviderCapacityBackoff, WatchtowerTerminalAthFinalizer
CONTRACT='operation-monitor.v1'; CONCURRENCY=1; MAX_BYTES=10_000_000
_TERMINAL_MONITOR_STATES={'PRICE_MONITOR_COMPLETE_COLLAPSED'}
_EMPTY_OHLCV_SAFETY_SECONDS=5
_OHLCV_BUCKET_SECONDS=15*60
class NoUsableOhlcvEvidence(ValueError):
 def __init__(self, *, now:int, classification:str='NO_USABLE_CANDLE'):
  self.classification=classification;self.next_eligible_at=((int(now)//_OHLCV_BUCKET_SECONDS)+1)*_OHLCV_BUCKET_SECONDS+_EMPTY_OHLCV_SAFETY_SECONDS
  super().__init__(classification)
class TerminalProviderFailure(RuntimeError):
 """A classified non-retryable provider outcome; never re-enters polling."""
 def __init__(self, classification='TERMINAL_PROVIDER_FAILURE'):
  self.classification=str(classification); super().__init__(self.classification)
class ResponseCapacityExceeded(RuntimeError):
 """Compact response guard failure; retryable under the generic policy."""
 pass
class StrictEntryNormalizationError(ValueError):
 def __init__(self, category, diagnostic):
  self.category=category;self.diagnostic=diagnostic
  super().__init__(category)
def _h(x:Any)->str:return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':'),default=str).encode()).hexdigest()

def _qualified_live_entry(envelope: dict[str, Any]) -> bool:
 """Whether an envelope has a qualified entry suitable for live monitoring.

 USD-qualified entries retain the established generic contract: an entry
 timestamp and USD reference are sufficient.  Native-qualified entries are
 equally actionable, but intentionally retain a NULL USD entry until an
 independent price fact exists.  This predicate is semantic, not tied to an
 operation, mint, or opening algorithm.
 """
 state=str(envelope.get('entry_reference_state') or '').upper()
 # A durable insufficient/terminal classification always wins over a stale
 # numeric field on an old queue envelope.  The terminal database check below
 # remains the authority for normal lifecycle processing; this is the local
 # queue-level fail-closed guard.
 if state in {'INSUFFICIENT_EVIDENCE','TERMINAL','PRICE_MONITOR_COMPLETE_COLLAPSED'}:
  return False
 if not envelope.get('entry_timestamp'):
  return False
 if envelope.get('entry_mc_usd') is not None:
  return True
 return (
  state == 'NATIVE_QUALIFIED'
  and envelope.get('entry_native_mc_sol') is not None
 )


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


_OPERATION_IDS={'04265d9f-6eb2-568c-a49e-9253091a4dbb':'watchtower','bb255638-a493-551f-938c-8be7c9ea4f1e':'watchtower_deep','d8ee4d7a-fcd6-5a5b-b897-24f6ab56e334':'byzantine'}
_WATCHTOWER_OPERATOR_ID='04265d9f-6eb2-568c-a49e-9253091a4dbb'
_WATCHTOWER_DEEP_OPERATOR_ID='bb255638-a493-551f-938c-8be7c9ea4f1e'
_BYZANTINE_OPERATOR_ID='d8ee4d7a-fcd6-5a5b-b897-24f6ab56e334'
def canonical_operation_id(value: str) -> str:
 return _OPERATION_IDS.get(str(value).lower(),str(value).lower())
@dataclass
class MonitorQueue:
 root:Path; enabled:bool=False; newest_assignment_first:bool=False; fair_scheduling:bool=False; opening_jobs_path:Path|None=None; provider_work_path:Path|None=None; soak_selection_path:Path|None=None
 def __post_init__(self):self.queue=EvidenceIntakeQueue(self.root,enabled=self.enabled,max_messages=500,max_bytes=MAX_BYTES,max_attempts=3)
 def _soak_allowlist(self):
  """Return the explicit DEV diagnostic allowlist, or ``None`` outside soak mode.

  The file is deliberately opt-in.  Normal queue operation is byte-for-byte
  unchanged unless a caller binds a durable selection document.
  """
  path=self.soak_selection_path or (Path(os.environ['DEV_005_SOAK_SELECTION_PATH']) if os.getenv('DEV_005_SOAK_SELECTION_PATH') else None)
  if path is None: return None
  try:
   payload=json.loads(path.read_text(encoding='utf-8'))
   if payload.get('mode')!='DEV_005_ISOLATED_SOAK': return None
   return frozenset(str(x) for x in payload.get('allowlist',[]) if isinstance(x,str))
  except FileNotFoundError:
   # Removing the opt-in binding returns the ordinary shared queue unchanged.
   return None
  except (OSError,ValueError,TypeError):
   # A present-but-invalid diagnostic selection fails closed.
   return frozenset()
 def soak_allows(self,envelope):
  allowlist=self._soak_allowlist()
  return allowlist is None or str((envelope or {}).get('mint') or '') in allowlist
 def _scheduler_cursor_path(self): return self.queue.root/'scheduler_cursor.json'
 def _scheduler_cursor(self):
  """Return the last durable fair-scheduler selection, if any.

  The cursor is compact queue state, not process memory. It is used only by
  the explicit fair-scheduling mode, leaving ordinary queue ordering intact.
  """
  try:
   value=json.loads(self._scheduler_cursor_path().read_text(encoding='utf-8')).get('last_message_id')
   return str(value) if value else None
  except (OSError,ValueError,TypeError): return None
 def _set_scheduler_cursor(self,message_id):
  path=self._scheduler_cursor_path(); path.parent.mkdir(parents=True,exist_ok=True)
  temporary=path.parent/(f'.{path.name}.{os.getpid()}.tmp')
  temporary.write_text(json.dumps({'last_message_id':str(message_id)},sort_keys=True,separators=(',',':'))+'\n')
  os.replace(temporary,path); self.queue._fsync_directory(path.parent)
 def claim(self, limit: int):
  """Claim normally in queue order, or by newest real assignment in DEV.

  The production queue deliberately retains its opaque, stable message-id
  ordering.  The isolated DEV mirror initially imports historical membership;
  prioritising a recent real assignment there prevents that backfill from
  hiding the current launch under unrelated old review work.
  """
  self.queue.initialize()
  ranked=[]
  for path in (self.queue.root/'pending').glob('*.json'):
   try:
    envelope=(json.loads(path.read_text()).get('envelope') or {})
    assigned_at=int((envelope.get('assignment') or {}).get('assigned_at') or 0)
    ranked.append(((-assigned_at if self.newest_assignment_first else 0),path))
   except (OSError,ValueError,TypeError):
    ranked.append((0,path))
  ordered=sorted(ranked,key=lambda item:(item[0],str(item[1])))
  if self.fair_scheduling and ordered:
   # Rotate eligible current work after the last durable selection. A new
   # successor that hashes before the cursor cannot jump ahead of another
   # currently due token, and restart retains the same rotation.
   cursor=self._scheduler_cursor()
   if cursor:
    pivot=next((index for index,(_,path) in enumerate(ordered) if path.stem>cursor),None)
    ordered=ordered[pivot:]+ordered[:pivot] if pivot is not None else ordered
  claimed=[]
  for _,source in ordered:
   if len(claimed)>=max(0,limit): break
   try:
    candidate=(json.loads(source.read_text()).get('envelope') or {})
    if not self.soak_allows(candidate): continue
    if int(candidate.get('next_eligible_dispatch_at') or 0) > int(time.time()): continue
   except (OSError,ValueError,TypeError): continue
   target=self.queue.root/'processing'/source.name
   try:
    os.replace(source,target);self.queue._fsync_directory(source.parent);self.queue._fsync_directory(target.parent)
   except FileNotFoundError: continue
   try:
    payload=json.loads(target.read_text(encoding='utf-8')); message=ClaimedMessage(str(payload['message_id']),payload,target);claimed.append(message)
    if self.fair_scheduling: self._set_scheduler_cursor(message.message_id)
   except (OSError,ValueError,KeyError,TypeError):
    os.replace(target,self.queue.root/'dead_letter'/target.name);raise
  return claimed
 def enqueue_qualified_activation(self, *, qualified_entry):
  """Canonical queue identity after a durable qualified entry transition."""
  p=dict(qualified_entry); operation_id=canonical_operation_id(str(p['operation_id']))
  payload={**p,'operation_id':operation_id,'cohort':p.get('cohort','PROSPECTIVE_MONITOR_COHORT'),
           'monitor_state':'ENTRY_REFERENCE_QUALIFIED','candle_resolution':p.get('candle_resolution','15m'),
           'contract':CONTRACT}
  ident=_h({'qualified_entry_activation':operation_id,'mint':p['mint'],'entry_timestamp':p['entry_timestamp'],
            'entry_provenance':p.get('entry_provenance'),'contract':CONTRACT})
  return self.queue.enqueue(payload,message_id=ident)
 def enqueue_active_successor(self, *, predecessor_id: str, fact: dict[str,Any], envelope: dict[str,Any], now: int | None = None):
  """Persist one future live opportunity after an authoritative observation."""
  if fact.get('monitor_state') != 'MONITORING_ACTIVE' or not fact.get('next_observation_at'):
   return {'status':'NOT_ENQUEUED_INELIGIBLE'}
  native=fact.get('entry_native_mc_sol'); usd=fact.get('entry_mc_usd')
  state='ENTRY_REFERENCE_QUALIFIED' if usd is not None else 'NATIVE_QUALIFIED'
  next_eligible=max(int(fact['next_observation_at']),int(time.time() if now is None else now)+15)
  successor={**envelope,'operation_id':fact['operation_id'],'mint':fact['mint'],
             'entry_timestamp':int(fact['entry_timestamp']),'entry_mc_usd':float(usd) if usd is not None else None,
             'entry_native_mc_sol':str(native) if native is not None else None,
             'entry_method':fact.get('entry_method') or envelope.get('entry_method'),
             'entry_exactness':fact.get('entry_exactness'),'entry_provenance':fact.get('provenance_digest'),
             'entry_reference_state':state,'monitor_state':'ENTRY_REFERENCE_QUALIFIED',
             'next_eligible_dispatch_at':next_eligible,'successor_of':predecessor_id,
             'candle_resolution':envelope.get('candle_resolution') or '15m'}
  if not _qualified_live_entry(successor): return {'status':'NOT_ENQUEUED_INVALID_FACT'}
  ident=_h({'active_monitor_successor':fact['operation_id'],'mint':fact['mint'],
            'predecessor':predecessor_id,'observation_timestamp':fact.get('last_observation_at'),
            'next_eligible_dispatch_at':next_eligible,'contract':CONTRACT})
  return {'status':'ENQUEUED_ACTIVE_SUCCESSOR','job_id':self.queue.enqueue(successor,message_id=ident)}
 def has_durable_message(self, message_id: str) -> bool:
  """Whether an idempotent queue identity survives in any durable state."""
  return any((self.queue.root / state / f'{message_id}.json').exists() for state in self.queue.STATES)
 def current_fact_identities(self, *, operation_id: str, mint: str):
  """Return schedulable identities for one authoritative Monitor fact.

  A pending, claimed, or provider-deferred envelope is still current work.
  Dead letters are retained history, never an excuse to silently reactivate a
  fact, and therefore do not satisfy the active-work invariant.
  """
  matches=[]; operation_id=canonical_operation_id(operation_id)
  for state in ('pending','processing','retry'):
   for path in (self.queue.root/state).glob('*.json'):
    try:
     payload=json.loads(path.read_text(encoding='utf-8')); envelope=payload.get('envelope') or {}
    except (OSError,ValueError,TypeError): continue
    if canonical_operation_id(str(envelope.get('operation_id') or ''))==operation_id and envelope.get('mint')==mint:
     matches.append((state,path,payload,envelope))
  return matches
 def enqueue_active_fact_reconstruction(self, *, fact: dict[str,Any], now: int | None = None):
  """Restore one missing current projection from an active fact, without transport."""
  usd,native=fact.get('entry_mc_usd'),fact.get('entry_native_mc_sol')
  timestamp=int(time.time() if now is None else now)
  state='ENTRY_REFERENCE_QUALIFIED' if usd is not None else 'NATIVE_QUALIFIED'
  due=max(int(fact.get('next_observation_at') or 0),timestamp+15)
  envelope={'operation_id':canonical_operation_id(fact['operation_id']),'mint':fact['mint'],
            'cohort':fact.get('cohort_class') or 'PROSPECTIVE_MONITOR_COHORT',
            'entry_method':fact.get('entry_method'),'entry_timestamp':int(fact['entry_timestamp']),
            'entry_mc_usd':float(usd) if usd is not None else None,
            'entry_native_mc_sol':str(native) if native is not None else None,
            'entry_exactness':fact.get('entry_exactness'),'entry_provenance':fact.get('provenance_digest'),
            'entry_reference_state':state,'monitor_state':'ENTRY_REFERENCE_QUALIFIED',
            'candle_resolution':'15m','contract':CONTRACT,'next_eligible_dispatch_at':due,
            'reconstructed_from_active_fact':True}
  if not _qualified_live_entry(envelope): return {'status':'NOT_ENQUEUED_INVALID_FACT'}
  ident=_h({'active_monitor_fact_reconstruction':envelope['operation_id'],'mint':envelope['mint'],
            'entry_timestamp':envelope['entry_timestamp'],'entry_provenance':envelope['entry_provenance'],
            'contract':CONTRACT})
  return {'status':'ENQUEUED_ACTIVE_FACT_RECONSTRUCTION','job_id':self.queue.enqueue(envelope,message_id=ident),'next_eligible_at':due}
 def enqueue_after_assignment(self,*,mint,operation_id,assignment,canonical_birth):
  operation_id=canonical_operation_id(operation_id)
  a=adapter_for(operation_id); ident=_h({'operation_id':operation_id,'mint':mint,'assignment':_h(assignment),'contract':CONTRACT})
  if a['method']=='ENTRY_METHOD_UNQUALIFIED':return {'status':'NOT_ENQUEUED_ENTRY_UNQUALIFIED'}
  # Identity is assignment-only; entry evidence is downstream mutable state.
  payload={'mint':mint,'operation_id':operation_id,'assignment':assignment,'assignment_digest':_h(assignment),'birth':canonical_birth,'scenario_d_evidence':canonical_birth.get('scenario_d_evidence'),'entry_method':a['method'],'entry_timestamp':canonical_birth.get('entry_timestamp'),'entry_mc_usd':canonical_birth.get('entry_mc_usd'),'candle_resolution':a['candle_resolution'],'monitor_state':'WAITING_FOR_ENTRY_REFERENCE','cohort':'PROSPECTIVE_MONITOR_COHORT','contract':CONTRACT}
  # Queue admission commits first.  The optional causal-evidence job is a
  # separate, idempotent post-commit effect: no provider request or membership
  # write occurs here.
  monitor_job_id=self.queue.enqueue(payload,message_id=ident)
  # DEV historical lifecycle evidence is a separate post-commit consumer. It
  # uses only explicit migration anchors and never affects membership, the
  # Monitor queue identity, or this admission's provider-free contract.
  historical_job=None
  if os.getenv('MONITOR_RUNTIME') == 'dev' and os.getenv('DEV_HISTORICAL_LIFECYCLE_MODE','0').lower() in {'1','true','yes','on'}:
   migration_timestamp=canonical_birth.get('migration_timestamp') or canonical_birth.get('migrated_at')
   migration_identity=canonical_birth.get('migration_tx') or canonical_birth.get('migration_signature')
   if operation_id in {'watchtower','watchtower_deep'} and migration_timestamp and migration_identity:
    try:
     from src.ops.historical_lifecycle_jobs import HistoricalLifecycleJobs
     historical_job=HistoricalLifecycleJobs(self.db_path if hasattr(self,'db_path') else os.getenv('WT_OPS_DB_PATH','')).admit_after_commit(
      mint=mint,operation_id=operation_id,anchor_type='MIGRATION',anchor_identity=str(migration_identity),anchor_timestamp=int(migration_timestamp))
    except Exception as exc:
     historical_job={'status':'HISTORY_JOB_CREATION_FAILED_POST_COMMIT','error_class':type(exc).__name__}
  capability=monitor_capability_for_operation(operation_id) or {}
  policy=capability.get('entry_reference_policy')
  opening=None
  if policy and self.opening_jobs_path is not None:
   from src.ops import live_opening_action_job
   creator=str(canonical_birth.get('creator') or '')
   signature=str(canonical_birth.get('create_signature') or canonical_birth.get('signature') or '')
   if creator and signature:
    try:
     opening=live_opening_action_job.admit(
      self.opening_jobs_path, operation_id=operation_id, mint=mint, creator=creator,
      create_signature=signature, entry_reference_policy=str(policy),
      acquisition_scope=canonical_birth.get('opening_acquisition_scope'),
      create_reuse=bool(canonical_birth.get('create_reuse', False)),
      retained_create_fact=canonical_birth.get('retained_create_fact'))
     if self.provider_work_path is not None:
      from src.ops.generic_provider_work_scheduler import sync_opening_action_job
      opening['provider_work'] = sync_opening_action_job(self.provider_work_path,self.opening_jobs_path,opening['job_id'])
    except Exception as exc:
     # This post-commit enrichment cannot undo canonical assignment or the
     # durable Monitor message.  A later idempotent reconciliation/admission
     # reuses their logical identities to repair the missing opening job.
     opening={'status':'OPENING_JOB_CREATION_FAILED_POST_COMMIT','error_class':type(exc).__name__}
   else:
    opening={'status':'NOT_ENQUEUED_INCOMPLETE_CANONICAL_BIRTH'}
  return {'status':'ENQUEUED','job_id':monitor_job_id,'opening_action_job':opening,'historical_lifecycle_job':historical_job}
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
  state=self.provider_backoff() or {}; return int(state.get('next_eligible_at',state.get('backoff_until',0)) or 0)<=int(time.time() if now is None else now)
 def admit_provider_dispatch(self,mint,request_class,*,now=None,global_limit=20,token_limit=4,force_dev=False):
  """DEV-only, one-debit admission immediately before a real dispatch."""
  if os.getenv('MONITOR_RUNTIME')!='dev' and not force_dev: return True
  if not self.provider_eligible(now=now): raise BudgetDenied('PROVIDER_GATE_CLOSED')
  DevProviderBudget(self.queue.root,now=time.time).admit(mint,request_class,now=now,global_limit=global_limit,token_limit=token_limit)
  return True
 def provider_gate_state(self, *, now=None):
  timestamp=int(time.time() if now is None else now); state=self.provider_backoff() or {}
  eligible=int(state.get('next_eligible_at',state.get('backoff_until',0)) or 0)
  return {**state,'provider':'BIRDEYE','next_eligible_at':eligible,'remaining_seconds':max(0,eligible-timestamp),
          'consecutive_429_count':int(state.get('consecutive_429_count') or 0),
          'fallback_level':int(state.get('fallback_level') or 0),'deadline_source':state.get('deadline_source','FALLBACK')}
 def _record_provider_backoff(self, *, eligible, error, state=None, now=None):
  timestamp=int(time.time() if now is None else now)
  payload={'provider':'BIRDEYE','provider_backoff_reason':error,'backoff_until':int(eligible),'next_eligible_at':int(eligible),'updated_at':timestamp}
  if state: payload.update(state)
  path=self._backoff_path(); path.parent.mkdir(parents=True,exist_ok=True)
  temporary=path.parent/(f'.{path.name}.{os.getpid()}.tmp')
  temporary.write_text(json.dumps(payload,sort_keys=True,separators=(',',':'))+'\n'); os.replace(temporary,path); self.queue._fsync_directory(path.parent)
 def apply_provider_rate_limit(self, *, metadata:ProviderRateLimitMetadata|None=None, now=None, error='HTTP_429'):
  """Advance the one compact shared Birdeye gate and return its deadline."""
  timestamp=int(time.time() if now is None else now)
  prior=self.provider_backoff() or {}
  number=int(prior.get('consecutive_429_count') or 0)+1
  fallback=wait_seconds(None,number); provider_deadline,provider_source=(metadata.provider_deadline() if metadata else (None,None))
  eligible=max(timestamp+fallback,int(provider_deadline or 0)); source=provider_source if provider_deadline and int(provider_deadline)>=timestamp+fallback else 'FALLBACK'
  compact=metadata.compact() if metadata else {'provider':'BIRDEYE','endpoint_class':'UNKNOWN','http_status':429,'request_timestamp':timestamp,'retry_after_seconds':None,'retry_after_http_date':None,'provider_reset_at':None}
  self._record_provider_backoff(eligible=eligible,error=error,now=timestamp,state={
   'consecutive_429_count':number,'fallback_level':fallback,'last_429_at':timestamp,'last_success_at':prior.get('last_success_at'),
   'deadline_source':source,'last_rate_limit':compact})
  return {'eligible':eligible,'source':source,'compact':compact,'count':number,'fallback':fallback}
 def defer_rate_limited(self,claimed,*,metadata:ProviderRateLimitMetadata|None=None,retry_after=None,error='HTTP_429',now=None):
  """Persist shared Birdeye capacity backoff without consuming queue retry budget."""
  if metadata is None and retry_after is not None:
   metadata=ProviderRateLimitMetadata.from_headers(provider='BIRDEYE',endpoint_class='TERMINAL_ATH',status=429,request_timestamp=int(time.time() if now is None else now),headers={'Retry-After':str(retry_after)})
  state=self.apply_provider_rate_limit(metadata=metadata,now=now,error=error); eligible=state['eligible'];compact=state['compact'];number=state['count'];source=state['source']
  payload=dict(claimed.payload); envelope=dict(payload.get('envelope') or {})
  envelope.update({'monitor_state':'PROVIDER_BACKOFF','provider_backoff_reason':error,
                   'provider_capacity_backoff_count':number,'backoff_until':eligible,'next_eligible_dispatch_at':eligible,
                   'last_provider_status':429,'provider_rate_limit':compact,'provider_deadline_source':source})
  payload['envelope']=envelope;payload['last_error']=error;payload['last_attempt_at']=int(time.time() if now is None else now)
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
    empty=e.get('monitor_state')=='NO_USABLE_CANDLE' or str(payload.get('last_error','')).startswith('NO_USABLE_CANDLE')
    generic=e.get('monitor_state')=='RETRYABLE_DEFERRED'
    legacy_blank=int(payload.get('attempts') or 0)>0 and not str(payload.get('last_error') or '') and not empty and e.get('provider_backoff_reason')!='HTTP_429'
    if legacy_blank:
     deadline=int(payload.get('last_attempt_at') or timestamp)+wait_seconds(None,1)
     e.update({'monitor_state':'RETRYABLE_DEFERRED','provider_outcome':'RETRYABLE_DEFERRED','provider_outcome_reason':'LEGACY_BLANK_RETRY_NORMALIZED','recovery_deadline_at':deadline,'next_eligible_dispatch_at':deadline});payload['envelope']=e;self.queue._replace_payload(path,payload);generic=True
    if not empty and not generic and e.get('provider_backoff_reason')!='HTTP_429' and 'HTTP_429' not in str(payload.get('last_error','')): continue
    gate=self.provider_gate_state(now=timestamp)
    eligible=int(e.get('next_eligible_dispatch_at') or e.get('recovery_deadline_at') or (int(payload.get('last_attempt_at') or timestamp)+wait_seconds(None,1)))
    if not empty: eligible=max(eligible,int(gate['next_eligible_at']))
    if eligible>timestamp: continue
    e.update({'monitor_state':'ENTRY_REFERENCE_QUALIFIED' if e.get('entry_timestamp') else 'WAITING_FOR_ENTRY_REFERENCE','next_eligible_dispatch_at':None,'recovery_deadline_at':None});payload['envelope']=e
    self.queue._replace_payload(path,payload);target=self.queue.root/'pending'/path.name;os.replace(path,target);self.queue._fsync_directory(path.parent);self.queue._fsync_directory(target.parent);moved+=1
   except (OSError,ValueError,TypeError): continue
  return moved
 def defer_empty_ohlcv(self,claimed,*,next_eligible_at:int,classification='NO_USABLE_CANDLE'):
  """Coalesce one evidence-unavailable retry at the next completed 15m boundary.

  This is deliberately independent from HTTP-429 pressure: an accepted HTTP
  request without a usable completed candle says nothing about provider quota.
  """
  payload=dict(claimed.payload);envelope=dict(payload.get('envelope') or {})
  count=min(3,int(envelope.get('empty_ohlcv_retry_count') or 0)+1)
  envelope.update({'monitor_state':'NO_USABLE_CANDLE','empty_ohlcv_retry_count':count,
                   'next_eligible_dispatch_at':int(next_eligible_at),'last_provider_status':200,
                   'empty_ohlcv_classification':classification})
  payload['envelope']=envelope;payload['last_error']=classification;payload['last_attempt_at']=int(time.time())
  self.queue._replace_payload(claimed.path,payload)
  target=self.queue.root/'retry'/claimed.path.name;os.replace(claimed.path,target)
  self.queue._fsync_directory(claimed.path.parent);self.queue._fsync_directory(target.parent)
 def defer_retryable(self,claimed,*,classification='RETRYABLE_PROVIDER_FAILURE',now=None,ready_now=False):
  """Persist a generic retryable outcome with an explicit future deadline."""
  timestamp=int(time.time() if now is None else now);deadline=timestamp if ready_now else timestamp+wait_seconds(None,1)
  payload=dict(claimed.payload);envelope=dict(payload.get('envelope') or {})
  envelope.update({'monitor_state':'RETRYABLE_DEFERRED','provider_outcome':'RETRYABLE_DEFERRED','provider_outcome_reason':str(classification)[:160],
                   'recovery_deadline_at':deadline,'next_eligible_dispatch_at':deadline})
  payload['envelope']=envelope;payload['last_error']=str(classification)[:500];payload['last_attempt_at']=timestamp
  self.queue._replace_payload(claimed.path,payload)
  target=self.queue.root/'retry'/claimed.path.name;os.replace(claimed.path,target)
  self.queue._fsync_directory(claimed.path.parent);self.queue._fsync_directory(target.parent)
 def defer_terminal_failure(self,claimed,*,classification='TERMINAL_PROVIDER_FAILURE'):
  """Persist an explicit terminal provider outcome without a retry route."""
  payload=dict(claimed.payload);envelope=dict(payload.get('envelope') or {})
  envelope.update({'monitor_state':'TERMINAL_PROVIDER_FAILURE','provider_outcome':'TERMINAL_FAILURE','provider_outcome_reason':str(classification)[:160],
                   'recovery_deadline_at':None,'next_eligible_dispatch_at':None})
  payload['envelope']=envelope;payload['last_error']=str(classification)[:500];payload['last_attempt_at']=int(time.time())
  self.queue._replace_payload(claimed.path,payload)
  target=self.queue.root/'dead_letter'/claimed.path.name;os.replace(claimed.path,target)
  self.queue._fsync_directory(claimed.path.parent);self.queue._fsync_directory(target.parent)
 def record_provider_success(self, *, now=None):
  """Conservative recovery: each success reduces shared 429 pressure by one."""
  timestamp=int(time.time() if now is None else now); prior=self.provider_backoff() or {}
  remaining=max(0,int(prior.get('consecutive_429_count') or 0)-1)
  self._record_provider_backoff(eligible=min(int(prior.get('next_eligible_at',prior.get('backoff_until',timestamp)) or timestamp),timestamp),error='READY',now=timestamp,state={
   'consecutive_429_count':remaining,'fallback_level':wait_seconds(None,remaining) if remaining else 0,
   'last_429_at':prior.get('last_429_at'),'last_success_at':timestamp,'deadline_source':prior.get('deadline_source','FALLBACK'),
   'last_rate_limit':prior.get('last_rate_limit')})
def production_queue():
 infrastructure=os.getenv('MONITOR_INFRASTRUCTURE_ENABLED','false').lower()=='true'; live=os.getenv('OPERATIONS_MODE','OFF').upper()=='MONITOR'
 opening=os.getenv('LIVE_OPENING_ACTION_JOBS_PATH')
 work=os.getenv('GENERIC_PROVIDER_WORK_PATH')
 return MonitorQueue(Path(os.getenv('OPERATION_MONITOR_QUEUE_PATH','database/evidence_platform/operation_monitor_jobs')),enabled=live or infrastructure,
                     fair_scheduling=os.getenv('OPERATION_MONITOR_FAIR_SCHEDULER','false').lower()=='true',
                     opening_jobs_path=Path(opening) if opening else None,provider_work_path=Path(work) if work else None)
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
  assignments=[dict(r) for r in con.execute("SELECT m.mint,m.assigned_at,m.event_id FROM operator_launch_membership m WHERE m.operator_id=? AND m.assigned_at>=? ORDER BY m.assigned_at",(_BYZANTINE_OPERATOR_ID,int(activation)))]
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

def reconcile_qualified_monitor_fact_queue_projection(db_path: str, q: MonitorQueue | None = None, *, now: int | None = None) -> dict[str, int]:
 """Make active qualified facts have exactly one current durable projection.

 The Monitor fact is the authority once a qualified entry has committed.  The
 queue is only its derived work projection, so this function never writes a
 fact or reacquires evidence.  A missing current projection is reconstructed
 idempotently from the fact; duplicate current projections fail closed. It is intentionally
 operation-agnostic: a qualified USD value and a qualified native value use
 the same envelope contract, with the value form determining the generic
 ``entry_reference_state``.
 """
 q = q or production_queue()
 result = {'examined': 0, 'reconciled': 0, 'reconstructed': 0, 'already_current': 0, 'refused': 0, 'missing_identity': 0, 'duplicate_identity': 0}
 if not q.enabled:
  return result
 with _read_only_connection(db_path) as con:
  con.row_factory = sqlite3.Row
  facts = [dict(row) for row in con.execute(
   "SELECT operation_id,mint,cohort_class,assignment_timestamp,assignment_provenance,entry_method,entry_timestamp,entry_mc_usd,entry_native_mc_sol,entry_status,entry_exactness,monitor_state,next_observation_at,evidence_status,provenance_digest "
   "FROM operation_monitor_facts WHERE entry_status='QUALIFIED'"
  )]
 for fact in facts:
  result['examined'] += 1
  if fact.get('monitor_state') in _TERMINAL_MONITOR_STATES or fact.get('monitor_state') != 'MONITORING_ACTIVE':
   result['refused'] += 1
   continue
  usd, native = fact.get('entry_mc_usd'), fact.get('entry_native_mc_sol')
  if not fact.get('entry_timestamp') or (usd is None and native is None):
   result['refused'] += 1
   continue
  entry_state = 'ENTRY_REFERENCE_QUALIFIED' if usd is not None else 'NATIVE_QUALIFIED'
  matches = q.current_fact_identities(operation_id=fact['operation_id'], mint=fact['mint'])
  if not matches:
   result['missing_identity'] += 1
   created=q.enqueue_active_fact_reconstruction(fact=fact,now=now)
   if created.get('status')!='ENQUEUED_ACTIVE_FACT_RECONSTRUCTION':
    result['refused'] += 1
   else: result['reconstructed'] += 1
   continue
  if len(matches) != 1:
   result['duplicate_identity'] += 1
   continue
  _state, path, payload, envelope = matches[0]
  projected = {
   **envelope,
   'operation_id': fact['operation_id'],
   'mint': fact['mint'],
   'cohort': fact.get('cohort_class') or envelope.get('cohort') or 'PROSPECTIVE_MONITOR_COHORT',
   'entry_method': fact.get('entry_method'),
   'entry_timestamp': int(fact['entry_timestamp']),
   'entry_mc_usd': float(usd) if usd is not None else None,
   'entry_native_mc_sol': str(native) if native is not None else None,
   'entry_exactness': fact.get('entry_exactness'),
   'entry_provenance': fact.get('provenance_digest'),
   'entry_reference_state': entry_state,
   'monitor_state': 'ENTRY_REFERENCE_QUALIFIED',
   'candle_resolution': envelope.get('candle_resolution') or '15m',
  }
  if not _qualified_live_entry(projected):
   result['refused'] += 1
   continue
  if projected == envelope:
   result['already_current'] += 1
   continue
  payload['envelope'] = projected
  q.queue._replace_payload(path, payload)
  result['reconciled'] += 1
 return result

def _reconcile_migration_price_assignment_admissions(db_path:str,q:MonitorQueue|None,*,operator_id:str,operation_id:str)->dict[str,int]:
 """Idempotently admit post-activation Watchtower memberships missing Monitor state.

 The authoritative membership table is the only input.  Existing facts and every
 durable queue state are treated as already admitted, so a normal five-second
 reconciliation cannot duplicate provider work or overwrite historical facts.
 Missing entry evidence remains fail-closed in the existing Watchtower adapter.
 """
 q=q or production_queue(); result={'examined':0,'enqueued':0,'already_present':0}
 if not q.enabled:return result
 with _read_only_connection(db_path) as con:
  con.row_factory=sqlite3.Row
  activation=con.execute("SELECT MAX(effective_at) FROM operation_monitor_mode_transitions WHERE mode='MONITOR'").fetchone()[0]
  if activation is None:return result
  assignments=[dict(r) for r in con.execute("SELECT m.mint,m.assigned_at,m.event_id FROM operator_launch_membership m WHERE m.operator_id=? AND m.assigned_at>=? ORDER BY m.assigned_at",(operator_id,int(activation)))]
  fact_mints={r[0] for r in con.execute("SELECT mint FROM operation_monitor_facts WHERE operation_id=?",(operation_id,))}
 queued_mints=set()
 for state in q.queue.STATES:
  for path in (q.queue.root/state).glob('*.json'):
   try:
    envelope=(json.loads(path.read_text()).get('envelope') or {})
    if canonical_operation_id(envelope.get('operation_id',''))==operation_id: queued_mints.add(envelope.get('mint'))
   except (OSError,ValueError,TypeError):continue
 for assignment in assignments:
  result['examined']+=1
  if assignment['mint'] in fact_mints or assignment['mint'] in queued_mints:
   result['already_present']+=1;continue
  q.enqueue_after_assignment(mint=assignment['mint'],operation_id=operation_id,assignment=assignment,canonical_birth={'mint':assignment['mint'],'admission_provenance':f'MISSED_POST_COMMIT_{operation_id.upper()}_MONITOR_RECONCILIATION'})
  result['enqueued']+=1
 return result

def reconcile_watchtower_assignment_admissions(db_path:str,q:MonitorQueue|None=None)->dict[str,int]:
 """Idempotently admit post-activation canonical Watchtower memberships."""
 return _reconcile_migration_price_assignment_admissions(db_path,q,operator_id=_WATCHTOWER_OPERATOR_ID,operation_id='watchtower')

def reconcile_watchtower_deep_assignment_admissions(db_path:str,q:MonitorQueue|None=None)->dict[str,int]:
 """Idempotently admit already-assigned Deep memberships; never classifies candidates."""
 return _reconcile_migration_price_assignment_admissions(db_path,q,operator_id=_WATCHTOWER_DEEP_OPERATOR_ID,operation_id='watchtower_deep')
class MonitorBirdeyeTransport:
 def __init__(self,binding=None,now=None):self.binding=binding or BirdeyeProductionBinding();self.now=now or time.time;self.last_entry_diagnostic=None
 def _rate_limited(self,result,endpoint_class):
  return ProviderRateLimited(ProviderRateLimitMetadata.from_headers(provider='BIRDEYE',endpoint_class=endpoint_class,status=result.status_code,request_timestamp=int(self.now()),headers=getattr(result,'response_headers',{}) or {}))
 def __call__(self,p):
  now=int(time.time()); start=p.get('last_observation_at') or p.get('entry_timestamp'); interval=p.get('candle_resolution')
  if not start or not interval: raise ValueError('WAITING_FOR_ENTRY_REFERENCE')
  start=int(start)
  if start > now: raise ValueError('INSUFFICIENT_EVIDENCE:ENTRY_AFTER_NOW')
  built=build_birdeye_ohlcv_request(address=p['mint'],interval=interval,time_from=start,time_to=now); params=built['request_parameters']
  result=self.binding(built)
  if result.status_code!=200:
   if result.status_code==429: raise self._rate_limited(result,'OHLCV')
   # Bounded non-secret diagnostic: provider code/message only, never raw body.
   body=result.payload if isinstance(result.payload,dict) else {}
   data=body.get('data') if isinstance(body.get('data'),dict) else {}
   code=body.get('code',data.get('code',''))
   message=body.get('message',body.get('msg',data.get('message',data.get('msg',''))))
   diagnostic={'http_status':result.status_code,'code':str(code)[:80],'message':str(message)[:240],'shape':sorted(body)[:20]}
   raise ConnectionError(f'{result.error_state or "HTTP_"+str(result.status_code)}:{json.dumps(diagnostic,sort_keys=True,separators=(",",":"))}')
  items=((result.payload or {}).get('data') or {}).get('items') or []
  candles=[{'timestamp':int(x.get('unixTime',x.get('unix_time',x.get('timestamp',0)))),'open':float(x.get('o',x.get('open',x.get('c',0))) or 0),'high':float(x.get('h',x.get('high',x.get('c',0))) or 0),'low':float(x.get('l',x.get('low',x.get('c',0))) or 0),'mc':float(x.get('c',x.get('close',x.get('value',0))) or 0)} for x in items]
  usable=[x for x in candles if x['timestamp'] and x['mc']>0]
  return {'candles':usable,'resolution':params['type'],'http_status':200,'request':params,'request_manifest':built,
          'empty_ohlcv_classification':'PROVIDER_EMPTY_RESPONSE' if not items else 'NO_USABLE_CANDLE'}
 def acquire_watchtower_entry(self,p,plan):
  """Acquire only the frozen two-second entry window after retained evidence is audited."""
  built=build_birdeye_ohlcv_request(address=p['mint'],interval='1s',time_from=plan['time_from'],time_to=plan['time_to'])
  result=self.binding(built)
  target=int(plan['migration_timestamp'])+1
  diagnostic={'normalizer_version':'STRICT_ENTRY_NORMALIZER_V2','provider':'Birdeye','request_family':'BIRDEYE_OHLCV_1S_STRICT_MIGRATION_WINDOW','http_status':int(result.status_code),'provider_item_count':0,'normalized_item_count':0,'exact_target_item_count':0,'target_timestamp':target,'target_timestamp_present':False,'value_field_present':False,'value_parse_state':'NOT_EVALUATED','normalization_state':'NOT_EVALUATED','failure_stage':None,'failure_category':None}
  self.last_entry_diagnostic=diagnostic
  if result.status_code != 200:
   diagnostic.update({'normalization_state':'NOT_RUN','failure_stage':'HTTP_STATUS','failure_category':'HTTP_429' if result.status_code==429 else 'HTTP_NON_200'})
   if result.status_code==429: raise self._rate_limited(result,'OPENING')
   raise StrictEntryNormalizationError(diagnostic['failure_category'],diagnostic)
  payload=result.payload if isinstance(result.payload,dict) else None
  items=((payload or {}).get('data') or {}).get('items') if payload else None
  if not isinstance(items,list):
   diagnostic.update({'normalization_state':'FAILED','failure_stage':'RESPONSE_CONTAINER','failure_category':'NORMALIZATION_FAILED'})
   raise StrictEntryNormalizationError(diagnostic['failure_category'],diagnostic)
  diagnostic['provider_item_count']=len(items)
  target_items=[]
  for item in items:
   if not isinstance(item,dict):
    diagnostic.update({'normalization_state':'FAILED','failure_stage':'ITEM_SHAPE','failure_category':'NORMALIZATION_FAILED'})
    raise StrictEntryNormalizationError(diagnostic['failure_category'],diagnostic)
   raw_timestamp=item.get('unixTime',item.get('unix_time'))
   try: timestamp=int(raw_timestamp)
   except (TypeError,ValueError):
    diagnostic.update({'normalization_state':'FAILED','failure_stage':'TIMESTAMP_PARSE','failure_category':'NORMALIZATION_FAILED'})
    raise StrictEntryNormalizationError(diagnostic['failure_category'],diagnostic)
   diagnostic['normalized_item_count']+=1
   if timestamp!=target: continue
   diagnostic['target_timestamp_present']=True;diagnostic['exact_target_item_count']+=1
   raw_value=item.get('c',item.get('close'))
   diagnostic['value_field_present']=raw_value is not None
   if raw_value is None: continue
   try: value=float(raw_value)
   except (TypeError,ValueError):
    diagnostic.update({'normalization_state':'FAILED','failure_stage':'VALUE_PARSE','value_parse_state':'INVALID','failure_category':'TARGET_VALUE_INVALID'})
    raise StrictEntryNormalizationError(diagnostic['failure_category'],diagnostic)
   if value<=0:
    diagnostic.update({'normalization_state':'FAILED','failure_stage':'VALUE_VALIDATE','value_parse_state':'NON_POSITIVE','failure_category':'TARGET_VALUE_INVALID'})
    raise StrictEntryNormalizationError(diagnostic['failure_category'],diagnostic)
   target_items.append({'timestamp':timestamp,'mc':value})
  if not items:
   diagnostic.update({'normalization_state':'COMPLETE','failure_stage':'TARGET_RESOLUTION','failure_category':'NO_PROVIDER_ITEMS','value_parse_state':'NOT_PRESENT'})
   raise StrictEntryNormalizationError(diagnostic['failure_category'],diagnostic)
  if not diagnostic['target_timestamp_present']:
   diagnostic.update({'normalization_state':'COMPLETE','failure_stage':'TARGET_RESOLUTION','failure_category':'TARGET_SECOND_ABSENT','value_parse_state':'NOT_PRESENT'})
   raise StrictEntryNormalizationError(diagnostic['failure_category'],diagnostic)
  if not target_items:
   diagnostic.update({'normalization_state':'COMPLETE','failure_stage':'TARGET_VALUE','failure_category':'TARGET_VALUE_ABSENT','value_parse_state':'ABSENT'})
   raise StrictEntryNormalizationError(diagnostic['failure_category'],diagnostic)
  if len(target_items)!=1:
   diagnostic.update({'normalization_state':'FAILED','failure_stage':'TARGET_RESOLUTION','failure_category':'NORMALIZATION_FAILED','value_parse_state':'AMBIGUOUS'})
   raise StrictEntryNormalizationError(diagnostic['failure_category'],diagnostic)
  diagnostic.update({'normalization_state':'COMPLETE','failure_stage':None,'failure_category':None,'value_parse_state':'VALID'})
  return target_items[0],built

def resolve_operation_migration_boundary(db_path: str, mint: str, *, canonical_migration_db_path: str | None = None) -> dict[str,Any] | None:
 """Return one normalized, read-only migration boundary for the shared entry path.

 Legacy Monitor projections are preferred when present.  A separately bound
 canonical evidence database may fill a projection gap, but its result is
 normalized before the opening primitive sees it.  This deliberately has no
 operation-specific branch: membership selects the operation; a migration
 boundary has the same meaning for Watchtower and WATCHTOWER_DEEP.
 """
 with _read_only_connection(db_path) as con:
  try:
   row=con.execute('SELECT migration_tx,migration_time,pool FROM migrated_tokens WHERE mint=?',(mint,)).fetchone()
   if row and row[1]:
    return {'migration_signature':row[0],'migration_timestamp':int(row[1]),'expected_pool':row[2],'migration_slot':None,'evidence_source':'migrated_tokens'}
   row=con.execute('SELECT migration_signature,migration_time,NULL FROM wt_launch_audit WHERE mint=?',(mint,)).fetchone()
   if row and row[1]:
    return {'migration_signature':row[0],'migration_timestamp':int(row[1]),'expected_pool':row[2],'migration_slot':None,'evidence_source':'wt_launch_audit'}
  except sqlite3.OperationalError:
   pass
 if not canonical_migration_db_path:
  return None
 with _read_only_connection(canonical_migration_db_path) as con:
  try:
   row=con.execute("SELECT migration_tx,migrated_at,migration_slot,pumpswap_pool_address,lifecycle_stage FROM token_analysis WHERE mint=?",(mint,)).fetchone()
  except sqlite3.OperationalError:
   return None
 if not row or not row[0] or row[1] is None or row[2] is None or not row[3] or row[4] != 'migrated':
  return None
 return {'migration_signature':row[0],'migration_timestamp':int(row[1]),'expected_pool':row[3],'migration_slot':int(row[2]),'evidence_source':'token_analysis'}

def _watchtower_entry_inventory(db_path: str, mint: str, *, canonical_migration_db_path: str | None = None) -> dict[str,Any]:
 """Read one normalized migration boundary, then use the shared opening plan."""
 boundary=resolve_operation_migration_boundary(db_path,mint,canonical_migration_db_path=canonical_migration_db_path)
 if not boundary:
  return {'result':'WAITING_FOR_ENTRY_REFERENCE','reason':'ENTRY_EVENT_NOT_OCCURRED','missing_evidence':['migration_timestamp','migration_signature'],'next_entry_evaluation_at':int(time.time())+60}
 timestamp=boundary['migration_timestamp']
 return {'result':'ENTRY_EVIDENCE_ACQUISITION_DUE','reason':'RETAINED_EVIDENCE_MISSING',**boundary,'missing_evidence':['FIRST_FULL_POST_MIGRATION_SECOND_MC'],'entry_acquisition':{'provider':'Birdeye','endpoint':'/defi/v3/ohlcv','interval':'1s','time_from':timestamp,'time_to':timestamp+2,'migration_timestamp':timestamp},'next_entry_evaluation_at':int(time.time())}

def _migration_boundary_entry_reference(db_path: str,p:dict[str,Any])->dict[str,Any]:
 return _watchtower_entry_inventory(db_path,p['mint'],canonical_migration_db_path=os.getenv('OPERATION_MONITOR_CANONICAL_MIGRATION_DB_PATH'))

def _scenario_d_committed_entry_reference(_db_path: str,p:dict[str,Any])->dict[str,Any]:
 result=derive_monitor_entry(p.get('scenario_d_evidence'))
 result['next_entry_evaluation_at']=None
 return result

_ENTRY_REFERENCE_PRODUCERS={
 'MIGRATION_BOUNDARY':_migration_boundary_entry_reference,
 'SCENARIO_D_COMMITTED':_scenario_d_committed_entry_reference,
}

def _entry_inventory(db_path: str,p:dict[str,Any])->dict[str,Any]:
 """Select an entry-reference producer from the capability declaration."""
 capability=monitor_capability_for_operation(str(p.get('operation_id') or ''))
 producer=(capability or {}).get('entry_reference_producer')
 resolver=_ENTRY_REFERENCE_PRODUCERS.get(str(producer or ''))
 if resolver is None:
  return {'result':'INSUFFICIENT_EVIDENCE','reason':'ENTRY_REFERENCE_PRODUCER_UNDECLARED','missing_evidence':['declared operation Monitor entry-reference producer'],'next_entry_evaluation_at':None}
 return resolver(db_path,p)
class MonitorWorker:
 def __init__(self,q,transport=None,persist=None,*,db_path=None,before_ack=None,terminal_finalizer_factory=None,provider_bindings=None,opening_jobs_path=None,provider_work_path=None):
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
  # The generic token-data binding registry is injected; Monitor creates no
  # Helius client and retains its existing Birdeye transport unchanged.
  self.provider_bindings=provider_bindings or {}
  self.opening_jobs_path=opening_jobs_path or getattr(q,'opening_jobs_path',None)
  self.provider_work_path=provider_work_path or getattr(q,'provider_work_path',None)

 def _evaluate_completed_opening_policy(self, opening, job_id, *, provisional=False):
  """Evaluate the declared policy only after compact opening evidence commits."""
  row=opening.get(self.opening_jobs_path,job_id)
  scope=(row or {}).get('acquisition_scope') or {}
  exact=scope.get('execution_mode') == opening.EXACT_WINDOW_DIAGNOSTIC
  if not row or (row.get('state') != 'EVIDENCE_COMPLETE' and not (provisional and exact and row.get('state') == opening.PENDING_OPENING_BLOCK)): return None
  policy=str(row.get('entry_reference_policy') or '')
  if policy != 'SCENARIO_D':
   result={'state':'INSUFFICIENT_EVIDENCE','reason':'ENTRY_REFERENCE_POLICY_UNSUPPORTED'}
   return opening.mark_provisional_policy_result(self.opening_jobs_path,job_id,result) if provisional else opening.mark_policy_result(self.opening_jobs_path,job_id,result)
  from pathlib import Path
  from src.ops.entry_reference_policy_adapters import evaluate
  from src.ops.byzantine_live_cluster_recurrence import classify,seed
  capability=monitor_capability_for_operation(str(row.get('operation_id') or '')) or {}
  relative=capability.get('scenario_d_cluster_seed_path')
  if not relative:
   result={'state':'INSUFFICIENT_EVIDENCE','reason':'SCENARIO_D_CLUSTER_SEED_UNDECLARED'}
   return opening.mark_provisional_policy_result(self.opening_jobs_path,job_id,result) if provisional else opening.mark_policy_result(self.opening_jobs_path,job_id,result)
  root=Path(__file__).resolve().parents[2]
  frozen_seed=seed(str(root / str(relative)))
  projection=row.get('policy_projection')
  if provisional and exact and projection is None:
   # Exact-window diagnostics retain partial evidence before their mandatory
   # final block.  Policy evaluation must see that same compact action shape
   # without claiming completion or releasing downstream work.
   sequence=row.get('sequence') or {}
   projection=opening._projection(row,sequence,{
    'state':'PROVISIONAL_EXACT_WINDOW','blocks_consumed':int(sequence.get('blocks_consumed') or 0),
   })
  if not isinstance(projection,dict): return None
  result=evaluate(policy,projection,classifier=lambda action: classify(action,frozen_seed),boundary_states={})
  return opening.mark_provisional_policy_result(self.opening_jobs_path,job_id,result) if provisional else opening.mark_policy_result(self.opening_jobs_path,job_id,result)

 def process_entry_reference_opening_once(self, *, now=None):
  """Consume one durable generic opening request through Monitor's gate.

  Transport is supplied by the existing generic provider-binding registry.
  The opening job remains the compact-result authority: its consume methods
  persist canonical compact facts before generic work is completed.
  """
  if not self.opening_jobs_path or not self.provider_work_path: return None
  from src.ops import generic_provider_work_scheduler as scheduler
  from src.ops import live_opening_action_job as opening
  # Complete evidence has no further provider dependency.  Reconcile it
  # before selecting ready work so a restart after the final compact block
  # converges to the policy terminal state without redispatching a block.
  pending_policy=opening.policy_pending_job_ids(self.opening_jobs_path)
  if pending_policy:
   job_id=pending_policy[0]
   result=self._evaluate_completed_opening_policy(opening,job_id)
   scheduler.sync_opening_action_job(self.provider_work_path,self.opening_jobs_path,job_id)
   return {'state':'POLICY_RECONCILED_AFTER_RESTART','job_id':job_id,'result':result}
  stamp=int(time.time() if now is None else now)
  try:
   def scope_dispatchable(work):
    payload=work.get('payload') or {}
    return opening.request_dispatchable(self.opening_jobs_path,str(payload.get('opening_job_id') or ''),str(payload.get('request_id') or ''))
   work=scheduler.admit_next(self.provider_work_path,self.q,now=stamp,families=('ENTRY_REFERENCE_OPENING',),pre_admit=scope_dispatchable)
  except BudgetDenied as exc:
   # DEV-012 exhaustion is a temporary admission result.  The scheduler has
   # not moved the durable work out of PENDING yet, so returning here retains
   # its identity for the next eligible window rather than crashing the
   # Supervisor loop or manufacturing a terminal opening outcome.
   return {'state':'BUDGET_DEFERRED','reason':str(exc)}
  if not work: return None
  if work.get('pre_admission_denied'):
   scheduler.discard_before_dispatch(self.provider_work_path,work['work_id'],now=stamp)
   scheduler.sync_opening_action_job(self.provider_work_path,self.opening_jobs_path,(work.get('payload') or {}).get('opening_job_id'),now=stamp)
   return {'state':'OPENING_SCOPE_BLOCKED_BEFORE_TRANSPORT','work_id':work['work_id']}
  payload=work['payload']; family=payload['request_family']
  request=opening.plan_next(self.opening_jobs_path,payload['opening_job_id'])
  if request.get('request_id') != payload['request_id']:
   # A crash may occur after the opening job's compact state transaction
   # commits but before this scheduler work is marked COMPLETE.  The opening
   # job is the causal authority: a different durable next request proves the
   # old identity was already consumed.  Retire only that stale work and
   # project the one durable successor; never reacquire its provider payload.
   scheduler.complete(self.provider_work_path,work['work_id'],now=stamp)
   scheduler.sync_opening_action_job(self.provider_work_path,self.opening_jobs_path,payload['opening_job_id'],now=stamp)
   return {'state':'RECONCILED_ALREADY_CONSUMED','request_id':payload['request_id']}
  key=(request['provider'],request['method_endpoint']); binding=self.provider_bindings.get(key)
  if binding is None: raise RuntimeError('OPENING_PROVIDER_BINDING_UNAVAILABLE')
  outcome=binding({'request_family':family,'method_endpoint':request['method_endpoint'],'request_parameters':request['request_parameters']})
  status=int(getattr(outcome,'status_code',outcome[0] if isinstance(outcome,tuple) else 200))
  body=getattr(outcome,'payload',outcome[1] if isinstance(outcome,tuple) and len(outcome)>1 else outcome)
  if status == 429:
   # Reuse the MonitorQueue's shared DEV-009 gate; the work keeps its
   # durable identity and becomes eligible only after the shared deadline.
   from src.ops.provider_rate_limit_gate import ProviderRateLimitMetadata
   headers=getattr(outcome,'response_headers',{}) or {}
   metadata=ProviderRateLimitMetadata.from_headers(provider='HELIUS',endpoint_class=family,status=429,request_timestamp=stamp,headers=headers)
   self.q.apply_provider_rate_limit(metadata=metadata,now=stamp,error='HTTP_429')
   scheduler.defer(self.provider_work_path,work['work_id'],now=stamp)
   return {'state':'PROVIDER_BACKOFF','status':429}
  if status != 200:
   opening.record_terminal(self.opening_jobs_path,payload['opening_job_id'],'PROVIDER_BLOCKED',f'HTTP_{status}')
   # Terminal job and durable scheduled identity converge before restart.
   scheduler.complete(self.provider_work_path,work['work_id'],now=stamp)
   return {'state':'PROVIDER_BLOCKED','status':status}
  if family=='CHAIN_CREATE_TRANSACTION': result=opening.consume_create(self.opening_jobs_path,payload['opening_job_id'],body)
  elif family=='OPENING_SEQUENCE_BLOCK': result=opening.consume_block(self.opening_jobs_path,payload['opening_job_id'],body)
  elif family=='SOL_USD_FX':
   from src.ops.token_data_fact_adapters import fx_fact
   job=opening.get(self.opening_jobs_path,payload['opening_job_id']) or {}
   result=opening.consume_fx(self.opening_jobs_path,payload['opening_job_id'],fx_fact(body,int(job.get('create_timestamp') or 0)))
  else: raise RuntimeError('OPENING_PROVIDER_FAMILY_UNSUPPORTED')
  scheduler.complete(self.provider_work_path,work['work_id'],now=stamp)
  self._evaluate_completed_opening_policy(opening,payload['opening_job_id'], provisional=(
   family == 'OPENING_SEQUENCE_BLOCK' and result.get('exact_window_provisional') is True))
  self._evaluate_completed_opening_policy(opening,payload['opening_job_id'])
  scheduler.sync_opening_action_job(self.provider_work_path,self.opening_jobs_path,payload['opening_job_id'],now=stamp)
  return result
 def _authoritative_terminal(self,p:dict[str,Any])->bool:
  """Read the lifecycle authority immediately before any price acquisition."""
  try:
   with _read_only_connection(self.db_path) as con:
    row=con.execute('SELECT monitor_state,next_observation_at FROM operation_monitor_facts WHERE operation_id=? AND mint=?',(p.get('operation_id'),p.get('mint'))).fetchone()
  except sqlite3.Error:
   return False
  return bool(row and row[0] in _TERMINAL_MONITOR_STATES and row[1] is None)
 def _activate_from_qualified_opening(self,p:dict[str,Any])->None:
  """Persist one live lifecycle transition before durable 15m evidence.

  Strict opening justifies the entry reference and initial peak, but not a
  fabricated current price, drawdown, or OHLC candle.
  """
  now=int(time.time());assignment=p.get('assignment') or {'digest':p.get('assignment_digest')};usd=p.get('entry_mc_usd');native=p.get('entry_native_mc_sol')
  if not p.get('entry_timestamp') or (usd is None and native is None): raise ValueError('QUALIFIED_ENTRY_REFERENCE_REQUIRED')
  # Native Scenario-D is a genuine entry reference.  It activates live price
  # eligibility without inventing USD metrics; USD fields remain NULL until FX.
  values=(p['operation_id'],p['mint'],p.get('cohort','PROSPECTIVE_MONITOR_COHORT'),assignment.get('assigned_at',now),_h(assignment),p['entry_method'],int(p['entry_timestamp']),float(usd) if usd is not None else None,str(native) if native is not None else None,'QUALIFIED',p.get('entry_exactness','FIRST_FULL_POST_MIGRATION_SECOND_MC'),'MONITORING_ACTIVE',now,None,float(usd) if usd is not None else None,int(p['entry_timestamp']),1.0 if usd is not None else None,'WAITING_FOR_FX_ATTACHMENT' if usd is None else 'WAITING_FOR_COMPLETED_CANDLE',_h({'activation':'QUALIFIED_ENTRY_REFERENCE','entry_provenance':p.get('entry_provenance'),'mint':p['mint'],'native':native}),now,now)
  sql='''INSERT INTO operation_monitor_facts(operation_id,mint,cohort_class,assignment_timestamp,assignment_provenance,entry_method,entry_timestamp,entry_mc_usd,entry_native_mc_sol,entry_status,entry_exactness,monitor_state,monitor_started_at,next_observation_at,running_peak_mc_usd,running_peak_timestamp,running_peak_multiple,evidence_status,provenance_digest,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(operation_id,mint) DO NOTHING'''
  receipt=self.persist(WriteItem('enrichment','operation-monitor-activate-from-strict-opening',[(sql,values)],_h({'activation':p['operation_id'],'mint':p['mint'],'entry':p['entry_timestamp']})))
  if not receipt or not receipt.committed: raise RuntimeError('MONITOR_ACTIVATION_UNCOMMITTED')
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
  p=c.payload['envelope']
  try: finalizer=self.terminal_finalizer_factory(self.db_path,before_dispatch=self.q.admit_provider_dispatch)
  except TypeError: finalizer=self.terminal_finalizer_factory(self.db_path)
  fact=finalizer.freeze(p['mint'])
  if finalizer.logical_job_identity(fact)!=p.get('logical_identity'):
   raise ValueError('TERMINAL_ATH_IDENTITY_MISMATCH')
  result=finalizer.finalize(p['mint'],interval='15m',watchtower_price_fact_contract=True)
  if result['state'] not in {'FINALIZED','ALREADY_FINALIZED'}: raise RuntimeError('TERMINAL_ATH_UNCOMMITTED')
  self.q.queue.ack(c); self.last_ack_timestamp=time.time(); return result
 def process_once(self):
  if not self.q.provider_eligible(): return 0
  claimed=self.q.claim(CONCURRENCY)
  for c in claimed:
   try:
    p=c.payload['envelope'];self.heartbeat=time.time()
    # Defence in depth: selection is checked before claim and again before a
    # provider-capable branch.  A stale processing envelope cannot bypass DEV
    # soak isolation after restart.
    if not self.q.soak_allows(p):
     target=self.q.queue.root/'pending'/c.path.name;os.replace(c.path,target);self.q.queue._fsync_directory(c.path.parent);self.q.queue._fsync_directory(target.parent);continue
    if p.get('work_type')=='WATCHTOWER_TERMINAL_ATH_FINALIZATION':
     self._process_terminal_ath(c); continue
    # A stale polling or opening envelope never outranks durable terminal
    # state.  Terminal ATH finalization above is intentionally separate.
    if self._authoritative_terminal(p):
     self.q.queue.ack(c);self.last_ack_timestamp=time.time();continue
    # The previous provider pass already committed its authoritative fact and
    # successor.  A crash after that commit but before ACK must converge by
    # acknowledging this predecessor, never acquiring price data twice.
    if p.get('active_successor_job_id') and self.q.has_durable_message(str(p['active_successor_job_id'])):
     self.q.queue.ack(c);self.last_ack_timestamp=time.time();continue
    # Assignment-first monitoring deliberately has no generic live window.
    # Retained evidence is evaluated first; only an operation-specific bounded plan may consume capacity.
    if not _qualified_live_entry(p) or not p.get('candle_resolution'):
     if p.get('monitor_state')=='WAITING_FOR_ENTRY_REFERENCE' and int(p.get('next_entry_evaluation_at') or 0)>int(time.time()):
      target=self.q.queue.root/'pending'/c.path.name;os.replace(c.path,target);self.q.queue._fsync_directory(c.path.parent);self.q.queue._fsync_directory(target.parent);continue
     evaluation=_entry_inventory(self.db_path,p); p['entry_evaluation_last_run']=int(time.time());p['entry_evaluation_result']=evaluation['result'];p['missing_entry_evidence']=evaluation.get('missing_evidence',[]);p['next_entry_evaluation_at']=evaluation.get('next_entry_evaluation_at');p['monitor_state']=evaluation['result'];p['entry_acquisition_request']=evaluation.get('entry_acquisition')
     if evaluation['result']=='ENTRY_EVIDENCE_ACQUISITION_DUE':
      self.q.admit_provider_dispatch(p['mint'],'OPENING');first,manifest=self.transport.acquire_watchtower_entry(p,evaluation['entry_acquisition'])
      self.q.record_provider_success()
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
      capability=monitor_capability_for_operation(str(p.get('operation_id') or '')) or {}
      if capability.get('persist_waiting_entry_fact') is True:
       self._persist_waiting_entry_fact(p,evaluation)
     c.payload['envelope']=p
     self.q.queue._replace_payload(c.path,c.payload)
     if not _qualified_live_entry(p):
      target=self.q.queue.root/'pending'/c.path.name
      os.replace(c.path,target); self.q.queue._fsync_directory(c.path.parent); self.q.queue._fsync_directory(target.parent)
      continue
    # Strict opening activates the live lifecycle.  The following request is
    # durable 15m evidence, not an activation prerequisite.
    self._activate_from_qualified_opening(p)
    # Freeze redacted request identity before provider activity; retained in retry/dead-letter.
    dispatch=int(time.time()); built=build_birdeye_ohlcv_request(address=p['mint'],interval=p.get('candle_resolution',''),time_from=int(p.get('last_observation_at') or p.get('entry_timestamp') or 0),time_to=dispatch)
    p['request_manifest']={**built,'dispatch_time':dispatch}; c.payload['envelope']=p; self.q.queue._replace_payload(c.path,c.payload)
    self.q.admit_provider_dispatch(p['mint'],'15M_EVIDENCE')
    p['provider_physical_attempt_count']=int(p.get('provider_physical_attempt_count') or 0)+1
    c.payload['envelope']=p;self.q.queue._replace_payload(c.path,c.payload)
    response=self.transport(p) # provider call has no DB handle/transaction
    p['provider_http_success_count']=int(p.get('provider_http_success_count') or 0)+1
    c.payload['envelope']=p;self.q.queue._replace_payload(c.path,c.payload)
    self.q.record_provider_success()
    candles=response.get('candles',[])
    if len(json.dumps(candles))>8192: raise ResponseCapacityExceeded('COMPACT_RESPONSE_CAPACITY_EXCEEDED')
    if not candles:
     raise NoUsableOhlcvEvidence(now=dispatch,classification=str(response.get('empty_ohlcv_classification') or 'NO_USABLE_CANDLE'))
    p['provider_normalized_usable_count']=int(p.get('provider_normalized_usable_count') or 0)+1
    c.payload['envelope']=p;self.q.queue._replace_payload(c.path,c.payload)
    latest=max(candles,key=lambda x:x['timestamp']) if candles else {};mc=float(latest.get('mc',0));ts=int(latest.get('timestamp',time.time()));entry_usd=p.get('entry_mc_usd');entry=float(entry_usd) if entry_usd is not None else None
    # A fresh readonly lookup occurs only after provider parsing.  Entry is a
    # prospective state boundary and therefore participates in the peak.
    try:
     with _read_only_connection(self.db_path) as prior:
      old=prior.execute('SELECT running_peak_mc_usd,running_peak_timestamp FROM operation_monitor_facts WHERE operation_id=? AND mint=?',(p['operation_id'],p['mint'])).fetchone()
    except sqlite3.Error: old=None
    candidates=[]
    if entry is not None: candidates.append((entry,int(p.get('entry_timestamp') or ts)))
    if old and old[0] is not None: candidates.append((float(old[0]),int(old[1] or ts)))
    candidates += [(float(x.get('high') or x['mc']),int(x['timestamp'])) for x in candles]
    peak,peak_ts=max(candidates,key=lambda x:(x[0],-x[1]));dd=(peak-mc)*100/peak if peak else 0;terminal=p['operation_id'].lower() in {'watchtower','watchtower_deep'} and dd>=85;now=int(time.time());assignment=p.get('assignment') or {'digest':p.get('assignment_digest')}
    f={'operation_id':p['operation_id'],'mint':p['mint'],'cohort_class':p['cohort'],'assignment_timestamp':assignment.get('assigned_at',now),'assignment_provenance':_h(assignment),'entry_method':p['entry_method'],'entry_timestamp':p.get('entry_timestamp',ts),'entry_mc_usd':entry,'entry_status':'QUALIFIED','entry_exactness':'ADAPTER_FROZEN','latest_mc_usd':mc,'latest_mc_timestamp':ts,'current_multiple':mc/entry if entry is not None else None,'running_peak_mc_usd':peak,'running_peak_timestamp':peak_ts,'running_peak_multiple':peak/entry if entry is not None else None,'drawdown_percent':dd,'reached_2x':int(entry is not None and peak>=entry*2),'reached_5x':int(entry is not None and peak>=entry*5),'reached_10x':int(entry is not None and peak>=entry*10),'monitor_state':'PRICE_MONITOR_COMPLETE_COLLAPSED' if terminal else 'MONITORING_ACTIVE','monitor_started_at':now,'last_observation_at':ts,'next_observation_at':None if terminal else ts+60,'monitor_completed_at':now if terminal else None,'provider_call_count':1,'candles_retained':len(candles),'candle_resolution':response.get('resolution'),'evidence_status':'QUALIFIED','provenance_digest':_h({'request':response.get('request'),'response':response})}
    cols=list(f)+['created_at','updated_at'];vals=tuple(f[x] for x in f)+(now,now);updates=','.join(f'{x}=excluded.{x}' for x in cols if x not in {'operation_id','mint','created_at','assignment_timestamp','assignment_provenance','entry_method','entry_timestamp','entry_mc_usd','entry_status','entry_exactness'})
    sql=f"INSERT INTO operation_monitor_facts({','.join(cols)}) VALUES({','.join('?' for _ in cols)}) ON CONFLICT(operation_id,mint) DO UPDATE SET {updates},running_peak_mc_usd=MAX(operation_monitor_facts.running_peak_mc_usd,excluded.running_peak_mc_usd),provider_call_count=operation_monitor_facts.provider_call_count+1"
    request_id=_h(response.get('request_manifest') or p.get('request_manifest') or response.get('request'))
    obs_sql='INSERT OR IGNORE INTO operation_monitor_observations(operation_id,mint,observation_timestamp,mc_usd,resolution,source,request_identity,provenance_digest,created_at,open_mc_usd,high_mc_usd,low_mc_usd,close_mc_usd) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)'
    observations=[(obs_sql,(p['operation_id'],p['mint'],int(x['timestamp']),float(x['mc']),response.get('resolution'),'BIRDEYE_OHLCV',request_id,_h(x),now,float(x.get('open') or x['mc']),float(x.get('high') or x['mc']),float(x.get('low') or x['mc']),float(x['mc']))) for x in candles]
    self.last_receipt=self.persist(WriteItem('enrichment','operation-monitor',observations+[(sql,vals)],_h({'job':c.message_id,'fact':f})))
    if not self.last_receipt or not self.last_receipt.committed:raise ConnectionError(getattr(self.last_receipt,'error','WRITER_UNAVAILABLE'))
    p['provider_observation_committed_count']=int(p.get('provider_observation_committed_count') or 0)+1
    p['provider_outcome']='OBSERVATION_COMMITTED';c.payload['envelope']=p;self.q.queue._replace_payload(c.path,c.payload)
    if terminal:
     self.q.enqueue_terminal_ath_finalization(f,provenance='POST_COMMIT_TERMINAL_COLLAPSE')
    else:
     with _read_only_connection(self.db_path) as committed:
      committed.row_factory=sqlite3.Row
      row=committed.execute('SELECT * FROM operation_monitor_facts WHERE operation_id=? AND mint=?',(p['operation_id'],p['mint'])).fetchone()
     if row is None: raise RuntimeError('MONITOR_SUCCESSOR_FACT_MISSING')
     successor=self.q.enqueue_active_successor(predecessor_id=c.message_id,fact=dict(row),envelope=p,now=now)
     if successor['status'] != 'ENQUEUED_ACTIVE_SUCCESSOR': raise RuntimeError(f"MONITOR_SUCCESSOR_NOT_ENQUEUED:{successor['status']}")
     # Checkpoint the completed predecessor only after its successor is
     # durable.  Retry/restart can now ACK without repeating provider work.
     p['active_successor_job_id']=successor['job_id'];c.payload['envelope']=p
     self.q.queue._replace_payload(c.path,c.payload)
    if self.before_ack:self.before_ack()
    self.q.queue.ack(c);self.last_ack_timestamp=time.time();assert self.last_receipt.commit_timestamp<self.last_ack_timestamp
   except Exception as e:
   # Preserve provider capacity/backoff as a visible queue state; retry ownership remains EvidenceIntakeQueue.
    if isinstance(e,NoUsableOhlcvEvidence):
     self.q.defer_empty_ohlcv(c,next_eligible_at=e.next_eligible_at,classification=e.classification)
     continue
    if isinstance(e,TerminalProviderFailure):
     self.q.defer_terminal_failure(c,classification=e.classification)
     continue
    if isinstance(e,ProviderRateLimited):
     self.q.defer_rate_limited(c,metadata=e.metadata,error='HTTP_429')
     continue
    if isinstance(e,ProviderCapacityBackoff):
     self.q.defer_rate_limited(c,retry_after=e.retry_after,error='HTTP_429')
     continue
    if 'HTTP_429' in str(e) or 'Too many requests' in str(e):
     self.q.defer_rate_limited(c,error='HTTP_429')
     continue
    # A completed provider write with a durable successor only needs an ACK
    # recovery. It is immediately eligible for that non-provider convergence.
    ready_now=bool(p.get('active_successor_job_id') and self.q.has_durable_message(str(p['active_successor_job_id'])))
    self.q.defer_retryable(c,classification=type(e).__name__+(':'+str(e) if str(e) else ''),ready_now=ready_now)
  return len(claimed)
