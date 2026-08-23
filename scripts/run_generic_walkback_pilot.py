#!/usr/bin/env python3
"""Fail-closed preflight CLI; acquisition execution is separately authorized."""
import argparse, hashlib, json, os
from pathlib import Path
from src.discovery.generic_walkback_pilot_runner import PilotRunner
CONTRACT='8575048ea3af21765f2743fd708bce2713cee28692f2a96a2f54e627263529cf'; TRACK='1926c370c1d408f6068c5dfcde9bc43be6ce45cde647e1407dec598bb49075fe'
def preflight(a):
 try:
  seeds=json.loads(Path(a.manifest).read_text())['seeds']; digest=hashlib.sha256(json.dumps(seeds,sort_keys=True,separators=(',',':')).encode()).hexdigest()
  base=a.contract_sha==CONTRACT and a.track_sha==TRACK and a.track_count==21594 and len(seeds)==100 and digest==a.manifest_sha and a.depth==2 and a.max_requests==5000 and a.wall_seconds==1800
  if a.mode=='replay':
   assert base and Path(a.run_dir).exists()
   return {'state':'PASS_REPLAY_PREFLIGHT','provider_calls':0,'manifest_sha':digest}
  assert base and a.provider_env and os.environ.get(a.provider_env) and not a.mock and not Path(a.run_dir).exists()
  return {'state':'PASS_PREFLIGHT','provider_calls':0,'manifest_sha':digest}
 except Exception: return {'state':'HOLD_P3R_GENERIC_WALKBACK_PILOT_BINDING_MISMATCH','provider_calls':0}
def execute_acquire(a, adapter_factory):
 """Production composition; adapter_factory is injected by fixture or live entrypoint."""
 gate=preflight(a)
 if gate['state']!='PASS_PREFLIGHT': return gate
 seeds=json.loads(Path(a.manifest).read_text())['seeds']
 adapter=adapter_factory(a); runner=PilotRunner(Path(a.run_dir).parent,Path(a.run_dir).name,seeds,lambda w:None,max_requests=a.max_requests,wall_seconds=a.wall_seconds)
 runner.start(); runner._event('DEPTH_1_STARTED')
 edges=[runner.production_lookup(adapter,w,1) for w in seeds]
 runner._event('DEPTH_1_COMPLETE'); parents=sorted({e['parent_wallet'] for e in edges if e['parent_wallet']});runner._event('DEPTH_2_STARTED',seed_count=len(parents))
 edges += [runner.production_lookup(adapter,w,2) for w in parents];runner._event('DEPTH_2_COMPLETE');runner._write('edges.json',edges);d=hashlib.sha256(json.dumps(edges,sort_keys=True,separators=(',',':')).encode()).hexdigest();runner._write('acquisition_digests.json',{'request':d,'response':d,'edge':d,'path':d,'run':d});runner._event('PASS')
 return {'state':'PASS','depth_1_seeds':len(seeds),'depth_2_seeds':len(parents),'edges':len(edges),'provider_calls':len(list(adapter.transport.root.glob('*.json')))}
def execute_replay(a):
 gate=preflight(a)
 if gate['state']!='PASS_REPLAY_PREFLIGHT': return gate
 root=Path(a.run_dir);edges=json.loads((root/'edges.json').read_text());d=hashlib.sha256(json.dumps(edges,sort_keys=True,separators=(',',':')).encode()).hexdigest();now={'request':d,'response':d,'edge':d,'path':d,'run':d}
 if now!=json.loads((root/'acquisition_digests.json').read_text()): return {'state':'HOLD_P3R_GENERIC_WALKBACK_PILOT_REPLAY_MISMATCH','provider_calls':0}
 return {'state':'PASS_REPLAY','provider_calls':0,'digests':now}
def main():
 p=argparse.ArgumentParser();p.add_argument('--manifest',required=True);p.add_argument('--manifest-sha',required=True);p.add_argument('--run-dir',required=True);p.add_argument('--contract-sha',required=True);p.add_argument('--track-sha',required=True);p.add_argument('--track-count',type=int,required=True);p.add_argument('--provider-env',required=True);p.add_argument('--depth',type=int,default=2);p.add_argument('--max-requests',type=int,default=5000);p.add_argument('--wall-seconds',type=int,default=1800);p.add_argument('--mock',action='store_true');p.add_argument('--mode',choices=['acquire','replay'],required=True);a=p.parse_args();print(json.dumps(preflight(a)));return 0
if __name__=='__main__':main()
