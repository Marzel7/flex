"""Durable non-deadline stop monitor for one priority observation."""
from __future__ import annotations
import json, os, sqlite3, time
from pathlib import Path

PRECEDENCE={"SAFETY_ABORT":3,"PROVIDER_DISPATCH_CAP":2,"CANDIDATE_CAP":1}

def _save(path, value):
 path=Path(path); path.parent.mkdir(parents=True,exist_ok=True); temporary=path.with_suffix('.tmp'); temporary.write_text(json.dumps(value,sort_keys=True)); os.replace(temporary,path)

class DurablePriorityObservationMonitor:
 def __init__(self,state_path,db_path,adapter,now=time.time): self.path=Path(state_path);self.db_path=str(db_path);self.adapter=adapter;self.now=now
 def _load(self): return json.loads(self.path.read_text())
 def prepare(self,observation_id,operation_id,operation_version,reason_version,cap=10,rpc_ceiling=400):
  with sqlite3.connect(self.db_path) as c: baseline=c.execute("SELECT COALESCE(MAX(rowid),0) FROM operation_evidence_priority_requests").fetchone()[0]
  state={"version":"durable-priority-observation-monitor.v1","observation_id":observation_id,"operation_id":operation_id,"operation_version":operation_version,"reason_version":reason_version,"request_rowid_baseline":baseline,"cap":cap,"rpc_ceiling":rpc_ceiling,"prepared_at":self.now(),"admitted_request_ids":[],"stop_reason":None,"off_verified":False,"rpc_accounting":"wt_walkback_queue.rpc_used internal aggregate; provider credits not explicit"};_save(self.path,state);return state
 def _rows(self,s):
  with sqlite3.connect(self.db_path) as c:
   c.row_factory=sqlite3.Row
   return c.execute("SELECT p.request_id,p.entity_id,COALESCE(q.rpc_used,0) rpc_used FROM operation_evidence_priority_requests p LEFT JOIN wt_walkback_queue q ON q.mint=p.entity_id WHERE p.rowid>? AND p.operation_id=? AND p.operation_version=? AND p.reason_version=? ORDER BY p.rowid",(s['request_rowid_baseline'],s['operation_id'],s['operation_version'],s['reason_version'])).fetchall()
 def evaluate(self,signals=()):
  s=self._load(); rows=self._rows(s); ids=[]; seen=set()
  for r in rows:
   if r['request_id'] not in seen: seen.add(r['request_id']);ids.append(r['request_id'])
  s['admitted_request_ids']=ids;s['candidate_count']=len(ids);s['internal_rpc_units']=sum(int(r['rpc_used']) for r in rows)
  try:
   with sqlite3.connect(self.db_path) as c: s['provider_dispatch_count']=c.execute('SELECT COUNT(*) FROM observation_provider_dispatches WHERE observation_id=?',(s['observation_id'],)).fetchone()[0]
  except sqlite3.OperationalError: s['provider_dispatch_count']=0
  reasons=[]
  if any(signals): reasons.append('SAFETY_ABORT')
  if s['provider_dispatch_count']>=40: reasons.append('PROVIDER_DISPATCH_CAP')
  # rpc_used is deliberately not used to enforce external provider credits.
  if s['candidate_count']>=s['cap']: reasons.append('CANDIDATE_CAP')
  if reasons:
   reason=max(reasons,key=lambda x:PRECEDENCE[x]);self.adapter.set_off();s['stop_reason']=reason;s['off_verified']=self.adapter.verify_off();s['stopped_at']=self.now()
  _save(self.path,s);return s
