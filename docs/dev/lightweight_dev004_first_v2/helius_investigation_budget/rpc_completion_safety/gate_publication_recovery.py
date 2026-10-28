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

# One explicit recovery bundle, not a new ledger or recurring archive.
_ORPHAN_CAP=16777216

def _orphan_bytes(path):
 import stat
 p=pathlib.Path(path)
 fd=os.open(p,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
 try:
  s=os.fstat(fd)
  if not stat.S_ISREG(s.st_mode) or s.st_nlink!=1 or s.st_size>_ORPHAN_CAP:raise RuntimeError('ORPHAN_EVIDENCE_FILE_AUTHORITY')
  data=os.read(fd,_ORPHAN_CAP+1)
  if len(data)>_ORPHAN_CAP:raise RuntimeError('ORPHAN_EVIDENCE_CAPACITY')
  return data
 finally:os.close(fd)

def _orphan_preserve(path,data):
 p=pathlib.Path(path)
 if len(data)>_ORPHAN_CAP:raise RuntimeError('ORPHAN_EVIDENCE_CAPACITY')
 if p.exists():
  if _orphan_bytes(p)!=data:raise RuntimeError('ORPHAN_EVIDENCE_CONFLICT')
 else:
  fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o400)
  try:
   done=0
   while done<len(data):
    n=os.write(fd,data[done:])
    if n<=0:raise RuntimeError('ORPHAN_EVIDENCE_SHORT_WRITE')
    done+=n
   os.fsync(fd)
  finally:os.close(fd)
 fd=os.open(p,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
 try:os.fsync(fd)
 finally:os.close(fd)
 directory=os.open(p.parent,os.O_RDONLY)
 try:os.fsync(directory)
 finally:os.close(directory)
 if _orphan_bytes(p)!=data:raise RuntimeError('ORPHAN_EVIDENCE_READBACK')

def _orphan_classify(module,gate,before_raw,stage_raw,ticket,pid):
 before=gate._decode(before_raw);target=gate._decode(stage_raw)
 if before.get('rpc_budget',{}).get('version')!=2:raise RuntimeError('ORPHAN_BUDGET_VERSION')
 b=before['rpc_budget']
 if set(b['inflight'])!={ticket}:raise RuntimeError('ORPHAN_EXACT_RESERVATION_REQUIRED')
 entry=b['inflight'][ticket];request=b['keys'][entry['key']]
 if request['status']!='INFLIGHT' or request['pid']!=pid or entry.get('probe'):raise RuntimeError('ORPHAN_OWNER_OR_PROBE_MISMATCH')
 # Exact observed finish_http(200) delta only. Even its success is NOT adopted.
 expected=copy.deepcopy(before);expected['rpc_budget']['inflight'].pop(ticket);expected['rpc_budget']['keys'][entry['key']]['status']='HTTP_COMPLETED';expected['outcomes']+=1;expected['statuses']['200']=expected['statuses'].get('200',0)+1
 if target!=expected:raise RuntimeError('ORPHAN_STAGE_NOT_EXACT_UNCOMMITTED_COMPLETION')
 return before,entry,request

def _orphan_reconciled(before,after,ticket,pid):
 entry=before['rpc_budget']['inflight'][ticket];original=before['rpc_budget']['keys'][entry['key']];b=after['rpc_budget']
 if b['wall']<before['rpc_budget']['wall'] or b['mono']<before['rpc_budget']['mono']:return False
 expected=copy.deepcopy(before);expected['rpc_budget']['wall']=b['wall'];expected['rpc_budget']['mono']=b['mono'];expected['rpc_budget']['inflight'].pop(ticket);key=expected['rpc_budget']['keys'][entry['key']];key['status']='OWNER_EXIT_UNCERTAIN';key['owner_exit']={'ticket':ticket,'root':entry['root'],'pid':pid,'original_at':original['at'],'retired_at':b['wall'],'reason':'PROCESS_GONE_OUTCOME_UNKNOWN'}
 return after==expected

def recover_orphan_stage(module,path,generation,*,expected_sha256,expected_stage_sha256,ticket,expected_pid,retired_pids,evidence_dir,crash=None):
 """Operator-only READY+orphan recovery using the existing no-refund owner API.

 No provider call, gate state transition, marker reset or staged outcome adoption.
 All actors must be independently stopped before invocation. Unsupported/new
 publication states remain fail-closed, with byte-exact evidence retained.
 """
 p=pathlib.Path(path);directory=pathlib.Path(evidence_dir)
 if not p.is_absolute() or str(p.resolve())!=str(p) or not directory.is_absolute() or str(directory.resolve())!=str(directory):raise RuntimeError('ORPHAN_PHYSICAL_PATH_REQUIRED')
 if type(expected_pid) is not int or expected_pid<=1 or expected_pid not in retired_pids or not retired_pids or len(set(retired_pids))!=len(retired_pids):raise RuntimeError('ORPHAN_QUIESCENCE_REQUIRED')
 for pid in retired_pids:
  if type(pid) is not int or pid<=1:raise RuntimeError('ORPHAN_OWNER_IDENTITY')
  try:os.kill(pid,0)
  except ProcessLookupError:pass
  except OSError:raise RuntimeError('ORPHAN_OWNER_UNVERIFIED') from None
  else:raise RuntimeError('ORPHAN_OWNER_STILL_PRESENT')
 gate=module.Gate(p,generation);stage=p.with_name(p.name+'.stage')
 if not directory.exists():directory.mkdir(mode=0o700)
 if not directory.is_dir() or directory.is_symlink():raise RuntimeError('ORPHAN_EVIDENCE_DIRECTORY')
 # Exactly five named files, at most two bounded bodies; never auto-prune.
 allowed={'ledger.before.json','stage.before.json','publication.before.marker','intent.json','result.json'}
 if set(x.name for x in directory.iterdir())-allowed:raise RuntimeError('ORPHAN_EVIDENCE_DIRECTORY_CONFLICT')
 with gate.locked():
  current_raw=gate._raw();marker=gate._marker()
  if marker!=b'READY_V1 '+hashlib.sha256(current_raw).hexdigest().encode()+b'\n':raise RuntimeError('ORPHAN_EXACT_READY_AUTHORITY_REQUIRED')
  before_path=directory/'ledger.before.json';stage_path=directory/'stage.before.json'
  before_raw=_orphan_bytes(before_path) if before_path.exists() else current_raw
  stage_raw=_orphan_bytes(stage_path) if stage_path.exists() else _orphan_bytes(stage)
  if hashlib.sha256(before_raw).hexdigest()!=expected_sha256 or hashlib.sha256(stage_raw).hexdigest()!=expected_stage_sha256:raise RuntimeError('ORPHAN_STATE_CAS_FAILED')
  before,entry,request=_orphan_classify(module,gate,before_raw,stage_raw,ticket,expected_pid);current=gate.read();already=_orphan_reconciled(before,current,ticket,expected_pid)
  if not already and current_raw!=before_raw:raise RuntimeError('ORPHAN_AUTHORITATIVE_STATE_CHANGED')
  if stage.exists() and _orphan_bytes(stage)!=stage_raw:raise RuntimeError('ORPHAN_ACTIVE_STAGE_CHANGED')
  if already and stage.exists():raise RuntimeError('ORPHAN_UNEXPECTED_STAGE_AFTER_RECONCILIATION')
  gate._storage()
  # Reestablish independent durability of the unchanged authoritative ledger.
  fd=os.open(p,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
  try:os.fsync(fd)
  finally:os.close(fd)
  gate._sync_directory()
  _orphan_preserve(before_path,before_raw);_orphan_preserve(stage_path,stage_raw);_orphan_preserve(directory/'publication.before.marker',b'READY_V1 '+expected_sha256.encode()+b'\n')
  intent={'version':1,'path':str(p),'generation':generation,'before_sha256':expected_sha256,'stage_sha256':expected_stage_sha256,'ticket':ticket,'owner_pid':expected_pid,'disposition':'UNCERTAIN_NO_REFUND_NO_REPLAY'}
  _orphan_preserve(directory/'intent.json',(json.dumps(intent,sort_keys=True)+'\n').encode())
  if crash:crash('evidence_durable')
  if stage.exists():
   # Only after byte-exact immutable destination readback, under owner lock.
   if gate._raw()!=before_raw or gate._marker()!=marker or _orphan_bytes(stage)!=stage_raw:raise RuntimeError('ORPHAN_PRE_QUARANTINE_CAS_FAILED')
   os.unlink(stage);gate._sync_directory()
  if crash:crash('stage_quarantined')
 # The accepted API acquires its own owner lock and proves PID absence again.
 # Its digest CAS rejects any writer racing this quiescent operator boundary.
 if not already:gate.reconcile_terminated_http(ticket,expected_pid=expected_pid,expected_sha256=expected_sha256)
 if crash:crash('owner_reconciled')
 with gate.locked():
  after=gate.read()
  if stage.exists() or not _orphan_reconciled(before,after,ticket,expected_pid):raise RuntimeError('ORPHAN_RECONCILIATION_READBACK')
  receipt={'version':1,'status':'RECOVERED_UNCERTAIN_NO_REFUND','before_sha256':expected_sha256,'stage_sha256':expected_stage_sha256,'after_sha256':hashlib.sha256(gate._raw()).hexdigest(),'generation':generation,'owner_pid':expected_pid,'original_request_credits':request['credits'],'spending_and_ownership_preserved':after['rpc_budget']['scopes']==before['rpc_budget']['scopes'] and after['rpc_budget']['owners']==before['rpc_budget']['owners'],'outcome_adopted':False,'state_unchanged':after['state']==before['state'],'request_replay_authorized':False}
  _orphan_preserve(directory/'result.json',(json.dumps(receipt,sort_keys=True)+'\n').encode())
 return dict(receipt,idempotent=already)
