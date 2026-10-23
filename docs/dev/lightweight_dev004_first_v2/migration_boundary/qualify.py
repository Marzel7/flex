"""Execute installed admission methods with synthetic sinks and disposable queue only."""
import ast,asyncio,builtins,hashlib,json,resource,socket,sqlite3,tempfile,time,types
from pathlib import Path
resource.setrlimit(resource.RLIMIT_FSIZE,(16000000,16000000))
socket.socket.connect=lambda *a,**k:(_ for _ in ()).throw(RuntimeError('NETWORK_FORBIDDEN'))
socket.create_connection=socket.socket.connect
source=Path('/Users/kevinkeaveney/Production/flex-listener/src/core/pumpfun_curve_listener.py');raw=source.read_bytes()
assert hashlib.sha256(raw).hexdigest()=='751d40d14150b112513cfbed44329cf1c9f0e3e58f34369dd8f29550a1cd3a5f'
tree=ast.parse(raw);out=Path(tempfile.mkdtemp(prefix='dev004-migration-boundary-',dir='/private/tmp'));db=out/'core.sqlite'
owner=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=='_ensure_webhook_birth_queue_schema')
# Run the accepted base queue initializer prefix, before quarantine imports.
segment=ast.get_source_segment(raw.decode(),owner).split('    conn.close()',1)[0]+'    conn.close()\n'
ns={};exec(segment,ns);ns[owner.name](str(db))
c=sqlite3.connect(db);c.execute('PRAGMA max_page_count=256');c.close()
logs=[]
class Audit:
 compact_path=True
 def __init__(self):self.outcomes=[]
 def record(self,**kw):return 'synthetic-receipt'
 def record_outcome(self,ident,**kw):self.outcomes.append(kw['outcome'])
class Actor:
 def __init__(self):
  self._eb_birth_audit=Audit();self._pumpfun_delivery_latency=types.SimpleNamespace(observe_pumpportal=lambda **kw:None);self._byzantine_birth_signal=None;self._portal_vsol={};self.completed_launches=set();self.seen_mints=set()
 async def _insert_bonding_curve_token(self,*a,**kw):raise RuntimeError('SYNTHETIC_CANONICAL_WRITE_FENCE')
class WS:
 async def send(self,*a):raise AssertionError('NO_WS_ALLOWED')
def loader(block_queue):
 def imp(name,*a,**kw):
  if name=='sqlite3' and block_queue:return types.SimpleNamespace(connect=lambda *a,**k:(_ for _ in ()).throw(RuntimeError('SYNTHETIC_ALL_V1_WRITE_FENCE')))
  return builtins.__import__(name,*a,**kw)
 scope={'__builtins__':dict(vars(builtins),__import__=imp),'time':time,'json':json,'asyncio':asyncio,'DB_PATH':str(db),'_PP_BIRTH_RETRY_MAX_ROWS':2048,'_PP_BIRTH_RETRY_MAX_BYTES':4096,'_listener_ops_db_path':lambda:str(out/'ops-unused.sqlite'),'_compact_birth_retry_storage_check':lambda *a:None,'log_print':lambda *a,**k:logs.append(' '.join(map(str,a)))}
 # Storage admission is stubbed ONLY to isolate fencing, not qualified here.
 for name in ['_compact_pumpportal_birth_retry','_decode_compact_pumpportal_birth_retry','_handle_pumpportal_create_frame']:
  n=next(n for n in ast.walk(tree) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name==name);exec(compile(ast.Module(body=[n],type_ignores=[]),str(source),'exec'),scope)
 return scope['_handle_pumpportal_create_frame']
async def birth(block,sig):
 a=Actor();await loader(block)(a,{'traderPublicKey':'synthetic-creator','symbol':'TEST'},signature=sig,mint='synthetic-mint',receive_utc_ns=time.time_ns(),receive_monotonic_ns=time.monotonic_ns(),tracked_trade_mints=set(),migration_sol_threshold=1000,ws=WS());return a
one=asyncio.run(birth(False,'synthetic-birth-1'));two=asyncio.run(birth(False,'synthetic-birth-1'))
with sqlite3.connect(db) as c:queued=c.execute('SELECT signature,consumed FROM webhook_birth_queue').fetchall()
blocked=asyncio.run(birth(True,'synthetic-birth-2'))
with sqlite3.connect(db) as c:after=c.execute('SELECT signature,consumed FROM webhook_birth_queue').fetchall()
# Actual migration dispatch branch: task admission precedes any handle_migration body.
branch=next(n for n in ast.walk(tree) if isinstance(n,ast.If) and ast.unparse(n.test)=="tx_type in ('migration', 'migrate')")
tasks=[];handled=[]
class MigrationActor:
 completed_migrations=set();_migration_census=types.SimpleNamespace(record=lambda **kw:None)
 async def handle_migration(self,*a):handled.append(a)
sc={'self':MigrationActor(),'data':{},'sig':'synthetic-migration','mint':'synthetic-mint','receive_utc_ns':1,'receive_monotonic_ns':1,'tracked_trade_mints':set(),'ws':WS(),'log_print':lambda *a,**kw:None,'asyncio':types.SimpleNamespace(create_task=lambda coro:tasks.append(coro))}
body=ast.AsyncFunctionDef(name='dispatch',args=ast.arguments(posonlyargs=[],args=[],kwonlyargs=[],kw_defaults=[],defaults=[]),body=branch.body,decorator_list=[])
exec(compile(ast.fix_missing_locations(ast.Module(body=[body],type_ignores=[])),str(source),'exec'),sc);asyncio.run(sc['dispatch']())
for coro in tasks:coro.close() # simulated process loss before task executes
checks={'canonical_only_fence_still_commits_retry_to_V1':queued==[('synthetic-birth-1',0)],'duplicate_retry_has_one_queue_row':len(queued)==1,'all_V1_write_fence_leaves_second_birth_without_retry_row':after==queued and 'UNRECOVERABLE' in blocked._eb_birth_audit.outcomes,'blocked_birth_never_claims_COMMITTED':'COMMITTED' not in blocked._eb_birth_audit.outcomes,'received_migration_only_scheduled_in_memory':len(tasks)==1 and not handled}
assert all(checks.values())
r={'status':'HOLD_EXISTING_QUEUES_NOT_INTAKE_PRESERVING_REBIND_BUFFER','source_sha256':hashlib.sha256(raw).hexdigest(),'checks':checks,'queue_rows':queued,'blocked_birth_outcomes':blocked._eb_birth_audit.outcomes,'scope':'Actual AST method/branch with synthetic canonical failure and audit/census sinks; actual disposable SQLite retry. Not real receipt fsync, full canonical persistence, daemon restart or migration investigation acceptance. Storage guard stubbed, not bypassed in production.','provider_calls':0,'production_writes':0,'maximum_file_bytes':max(p.stat().st_size for p in out.iterdir())}
(out/'results.json').write_text(json.dumps(r,indent=2)+'\n');print(out);print(json.dumps(r,indent=2))
