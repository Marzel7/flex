"""Existing writer/journal controls only; no new fence or coordinator."""
import ast,asyncio,builtins,hashlib,importlib.util,json,os,resource,socket,sqlite3,sys,tempfile,time,types
from pathlib import Path
resource.setrlimit(resource.RLIMIT_FSIZE,(16000000,16000000));sys.dont_write_bytecode=True
root=Path('/Users/kevinkeaveney/.codex/worktrees/dev004-first-v2-prototype/flex');out=Path(tempfile.mkdtemp(prefix='dev004-paired-transfer-gate-',dir='/private/tmp'))
socket.socket.connect=lambda *a,**k:(_ for _ in ()).throw(RuntimeError('NETWORK_FORBIDDEN'));socket.create_connection=socket.socket.connect
real_connect=sqlite3.connect

def connect(path,*a,**kw):
 raw=str(path);plain=raw[5:].split('?',1)[0] if raw.startswith('file:') else raw
 if plain!=':memory:' and not Path(plain).resolve().is_relative_to(out.resolve()):raise RuntimeError('OUTSIDE_DISPOSABLE_DB')
 c=real_connect(path,*a,**kw)
 if 'mode=ro' not in raw:c.execute('PRAGMA max_page_count=256')
 return c
sqlite3.connect=connect

def load(name,path):
 spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module);return module
m=load('paired_gate_journal',root/'runtime/listener/src/core/migration_signature_journal.py')
w=load('paired_gate_write_service',root/'runtime/listener/src/core/database_write_service.py')
core=out/'core-v1.sqlite';ops=out/'ops-v1.sqlite';core2=out/'core-v2.sqlite';ops2=out/'ops-v2.sqlite'
for p,label in [(core,'V1'),(ops,'V1'),(core2,'V2'),(ops2,'V2')]:
 with sqlite3.connect(p) as c:c.execute('CREATE TABLE evidence(id TEXT PRIMARY KEY,generation TEXT)');c.execute('INSERT INTO evidence VALUES (?,?)',('initial',label))
j=m.MigrationJournal(out/'stable-migration.jsonl');floor_hold=False
free=os.statvfs(out);available=free.f_bavail*free.f_frsize
try:j.initialize()
except m.JournalHold as exc:
 if str(exc)!='DISK_FLOOR':raise
 floor_hold=True
# The existing lease protects transactions by path, not a paired retired epoch.
stale=sqlite3.connect(core)
lease=w.acquire_write_lease('core',str(core),'fence-control','fixture-fence',timeout=.1)
try:
 admitted=None if floor_hold else j.admit('synthetic-migration','PUMPSWAP_MIGRATION_ACCOUNT',1)
 try:
  second=w.acquire_write_lease('ops',str(ops),'paired-control','fixture-pair',timeout=.1)
 except w.NestedDatabaseWriteError:
  pair_rejected=True
 else:
  pair_rejected=False;w.release_write_lease(second)
finally:w.release_write_lease(lease)
# After a finite lease is released, the same old handle can take a fresh V1
# lease and commit. No fake phase file is invented to claim it was retired.
lease=w.acquire_write_lease('core',str(core),'old-owner','stale-v1-control',timeout=.1)
try:
 stale.execute('INSERT INTO evidence VALUES (?,?)',('after-fence-release','V1'));stale.commit()
finally:w.release_write_lease(lease)
stale.close()
with sqlite3.connect(core) as c:old_commit=c.execute('SELECT generation FROM evidence WHERE id=?',('after-fence-release',)).fetchone()
service=w.DatabaseWriteService()
service.register_database('core',str(core))
try:service.register_database('core',str(core2))
except ValueError:same_name_rejected=True
else:same_name_rejected=False
service.register_database('core-v2',str(core2))
# Actual birth frame on current candidate, canonical writes denied.
source=root/'runtime/listener/src/core/pumpfun_curve_listener.py';text=source.read_text();tree=ast.parse(text)
owner=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=='_ensure_webhook_birth_queue_schema')
ddl=next(n.value for n in ast.walk(owner) if isinstance(n,ast.Constant) and isinstance(n.value,str) and 'CREATE TABLE IF NOT EXISTS webhook_birth_queue' in n.value)
with sqlite3.connect(core) as c:
 c.execute(ddl)
 for col,definition in [('payload','TEXT'),('source',"TEXT NOT NULL DEFAULT 'helius_webhook'"),('retry_count','INTEGER NOT NULL DEFAULT 0')]:c.execute(f'ALTER TABLE webhook_birth_queue ADD COLUMN {col} {definition}')
class Audit:
 compact_path=True
 def __init__(self):self.outcomes=[]
 def record(self,**kw):return 'synthetic-receipt'
 def record_outcome(self,*a,**kw):self.outcomes.append(kw['outcome'])
class Actor:
 def __init__(self):
  self._eb_birth_audit=Audit();self._pumpfun_delivery_latency=types.SimpleNamespace(observe_pumpportal=lambda **kw:None);self._byzantine_birth_signal=None;self._portal_vsol={};self.completed_launches=set();self.seen_mints=set()
 async def _insert_bonding_curve_token(self,*a,**kw):raise RuntimeError('CANONICAL_FENCE')
class WS:
 async def send(self,*a):raise RuntimeError('NO_WS')
def loader(block_retry):
 def imp(name,*a,**kw):
  if name=='sqlite3' and block_retry:return types.SimpleNamespace(connect=lambda *a,**k:(_ for _ in ()).throw(RuntimeError('ALL_V1_FENCE')))
  return builtins.__import__(name,*a,**kw)
 scope={'__builtins__':dict(vars(builtins),__import__=imp),'time':time,'json':json,'asyncio':asyncio,'DB_PATH':str(core),'_PP_BIRTH_RETRY_MAX_ROWS':2048,'_PP_BIRTH_RETRY_MAX_BYTES':4096,'_listener_ops_db_path':lambda:str(ops),'_compact_birth_retry_storage_check':lambda *a:None,'log_print':lambda *a,**kw:None}
 for name in ['_compact_pumpportal_birth_retry','_decode_compact_pumpportal_birth_retry','_handle_pumpportal_create_frame']:
  n=next(n for n in ast.walk(tree) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name==name);exec(compile(ast.Module(body=[n],type_ignores=[]),str(source),'exec'),scope)
 return scope['_handle_pumpportal_create_frame']
async def birth(block,sig):
 actor=Actor();await loader(block)(actor,{'traderPublicKey':'synthetic-creator'},signature=sig,mint='synthetic-mint',receive_utc_ns=time.time_ns(),receive_monotonic_ns=time.monotonic_ns(),tracked_trade_mints=set(),migration_sol_threshold=1000,ws=WS());return actor
one=asyncio.run(birth(False,'birth-before-final-fence'));two=asyncio.run(birth(True,'birth-during-final-fence'))
with sqlite3.connect(core) as c:births=c.execute('SELECT signature FROM webhook_birth_queue').fetchall()
checks={'journal_floor_or_durable_admission_consistent':(floor_hold and available < m.DISK_FLOOR + m.MAX_JOURNAL_BYTES) or (not floor_hold and admitted['state']=='PENDING' and m.MigrationJournal(j.path).pending()[0]['signature']=='synthetic-migration'),'existing_same_thread_pair_lease_rejected':pair_rejected,'old_V1_handle_can_commit_after_finite_lease_release':old_commit==('V1',),'same_service_alias_rebind_fails_closed':same_name_rejected,'new_alias_does_not_retire_old_authority':service._paths['core']==str(core.resolve()) and service._paths['core-v2']==str(core2.resolve()),'birth_canonical_only_fence_still_writes_V1_retry':births==[('birth-before-final-fence',)],'birth_under_all_V1_fence_has_no_durable_retry':'UNRECOVERABLE' in two._eb_birth_audit.outcomes and births==[('birth-before-final-fence',)],'birth_failed_admission_never_claims_COMMITTED':'COMMITTED' not in two._eb_birth_audit.outcomes}
assert all(checks.values())
result={'status':'HOLD_BIRTH_ADMISSION_AND_DURABLE_RETIREMENT_FENCE_MISSING','checks':checks,'scope':'Actual write-lease and registry interfaces, real disposable SQLite and current journal. Birth AST uses synthetic canonical denial and audit sinks; storage guard stubbed only to isolate fence. Stale-handle control uses actual lease API with raw fixture SQLite, not a full TrackedConnection/daemon test. Registry creates two idle daemon workers, not services; no commands submitted.','provider_calls':0,'production_database_connections':0,'production_writes':0,'maximum_fixture_file_bytes':max(p.stat().st_size for p in out.iterdir() if p.is_file()),'journal_pending':None if floor_hold else [r['signature'] for r in j.pending()],'available_bytes':available,'journal_floor_refusal':floor_hold,'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest()}
(out/'results.json').write_text(json.dumps(result,indent=2)+'\n');print(out);print(json.dumps(result,indent=2))
