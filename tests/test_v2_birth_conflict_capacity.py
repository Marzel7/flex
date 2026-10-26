"""Typed reuse of protected OPS owner; synthetic only, all network blocked."""
import ast,hashlib,importlib.util,json,os,pathlib,resource,socket,sqlite3,sys,types
import pytest
ROOT=pathlib.Path(__file__).resolve().parents[1]

def load(name):
 p=ROOT/'runtime/listener/src/core'/(name+'.py');spec=importlib.util.spec_from_file_location('birth_fixture_'+name,p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

@pytest.fixture
def case(tmp_path,monkeypatch):
 def forbidden(*a,**k):raise AssertionError('NETWORK_FORBIDDEN')
 monkeypatch.setattr(socket.socket,'connect',forbidden);monkeypatch.setattr(socket,'create_connection',forbidden)
 jm=load('migration_signature_journal');monkeypatch.setitem(sys.modules,'src.core.migration_signature_journal',jm)
 bm=load('birth_admission_journal');tm=load('migration_terminal_evidence');monkeypatch.setitem(sys.modules,'src.core.birth_admission_journal',bm);monkeypatch.setitem(sys.modules,'src.core.migration_terminal_evidence',tm)
 monkeypatch.setattr(jm,'_paired_ack_generation',lambda:'birth-fixture-v2')
 old=resource.getrlimit(resource.RLIMIT_FSIZE);resource.setrlimit(resource.RLIMIT_FSIZE,(499000000,old[1]))
 ops=tmp_path/'ops.sqlite'
 with sqlite3.connect(ops) as c:c.execute('CREATE TABLE canonical_fixture(identity TEXT PRIMARY KEY, signature TEXT)');c.execute("INSERT INTO canonical_fixture VALUES('canonical','original')")
 j=bm.BirthJournal(tmp_path/'birth.jsonl',verify_completed=lambda r:False);j.initialize()
 generation=['birth-fixture-v2']
 def validate(path):
  if pathlib.Path(path)!=ops:raise tm.TerminalHold('WRONG_OPS')
  return generation[0]
 def owner(journal=j,path=ops,gen='birth-fixture-v2',validator=validate):return tm.BirthTerminalEvidenceStore(path,j.path,generation=gen,validate_record=journal._validate,validate_pair=validator)
 o=owner();o.initialize();j.terminal_owner=o
 def payload(n):return dict(_birth_retry_schema=1,mint='mint-'+str(n),signature='birth-signature-'+str(n),traderPublicKey='creator-'+str(n),bondingCurveKey=None,symbol=None,name=None,receive_utc_ns=1000000+n,provenance='PUMPPORTAL_NEW_TOKEN')
 def rows(n=512):
  result=[j._record(payload(v)) for v in range(n)]
  for v in result:v.update(state='QUARANTINED',error_code='CORE_AUTHORITY_CONFLICT')
  return result
 def seed(n=512):
  result=rows(n)
  with j._lock():j._publish(result)
  return result
 def restart():
  k=bm.BirthJournal(j.path,verify_completed=lambda r:False);k.terminal_owner=owner(k);return k
 yield types.SimpleNamespace(j=j,o=o,jm=jm,bm=bm,tm=tm,ops=ops,tmp=tmp_path,payload=payload,rows=rows,seed=seed,restart=restart,generation=generation,owner=owner)
 with sqlite3.connect(ops) as c:assert c.execute('SELECT * FROM canonical_fixture').fetchall()==[('canonical','original')];assert c.execute('PRAGMA foreign_key_check').fetchall()==[]
 assert all(p.stat().st_size<499000000 for p in tmp_path.rglob('*') if p.is_file());resource.setrlimit(resource.RLIMIT_FSIZE,old)

def test_exact_512_conflicts_event_513_and_complete_readback(case):
 c=case;original=c.seed();new=c.j.admit_birth(c.payload(513));assert new['state']=='RESOLVED' and new['attempts']==0
 assert len(c.j.snapshot())==493
 while any(r['state']=='QUARANTINED' for r in c.j.snapshot()):assert len(c.j.reclaim_protected())<=20
 assert c.j.snapshot()==[new]
 for row in original:assert c.o.verified(row);assert c.o.lookup(row['id'])==row
 with sqlite3.connect(c.ops) as db:assert db.execute('SELECT used_records FROM birth_terminal_owner_v1').fetchone()[0]==512

def test_duplicate_after_reclamation_restart_no_attempt_or_identity_reset(case):
 c=case;row=c.seed(1)[0];c.j.reclaim_protected();p=dict(row['payload'],receive_utc_ns=999999999)
 for _ in range(4):
  j=c.restart();assert j.admit_birth(p)==row;assert j.snapshot()==[];assert c.o.verified(row)

def test_distinct_birth_identities_can_share_notification_signature(case):
 c=case;rows=c.rows(2);rows[1]['payload']['signature']=rows[0]['signature'];rows[1]=c.j._record(rows[1]['payload']);rows[1].update(state='QUARANTINED',error_code='CORE_AUTHORITY_CONFLICT')
 with c.j._lock():c.j._publish(rows)
 c.j.reclaim_protected();assert all(c.o.verified(r) for r in rows);assert rows[0]['id']!=rows[1]['id']

def test_conflicting_notification_after_transfer_fails_closed(case):
 c=case;row=c.seed(1)[0];c.j.reclaim_protected();p=dict(row['payload'],name='different')
 with pytest.raises(c.jm.JournalHold,match='IDENTITY_CONFLICT'):c.j.admit_birth(p)
 assert c.o.verified(row) and c.j.snapshot()==[]

def test_pending_exhausted_other_conflicts_are_not_reclaimed(case):
 c=case;rows=c.rows(4);rows[1].update(state='RESOLVED',error_code=None);rows[2].update(state='RETRY_EXHAUSTED',error_code='RETRY_CEILING');rows[3]['error_code']='BIRTH_PAYLOAD_CONFLICT'
 with c.j._lock():c.j._publish(rows)
 assert c.j.reclaim_protected()==[rows[0]['id']];assert c.j.snapshot()==rows[1:]

@pytest.mark.parametrize('point',['before_commit','after_commit','after_verified','journal_replace','after_journal_replace'])
def test_crash_and_repeated_restart_never_lose_conflicts(case,monkeypatch,point):
 c=case;rows=c.seed(20)
 if point=='before_commit':
  original=c.o._authority;calls=[0]
  def fail():
   calls[0]+=1
   if calls[0]==2:raise RuntimeError('CRASH')
   return original()
  monkeypatch.setattr(c.o,'_authority',fail)
 elif point=='after_commit':monkeypatch.setattr(c.o,'lookup',lambda *a,**k:(_ for _ in ()).throw(RuntimeError('CRASH')))
 elif point=='after_verified':
  original=c.o.preserve_batch
  def fail(rows):original(rows);raise RuntimeError('CRASH')
  monkeypatch.setattr(c.o,'preserve_batch',fail)
 elif point=='journal_replace':
  original=c.jm.os.replace
  def fail(a,b):
   if pathlib.Path(b)==c.j.path:raise RuntimeError('CRASH')
   return original(a,b)
  monkeypatch.setattr(c.jm.os,'replace',fail)
 else:
  monkeypatch.setattr(c.j,'_sync_dir',lambda:(_ for _ in ()).throw(RuntimeError('CRASH')))
 with pytest.raises(RuntimeError,match='CRASH'):c.j.reclaim_protected()
 monkeypatch.undo()
 # Reload fixture authority only; no provider or production source is imported.
 c.jm._paired_ack_generation=lambda:'birth-fixture-v2'
 for _ in range(2):
  j=c.restart();j.snapshot();j.reclaim_protected()
  for row in rows:assert row in j.snapshot() or j.terminal_owner.verified(row)
 assert all(c.o.verified(row) for row in rows)

@pytest.mark.parametrize('mode',['disk','quota','wrong_generation','corrupt_destination','missing_schema','kernel','file'])
def test_refusals_preserve_full_source(case,monkeypatch,mode):
 c=case;rows=c.seed();before=c.j.path.read_bytes()
 if mode=='disk':monkeypatch.setattr(c.tm.os,'statvfs',lambda p:types.SimpleNamespace(f_bavail=0,f_frsize=4096))
 elif mode=='quota':
  with sqlite3.connect(c.ops) as db:db.execute('UPDATE birth_terminal_owner_v1 SET used_records=8192 WHERE singleton=1')
 elif mode=='wrong_generation':c.generation[0]='wrong'
 elif mode=='missing_schema':
  with sqlite3.connect(c.ops) as db:db.execute('DROP TRIGGER birth_terminal_preserve_v1')
 elif mode=='kernel':monkeypatch.setattr(c.tm.resource,'getrlimit',lambda _: (resource.RLIM_INFINITY,resource.RLIM_INFINITY))
 elif mode=='file':monkeypatch.setattr(c.tm,'MAX_DB_BYTES',1)
 else:
  c.o.preserve_batch(rows[:1])
  with sqlite3.connect(c.ops) as db:
   db.execute('DROP TRIGGER birth_terminal_immutable_v1');db.execute("UPDATE birth_terminal_evidence_v1 SET evidence_hash='bad'");db.execute(next(s for s in c.tm.BirthTerminalEvidenceStore.DDL if s.startswith('CREATE TRIGGER birth_terminal_immutable')))
 with pytest.raises(c.tm.TerminalHold):c.j.admit_birth(c.payload(513))
 assert c.j.path.read_bytes()==before

def test_identity_lookup_and_schema_idempotency(case):
 c=case;c.o.initialize()
 with sqlite3.connect(c.ops) as db:
  plan=db.execute('EXPLAIN QUERY PLAN SELECT payload FROM birth_terminal_evidence_v1 WHERE identity=?',('x',)).fetchall()
  assert all('SEARCH' in p[3] and 'INDEX' in p[3] for p in plan)
  assert db.execute("SELECT count(*) FROM sqlite_master WHERE type='trigger' AND name LIKE 'birth_terminal_%'").fetchone()[0]==4

def test_two_thousand_sequential_admissions_stay_bounded(case):
 c=case;allrows=[]
 for n in range(2000):
  row=c.j.admit_birth(c.payload(n));c.j.quarantine(row['id'],'CORE_AUTHORITY_CONFLICT');row=c.j.snapshot()[-1];allrows.append(row)
  if n%20==19:c.j.reclaim_protected()
 assert c.j.snapshot()==[]
 for r in allrows:assert c.o.verified(r)
 with sqlite3.connect(c.ops) as db:
  n,used=db.execute('SELECT used_records,used_bytes FROM birth_terminal_owner_v1').fetchone();assert n==2000 and used<64000000
 assert c.ops.stat().st_size<8000000

def test_fresh_compact_destination_keeps_original_generation_and_dedup(case):
 c=case;rows=c.seed(20);c.j.reclaim_protected();dest=c.tmp/'fresh-ops.sqlite'
 src=sqlite3.connect(c.ops);dst=sqlite3.connect(dest)
 try:src.backup(dst,pages=20)
 finally:src.close();dst.close()
 o=c.owner(path=dest,gen='fresh-pair',validator=lambda p:'fresh-pair');o.initialize();c.j.terminal_owner=o
 for row in rows:assert o.verified(row);assert c.j.admit_birth(row['payload'])==row
 with sqlite3.connect(dest) as db:assert db.execute('SELECT DISTINCT transfer_generation FROM birth_terminal_evidence_v1').fetchall()==[('birth-fixture-v2',)]

@pytest.mark.parametrize('mode',['valid','flag0','missing_schema','wrong_pair'])
def test_real_listener_factory_requires_typed_owner_and_optin(case,monkeypatch,mode):
 c=case;tree=ast.parse((ROOT/'runtime/listener/src/core/pumpfun_curve_listener.py').read_text());node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_birth_admission_journal');ns={'os':os,'_birth_journal_readback':lambda r:False,'_listener_ops_db_path':lambda:str(c.ops),'_validate_active_pair':lambda:'wrong' if mode=='wrong_pair' else 'birth-fixture-v2'};exec(compile(ast.Module(body=[node],type_ignores=[]),'factory','exec'),ns)
 monkeypatch.setenv('DEV004_DURABLE_BIRTH_JOURNAL_ENABLED','1');monkeypatch.setenv('BIRTH_JOURNAL_PATH',str(c.j.path));monkeypatch.setenv('DEV004_BIRTH_TERMINAL_EVIDENCE_ENABLED','0' if mode=='flag0' else '1')
 if mode=='missing_schema':
  with sqlite3.connect(c.ops) as db:db.execute('DROP TRIGGER birth_terminal_preserve_v1')
 if mode=='wrong_pair':ns['_validate_active_pair']=lambda:(_ for _ in ()).throw(c.jm.JournalHold('WRONG_GENERATION'))
 if mode!='valid':
  with pytest.raises((c.jm.JournalHold,c.tm.TerminalHold)):ns['_birth_admission_journal']()
 else:
  j=ns['_birth_admission_journal']();assert j.terminal_owner is not None;c.seed();assert j.admit_birth(c.payload(513))['state']=='RESOLVED'


from tests.test_v2_terminal_recovery_config import case as base_case
from tests.test_v2_helius_recovery_config import case as recovery_case

@pytest.mark.parametrize('mode',['valid','flag0','legacy_source','missing_schema'])
def test_recovery_renderer_preserves_birth_terminal_authority(recovery_case,monkeypatch,mode):
 e=recovery_case;monkeypatch.setitem(sys.modules,'src.core.migration_signature_journal',e.jm)
 bm=load('birth_admission_journal');j=bm.BirthJournal(e.env['BIRTH_JOURNAL_PATH'])
 o=e.tm.BirthTerminalEvidenceStore(e.ops,j.path,generation='source',validate_record=j._validate,
  validate_pair=lambda p:e.g.validate_pair(core_path=e.env['DB_PATH'],ops_path=str(p)))
 o.initialize();flag='DEV004_BIRTH_TERMINAL_EVIDENCE_ENABLED';e.env[flag]='1';e.plan['planned_production_environment'][flag]='1'
 if mode=='flag0':e.plan['roles']['listener']['runtime_environment_overrides'][flag]='0'
 elif mode=='legacy_source':(e.package/'src/core/birth_admission_journal.py').write_text('# terminal unaware')
 elif mode=='missing_schema':
  with sqlite3.connect(e.ops) as db:db.execute('DROP TRIGGER birth_terminal_preserve_v1')
 before=e.live.read_bytes()
 if mode=='valid':
  e.ns['write_config']('v2');assert e.output_env()[flag]=='1';assert e.output_env()['FLEX_HELIUS_DENIAL_GATE_ENABLED']=='1'
  assert e.gate.read()['state']=='PAYMENT_DENIED'
 else:
  with pytest.raises(RuntimeError):e.ns['write_config']('v2')
  assert e.live.read_bytes()==before


def test_concurrent_duplicates_and_new_admission_do_not_reset_protection(case):
 from concurrent.futures import ThreadPoolExecutor
 c=case;row=c.seed(1)[0];c.j.reclaim_protected()
 def attempt(n):
  import time
  for _ in range(1000):
   try:return c.restart().admit_birth(row['payload'] if n%2 else c.payload(513))
   except c.jm.JournalHold as error:
    if str(error)!='OWNER_BUSY':raise
    time.sleep(.001)
  raise AssertionError('bounded fixture contention did not resolve')
 with ThreadPoolExecutor(max_workers=4) as pool:
  results=list(pool.map(attempt,range(32)))
 assert len(c.j.snapshot())==1 and c.j.snapshot()[0]['mint']=='mint-513'
 assert all(r==row for n,r in enumerate(results) if n%2);assert c.o.verified(row)
