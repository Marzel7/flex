"""Provider-free bounded launcher for monitor membership outbox delivery."""
from __future__ import annotations
import json, os, signal, sqlite3, time
from pathlib import Path
from src.ops.monitor_live_admission import consume_once
from src.ops.operation_monitor_worker import MonitorQueue

_STOP=False
def _bounded_iteration_limit(env):
 raw=str(env.get('MONITOR_BRIDGE_MAX_ITERATIONS','')).strip()
 if not raw:return None
 try: value=int(raw)
 except ValueError as exc: raise RuntimeError('INVALID_MONITOR_BRIDGE_MAX_ITERATIONS') from exc
 if value<1: raise RuntimeError('INVALID_MONITOR_BRIDGE_MAX_ITERATIONS')
 return value
def config(env=None):
 v=dict(os.environ if env is None else env); source=v.get('MONITOR_BRIDGE_SOURCE_DB'); monitor=v.get('MONITOR_BRIDGE_MONITOR_DB'); queue=v.get('MONITOR_BRIDGE_QUEUE_PATH')
 if not source or not monitor or not queue or Path(source).resolve()==Path(monitor).resolve(): raise RuntimeError('MONITOR_BRIDGE_INVALID_CONFIG')
 return {'source_db':source,'monitor_db':monitor,'queue_path':queue,'cadence':max(1,min(60,int(v.get('MONITOR_BRIDGE_CADENCE_SECONDS','5')))),'health_path':v.get('MONITOR_BRIDGE_HEALTH_PATH',''),'selection_path':v.get('MONITOR_BRIDGE_SELECTION_PATH',''),'max_iterations':_bounded_iteration_limit(v)}
def run_once(cfg,*,now=None):
 stamp=int(time.time() if now is None else now); selection=Path(cfg['selection_path']) if cfg.get('selection_path') else None; q=MonitorQueue(Path(cfg['queue_path']),enabled=True,soak_selection_path=selection)
 def selected(row):
  _event_id,operation_id,mint,membership_id,committed_at=row
  return q.soak_allows({'operation_id':operation_id,'mint':mint,'assignment':{'event_id':membership_id,'assigned_at':committed_at}})
 result=consume_once(cfg['source_db'],q,now=stamp,eligible=selected if selection else None)
 health={'version':'monitor-bridge-health.v1','last_iteration_at':stamp,'last_claim_at':stamp if result else None,'last_successful_handoff_at':stamp if result and result.get('state')=='DELIVERED' else None,'last_ack_at':stamp if result and result.get('state')=='DELIVERED' else None,'result':result.get('state') if result else 'EMPTY'}
 if cfg['health_path']: Path(cfg['health_path']).write_text(json.dumps(health,separators=(',',':'))+'\n')
 return result
def main():
 global _STOP
 cfg=config();signal.signal(signal.SIGTERM,lambda *_:globals().update(_STOP=True));signal.signal(signal.SIGINT,lambda *_:globals().update(_STOP=True));iterations=0
 while not _STOP:
  run_once(cfg);iterations+=1
  if cfg['max_iterations'] is not None and iterations>=cfg['max_iterations']:return
  time.sleep(cfg['cadence'])
if __name__=='__main__':main()
