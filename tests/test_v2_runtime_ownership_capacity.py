"""Focused real-function and disposable-owner tests. All network forbidden."""
import ast,contextlib,sqlite3,types,sys,time,os,json,concurrent.futures
from pathlib import Path
import pytest
from tests.test_v2_terminal_capacity import case as base_case
ROOT=Path(__file__).resolve().parents[1]

@pytest.fixture
def case(base_case):
 e=base_case
 owner=e.tm.DeferredMigrationStore(e.ops,e.j.path,generation='fixture-v2',validate_record=e.j._validate,validate_pair=lambda *_:'fixture-v2');owner.initialize();e.j.deferred_owner=owner;e.deferred=owner
 def seed(n=512):
  rows=[]
  for i in range(n):
   r=e.j._new(f'pending-{i}','PUMPSWAP',100+i,None,None);r.update(error_code='HELIUS_BUDGET_DEFERRED',retry_not_before=1300);rows.append(r)
  with e.j._lock():e.j._publish(rows)
  return rows
 e.seed_pending=seed;e.restart=lambda:e.jm.MigrationJournal(e.j.path,terminal_owner=e.owner,deferred_owner=owner)
 return e

def retained(e):
 with sqlite3.connect(e.ops) as c:rows=[json.loads(x[0]) for x in c.execute('select payload from migration_deferred_evidence_v1')]
 return {r['id']:r for r in rows+e.j.snapshot()}

@pytest.fixture(autouse=True)
def blocked(monkeypatch):
 import socket
 def no(*a,**kw):raise AssertionError('NETWORK_FORBIDDEN')
 monkeypatch.setattr(socket.socket,'connect',no);monkeypatch.setattr(socket,'create_connection',no)
 # Always use synthetic records, never an operator evidence file.
 import tests.test_v2_terminal_capacity as t
 monkeypatch.setattr(t,'PRIVATE',Path('/private/tmp/never-use-live-evidence-fixture'))

@pytest.fixture
def worker(monkeypatch,tmp_path):
 class BudgetDeferred(Exception):pass
 class Integrity(Exception):pass
 mod=types.ModuleType('src.core.helius_denial_gate');mod.BudgetDeferred=BudgetDeferred
 http=types.ModuleType('src.core.helius_http_protection');calls=[]
 @contextlib.contextmanager
 def owned(identity):calls.append(('enter',identity));yield;calls.append(('exit',identity))
 http.owned_context=owned
 monkeypatch.setitem(sys.modules,'src.core.helius_denial_gate',mod);monkeypatch.setitem(sys.modules,'src.core.helius_http_protection',http)
 monkeypatch.setenv('FLEX_HELIUS_DENIAL_GATE_ENABLED','1');monkeypatch.setenv('WALKBACK_WORKER_ID','fixture-owner')
 p=tmp_path/'ops.sqlite';q=sqlite3.connect(p);q.row_factory=sqlite3.Row
 q.execute('create table wt_walkback_queue(mint primary key,status,attempts,lease_expires_at,next_retry_at,last_error,rpc_used,updated_at,claimed_by)')
 q.execute("insert into wt_walkback_queue values('m','running',2,999,0,NULL,7,0,'fixture-owner')");q.commit()
 ns={'sqlite3':sqlite3,'time':time,'os':os,'_helius_install':lambda role:calls.append(('install',role))}
 tree=ast.parse((ROOT/'runtime/walkback/src/core/walkback_worker.py').read_text());nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in ['_process_row','_defer_helius_claim']]
 exec(compile(ast.Module(body=nodes,type_ignores=[]),'actual_worker','exec'),ns)
 ns['_process_owned_row']=lambda *a:calls.append(('dispatch',)) or 3
 yield types.SimpleNamespace(ns=ns,http=http,BudgetDeferred=BudgetDeferred,Integrity=Integrity,q=q,p=p,calls=calls,row={'mint':'m','attempts':1})
 q.close()

@pytest.mark.parametrize('reason',['HELIUS_GATE_BUSY','HELIUS_REQUEST_RATE_LIMIT','HELIUS_INVESTIGATION_CREDIT_LIMIT','HELIUS_REQUEST_COOLDOWN'])
def test_entry_typed_reason_durable_no_dispatch_refund(worker,reason):
 e=worker;exc=e.BudgetDeferred(reason)
 @contextlib.contextmanager
 def denied(identity):raise exc;yield
 e.http.owned_context=denied
 seen=[];real=e.ns['_defer_helius_claim']
 def record(*a):seen.append(a[2]);return real(*a)
 e.ns['_defer_helius_claim']=record;e.ns['_process_row'](e.q,e.row)
 assert seen==[exc] and ('dispatch',) not in e.calls
 e.q.close();e.q=sqlite3.connect(e.p);r=e.q.execute('select status,attempts,last_error,rpc_used,next_retry_at from wt_walkback_queue').fetchone()
 assert r[:4]==('pending',1,reason,7) and r[4]>=int(time.time())+(3 if reason=='HELIUS_GATE_BUSY' else 298)
 # A stale invocation must not refund the already-pending claim again.
 e.ns['_process_row'](e.q,e.row);assert e.q.execute('select attempts from wt_walkback_queue').fetchone()[0]==1

def test_owned_processing_once_context_exit(worker):
 e=worker;assert e.ns['_process_row'](e.q,e.row)==3;assert e.calls.count(('dispatch',))==1;assert ('exit','mint:m') in e.calls

def test_wrong_owner_never_refunded(worker):
 e=worker;e.q.execute("update wt_walkback_queue set claimed_by='other'");e.q.commit();e.ns['_defer_helius_claim'](e.q,e.row,e.BudgetDeferred('HELIUS_GATE_BUSY'));assert e.q.execute('select status,attempts from wt_walkback_queue').fetchone()[:]==('running',2)

@pytest.mark.parametrize('error',['SCHEMA','GENERATION','DISK'])
def test_entry_integrity_not_swallowed(worker,error):
 e=worker
 @contextlib.contextmanager
 def bad(identity):raise e.Integrity(error);yield
 e.http.owned_context=bad
 with pytest.raises(e.Integrity,match=error):e.ns['_process_row'](e.q,e.row)
 assert ('dispatch',) not in e.calls and e.q.execute('select attempts from wt_walkback_queue').fetchone()[0]==2

def test_proactive_492_single_batch_exact_ownership(case):
 e=case;old=e.seed_pending(491);e.j.admit('new','PUMPSWAP',2000)
 assert len(e.j.snapshot())==472 and len(retained(e))==492
 with sqlite3.connect(e.ops) as q:assert q.execute('select used_records from migration_deferred_owner_v1').fetchone()[0]==20
 assert all(retained(e)[r['id']]==r for r in old)

def test_below_watermark_no_transfer(case):
 e=case;e.seed_pending(490);e.j.admit('new','PUMPSWAP',2000);assert len(e.j.snapshot())==491
 with sqlite3.connect(e.ops) as q:assert q.execute('select used_records from migration_deferred_owner_v1').fetchone()[0]==0

def test_reserved_dispatch_never_transferred(case):
 e=case;rows=e.seed_pending(491)
 for r in rows[:30]:r['error_code']='DISPATCH_RESERVED'
 with e.j._lock():e.j._publish(rows)
 e.j.admit('new','PUMPSWAP',2000);active={r['id'] for r in e.j.snapshot()};assert all(r['id'] in active for r in rows[:30])

def test_full_owner_optional_maintenance_does_not_waste_active_slots(case):
 e=case;e.seed_pending(491)
 with sqlite3.connect(e.ops) as q:q.execute('update migration_deferred_owner_v1 set used_records=8192')
 for i in range(21):e.j.admit('spare'+str(i),'PUMPSWAP',2000)
 assert len(e.j.snapshot())==512;before=e.j.path.read_bytes()
 with pytest.raises(Exception,match='DEFERRED_CAPACITY'):e.j.admit('overflow','PUMPSWAP',2000)
 assert e.j.path.read_bytes()==before

@pytest.mark.parametrize('point',['after_ops','before_publish','after_publish'])
def test_proactive_crash_recovery_and_duplicate(case,monkeypatch,point):
 e=case;old=e.seed_pending(491);publish=e.j._publish;preserve=e.deferred.preserve_batch
 def failpreserve(rows):preserve(rows);raise RuntimeError('crash')
 def failpublish(rows):
  if point=='after_publish':publish(rows)
  raise RuntimeError('crash')
 if point=='after_ops':monkeypatch.setattr(e.deferred,'preserve_batch',failpreserve)
 else:monkeypatch.setattr(e.j,'_publish',failpublish)
 with pytest.raises(RuntimeError,match='crash'):e.j.admit('new','PUMPSWAP',2000)
 monkeypatch.setattr(e.deferred,'preserve_batch',preserve);monkeypatch.setattr(e.j,'_publish',publish)
 e.j=e.restart();e.j.admit('new','PUMPSWAP',2000)
 assert len(retained(e))==492 and all(retained(e)[r['id']]==r for r in old)
 e.j.admit('new','PUMPSWAP',9999);assert len(retained(e))==492

def test_no_eligible_records_no_forced_transfer(case):
 e=case;rows=e.seed_pending(491)
 for r in rows:r['error_code']='DISPATCH_RESERVED'
 with e.j._lock():e.j._publish(rows)
 e.j.admit('new','PUMPSWAP',2000);assert len(e.j.snapshot())==492
 with sqlite3.connect(e.ops) as q:assert q.execute('select used_records from migration_deferred_owner_v1').fetchone()[0]==0


def test_512_pending_event513_independent_verified_custody(case):
 e=case;old=e.seed_pending();r=e.j.admit('event513','PUMPPORTAL',2000,notification_mint='subject')
 assert r['state']=='PENDING' and r['ack'] is None and len(e.j.snapshot())==493
 keep=retained(e);assert all(keep[r['id']]==r for r in old)
 with sqlite3.connect(e.ops) as c:assert c.execute("select count(*) from migration_deferred_evidence_v1 where custody='OWNED'").fetchone()[0]==20
 assert e.restart().admit('pending-0','PUMPSWAP',9999)['attempts']==0
 assert len(retained(e))==513


def test_two_thousand_sequential_admissions_bounded(case):
 e=case;e.seed_pending()
 for i in range(2000):e.j.admit(f'next-{i}','PUMPSWAP',2000+i)
 assert len(retained(e))==2512 and len(e.j.snapshot())<=512
 with sqlite3.connect(e.ops) as c:
  used=c.execute('select used_records,used_bytes from migration_deferred_owner_v1').fetchone();assert used[0]<=8192 and used[1]<=64000000
 assert e.j.path.stat().st_size<=2000000


@pytest.mark.parametrize('boundary',['after_OPS_commit','before_journal_publish','after_journal_publish'])
def test_interrupted_transfer_restart_no_loss(case,monkeypatch,boundary):
 e=case;old=e.seed_pending();publish=e.j._publish;preserve=e.deferred.preserve_batch
 def crash_preserve(rows):preserve(rows);raise RuntimeError('CRASH_OPS')
 def crash_publish(rows):
  if boundary=='after_journal_publish':publish(rows)
  raise RuntimeError('CRASH_JOURNAL')
 if boundary=='after_OPS_commit':monkeypatch.setattr(e.deferred,'preserve_batch',crash_preserve)
 else:monkeypatch.setattr(e.j,'_publish',crash_publish)
 with pytest.raises(RuntimeError):e.j.admit('crash-event','PUMPSWAP',2000)
 monkeypatch.setattr(e.deferred,'preserve_batch',preserve);monkeypatch.setattr(e.j,'_publish',publish)
 j=e.restart();j.admit('crash-event','PUMPSWAP',2000);assert all(retained(e)[r['id']]==r for r in old);assert len(retained(e))==513


def test_due_selection_restarts_and_later_eligible_not_starved(case):
 e=case;e.seed_pending();e.j.admit('eligible','PUMPSWAP',2000)
 first=e.j.pending(now=1000);assert [r['signature'] for r in first]==['eligible']
 # Deferred custody is selected fairly once its original retry deadline expires.
 seen=set()
 for i in range(35):
  for r in e.restart().pending(now=1300+i*10):
   seen.add(r['id'])
   if e.j.begin_attempt(r['signature'],now=1300+i*10,defer_until=1345+i*10):
    row=next(x for x in e.j.snapshot() if x['id']==r['id']);assert e.j.defer_budget(r['signature'],expected_attempts=row['attempts'],dispatch_until=1345+i*10,not_before=2000+i*10)
 assert len(seen)==513
 assert all(r['attempts']==0 for r in retained(e).values())


def test_terminal_conflicts_never_relabeled_or_replayed(case):
 e=case;old=e.seed_pending();terminal=e.owner.lookup('none');assert terminal is None
 bad=dict(old[0],state='QUARANTINED',error_code='CONFLICT');e.owner.preserve_batch([bad])
 with e.j._lock():e.j._publish([bad]+old[1:])
 e.j.admit('fresh','PUMPSWAP',2000);assert e.owner.lookup(bad['signature'])==bad
 assert e.j.admit(bad['signature'],'PUMPSWAP',9999)==bad
 assert all(r['state']!='QUARANTINED' for r in e.j.pending(now=9999))


def test_owner_full_refuses_without_losing_pending(case,monkeypatch):
 e=case;old=e.seed_pending()
 with sqlite3.connect(e.ops) as c:c.execute('update migration_deferred_owner_v1 set used_records=8192')
 before=e.j.path.read_bytes()
 with pytest.raises(Exception,match='DEFERRED_CAPACITY'):e.j.admit('refused','PUMPSWAP',2000)
 assert e.j.path.read_bytes()==before and len(retained(e))==512


def test_corrupt_owner_readback_fails_closed(case,monkeypatch):
 e=case;e.seed_pending();before=e.j.path.read_bytes();lookup=e.deferred.lookup
 monkeypatch.setattr(e.deferred,'lookup',lambda *a,**k:None)
 with pytest.raises(Exception,match='READBACK'):e.j.admit('no-readback','PUMPSWAP',2000)
 assert e.j.path.read_bytes()==before


def test_concurrent_admission_and_resolver_writes(case):
 e=case;e.seed_pending()
 def admit(i):
  import time
  for attempt in range(200):
   try:return e.restart().admit('concurrent-'+str(i%4),'PUMPSWAP',2000+i%4)
   except e.jm.JournalHold as err:
    if str(err)!='OWNER_BUSY':raise
    time.sleep(.01)
  raise AssertionError('OWNER_BUSY_DID_NOT_SETTLE')
 def resolver():
  for i in range(20):
   with sqlite3.connect(e.ops,timeout=5) as c:c.execute('insert or ignore into unrelated_evidence values(?)',(str(i),))
 with concurrent.futures.ThreadPoolExecutor(4) as pool:
  f=pool.submit(resolver);list(pool.map(admit,range(12)));f.result()
 assert len(retained(e))==516
 with sqlite3.connect(e.ops) as c:assert c.execute('pragma foreign_key_check').fetchall()==[]


def test_disk_floor_refusal_preserves_active(case,monkeypatch):
 e=case;e.seed_pending();before=e.j.path.read_bytes()
 monkeypatch.setattr(e.tm.os,'statvfs',lambda *_:types.SimpleNamespace(f_bavail=1,f_frsize=1))
 with pytest.raises(Exception,match='DISK_FLOOR'):e.j.admit('disk-refused','PUMPSWAP',2000)
 assert e.j.path.read_bytes()==before


def test_actual_refusal_boundary_keeps_simulated_birth_intake(case):
 e=case;e.seed_pending();e.j.deferred_owner=None
 source=ROOT/'runtime/listener/src/core/pumpfun_curve_listener.py';node=next(n for n in ast.walk(ast.parse(source.read_text())) if isinstance(n,ast.FunctionDef) and n.name=='_schedule_journal_migration');log=[];ns={'_signature_migration_journal':lambda:e.j,'time':__import__('time'),'log_print':lambda *a,**kw:log.append(a[0])}
 exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),'exec'),ns)
 births=0
 for event in ['birth','migration','birth','migration']:
  if event=='birth':births+=1
  else:assert ns['_schedule_journal_migration'](types.SimpleNamespace(),'event513',[])=='ADMISSION_REFUSED'
 assert births==2 and len(log)==2 and len(e.j.snapshot())==512
 assert not any(r['signature']=='event513' for r in e.j.snapshot())
 # PumpPortal branch must not unsubscribe/process a refused input.
 text=source.read_text();assert "if scheduled == 'ADMISSION_REFUSED':continue" in text


def test_restoration_durable_checkout_canonical_handoff_and_dedup(case):
 e=case;e.seed_pending();e.j.admit('extra','PUMPSWAP',2000)
 row=e.deferred.lookup('pending-0');assert row and row['state']=='PENDING'
 due=e.j.pending(now=1300);assert any(r['signature']=='pending-0' for r in due)
 assert e.j.begin_attempt('pending-0',now=1300,defer_until=1345)
 assert e.j.defer_budget('pending-0',expected_attempts=1,dispatch_until=1345,not_before=1600)
 # Restoration only permits eligible work; retained IDs/attempts never reset.
 due=e.restart().pending(now=1600)
 if not any(r['signature']=='pending-0' for r in due):e.j.admit('pending-0','PUMPSWAP',9999)
 assert e.j.begin_attempt('pending-0',now=1600,defer_until=1645)
 e.j.resolve('pending-0','mint')
 with sqlite3.connect(e.ops) as c:
  c.execute('create table fixture_canonical(signature text primary key,mint text)');c.execute('create table fixture_handoff(signature text primary key,state text)')
 def verify(r):
  with sqlite3.connect(e.ops) as c:
   a=c.execute('select mint from fixture_canonical where signature=?',(r['signature'],)).fetchone();b=c.execute('select state from fixture_handoff where signature=?',(r['signature'],)).fetchone()
  return bool(a==('mint',) and b==('COMPLETE',))
 assert not e.j.acknowledge('pending-0','fixture',verify)
 with sqlite3.connect(e.ops) as c:c.execute("insert into fixture_canonical values('pending-0','mint')");c.execute("insert into fixture_handoff values('pending-0','COMPLETE')")
 assert e.j.acknowledge('pending-0','fixture',verify)
 e.j.reclaim_completed(verify);row=e.restart().admit('pending-0','PUMPSWAP',9999)
 assert row['state']=='ACKED' and row['attempts']==1 and row['received_at']==100
 with pytest.raises(Exception,match='DEFERRED_NOTIFICATION_CONFLICT'):e.restart().admit('pending-0','PUMPPORTAL',9999,notification_mint='wrong')
 assert e.deferred.lookup('pending-0')==row


@pytest.mark.parametrize('boundary',['before_publish','after_publish'])
def test_checkout_crash_reconciles_exact_custody(case,monkeypatch,boundary):
 e=case;old=e.seed_pending();e.j.admit('fresh','PUMPSWAP',2000);pub=e.j._publish
 def crash(rows):
  if boundary=='after_publish':pub(rows)
  raise RuntimeError('CHECKOUT_CRASH')
 monkeypatch.setattr(e.j,'_publish',crash)
 with pytest.raises(RuntimeError):e.j.pending(now=1300)
 monkeypatch.setattr(e.j,'_publish',pub)
 e.restart().pending(now=1310)
 assert all(retained(e)[r['id']]==r for r in old)
 assert len(retained(e))==513


def test_conflicting_duplicate_offloaded_subject_protected(case):
 e=case;old=e.seed_pending();old[0]['notification_mint']='original'
 with e.j._lock():e.j._publish(old)
 e.j.admit('extra','PUMPSWAP',2000)
 row=e.j.admit('pending-0','PUMPPORTAL',9000,notification_mint='conflict')
 assert row['state']=='QUARANTINED' and row['notification_mint']=='original' and row['conflicting_mint']=='conflict'
 assert row['attempts']==0 and row['received_at']==100


def test_fsynced_temp_publication_recovers_after_rename_crash(case,monkeypatch):
 e=case;old=e.seed_pending();replace=e.jm.os.replace
 def crash(src,dst):
  if str(dst)==str(e.j.path):raise OSError('RENAME_CRASH')
  return replace(src,dst)
 monkeypatch.setattr(e.jm.os,'replace',crash)
 with pytest.raises(OSError):e.j.admit('renamed','PUMPSWAP',2000)
 assert e.j.tmp.exists()
 monkeypatch.setattr(e.jm.os,'replace',replace)
 e.restart().snapshot()
 assert all(retained(e)[r['id']]==r for r in old) and len(retained(e))==513


def test_deferred_ready_query_is_indexed_and_bounded(case):
 e=case;e.seed_pending();e.j.admit('fresh','PUMPSWAP',2000)
 with sqlite3.connect(e.ops) as c:
  p=c.execute("explain query plan select identity from migration_deferred_evidence_v1 indexed by migration_deferred_due_v1 where custody='OWNED' and due<=1300 order by due,received_at,identity limit 10").fetchall()
 assert all('INDEX' in r[3] for r in p) and not any('TEMP B-TREE' in r[3] for r in p)
 assert len(e.deferred.ready(10,1300))<=10


def test_inflight_dispatch_is_never_transferred_or_relabelled(case):
 e=case;old=e.seed_pending();old[0].update(error_code='DISPATCH_RESERVED',attempts=1,retry_not_before=2000)
 with e.j._lock():e.j._publish(old)
 e.j.admit('new513','PUMPSWAP',2000)
 assert next(r for r in e.j.snapshot() if r['id']==old[0]['id'])==old[0]
 assert e.deferred.lookup(old[0]['signature']) is None
 with pytest.raises(Exception,match='DISPATCH_UNCERTAIN'):e.deferred.preserve_batch([old[0]])

def test_processing_checkout_does_not_proactively_displace_batch(case):
 e=case;e.seed_pending(500);selected=e.j.pending(now=1300)
 assert len(selected)==20
 for r in selected:
  assert e.j.begin_attempt(r['signature'],now=1300,defer_until=1345)
  assert e.j.defer_budget(r['signature'],expected_attempts=1,dispatch_until=1345,not_before=1600)
 assert len(e.j.snapshot())==500

@pytest.mark.parametrize('reason',['DEFERRED_READBACK','PAIR_GENERATION','DISK_FLOOR','SCHEMA'])
def test_proactive_real_safety_failures_not_suppressed(case,monkeypatch,reason):
 e=case;e.seed_pending(491);before=e.j.path.read_bytes()
 def refuse(*a):raise e.tm.TerminalHold(reason)
 monkeypatch.setattr(e.deferred,'preserve_batch',refuse)
 with pytest.raises(e.tm.TerminalHold,match=reason):e.j.admit('new','PUMPSWAP',2000)
 assert e.j.path.read_bytes()==before

def test_entry_claim_update_crash_never_loses_durable_job(worker):
 e=worker
 class CrashCommit:
  def execute(self,*a):return e.q.execute(*a)
  def commit(self):raise RuntimeError('CRASH_BEFORE_COMMIT')
 with pytest.raises(RuntimeError,match='CRASH_BEFORE_COMMIT'):
  e.ns['_defer_helius_claim'](CrashCommit(),e.row,e.BudgetDeferred('HELIUS_GATE_BUSY'))
 e.q.rollback();e.q.close();e.q=sqlite3.connect(e.p)
 assert e.q.execute('select status,attempts,rpc_used from wt_walkback_queue').fetchone()==('running',2,7)
 # Existing durable claim remains recoverable; completing the deferral after
 # restart refunds it exactly once, without issuing a request.
 e.ns['_defer_helius_claim'](e.q,e.row,e.BudgetDeferred('HELIUS_GATE_BUSY'))
 assert e.q.execute('select status,attempts,rpc_used from wt_walkback_queue').fetchone()==('pending',1,7)

def test_recovery_exact_two_pins_and_other_contracts_unchanged():
 import subprocess,hashlib
 rel='docs/dev/lightweight_dev004_first_v2/quarantine_continuation/production_control.py'
 old=subprocess.check_output(['git','show','8ce26bf4f44005ebecd3f545be99a0ed55c884d1:'+rel],cwd=ROOT).decode()
 s=(ROOT/rel).read_text()
 replacements={'3a7d8351f65fd0e40b27a78547f0bbd668de6c0a3e1510a2dcfc2c91dcc94b6a':'runtime/walkback/src/core/walkback_worker.py','cb78113715f1a32b62f43b490e0eee427a0030adfda6045e8c791565c9a5ecd9':'runtime/listener/src/core/migration_signature_journal.py'}
 for pre,path in replacements.items():
  assert old.count(pre)==1
  old=old.replace(pre,hashlib.sha256((ROOT/path).read_bytes()).hexdigest())
 assert s==old
 ast.parse(s)


def test_exact_runtime_delta_and_installed_preimage_authority():
 import subprocess,hashlib
 baseline='8ce26bf4f44005ebecd3f545be99a0ed55c884d1'
 paths={'runtime/walkback/src/core/walkback_worker.py':('3a7d8351f65fd0e40b27a78547f0bbd668de6c0a3e1510a2dcfc2c91dcc94b6a',{'_process_row','_process_owned_row','_defer_helius_claim'}),'runtime/listener/src/core/migration_signature_journal.py':('cb78113715f1a32b62f43b490e0eee427a0030adfda6045e8c791565c9a5ecd9',{'_change','_maintain_reserve','admit'})}
 for rel,(pre,allowed) in paths.items():
  raw=subprocess.check_output(['git','show',baseline+':'+rel],cwd=ROOT);assert hashlib.sha256(raw).hexdigest()==pre
  old=ast.parse(raw);new=ast.parse((ROOT/rel).read_text())
  def funcs(t):return {n.name:ast.dump(n,include_attributes=False) for n in ast.walk(t) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name not in allowed and n.name!='change'}
  assert funcs(old)==funcs(new)
