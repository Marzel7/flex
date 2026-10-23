"""Provider-free tests of only the DEV004 budget owner; no price imports."""
from pathlib import Path
import concurrent.futures,hashlib,json,os,resource,subprocess,sys,tempfile
resource.setrlimit(resource.RLIMIT_FSIZE,(16000000,16000000))
MODULE=Path(sys.argv[1]).resolve();OUT=Path(tempfile.mkdtemp(prefix='dev004-budget-durability-',dir='/private/tmp'));results=[]
CHILD=r'''
import os,sys,json,resource,socket,stat,importlib.util
from pathlib import Path
resource.setrlimit(resource.RLIMIT_FSIZE,(16000000,16000000))
socket.socket.connect=lambda *a,**k:(_ for _ in ()).throw(AssertionError('NETWORK_FORBIDDEN'))
s=importlib.util.spec_from_file_location('budget',sys.argv[1]);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
a=json.loads(sys.argv[2]);root=Path(a['root']);fault=a.get('fault');events=[];roles={};dir_count=0;guard_count=0
raw_open,raw_write,raw_sync,raw_replace=os.open,os.write,os.fsync,os.replace

def opened(path,flags,*args,**kw):
 if fault=='read_unavailable' and str(path).endswith('/provider_budget.json'):raise PermissionError('synthetic state read failure')
 fd=raw_open(path,flags,*args,**kw)
 roles[fd]='guard' if str(path).endswith('/provider_budget.lock') else 'temp' if str(path).endswith('/.provider_budget.json.tmp') else 'directory' if stat.S_ISDIR(os.fstat(fd).st_mode) else 'ledger'
 return fd

def written(fd,data):
 role=roles.get(fd);events.append('write:'+str(role))
 if fault=='partial_write' and role=='temp':raw_write(fd,data[:max(1,len(data)//2)]);os._exit(71)
 if fault=='write_failure' and role=='temp':raise OSError('synthetic partial write failure')
 return raw_write(fd,data)

def synced(fd):
 global dir_count,guard_count
 role=roles.get(fd);events.append('sync:'+str(role))
 if role=='guard':
  guard_count+=1
  if fault=='guard_sync_failure' or (fault=='ready_sync_failure' and guard_count==2):raise OSError('synthetic guard sync failure')
 if role=='directory':
  dir_count+=1
  if fault=='directory_sync_failure' and dir_count==2:raise OSError('synthetic directory sync failure')
  if fault=='unsupported_directory_sync':raise OSError('synthetic unsupported directory sync')
 if role=='temp' and fault=='file_sync_failure':raise OSError('synthetic file sync failure')
 raw_sync(fd)
 if role=='directory' and fault=='crash_after_publication' and dir_count==2:os._exit(71)

def replaced(*args,**kw):
 events.append('replace')
 if fault=='before_rename':os._exit(71)
 if fault=='rename_failure':raise OSError('synthetic rename failure')
 r=raw_replace(*args,**kw)
 if fault=='after_rename':os._exit(71)
 return r
os.open,os.write,os.fsync,os.replace=opened,written,synced,replaced
try:
 result=m.DevProviderBudget(root).admit(a.get('mint','SYNTHETIC_A'),'SYNTHETIC_WINDOW',now=a.get('now',1000))
 events.append('permission_returned')
 if fault=='crash_before_dispatch':os._exit(71)
 events.append('synthetic_dispatch')
 print(json.dumps({'allowed':True,'result':result,'events':events}))
except m.BudgetDenied as e:print(json.dumps({'allowed':False,'error':str(e),'events':events}))
'''
def seed(name,calls=None):
 root=OUT/name;root.mkdir();state={'version':1,'updated_at':1000,'calls':calls or []}
 with (root/'provider_budget.json').open('w') as f:json.dump(state,f);f.flush();os.fsync(f.fileno())
 fd=os.open(root,os.O_RDONLY);os.fsync(fd);os.close(fd)
 return root

def run(root,**kw):
 p=subprocess.run([sys.executable,'-B','-c',CHILD,str(MODULE),json.dumps({'root':str(root),**kw})],env={'PATH':'/usr/bin:/bin','PYTHONDONTWRITEBYTECODE':'1'},capture_output=True,text=True,timeout=15)
 if p.returncode==71:return {'crash':71}
 assert p.returncode==0,p.stderr
 return json.loads(p.stdout)

def state(root):return json.loads((root/'provider_budget.json').read_text())
def note(name,**kw):results.append({'case':name,'status':'PASS',**kw})
def exhausted(name):
 root=seed(name)
 for i in range(4):assert run(root)['result']['token_calls']==i+1
 assert run(root)['error']=='TOKEN_PROVIDER_BUDGET_EXHAUSTED'
 return root

r=exhausted('normal');note('valid_state_fresh_interpreter_restart_and_token_ceiling')
old=(r/'provider_budget.json').read_bytes();assert run(r)['allowed'] is False;assert old==(r/'provider_budget.json').read_bytes();note('denial_preserves_authority')
for name,change in [('missing',lambda r:(r/'provider_budget.json').rename(r/'independent_authority_copy.json')),('malformed',lambda r:(r/'provider_budget.json').write_text('{incomplete')),('truncated',lambda r:(r/'provider_budget.json').write_bytes((r/'provider_budget.json').read_bytes()[:-8])),('unsupported_version',lambda r:(r/'provider_budget.json').write_text(json.dumps({**state(r),'version':2})))]:
 r=exhausted(name);change(r);before=(r/'provider_budget.json').read_bytes() if (r/'provider_budget.json').exists() else None
 answer=run(r);assert not answer['allowed'];assert 'synthetic_dispatch' not in answer['events'];assert ((r/'provider_budget.json').read_bytes() if (r/'provider_budget.json').exists() else None)==before
 note(name+'_established_authority_blocks_restart',error=answer['error'])
r=OUT/'never_initialized';r.mkdir();assert not run(r)['allowed'];assert not (r/'provider_budget.json').exists();note('no_silent_first_initialization')
r=seed('unreadable');assert not run(r,fault='read_unavailable')['allowed'];note('unreadable_state_blocks_dispatch')
for name,payload in [('duplicate_key','{"version":1,"version":1,"calls":[],"updated_at":1000}'),('wrong_shape','{"version":1,"calls":{},"updated_at":1000}'),('oversize',' '*65537)]:
 r=seed(name);(r/'provider_budget.json').write_text(payload);assert not run(r)['allowed'];note('incompatible_'+name+'_denied')
r=seed('trace');answer=run(r);assert answer['allowed'];ev=answer['events'];assert ev==['write:guard','sync:guard','sync:directory','write:temp','sync:temp','replace','sync:directory','write:guard','sync:guard','permission_returned','synthetic_dispatch'],ev
note('actual_file_and_parent_directory_sync_before_permission',events=ev)
for fault in ['partial_write','before_rename','after_rename','crash_after_publication','write_failure','rename_failure','file_sync_failure','directory_sync_failure','unsupported_directory_sync']:
 r=seed(fault);answer=run(r,fault=fault)
 if fault in ['partial_write','before_rename','after_rename','crash_after_publication']:assert answer=={'crash':71}
 else:assert not answer['allowed'] and 'synthetic_dispatch' not in answer['events']
 count=len(state(r)['calls']);restart=run(r);assert not restart['allowed'] and restart['error']=='PROVIDER_BUDGET_RECOVERY_REQUIRED';assert len(state(r)['calls'])==count
 note(fault+'_blocks_unverified_restart',retained_reservations=count)
r=seed('guard_sync_failure');assert not run(r,fault='guard_sync_failure')['allowed'];assert not run(r)['allowed'];note('guard_sync_failure_denies_and_blocks_ambiguity')
r=seed('ready_sync_failure');answer=run(r,fault='ready_sync_failure');assert not answer['allowed'];assert 'synthetic_dispatch' not in answer['events'];assert len(state(r)['calls'])==1;assert run(r)['result']['token_calls']==2;note('ready_marker_sync_failure_denies_and_keeps_durable_debit')
r=seed('fifo');(r/'provider_budget.json').rename(r/'independent_authority_copy.json');os.mkfifo(r/'provider_budget.json');assert not run(r)['allowed'];note('nonregular_fifo_denied_without_blocking')
# Reproduce the original exact allowance reset using the retained accepted source.
import importlib.util
old_path=MODULE.parents[4]/'docs/dev/lightweight_dev004_first_v2/birdeye_recovery_composition/accepted_dev_provider_budget.py'
spec=importlib.util.spec_from_file_location('old_budget',old_path);old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
r=seed('original_counterexample')
for i in range(4):assert old.DevProviderBudget(r).admit('SYNTHETIC_A','SYNTHETIC_WINDOW',now=1000)['token_calls']==i+1
(r/'provider_budget.json').write_text('{incomplete');assert old.DevProviderBudget(r).admit('SYNTHETIC_A','SYNTHETIC_WINDOW',now=1000)['remaining_token']==3;note('original_four_consumed_malformed_fresh_allowance_reproduced')
r=seed('crash_before_dispatch');assert run(r,fault='crash_before_dispatch')=={'crash':71};assert len(state(r)['calls'])==1;answer=run(r);assert answer['result']['token_calls']==2;note('crash_after_durable_reservation_before_dispatch_remains_charged')
r=exhausted('expiry');assert run(r,now=1060)['result']['token_calls']==1;note('unchanged_60_second_window_expiry')
r=seed('clock');assert not run(r,now=999)['allowed'];note('clock_regression_denied')
for name,token_count in [('concurrent_token',True),('concurrent_global',False)]:
 r=seed(name)
 with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
  futures=[pool.submit(run,r,mint='SYNTHETIC_A' if token_count else 'SYNTHETIC_'+str(i)) for i in range(32)]
  answers=[f.result() for f in futures]
 successful=sum(a['allowed'] for a in answers);assert successful==(4 if token_count else 20),(name,successful)
 assert len(state(r)['calls'])==successful
 note(name,successful=successful,denied=32-successful)
max_file=max(p.stat().st_size for p in OUT.rglob('*') if p.is_file());assert max_file<16000000
result={'status':'PASS','source_module':str(MODULE),'source_sha256':hashlib.sha256(MODULE.read_bytes()).hexdigest(),'groups':results,'group_count':len(results),'provider_calls':0,'file_cap':16000000,'maximum_observed_fixture_file_bytes':max_file,'output_root':str(OUT),'raw_retention':False,'new_unbounded_growth_paths':0,'multi_window_callbacks_integrated':False}
(OUT/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
