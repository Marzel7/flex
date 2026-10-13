"""Operation-neutral, injected-transport executor; real dispatch remains opt-in."""
from __future__ import annotations
import json, sqlite3
from typing import Any, Callable
from src.ops.universal_incremental_window import WindowState, reduce_checkpoint
from src.ops.universal_monitor_runtime import OWNER_UNIVERSAL, dispatch_eligibility

WORK_TYPES=frozenset({'ACQUIRE_ENTRY','ACQUIRE_PRICE_WINDOW','RESUME_PRICE_WINDOW','RECONCILE_AMBIGUOUS_PROVIDER_CALL'})
STATES=frozenset({'PLANNED','BUDGET_ADMITTED','PROVIDER_CALL_STARTED','PROVIDER_CALL_COMPLETED','FACT_COMMITTED','AMBIGUOUS_PROVIDER_CALL','TERMINAL','FAILED_CLOSED'})

class UniversalProviderExecutor:
 def __init__(self, *, entry_transport:Callable, window_transport:Callable, terminal_policy:Callable): self.entry_transport,self.window_transport,self.terminal_policy=entry_transport,window_transport,terminal_policy
 def execute(self, conn:sqlite3.Connection, work_id:str)->dict[str,Any]:
  row=conn.execute('SELECT operation_id,mint,work_type,resume_from,checkpoint_boundary,state FROM universal_monitor_global_work WHERE work_id=?',(work_id,)).fetchone()
  if not row:return {'state':'FAILED_CLOSED','reason':'WORK_NOT_FOUND','provider_calls':0}
  op,mint,kind,resume,due,state=row
  if state in {'FACT_COMMITTED','TERMINAL','FAILED_CLOSED'}:return {'state':state,'provider_calls':0}
  if kind not in WORK_TYPES:return {'state':'FAILED_CLOSED','reason':'UNSUPPORTED_WORK_TYPE','provider_calls':0}
  owner=conn.execute('SELECT owner FROM universal_monitor_ownership WHERE operation_id=? AND mint=?',(op,mint)).fetchone()
  if not owner or owner[0]!=OWNER_UNIVERSAL:return {'state':'FAILED_CLOSED','reason':'OWNERSHIP_GUARD','provider_calls':0}
  if not dispatch_eligibility(conn,work_id=work_id).get('eligible'):return {'state':'PLANNED','reason':'DISPATCH_DISABLED_OR_UNADMITTED','provider_calls':0}
  with conn: conn.execute("UPDATE universal_monitor_global_work SET state='PROVIDER_CALL_STARTED' WHERE work_id=?",(work_id,))
  try:
   if kind=='ACQUIRE_ENTRY':
    entry=self.entry_transport({'operation_id':op,'mint':mint,'max_signature_pages':1,'max_transaction_lookups':8})
    if not entry.get('qualified') or entry.get('event_type') not in {'SWAP','BUY','SELL'}: raise ValueError('NO_QUALIFYING_ECONOMIC_TRADE')
    compact={k:entry[k] for k in ('signature','slot','timestamp','event_type','mc_usd','provenance')}
    result={'entry':compact,'provider_calls':int(entry.get('provider_calls',0))}
   else:
    response=self.window_transport({'operation_id':op,'mint':mint,'start':resume,'end':due})
    prior=conn.execute('SELECT last_successful_coverage_boundary FROM universal_monitor_checkpoint_state WHERE operation_id=? AND mint=?',(op,mint)).fetchone()
    reduced=reduce_checkpoint(WindowState(int(prior[0] if prior and prior[0] is not None else resume or 0)),checkpoint_boundary=int(due),response_coverage_start=int(response['coverage_start']),response_coverage_end=int(response['coverage_end']),observations=response['observations'])
    if reduced['status']!='QUALIFIED': return {'state':'FAILED_CLOSED','reason':reduced['status'],'provider_calls':1}
    terminal=bool(self.terminal_policy(op,reduced['state']))
    result={'window':{'latest_mc':reduced['state'].current_mc_usd,'running_peak':reduced['state'].running_peak_mc_usd,'boundary':reduced['state'].last_successful_coverage_boundary},'terminal':terminal,'provider_calls':1}
   with conn:
    conn.execute("UPDATE universal_monitor_global_work SET state=? WHERE work_id=?",('TERMINAL' if result.get('terminal') else 'FACT_COMMITTED',work_id))
    if result.get('window'): conn.execute('UPDATE universal_monitor_checkpoint_state SET last_successful_coverage_boundary=?,proven_coverage_boundary=?,updated_at=strftime(\'%s\',\'now\') WHERE operation_id=? AND mint=?',(result['window']['boundary'],result['window']['boundary'],op,mint))
   return {'state':'TERMINAL' if result.get('terminal') else 'FACT_COMMITTED',**result}
  except Exception as exc:
   with conn: conn.execute("UPDATE universal_monitor_global_work SET state='AMBIGUOUS_PROVIDER_CALL' WHERE work_id=?",(work_id,))
   return {'state':'AMBIGUOUS_PROVIDER_CALL','reason':str(exc),'provider_calls':0}
