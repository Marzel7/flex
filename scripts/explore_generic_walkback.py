#!/usr/bin/env python3
import hashlib,json,time,os
from pathlib import Path
from src.core import walkback_worker as w
from src.discovery.generic_wallet_walkback import find_funding_parent
LIMIT=5000; DEADLINE=1800; started=time.monotonic(); calls=0
OUT=Path('/tmp/p3r_exploratory_walkback.json')
def durable(d):
 with OUT.open('w') as f: json.dump(d,f,sort_keys=True);f.flush();os.fsync(f.fileno())
orig=w._rpc
def bounded(method,params):
 global calls
 state['calls']=calls;durable(state)
 if calls>=LIMIT: raise RuntimeError('HOLD_P3R_EXPLORATORY_WALKBACK_REQUEST_BOUND')
 if time.monotonic()-started>=DEADLINE: raise TimeoutError('HOLD_P3R_EXPLORATORY_WALKBACK_WALLTIME_BOUND')
 calls+=1; return orig(method,params)
def main():
 rows=[json.loads(x) for x in Path('docs/audits/ops_discovery_p3r_s2b_watchtower_like_single_creator_manifest.jsonl').read_text().splitlines()]
 seen=[]
 for r in rows:
  if r['create_creator'] not in seen: seen.append(r['create_creator'])
 sample=seen[:100]; out=[]; state={'sample_sha256':hashlib.sha256(json.dumps(sample,separators=(',',':')).encode()).hexdigest(),'calls':0,'started_at':time.time(),'status':'STARTED','rows':out};durable(state); w._rpc=bounded
 try:
  for creator in sample:
   try:
    a=find_funding_parent(creator); row={'creator':creator,'parent1':a.parent_wallet,'sig1':a.signature,'state1':a.state}
    if a.parent_wallet:
     b=find_funding_parent(a.parent_wallet);row.update(parent2=b.parent_wallet,sig2=b.signature,state2=b.state)
    out.append(row);state['calls']=calls;durable(state)
   except Exception as e: out.append({'creator':creator,'state1':type(e).__name__});state['calls']=calls;durable(state)
 finally: w._rpc=orig
 state.update(calls=calls,elapsed=time.monotonic()-started,status='COMPLETE');durable(state)
if __name__=='__main__': main()
