"""Deterministic, provider-free checkpoint and durable RPC authorization primitives."""
from dataclasses import dataclass, field
import json, os, time
from pathlib import Path

@dataclass
class RunLedger:
    max_network_calls: int
    entries: list[dict] = field(default_factory=list)
    def call(self, method, subject, fn):
        if self.network_calls >= self.max_network_calls: raise RuntimeError('RPC_BUDGET_EXHAUSTED')
        entry={'method':method,'subject':subject,'network_attempted':True,'success':False}; self.entries.append(entry)
        try: entry['value']=fn(); entry['success']=True; return entry['value']
        except Exception as exc: entry['error_code']=type(exc).__name__; entry['error_message']=str(exc); raise
    @property
    def network_calls(self): return len(self.entries)
    @property
    def successes(self): return sum(x['success'] for x in self.entries)
    @property
    def failures(self): return self.network_calls-self.successes

def remaining_funders(current, checkpoints):
    return sorted(set(current)-{f for f,x in checkpoints.items() if x.get('history_complete')})

def next_transactions(signatures, statuses):
    """Terminal failures are durable outcomes, never implicit retry candidates."""
    return [s for s in signatures if statuses.get(s,'NOT_DECODED') in ('NOT_DECODED','FAILED_RETRYABLE')]

class DurableAuthorizationLedger:
    """Atomic authorization envelope. A RESERVED attempt is deliberately spent."""
    def __init__(self, path, data): self.path=Path(path); self.data=data
    @staticmethod
    def _now(): return int(time.time())
    def _save(self):
        self.data['last_updated_at']=self._now(); tmp=self.path.with_name(self.path.name+'.tmp')
        tmp.write_text(json.dumps(self.data,sort_keys=True,indent=2)+'\n'); os.replace(tmp,self.path)
    @classmethod
    def new(cls,path,run_id,purpose,candidate_id,authorized_max_network_calls):
        path=Path(path)
        if path.exists(): raise RuntimeError('EXISTING_AUTHORIZATION_REQUIRES_RESUME')
        now=cls._now(); data={'schema_version':'durable_rpc_authorization.v1','run_id':run_id,'purpose':purpose,'candidate_id':candidate_id,
          'authorization_created_at':now,'authorized_max_network_calls':authorized_max_network_calls,'status':'ACTIVE','calls_attempted':0,'calls_succeeded':0,'calls_failed':0,'calls_remaining':authorized_max_network_calls,'started_at':now,'last_updated_at':now,'completed_at':None,'stop_reason':None,'attempts':[]}
        x=cls(path,data); x._save(); return x
    @classmethod
    def resume(cls,path,run_id,purpose,candidate_id):
        path=Path(path)
        if not path.exists(): raise RuntimeError('AUTHORIZATION_NOT_FOUND')
        x=cls(path,json.loads(path.read_text()))
        if x.data['run_id']!=run_id or x.data['purpose']!=purpose or x.data['candidate_id']!=candidate_id: raise RuntimeError('AUTHORIZATION_PURPOSE_MISMATCH')
        if x.data['status'] in ('COMPLETE','EXHAUSTED'): raise RuntimeError('AUTHORIZATION_NOT_RESUMABLE')
        x._recount(); x._save(); return x
    def _recount(self):
        attempts=self.data['attempts']; self.data['calls_attempted']=len(attempts); self.data['calls_succeeded']=sum(a['state']=='SUCCESS' for a in attempts); self.data['calls_failed']=sum(a['state']=='FAILED' for a in attempts)
        self.data['calls_remaining']=self.data['authorized_max_network_calls']-len(attempts)
        if self.data['calls_remaining']<=0: self.data['status']='EXHAUSTED'; self.data['stop_reason']='RPC_AUTHORIZATION_EXHAUSTED'
    @property
    def remaining(self): return self.data['calls_remaining']
    def reserve(self,provider,rpc_method,target,context=None,recovered=False):
        self._recount()
        if self.remaining<=0: self._save(); raise RuntimeError('RPC_AUTHORIZATION_EXHAUSTED')
        a={'run_id':self.data['run_id'],'sequence_number':len(self.data['attempts'])+1,'timestamp':self._now(),'provider':provider,'rpc_method':rpc_method,'target':target,'attempt_number':1,'state':'RECOVERED_HISTORICAL_ATTEMPT' if recovered else 'RESERVED','success':None,'error_code':None,'error_name':None,'error_message':None,'context':context or {}}
        self.data['attempts'].append(a); self._recount(); self._save(); return a['sequence_number']
    def finish(self,sequence,success,error=None):
        a=self.data['attempts'][sequence-1]
        if a['state'] not in ('RESERVED','RECOVERED_HISTORICAL_ATTEMPT'): raise RuntimeError('ATTEMPT_NOT_RESERVABLE')
        a['state']='SUCCESS' if success else 'FAILED'; a['success']=success
        if error: a.update(error_code=type(error).__name__,error_name=type(error).__name__,error_message=str(error))
        self._recount(); self._save()
    def call(self,provider,rpc_method,target,fn,context=None):
        n=self.reserve(provider,rpc_method,target,context)
        try: value=fn()
        except Exception as e: self.finish(n,False,e); raise
        self.finish(n,True); return value
    def recover(self, records):
        for r in records:
            n=self.reserve(r.get('provider','helius'),r['rpc_method'],r.get('target','UNKNOWN'),r.get('context',{}),recovered=True)
            # Known historical dispatches are counted, but their terminal state is not fabricated.
            self.data['attempts'][n-1]['recovery_note']='Recovered known dispatched attempt; terminal metadata unavailable.'; self._save()
        return self.remaining

def require_resolved_cache_provenance(cache, authorization):
    """Never treat a legacy cache counter as disposable when it exceeds the ledger."""
    legacy=(cache.get('calls') or {}).get('total')
    if isinstance(legacy,int) and legacy>authorization.data['calls_attempted']:
        raise RuntimeError('RPC_AUTHORIZATION_PROVENANCE_UNRESOLVED')
