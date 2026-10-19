"""Small durable controller for a bounded temporary feature activation."""
from __future__ import annotations
import json, os, time, uuid
from pathlib import Path

TERMINAL={"COMPLETED","ABORTED","RECOVERY_FORCED_OFF"}
class Controller:
 def __init__(self,path,adapter,now=time.time): self.path=Path(path);self.adapter=adapter;self.now=now
 def _load(self): return json.loads(self.path.read_text()) if self.path.exists() else None
 def _save(self,s):
  self.path.parent.mkdir(parents=True,exist_ok=True);tmp=self.path.with_suffix('.tmp');tmp.write_text(json.dumps(s,sort_keys=True));os.replace(tmp,self.path)
 def prepare(self,feature,runtime,deadline,cap,ceiling=None):
  if self._load() and self._load()['state'] not in TERMINAL: raise RuntimeError('active observation exists')
  spec=getattr(self.adapter,'watchdog_spec',None)
  if spec and self.adapter.read_state()=='ON':
   self.adapter.set_off()
   if not self.adapter.verify_off(): raise RuntimeError('UNMANAGED_FEATURE_ON_OFF_VERIFICATION_FAILED')
   raise RuntimeError('UNMANAGED_FEATURE_ON_DETECTED')
  s={'id':str(uuid.uuid4()),'version':'bounded-feature-observation.v1','feature':feature,'runtime':runtime,'original_state':self.adapter.read_state(),'requested_state':'ON','final_state':'OFF','created_at':self.now(),'activated_at':None,'deadline':deadline,'cap':cap,'ceiling':ceiling,'count':0,'resource':0,'state':'PREPARED','owner':str(uuid.uuid4()),'heartbeat':self.now(),'stop_reason':None,'finalized_at':None,'verified_off':False,'watchdog':{'version':'independent-deadline-off-watchdog.v1','state_store':str(self.path.resolve()),'adapter':spec} if spec else None};self._save(s);return s
 def activate(self):
  s=self._load()
  if not s or s['state']!='PREPARED': raise RuntimeError('not prepared')
  self.adapter.set_on();s['activated_at']=self.now();s['state']='ACTIVE';s['heartbeat']=self.now();self._save(s);return s
 def tick(self,count=None,resource=None,abort=False):
  s=self._load();
  if not s:return None
  if s['state'] in TERMINAL:return s
  if count is not None:s['count']=count
  if resource is not None:s['resource']=resource
  reason='ABORT' if abort else ('DEADLINE' if self.now()>=s['deadline'] else ('CAP' if s['count']>=s['cap'] else ('CEILING' if s['ceiling'] is not None and s['resource']>=s['ceiling'] else None)))
  if reason:return self.stop(reason,aborted=reason in {'ABORT','CEILING'})
  s['heartbeat']=self.now();self._save(s);return s
 def stop(self,reason,aborted=False,recovery=False):
  s=self._load();s['state']='STOPPING';s['stop_reason']=reason;self._save(s);self.adapter.set_off()
  if not self.adapter.verify_off():raise RuntimeError('final OFF verification failed')
  s['state']='RECOVERY_FORCED_OFF' if recovery else ('ABORTED' if aborted else 'COMPLETED');s['verified_off']=True;s['finalized_at']=self.now();self._save(s);return s
 def recover(self):
  s=self._load()
  if s and s['state'] not in TERMINAL and self.now()>=s['deadline']:return self.stop('RECOVERY_DEADLINE',recovery=True)
  return s
