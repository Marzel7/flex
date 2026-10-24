import ast,asyncio,builtins,hashlib,importlib.util,json,os,resource,socket,sqlite3,sys,threading,types
from pathlib import Path
import pytest
resource.setrlimit(resource.RLIMIT_FSIZE,(16000000,16000000));sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parents[2];NATIVE=sqlite3.dbapi2.connect
ACTIVE_DB_ROOT=None
def audit_db(event,args):
 if event=="sqlite3.connect" and ACTIVE_DB_ROOT is not None:
  raw=str(args[0]);plain=raw[5:].split("?",1)[0] if raw.startswith("file:") else raw
  if plain!=":memory:" and not Path(plain).resolve().is_relative_to(ACTIVE_DB_ROOT.resolve()):raise RuntimeError("OUTSIDE_DISPOSABLE_DB")
sys.addaudithook(audit_db)

def load(name,path,omit_reaper=False):
 spec=importlib.util.spec_from_file_location(name,path);mod=importlib.util.module_from_spec(spec);sys.modules[name]=mod
 if omit_reaper:
  t=ast.parse(Path(path).read_text());t.body=[n for n in t.body if not(isinstance(n,ast.Expr) and isinstance(n.value,ast.Call) and isinstance(n.value.func,ast.Name) and n.value.func.id=='_start_connection_reaper')];exec(compile(t,str(path),'exec'),mod.__dict__)
 else:spec.loader.exec_module(mod)
 return mod

@pytest.fixture(autouse=True)
def no_network(monkeypatch,tmp_path):
 global ACTIVE_DB_ROOT
 ACTIVE_DB_ROOT=tmp_path
 monkeypatch.setattr(socket.socket,'connect',lambda *a,**k:(_ for _ in ()).throw(AssertionError('NETWORK_FORBIDDEN')));monkeypatch.setattr(socket,'create_connection',lambda *a,**k:(_ for _ in ()).throw(AssertionError('NETWORK_FORBIDDEN')))
 try:yield
 finally:ACTIVE_DB_ROOT=None

@pytest.fixture
def env(tmp_path,monkeypatch):
 for name in ['src','src.core','src.utils']:
  pkg=types.ModuleType(name);pkg.__path__=[];monkeypatch.setitem(sys.modules,name,pkg)
 for name in ['src.core.database_write_service','src.utils.db_locking','src.core.migration_signature_journal','src.core.birth_admission_journal','src.core.paired_binding_guard']:
  monkeypatch.delitem(sys.modules,name,raising=False)
 monkeypatch.setattr(sqlite3,'connect',NATIVE)
 monkeypatch.setenv('DB_PATH',str(tmp_path/'core.sqlite'));monkeypatch.setenv('WT_OPS_DB_PATH',str(tmp_path/'ops.sqlite'));monkeypatch.setenv('DB_WRITE_SERIALIZE','1')
 for key in ['DEV004_PAIRED_BINDING_REQUIRED','FLEX_DB_PAIR_MANIFEST','FLEX_DB_PAIR_AUTHORITY','FLEX_DB_PAIR_ROLE','FLEX_DB_PAIR_OWNER']:monkeypatch.delenv(key,raising=False)
 for k in ['DB_SQLITE_LIFECYCLE_DIAGNOSTICS_PATH','DB_CONNECTION_LIFECYCLE_DIAGNOSTICS_PATH','DB_CONNECTION_FAILURE_DIAGNOSTICS_PATH']:monkeypatch.delenv(k,raising=False)
 g=load('src.core.paired_binding_guard',ROOT/'runtime/listener/src/core/paired_binding_guard.py');monkeypatch.setitem(sys.modules,'src.core.paired_binding_guard',g)
 m=load('src.core.migration_signature_journal',ROOT/'runtime/listener/src/core/migration_signature_journal.py');b=load('src.core.birth_admission_journal',ROOT/'runtime/listener/src/core/birth_admission_journal.py')
 # Keep dynamic module entries scoped to this test.
 monkeypatch.setitem(sys.modules,'src.core.migration_signature_journal',m);monkeypatch.setitem(sys.modules,'src.core.birth_admission_journal',b)
 return types.SimpleNamespace(m=m,b=b,path=tmp_path,patch=monkeypatch)

def setup_writer(env,component='listener'):
 w=load('src.core.database_write_service',ROOT/f'runtime/{component}/src/core/database_write_service.py');env.patch.setitem(sys.modules,'src.core.database_write_service',w)
 d=load('src.utils.db_locking',ROOT/f'runtime/{component}/src/utils/db_locking.py',True);env.patch.setitem(sys.modules,'src.utils.db_locking',d);sys.modules['src.utils'].db_locking=d
 # Shared source registries are real; only optional background diagnostic threads omitted.
 return w,d

def database(path):
 c=NATIVE(path);c.execute('PRAGMA max_page_count=256');c.execute('CREATE TABLE evidence(id TEXT PRIMARY KEY)');c.commit();c.close()

def tracked(d,path,kind='tracked',autocommit=False):
 cls=d.TrackedConnection if kind=='tracked' else d._EmergencyOpsRecoveryConnection
 c=NATIVE(path,factory=cls,isolation_level=None if autocommit else '');c._db_path=str(path);c._db_caller='test';return c

def payload(sig='sig',mint='mint',creator='creator',time=1):
 return {'_birth_retry_schema':1,'mint':mint,'signature':sig,'traderPublicKey':creator,'bondingCurveKey':'curve','symbol':'T','name':'Token','receive_utc_ns':time,'provenance':'PUMPPORTAL_NEW_TOKEN'}

def birthjournal(env):
 j=env.b.BirthJournal(env.path/'stable-birth.jsonl');j.initialize();return j

@pytest.mark.parametrize('component',['listener','creator_resolution','walkback'])
@pytest.mark.parametrize('kind',['tracked','emergency'])
def test_stale_connections_and_new_leases_rejected(env,component,kind):
 w,d=setup_writer(env,component);p=env.path/'old.sqlite';database(p);c=tracked(d,p,kind)
 c.execute('INSERT INTO evidence VALUES (?)',('before',));c.commit()
 w.retire_database('fixture',p,generation='frozen-v1',timeout=.2)
 with pytest.raises(w.DatabaseRetiredError):c.execute('INSERT INTO evidence VALUES (?)',('after',))
 with pytest.raises(w.DatabaseRetiredError):c.cursor().execute('INSERT INTO evidence VALUES (?)',('cursor-after',))
 with pytest.raises(w.DatabaseRetiredError):c.executescript("INSERT INTO evidence VALUES ('script-after');")
 with pytest.raises(w.DatabaseRetiredError):w.acquire_write_lease('fixture',str(p),'stale','after-release',timeout=.1)
 assert c.execute('SELECT id FROM evidence').fetchall()==[('before',)];c.close()
 assert w.retire_database('fixture',p,generation='frozen-v1')=={'version':1,'path':str(p.resolve()),'generation':'frozen-v1','retired':True}

@pytest.mark.parametrize('component',['listener','creator_resolution','walkback'])
@pytest.mark.parametrize('sql',["/* comment */ INSERT INTO evidence VALUES ('x')","WITH x AS (SELECT 1) INSERT INTO evidence SELECT 'x' FROM x"])
def test_sql_spelling_does_not_bypass_autocommit(env,component,sql):
 w,d=setup_writer(env,component);p=env.path/'old.sqlite';database(p);c=tracked(d,p,autocommit=True);w.retire_database('fixture',p,generation='v1',timeout=.2)
 with pytest.raises(w.DatabaseRetiredError):c.execute(sql)
 assert c.execute('SELECT * FROM evidence').fetchall()==[];c.close()

@pytest.mark.parametrize('component',['listener','creator_resolution','walkback'])
def test_transaction_started_before_ambiguous_marker_cannot_commit(env,component):
 w,d=setup_writer(env,component);p=env.path/'old.sqlite';database(p);c=tracked(d,p)
 # Emulate interrupted marker publication while a cursor-written tx remains.
 c.execute("INSERT INTO evidence VALUES ('uncommitted')");Path(str(p)+'.retired').write_bytes(b'{')
 with pytest.raises(w.DatabaseRetiredError):c.commit()
 assert not c.in_transaction;c.close()
 with NATIVE(p) as r:assert r.execute('SELECT * FROM evidence').fetchall()==[]

@pytest.mark.parametrize('component',['listener','creator_resolution','walkback'])
def test_service_connection_cursor_and_commit_guard(env,component):
 w,d=setup_writer(env,component);p=env.path/'old.sqlite';database(p)
 c=NATIVE(p,factory=w._ServiceConnection);c._db_path=str(p)
 w.retire_database('fixture',p,generation='v1',timeout=.2)
 with pytest.raises(w.DatabaseRetiredError):c.execute("INSERT INTO evidence VALUES ('x')")
 with pytest.raises(w.DatabaseRetiredError):c.cursor().execute("INSERT INTO evidence VALUES ('x')")
 with pytest.raises(w.DatabaseRetiredError):c.service_commit()
 c.service_rollback();c.close()

def test_managed_service_submit_after_retirement(env):
 w,d=setup_writer(env);p=env.path/'old.sqlite';database(p);service=w.DatabaseWriteService(_test_sqlite_timeout=.1);service.register_database('fixture',str(p));service.submit('fixture','before',lambda c:c.execute("INSERT INTO evidence VALUES ('before')"))
 w.retire_database('retirement',p,generation='v1',timeout=.2)
 with pytest.raises(w.DatabaseRetiredError):service.submit('fixture','after',lambda c:c.execute("INSERT INTO evidence VALUES ('after')"))
 with NATIVE(p) as c:assert c.execute('SELECT id FROM evidence').fetchall()==[('before',)]

def test_active_native_writer_blocks_retirement_before_marker(env):
 w,d=setup_writer(env);p=env.path/'old.sqlite';database(p);c=NATIVE(p);c.execute("INSERT INTO evidence VALUES ('active')")
 with pytest.raises(sqlite3.OperationalError):w.retire_database('fixture',p,generation='v1',timeout=.05)
 assert not Path(str(p)+'.retired').exists();c.rollback();c.close();w.retire_database('fixture',p,generation='v1',timeout=.2)

def test_held_lease_blocks_retirement_until_release(env):
 w,d=setup_writer(env);p=env.path/'old.sqlite';database(p);lease=w.acquire_write_lease('old',str(p),'tx','old-owner',timeout=.1);result=[]
 def retire():
  try:w.retire_database('fixture',p,generation='v1',timeout=.05)
  except w.CrossProcessDatabaseWriteTimeout:result.append('blocked')
 t=threading.Thread(target=retire);t.start();t.join(2);assert result==['blocked'];assert not Path(str(p)+'.retired').exists();w.release_write_lease(lease);w.retire_database('fixture',p,generation='v1',timeout=.2)
 with pytest.raises(w.DatabaseRetiredError):w.acquire_write_lease('old',str(p),'new','stale',timeout=.1)

@pytest.mark.parametrize('fault',['short','fsync','directory'])
def test_interrupted_retirement_fails_closed(env,monkeypatch,fault):
 w,d=setup_writer(env);p=env.path/'old.sqlite';database(p)
 real_write=os.write;real_sync=os.fsync
 def write(fd,data):
  if b'"retired":true' in data:
   if fault=='short':return real_write(fd,data[:2])
  return real_write(fd,data)
 def sync(fd):
  import stat
  if fault=='directory' and stat.S_ISDIR(os.fstat(fd).st_mode) and Path(str(p)+'.retired').exists():raise OSError('directory failure')
  if fault=='fsync' and os.fstat(fd).st_size>0:
   try:retired=json.loads(Path(str(p)+'.retired').read_text()).get('retired')
   except Exception:retired=False
   if retired:raise OSError('file sync failure')
  return real_sync(fd)
 monkeypatch.setattr(os,'write',write);monkeypatch.setattr(os,'fsync',sync)
 with pytest.raises((w.DatabaseRetiredError,OSError)):w.retire_database('fixture',p,generation='v1',timeout=.2)
 assert Path(str(p)+'.retired').exists()
 with pytest.raises(w.DatabaseRetiredError):w.acquire_write_lease('stale',str(p),'x','stale')


def test_birth_independent_admission_and_duplicate(env):
 j=birthjournal(env);r=j.admit_birth(payload());duplicate=j.admit_birth(payload(time=2));assert r['id']==duplicate['id'];assert len(j.snapshot())==1 and j.snapshot()[0]['received_at']==1;assert not (env.path/'core.sqlite').exists()
 reopened=env.b.BirthJournal(j.path);assert reopened.pending()[0]['payload']==payload()

def test_birth_retry_state_quarantine_and_ack_retained(env):
 j=birthjournal(env);r=j.admit_birth(payload());assert j.begin_attempt(r['id']);j.quarantine(r['id'],'CORE_AUTHORITY_CONFLICT');assert not j.pending();assert not j.begin_attempt(r['id']);assert j.snapshot()[0]['attempts']==1

def test_birth_retry_ceiling_survives_reopen(env):
 j=birthjournal(env);r=j.admit_birth(payload())
 for _ in range(3):assert env.b.BirthJournal(j.path).begin_attempt(r['id'])
 assert not j.begin_attempt(r['id']);assert j.snapshot()[0]['state']=='RETRY_EXHAUSTED';j.admit_birth(payload(time=2));assert not j.pending()

def test_birth_payload_conflict_retained(env):
 j=birthjournal(env);j.admit_birth(payload());j.admit_birth(payload()|{'bondingCurveKey':'other'});r=j.snapshot()[0];assert r['state']=='QUARANTINED' and r['payload']['bondingCurveKey']=='curve' and r['conflicting_payload']['bondingCurveKey']=='other'

def listener_functions(scope,names):
 tree=ast.parse((ROOT/'runtime/listener/src/core/pumpfun_curve_listener.py').read_text())
 if '_validate_active_pair' not in scope:
  def validate_active():
   from src.core.paired_binding_guard import validate_pair
   return validate_pair(core_path=scope.get('DB_PATH'), ops_path=scope.get('_listener_ops_db_path',lambda:os.environ.get('WT_OPS_DB_PATH'))(),purpose='active')
  scope['_validate_active_pair']=validate_active
 for name in names:
  n=next(n for n in ast.walk(tree) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name==name);exec(compile(ast.Module(body=[n],type_ignores=[]),'actual-listener-interface','exec'),scope)
 return scope

def birth_scope(env,j,p):
 return {'os':os,'json':json,'asyncio':asyncio,'time':__import__('time'),'DB_PATH':str(p),'_birth_admission_journal':lambda:j,'log_print':lambda *a,**kw:None}

def test_birth_arrival_after_retirement_has_no_V1_dependency(env):
 w,d=setup_writer(env);p=env.path/'core.sqlite';database(p);w.retire_database('core',p,generation='v1',timeout=.2);j=birthjournal(env)
 scope=birth_scope(env,j,p);scope.update({'_birth_journal_readback':lambda r:False,'_birth_journal_canonical':lambda r:None});listener_functions(scope,['_persist_durable_birth'])
 class A:
  async def _insert_bonding_curve_token(self,*a,**kw):raise AssertionError('V1_WRITE_ATTEMPT')
 r=j.admit_birth(payload());assert asyncio.run(scope['_persist_durable_birth'](A(),r)) is False
 assert j.snapshot()[0]['attempts']==0 and len(env.b.BirthJournal(j.path).pending())==1

def test_birth_retry_crash_after_commit_no_replay(env):
 w,d=setup_writer(env);p=env.path/'core.sqlite'
 with NATIVE(p) as c:c.execute('CREATE TABLE token_analysis(mint TEXT PRIMARY KEY,earliest_tx_creator TEXT,pf_ws_creator TEXT,create_tx_signature TEXT)')
 j=birthjournal(env);scope=birth_scope(env,j,p);listener_functions(scope,['_birth_journal_canonical','_birth_journal_readback','_persist_durable_birth']);calls=[]
 class Crash(BaseException):pass
 class A:
  async def _insert_bonding_curve_token(self,mint,creator,*a,**kw):
   calls.append(mint)
   with NATIVE(p) as c:c.execute('INSERT INTO token_analysis VALUES(?,?,?,?)',(mint,creator,creator,kw['create_tx_signature']))
   raise Crash()
 r=j.admit_birth(payload())
 with pytest.raises(Crash):asyncio.run(scope['_persist_durable_birth'](A(),r))
 assert j.snapshot()[0]['state']=='RESOLVED'
 assert asyncio.run(scope['_persist_durable_birth'](A(),j.pending()[0])) is True
 assert calls==['mint'] and j.snapshot()[0]['state']=='ACKED';assert j.path.exists()

def test_birth_failed_persistence_does_not_ack_or_use_Core_retry(env):
 w,d=setup_writer(env);p=env.path/'core.sqlite';database(p);j=birthjournal(env);scope=birth_scope(env,j,p);scope.update({'_birth_journal_readback':lambda r:False,'_birth_journal_canonical':lambda r:None});listener_functions(scope,['_persist_durable_birth'])
 class A:
  async def _insert_bonding_curve_token(self,*a,**kw):return False
 assert asyncio.run(scope['_persist_durable_birth'](A(),j.admit_birth(payload()))) is False
 assert j.snapshot()[0]['attempts']==1 and j.snapshot()[0]['ack'] is None
 with NATIVE(p) as c:assert c.execute("SELECT name FROM sqlite_master WHERE name='webhook_birth_queue'").fetchone() is None

def test_birth_conflicting_canonical_quarantined(env):
 w,d=setup_writer(env);p=env.path/'core.sqlite'
 with NATIVE(p) as c:c.execute('CREATE TABLE token_analysis(mint TEXT PRIMARY KEY,earliest_tx_creator TEXT,pf_ws_creator TEXT,create_tx_signature TEXT)');c.execute("INSERT INTO token_analysis VALUES('mint','other','other','other-sig')")
 j=birthjournal(env);scope=birth_scope(env,j,p);listener_functions(scope,['_birth_journal_canonical','_birth_journal_readback','_persist_durable_birth']);r=j.admit_birth(payload());assert not asyncio.run(scope['_persist_durable_birth'](object(),r));assert j.snapshot()[0]['state']=='QUARANTINED'

def test_unreadable_retirement_state_refuses_write(env,monkeypatch):
 w,d=setup_writer(env);p=env.path/'old.sqlite';database(p);real=os.lstat
 def fail(path,*a,**kw):
  if str(path)==str(p)+'.retired':raise PermissionError('synthetic metadata error')
  return real(path,*a,**kw)
 monkeypatch.setattr(os,'lstat',fail)
 with pytest.raises(w.DatabaseRetiredError,match='UNAVAILABLE'):w.assert_database_writable(p)

def test_actual_birth_frame_durable_before_receipt_with_retired_Core(env):
 w,d=setup_writer(env);p=env.path/'core.sqlite';database(p);w.retire_database('old',p,generation='v1',timeout=.2);j=birthjournal(env);order=[]
 scope=birth_scope(env,j,p);scope.update({'_PP_BIRTH_RETRY_MAX_BYTES':4096})
 listener_functions(scope,['_compact_pumpportal_birth_retry','_birth_journal_canonical','_birth_journal_readback','_persist_durable_birth','_record_pp_birth_outcome','_handle_pumpportal_create_frame'])
 class Audit:
  compact_path=True
  def record(self,**kw):assert len(j.snapshot())==1;order.append('receipt-after-durable');return 'receipt'
  def record_outcome(self,*a,**kw):order.append(kw['outcome'])
 class A:
  _persist_durable_birth=scope['_persist_durable_birth'];_record_pp_birth_outcome=scope['_record_pp_birth_outcome']
  _eb_birth_audit=Audit();_pumpfun_delivery_latency=types.SimpleNamespace(observe_pumpportal=lambda **kw:None);_byzantine_birth_signal=None;_portal_vsol={};completed_launches=set();seen_mints=set()
  async def _insert_bonding_curve_token(self,*a,**kw):raise AssertionError('RETIRED_V1_WRITE')
  async def _ensure_pf_ws_creator(self,*a,**kw):raise AssertionError('NO_CANONICAL_COMMIT')
 class WS:
  async def send(self,*a):raise AssertionError('NO_WS')
 async def run():await scope['_handle_pumpportal_create_frame'](A(),{'traderPublicKey':'creator','symbol':'T','name':'Token'},signature='sig',mint='mint',receive_utc_ns=1,receive_monotonic_ns=1,tracked_trade_mints=set(),migration_sol_threshold=1000,ws=WS())
 asyncio.run(run());assert order==['receipt-after-durable','RETRY_PENDING'];assert j.snapshot()[0]['attempts']==0;assert len(j.pending())==1

def test_failure_before_birth_admission_never_acknowledges(env,monkeypatch):
 j=birthjournal(env);scope=birth_scope(env,j,env.path/'core.sqlite');scope['_PP_BIRTH_RETRY_MAX_BYTES']=4096;listener_functions(scope,['_compact_pumpportal_birth_retry','_handle_pumpportal_create_frame'])
 monkeypatch.setattr(j,'admit_birth',lambda p:(_ for _ in ()).throw(env.m.JournalHold('DISK_FLOOR')))
 class A:
  _eb_birth_audit=types.SimpleNamespace(record=lambda **kw:(_ for _ in ()).throw(AssertionError('EARLY_RECEIPT_ACK')))
 async def run():await scope['_handle_pumpportal_create_frame'](A(),{'traderPublicKey':'creator'},signature='sig',mint='mint',receive_utc_ns=1,receive_monotonic_ns=1,tracked_trade_mints=set(),migration_sol_threshold=1000,ws=None)
 with pytest.raises(env.m.JournalHold):asyncio.run(run())
 assert j.snapshot()==[]

def test_birth_capacity_and_partial_state_fail_closed(env,monkeypatch):
 j=birthjournal(env);r=j.admit_birth(payload());original=j.path.read_bytes();write=os.write
 def short(fd,data):return write(fd,data[:max(1,len(data)//2)])
 monkeypatch.setattr(os,'write',short)
 with pytest.raises(env.m.JournalHold):j.admit_birth(payload(sig='two',mint='two'))
 assert j.path.read_bytes()==original and j.tmp.exists()
 with pytest.raises(env.m.JournalHold):env.b.BirthJournal(j.path).pending()

def test_birth_file_fsync_failure_retains_original(env,monkeypatch):
 import stat
 j=birthjournal(env);before=j.path.read_bytes();sync=os.fsync
 def fail(fd):
  if stat.S_ISREG(os.fstat(fd).st_mode) and os.fstat(fd).st_size>0:raise OSError('synthetic file sync')
  return sync(fd)
 monkeypatch.setattr(os,'fsync',fail)
 with pytest.raises(OSError):j.admit_birth(payload())
 assert j.path.read_bytes()==before and j.tmp.exists()

def test_birth_legacy_compatible_evidence(env):
 j=birthjournal(env);legacy={'mint':'mint','creator':'creator','created_at':'1','bonding_curve_pda':'curve','create_tx_signature':'sig','symbol':'T','name':'Token','received_at':1,'last_error':'fixture','birth_evidence_source':'PUMPPORTAL_NEW_TOKEN'}
 j.path.write_text(json.dumps(legacy)+'\n');r=j.pending()[0];assert r['legacy_birth']==legacy and r['payload']['receive_utc_ns']==1_000_000_000
 j.admit_birth(payload(sig='other',mint='other'));assert j.snapshot()[0]['legacy_birth']==legacy

def test_birth_unqualified_legacy_never_promoted(env):
 j=birthjournal(env);j.path.write_text(json.dumps({'mint':'mint','create_tx_signature':'sig','received_at':1})+'\n');before=j.path.read_bytes()
 with pytest.raises(env.m.JournalHold,match='UNQUALIFIED'):j.admit_birth(payload())
 assert j.path.read_bytes()==before

def test_legacy_bulk_importer_cannot_delete_versioned_births(env):
 j=birthjournal(env);j.admit_birth(payload());before=j.path.read_bytes();scope={'os':os,'_fallback_file_path':lambda:str(j.path)};listener_functions(scope,['_reconcile_fallback_file_into_queue']);assert scope['_reconcile_fallback_file_into_queue']()==0 and j.path.read_bytes()==before

def test_birth_batch_limit_and_disk_floor(env,monkeypatch):
 j=birthjournal(env)
 for i in range(22):j.admit_birth(payload(sig=str(i),mint=str(i)))
 assert len(j.pending())==20
 monkeypatch.setattr(os,'statvfs',lambda p:types.SimpleNamespace(f_bavail=0,f_frsize=4096));before=j.path.read_bytes()
 with pytest.raises(env.m.JournalHold,match='DISK_FLOOR'):j.admit_birth(payload(sig='new',mint='new'))
 assert j.path.read_bytes()==before

def test_retirement_survives_real_process_restart(env):
 import subprocess
 w,d=setup_writer(env);p=env.path/'old.sqlite';database(p);stale=tracked(d,p)
 code='''import importlib.util,sys,os,resource
resource.setrlimit(resource.RLIMIT_FSIZE,(16000000,16000000))
s=importlib.util.spec_from_file_location('w',sys.argv[1]);w=importlib.util.module_from_spec(s);sys.modules['w']=w;s.loader.exec_module(w)
w.retire_database('child',sys.argv[2],generation='frozen-v1',timeout=.2)
os._exit(17)
'''
 result=subprocess.run([sys.executable,'-B','-c',code,str(ROOT/'runtime/listener/src/core/database_write_service.py'),str(p)],cwd=env.path,env={'PATH':os.environ.get('PATH',''),'PYTHONDONTWRITEBYTECODE':'1'},capture_output=True,timeout=10)
 assert result.returncode==17,result.stderr
 with pytest.raises(w.DatabaseRetiredError):stale.execute("INSERT INTO evidence VALUES ('after-restart')")
 stale.close()
 with pytest.raises(w.DatabaseRetiredError):w.acquire_write_lease('new',str(p),'new','restart')

@pytest.mark.parametrize('stage',['admitted','attempted'])
def test_durable_birth_survives_real_child_exit(env,stage):
 import subprocess
 j=birthjournal(env)
 code='''import importlib.util,sys,os,types,json,resource
resource.setrlimit(resource.RLIMIT_FSIZE,(16000000,16000000))
for name in ['src','src.core']:
 p=types.ModuleType(name);p.__path__=[];sys.modules[name]=p
for name,path in [('src.core.migration_signature_journal',sys.argv[1]),('src.core.birth_admission_journal',sys.argv[2])]:
 s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);sys.modules[name]=m;s.loader.exec_module(m)
j=m.BirthJournal(sys.argv[3]);r=j.admit_birth(json.loads(sys.argv[4]))
if sys.argv[5]=='attempted':j.begin_attempt(r['id'])
os._exit(17)
'''
 result=subprocess.run([sys.executable,'-B','-c',code,str(ROOT/'runtime/listener/src/core/migration_signature_journal.py'),str(ROOT/'runtime/listener/src/core/birth_admission_journal.py'),str(j.path),json.dumps(payload()),stage],cwd=env.path,env={'PATH':os.environ.get('PATH',''),'PYTHONDONTWRITEBYTECODE':'1'},capture_output=True,timeout=10)
 assert result.returncode==17,result.stderr;r=env.b.BirthJournal(j.path).pending()[0];assert r['payload']==payload() and r['attempts']==(1 if stage=='attempted' else 0)

@pytest.mark.parametrize('component',['listener','creator_resolution','walkback'])
def test_cached_statement_rejected_after_retirement(env,component):
 w,d=setup_writer(env,component);p=env.path/'old.sqlite';database(p);c=tracked(d,p,autocommit=True);sql='INSERT INTO evidence VALUES (?)';c.execute(sql,('before',));w.retire_database('old',p,generation='v1',timeout=.2)
 with pytest.raises(w.DatabaseRetiredError):c.execute(sql,('after',))
 assert c.execute('SELECT * FROM evidence').fetchall()==[('before',)];c.close()

def test_service_context_cannot_commit_after_marker(env):
 w,d=setup_writer(env);p=env.path/'old.sqlite';database(p);c=NATIVE(p,factory=w._ServiceConnection);c._db_path=str(p)
 c.execute("INSERT INTO evidence VALUES ('uncommitted')");Path(str(p)+'.retired').write_bytes(b'{')
 with pytest.raises(w.DatabaseRetiredError):
  with c:pass
 c.close()
 with NATIVE(p) as r:assert r.execute('SELECT * FROM evidence').fetchall()==[]

def test_service_nested_helper_context_preserves_owner_atomicity(env):
 w,d=setup_writer(env);p=env.path/'old.sqlite';database(p);service=w.DatabaseWriteService(_test_sqlite_timeout=.1)
 def callback(c):
  with c:c.execute("INSERT INTO evidence VALUES ('must-roll-back')")
  raise RuntimeError('later callback failure')
 service.register_database('fixture',str(p))
 with pytest.raises(RuntimeError):service.submit('fixture','callback',callback)
 with NATIVE(p) as c:assert c.execute('SELECT * FROM evidence').fetchall()==[]

def test_birth_pending_retries_then_single_commit(env):
 w,d=setup_writer(env);p=env.path/'core.sqlite'
 with NATIVE(p) as c:c.execute('CREATE TABLE token_analysis(mint TEXT PRIMARY KEY,earliest_tx_creator TEXT,pf_ws_creator TEXT,create_tx_signature TEXT)')
 j=birthjournal(env);scope=birth_scope(env,j,p);listener_functions(scope,['_birth_journal_canonical','_birth_journal_readback','_persist_durable_birth']);attempts=[]
 class A:
  async def _insert_bonding_curve_token(self,mint,creator,*a,**kw):
   attempts.append(mint)
   if len(attempts)<3:return False
   with NATIVE(p) as c:c.execute('INSERT INTO token_analysis VALUES(?,?,?,?)',(mint,creator,creator,kw['create_tx_signature']))
   return True
 a=A();j.admit_birth(payload())
 for expected in [False,False,True]:assert asyncio.run(scope['_persist_durable_birth'](a,env.b.BirthJournal(j.path).pending()[0])) is expected
 assert j.snapshot()[0]['state']=='ACKED' and j.snapshot()[0]['attempts']==3
 assert asyncio.run(scope['_persist_durable_birth'](a,j.snapshot()[0]));assert len(attempts)==3
 with NATIVE(p) as c:assert c.execute('SELECT count(*) FROM token_analysis').fetchone()[0]==1

def test_birth_stable_path_not_DB_dependent(env,monkeypatch):
 j=birthjournal(env);j.admit_birth(payload());scope={'os':os};listener_functions(scope,['_birth_admission_journal'])
 monkeypatch.setenv('DEV004_DURABLE_BIRTH_JOURNAL_ENABLED','1');monkeypatch.setenv('BIRTH_JOURNAL_PATH',str(j.path));monkeypatch.setenv('DB_PATH','not-a-production-database')
 assert scope['_birth_admission_journal']().pending()[0]['payload']==payload()

def test_birth_retention_capacity_no_eviction(env,monkeypatch):
 j=birthjournal(env);j.admit_birth(payload());monkeypatch.setattr(env.m,'MAX_RECORDS',1);before=j.path.read_bytes()
 with pytest.raises(env.m.JournalHold,match='CAPACITY'):j.admit_birth(payload(sig='other',mint='other'))
 assert j.path.read_bytes()==before

@pytest.mark.parametrize('component',['listener','creator_resolution','walkback'])
@pytest.mark.parametrize('route',['execute','cursor','script'])
def test_native_statement_wait_race_cannot_commit_after_retirement(env,monkeypatch,component,route):
 w,d=setup_writer(env,component);p=env.path/'old.sqlite';database(p);c=tracked(d,p,'emergency',autocommit=True)
 original=d._assert_not_retired;seen=[0]
 def check_then_retire(connection):
  seen[0]+=1
  original(connection)
  if seen[0]==2:
   w.retire_database('racing-retirement',p,generation='v1',timeout=.2)
 monkeypatch.setattr(d,'_assert_not_retired',check_then_retire)
 with pytest.raises((w.DatabaseRetiredError,sqlite3.OperationalError)):
  if route=='execute':c.execute("INSERT INTO evidence VALUES ('after')")
  elif route=='cursor':c.cursor().execute("INSERT INTO evidence VALUES ('after')")
  else:c.executescript("INSERT INTO evidence VALUES ('after');")
 assert seen[0]>=3
 c.close()
 with NATIVE(p) as r:assert r.execute('SELECT * FROM evidence').fetchall()==[]

def test_native_progress_callback_preserved(env):
 w,d=setup_writer(env);p=env.path/'old.sqlite';database(p);c=tracked(d,p,'emergency',autocommit=True);calls=[]
 c.set_progress_handler(lambda:calls.append(1) or 0,1);c.execute("INSERT INTO evidence VALUES ('before')");assert calls;calls.clear();c.execute('SELECT * FROM evidence').fetchall();assert calls
 c.set_progress_handler(lambda:1,1)
 with pytest.raises(sqlite3.OperationalError):c.execute("INSERT INTO evidence VALUES ('blocked-by-user-timeout')")
 c.set_progress_handler(None,0);assert c.execute('SELECT * FROM evidence').fetchall()==[('before',)];c.close()

@pytest.mark.parametrize('route',['execute','cursor'])
def test_serialization_disabled_still_checks_retirement_at_execution(env,monkeypatch,route):
 w,d=setup_writer(env);monkeypatch.setattr(d,'_DB_WRITE_SERIALIZE',False);p=env.path/'old.sqlite';database(p);c=tracked(d,p,autocommit=True);original=d._assert_not_retired;seen=[0]
 def check_then_retire(connection):
  seen[0]+=1
  original(connection)
  if seen[0]==2:w.retire_database('retire',p,generation='v1',timeout=.2)
 monkeypatch.setattr(d,'_assert_not_retired',check_then_retire)
 with pytest.raises((w.DatabaseRetiredError,sqlite3.OperationalError)):
  if route=='execute':c.execute("INSERT INTO evidence VALUES ('after')")
  else:c.cursor().execute("INSERT INTO evidence VALUES ('after')")
 assert seen[0]>=3
 c.close()
 with NATIVE(p) as r:assert r.execute('SELECT * FROM evidence').fetchall()==[]


@pytest.mark.parametrize('numeric',[0,'malformed'])
def test_birth_flag_off_preserves_existing_persistence(env,numeric):
 calls=[];scope={'os':os,'json':json,'asyncio':asyncio,'time':__import__('time'),'_birth_admission_journal':lambda:None,'log_print':lambda *a,**kw:None}
 listener_functions(scope,['_handle_pumpportal_create_frame'])
 class A:
  _eb_birth_audit=types.SimpleNamespace(compact_path=True,record=lambda **kw:'receipt');_pumpfun_delivery_latency=types.SimpleNamespace(observe_pumpportal=lambda **kw:None);_byzantine_birth_signal=None
  def __init__(self):self._portal_vsol={};self.completed_launches=set();self.seen_mints=set()
  async def _insert_bonding_curve_token(self,*a,**kw):calls.append(('canonical',a,kw));return True
  async def _record_pp_birth_outcome(self,*a):calls.append(('receipt',a))
  async def _ensure_pf_ws_creator(self,*a,**kw):calls.append(('creator',a))
 class WS:
  async def send(self,*a):raise AssertionError('NO_WS')
 actor=A()
 async def run():
  await scope['_handle_pumpportal_create_frame'](actor,{'traderPublicKey':'creator','vSolInBondingCurve':numeric,'marketCapSol':numeric},signature='sig',mint='mint',receive_utc_ns=1,receive_monotonic_ns=1,tracked_trade_mints=set(),migration_sol_threshold=1000,ws=WS());await asyncio.sleep(0)
 asyncio.run(run());assert [x[0] for x in calls]==['canonical','receipt','creator'];assert calls[1][1]==('receipt','mint',True)
