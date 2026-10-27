"""Existing OPS owner and journal, disposable stores, kernel cap, no network."""
import json,sqlite3,types,asyncio,ast,os,hashlib,concurrent.futures
from pathlib import Path
import pytest
from tests.test_v2_terminal_capacity import case as base_case
ROOT=Path(__file__).resolve().parents[1]
@pytest.fixture
def case(base_case):
 e=base_case
 owner=e.tm.DeferredMigrationStore(e.ops,e.j.path,generation='fixture-v2',validate_record=e.j._validate,validate_pair=lambda *_:'fixture-v2');owner.initialize();e.j.deferred_owner=owner;e.deferred=owner
 e.seed_pending=lambda n=512:seed(e,n)
 def restart():return e.jm.MigrationJournal(e.j.path,terminal_owner=e.owner,deferred_owner=owner)
 e.restart=restart;return e

def seed(e,n):
 rows=[]
 for i in range(n):
  r=e.j._new(f'pending-{i}','PUMPSWAP',100+i,None,None);r.update(error_code='HELIUS_BUDGET_DEFERRED',retry_not_before=1300);rows.append(r)
 with e.j._lock():e.j._publish(rows)
 return rows

def retained(e):
 with sqlite3.connect(e.ops) as c:r=[json.loads(x[0]) for x in c.execute('select payload from migration_deferred_evidence_v1')]
 return {x['id']:x for x in r+e.j.snapshot()}

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


def test_real_existing_budget_context_after_eligible_checkout(case,v,monkeypatch):
 import sys
 e=case;e.seed_pending();e.j.admit('fresh','PUMPSWAP',2000)
 monkeypatch.setitem(sys.modules,'src.core.migration_signature_journal',e.jm)
 monkeypatch.setenv('MIGRATION_JOURNAL_PATH',str(e.j.path));monkeypatch.setenv('DB_PATH',str(v.core));v.open();v.h.install('listener')
 e.j.admit('pending-0','PUMPPORTAL',9999,notification_mint='known')
 with pytest.raises(v.m.BudgetDeferred):
  with v.h.owned_context('migration:pending-0'):pass
 root=v.root_id('known')
 with v.h.owned_context('migration:pending-0'):
  reservation=v.h._admit('https://mainnet.helius-rpc.com/','POST',b'{"method":"getTransaction","params":["pending-0"]}')
 v.h._finish(reservation,200)
 assert v.g.read()['rpc_budget']['scopes'][root]['credits']==1
 assert v.g.read()['rpc_budget']['policy']['lifetime_credits']==100000

from tests.test_v2_helius_investigation_budget import v
from tests.test_v2_helius_rpc_budget import e as legacy


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


def test_exact_retained512_population_transfer_no_reclassification(case):
 e=case;p=Path('/private/tmp/pending-current512-fixture.jsonl')
 if not p.exists():pytest.skip('local fixed production cohort not present')
 rows=[json.loads(l) for l in p.read_text().splitlines()];assert len(rows)==512
 assert sum(r['state']=='PENDING' for r in rows)==511 and sum(r['state']=='RESOLVED' for r in rows)==1
 with e.j._lock():e.j._publish(rows)
 e.j.admit('new-513','PUMPSWAP',2000)
 r=retained(e);assert all(r[x['id']]==x for x in rows)
 assert not any(x['state'] in ('RETRY_EXHAUSTED','ACKED') for x in r.values())


def test_inflight_dispatch_is_never_transferred_or_relabelled(case):
 e=case;old=e.seed_pending();old[0].update(error_code='DISPATCH_RESERVED',attempts=1,retry_not_before=2000)
 with e.j._lock():e.j._publish(old)
 e.j.admit('new513','PUMPSWAP',2000)
 assert next(r for r in e.j.snapshot() if r['id']==old[0]['id'])==old[0]
 assert e.deferred.lookup(old[0]['signature']) is None
 with pytest.raises(Exception,match='DISPATCH_UNCERTAIN'):e.deferred.preserve_batch([old[0]])
