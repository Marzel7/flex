"""Provider-free completion races, cancellation and explicit dead-owner recovery."""
import asyncio,concurrent.futures,contextlib,hashlib,http.client,json,multiprocessing,os,pathlib,socket,time,types
import pytest
from tests.test_v2_helius_rpc_budget import e as legacy,Wire,URL
from tests.test_v2_helius_investigation_budget import v


def test_stdlib_completion_waits_for_owner_instead_of_losing_terminal_result(v):
 v.root_id('root');v.open();v.h.install('listener');c=http.client.HTTPConnection('mainnet.helius-rpc.com');c.sock=Wire()
 with v.h.investigation('mint:root'):c.request('POST','/',body=b'{"method":"getTransaction","params":["fixture-signature"]}')
 response=c.getresponse();other=v.m.Gate(v.g.path,'fixture')
 with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
  with other.locked():
   future=pool.submit(response.read);time.sleep(.05)
   assert not future.done(),'completion was abandoned while another owner held the lock'
  assert future.result(timeout=2)==b'{}'
 state=v.g.read();assert not state['rpc_budget']['inflight'];assert state['outcomes']==1;assert state['rpc_budget']['scopes'][v.h._hash('mint:root')]['credits']==1
 response.close();assert v.g.read()['outcomes']==1


@pytest.mark.parametrize('failure',[TimeoutError,asyncio.CancelledError])
def test_aiohttp_start_exception_before_headers_releases_only_known_terminal_attempt(v,monkeypatch,failure):
 import aiohttp.client_reqrep
 class Response:
  status=None;closed=False;headers={}
  def release(self):self.closed=True
  def close(self):self.closed=True
  def _release_connection(self):pass
 async def send(*a):return Response()
 async def start(self,*a,**k):self.close();raise failure()
 monkeypatch.setattr(aiohttp.client_reqrep.ClientRequest,'send',send);monkeypatch.setattr(aiohttp.client_reqrep.ClientResponse,'start',start)
 v.root_id('root');v.open();v.h.install('listener')
 async def run():
  req=types.SimpleNamespace(url=URL,method='POST',body=types.SimpleNamespace(_value=b'{"method":"getTransaction","params":["fixture"]}'))
  with v.h.investigation('mint:root'):r=await aiohttp.client_reqrep.ClientRequest.send(req,None)
  with pytest.raises(failure):await aiohttp.client_reqrep.ClientResponse.start(r,None)
 asyncio.run(run());state=v.g.read();assert not state['rpc_budget']['inflight'];assert state['outcomes']==1;assert state['rpc_budget']['scopes'][v.h._hash('mint:root')]['credits']==1


@pytest.mark.parametrize('scenario',['body_failure','headers_failure'])
def test_stdlib_terminal_transport_error_keeps_charge_releases_slot(v,scenario):
 class BrokenWire(Wire):
  def makefile(self,*a,**k):
   import io
   return io.BytesIO(b'HTTP/1.1 200 OK\r\nContent-Length: 99\r\n\r\n{}' if scenario=='body_failure' else b'')
 v.root_id('root');v.open();v.h.install('listener');c=http.client.HTTPConnection('mainnet.helius-rpc.com');c.sock=BrokenWire()
 with v.h.investigation('mint:root'):c.request('POST','/',body=b'{"method":"getTransaction","params":["fixture-signature"]}')
 with pytest.raises((http.client.IncompleteRead,http.client.RemoteDisconnected)):
  response=c.getresponse();response.read()
 state=v.g.read();assert not state['rpc_budget']['inflight'] and state['outcomes']==1;assert state['rpc_budget']['scopes'][v.h._hash('mint:root')]['credits']==1


def test_denial_header_notice_waits_for_durable_writer(v):
 v.root_id('root');v.open();ticket=v.reserve2();other=v.m.Gate(v.g.path,'fixture')
 with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
  with other.locked():
   future=pool.submit(v.g.notice_http,ticket,402);time.sleep(.05);assert not future.done()
  future.result(timeout=2)
 assert v.g.read()['state']=='PAYMENT_DENIED';assert len(v.g.read()['rpc_budget']['inflight'])==1
 v.g.finish_http(ticket,402);assert not v.g.read()['rpc_budget']['inflight']


def test_async_httpx_stream_exhaustion_and_error_are_terminal(v,monkeypatch):
 import httpx
 class Stream(httpx.AsyncByteStream):
  async def __aiter__(self):yield b'{}'
  async def aclose(self):pass
 async def fake(*a):return httpx.Response(200,stream=Stream())
 monkeypatch.setattr(httpx.AsyncHTTPTransport,'handle_async_request',fake);v.root_id('root');v.open();v.h.install('listener')
 async def run():
  with v.h.investigation('mint:root'):response=await httpx.AsyncHTTPTransport().handle_async_request(httpx.Request('POST',URL,json={'method':'getTransaction','params':['fixture']}))
  assert b''.join([v async for v in response.stream])==b'{}'
  assert not v.g.read()['rpc_budget']['inflight'];await response.aclose()
 asyncio.run(run());assert v.g.read()['outcomes']==1


def test_sync_httpx_exhaustion_does_not_require_later_close(v,monkeypatch):
 import httpx
 class Stream(httpx.SyncByteStream):
  def __iter__(self):yield b'{}'
  def close(self):pass
 monkeypatch.setattr(httpx.HTTPTransport,'handle_request',lambda *a:httpx.Response(200,stream=Stream()));v.root_id('root');v.open();v.h.install('listener')
 with v.h.investigation('mint:root'):response=httpx.HTTPTransport().handle_request(httpx.Request('POST',URL,json={'method':'getTransaction','params':['fixture']}))
 assert b''.join(response.stream)==b'{}';assert not v.g.read()['rpc_budget']['inflight'];response.close();assert v.g.read()['outcomes']==1


def test_live_owner_age_and_restart_never_authorize_reconciliation(v):
 v.root_id('root');v.open();ticket=v.reserve2();v.now[0]+=100000
 snapshot=v.g.path.read_bytes();fresh=v.m.Gate(v.g.path,'fixture',clock=lambda:v.now[0]);fresh.monotonic=lambda:v.now[0]
 with pytest.raises(v.m.Integrity,match='STILL_PRESENT'):fresh.reconcile_terminated_http(ticket,expected_pid=os.getpid(),expected_sha256=hashlib.sha256(snapshot).hexdigest())
 assert fresh.path.read_bytes()==snapshot and len(fresh.read()['rpc_budget']['inflight'])==1


@pytest.mark.parametrize('defect',['pid_mismatch','permission','snapshot','generation','unknown_ticket'])
def test_reconciliation_refuses_unproven_authority_without_state_change(v,monkeypatch,defect):
 v.root_id('root');v.open();ticket=v.reserve2();before=v.g.path.read_bytes();digest=hashlib.sha256(before).hexdigest();pid=os.getpid()
 if defect=='pid_mismatch':pid+=1
 elif defect=='permission':monkeypatch.setattr(os,'kill',lambda *a:(_ for _ in ()).throw(PermissionError()))
 elif defect=='snapshot':digest='0'*64
 elif defect=='generation':v.g.generation='wrong'
 elif defect=='unknown_ticket':ticket='0'*32
 with pytest.raises(v.m.Integrity):v.g.reconcile_terminated_http(ticket,expected_pid=pid,expected_sha256=digest)
 assert v.g.path.read_bytes()==before


def test_explicit_dead_owner_reconciliation_preserves_spend_and_blocks_unknown_replay(v):
 # Actual disposable owner process is created, reserved on its behalf in a
 # synthetic ledger, then joined. No production PID or provider is touched.
 v.root_id('root');v.open();ticket=v.reserve2(key='uncertain')
 context=multiprocessing.get_context('fork');process=context.Process(target=time.sleep,args=(.05,));process.start();pid=process.pid;process.join(timeout=2);assert process.exitcode==0
 with v.g.locked():
  state=v.g.read();key=state['rpc_budget']['inflight'][ticket]['key'];state['rpc_budget']['keys'][key]['pid']=pid;v.g.save(state)
 original=v.g.read();digest=hashlib.sha256(v.g.path.read_bytes()).hexdigest();assert v.g.reconcile_terminated_http(ticket,expected_pid=pid,expected_sha256=digest)=='RECONCILED_UNCERTAIN_NO_REFUND'
 after=v.g.read();assert after['rpc_budget']['scopes']==original['rpc_budget']['scopes'];assert after['reservations']==original['reservations'] and after['outcomes']==original['outcomes'];assert after['state']==original['state'];assert not after['rpc_budget']['inflight'];assert after['rpc_budget']['keys'][key]['status']=='OWNER_EXIT_UNCERTAIN'
 fresh=v.m.Gate(v.g.path,'fixture',clock=lambda:v.now[0]);fresh.monotonic=lambda:v.now[0]
 assert fresh.reconcile_terminated_http(ticket,expected_pid=pid,expected_sha256=hashlib.sha256(fresh.path.read_bytes()).hexdigest())=='ALREADY_RECONCILED'
 with pytest.raises(v.m.BudgetDeferred,match='REQUIRES_REVIEW'):v.reserve2(key='uncertain')
 v.root_id('other');new=v.reserve2(alias='mint:other',key='eligible');v.g.finish_http(new,200)
 assert v.g.read()['rpc_budget']['scopes'][v.h._hash('mint:root')]['credits']==1


def test_duplicate_terminal_ack_is_idempotent_after_uncertain_reconciliation(v,monkeypatch):
 v.root_id('root');v.open();ticket=v.reserve2()
 # Simulated OS process absence is allowed only in this isolated fixture.
 monkeypatch.setattr(os,'kill',lambda *a:(_ for _ in ()).throw(ProcessLookupError()))
 v.g.reconcile_terminated_http(ticket,expected_pid=os.getpid(),expected_sha256=hashlib.sha256(v.g.path.read_bytes()).hexdigest());before=v.g.path.read_bytes();v.g.finish_http(ticket,200)
 assert v.g.path.read_bytes()==before


def test_spending_ceiling_and_warning_survive_owner_reconciliation(v,monkeypatch):
 root=v.root_id('root');v.open()
 with v.g.locked():
  s=v.g.read();q=s['rpc_budget']['scopes'][root];q['credits']=99999;q['workers']['listener']=99999;q['warned']=True;v.g.save(s)
 ticket=v.reserve2();monkeypatch.setattr(os,'kill',lambda *a:(_ for _ in ()).throw(ProcessLookupError()))
 v.g.reconcile_terminated_http(ticket,expected_pid=os.getpid(),expected_sha256=hashlib.sha256(v.g.path.read_bytes()).hexdigest())
 with pytest.raises(v.m.BudgetDeferred,match='CREDIT_LIMIT'):v.reserve2(key='over-limit')
 assert v.g.read()['rpc_budget']['scopes'][root]['credits']==100000 and v.g.read()['rpc_budget']['scopes'][root]['warned']


def test_disk_refusal_never_forgets_charge_or_releases_ambiguous_ticket(v,monkeypatch):
 v.root_id('root');v.open();ticket=v.reserve2();before=v.g.read();monkeypatch.setattr(os,'statvfs',lambda *a:types.SimpleNamespace(f_bavail=0,f_frsize=4096))
 with pytest.raises(v.m.Integrity,match='DISK_FLOOR'):v.g.finish_http(ticket,200)
 # Existing publication marker deliberately fails closed on incomplete durability.
 with pytest.raises(v.m.Integrity):v.g.read()
 raw=json.loads(v.g.path.read_text());assert raw==before and raw['rpc_budget']['scopes'][v.h._hash('mint:root')]['credits']==1


def test_three_cross_process_orphans_require_dead_owners_and_preserve_every_charge(v):
 v.root_id('root');v.open();context=multiprocessing.get_context('fork');pids=[]
 def reserve_child(role,count):
  for i in range(count):v.reserve2(key=role+str(i),role=role)
 for role,count in [('walkback',2),('listener',1)]:
  child=context.Process(target=reserve_child,args=(role,count));child.start();pids.append(child.pid);child.join(timeout=3);assert child.exitcode==0
 before=v.g.read();assert len(before['rpc_budget']['inflight'])==3;assert before['rpc_budget']['scopes'][v.h._hash('mint:root')]['credits']==3
 for ticket,row in list(before['rpc_budget']['inflight'].items()):
  owner=before['rpc_budget']['keys'][row['key']]['pid'];assert owner in pids
  v.g.reconcile_terminated_http(ticket,expected_pid=owner,expected_sha256=hashlib.sha256(v.g.path.read_bytes()).hexdigest())
 after=v.g.read();assert not after['rpc_budget']['inflight'];assert after['rpc_budget']['scopes']==before['rpc_budget']['scopes'];assert after['outcomes']==before['outcomes'];assert len([k for k in after['rpc_budget']['keys'].values() if k['status']=='OWNER_EXIT_UNCERTAIN'])==3
 for role,count in [('walkback',2),('listener',1)]:
  for i in range(count):
   with pytest.raises(v.m.BudgetDeferred,match='REQUIRES_REVIEW'):v.reserve2(key=role+str(i),role=role)
 ticket=v.reserve2(key='other-eligible');v.g.finish_http(ticket,200)
 assert v.g.read()['rpc_budget']['scopes'][v.h._hash('mint:root')]['credits']==4


def test_crash_after_completion_commit_duplicate_close_never_refunds_or_double_counts(v,monkeypatch):
 v.root_id('root');v.open();v.h.install('listener');c=http.client.HTTPConnection('mainnet.helius-rpc.com');c.sock=Wire()
 with v.h.investigation('mint:root'):c.request('POST','/',body=b'{"method":"getTransaction","params":["fixture-signature"]}')
 response=c.getresponse();save=v.g.save;once=[True]
 def committed_then_interrupted(state):
  save(state)
  if once[0]:once[0]=False;raise RuntimeError('AFTER_DURABLE_COMPLETION')
 monkeypatch.setattr(v.g,'save',committed_then_interrupted)
 with pytest.raises(RuntimeError,match='AFTER_DURABLE'):response.read()
 response.close();state=v.g.read();assert state['outcomes']==1 and not state['rpc_budget']['inflight'];assert state['rpc_budget']['scopes'][v.h._hash('mint:root')]['credits']==1


def test_uncertain_owner_receipt_cannot_be_tampered_or_evicted(v,monkeypatch):
 v.root_id('root');v.open();ticket=v.reserve2();monkeypatch.setattr(os,'kill',lambda *a:(_ for _ in ()).throw(ProcessLookupError()))
 v.g.reconcile_terminated_http(ticket,expected_pid=os.getpid(),expected_sha256=hashlib.sha256(v.g.path.read_bytes()).hexdigest());state=v.g.read();key=next(iter(state['rpc_budget']['keys']));state['rpc_budget']['keys'][key]['owner_exit']['reason']='KNOWN_SUCCESS';v.g.save(state)
 with pytest.raises(v.m.Integrity,match='OWNER_EXIT_EVIDENCE'):v.g.read()


def test_operator_single_probe_held_owner_scope_accepts_completion_lock_mode(v,monkeypatch):
 # The qualified operator scope reuses an actually-held owner lock. Keyword
 # forwarding matters: do not reuse old no-argument private lock shims.
 v.root_id('root');real_lock=v.g.locked
 with real_lock():
  with monkeypatch.context() as patch:
   patch.setattr(v.g,'locked',lambda **kwargs:contextlib.nullcontext())
   v.g.authorize_probe(hashlib.sha256(v.g.path.read_bytes()).hexdigest());ticket=v.reserve2()
 with real_lock():
  with monkeypatch.context() as patch:
   patch.setattr(v.g,'locked',lambda **kwargs:contextlib.nullcontext());v.g.finish_http(ticket,200)
 state=v.g.read();assert state['state']=='OPEN' and not state['rpc_budget']['inflight'];assert state['rpc_budget']['scopes'][v.h._hash('mint:root')]['credits']==1
