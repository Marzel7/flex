import ast,asyncio,importlib.util,json,os,resource,socket,sqlite3,stat,types
resource.setrlimit(resource.RLIMIT_FSIZE,(16000000,16000000))
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[2]
P=ROOT/'runtime/listener/src/core/migration_signature_journal.py'
spec=importlib.util.spec_from_file_location('journal_under_test',P);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
@pytest.fixture(autouse=True)
def no_network(monkeypatch,tmp_path):
 real_connect=sqlite3.connect
 def guarded_connect(path,*a,**kw):
  raw=str(path);plain=raw[5:].split("?",1)[0] if raw.startswith("file:") else raw
  assert plain==":memory:" or Path(plain).resolve().is_relative_to(tmp_path.resolve()),"OUTSIDE_DISPOSABLE_DB"
  c=real_connect(path,*a,**kw)
  if "mode=ro" not in raw:c.execute("PRAGMA max_page_count=256")
  return c
 monkeypatch.setattr(sqlite3,"connect",guarded_connect)
 monkeypatch.setattr(socket.socket,'connect',lambda *a,**k:(_ for _ in ()).throw(AssertionError('NETWORK_FORBIDDEN')))
 monkeypatch.setattr(socket,'create_connection',lambda *a,**k:(_ for _ in ()).throw(AssertionError('NETWORK_FORBIDDEN')))
@pytest.fixture
def j(tmp_path):
 x=m.MigrationJournal(tmp_path/'migration.jsonl');x.initialize();return x

def admit(j,s='signature',mint=None):return j.admit(s,'PUMPSWAP',1,mint)
def row(j):return j.snapshot()[0]
def test_signature_restart(j):
 r=admit(j);assert r['mint'] is None and r['state']=='PENDING';assert m.MigrationJournal(j.path).snapshot()==[r]
def test_resolve(j):
 admit(j);j.resolve('signature','mint');assert row(j)['state']=='RESOLVED'
def test_duplicate_preserves_time_attempts(j):
 admit(j);j.begin_attempt('signature');j.admit('signature','OTHER',2);assert len(j.snapshot())==1 and row(j)['received_at']==1 and row(j)['attempts']==1
def test_conflict_quarantine(j):
 admit(j,mint='mint');j.resolve('signature','other');assert row(j)['mint']=='mint' and row(j)['state']=='QUARANTINED';assert not j.begin_attempt('signature');assert not j.acknowledge('signature','pair',lambda r:True)
def test_quarantine_restart(j):
 admit(j);j.quarantine('signature','RETAINED_CONFLICT');assert not m.MigrationJournal(j.path).pending();assert row(j)['error_code']=='RETAINED_CONFLICT'
def test_legacy_mixed(j):
 legacy={'version':1,'mint':'known','signature':'legacy','received_at':1,'source':'mark_migrated','last_error':'retained'}
 j.path.write_text(json.dumps(legacy)+'\n');admit(j,'unresolved');j.acknowledge('legacy','pair',lambda r:True)
 rows=j.snapshot();assert len(rows)==2 and rows[0]['legacy']==legacy and rows[0]['state']=='ACKED' and rows[1]['state']=='PENDING';assert j.path.exists()
def test_identical_legacy_duplicates(j):
 r={'version':1,'mint':'known','signature':'legacy','received_at':1,'source':'mark_migrated'};j.path.write_text((json.dumps(r)+'\n')*2);assert len(j.snapshot())==1
def test_unresolved_legacy_fail_closed(j):
 j.path.write_text(json.dumps({'version':1,'mint':None,'signature':'sig','received_at':1})+'\n')
 with pytest.raises(m.JournalHold):j.snapshot()
def test_partial_existing_retained(j):
 j.path.write_text('{"version":');before=j.path.read_bytes()
 with pytest.raises(m.JournalHold):admit(j)
 assert j.path.read_bytes()==before
def test_partial_publication(j,monkeypatch):
 before=j.path.read_bytes();write=os.write
 def short(fd,payload):return write(fd,payload[:max(0,len(payload)//2)])
 monkeypatch.setattr(os,'write',short)
 with pytest.raises(m.JournalHold):admit(j)
 assert j.path.read_bytes()==before and j.tmp.exists()
 with pytest.raises(m.JournalHold):m.MigrationJournal(j.path).snapshot()
def test_file_fsync_failure(j,monkeypatch):
 before=j.path.read_bytes();real=os.fsync
 def fail(fd):
  if stat.S_ISREG(os.fstat(fd).st_mode) and os.fstat(fd).st_size>0:raise OSError('synthetic sync failure')
  return real(fd)
 monkeypatch.setattr(os,'fsync',fail)
 with pytest.raises(OSError):admit(j)
 assert j.path.read_bytes()==before and j.tmp.exists()
def test_directory_fsync_failure_after_replace(j,monkeypatch):
 sync=j._sync_dir;n=[0]
 def fail():
  n[0]+=1
  if n[0]==2:raise OSError('synthetic directory sync failure')
  sync()
 monkeypatch.setattr(j,'_sync_dir',fail)
 with pytest.raises(OSError):admit(j)
 monkeypatch.setattr(j,'_sync_dir',sync)
 # No caller ACK was returned. Duplicate durable publication now succeeds.
 admit(j);assert len(j.snapshot())==1
def test_rename_failure(j,monkeypatch):
 before=j.path.read_bytes();monkeypatch.setattr(os,'replace',lambda *a:(_ for _ in ()).throw(OSError('rename fail')))
 with pytest.raises(OSError):admit(j)
 assert j.path.read_bytes()==before and j.tmp.exists()
def test_disk_floor(j,monkeypatch):
 before=j.path.read_bytes();monkeypatch.setattr(os,'statvfs',lambda p:types.SimpleNamespace(f_bavail=0,f_frsize=4096))
 with pytest.raises(m.JournalHold,match='DISK_FLOOR'):admit(j)
 assert j.path.read_bytes()==before
def test_capacity(j,monkeypatch):
 monkeypatch.setattr(m,'MAX_RECORDS',2);admit(j,'one');admit(j,'two');before=j.path.read_bytes()
 with pytest.raises(m.JournalHold,match='CAPACITY'):admit(j,'three')
 assert j.path.read_bytes()==before
def test_record_capacity(j):
 with pytest.raises(m.JournalHold):j.admit('x'*129,'PUMPSWAP',1)
 assert j.snapshot()==[]
def test_bounded_batch(j):
 for x in range(25):admit(j,str(x))
 assert len(j.pending())==20
 with pytest.raises(m.JournalHold):j.pending(21)
def test_retry_ceiling_survives_restart(j):
 admit(j)
 for _ in range(3):assert m.MigrationJournal(j.path).begin_attempt('signature')
 assert not j.begin_attempt('signature');assert row(j)['state']=='RETRY_EXHAUSTED';assert not j.pending();admit(j);assert row(j)['attempts']==3
def test_before_dispatch_attempt_durable(j):
 admit(j);assert j.begin_attempt('signature');assert m.MigrationJournal(j.path).snapshot()[0]['attempts']==1
def test_core_commit_without_ops_is_not_ack(j):
 admit(j,mint='mint');assert not j.acknowledge('signature','pair',lambda r:False);assert row(j)['state']=='RESOLVED'
def test_core_ops_then_crash_after_ack(j):
 admit(j,mint='mint');assert j.acknowledge('signature','pair',lambda r:True);assert not m.MigrationJournal(j.path).pending();assert len(j.snapshot())==1;assert not j.begin_attempt('signature')
def test_stable_path_rebind(j,monkeypatch):
 admit(j);monkeypatch.setenv('DB_PATH','/not/a/database');assert len(m.MigrationJournal(j.path).pending())==1
def test_missing_file_no_recreation(j):
 j.path.unlink()
 with pytest.raises(m.JournalHold,match='MISSING'):admit(j)
 assert not j.path.exists()
def test_unknown_version_no_mutation(j):
 j.path.write_text('{"version":99}\n');before=j.path.read_bytes()
 with pytest.raises(m.JournalHold):admit(j)
 assert j.path.read_bytes()==before

def listener_methods(scope,names):
 t=ast.parse((ROOT/'runtime/listener/src/core/pumpfun_curve_listener.py').read_text())
 if '_validate_active_pair' not in scope:
  scope['_validate_active_pair']=lambda:None  # This retained suite is legacy unsealed/flag-off only.
 for name in names:
  n=next(n for n in ast.walk(t) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name==name);exec(compile(ast.Module(body=[n],type_ignores=[]),'installed-interface','exec'),scope)
 return scope

def test_admission_precedes_dispatch_and_restart(j):
 scope={'asyncio':asyncio,'time':__import__('time'),'os':os,'DB_PATH':'core','_listener_ops_db_path':lambda:'ops','_signature_migration_journal':lambda:j,'_migration_journal_readback':lambda *a,**kw:False}
 listener_methods(scope,['_schedule_journal_migration','_run_journal_migration'])
 observations=[]
 class Actor:
  _run_journal_migration=scope['_run_journal_migration']
  async def handle_migration(self,sig,logs,source):
   observations.append(j.snapshot()[0]['attempts']);j.resolve(sig,'mint')
 a=Actor()
 async def run():
  task=scope['_schedule_journal_migration'](a,'signature',[]);await task
 asyncio.run(run());assert observations==[1] and row(j)['state']=='RESOLVED'

def test_admission_failure_never_dispatches(j,monkeypatch):
 scope={'asyncio':asyncio,'time':__import__('time'),'os':os,'_signature_migration_journal':lambda:j};listener_methods(scope,['_schedule_journal_migration'])
 monkeypatch.setattr(j,'admit',lambda *a:(_ for _ in ()).throw(m.JournalHold('NO_SPACE')))
 with pytest.raises(m.JournalHold):scope['_schedule_journal_migration'](object(),'signature',[])
def test_duplicate_scheduling(j):
 scope={'asyncio':asyncio,'time':__import__('time'),'os':os,'DB_PATH':'core','_listener_ops_db_path':lambda:'ops','_signature_migration_journal':lambda:j,'_migration_journal_readback':lambda *a,**kw:False};listener_methods(scope,['_schedule_journal_migration','_run_journal_migration'])
 class Actor:
  _run_journal_migration=scope['_run_journal_migration']
  async def handle_migration(self,*a,**kw):pass
 a=Actor()
 async def run():
  one=scope['_schedule_journal_migration'](a,'signature',[]);two=scope['_schedule_journal_migration'](a,'signature',[]);assert two is None;await one
 asyncio.run(run());assert row(j)['attempts']==1

def test_core_committed_does_not_repeat_handler(j):
 admit(j,mint='mint')
 scope={'asyncio':asyncio,'time':__import__('time'),'os':os,'DB_PATH':'core','_listener_ops_db_path':lambda:'ops','_signature_migration_journal':lambda:j,'_migration_journal_readback':lambda r,require_handoff=True:not require_handoff};listener_methods(scope,['_schedule_journal_migration'])
 assert scope['_schedule_journal_migration'](types.SimpleNamespace(),'signature',[]) is None;assert row(j)['attempts']==0

def test_actual_core_ops_readback(j,tmp_path):
 core=tmp_path/'core.sqlite';ops=tmp_path/'ops.sqlite'
 with sqlite3.connect(core) as c:
  c.executescript('CREATE TABLE token_analysis(mint TEXT PRIMARY KEY,migration_tx TEXT);CREATE TABLE migration_walkback_handoff(mint TEXT PRIMARY KEY,state TEXT,migration_tx TEXT);');c.execute("INSERT INTO token_analysis VALUES('mint','signature')")
 with sqlite3.connect(ops) as c:c.execute('CREATE TABLE wt_walkback_queue(mint TEXT PRIMARY KEY)')
 scope={'DB_PATH':str(core),'_listener_ops_db_path':lambda:str(ops)};listener_methods(scope,['_migration_journal_readback']);verify=scope['_migration_journal_readback'];admit(j,mint='mint')
 assert not j.acknowledge('signature','pair',verify)
 with sqlite3.connect(core) as c:c.execute("INSERT INTO migration_walkback_handoff VALUES('mint','COMPLETE','signature')")
 assert not j.acknowledge('signature','pair',verify)
 with sqlite3.connect(ops) as c:c.execute("INSERT INTO wt_walkback_queue VALUES('mint')")
 assert j.acknowledge('signature','pair',verify)

def test_flag_off_delegates_existing_handler():
 seen=[]
 scope={'asyncio':asyncio,'_signature_migration_journal':lambda:None};listener_methods(scope,['_schedule_journal_migration'])
 class A:
  async def handle_migration(self,sig,logs,source):seen.append((sig,logs,source))
 async def run():await scope['_schedule_journal_migration'](A(),'s',['log'])
 asyncio.run(run());assert seen==[('s',['log'],'WEBSOCKET')]

def test_os_lock_excludes_other_instance(j):
 with j._lock():
  with pytest.raises(m.JournalHold,match='OWNER_BUSY'):m.MigrationJournal(j.path).snapshot()

def test_notification_entrypoints_admit_before_task():
 s=(ROOT/'runtime/listener/src/core/pumpfun_curve_listener.py').read_text()
 assert 'self._schedule_journal_migration(signature, logs, notification_source="PUMPSWAP_MIGRATION_ACCOUNT")' in s
 assert 'self._schedule_journal_migration(sig, [], notification_source="PUMPPORTAL_MIGRATION")' in s

def test_flag_off_importer_retains_mixed_unresolved(j):
 legacy={'version':1,'mint':'mint','signature':'known','received_at':1,'source':'mark_migrated'}
 j.path.write_text(json.dumps(legacy)+'\n'+json.dumps({'version':1,'mint':None,'signature':'unknown'})+'\n');before=j.path.read_bytes()
 scope={'os':os,'time':__import__('time'),'_migration_fallback_file_path':lambda:str(j.path)}
 listener_methods(scope,['_reconcile_migration_fallback_file_into_queue']);assert scope['_reconcile_migration_fallback_file_into_queue']()==0;assert j.path.read_bytes()==before

@pytest.mark.parametrize('crash_stage',['core','ops'])
def test_actual_readback_restart_after_core_or_ops(j,tmp_path,crash_stage):
 core=tmp_path/'recovery-core.sqlite';ops=tmp_path/'recovery-ops.sqlite'
 with sqlite3.connect(core) as c:c.executescript('CREATE TABLE token_analysis(mint TEXT PRIMARY KEY,migration_tx TEXT);CREATE TABLE migration_walkback_handoff(mint TEXT PRIMARY KEY,state TEXT,migration_tx TEXT);')
 with sqlite3.connect(ops) as c:c.execute('CREATE TABLE wt_walkback_queue(mint TEXT PRIMARY KEY)')
 scope={'asyncio':asyncio,'time':__import__('time'),'os':os,'DB_PATH':str(core),'_listener_ops_db_path':lambda:str(ops),'_signature_migration_journal':lambda:m.MigrationJournal(j.path)}
 listener_methods(scope,['_migration_journal_readback','_schedule_journal_migration','_run_journal_migration'])
 calls=[]
 class Crash(BaseException):pass
 class Actor:
  _run_journal_migration=scope['_run_journal_migration']
  async def handle_migration(self,sig,logs,source):
   calls.append(sig);j.resolve(sig,'mint')
   with sqlite3.connect(core) as c:c.execute('INSERT OR IGNORE INTO token_analysis VALUES(?,?)',('mint',sig))
   if crash_stage=='core':raise Crash()
   with sqlite3.connect(core) as c:c.execute('INSERT OR IGNORE INTO migration_walkback_handoff VALUES(?,?,?)',('mint','COMPLETE',sig))
   with sqlite3.connect(ops) as c:c.execute('INSERT OR IGNORE INTO wt_walkback_queue VALUES(?)',('mint',))
   raise Crash()
 async def run():
  with pytest.raises(Crash):await scope['_schedule_journal_migration'](Actor(),'signature',[])
 asyncio.run(run());assert row(j)['state']=='RESOLVED'
 # Restart never repeats acquisition/investigation after a visible Core commit.
 assert scope['_schedule_journal_migration'](Actor(),'signature',[]) is None
 if crash_stage=='core':
  assert row(j)['state']=='RESOLVED'
  # Existing handoff owner eventually converges. Journal only verifies it.
  with sqlite3.connect(core) as c:c.execute('INSERT INTO migration_walkback_handoff VALUES(?,?,?)',('mint','COMPLETE','signature'))
  with sqlite3.connect(ops) as c:c.execute('INSERT INTO wt_walkback_queue VALUES(?)',('mint',))
  assert scope['_schedule_journal_migration'](Actor(),'signature',[]) is None
 assert row(j)['state']=='ACKED' and calls==['signature']
 with sqlite3.connect(core) as c:assert c.execute('SELECT count(*) FROM token_analysis').fetchone()[0]==1

def test_resolution_crash_retains_record(j):
 admit(j);j.begin_attempt('signature');j.resolve('signature','mint')
 reopened=m.MigrationJournal(j.path);assert reopened.pending()[0]['mint']=='mint' and reopened.pending()[0]['attempts']==1

def test_no_raw_provider_fields(j):
 admit(j);r=row(j);assert not any(k in r for k in ['logs','raw_payload','transaction','provider_response'])

def test_journal_total_byte_cap(j,monkeypatch):
 admit(j);before=j.path.read_bytes();monkeypatch.setattr(m,'MAX_JOURNAL_BYTES',len(before)+1)
 with pytest.raises(m.JournalHold,match='CAPACITY'):admit(j,'second')
 assert j.path.read_bytes()==before

def test_bindings_ack_evidence_not_global_cutover(j):
 admit(j,mint='mint');j.acknowledge('signature','v1-core|v1-ops',lambda r:True)
 assert row(j)['ack']['binding']=='v1-core|v1-ops'
 # Retained ACK is evidence tied to its pair, never a claim that V2 contains it.
 assert row(j)['ack']['binding']!='v2-core|v2-ops'

def test_conflicting_evidence_retained(j):
 admit(j,mint='original');j.resolve('signature','alternative');assert row(j)['conflicting_mint']=='alternative'
 with pytest.raises(m.JournalHold,match='CONFLICT_CAPACITY'):j.resolve('signature','third')
 assert row(j)['mint']=='original' and row(j)['conflicting_mint']=='alternative'

def test_ack_binding_cannot_be_reassigned(j):
 admit(j,mint='mint');j.acknowledge('signature','pair-one',lambda r:True)
 with pytest.raises(m.JournalHold,match='ACK_BINDING'):j.acknowledge('signature','pair-two',lambda r:True)
 assert row(j)['ack']['binding']=='pair-one'

def test_unexpected_raw_legacy_rejected(j):
 j.path.write_text(json.dumps({'version':1,'mint':'m','signature':'s','source':'old','received_at':1,'raw_payload':{}})+'\n')
 with pytest.raises(m.JournalHold,match='UNEXPECTED_LEGACY'):j.snapshot()

def test_file_then_directory_sync_precedes_dispatch(j,monkeypatch):
 steps=[];sync=os.fsync;replace=os.replace;create=asyncio.create_task
 def tracked_sync(fd):steps.append('dir_sync' if stat.S_ISDIR(os.fstat(fd).st_mode) else 'file_sync');return sync(fd)
 def tracked_replace(*a):steps.append('replace');return replace(*a)
 def tracked_task(coro):steps.append('dispatch');return create(coro)
 monkeypatch.setattr(os,'fsync',tracked_sync);monkeypatch.setattr(os,'replace',tracked_replace);monkeypatch.setattr(asyncio,'create_task',tracked_task)
 scope={'asyncio':asyncio,'time':__import__('time'),'os':os,'DB_PATH':'core','_listener_ops_db_path':lambda:'ops','_signature_migration_journal':lambda:j,'_migration_journal_readback':lambda *a,**kw:False};listener_methods(scope,['_schedule_journal_migration','_run_journal_migration'])
 class A:
  _run_journal_migration=scope['_run_journal_migration']
  async def handle_migration(self,*a,**kw):pass
 async def run():await scope['_schedule_journal_migration'](A(),'signature',[])
 asyncio.run(run());before=steps[:steps.index('dispatch')];assert before[-1]=='dir_sync' and before[-2]=='replace' and before[-3]=='file_sync'

@pytest.mark.parametrize('stage',['admission','reservation','resolution','ack'])
def test_real_process_restart(j,stage):
 import subprocess,sys
 code='''import importlib.util,os,sys,resource
resource.setrlimit(resource.RLIMIT_FSIZE,(16000000,16000000))
s=importlib.util.spec_from_file_location('j',sys.argv[1]);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
j=m.MigrationJournal(sys.argv[2]);j.admit('signature','PUMPSWAP',1)
if sys.argv[3] in ('reservation','resolution','ack'):j.begin_attempt('signature')
if sys.argv[3] in ('resolution','ack'):j.resolve('signature','mint')
if sys.argv[3]=='ack':j.acknowledge('signature','pair',lambda r:True)
os._exit(17)
'''
 result=subprocess.run([sys.executable,'-B','-c',code,str(P),str(j.path),stage],env={'PATH':os.environ.get('PATH',''),'PYTHONDONTWRITEBYTECODE':'1'},capture_output=True,timeout=10)
 assert result.returncode==17,result.stderr
 r=m.MigrationJournal(j.path).snapshot()[0];assert r['signature']=='signature'
 assert r['state']==('ACKED' if stage=='ack' else 'RESOLVED' if stage=='resolution' else 'PENDING')


def test_other_process_lock_excluded(j):
 import subprocess,sys
 code='''import fcntl,os,sys
fd=os.open(sys.argv[1],os.O_RDWR)
try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
except BlockingIOError:sys.exit(23)
sys.exit(0)
'''
 with j._lock():
  result=subprocess.run([sys.executable,'-B','-c',code,str(j.lock_path)],env={'PATH':os.environ.get('PATH','')},capture_output=True,timeout=10)
 assert result.returncode==23

def test_actual_explicit_path_ignores_DB_rebind(j,monkeypatch):
 import builtins
 def imp(name,*args,**kw):
  if name=='src.core.migration_signature_journal':return m
  return builtins.__import__(name,*args,**kw)
 scope={'__builtins__':dict(vars(builtins),__import__=imp),'os':os,'DB_PATH':'old-core'}
 listener_methods(scope,['_signature_migration_journal','_migration_fallback_file_path'])
 monkeypatch.setenv('DEV004_SIGNATURE_FIRST_JOURNAL_ENABLED','1');monkeypatch.setenv('MIGRATION_JOURNAL_PATH',str(j.path));admit(j)
 old=scope['_migration_fallback_file_path']();scope['DB_PATH']='new-core';assert scope['_migration_fallback_file_path']()==old;assert scope['_signature_migration_journal']().pending()[0]['signature']=='signature'
 monkeypatch.delenv('MIGRATION_JOURNAL_PATH')
 with pytest.raises(m.JournalHold):scope['_signature_migration_journal']()

def test_accepted_composition_preserved():
 base=ROOT/'docs/dev/lightweight_dev004_first_v2/gap_recovery_and_listener_reconciliation/prototype'
 for rel in ['src/core/pumpportal_birth_audit.py','src/core/pumpportal_receipt_maintenance.py','scripts/maintain_pumpportal_compact_receipts.py']:
  if rel=='src/core/pumpportal_birth_audit.py':
   def audit_functions(p):return {n.name:ast.dump(n,include_attributes=False) for n in ast.walk(ast.parse(p.read_text())) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))}
   old_audit=audit_functions(base/(rel+'.txt'));new_audit=audit_functions(ROOT/'runtime/listener'/rel)
   assert {k for k in old_audit if old_audit[k]!=new_audit.get(k)}=={'record_outcome'}
  else:assert (ROOT/'runtime/listener'/rel).read_bytes()==(base/(rel+'.txt')).read_bytes()
 def functions(p):return {n.name:ast.dump(n,include_attributes=False) for n in ast.walk(ast.parse(p.read_text())) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))}
 old=functions(base/'src/core/pumpfun_curve_listener.py.txt');new=functions(ROOT/'runtime/listener/src/core/pumpfun_curve_listener.py')
 changed={k for k in old if old[k]!=new.get(k)}
 assert changed=={'_migration_fallback_file_path','_migration_fallback_append','_retain_migration_terminal_risk','_reconcile_migration_fallback_file_into_queue','_process_migration_with_mint','listen_pumpswap_websocket','listen_pumpportal_websocket','drain_migration_persist_queue','_handle_pumpportal_create_frame','_insert_bonding_curve_token','_reconcile_fallback_file_into_queue','drain_birth_persist_queue','_record_pp_birth_outcome'}

def test_flag_off_does_not_delete_v2_journal(j):
 admit(j,'unknown');admit(j,'known',mint='mint');before=j.path.read_bytes()
 scope={'os':os,'time':__import__('time'),'_migration_fallback_file_path':lambda:str(j.path)};listener_methods(scope,['_reconcile_migration_fallback_file_into_queue'])
 assert scope['_reconcile_migration_fallback_file_into_queue']()==0 and j.path.read_bytes()==before

def test_quarantined_scheduler_does_not_dispatch(j):
 admit(j,mint='mint');j.quarantine('signature','CONFLICT')
 scope={'asyncio':asyncio,'time':__import__('time'),'os':os,'_signature_migration_journal':lambda:j};listener_methods(scope,['_schedule_journal_migration'])
 assert scope['_schedule_journal_migration'](types.SimpleNamespace(),'signature',[]) is None
 assert row(j)['state']=='QUARANTINED'

def test_crash_before_task_creation_keeps_admission(j,monkeypatch):
 scope={'asyncio':asyncio,'time':__import__('time'),'os':os,'DB_PATH':'core','_listener_ops_db_path':lambda:'ops','_signature_migration_journal':lambda:j,'_migration_journal_readback':lambda *a,**kw:False};listener_methods(scope,['_schedule_journal_migration','_run_journal_migration'])
 class A:
  _run_journal_migration=scope['_run_journal_migration']
 def fail(coro):coro.close();raise RuntimeError('SIMULATED_TASK_CREATION_CRASH')
 monkeypatch.setattr(asyncio,'create_task',fail)
 with pytest.raises(RuntimeError):scope['_schedule_journal_migration'](A(),'signature',[])
 assert row(j)['state']=='PENDING' and row(j)['attempts']==1

def test_legacy_retry_metadata_retained(j):
 one={'version':1,'mint':'known','signature':'legacy','received_at':2,'source':'mark_migrated','last_error':'first'}
 two=one|{'received_at':1,'last_error':'second'}
 j.path.write_text(json.dumps(one)+'\n'+json.dumps(two)+'\n');admit(j,'new')
 rows=j.snapshot();assert len(rows)==2 and rows[0]['received_at']==1 and rows[0]['legacy']==one and rows[0]['legacy_duplicates']==[two]

def test_legacy_conflicting_mint_fail_closed(j):
 one={'version':1,'mint':'known','signature':'legacy','received_at':1,'source':'mark_migrated'}
 j.path.write_text(json.dumps(one)+'\n'+json.dumps(one|{'mint':'other'})+'\n');before=j.path.read_bytes()
 with pytest.raises(m.JournalHold):admit(j)
 assert j.path.read_bytes()==before

@pytest.mark.parametrize('mutation',[{'version':True},{'state':'RESOLVED'},{'ack':{'unexpected':'value'}}])
def test_incompatible_state_fail_closed(j,mutation):
 admit(j);r=row(j)|mutation;j.path.write_text(json.dumps(r)+'\n');before=j.path.read_bytes()
 with pytest.raises(m.JournalHold):admit(j)
 assert j.path.read_bytes()==before

@pytest.mark.parametrize('conflict',[False,True])
def test_actual_mint_resolution_hook(j,conflict):
 import builtins,threading,typing
 class End(BaseException):pass
 def imp(name,*args,**kw):
  if name=='src.core.migration_signature_journal':return m
  return builtins.__import__(name,*args,**kw)
 scope={'__builtins__':dict(vars(builtins),__import__=imp),'_signature_migration_journal':lambda:j,'Optional':typing.Optional,'Dict':typing.Dict,'time':__import__('time'),'threading':threading,'log_print':lambda *a,**kw:(_ for _ in ()).throw(End())}
 listener_methods(scope,['_process_migration_with_mint']);admit(j,mint='original' if conflict else None)
 async def run():
  with pytest.raises(m.JournalHold if conflict else End):await scope['_process_migration_with_mint'](types.SimpleNamespace(),'signature',[],'resolved')
 asyncio.run(run());assert row(j)['state']==('QUARANTINED' if conflict else 'RESOLVED')
 assert row(j)['mint']==('original' if conflict else 'resolved')

def test_resolved_fallback_slot_retained(j):
 j.admit('signature','mark_migrated',1,'mint',migration_slot=123);assert row(j)['migration_slot']==123
 j.admit('signature','mark_migrated',2,'mint',migration_slot=124)
 assert row(j)['state']=='QUARANTINED' and row(j)['migration_slot']==123 and row(j)['conflicting_slot']==124

def test_legacy_slot_preserved(j):
 r={'version':1,'mint':'known','signature':'legacy','received_at':1,'source':'mark_migrated','migration_slot':77}
 j.path.write_text(json.dumps(r)+'\n');admit(j,'other');assert j.snapshot()[0]['migration_slot']==77 and j.snapshot()[0]['legacy']==r


def test_capacity_cannot_spill_into_legacy_risk_file(j):
 import builtins
 def imp(name,*args,**kw):
  if name=='src.core.migration_signature_journal':return m
  return builtins.__import__(name,*args,**kw)
 scope={'__builtins__':dict(vars(builtins),__import__=imp),'_signature_migration_journal':lambda:j,'_durable_jsonl_append':lambda *a:(_ for _ in ()).throw(AssertionError('UNBOUNDED_RISK_FILE'))}
 listener_methods(scope,['_retain_migration_terminal_risk'])
 with pytest.raises(m.JournalHold,match='NOT_BYPASSABLE'):scope['_retain_migration_terminal_risk']('mint','signature',1,'queue failure','capacity')
