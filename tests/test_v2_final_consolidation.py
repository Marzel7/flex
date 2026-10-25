"""Focused final-consolidation controls; synthetic files and no network."""
import ast,hashlib,importlib.util,os,resource,socket,sys,time
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]

@pytest.fixture
def owner(tmp_path,monkeypatch):
 import src.ops.storage_lifecycle_runner as r
 def forbidden(*a,**k):raise AssertionError('NETWORK_FORBIDDEN')
 monkeypatch.setattr(socket.socket,'connect',forbidden)
 monkeypatch.setattr(socket,'create_connection',forbidden)
 old=resource.getrlimit(resource.RLIMIT_FSIZE);resource.setrlimit(resource.RLIMIT_FSIZE,(499000000,old[1]))
 yield r
 assert all(p.stat().st_size<500000000 for p in tmp_path.rglob('*') if p.is_file())
 resource.setrlimit(resource.RLIMIT_FSIZE,old)

def temp(root,age,name='snapshot.json.tmp.999999.123',now=1800000000):
 p=root/name;p.write_bytes(b'disposable fixture');os.utime(p,(now-age,now-age));return p

@pytest.mark.parametrize('days,eligible',[(6,False),(7,False),(8,True)])
def test_strict_seven_day_abandoned_temp_contract(owner,tmp_path,monkeypatch,days,eligible):
 p=temp(tmp_path,days*86400);monkeypatch.setattr(owner.time,'time',lambda:1800000000)
 rows=owner._find_abandoned_temp_files(str(tmp_path),min_age_seconds=owner.MIN_TEMP_FILE_AGE_SECONDS)
 assert bool(rows)==eligible
 if eligible:
  assert owner.execute(rows[0])['result']=='SUCCESS';assert not p.exists()
 else:assert p.exists()

def test_bounded_repeated_cycles_progress_without_new_checkpoint_store(owner,tmp_path,monkeypatch):
 monkeypatch.setattr(owner.time,'time',lambda:1800000000)
 for i in range(70):temp(tmp_path,8*86400,name=f'snapshot-{i:03}.json.tmp.999999.{i}')
 sizes=[]
 for _ in range(4):
  batch=owner._find_abandoned_temp_files(str(tmp_path),min_age_seconds=owner.MIN_TEMP_FILE_AGE_SECONDS);sizes.append(len(batch))
  for action in batch:assert owner.execute(action)['result']=='SUCCESS'
 assert sizes==[32,32,6,0] and not list(tmp_path.iterdir())

def test_overcapacity_and_symlink_are_not_silently_processed(owner,tmp_path,monkeypatch):
 monkeypatch.setattr(owner,'MAX_TEMP_ENTRIES',2)
 for i in range(3):temp(tmp_path,8*86400,name=f'x.tmp.999999.{i}')
 with pytest.raises(RuntimeError,match='TEMP_DIRECTORY_CAPACITY'):owner._find_abandoned_temp_files(str(tmp_path),min_age_seconds=owner.MIN_TEMP_FILE_AGE_SECONDS)
 assert len(list(tmp_path.iterdir()))==3

def test_open_temp_file_is_protected(owner,tmp_path,monkeypatch):
 monkeypatch.setattr(owner.time,'time',lambda:1800000000)
 p=temp(tmp_path,8*86400)
 action=owner.PlannedAction('DELETE_ABANDONED_TEMP',str(p),'fixture')
 with p.open('rb'):
  assert owner.validate(action)==(False,'OPEN_OR_UNVERIFIED_FILE')
  assert owner.execute(action)['result']=='FAILED'
 assert p.exists()

def test_unavailable_open_file_authority_fails_closed(owner,tmp_path,monkeypatch):
 monkeypatch.setattr(owner.time,'time',lambda:1800000000)
 p=temp(tmp_path,8*86400)
 monkeypatch.setattr(owner.subprocess,'run',lambda *a,**kw:(_ for _ in ()).throw(OSError('synthetic unavailable lsof')))
 assert owner.validate(owner.PlannedAction('DELETE_ABANDONED_TEMP',str(p),'fixture'))==(False,'OPEN_OR_UNVERIFIED_FILE')
 assert p.exists()

def test_current_listener_recovery_payload_hash_is_exact():
 path=ROOT/'docs/dev/lightweight_dev004_first_v2/quarantine_continuation/production_control.py'
 tree=ast.parse(path.read_text());assignment=next(n for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='TERMINAL_PAYLOAD' for t in n.targets))
 payload=ast.literal_eval(assignment.value)
 for name,expected in payload.items():assert hashlib.sha256((ROOT/'runtime/listener/src/core'/name).read_bytes()).hexdigest()==expected


def test_unknown_protected_active_and_oversized_files_remain(owner,tmp_path,monkeypatch):
 monkeypatch.setattr(owner.time,'time',lambda:1800000000)
 temp(tmp_path,8*86400,name='canonical.json')
 temp(tmp_path,8*86400,name=f'snapshot.tmp.{os.getpid()}.12')
 temp(tmp_path,8*86400,name='oversized.tmp.999999.13')
 monkeypatch.setattr(owner,'MAX_TEMP_FILE_BYTES',4)
 assert owner._find_abandoned_temp_files(str(tmp_path),min_age_seconds=owner.MIN_TEMP_FILE_AGE_SECONDS)==[]
 assert len(list(tmp_path.iterdir()))==3


def test_interrupted_directory_sync_is_not_false_completion(owner,tmp_path,monkeypatch):
 monkeypatch.setattr(owner.time,'time',lambda:1800000000)
 p=temp(tmp_path,8*86400)
 action=owner.PlannedAction('DELETE_ABANDONED_TEMP',str(p),'fixture')
 monkeypatch.setattr(owner.os,'fsync',lambda fd:(_ for _ in ()).throw(OSError('synthetic sync interruption')))
 assert owner.execute(action)['result']=='FAILED'
 assert not p.exists()
 # Restart/continuation examines only remaining durable source identities.
 assert owner._find_abandoned_temp_files(str(tmp_path),min_age_seconds=owner.MIN_TEMP_FILE_AGE_SECONDS)==[]


def test_required_api_default_binding_counterexample_without_opening_database():
 # Diagnostic evidence for the HOLD, not an API readiness PASS.
 tree=ast.parse((ROOT/'src/apis/price_api.py').read_text())
 assignment=next(n for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='_db_path' for t in n.targets))
 register=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='register_price_api')
 requests=[]
 class Logger:
  def info(self,*a):pass
 class App:
  config={}
  def register_blueprint(self,*a):pass
 ns={'_ensure_metadata_cache_table':requests.append,'price_api':object(),'logger':Logger()}
 for n in [assignment,register]:exec(compile(ast.Module(body=[n],type_ignores=[]),'<exact-api-binding>','exec'),ns)
 ns['register_price_api'](App())
 assert requests==['database/flex_complete_database.db']
 assert not (ROOT/'src/core/paired_binding_guard.py').exists()
 assert (ROOT/'runtime/listener/src/core/paired_binding_guard.py').is_file()


def test_recovery_source_authority_has_no_old_worktree_fallback(tmp_path):
 import subprocess
 path=ROOT/'docs/dev/lightweight_dev004_first_v2/quarantine_continuation/production_control.py'
 tree=ast.parse(path.read_text());function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='qualified_source_root')
 ns={'pathlib':__import__('pathlib'),'subprocess':subprocess}
 exec(compile(ast.Module(body=[function],type_ignores=[]),'<source-authority>','exec'),ns)
 with pytest.raises(KeyError):ns['qualified_source_root']({})
 with pytest.raises(RuntimeError,match='ROOT_REQUIRED'):ns['qualified_source_root']({'immutable_source_root':'relative'})
 with pytest.raises(RuntimeError,match='COMMIT_REQUIRED'):ns['qualified_source_root']({'immutable_source_root':str(tmp_path),'immutable_source_commit':'unknown'})


def test_recovery_accepts_exact_clean_commit_and_rejects_staged_change(tmp_path):
 import subprocess
 path=ROOT/'docs/dev/lightweight_dev004_first_v2/quarantine_continuation/production_control.py'
 tree=ast.parse(path.read_text());function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='qualified_source_root')
 ns={'pathlib':__import__('pathlib'),'subprocess':subprocess}
 exec(compile(ast.Module(body=[function],type_ignores=[]),'<source-authority>','exec'),ns)
 def git(*args):return subprocess.check_output(['git','-C',str(tmp_path),*args],text=True).strip()
 git('init','-q');(tmp_path/'source.py').write_text('accepted=1\n');git('add','source.py');git('-c','user.name=DEV Fixture','-c','user.email=fixture@example.invalid','commit','-qm','synthetic authority')
 plan={'immutable_source_root':str(tmp_path),'immutable_source_commit':git('rev-parse','HEAD')}
 assert ns['qualified_source_root'](plan)==tmp_path
 with pytest.raises(RuntimeError,match='SOURCE_MISMATCH'):ns['qualified_source_root']({**plan,'immutable_source_commit':'0'*40})
 (tmp_path/'source.py').write_text('unqualified=2\n');git('add','source.py')
 with pytest.raises(RuntimeError,match='SOURCE_MISMATCH'):ns['qualified_source_root'](plan)
