"""Existing canonical birth SQL and bounded protected lifecycle ledger only."""
import ast,contextlib,dataclasses,hashlib,importlib.util,json,os,resource,socket,sqlite3,sys,types
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]

def load(path,name):
 spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);sys.modules[name]=m;spec.loader.exec_module(m);return m

@pytest.fixture(autouse=True)
def bounded_provider_free(monkeypatch,tmp_path):
 def forbidden(*a,**k):raise AssertionError('NETWORK_FORBIDDEN')
 monkeypatch.setattr(socket.socket,'connect',forbidden);monkeypatch.setattr(socket,'create_connection',forbidden)
 old=resource.getrlimit(resource.RLIMIT_FSIZE);resource.setrlimit(resource.RLIMIT_FSIZE,(499000000,old[1]))
 yield
 assert all(p.stat().st_size<500000000 for p in tmp_path.rglob('*') if p.is_file())
 resource.setrlimit(resource.RLIMIT_FSIZE,old)

@pytest.fixture
def birth(tmp_path,monkeypatch):
 # Accepted schema definition, actual nested writer AST, actual handoff producer.
 schema=json.loads((ROOT/'docs/dev/lightweight_dev004_first_v2/production_profile_bootstrap/accepted_fixture_schema.json').read_text())
 db=tmp_path/'core.sqlite'
 with sqlite3.connect(db) as c:c.execute(next(v['sql'] for v in schema['core'] if v['name']=='token_analysis'))
 for name in ['src','src.ops']:
  p=types.ModuleType(name);p.__path__=[str(ROOT/'runtime/listener'/name.replace('.','/'))];monkeypatch.setitem(sys.modules,name,p)
 monkeypatch.delitem(sys.modules,'src.ops.walkback_create_handoff',raising=False)
 source=(ROOT/'runtime/listener/src/core/pumpfun_curve_listener.py').read_text()
 tree=ast.parse(source);outer=next(n for n in ast.walk(tree) if isinstance(n,ast.AsyncFunctionDef) and n.name=='_insert_bonding_curve_token')
 inner=next(n for n in outer.body if isinstance(n,ast.FunctionDef) and n.name=='_do_write')
 @contextlib.contextmanager
 def connect(*a,**kw):
  c=sqlite3.connect(db)
  try:yield c
  finally:c.close()
 ns={'managed_db_connect':connect,'DB_PATH':str(db),'PRIORITY_P0_CRITICAL_INGESTION':0,'_listener_ops_db_path':lambda:str(tmp_path/'ops.sqlite')}
 exec(compile(ast.Module(body=[inner],type_ignores=[]),'<actual-birth-writer>','exec'),ns)
 def write(mint='mint',creator='original-creator',signature='original-create',created='1700000000'):
  ns.update(mint=mint,creator=creator,create_tx_signature=signature,created_at=created,analyzed_at=1800000000,birth_seen_at=1800000000,bonding_curve_pda=None,birth_evidence_source='PUMPPORTAL_NEW_TOKEN')
  ns['_do_write']()
 def read():
  with sqlite3.connect(db) as c:return c.execute('SELECT mint,created_at,earliest_tx_creator,pf_ws_creator,create_tx_signature FROM token_analysis').fetchall()
 return types.SimpleNamespace(db=db,write=write,read=read)

def test_actual_unique_mint_writer_preserves_original_after_receipt_absence(birth):
 birth.write();original=birth.read()
 for _ in range(3):birth.write(created='1800000000')
 assert birth.read()==original
 assert len(original)==1 and int(original[0][1])==1700000000

@pytest.mark.parametrize('field',['creator','signature'])
def test_conflicting_duplicate_cannot_overwrite_existing_canonical_tuple(birth,field):
 birth.write();old=birth.read();birth.write(**{field:'conflicting'},created='1800000000')
 assert birth.read()==old
 # This proves canonical persistence only, never qualifying the conflicting notification.


def test_migration_fallback_time_is_not_original_birth_authority(birth):
 birth.write(created='1800000000')
 with sqlite3.connect(birth.db) as c:c.execute("UPDATE token_analysis SET migration_signal_source='flow',lifecycle_stage='migrated'")
 birth.write(created='1700000000')
 # COALESCE protects an existing timestamp but cannot establish its provenance.
 assert int(birth.read()[0][1])==1800000000

@pytest.fixture
def ledger(tmp_path):
 m=load(ROOT/'src/ops/storage_audit_ledger.py','bounded_lifecycle_ledger')
 e=m.LedgerEntry('synthetic',1800000000,'dev','fixture','RETAIN','protected audit','PERMANENT','PASS',1,1,'SUCCESS')
 return types.SimpleNamespace(m=m,e=e,path=tmp_path/'audit.jsonl')

def test_ledger_durable_append_restart_and_exact_original_evidence(ledger):
 for _ in range(3):ledger.m.append_entry(ledger.path,ledger.e)
 first=ledger.path.read_bytes();assert len(ledger.m.read_ledger(ledger.path))==3
 m=load(ROOT/'src/ops/storage_audit_ledger.py','bounded_lifecycle_restart')
 assert len(m.read_ledger(ledger.path))==3 and ledger.path.read_bytes()==first


def test_ledger_quota_never_truncates_or_expires_protected_history(ledger,monkeypatch):
 ledger.m.append_entry(ledger.path,ledger.e);old=ledger.path.read_bytes()
 monkeypatch.setattr(ledger.m,'MAX_LEDGER_BYTES',len(old))
 with pytest.raises(ledger.m.LedgerHold,match='LEDGER_CAPACITY'):ledger.m.append_entry(ledger.path,ledger.e)
 assert ledger.path.read_bytes()==old

@pytest.mark.parametrize('fault',['entry','disk','interrupted'])
def test_ledger_refusals_keep_history(ledger,monkeypatch,fault):
 ledger.m.append_entry(ledger.path,ledger.e)
 if fault=='entry':monkeypatch.setattr(ledger.m,'MAX_ENTRY_BYTES',1)
 if fault=='disk':monkeypatch.setattr(ledger.m.os,'fstatvfs',lambda fd:types.SimpleNamespace(f_bavail=0,f_frsize=4096))
 if fault=='interrupted':
  with ledger.path.open('ab') as f:f.write(b'{')
 old=ledger.path.read_bytes()
 with pytest.raises(ledger.m.LedgerHold):ledger.m.append_entry(ledger.path,ledger.e)
 assert ledger.path.read_bytes()==old


def test_ledger_exclusive_writer_and_symlink_rejection(ledger):
 import fcntl
 ledger.m.append_entry(ledger.path,ledger.e)
 fd=os.open(ledger.path,os.O_RDWR);fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
 try:
  with pytest.raises(BlockingIOError):ledger.m.append_entry(ledger.path,ledger.e)
 finally:os.close(fd)
 link=ledger.path.with_name('link.jsonl');link.symlink_to(ledger.path)
 with pytest.raises(ledger.m.LedgerHold,match='PHYSICAL_PATH'):ledger.m.append_entry(link,ledger.e)


def test_ledger_sync_failure_never_silently_reports_success(ledger,monkeypatch):
 original=ledger.m.os.fsync
 monkeypatch.setattr(ledger.m.os,'fsync',lambda fd:(_ for _ in ()).throw(OSError('synthetic fsync failure')))
 with pytest.raises(OSError):ledger.m.append_entry(ledger.path,ledger.e)
 monkeypatch.setattr(ledger.m.os,'fsync',original)
 # Ambiguous committed append is preserved, never rolled back by truncation.
 assert len(ledger.m.read_ledger(ledger.path))==1


def test_partial_write_crash_blocks_next_append_without_truncating_history(ledger,monkeypatch):
 ledger.m.append_entry(ledger.path,ledger.e);old=ledger.path.read_bytes();original=ledger.m.os.write;calls=[0]
 def interrupted(fd,data):
  calls[0]+=1
  if calls[0]==1:return original(fd,data[:10])
  raise OSError('synthetic interrupted append')
 monkeypatch.setattr(ledger.m.os,'write',interrupted)
 with pytest.raises(OSError):ledger.m.append_entry(ledger.path,ledger.e)
 monkeypatch.setattr(ledger.m.os,'write',original)
 assert ledger.path.read_bytes().startswith(old)
 damaged=ledger.path.read_bytes()
 with pytest.raises(ledger.m.LedgerHold,match='INTERRUPTED_TAIL'):ledger.m.append_entry(ledger.path,ledger.e)
 assert ledger.path.read_bytes()==damaged


def test_directory_sync_failure_is_explicit(ledger,monkeypatch):
 original=ledger.m.os.fsync;calls=[0]
 def fail_directory(fd):
  calls[0]+=1
  if calls[0]==2:raise OSError('synthetic directory fsync failure')
  return original(fd)
 monkeypatch.setattr(ledger.m.os,'fsync',fail_directory)
 with pytest.raises(OSError):ledger.m.append_entry(ledger.path,ledger.e)
 assert len(ledger.m.read_ledger(ledger.path))==1
