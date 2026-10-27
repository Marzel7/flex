"""Explicit offline conservative recovery; never called by startup or dispatch."""
import os,json,pathlib,hashlib,tempfile,importlib.util,copy

def recover(module, path, generation, expected_sha256, snapshot, expected_snapshot_sha256, retired_pids):
 p=pathlib.Path(path);snap=pathlib.Path(snapshot)
 if not retired_pids or len(set(retired_pids))!=len(retired_pids):raise RuntimeError('QUIESCENCE_REQUIRED')
 for pid in retired_pids:
  if type(pid) is not int or pid<=1:raise RuntimeError('OWNER_IDENTITY')
  try:os.kill(pid,0)
  except ProcessLookupError:pass
  else:raise RuntimeError('OWNER_STILL_ACTIVE')
 if p.is_symlink() or snap.is_symlink():raise RuntimeError('PATH_AUTHORITY')
 gate=module.Gate(p,generation)
 with gate.locked():
  if pathlib.Path(str(p)+'.lock').read_bytes()!=b'PENDING_V1\n':raise RuntimeError('EXACT_PENDING_MARKER_REQUIRED')
  if p.with_name(p.name+'.stage').exists():raise RuntimeError('UNRESOLVED_STAGED_PUBLICATION')
  raw=p.read_bytes();prior=snap.read_bytes()
  if len(raw)>16777216 or len(prior)>16777216:raise RuntimeError('STATE_CAPACITY')
  if hashlib.sha256(raw).hexdigest()!=expected_sha256 or hashlib.sha256(prior).hexdigest()!=expected_snapshot_sha256:raise RuntimeError('STATE_CAS_FAILED')
  # Validate with the existing authoritative reader against a disposable copy;
  # never manufacture a READY marker on the production state being diagnosed.
  with tempfile.TemporaryDirectory(prefix='helius-gate-validation-') as td:
   test=pathlib.Path(td).resolve()/'gate.json';test.write_bytes(raw);pathlib.Path(str(test)+'.lock').write_bytes(b'READY_V1 '+hashlib.sha256(raw).hexdigest().encode()+b'\n')
   state=module.Gate(test,generation).read()
  if json.loads(prior)!=state:raise RuntimeError('INDEPENDENT_SNAPSHOT_MISMATCH')
  if state.get('rpc_budget',{}).get('version')!=2:raise RuntimeError('BUDGET_VERSION')
  if state['rpc_budget']['inflight'] or any(v['status']=='INFLIGHT' for v in state['rpc_budget']['keys'].values()):raise RuntimeError('UNRESOLVED_RESERVATION')
  old=copy.deepcopy(state);state['state']='PAYMENT_DENIED'
  # Existing owner's durable fsync/rename protocol; explicit conservative
  # operator transaction, not proof that the interrupted transaction failed.
  gate.save(state);readback=gate.read()
  expected=copy.deepcopy(old);expected['state']='PAYMENT_DENIED'
  if readback!=expected:raise RuntimeError('RECOVERY_READBACK_MISMATCH')
  return {'before_sha256':expected_sha256,'after_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'accounting_preserved':readback['rpc_budget']==old['rpc_budget'],'state':'PAYMENT_DENIED','probe_authorized':False,'interrupted_operation_outcome':'UNPROVEN_NO_OUTCOME_INFERRED'}
