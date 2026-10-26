"""Provider-free denial publication and real boundary hooks; no runtime start."""
import ast,asyncio,hashlib,importlib.util,json,os,socket,sqlite3,types
from pathlib import Path
import pytest
from tests.test_v2_migration_completion import env as completion_env
from tests.test_v2_create_instrumentation import env as bound_env
ROOT=Path(__file__).resolve().parents[1]
@pytest.fixture
def case(tmp_path,monkeypatch):
 def forbidden(*a,**kw):raise AssertionError('NETWORK_FORBIDDEN')
 monkeypatch.setattr(socket.socket,'connect',forbidden);monkeypatch.setattr(socket,'create_connection',forbidden)
 p=ROOT/'runtime/listener/src/core/helius_denial_gate.py';spec=importlib.util.spec_from_file_location('denial_fixture',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
 now=[1000];g=m.Gate(tmp_path/'helius.json','fixture',clock=lambda:now[0]);g.initialize('OPEN');monkeypatch.setenv(m.FLAG,'1');original_current=m.current;monkeypatch.setattr(m,'current',lambda:g)
 return types.SimpleNamespace(m=m,g=g,now=now,root=tmp_path,original_current=original_current)

def test_confirmed_payment_denial_survives_restart_and_no_auto_reopen(case):
 e=case;e.g.reserve('getTransaction');e.g.result(402);e.now[0]+=10**6
 fresh=e.m.Gate(e.g.path,'fixture',clock=lambda:e.now[0])
 assert fresh.blocked()
 with pytest.raises(e.m.Deferred):fresh.reserve()
 before=fresh.read();fresh.result(200,probe=True)
 assert fresh.read()['state']=='PAYMENT_DENIED' and fresh.read()['reservations']==before['reservations']

@pytest.mark.parametrize('status,state',[(429,'RATE_LIMITED'),(402,'PAYMENT_DENIED'),(403,'AUTH_DENIED'),(401,'AUTH_DENIED')])
def test_status_semantics(case,status,state):
 e=case;e.g.reserve();e.g.result(status);assert e.g.read()['state']==state and e.g.blocked()


def test_outage_single_probe_and_paced_recovery(case):
 e=case
 for i in range(3):e.g.reserve('getTransaction');e.g.result(503);e.now[0]+=2
 assert e.g.read()['state']=='OUTAGE' and e.g.blocked()
 e.now[0]+=301;assert e.g.reserve() is True
 with pytest.raises(e.m.Deferred):e.g.reserve()
 e.g.result(200,probe=True);assert e.g.blocked();e.now[0]+=2
 assert e.g.reserve() is False


def test_operator_restore_requires_exact_state(case):
 e=case;e.g.result(402)
 with pytest.raises(e.m.Integrity):e.g.authorize_probe('unverified')
 e.g.authorize_probe(hashlib.sha256(e.g.path.read_bytes()).hexdigest())
 assert e.g.reserve() is True

@pytest.mark.parametrize('fault',['missing','corrupt','generation','pending'])
def test_ambiguous_state_never_grants_allowance(case,fault):
 e=case
 if fault=='missing':e.g.path.unlink()
 elif fault=='corrupt':e.g.path.write_text('{')
 elif fault=='generation':e.g.generation='wrong'
 else:Path(str(e.g.path)+'.lock').write_bytes(b'PENDING_V1\n')
 with pytest.raises((e.m.Integrity,FileNotFoundError)):e.g.reserve()

@pytest.mark.parametrize('fault',['file_fsync','dir_fsync','rename'])
def test_publication_failure_is_fail_closed_after_restart(case,monkeypatch,fault):
 e=case;real=e.m.os.fsync
 if fault=='rename':monkeypatch.setattr(e.m.os,'replace',lambda *a:(_ for _ in ()).throw(OSError('injected')))
 else:
  count=[0]
  def fsync(fd):
   count[0]+=1
   if count[0]==(2 if fault=='file_fsync' else 3):raise OSError('injected')
   return real(fd)
  monkeypatch.setattr(e.m.os,'fsync',fsync)
 with pytest.raises(OSError):e.g.reserve()
 with pytest.raises(e.m.Integrity):e.g.reserve()


def test_crash_after_reservation_preserves_count_and_probe_lease(case):
 e=case;e.g.result(503);e.g.result(503);e.g.result(503);e.now[0]+=301
 e.g.reserve('getTransaction');old=e.g.read()['reservations']
 restart=e.m.Gate(e.g.path,'fixture',clock=lambda:e.now[0])
 assert restart.blocked() and restart.read()['reservations']==old
 e.now[0]+=301;assert restart.reserve() is True


def test_concurrent_reservation_one_winner(case):
 from concurrent.futures import ThreadPoolExecutor
 e=case;e.g.result(503);e.g.result(503);e.g.result(503);e.now[0]+=301
 def reserve():
  try:e.g.reserve();return True
  except e.m.Deferred:return False
 with ThreadPoolExecutor(8) as pool:assert sum(pool.map(lambda _:reserve(),range(8)))==1
 assert e.g.read()['reservations']==1


def test_async_direct_vault_and_analyzer_gate_no_paid_dispatch(case):
 e=case;calls=[]
 class Response:
  status=402;headers={}
 class Context:
  async def __aenter__(self):return Response()
  async def __aexit__(self,*a):pass
 class Session:
  def post(self,url,**kwargs):calls.append(1);return Context()
 async def run():
  with pytest.raises(e.m.Deferred):
   async with e.m.post(Session(),'https://mainnet.helius-rpc.com/?api-key=SECRET',json={'method':'getTransaction'}):pass
  for i in range(100):
   with pytest.raises(e.m.Deferred):
    async with e.m.post(Session(),'https://api-mainnet.helius-rpc.com/v0/transactions?api-key=SECRET',json={}):pass
 asyncio.run(run());assert calls==[1]
 assert 'SECRET' not in e.g.path.read_text() and e.g.read()['reservations']==1


def test_ordinary_429_not_credit_exhaustion_and_retry_after_clamped(case):
 e=case;e.g.result(429,retry_after=10**9);assert e.g.read()['next_at']==1900
 e.now[0]=1900;assert e.g.reserve() is True


def test_walkback_sync_transport_denied_before_urlopen(case,monkeypatch):
 import urllib.request
 e=case;e.g.result(402);calls=[];monkeypatch.setattr(urllib.request,'urlopen',lambda *a,**kw:calls.append(1))
 with pytest.raises(e.m.Deferred):
  with e.m.urlopen(urllib.request.Request('https://mainnet.helius-rpc.com',data=b'{"method":"getTransaction"}'),timeout=1):pass
 assert not calls


def test_disk_refusal_is_not_provider_error(case,monkeypatch):
 e=case;monkeypatch.setattr(e.m.os,'statvfs',lambda p:types.SimpleNamespace(f_bavail=1,f_frsize=1))
 with pytest.raises(e.m.Integrity,match='DISK_FLOOR'):e.g.reserve()


def test_bounded_state_thousands_of_dispatches(case):
 e=case
 for i in range(2000):e.g.reserve('getTransaction');e.g.result(200);e.now[0]+=2
 assert e.g.path.stat().st_size<=4096 and e.g.read()['reservations']==2000
 assert e.g.read()['methods']=={'getTransaction':2000}


def test_real_integration_hook_ordering_and_coverage():
 listener=(ROOT/'runtime/listener/src/core/pumpfun_curve_listener.py').read_text()
 tree=ast.parse(listener);methods={n.name:ast.get_source_segment(listener,n) for n in ast.walk(tree) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))}
 schedule=methods['_schedule_journal_migration'];assert schedule.index('journal.admit(')<schedule.index('_helius_blocked()')<schedule.index('journal.begin_attempt(')
 assert '_helius_post(' in methods['__internal_rpc'] and '_helius_post(' in methods['_post_rpc_with_fallback']
 ws=methods['listen_pumpswap_websocket'];assert '_helius_websocket(' in ws and '_HeliusDeferred' in ws and 'os._exit' not in ws
 for role in ('listener','creator_resolution'):
  source=(ROOT/f'runtime/{role}/src/analysis/pump_fun_post_migration_analyzer.py').read_text();assert 'session.post(' not in source and '_helius_post(' in source
 walk=(ROOT/'runtime/walkback/src/core/walkback_worker.py').read_text();assert '_helius_urlopen(req' in walk
 queue=(ROOT/'runtime/creator_resolution/src/core/creator_resolution_queue.py').read_text();body=queue[queue.index('def process_queue('):];assert body.index('_helius_blocked()')<body.index("status='running'")


def test_shared_schema_bytes_and_no_sensitive_state(case):
 e=case;files=[(ROOT/f'runtime/{r}/src/core/helius_denial_gate.py').read_bytes() for r in ('listener','creator_resolution','walkback')];assert files[0]==files[1]==files[2]
 assert set(p.name for p in e.root.iterdir())=={'helius.json','helius.json.lock'}



def test_pool_transport_bypasses_are_wrapped():
 for name in ('pool_discovery.py','post_migration_pool_discovery.py'):
  text=(ROOT/'runtime/listener/src/core'/name).read_text();assert 'session.post(' not in text and '_helius_post(session,' in text


def test_public_fallback_not_misreported_as_helius(case):
 e=case;assert not e.m.helius('https://api.mainnet-beta.solana.com')
 assert not e.m.helius('https://mainnet.helius-rpc.com.attacker.invalid')
 assert e.m.helius('https://mainnet.helius-rpc.com/?api-key=SECRET')


def test_direct_transport_timeout_opens_outage(case):
 e=case
 class Bad:
  async def __aenter__(self):raise TimeoutError('SECRET')
  async def __aexit__(self,*a):pass
 class Session:
  def post(self,*a,**kw):return Bad()
 async def run():
  for i in range(3):
   with pytest.raises(e.m.Deferred,match='TRANSPORT_UNAVAILABLE'):
    async with e.m.post(Session(),'https://mainnet.helius-rpc.com',json={'method':'getTransaction'}):pass
   e.now[0]+=2
 asyncio.run(run());assert e.g.blocked() and e.g.read()['state']=='OUTAGE'


def test_stale_success_never_clears_later_denial(case):
 e=case;e.g.result(402);e.g.result(200,probe=True);assert e.g.read()['state']=='PAYMENT_DENIED'


def test_durable_pending_intent_survives_denied_admission(case):
 e=case;e.g.result(402)
 p=e.root/'pending.sqlite'
 with sqlite3.connect(p) as c:
  c.execute('CREATE TABLE journal(id TEXT PRIMARY KEY,state TEXT,attempts INTEGER)');c.execute("INSERT INTO journal VALUES('signature','PENDING',0)")
 for i in range(100):assert e.g.blocked()
 with sqlite3.connect(p) as c:assert c.execute('SELECT * FROM journal').fetchall()==[('signature','PENDING',0)]



def test_actual_journal_admission_before_gate_and_no_attempt_charge(case,completion_env,monkeypatch):
 import sys
 e=case;j=completion_env;monkeypatch.setitem(sys.modules,'src.core.helius_denial_gate',e.m);e.g.result(402)
 for i in range(30):assert j.w._schedule_journal_migration('blocked-signature',[],notification_source='PUMPPORTAL_MIGRATION',notification_mint='retained-subject') is None
 row=j.m.MigrationJournal(j.j.path).snapshot()[0]
 assert row['signature']=='blocked-signature' and row['notification_mint']=='retained-subject' and row['attempts']==0 and row['ack'] is None


def test_actual_worker_claims_deferred_before_database_mutation(case,monkeypatch):
 import sys,typing
 e=case;e.g.result(402);monkeypatch.setitem(sys.modules,'src.core.helius_denial_gate',e.m)
 tests=[('runtime/creator_resolution/src/core/creator_resolution_queue.py','process_queue',{'Dict':typing.Dict,'Any':typing.Any}),('runtime/walkback/src/core/walkback_worker.py','drain_batch',{'sqlite3':sqlite3,'BATCH_SIZE':20,'RPC_BUDGET_BATCH':40})]
 for filename,name,ns in tests:
  tree=ast.parse((ROOT/filename).read_text());fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==name)
  exec(compile(ast.Module(body=[fn],type_ignores=[]),filename,'exec'),ns)
  ns['_helius_install']=lambda role:None
  result=ns[name](None);assert result['processed']==0


def test_actual_pair_authority_rejects_wrong_generation(case,bound_env,monkeypatch):
 e=case;b=bound_env
 assert e.m.authority()==b.manifest['generation']
 d=json.loads(b.owner.read_text());d['generation']='wrong';b.save(b.owner,d)
 with pytest.raises(Exception):e.m.authority()


def test_corrupt_gate_blocks_channel_without_stopping_birth_admission(case):
 e=case;e.g.path.write_bytes(b'{');assert e.m.blocked()


def test_denied_websocket_has_no_network_or_subscription_calls(case):
 e=case;e.g.result(402);called=[]
 async def run():
  with pytest.raises(e.m.Deferred):
   async with e.m.websocket(lambda *a,**kw:called.append(1),'wss://mainnet.helius-rpc.com/?api-key=SECRET'):pass
 asyncio.run(run());assert not called


def test_real_analyzer_preserves_deferral_not_missing_evidence(case,monkeypatch):
 import sys,time,typing
 e=case;e.g.result(402);monkeypatch.setitem(sys.modules,'src.core.helius_denial_gate',e.m)
 source=(ROOT/'runtime/creator_resolution/src/analysis/pump_fun_post_migration_analyzer.py').read_text();tree=ast.parse(source);fn=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='_rpc_post')
 ns={'aiohttp':types.SimpleNamespace(ClientSession=object,ClientTimeout=lambda **kw:None),'Optional':typing.Optional,'asyncio':asyncio,'os':os,'time':time,'_helius_post':e.m.post,'record_request':lambda **kw:(_ for _ in ()).throw(AssertionError('not a provider response'))}
 exec(compile(ast.Module(body=[fn],type_ignores=[]),'accepted-analyzer','exec'),ns)
 with pytest.raises(e.m.Deferred):asyncio.run(ns['_rpc_post'](None,'https://mainnet.helius-rpc.com',{'method':'getTransaction'}))


def test_no_scope_budget_or_canonical_rewrites():
 import subprocess
 old=subprocess.check_output(['git','show','9dcf6277:runtime/listener/src/core/pumpfun_curve_listener.py'],text=True)
 new=(ROOT/'runtime/listener/src/core/pumpfun_curve_listener.py').read_text()
 def extract(s,name):return ast.dump(next(n for n in ast.walk(ast.parse(s)) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name==name))
 for name in ('_migration_notification_disposition','_migration_journal_readback','_compact_pumpportal_birth_retry'):
  assert extract(old,name)==extract(new,name)



def test_feature_flag_downgrade_with_control_path_is_closed(case,monkeypatch):
 e=case;monkeypatch.setenv('HELIUS_DENIAL_GATE_PATH',str(e.g.path));monkeypatch.setenv(e.m.FLAG,'0')
 with pytest.raises(e.m.Integrity,match='DOWNGRADE'):e.m.enabled()


def test_default_flag_omission_cannot_hide_stable_state(case,monkeypatch):
 e=case;stable=e.root/'helius-denial-v1.json';stable.write_bytes(b'existing-protected-state');monkeypatch.setenv('MIGRATION_JOURNAL_PATH',str(e.root/'migration.jsonl'));monkeypatch.setenv(e.m.FLAG,'0');monkeypatch.delenv('HELIUS_DENIAL_GATE_PATH',raising=False)
 with pytest.raises(e.m.Integrity,match='DOWNGRADE'):e.m.enabled()


def test_generation_binding_checked_for_actual_current(case,bound_env,monkeypatch):
 e=case;b=bound_env;monkeypatch.setenv('MIGRATION_JOURNAL_PATH',str(e.root/'migration.jsonl'));monkeypatch.setenv('HELIUS_DENIAL_GATE_PATH',str(e.root/'helius-denial-v1.json'));monkeypatch.setenv(e.m.FLAG,'1')
 e.m.Gate(e.root/'helius-denial-v1.json',b.manifest['generation']).initialize('PAYMENT_DENIED')
 gate=e.original_current();assert gate.generation==b.manifest['generation'] and gate.blocked()
 d=json.loads(b.owner.read_text());d['generation']='wrong';b.save(b.owner,d)
 with pytest.raises(Exception):e.original_current()



def test_failed_control_publication_is_not_missing_create_evidence(case,monkeypatch):
 e=case
 monkeypatch.setattr(e.g,'reserve',lambda *a:(_ for _ in ()).throw(OSError('private fs error')))
 with pytest.raises(e.m.Deferred,match='PUBLICATION_FAILED'):asyncio.run(e.m.reserve_async(e.g,'getTransaction'))
 assert issubclass(e.m.Integrity,e.m.Deferred)


def test_all_other_birth_and_canonical_methods_unchanged():
 import subprocess
 old=subprocess.check_output(['git','show','9dcf6277:runtime/listener/src/core/pumpfun_curve_listener.py'],text=True)
 new=(ROOT/'runtime/listener/src/core/pumpfun_curve_listener.py').read_text()
 def functions(text):return {n.name:ast.dump(n) for n in ast.walk(ast.parse(text)) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))}
 before,after=functions(old),functions(new)
 changed={name for name in before if before[name]!=after[name]}
 assert changed=={'__internal_rpc','_get_transaction_cached','_process_migration_with_mint','_schedule_journal_migration','handle_migration','listen_pumpswap_websocket','delayed_mint_recheck','_post_rpc_with_fallback','main','analyze_post_migration','_run_journal_migration'}


def test_journal_runner_delta_only_budget_claim_deferral():
 import subprocess
 old=subprocess.check_output(['git','show','4f4d2d52:runtime/listener/src/core/pumpfun_curve_listener.py'],text=True)
 new=(ROOT/'runtime/listener/src/core/pumpfun_curve_listener.py').read_text()
 def node(text):return next(n for n in ast.walk(ast.parse(text)) if isinstance(n,ast.AsyncFunctionDef) and n.name=='_run_journal_migration')
 before=node(old);after=node(new)
 assert [n.arg for n in after.args.args[-2:]]==['expected_attempts','dispatch_until']
 after.args.args=after.args.args[:-2];after.args.defaults=[]
 body=after.body[0].body
 guards=[n for n in body if isinstance(n,ast.If) and isinstance(n.test,ast.BoolOp)]
 assert len(guards)==1 and 'BUDGET_DEFERRED' in ast.unparse(guards[0].test)
 assert 'journal.defer_budget' in ast.unparse(guards[0])
 body.remove(guards[0]);assert ast.dump(before)==ast.dump(after)
