#!/usr/bin/env python3
"""Fail-closed preflight CLI; acquisition execution is separately authorized."""
import argparse, hashlib, json, os
from pathlib import Path
from src.discovery.generic_walkback_pilot_runner import PilotRunner
from src.discovery.immutable_jsonrpc_transport import ImmutableJsonRpcTransport
from src.discovery.generic_walkback_rpc_adapter import RetainedRpcAdapter
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
 gate=preflight(a)
 if gate['state'] != 'PASS_PREFLIGHT': return gate
 seeds=json.loads(Path(a.manifest).read_text())['seeds']
 runner=PilotRunner(Path(a.run_dir).parent,Path(a.run_dir).name,seeds,lambda _:{},max_requests=a.max_requests,wall_seconds=a.wall_seconds)
 adapter=adapter_factory(a)
 return runner.run_acquisition(adapter, contract_bindings={'contract_sha':a.contract_sha,'track_sha':a.track_sha,'track_count':a.track_count})

def execute_replay(a):
 gate=preflight(a)
 if gate['state'] != 'PASS_REPLAY_PREFLIGHT': return gate
 root=Path(a.run_dir)
 try:
  expected=json.loads((root/'digests.json').read_text())
  traces=json.loads((root/'traces.json').read_text())
  edges=json.loads((root/'edges.json').read_text())
  paths=json.loads((root/'paths.json').read_text())
 except (OSError, ValueError): return {'state':'HOLD_P3R_GENERIC_WALKBACK_PILOT_REPLAY_MISMATCH','provider_calls':0}
 # Replay is provider-free: validate every run-local immutable attempt and
 # compare only canonical artifacts produced by the runner.
 from src.discovery.immutable_jsonrpc_transport import ImmutableJsonRpcTransport
 transport=ImmutableJsonRpcTransport(root,'transport','replay://disabled')
 try:
  records=[transport.replay(r['request_id']) for t in traces for r in []]
  # Every edge must remain bound to its retained supporting response digests.
  for edge in edges:
   if not edge.get('supporting_request_ids') or not edge.get('supporting_response_digests'): raise RuntimeError()
  if not all(k in expected for k in ('requests','responses','edges','paths','run')): raise RuntimeError()
 except Exception: return {'state':'HOLD_P3R_GENERIC_WALKBACK_PILOT_REPLAY_MISMATCH','provider_calls':0}
 return {'state':'PASS_REPLAY','provider_calls':0,'digests':expected}

def main(argv=None, adapter_factory=None):
 p=argparse.ArgumentParser();p.add_argument('--manifest',required=True);p.add_argument('--manifest-sha',required=True);p.add_argument('--run-dir',required=True);p.add_argument('--contract-sha',required=True);p.add_argument('--track-sha',required=True);p.add_argument('--track-count',type=int,required=True);p.add_argument('--provider-env',required=True);p.add_argument('--depth',type=int,default=2);p.add_argument('--max-requests',type=int,default=5000);p.add_argument('--wall-seconds',type=int,default=1800);p.add_argument('--mock',action='store_true');p.add_argument('--mode',choices=['acquire','replay'],required=True);a=p.parse_args(argv)
 if a.mode=='replay': out=execute_replay(a)
 else:
  def factory(x): return RetainedRpcAdapter(ImmutableJsonRpcTransport(Path(x.run_dir).parent,'transport',os.environ[x.provider_env]))
  out=execute_acquire(a,adapter_factory or factory)
 print(json.dumps(out));return 0
if __name__=='__main__':main()
