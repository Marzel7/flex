"""Synthetic billing units, disposable state and fake wires; no provider calls.
These costs are NOT Helius prices and are never a deployable policy.
"""
import ast,asyncio,copy,hashlib,http.client,importlib.util,io,json,socket,ssl,sys,types
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pytest
from tests.test_v2_migration_completion import env as completion_env
ROOT=Path(__file__).resolve().parents[1]
URL='https://mainnet.helius-rpc.com/?api-key=DO_NOT_RETAIN'

def policy(**changes):
 p=dict(version=1,minute=100,hour=1000,day=9000,inflight=8,scope_credits=100,scope_requests=20,scope_retries=2,scope_pages=3,scope_fallbacks=2,workers=dict(listener=3000,creator_resolution=3000,walkback=3000),costs={k:dict(credits=10,freshness_seconds=0,evidence_sha256=hashlib.sha256(b'SYNTHETIC_NOT_PROVIDER_PRICING').hexdigest()) for k in ('rpc:getTransaction','rpc:getSignaturesForAddress','rest:address-transactions')})
 p.update(changes);return p

def load(name,path):
 spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

@pytest.fixture
def e(tmp_path,monkeypatch):
 def no_network(*a,**k):raise AssertionError('NETWORK_FORBIDDEN')
 monkeypatch.setattr(socket.socket,'connect',no_network);monkeypatch.setattr(socket,'create_connection',no_network)
 m=load('budget_fixture_gate',ROOT/'runtime/listener/src/core/helius_denial_gate.py');now=[1000];g=m.Gate(tmp_path/'helius.json','fixture',clock=lambda:now[0]);g.monotonic=lambda:now[0];g.initialize('PAYMENT_DENIED')
 import src.core
 monkeypatch.setitem(sys.modules,'src.core.helius_denial_gate',m);monkeypatch.setattr(src.core,'helius_denial_gate',m,raising=False)
 h=load('budget_fixture_http',ROOT/'runtime/listener/src/core/helius_http_protection.py');monkeypatch.setitem(sys.modules,'src.core.helius_http_protection',h)
 original_current=m.current
 monkeypatch.setenv(m.FLAG,'1');monkeypatch.setattr(m,'current',lambda:g)
 def provision(p=None):
  selected=p or policy()
  configure=m._configure_legacy_budget if selected.get('version')==1 else type(g).configure_budget
  configure(g,selected,hashlib.sha256(g.path.read_bytes()).hexdigest())
 def open_fixture():
  with g.locked():s=g.read();s['state']='OPEN';g.save(s)
 def reserve(key='one',scope='mint',role='listener',selector='rpc:getTransaction',page=False,origin='one'):
  return g.reserve_http(role=role,scopes=(h._hash(scope),),key=h._hash(key),selector=selector,page=page,origin=h._hash(origin))
 yield types.SimpleNamespace(m=m,h=h,g=g,now=now,provision=provision,open=open_fixture,reserve=reserve,root=tmp_path,original_current=original_current)
 h.uninstall_for_tests()


def test_policy_only_explicit_denied_and_never_reset(e):
 e.provision();before=e.g.read()
 with pytest.raises(e.m.Integrity):e.provision()
 assert e.g.read()==before and before['state']=='PAYMENT_DENIED'
 with pytest.raises(e.m.Deferred):e.reserve()

@pytest.mark.parametrize('field,value',[('minute',0),('inflight',33),('costs',{}),('workers',{'listener':1}),('costs',{'rpc:x':{'credits':1}})])
def test_bad_policy_refused(e,field,value):
 with pytest.raises(e.m.Integrity):e.provision(policy(**{field:value}))
 assert not e.g.has_budget()


def test_one_token_thousands_cannot_exceed_credits(e):
 e.provision();e.open();allowed=0;refusals={}
 for i in range(2000):
  try:t=e.reserve(key=str(i));e.g.finish_http(t,200);allowed+=1
  except e.m.Deferred as ex:refusals[str(ex)]=refusals.get(str(ex),0)+1
 assert allowed==10 and e.g.read()['rpc_budget']['windows']['day']['credits']==100, (allowed,refusals,e.g.read())
 assert e.g.path.stat().st_size<e.m.BUDGET_MAX_BYTES

@pytest.mark.parametrize('window,duration', [('minute',60),('hour',3600),('day',86400)])
def test_exact_global_window_boundary_and_restart(e,window,duration):
 p=policy(minute=20,hour=20,day=30,workers=dict(listener=10,creator_resolution=10,walkback=10),scope_credits=100)
 # Give each worker one request; reach global minute/hour before third worker.
 e.provision(p);e.open()
 for role in ('listener','creator_resolution'):
  t=e.reserve(key=role,scope=role,role=role);e.g.finish_http(t,200)
 with pytest.raises(e.m.Deferred):e.reserve(key='third',scope='third',role='walkback')
 e.now[0]+=2*duration
 # Only after all narrower relevant windows expire can next charge proceed.
 fresh=e.m.Gate(e.g.path,'fixture',clock=lambda:e.now[0]);fresh.monotonic=lambda:e.now[0]
 assert fresh.read()['rpc_budget']['windows']['day']['credits']==20
 if duration<3600:
  with pytest.raises(e.m.Deferred):e.reserve(key='third',scope='third',role='walkback')
 else:
  t=e.reserve(key='third',scope='third',role='walkback');e.g.finish_http(t,200)


def test_per_worker_cannot_borrow_other_allocation(e):
 e.provision(policy(day=1020,workers=dict(listener=10,creator_resolution=10,walkback=1000)));e.open()
 t=e.reserve();e.g.finish_http(t,200)
 with pytest.raises(e.m.Deferred,match='WORKER_LIMIT'):e.reserve(key='second',scope='other')
 t=e.reserve(key='resolver',scope='other',role='creator_resolution');e.g.finish_http(t,200)


def test_simultaneous_three_worker_global_reservations(e):
 e.provision(policy(minute=30));e.open()
 def call(i):
  for _ in range(300):
   try:return e.reserve(key=str(i),scope=str(i),role=e.m.ROLES[i%3])
   except e.m.Deferred as ex:
    if str(ex)!='HELIUS_GATE_BUSY':return None
    __import__('time').sleep(.001)
  return None
 with ThreadPoolExecutor(12) as pool:out=list(pool.map(call,range(12)))
 s=e.g.read();assert len([x for x in out if x])==3
 assert s['rpc_budget']['windows']['minute']['credits']==30 and len(s['rpc_budget']['inflight'])==3


def test_duplicate_across_workers_then_retained_success(e):
 e.provision();e.open();t=e.reserve()
 with pytest.raises(e.m.Deferred,match='DUPLICATE_INFLIGHT'):e.reserve(role='walkback',scope='other')
 e.g.finish_http(t,200)
 with pytest.raises(e.m.Deferred,match='REQUEST_COOLDOWN'):e.reserve()
 assert e.g.read()['reservations']==1


def test_freshness_is_explicit_and_no_cross_signature_equivalence(e):
 p=policy();p['costs']['rpc:getTransaction']['freshness_seconds']=10;e.provision(p);e.open()
 t=e.reserve(key='signature-A');e.g.finish_http(t,200)
 t=e.reserve(key='signature-B');e.g.finish_http(t,200)
 e.now[0]+=59
 with pytest.raises(e.m.Deferred):e.reserve(key='signature-A')
 e.now[0]+=1;t=e.reserve(key='signature-A');e.g.finish_http(t,200)
 assert e.g.read()['reservations']==3


def test_pagination_and_fallback_are_charged_and_bounded(e):
 e.provision(policy(scope_pages=2,scope_fallbacks=1));e.open()
 for i in range(2):
  t=e.reserve(key=str(i),page=True,origin=str(i));e.g.finish_http(t,200)
 with pytest.raises(e.m.Deferred,match='PAGES_LIMIT'):e.reserve(key='third',page=True)
 with pytest.raises(e.m.Deferred,match='FALLBACKS_LIMIT'):e.reserve(key='third',origin='another')
 assert e.g.read()['rpc_budget']['windows']['day']['credits']==20


def test_retry_each_attempt_charged_and_ceiling(e):
 e.provision(policy(scope_retries=1));e.open()
 t=e.reserve();e.g.finish_http(t,408)
 t=e.reserve();e.g.finish_http(t,408)
 with pytest.raises(e.m.Deferred,match='RETRIES_LIMIT'):e.reserve()
 assert e.g.read()['reservations']==2

@pytest.mark.parametrize('status,state',[(402,'PAYMENT_DENIED'),(401,'AUTH_DENIED'),(403,'AUTH_DENIED'),(429,'RATE_LIMITED')])
def test_status_never_refunds_and_429_not_billing_denial(e,status,state):
 e.provision();e.open();t=e.reserve();e.g.finish_http(t,status)
 assert e.g.read()['state']==state
 assert e.g.read()['rpc_budget']['windows']['day']['credits']==10
 with pytest.raises(e.m.Deferred):e.reserve(key='next')


def test_crash_after_reservation_remains_inflight_no_automatic_expiry(e):
 e.provision();e.open();e.reserve();e.now[0]+=100000
 fresh=e.m.Gate(e.g.path,'fixture',clock=lambda:e.now[0]);fresh.monotonic=lambda:e.now[0]
 assert len(fresh.read()['rpc_budget']['inflight'])==1
 with pytest.raises(e.m.Deferred,match='DUPLICATE'):e.reserve()
 assert e.g.read()['rpc_budget']['windows']['day']['credits']==10


def test_crash_before_reservation_no_dispatch_and_missing_state(e):
 e.provision();e.open();before=e.g.read();e.g.path.unlink()
 with pytest.raises((e.m.Integrity,OSError)):e.reserve()
 assert before['reservations']==0

@pytest.mark.parametrize('kind',['backward','forward_jump','reboot'])
def test_clock_ambiguity_fail_closed(e,kind):
 e.provision();e.open()
 if kind=='backward':e.now[0]-=1
 elif kind=='forward_jump':e.now[0]+=100;e.g.monotonic=lambda:1000
 else:e.g.monotonic=lambda:1
 with pytest.raises(e.m.Integrity,match='CLOCK'):e.reserve()
 assert e.g.read()['reservations']==0


def test_unknown_cost_and_missing_investigation_never_dispatch(e):
 e.provision();e.open()
 with pytest.raises(e.m.Deferred,match='UNKNOWN_REQUEST_COST'):e.reserve(selector='rpc:unknown')
 e.h.install('listener')
 with pytest.raises(e.m.Deferred,match='INVESTIGATION'):e.h._admit(URL,'POST',b'{"method":"getTransaction","params":["sig"]}')
 assert e.g.read()['reservations']==0

@pytest.mark.parametrize('fault',['file_fsync','directory_fsync','replace'])
def test_crash_during_reservation_publication_fails_closed(e,monkeypatch,fault):
 e.provision();e.open();real=e.m.os.fsync;count=[0]
 if fault=='replace':monkeypatch.setattr(e.m.os,'replace',lambda *a:(_ for _ in ()).throw(OSError('injected')))
 else:
  def fsync(fd):
   count[0]+=1
   if count[0]==(2 if fault=='file_fsync' else 3):raise OSError('injected')
   return real(fd)
  monkeypatch.setattr(e.m.os,'fsync',fsync)
 with pytest.raises(OSError):e.reserve()
 with pytest.raises(e.m.Integrity):e.g.read()


def test_disk_floor_no_reservation_and_no_wire(e,monkeypatch):
 e.provision();e.open();monkeypatch.setattr(e.m.os,'statvfs',lambda p:types.SimpleNamespace(f_bavail=1,f_frsize=1))
 with pytest.raises(e.m.Integrity,match='DISK_FLOOR'):e.reserve()
 with pytest.raises(e.m.Integrity):e.g.read()


def test_no_raw_url_key_mint_or_body_in_shared_state(e):
 e.provision();e.open();e.h.install('listener')
 with e.h.investigation('mint:PRIVATE_MINT'):
  r=e.h._admit(URL,'POST',b'{"method":"getTransaction","id":1,"params":["PRIVATE_SIGNATURE"]}')
 e.h._finish(r,200)
 s=e.g.path.read_text();assert all(x not in s for x in ['DO_NOT_RETAIN','PRIVATE_MINT','PRIVATE_SIGNATURE','https://'])

class Wire:
 def __init__(self,status=200):self.sent=[];self.status=status
 def sendall(self,b):self.sent.append(b)
 def makefile(self,*a,**k):return io.BytesIO(('HTTP/1.1 %s OK\r\nContent-Length: 2\r\n\r\n{}'%self.status).encode())
 def close(self):pass
 def settimeout(self,value):pass


def test_stdlib_direct_boundary_before_first_byte_and_unchanged_other_provider(e):
 e.provision();e.h.install('listener')
 c=http.client.HTTPConnection('mainnet.helius-rpc.com');wire=Wire();c.sock=wire
 with e.h.investigation('mint:A'):
  with pytest.raises(e.m.Deferred):c.request('POST','/',body=b'{"method":"getTransaction"}')
 assert wire.sent==[]
 # Non-Helius traffic does not need an investigation or budget.
 c=http.client.HTTPConnection('example.invalid');wire=Wire();c.sock=wire;c.request('GET','/');assert wire.sent


def test_stdlib_complete_body_and_duplicate_ack_are_idempotent(e):
 e.provision();e.open();e.h.install('listener')
 c=http.client.HTTPConnection('mainnet.helius-rpc.com');wire=Wire();c.sock=wire
 with e.h.investigation('mint:A'):c.request('POST','/',body=b'{"method":"getTransaction","params":["sig"]}')
 assert len(e.g.read()['rpc_budget']['inflight'])==1 and wire.sent
 r=c.getresponse();assert r.read()==b'{}';r.close();r.close()
 assert not e.g.read()['rpc_budget']['inflight'] and e.g.read()['outcomes']==1


def test_requests_real_adapter_wire_is_covered(e,monkeypatch):
 import urllib3.connection,requests
 monkeypatch.setattr(urllib3.connection.HTTPConnection,'connect',lambda self:setattr(self,'sock',Wire()))
 e.provision();e.open();e.h.install('listener')
 s=requests.Session();s.trust_env=False
 with e.h.investigation('mint:A'):
  r=s.post('http://mainnet.helius-rpc.com/',json={'method':'getTransaction','params':['A']});assert r.status_code==200
 assert e.g.read()['reservations']==1 and e.g.read()['outcomes']==1


def test_aiohttp_direct_send_each_wire_attempt_and_denial(e,monkeypatch):
 import aiohttp.client_reqrep
 calls=[]
 class Response:
  status=200
  def release(self):pass
  def close(self):pass
  def _release_connection(self):pass
 async def fake(self,conn):calls.append(1);return Response()
 monkeypatch.setattr(aiohttp.client_reqrep.ClientRequest,'send',fake)
 e.provision();e.open();e.h.install('listener')
 async def run():
  req=types.SimpleNamespace(url=URL,method='POST',body=types.SimpleNamespace(_value=b'{"method":"getTransaction","params":["a"]}'))
  with e.h.investigation('mint:A'):
   r=await aiohttp.client_reqrep.ClientRequest.send(req,None);r.release();r.close()
   with pytest.raises(e.m.Deferred,match='REQUEST_COOLDOWN'):await aiohttp.client_reqrep.ClientRequest.send(req,None)
 asyncio.run(run());assert len(calls)==1 and e.g.read()['outcomes']==1

@pytest.mark.parametrize('async_mode',[False,True])
def test_httpx_transport_covered(e,monkeypatch,async_mode):
 import httpx
 def fake(self,request):return httpx.Response(200,content=b'{}')
 async def fake_async(self,request):return httpx.Response(200,content=b'{}')
 monkeypatch.setattr(httpx.HTTPTransport,'handle_request',fake);monkeypatch.setattr(httpx.AsyncHTTPTransport,'handle_async_request',fake_async)
 e.provision();e.open();e.h.install('listener')
 req=httpx.Request('POST',URL,json={'method':'getTransaction','params':['a']})
 if not async_mode:
  with e.h.investigation('mint:A'):r=httpx.HTTPTransport().handle_request(req);r.close()
 else:
  async def run():
   with e.h.investigation('mint:A'):r=await httpx.AsyncHTTPTransport().handle_async_request(req);await r.aclose()
  asyncio.run(run())
 assert e.g.read()['reservations']==1 and e.g.read()['outcomes']==1


def test_unknown_tls_transport_fails_closed(e):
 e.h.install('listener');ctx=ssl.create_default_context()
 with pytest.raises(e.m.Deferred,match='UNQUALIFIED_TRANSPORT'):ctx.wrap_bio(ssl.MemoryBIO(),ssl.MemoryBIO(),server_hostname='mainnet.helius-rpc.com')


def test_scope_inheritance_and_no_automatic_new_allowance(e):
 e.provision();e.open();e.h.install('listener')
 with e.h.investigation('migration:signature'):
  with e.h.investigation('mint:token'):
   r=e.h._admit(URL,'POST',b'{"method":"getTransaction","params":["sig"]}')
 e.h._finish(r,200)
 assert len(e.g.read()['rpc_budget']['scopes'])==2
 assert all(x['credits']==10 for x in e.g.read()['rpc_budget']['scopes'].values())


def test_guard_missing_policy_cannot_use_old_helper(e):
 e.open();e.h.install('listener')
 with e.h.investigation('mint:A'):
  with pytest.raises(e.m.Integrity,match='BUDGET_REQUIRED'):e.h._admit(URL,'POST',b'{"method":"getTransaction"}')


def test_wrong_generation_durable_authority_refused(e):
 e.provision();e.open();e.g.generation='wrong'
 with pytest.raises(e.m.Integrity):e.reserve()


def test_three_role_sources_identical_and_primary_scope_hooks():
 for name in ('helius_denial_gate.py','helius_http_protection.py'):
  sources=[(ROOT/f'runtime/{r}/src/core/{name}').read_bytes() for r in ('listener','creator_resolution','walkback')];assert sources[0]==sources[1]==sources[2]
 for role,file,fn,param in [('listener','pumpfun_curve_listener.py','handle_migration','signature'),('listener','pumpfun_curve_listener.py','_process_migration_with_mint','mint'),('creator_resolution','creator_resolution_queue.py','_resolve_creator_rpc','mint'),('walkback','walkback_worker.py','_process_row','row')]:
  t=ast.parse((ROOT/f'runtime/{role}/src/core/{file}').read_text());n=next(n for n in ast.walk(t) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name==fn);assert any(isinstance(d,ast.Call) and isinstance(d.func,ast.Name) and d.func.id=='_helius_scoped' and d.args[1].value==param for d in n.decorator_list)


def test_rolling_boundary_cannot_double_global_allowance(e):
 e.provision(policy(minute=20));e.open()
 e.now[0]=1059
 for key in ['one','two']:
  t=e.reserve(key=key,scope=key);e.g.finish_http(t,200)
 e.now[0]=1060
 with pytest.raises(e.m.Deferred,match='MINUTE'):e.reserve(key='three',scope='three')
 assert e.g.read()['rpc_budget']['windows']['minute']['credits']==20


def test_inflight_cap_and_ambiguous_requests_never_auto_refund(e):
 e.provision(policy(inflight=1));e.open();t=e.reserve()
 with pytest.raises(e.m.Deferred,match='INFLIGHT_LIMIT'):e.reserve(key='two')
 e.now[0]+=172800
 with pytest.raises(e.m.Deferred,match='INFLIGHT_LIMIT'):e.reserve(key='two')
 assert e.g.read()['rpc_budget']['windows']['day']['credits']==10


def test_deterministic_failure_stops_unchanged_reacquisition(e):
 e.provision();e.open();t=e.reserve();e.g.finish_http(t,422)
 with pytest.raises(e.m.Deferred,match='DETERMINISTIC_FAILURE'):e.reserve()
 assert e.g.read()['reservations']==1

@pytest.mark.parametrize('quota',['BUDGET_MAX_KEYS','BUDGET_MAX_SCOPES'])
def test_identity_capacity_is_explicit_fail_closed(e,monkeypatch,quota):
 e.provision();e.open();monkeypatch.setattr(e.m,quota,1)
 t=e.reserve();e.g.finish_http(t,200)
 with pytest.raises(e.m.Deferred,match='CAPACITY'):e.reserve(key='two',scope='new-scope')
 assert e.g.read()['reservations']==1

@pytest.mark.parametrize('fault',['missing_window','policy_hash','negative_credits','invalid_json'])
def test_corrupt_versioned_budget_state_refused(e,fault):
 e.provision();e.open();s=e.g.read()
 if fault=='missing_window':del s['rpc_budget']['windows']['day']
 elif fault=='policy_hash':s['rpc_budget']['policy_sha256']='0'*64
 elif fault=='negative_credits':s['rpc_budget']['windows']['day']['credits']=-1
 else:e.g.path.write_text('{')
 if fault!='invalid_json':e.g.save(s)
 with pytest.raises(e.m.Integrity):e.reserve()


def test_aiohttp_denied_never_reaches_original_send(e,monkeypatch):
 import aiohttp.client_reqrep
 calls=[]
 async def fake(*a,**k):calls.append(1)
 monkeypatch.setattr(aiohttp.client_reqrep.ClientRequest,'send',fake);e.provision();e.h.install('listener')
 async def run():
  req=types.SimpleNamespace(url=URL,method='POST',body=types.SimpleNamespace(_value=b'{"method":"getTransaction"}'))
  with e.h.investigation('mint:A'):
   with pytest.raises(e.m.Deferred):await aiohttp.client_reqrep.ClientRequest.send(req,None)
 asyncio.run(run());assert calls==[]


def test_protected_journal_budget_defer_is_exact_and_idempotent(e):
 module=load('budget_journal_fixture',ROOT/'runtime/listener/src/core/migration_signature_journal.py')
 j=module.MigrationJournal(e.root/'migration.jsonl');j.initialize();j.admit('sig','PUMPSWAP',100,notification_mint='supplied-mint')
 assert j.begin_attempt('sig',now=1000,defer_until=1045)
 assert not j.defer_budget('sig',expected_attempts=1,dispatch_until=999,not_before=1300)
 assert j.defer_budget('sig',expected_attempts=1,dispatch_until=1045,not_before=1300)
 assert not j.defer_budget('sig',expected_attempts=1,dispatch_until=1045,not_before=1300)
 restarted=module.MigrationJournal(j.path);row=restarted.snapshot()[0]
 assert row['attempts']==0 and row['notification_mint']=='supplied-mint' and row['ack'] is None
 assert not restarted.begin_attempt('sig',now=1299,defer_until=1344)
 assert restarted.begin_attempt('sig',now=1300,defer_until=1345)
 j.quarantine('sig','PROTECTED_CONFLICT')
 assert not j.defer_budget('sig',expected_attempts=1,dispatch_until=1345,not_before=1500)
 assert j.snapshot()[0]['error_code']=='PROTECTED_CONFLICT'


def test_real_walkback_budget_deferral_preserves_attempts(e):
 import sqlite3,time,os
 source=ROOT/'runtime/walkback/src/core/walkback_worker.py';tree=ast.parse(source.read_text());node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_process_row');node.decorator_list=[]
 class Unavailable(Exception):pass
 def blocked(*a,**k):raise e.m.BudgetDeferred('HELIUS_GLOBAL_MINUTE_LIMIT')
 ns=dict(sqlite3=sqlite3,deep_walkback=types.SimpleNamespace(ensure_schema=lambda _:None),AcquisitionUnavailable=Unavailable,os=os,time=time,_find_with_evidence=blocked)
 exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),'exec'),ns)
 c=sqlite3.connect(':memory:');c.row_factory=sqlite3.Row
 c.execute('CREATE TABLE wt_walkback_queue(mint TEXT PRIMARY KEY,status TEXT,attempts INTEGER,lease_expires_at INTEGER,next_retry_at INTEGER,last_error TEXT,updated_at INTEGER,claimed_by TEXT)')
 c.execute('INSERT INTO wt_walkback_queue VALUES(?,?,?,?,?,?,?,?)',('mint','running',3,2000,0,None,1000,os.environ.get('WALKBACK_WORKER_ID',f'walkback-{os.getpid()}')))
 row=dict(mint='mint',creator='creator',subprov='funder',walkback_class='PARTIAL_TREASURY',attempts=2)
 assert ns['_process_row'](c,row)==0
 saved=c.execute('SELECT * FROM wt_walkback_queue').fetchone();assert saved['attempts']==2 and saved['status']=='pending' and saved['last_error']=='HELIUS_ACQUISITION_DEFERRED'


def test_no_qualified_evidence_claimed_from_http_status(e):
 e.provision();e.open();t=e.reserve();e.g.finish_http(t,200)
 record=next(iter(e.g.read()['rpc_budget']['keys'].values()))
 assert record['status']=='HTTP_COMPLETED' and record['selector']=='rpc:getTransaction' and record['role']=='listener'
 assert type(record['pid']) is int
 assert 'provenance' not in record


def test_actual_scheduler_budget_deferral_preserves_admission(e,completion_env):
 e.open();w=completion_env.w
 async def handler(*a,**kw):return 'BUDGET_DEFERRED'
 w.handle_migration=handler
 async def run():
  task=w._schedule_journal_migration('budget-refusal',[],notification_source='PUMPSWAP',notification_mint='retained')
  assert task is not None
  await task
 asyncio.run(run());row=completion_env.j.snapshot()[0]
 assert row['signature']=='budget-refusal' and row['attempts']==0 and row['ack'] is None
 assert row['notification_mint']=='retained' and row['retry_not_before']==1300


def test_separate_worker_processes_share_one_credit_authority(e):
 import subprocess,time
 e.provision(policy(minute=30));e.open()
 code='''import importlib.util,sys,time,json,socket
socket.socket.connect=lambda *a,**k:(_ for _ in ()).throw(AssertionError('NETWORK_FORBIDDEN'))
s=importlib.util.spec_from_file_location('g',sys.argv[1]);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
g=m.Gate(sys.argv[2],'fixture',clock=lambda:1000);g.monotonic=lambda:1000
for _ in range(400):
 try:
  t=g.reserve_http(role=sys.argv[3],scopes=(sys.argv[4]*64,),key=sys.argv[4]*64,selector='rpc:getTransaction',origin='9'*64);print('RESERVED');break
 except m.Deferred as ex:
  if str(ex)!='HELIUS_GATE_BUSY':print('REFUSED');break
  time.sleep(.002)
else:print('BUSY')
'''
 children=[subprocess.Popen([sys.executable,'-B','-c',code,str(ROOT/'runtime/listener/src/core/helius_denial_gate.py'),str(e.g.path),e.m.ROLES[i%3],str(i+1)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for i in range(6)]
 outputs=[]
 for child in children:
  out,err=child.communicate(timeout=20);assert child.returncode==0,err;outputs.append(out.strip())
 assert outputs.count('RESERVED')==3 and outputs.count('REFUSED')==3
 assert e.g.read()['rpc_budget']['windows']['minute']['credits']==30
 assert e.g.read()['reservations']==3


def test_httpx_other_provider_stream_does_not_require_budget(e,monkeypatch):
 import httpx
 monkeypatch.setattr(httpx.HTTPTransport,'handle_request',lambda *a:httpx.Response(200,content=b'{}'))
 e.h.install('listener')
 request=httpx.Request('POST','https://example.invalid/',content=iter([b'body']))
 r=httpx.HTTPTransport().handle_request(request);assert r.status_code==200
 assert e.g.read()['state']=='PAYMENT_DENIED' and e.g.read()['reservations']==0


def test_supported_rest_pages_have_distinct_keys_and_quota(e):
 e.provision(policy(scope_pages=1));e.open();e.h.install('listener')
 with e.h.investigation('mint:A'):
  r=e.h._admit('https://api.helius.xyz/v0/addresses/wallet/transactions?api-key=SECRET&limit=100','GET',None);e.h._finish(r,200)
  with pytest.raises(e.m.Deferred,match='PAGES_LIMIT'):e.h._admit('https://api.helius.xyz/v0/addresses/wallet/transactions?api-key=SECRET&limit=100&before=NEXT','GET',None)
 assert e.g.read()['reservations']==1


def test_http_redelivery_charges_each_retry_wire(e):
 e.provision();e.open();e.h.install('listener')
 with e.h.investigation('mint:A'):
  for _ in range(2):
   c=http.client.HTTPConnection('mainnet.helius-rpc.com');c.sock=Wire(status=408)
   c.request('POST','/',body=b'{"method":"getTransaction","params":["same"]}');r=c.getresponse();r.read();r.close()
 assert e.g.read()['reservations']==2 and e.g.read()['rpc_budget']['windows']['day']['credits']==20

@pytest.mark.parametrize('status,state',[(401,'AUTH_DENIED'),(402,'PAYMENT_DENIED'),(403,'AUTH_DENIED'),(429,'RATE_LIMITED')])
def test_denial_headers_close_gate_before_body_without_releasing_slot(e,status,state):
 e.provision();e.open();e.h.install('listener')
 c=http.client.HTTPConnection('mainnet.helius-rpc.com');c.sock=Wire(status=status)
 with e.h.investigation('mint:A'):c.request('POST','/',body=b'{"method":"getTransaction","params":["same"]}')
 r=c.getresponse();s=e.g.read();assert s['state']==state and len(s['rpc_budget']['inflight'])==1
 with pytest.raises(e.m.Deferred):e.reserve(key='another')
 r.read();r.close();assert not e.g.read()['rpc_budget']['inflight'] and e.g.read()['outcomes']==1


def test_aiohttp_early_eof_does_not_hide_payment_denial(e,monkeypatch):
 import aiohttp.client_reqrep
 class Response:
  status=None;closed=True;headers={}
  def release(self):pass
  def close(self):pass
  def _release_connection(self):pass
 async def fake_send(*a):return Response()
 async def fake_start(self,*a,**kw):
  self._release_connection()  # aiohttp may call EOF before setting status.
  self.status=402
  return self
 monkeypatch.setattr(aiohttp.client_reqrep.ClientRequest,'send',fake_send)
 monkeypatch.setattr(aiohttp.client_reqrep.ClientResponse,'start',fake_start)
 e.provision();e.open();e.h.install('listener')
 async def run():
  req=types.SimpleNamespace(url=URL,method='POST',body=types.SimpleNamespace(_value=b'{"method":"getTransaction"}'))
  with e.h.investigation('mint:A'):
   response=await aiohttp.client_reqrep.ClientRequest.send(req,None)
   await aiohttp.client_reqrep.ClientResponse.start(response,None)
 asyncio.run(run());s=e.g.read();assert s['state']=='PAYMENT_DENIED' and s['outcomes']==1 and not s['rpc_budget']['inflight']


def test_retry_after_is_preserved_at_header_notice(e):
 e.provision();e.open();t=e.reserve();e.g.notice_http(t,429,retry_after=600)
 s=e.g.read();assert s['state']=='RATE_LIMITED' and s['next_at']==1600 and len(s['rpc_budget']['inflight'])==1
 e.g.finish_http(t,429);assert e.g.read()['next_at']==1600


def test_completed_response_crash_can_recover_without_false_retention_claim(e):
 e.provision();e.open();ticket=e.reserve();e.g.finish_http(ticket,200)
 e.now[0]+=60
 restarted=e.m.Gate(e.g.path,'fixture',clock=lambda:e.now[0]);restarted.monotonic=lambda:e.now[0]
 ticket=restarted.reserve_http(role='listener',scopes=(e.h._hash('mint'),),key=e.h._hash('one'),selector='rpc:getTransaction',origin=e.h._hash('one'))
 restarted.finish_http(ticket,200)
 assert restarted.read()['rpc_budget']['windows']['day']['credits']==20
 assert next(iter(restarted.read()['rpc_budget']['scopes'].values()))['retries']==1


def test_legacy_count_only_permit_cannot_authorize_paid_http(e):
 e.provision();e.open()
 with pytest.raises(e.m.Integrity,match='REQUIRES_WIRE_GATE'):e.g.reserve('getTransaction')
 assert e.g.read()['reservations']==0


def test_accepted_websocket_handshake_preserved_without_permit_leak(e):
 e.provision();e.open();e.h.install('listener');seen=[]
 class Connection:
  async def __aenter__(self):
   seen.append(e.h._transport.get());ctx=ssl.create_default_context()
   ctx.wrap_bio(ssl.MemoryBIO(),ssl.MemoryBIO(),server_hostname='mainnet.helius-rpc.com')
   return object()
  async def __aexit__(self,*a):seen.append(e.h._transport.get())
 async def run():
  async with e.m.websocket(lambda *a,**k:Connection(),'wss://mainnet.helius-rpc.com'):
   assert not e.h._transport.get()
 asyncio.run(run());assert seen==[True,False]
 s=e.g.read();assert s['methods']['websocket_connect']==1 and s['rpc_budget']['windows']['day']['credits']==0


@pytest.mark.parametrize('host',['MAINNET.HELIUS-RPC.COM','mainnet.helius-rpc.com.','api.helius.xyz.','mainnet\u3002helius-rpc.com'])
def test_provider_host_alias_cannot_skip_http_protection(e,host):
 assert e.m.helius('https://'+host+'/')


def test_budget_enabled_adapter_keeps_transport_failure_deferred_and_sanitized(e):
 import aiohttp
 e.provision();e.open();e.h.install('listener')
 class Failed:
  async def __aenter__(self):raise aiohttp.ClientError('SECRET_PROVIDER_EXCEPTION')
  async def __aexit__(self,*a):pass
 class Session:
  def post(self,*a,**kw):return Failed()
 async def run():
  with e.h.investigation('mint:A'):
   with pytest.raises(e.m.Deferred,match='HELIUS_TRANSPORT_UNAVAILABLE') as caught:
    async with e.m.post(Session(),URL,json={'method':'getTransaction'}):pass
   assert 'SECRET' not in str(caught.value)
 asyncio.run(run())


def test_not_found_does_not_invent_permanent_evidence_failure(e):
 e.provision();e.open();ticket=e.reserve();e.g.finish_http(ticket,404)
 ticket=e.reserve();e.g.finish_http(ticket,200)
 assert e.g.read()['reservations']==2 and e.g.read()['rpc_budget']['windows']['day']['credits']==20


@pytest.mark.parametrize('count',[1,2,100])
def test_native_enhanced_batch_requires_size_bound_cost_authority(e,count):
 p=policy();p['costs']['rest:transaction-batch:'+str(count)]=dict(p['costs']['rpc:getTransaction'])
 e.provision(p);e.open();e.h.install('listener')
 body=json.dumps({'transactions':['sig-'+str(i) for i in range(count)]}).encode()
 with e.h.investigation('mint:A'):
  reservation=e.h._admit('https://api.helius.xyz/v0/transactions?api-key=SECRET','POST',body);e.h._finish(reservation,200)
  with pytest.raises(e.m.Deferred,match='UNKNOWN_REQUEST_COST'):
   e.h._admit('https://api.helius.xyz/v0/transactions?api-key=SECRET','POST',json.dumps({'transactions':['other-'+str(i) for i in range(count+1)]}).encode())
 assert e.g.read()['reservations']==1 and e.g.read()['rpc_budget']['windows']['day']['credits']==10
