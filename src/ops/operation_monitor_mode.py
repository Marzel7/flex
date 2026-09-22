"""Bounded shadow monitor substrate; provider transport is deliberately injected."""
from __future__ import annotations
import hashlib,json,sqlite3,time
from pathlib import Path
from src.ops.operation_price_research_orchestrator import adapter_for
MODES={'OFF','MONITOR','TRADING'}; DEFAULT_MODE='OFF'; COHORT='PROSPECTIVE_MONITOR_COHORT'; CEILING_TOKEN=8192; CEILING_GLOBAL=10_000_000
TERMINAL={'PRICE_MONITOR_COMPLETE_COLLAPSED','PRICE_MONITOR_COMPLETE_TERMINAL','INSUFFICIENT_EVIDENCE','FAILED_TERMINAL'}
def _id(x):return hashlib.sha256(json.dumps(x,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def profile(operation_id):
 if operation_id=='watchtower':return {'calls_per_token':8,'cadence_seconds':[60,300,900,3600,21600],'terminal':'>=85% drawdown from running peak','terminal_state':'PRICE_MONITOR_COMPLETE_COLLAPSED'}
 if operation_id=='byzantine':return {'calls_per_token':12,'cadence_seconds':[300,1800,7200,21600,86400],'terminal':'MONITOR_TERMINAL_RULE_UNQUALIFIED','terminal_state':'PRICE_MONITOR_COMPLETE_TERMINAL'}
 return {'calls_per_token':0,'cadence_seconds':[],'terminal':'ENTRY_METHOD_UNQUALIFIED','terminal_state':'INSUFFICIENT_EVIDENCE'}
class MonitorStore:
 def __init__(self,path):
  self.path=Path(path); c=sqlite3.connect(self.path);c.execute('create table if not exists monitor_jobs (id text primary key, operation_id text,mint text,state text,payload text,attempts int default 0,updated real)');c.commit();c.close()
 def enqueue_assignment(self,operation_id,mint,assignment_timestamp,provenance):
  adapter=adapter_for(operation_id); ident=_id({'operation_id':operation_id,'mint':mint,'assignment_timestamp':assignment_timestamp,'cohort':COHORT})
  p={'operation_id':operation_id,'mint':mint,'assignment_timestamp':assignment_timestamp,'assignment_provenance':provenance,'cohort':COHORT,'entry_method':adapter['method'],'monitor_profile':profile(operation_id),'state':'WAITING_FOR_ENTRY_REFERENCE','provider_calls':0,'raw_bytes':0}
  c=sqlite3.connect(self.path);c.execute('insert or ignore into monitor_jobs values(?,?,?,?,?,?,?)',(ident,operation_id,mint,'QUEUED',json.dumps(p),0,time.time()));c.commit();c.close();return ident
 def claim_one(self):
  c=sqlite3.connect(self.path);r=c.execute("select id,payload from monitor_jobs where state in ('QUEUED','FAILED_RETRYABLE') order by updated limit 1").fetchone()
  if not r:c.close();return None
  c.execute("update monitor_jobs set state='MONITORING_ACTIVE',updated=? where id=?",(time.time(),r[0]));c.commit();c.close();return r[0],json.loads(r[1])
 def finish(self,ident,payload,state):
  c=sqlite3.connect(self.path);c.execute('update monitor_jobs set state=?,payload=?,updated=? where id=?',(state,json.dumps(payload),time.time(),ident));c.commit();c.close()
 def rows(self):
  c=sqlite3.connect(self.path);r=c.execute('select id,state,payload from monitor_jobs').fetchall();c.close();return r
def reduce_observation(payload,mc,timestamp):
 if payload['entry_method']=='ENTRY_METHOD_UNQUALIFIED':payload['state']='INSUFFICIENT_EVIDENCE';return payload
 entry=payload.get('entry_mc_usd') or mc; payload['entry_mc_usd']=entry; payload['latest_mc_usd']=mc;payload['latest_mc_timestamp']=timestamp;peak=max(payload.get('running_max_mc_usd',0),mc);payload['running_max_mc_usd']=peak;payload['running_max_timestamp']=timestamp if peak==mc else payload.get('running_max_timestamp');payload['current_multiple']=mc/entry;payload['running_max_multiple']=peak/entry;payload['drawdown_from_running_max_percent']=(peak-mc)*100/peak;payload['reached_2x']=peak>=entry*2;payload['reached_5x']=peak>=entry*5;payload['reached_10x']=peak>=entry*10;payload['state']='MONITORING_ACTIVE';payload['next_observation_at']=timestamp+(payload['monitor_profile']['cadence_seconds'][0] if payload['monitor_profile']['cadence_seconds'] else 0)
 if payload['operation_id']=='watchtower' and peak and mc<=peak*.15:payload['state']='PRICE_MONITOR_COMPLETE_COLLAPSED'
 return payload
