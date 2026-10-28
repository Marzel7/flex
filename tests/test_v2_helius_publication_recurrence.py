"""Real gate owner, disposable ledgers/faults/flocks only; network denied."""
import copy,hashlib,importlib.util,json,os,pathlib,subprocess,sys,time,types,contextlib,socket,concurrent.futures,sqlite3
import pytest
from tests.test_v2_helius_investigation_budget import v
from tests.test_v2_helius_rpc_budget import e as legacy
ROOT=pathlib.Path(__file__).resolve().parents[1]
BASE='70783774bce38e4fd021135b57223951427025dd'
@pytest.fixture(autouse=True)
def no_network(monkeypatch):
 def no(*a,**kw):raise AssertionError('NETWORK_FORBIDDEN')
 monkeypatch.setattr(socket.socket,'connect',no);monkeypatch.setattr(socket,'create_connection',no)

def marker(e):return pathlib.Path(str(e.g.path)+'.lock')
def stage(e):return e.g.path.with_name(e.g.path.name+'.stage')
def ready(e):return marker(e).read_bytes()==b'READY_V1 '+hashlib.sha256(e.g.path.read_bytes()).hexdigest().encode()+b'\n'
@pytest.fixture
def opened(v):
 e=v;e.root_id('root');e.open();return e

def low_disk(e,monkeypatch):
 real=os.statvfs
 monkeypatch.setattr(e.m.os,'statvfs',lambda p:types.SimpleNamespace(f_bavail=1,f_frsize=1) if pathlib.Path(p)==e.g.path.parent else real(p))

def test_original_normal_disk_refusal_poisoning_counterexample(tmp_path,monkeypatch):
 raw=subprocess.check_output(['git','-C',str(ROOT),'show',BASE+':runtime/listener/src/core/helius_denial_gate.py']);p=tmp_path/'old_gate.py';p.write_bytes(raw);sp=importlib.util.spec_from_file_location('old_owner',p);m=importlib.util.module_from_spec(sp);sp.loader.exec_module(m);g=m.Gate(tmp_path/'gate.json','fixture');g.initialize('PAYMENT_DENIED')
 policy=json.loads((ROOT/'docs/dev/lightweight_dev004_first_v2/helius_investigation_budget/POLICY.json').read_text());g.configure_budget(policy,hashlib.sha256(g.path.read_bytes()).hexdigest());root=g.register_origin('mint:fixture','a'*64)
 with g.locked():s=g.read();s['state']='OPEN';g.save(s)
 before=g.path.read_bytes();assert g.read()['state']=='OPEN' and g.read()['rpc_budget']['version']==2
 monkeypatch.setattr(m.os,'statvfs',lambda p:types.SimpleNamespace(f_bavail=1,f_frsize=1))
 with pytest.raises(m.Integrity,match='DISK_FLOOR'):g.reserve_http(role='walkback',scopes=(root,),key='b'*64,selector='rpc:getTransaction',origin='c'*64)
 assert g.path.read_bytes()==before and pathlib.Path(str(g.path)+'.lock').read_bytes()==b'PENDING_V1\n' and not g.path.with_name(g.path.name+'.stage').exists()
 with pytest.raises(m.Integrity,match='PUBLICATION_AMBIGUOUS'):g.read()


def test_disk_refusal_before_reservation_retains_ready_and_no_charge(opened,monkeypatch):
 e=opened;before=e.g.path.read_bytes();low_disk(e,monkeypatch)
 with pytest.raises(e.m.Integrity,match='DISK_FLOOR'):e.reserve2(key='refused')
 assert e.g.path.read_bytes()==before and ready(e) and not stage(e).exists()
 assert e.g.read()['rpc_budget']==json.loads(before)['rpc_budget']


def test_completion_disk_refusal_preserves_paid_charge_and_inflight(opened,monkeypatch):
 e=opened;t=e.reserve2();before=e.g.read();real=os.statvfs;low_disk(e,monkeypatch)
 with pytest.raises(e.m.Integrity,match='DISK_FLOOR'):e.g.finish_http(t,200)
 assert ready(e) and e.g.read()==before and t in e.g.read()['rpc_budget']['inflight']
 monkeypatch.setattr(os,'statvfs',real);e.g.finish_http(t,200);e.g.finish_http(t,200)
 after=e.g.read();assert after['rpc_budget']['scopes']==before['rpc_budget']['scopes'];assert not after['rpc_budget']['inflight'] and after['outcomes']==before['outcomes']+1

@pytest.mark.parametrize('status',[402,403,429])
def test_new_provider_denial_cannot_be_lost_on_disk_refusal(opened,monkeypatch,status):
 e=opened;t=e.reserve2();before=e.g.path.read_bytes();low_disk(e,monkeypatch)
 with pytest.raises(e.m.Integrity,match='DISK_FLOOR'):e.g.notice_http(t,status)
 assert e.g.path.read_bytes()==before and marker(e).read_bytes().startswith(b'PENDING_V2 ')
 with pytest.raises(e.m.Integrity):
  with e.g.locked():e.g.finish_publication()
 assert json.loads(before)['rpc_budget']['inflight']

@pytest.mark.parametrize('fault_at',[1,2,3,4])
def test_fsync_fault_boundaries_keep_spending_and_proven_recovery(opened,monkeypatch,fault_at):
 e=opened;before=e.g.read();real=e.m.os.fsync;calls=[0]
 def fault(fd):
  calls[0]+=1
  if calls[0]==fault_at:raise OSError('SIMULATED_FSYNC')
  return real(fd)
 monkeypatch.setattr(e.m.os,'fsync',fault)
 with pytest.raises(OSError,match='SIMULATED_FSYNC'):e.reserve2(key='fault')
 monkeypatch.setattr(e.m.os,'fsync',real)
 if fault_at==1:assert e.g.read()==before and ready(e) and not stage(e).exists()
 elif fault_at==2:
  assert stage(e).exists()
  with pytest.raises(e.m.Integrity):
   with e.g.locked():e.g.finish_publication()
  assert e.g.path.read_bytes()==(json.dumps(before,sort_keys=True)+'\n').encode()
 else:
  if not ready(e):
   with pytest.raises(e.m.Integrity):e.g.read()  # audit readers never repair
   with e.g.locked():e.g.finish_publication()
  after=e.g.read();root=e.h._hash('mint:root');assert after['rpc_budget']['scopes'][root]['credits']==before['rpc_budget']['scopes'][root]['credits']+1
  assert len(after['rpc_budget']['inflight'])==1 and after['rpc_budget']['policy']['lifetime_credits']==100000
  with pytest.raises(e.m.BudgetDeferred,match='DUPLICATE_INFLIGHT'):e.reserve2(key='fault')

@pytest.mark.parametrize('when',['before','after'])
def test_atomic_replace_failure_only_recovers_actual_target(opened,monkeypatch,when):
 e=opened;before=e.g.read();real=e.m.os.replace
 def fail(src,dst):
  if when=='after':real(src,dst)
  raise OSError('SIMULATED_RENAME')
 monkeypatch.setattr(e.m.os,'replace',fail)
 with pytest.raises(OSError):e.reserve2(key='rename')
 monkeypatch.setattr(e.m.os,'replace',real)
 with e.g.locked():
  if when=='before':
   with pytest.raises(e.m.Integrity):e.g.finish_publication()
  else:assert e.g.finish_publication()['rpc_budget']['policy']['lifetime_credits']==100000
 assert json.loads(e.g.path.read_bytes())['rpc_budget']['owners']==before['rpc_budget']['owners']

@pytest.mark.parametrize('fault',['legacy','hash','corrupt','generation','operation','owner','stage'])
def test_uncertain_or_corrupt_publication_never_opens(opened,monkeypatch,fault):
 e=opened;s=e.g.read();raw=e.g.path.read_bytes();digest=hashlib.sha256(raw).hexdigest().encode();proof=b'PENDING_V2 '+digest+b' 99999999 state\n'
 if fault=='legacy':proof=b'PENDING_V1\n'
 if fault=='hash':proof=b'PENDING_V2 '+b'0'*64+b' 99999999 state\n'
 if fault=='operation':proof=b'PENDING_V2 '+digest+b' 99999999 unknown\n'
 if fault=='owner':proof=b'PENDING_V2 '+digest+b' unknown state\n'
 if fault=='stage':stage(e).write_bytes(raw)
 if fault=='corrupt':raw=b'{';e.g.path.write_bytes(raw);proof=b'PENDING_V2 '+hashlib.sha256(raw).hexdigest().encode()+b' 99999999 state\n'
 marker(e).write_bytes(proof);g=e.m.Gate(e.g.path,'wrong' if fault=='generation' else e.g.generation,clock=e.g.clock)
 with pytest.raises(e.m.Integrity):
  with g.locked():g.finish_publication()
 assert marker(e).read_bytes()==proof and e.g.path.read_bytes()==raw


def test_legacy_cannot_grant_allowance_but_explicit_closed_recovery_compatible(opened):
 e=opened;before=e.g.read();marker(e).write_bytes(b'PENDING_V1\n')
 with pytest.raises(e.m.Integrity):e.g.save(before)
 closed=copy.deepcopy(before);closed['state']='PAYMENT_DENIED';e.g.save(closed)
 assert e.g.read()==closed and e.g.read()['rpc_budget']==before['rpc_budget']
 with pytest.raises(e.m.BudgetDeferred):e.reserve2(key='still-closed')


def test_read_only_reader_does_not_finalize_even_with_a_lock(opened):
 e=opened;raw=e.g.path.read_bytes();marker(e).write_bytes(b'PENDING_V2 '+hashlib.sha256(raw).hexdigest().encode()+b' 99999999 state\n');before=marker(e).read_bytes()
 with e.g.locked(),pytest.raises(e.m.Integrity):e.g.read()
 assert marker(e).read_bytes()==before
 with pytest.raises(e.m.Integrity,match='LOCK_REQUIRED'):e.g.finish_publication()
 with e.g.locked():assert e.g.finish_publication()==json.loads(raw)


def test_real_process_restart_after_rename_preserves_charge(opened):
 e=opened;before=e.g.read();root=e.h._hash('mint:root');key=e.h._hash('child-crash');origin=e.h._hash('endpoint')
 code="""import importlib.util,sys,os
s=importlib.util.spec_from_file_location('owner',sys.argv[1]);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
g=m.Gate(sys.argv[2],'fixture',clock=lambda:1000);g.monotonic=lambda:1000
g._sync_directory=lambda:os._exit(71)
g.reserve_http(role='walkback',scopes=(sys.argv[3],),key=sys.argv[4],selector='rpc:getTransaction',origin=sys.argv[5])
"""
 result=subprocess.run([sys.executable,'-B','-c',code,str(ROOT/'runtime/listener/src/core/helius_denial_gate.py'),str(e.g.path),root,key,origin],capture_output=True,text=True,timeout=20);assert result.returncode==71
 assert marker(e).read_bytes().startswith(b'PENDING_V2 ')
 restarted=e.m.Gate(e.g.path,'fixture',clock=e.g.clock);restarted.monotonic=lambda:1000
 with restarted.locked():after=restarted.finish_publication()
 assert after['rpc_budget']['scopes'][root]['credits']==before['rpc_budget']['scopes'][root]['credits']+1
 assert after['rpc_budget']['owners']==before['rpc_budget']['owners'] and len(after['rpc_budget']['inflight'])==1
 # Completed publication recovery does not mean the never-returned ticket was
 # dispatched or completed. Its charge/reservation remains protected.
 assert after['rpc_budget']['keys'][key]['status']=='INFLIGHT'


def test_two_workers_normal_publications_and_duplicates(opened):
 e=opened;root=e.h._hash('mint:root');before=e.g.read()
 def work(i):
  g=e.m.Gate(e.g.path,'fixture',clock=e.g.clock);g.monotonic=lambda:1000
  for attempt in range(500):
   try:t=g.reserve_http(role='listener' if i%2 else 'walkback',scopes=(root,),key=e.h._hash('parallel'+str(i)),selector='rpc:getTransaction',origin=e.h._hash('endpoint'));break
   except e.m.BudgetDeferred as ex:
    if str(ex)!='HELIUS_GATE_BUSY':raise
    time.sleep(.002)
  else:raise AssertionError('contention did not settle')
  g.finish_http(t,200);g.finish_http(t,200)
 with concurrent.futures.ThreadPoolExecutor(2) as pool:list(pool.map(work,range(24)))
 after=e.g.read();q=after['rpc_budget']['scopes'][root]
 assert q['credits']==24 and q['requests']==24 and q['workers']['listener']==12 and q['workers']['walkback']==12
 assert after['outcomes']==before['outcomes']+24 and not after['rpc_budget']['inflight'] and ready(e)


def test_real_lock_contention_cannot_finalize_another_writer(opened):
 e=opened;raw=e.g.path.read_bytes();marker(e).write_bytes(b'PENDING_V2 '+hashlib.sha256(raw).hexdigest().encode()+b' 99999999 state\n')
 child=subprocess.Popen([sys.executable,'-B','-c',"import sys,fcntl;f=open(sys.argv[1],'r+');fcntl.flock(f,fcntl.LOCK_EX);print('READY',flush=True);sys.stdin.read()",str(marker(e))],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
 assert child.stdout.readline().strip()=='READY'
 try:
  with pytest.raises(e.m.BudgetDeferred,match='BUSY'):
   with e.g.locked():e.g.finish_publication()
 finally:child.stdin.close();child.wait(timeout=10)
 with e.g.locked():e.g.finish_publication()
 assert ready(e)


def test_clock_update_commit_recovery_changes_no_spending(opened,monkeypatch):
 e=opened;before=e.g.read();real=e.g._sync_directory
 monkeypatch.setattr(e.g,'_sync_directory',lambda:(_ for _ in ()).throw(OSError('CLOCK_COMMIT')))
 def recover_clock():
  s=e.g.read();s['rpc_budget']['wall']+=1;s['rpc_budget']['mono']+=1;e.g.save(s)
 with pytest.raises(OSError):recover_clock()
 assert marker(e).read_bytes().split()[-1]==b'clock_update'
 monkeypatch.setattr(e.g,'_sync_directory',real)
 with e.g.locked():after=e.g.finish_publication()
 for field in ['scopes','owners','keys','inflight','policy']:assert after['rpc_budget'][field]==before['rpc_budget'][field]


def test_current_large_ledger_independent_copy_only(tmp_path,monkeypatch):
 # Read one bounded retained ledger. Never change its production authority.
 live=pathlib.Path('/Users/kevinkeaveney/Dev/claude/flex/.runtime/dev004-v2/journals/helius-denial-v1.json');raw=live.read_bytes();assert 1000000<len(raw)<16777216
 sp=importlib.util.spec_from_file_location('large_owner',ROOT/'runtime/listener/src/core/helius_denial_gate.py');m=importlib.util.module_from_spec(sp);sp.loader.exec_module(m);p=tmp_path/'gate.json';p.write_bytes(raw);p.chmod(0o600);pathlib.Path(str(p)+'.lock').write_bytes(b'READY_V1 '+hashlib.sha256(raw).hexdigest().encode()+b'\n');before=json.loads(raw);g=m.Gate(p,before['generation']);s=g.read();s['next_at']+=1;real=g._sync_directory
 monkeypatch.setattr(g,'_sync_directory',lambda:(_ for _ in ()).throw(OSError('LARGE_COMMIT')))
 with pytest.raises(OSError):g.save(s)
 monkeypatch.setattr(g,'_sync_directory',real)
 with g.locked():after=g.finish_publication()
 assert after==s and after['rpc_budget']==before['rpc_budget'];assert p.stat().st_size<16777216 and not p.with_name(p.name+'.stage').exists()

@pytest.mark.parametrize('fault',['newline','whitespace'])
def test_partial_or_noncanonical_intent_marker_stays_closed(opened,fault):
 e=opened;raw=e.g.path.read_bytes();proof=b'PENDING_V2 '+hashlib.sha256(raw).hexdigest().encode()+b' 99999999 state\n'
 proof=proof.rstrip(b'\n') if fault=='newline' else proof.replace(b' state',b'  state')
 marker(e).write_bytes(proof)
 with e.g.locked(),pytest.raises(e.m.Integrity):e.g.finish_publication()
 assert marker(e).read_bytes()==proof

@pytest.mark.parametrize('denied',[False,True])
def test_real_current_writer_finishes_only_proven_state(opened,monkeypatch,denied):
 e=opened;stable=e.g.path.parent/'helius-denial-v1.json';s=e.g.read()
 if denied:s['state']='PAYMENT_DENIED'
 raw=(json.dumps(s,sort_keys=True)+'\n').encode();stable.write_bytes(raw);lock=pathlib.Path(str(stable)+'.lock');lock.write_bytes(b'PENDING_V2 '+hashlib.sha256(raw).hexdigest().encode()+b' 99999999 finish_http\n')
 monkeypatch.setenv('HELIUS_DENIAL_GATE_PATH',str(stable));monkeypatch.setenv('MIGRATION_JOURNAL_PATH',str(stable.parent/'migration.jsonl'));monkeypatch.setattr(e.m,'authority',lambda:'fixture')
 g=e.original_current();assert g.read()==s and g.read()['rpc_budget']==e.g.read()['rpc_budget']
 assert lock.read_bytes().startswith(b'READY_V1 ')
 g.clock=e.g.clock;g.monotonic=lambda:1000
 if not denied:
  t=g.reserve_http(role='walkback',scopes=(e.h._hash('mint:root'),),key=e.h._hash('new-owned'),selector='rpc:getTransaction',origin=e.h._hash('endpoint'));g.finish_http(t,200)
  assert g.read()['rpc_budget']['scopes'][e.h._hash('mint:root')]['credits']==1
 if denied:
  with pytest.raises(e.m.BudgetDeferred):g.reserve_http(role='walkback',scopes=(e.h._hash('mint:root'),),key=e.h._hash('denied'),selector='rpc:getTransaction',origin=e.h._hash('endpoint'))


def test_matched_fresh_pair_generation_only_publication(opened,monkeypatch):
 e=opened;sp=importlib.util.spec_from_file_location('actual_guard',ROOT/'runtime/listener/src/core/paired_binding_guard.py');pair=importlib.util.module_from_spec(sp);sp.loader.exec_module(pair);before=e.g.read();target_gen='fresh-publication-recovery'
 authority_path=e.root/'pair-authority.json';authority={'id':'publication-fixture','path':str(authority_path),'origin_generation':'fixture'};paths={role:e.root/(role+'.sqlite') for role in ['core','ops']}
 for role,p in paths.items():
  with sqlite3.connect(p) as q:q.execute('create table preserved(identity primary key)');q.execute("insert into preserved values('original')")
 previous_paths={role:e.root/('previous-'+role+'.sqlite') for role in ['core','ops']}
 for p in previous_paths.values():
  with sqlite3.connect(p) as q:q.execute('create table original(identity primary key)')
  pathlib.Path(str(p)+'.retired').write_text(json.dumps({'version':1,'path':str(p),'generation':'fixture','retired':True}))
 manifest={'version':1,'authority':authority,'generation':target_gen,'stores':{role:pair.database_identity(str(p),target_gen,role) for role,p in paths.items()},'predecessor':{'generation':'fixture','stores':{role:pair.database_identity(str(p),'fixture',role) for role,p in previous_paths.items()}}};path=e.root/'pair.json';path.write_text(json.dumps(manifest));authority_path.write_text(json.dumps({'version':1,'authority_id':authority['id'],'origin_generation':'fixture','generation':target_gen,'manifest_path':str(path),'manifest_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'phase':'ACTIVE','owner':'runtime','transfer_complete':True}));pair.seal_manifest(path)
 import src.core
 monkeypatch.setitem(sys.modules,'src.core.paired_binding_guard',pair);monkeypatch.setattr(src.core,'paired_binding_guard',pair,raising=False)
 for key,value in {'DB_PATH':str(paths['core']),'WT_OPS_DB_PATH':str(paths['ops']),'DEV004_PAIRED_BINDING_REQUIRED':'1','FLEX_DB_PAIR_MANIFEST':str(path),'FLEX_DB_PAIR_AUTHORITY':str(authority_path),'FLEX_DB_PAIR_ROLE':'runtime'}.items():monkeypatch.setenv(key,value)
 assert e.m.authority()==target_gen
 # Same renderer call shape: old owner publishes exact generation-only move.
 expected=dict(before,generation=target_gen)
 with e.g.locked():e.g.save(expected)
 after=e.m.Gate(e.g.path,target_gen).read();assert after==expected and after['rpc_budget']==before['rpc_budget']
 with pytest.raises(e.m.Integrity):e.g.read()
 for p in paths.values():
  with sqlite3.connect(p) as q:assert q.execute('select * from preserved').fetchall()==[('original',)]


def test_generation_change_with_accounting_edit_refused(opened,monkeypatch):
 e=opened;before=e.g.path.read_bytes();s=e.g.read();s['generation']='new';s['failures']+=1;monkeypatch.setattr(e.m,'authority',lambda:'new')
 with pytest.raises(e.m.Integrity,match='GENERATION_MOVE_AUTHORITY'):e.g.save(s)
 assert e.g.path.read_bytes()==before and ready(e)


def test_closing_read_and_marker_fds_never_releases_writer_flock(opened):
 e=opened
 code="""import os,sys,fcntl
f=os.open(sys.argv[1],os.O_RDWR)
try:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
except BlockingIOError:sys.exit(73)
sys.exit(0)
"""
 with e.g.locked():
  s=e.g.read()
  assert subprocess.run([sys.executable,'-B','-c',code,str(marker(e))],timeout=10).returncode==73
  e.g.save(s)
  assert subprocess.run([sys.executable,'-B','-c',code,str(marker(e))],timeout=10).returncode==73
 assert subprocess.run([sys.executable,'-B','-c',code,str(marker(e))],timeout=10).returncode==0


def test_repeated_normal_reservation_and_completion_bounded(opened):
 e=opened;root=e.h._hash('mint:root')
 for i in range(256):
  e.now[0]+=3;t=e.reserve2(key='sequential-'+str(i));e.g.finish_http(t,200)
 after=e.g.read();assert after['rpc_budget']['scopes'][root]['credits']==256;assert len(after['rpc_budget']['keys'])==256 and not after['rpc_budget']['inflight'];assert ready(e) and not stage(e).exists();assert e.g.path.stat().st_size<16777216


def test_oversize_target_refused_before_intent(opened):
 e=opened;before=e.g.path.read_bytes();s=e.g.read();s['too_large']='x'*16777216
 with pytest.raises(e.m.BudgetDeferred,match='STATE_CAPACITY'):e.g.save(s)
 assert ready(e) and e.g.path.read_bytes()==before and not stage(e).exists()


def test_candidate_renderer_changes_only_three_gate_pins():
 old=subprocess.check_output(['git','-C',str(ROOT),'show',BASE+':docs/dev/lightweight_dev004_first_v2/quarantine_continuation/production_control.py']).decode();new=(ROOT/'docs/dev/lightweight_dev004_first_v2/quarantine_continuation/production_control.py').read_text();digest=hashlib.sha256((ROOT/'runtime/listener/src/core/helius_denial_gate.py').read_bytes()).hexdigest();pre='623b81d79f1160a1b1e0b52b7c3904a1489416e8617658e3cff08ad0c30bd15f';assert old.count(pre)==3;assert new==old.replace(pre,digest).replace("   current=new.read()\n   if current!=expected:raise RuntimeError('HELIUS_RECOVERY_STATE_CONFLICT')","   try:current=new.read()\n   except module.Integrity as exc:\n    if str(exc)!='HELIUS_GATE_PUBLICATION_AMBIGUOUS':raise\n    current=new.finish_publication(expected_state=expected)\n   if current!=expected:raise RuntimeError('HELIUS_RECOVERY_STATE_CONFLICT')")
 for role in ('creator_resolution','walkback'):assert (ROOT/f'runtime/{role}/src/core/helius_denial_gate.py').read_bytes()==(ROOT/'runtime/listener/src/core/helius_denial_gate.py').read_bytes()


def test_fresh_owner_same_file_lock_and_independent_expected_state(opened):
 e=opened;before=e.g.read();raw=e.g.path.read_bytes();marker(e).write_bytes(b'PENDING_V2 '+hashlib.sha256(raw).hexdigest().encode()+b' 99999999 generation_move\n');new=e.m.Gate(e.g.path,'fixture');wrong=dict(before,failures=before['failures']+1)
 with e.g.locked():
  with pytest.raises(e.m.Integrity,match='EXPECTED_STATE_MISMATCH'):new.finish_publication(expected_state=wrong)
  assert marker(e).read_bytes().startswith(b'PENDING_V2 ')
  assert new.finish_publication(expected_state=before)==before


def test_one_gate_lock_never_authorizes_a_different_path(opened):
 e=opened;other=e.m.Gate(e.root/'other.json','fixture');other.initialize('PAYMENT_DENIED');raw=other.path.read_bytes();path=pathlib.Path(str(other.path)+'.lock');path.write_bytes(b'PENDING_V2 '+hashlib.sha256(raw).hexdigest().encode()+b' 99999999 state\n')
 with e.g.locked(),pytest.raises(e.m.Integrity,match='LOCK_REQUIRED'):other.finish_publication()
 assert path.read_bytes().startswith(b'PENDING_V2 ')


def test_forked_child_cannot_borrow_parent_publication_ownership(opened):
 e=opened
 with e.g.locked():
  pid=os.fork()
  if pid==0:os._exit(71 if e.g._publication_owned() else 0)
  _,status=os.waitpid(pid,0)
  assert os.WIFEXITED(status) and os.WEXITSTATUS(status)==0
  assert e.g._publication_owned()
