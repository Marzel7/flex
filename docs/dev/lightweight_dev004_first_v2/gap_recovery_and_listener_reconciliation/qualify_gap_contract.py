import ast,asyncio,hashlib,json,os,socket,subprocess,tempfile,types,resource
from pathlib import Path
resource.setrlimit(resource.RLIMIT_FSIZE,(16000000,16000000))
root=Path('/Users/kevinkeaveney/.codex/worktrees/dev004-first-v2-prototype/flex');out=Path('/private/tmp/dev004-listener-reconcile-q0rho3wv')
source=Path('/Users/kevinkeaveney/Production/flex-listener/src/core/pumpfun_curve_listener.py');text=source.read_text();tree=ast.parse(text)
socket.socket.connect=lambda *a,**k:(_ for _ in ()).throw(RuntimeError('NETWORK_FORBIDDEN'))
socket.create_connection=socket.socket.connect
class EndSweep(BaseException):pass
signatures=[f'synthetic-sig-{i:03}' for i in range(150,0,-1)]
class AsyncBoundary:
 def __init__(self,warm,interval):self.warm=warm;self.interval=interval;self.sweeps=0
 async def sleep(self,seconds):
  if seconds==self.warm:return
  self.sweeps+=1
  await asyncio.sleep(0)
  if self.sweeps>=2:raise EndSweep()
  return
 Semaphore=asyncio.Semaphore
 gather=staticmethod(asyncio.gather)
 create_task=staticmethod(asyncio.create_task)
 async def to_thread(self,fn,*a):return fn(*a)
class Actor:
 def __init__(self,fail=False):
  self.fail=fail;self.requests=[];self.fetched=[];self.births=[];self.migrations=[];self._pumpportal_connected=False;self._birth_reconciler_last_seen_sig='';self.completed_launches=set();self.completed_migrations=set();self.processing_migrations=set();self._reconciler_non_migration=set();self._reconciler_failed={};self._reconciler_pending=set();self.reconciler_checked=0
 async def call_discovery_rpc(self,method,params,**kw):
  self.requests.append({'method':method,'params':params});return {'result':[{'signature':s,'err':None} for s in signatures[:params[1]['limit']]]}
 async def _get_transaction_cached(self,sig):
  self.fetched.append(sig);return None if self.fail else {'meta':{'logMessages':['synthetic create']}}
 def _is_pumpfun_create_candidate(self,logs):return True
 async def handle_birth(self,sig,logs):self.births.append(sig)
 async def _wait_for_launch_toggle(self,*a):return
 def _reconciler_unseen_sigs(self,sigs):return [s for s in sigs if s not in self.completed_migrations]
 async def _reconcile_one(self,sig):self.migrations.append(sig);self.completed_migrations.add(sig);self._reconciler_pending.discard(sig)
def extract(name,async_boundary):
 node=next(n for n in ast.walk(tree) if isinstance(n,ast.AsyncFunctionDef) and n.name==name)
 scope={'os':os,'asyncio':async_boundary,'log_print':lambda *a,**k:None,'PUMPFUN_PROGRAM':'synthetic-pumpfun-program','PUMPFUN_MIGRATION_ACCOUNT':'synthetic-migration-account','time':__import__('time')}
 exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),'exec'),scope);return scope[name]
async def run(name,actor,warm,interval):
 b=AsyncBoundary(warm,interval)
 try:await extract(name,b)(actor)
 except EndSweep:pass
 return actor
os.environ.update(PUMPFUN_BIRTH_RECONCILE_LIMIT='100',PUMPFUN_BIRTH_RECONCILE_INTERVAL_SECONDS='60',PUMPFUN_BIRTH_RECONCILE_ONLY_WHEN_PUMPPORTAL_DOWN='1',PUMPFUN_BIRTH_TX_CONCURRENCY='1',MIGRATION_RECONCILER_SIG_LIMIT='100',MIGRATION_RECONCILER_INTERVAL='120')
birth=asyncio.run(run('_birth_reconciler_loop',Actor(),45,60));failed=asyncio.run(run('_birth_reconciler_loop',Actor(True),45,60));migration=asyncio.run(run('_migration_reconciler_loop',Actor(),30,120))
checks={'birth_page_not_complete_gap':len(set(birth.births))==100 and len(set(signatures)-set(birth.births))==50,'migration_page_not_complete_gap':len(set(migration.migrations))==100 and len(set(signatures)-set(migration.migrations))==50,'requests_have_no_pagination_cursor':all('before' not in x['params'][1] and 'until' not in x['params'][1] for a in [birth,migration] for x in a.requests),'birth_anchor_advances_despite_failed_acquisition':failed._birth_reconciler_last_seen_sig==signatures[0] and not failed.births and len(failed.fetched)==100,'birth_anchor_not_durable':Actor()._birth_reconciler_last_seen_sig==''}
assert all(checks.values())
result={'status':'HOLD_NO_COMPLETE_GAP_COVERAGE_CONTRACT','checks':checks,'synthetic_gap_events':150,'birth_recovered_unique':len(set(birth.births)),'migration_recovered_unique':len(set(migration.migrations)),'events_outside_single_page':50,'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'scope':'Actual accepted loop bodies, synthetic RPC and transaction sinks only; two instantaneous sweeps. Positive observations prove partial recovery, not on-chain coverage.','provider_requests':0,'production_writes':0,'database_connections':0}
(out/'gap-qualification.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
