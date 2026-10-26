"""Verified public billing weights; disposable ledger/Core and blocked network."""
import asyncio,copy,hashlib,json,os,sqlite3,sys,types
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import pytest
from tests.test_v2_helius_rpc_budget import e as legacy
ROOT=Path(__file__).resolve().parents[1]
DOC=ROOT/'docs/dev/lightweight_dev004_first_v2/helius_investigation_budget'

@pytest.fixture
def v(legacy,monkeypatch):
 e=legacy;p=json.loads((DOC/'POLICY.json').read_text());e.provision(p)
 core=e.root/'core.sqlite'
 with sqlite3.connect(core) as c:c.execute('CREATE TABLE token_analysis(mint TEXT PRIMARY KEY)')
 monkeypatch.setenv('DB_PATH',str(core));e.core=core
 def root(mint):
  with sqlite3.connect(core) as c:c.execute('INSERT OR IGNORE INTO token_analysis VALUES(?)',(mint,))
  proof=e.h.core_identity_proof(mint)
  return e.g.register_origin('mint:'+mint,proof)
 def reserve(alias='mint:root',key='one',role='listener',selector='rpc:getTransaction',page=False,origin='endpoint'):
  return e.g.reserve_http(role=role,scopes=(e.h._hash(alias),),key=e.h._hash(key),selector=selector,page=page,origin=e.h._hash(origin))
 e.root_id=root;e.reserve2=reserve;e.policy=p
 return e


def test_revised_policy_has_no_hour_day_or_worker_credit_quota(v):
 assert set(v.policy)=={'version','lifetime_credits','warning_credits','requests_per_minute','inflight','costs'}
 assert v.policy['lifetime_credits']==100000 and v.policy['warning_credits']==50000
 b=v.g.read()['rpc_budget'];assert 'windows' not in b and 'rate' in b
 assert b['policy']['version']==2 and v.g.read()['state']=='PAYMENT_DENIED'

@pytest.mark.parametrize('key',['hour','day','workers','minute'])
def test_aggregate_credit_quotas_cannot_reappear_in_version2(v,key):
 p=dict(v.policy);p[key]=1
 with pytest.raises(v.m.Integrity):v.m._policy(p)


def test_verified_weights_match_frozen_public_billing_authority(v):
 digest=hashlib.sha256((DOC/'BILLING_AUTHORITY.json').read_bytes()).hexdigest()
 assert all(row['evidence_sha256']==digest for row in v.policy['costs'].values())
 assert v.policy['costs']['rpc:getTransaction']['credits']==1
 assert v.policy['costs']['rpc:getTransactionsForAddress:signatures']['credits']==10
 assert v.policy['costs']['rpc:getTransactionsForAddress:full:band10']['credits']==100
 assert not any(k.startswith('rest:transaction-batch') for k in v.policy['costs'])


def test_lifetime_100000_warning_50000_and_other_origin_continues(v,caplog):
 root=v.root_id('root');v.open();allowed=0
 for i in range(1200):
  v.now[0]+=2
  try:
   t=v.reserve2(key=str(i),selector='rpc:getTransactionsForAddress:full:band10');v.g.finish_http(t,200);allowed+=1
  except v.m.BudgetDeferred as ex:assert 'INVESTIGATION_CREDIT_LIMIT' in str(ex)
 assert allowed==1000
 b=v.g.read()['rpc_budget'];q=b['scopes'][root];assert q['credits']==100000 and q['warned'] and q['requests']==1000
 assert sum('HELIUS_INVESTIGATION_WARNING' in r.message for r in caplog.records)==1
 v.root_id('unrelated');t=v.reserve2(alias='mint:unrelated',key='unrelated');v.g.finish_http(t,200)
 assert v.g.read()['rpc_budget']['scopes'][v.h._hash('mint:unrelated')]['credits']==1


def test_two_thousand_independent_origins_exceed_aggregate_100k_without_quota(v):
 # No live/V1 data, copies or calls. Every origin has a real indexed fixture
 # identity, and every cost is the published full-history upper bound.
 with sqlite3.connect(v.core) as c:c.executemany('INSERT INTO token_analysis VALUES(?)',[(f'independent-{i}',) for i in range(2000)])
 v.open()
 for i in range(2000):
  mint=f'independent-{i}';v.g.register_origin('mint:'+mint,v.h.core_identity_proof(mint));v.now[0]+=2
  t=v.reserve2(alias='mint:'+mint,key='request-'+str(i),role=v.m.ROLES[i%3],selector='rpc:getTransactionsForAddress:full:band10');v.g.finish_http(t,200)
 b=v.g.read()['rpc_budget'];assert len(b['scopes'])==2000
 assert sum(q['credits'] for q in b['scopes'].values())==200000
 assert all(q['credits']==100 for q in b['scopes'].values())
 assert v.g.path.stat().st_size<=v.m.INVESTIGATION_STATE_BYTES


def test_child_wallet_work_cross_worker_restart_cannot_change_owner(v):
 root=v.root_id('root');v.open();parent=v.h._hash('mint:root');child=v.h._hash('job:child');wallet=v.h._hash('job:wallet')
 v.g.bind_child(parent,child,'1'*64);v.g.bind_child(child,wallet,'2'*64)
 for i,alias in enumerate(['mint:root','job:child','job:wallet']):
  t=v.reserve2(alias=alias,key=str(i),role=v.m.ROLES[i],page=True,origin=str(i));v.g.finish_http(t,200)
 fresh=v.m.Gate(v.g.path,'fixture',clock=lambda:v.now[0]);fresh.monotonic=lambda:v.now[0]
 assert fresh.investigation_owner(wallet)==root
 b=fresh.read()['rpc_budget'];q=b['scopes'][root]
 assert q['credits']==3 and q['pages']==3 and q['fallbacks']==2 and q['workers']=={r:1 for r in v.m.ROLES}
 v.root_id('other')
 with pytest.raises(v.m.Integrity,match='REBIND'):fresh.bind_child(v.h._hash('mint:other'),wallet,'3'*64)
 assert fresh.read()['rpc_budget']['scopes'][root]==q


def test_changed_investigation_ids_and_repeated_launch_do_not_reset(v):
 root=v.root_id('root');v.open();t=v.reserve2();v.g.finish_http(t,200)
 for i in range(20):
  assert v.g.register_origin('mint:root',v.h._hash('another-proof-'+str(i)))==root
  child=v.h._hash('launch:'+str(i));v.g.bind_child(root,child,'4'*64)
  assert v.g.investigation_owner(child)==root
 assert v.g.read()['rpc_budget']['scopes'][root]['credits']==1
 with pytest.raises(v.m.BudgetDeferred,match='UNATTRIBUTED'):v.reserve2(alias='investigation:renamed',key='another')
 with pytest.raises(v.m.BudgetDeferred,match='ORIGIN_UNKNOWN'):
  with v.h.owned_context('mint:nonexistent'):pass
 assert len(v.g.read()['rpc_budget']['scopes'])==1


def test_context_children_remain_durably_bound_after_context_loss(v):
 root=v.root_id('root');v.open();v.h.install('listener')
 with v.h.owned_context('mint:root'):
  with v.h.investigation('job:child'):
   with v.h.investigation('job:grandchild'):
    alias=v.h._scope.get()[0]
    reservation=v.h._admit('https://mainnet.helius-rpc.com/','POST',b'{"method":"getTransaction","params":["s"]}')
 v.h._finish(reservation,200)
 assert not v.h._scope.get()
 with v.h.resume_investigation(alias):
  assert v.g.investigation_owner(v.h._scope.get()[0])==root
 assert v.g.read()['rpc_budget']['scopes'][root]['credits']==1


def test_request_rate_limits_requests_not_credits(v):
 v.root_id('root');v.open()
 for i in range(120):
  t=v.reserve2(key=str(i));v.g.finish_http(t,200)
 with pytest.raises(v.m.BudgetDeferred,match='REQUEST_RATE_LIMIT'):v.reserve2(key='burst')
 v.now[0]+=120;t=v.reserve2(key='after-window');v.g.finish_http(t,200)
 assert v.g.read()['rpc_budget']['scopes'][v.h._hash('mint:root')]['credits']==121


def test_origin_refuses_over_cap_before_any_wire_reservation(v):
 root=v.root_id('root');v.open()
 with v.g.locked():
  s=v.g.read();q=s['rpc_budget']['scopes'][root];q.update(credits=99950,warned=True);q['workers']['listener']=99950;v.g.save(s)
 before=v.g.read()
 with pytest.raises(v.m.BudgetDeferred,match='CREDIT_LIMIT'):v.reserve2(selector='rpc:getTransactionsForAddress:full:band10')
 assert v.g.read()==before


def test_concurrent_workers_cannot_cross_single_origin_limit(v):
 root=v.root_id('root');v.open()
 with v.g.locked():
  s=v.g.read();q=s['rpc_budget']['scopes'][root];q.update(credits=99999,warned=True);q['workers']['listener']=99999;v.g.save(s)
 def call(i):
  import time
  for _ in range(1000):
   try:return v.reserve2(key=str(i),role=v.m.ROLES[i%3])
   except v.m.Deferred as ex:
    if str(ex)!='HELIUS_GATE_BUSY':return None
    time.sleep(.001)
 with ThreadPoolExecutor(9) as pool:answers=list(pool.map(call,range(9)))
 assert len([x for x in answers if x])==1
 assert v.g.read()['rpc_budget']['scopes'][root]['credits']==100000


def test_crash_keeps_charge_inflight_owner_and_warning(v):
 root=v.root_id('root');v.open()
 with v.g.locked():
  s=v.g.read();q=s['rpc_budget']['scopes'][root];q['credits']=49999;q['workers']['listener']=49999;v.g.save(s)
 v.reserve2()
 fresh=v.m.Gate(v.g.path,'fixture',clock=lambda:v.now[0]);fresh.monotonic=lambda:v.now[0]
 b=fresh.read()['rpc_budget'];assert b['scopes'][root]['credits']==50000 and b['scopes'][root]['warned']
 assert len(b['inflight'])==1 and fresh.investigation_owner(root)==root
 with pytest.raises(v.m.BudgetDeferred,match='DUPLICATE'):v.reserve2()

@pytest.mark.parametrize('cap,expected',[('INVESTIGATION_MAX_ROOTS','ORIGIN_CAPACITY'),('INVESTIGATION_MAX_IDENTITIES','CHILD_IDENTITY_CAPACITY'),('INVESTIGATION_MAX_REQUESTS','REQUEST_IDENTITY_CAPACITY')])
def test_finite_ledger_refuses_without_eviction_or_allowance_reset(v,monkeypatch,cap,expected):
 root=v.root_id('root');v.open();t=v.reserve2();v.g.finish_http(t,200)
 monkeypatch.setattr(v.m,cap,1);before=v.g.read()
 with pytest.raises(v.m.BudgetDeferred,match=expected):
  if cap=='INVESTIGATION_MAX_ROOTS':v.root_id('other')
  elif cap=='INVESTIGATION_MAX_IDENTITIES':v.g.bind_child(root,v.h._hash('new-child'),'9'*64)
  else:v.reserve2(key='new-request')
 assert v.g.read()==before
 assert v.g.investigation_owner(root)==root and v.g.read()['rpc_budget']['scopes'][root]['credits']==1


def test_same_wallet_can_be_a_child_of_independent_origins(v):
 roots=[v.root_id(name) for name in ('first','second')];aliases=[]
 for name in ('first','second'):
  with v.h.owned_context('mint:'+name):
   with v.h.investigation('wallet:shared'):
    aliases.append(v.h._scope.get()[0])
 assert aliases[0]!=aliases[1]
 assert [v.g.investigation_owner(alias) for alias in aliases]==roots


def test_signature_only_unknown_subject_stays_unattributed_and_no_dispatch(v,monkeypatch):
 module=__import__('tests.test_v2_helius_rpc_budget',fromlist=['load']).load('origin_journal',ROOT/'runtime/listener/src/core/migration_signature_journal.py')
 journal=module.MigrationJournal(v.root/'journal.jsonl');journal.initialize();journal.admit('sig','PUMPSWAP',1)
 import src.core
 monkeypatch.setitem(sys.modules,'src.core.migration_signature_journal',module);monkeypatch.setattr(src.core,'migration_signature_journal',module,raising=False)
 monkeypatch.setenv('MIGRATION_JOURNAL_PATH',str(journal.path));v.open()
 with pytest.raises(v.m.BudgetDeferred,match='ORIGIN_UNKNOWN'):
  with v.h.owned_context('migration:sig'):pass
 assert v.g.read()['reservations']==0 and journal.snapshot()[0]['attempts']==0


def test_unknown_cost_cannot_leak_origin_allowance(v):
 root=v.root_id('root');v.open();before=v.g.read()
 with pytest.raises(v.m.BudgetDeferred,match='UNKNOWN_REQUEST_COST'):v.reserve2(selector='rpc:unpriced')
 assert v.g.read()==before and v.g.read()['rpc_budget']['scopes'][root]['credits']==0


def test_repeated_signature_after_restart_does_not_reset_root(v):
 root=v.root_id('root');v.open();t=v.reserve2(key='same-signature');v.g.finish_http(t,200)
 v.now[0]+=60
 fresh=v.m.Gate(v.g.path,'fixture',clock=lambda:v.now[0]);fresh.monotonic=lambda:v.now[0]
 fresh.register_origin('mint:root',v.h._hash('new-launch'))
 t=v.reserve2(key='same-signature',role='walkback');v.g.finish_http(t,200)
 q=fresh.read()['rpc_budget']['scopes'][root]
 assert q['credits']==2 and q['retries']==1 and q['workers']['walkback']==1


def test_origin_qualification_is_indexed_and_read_only(v):
 v.root_id('root')
 with sqlite3.connect(v.core) as c:
  plan=c.execute('EXPLAIN QUERY PLAN SELECT mint FROM token_analysis WHERE mint=? LIMIT 1',('root',)).fetchall()
 assert any('SEARCH' in row[3] and 'INDEX' in row[3] for row in plan)
 proof=v.h.core_identity_proof('root');assert len(proof)==64
 with pytest.raises(v.m.BudgetDeferred):v.h.core_identity_proof('absent')


def test_hours_days_and_restarts_never_refill_origin(v):
 root=v.root_id('root');v.root_id('other');v.open()
 with v.g.locked():
  state=v.g.read();q=state['rpc_budget']['scopes'][root];q.update(credits=100000,warned=True);q['workers']['listener']=100000;v.g.save(state)
 v.now[0]+=3*86400
 restarted=v.m.Gate(v.g.path,'fixture',clock=lambda:v.now[0]);restarted.monotonic=lambda:v.now[0]
 assert restarted.register_origin('mint:root','7'*64)==root
 with pytest.raises(v.m.BudgetDeferred,match='CREDIT_LIMIT'):v.reserve2()
 ticket=v.reserve2(alias='mint:other',key='other');v.g.finish_http(ticket,200)
 assert restarted.read()['rpc_budget']['scopes'][root]['credits']==100000


def test_new_public_configuration_rejects_old_global_quota_policy(legacy):
 from tests.test_v2_helius_rpc_budget import policy
 e=legacy;before=e.g.read()
 with pytest.raises(e.m.Integrity,match='V2_REQUIRED'):e.g.configure_budget(policy(),hashlib.sha256(e.g.path.read_bytes()).hexdigest())
 assert e.g.read()==before


def test_real_http_identity_authority_rejects_legacy_budget(legacy,monkeypatch):
 from tests.test_v2_helius_rpc_budget import policy
 e=legacy;e.provision(policy());stable=e.root/'helius-denial-v1.json';stable.write_bytes(e.g.path.read_bytes());Path(str(stable)+'.lock').write_bytes(Path(str(e.g.path)+'.lock').read_bytes())
 monkeypatch.setenv('MIGRATION_JOURNAL_PATH',str(e.root/'migration.jsonl'));monkeypatch.setenv('HELIUS_DENIAL_GATE_PATH',str(stable));monkeypatch.setattr(e.m,'authority',lambda:'fixture')
 with pytest.raises(e.m.Integrity,match='V2_REQUIRED'):e.original_current()


def test_metered_rpc_variants_reserve_verified_worst_case(v):
 v.root_id('root');v.open();v.h.install('listener')
 with v.h.owned_context('mint:root'):
  reservation=v.h._admit('https://mainnet.helius-rpc.com/','POST',json.dumps({'method':'getTransactionsForAddress','params':['wallet',{'transactionDetails':'full','limit':1000}]}).encode());v.h._finish(reservation,200)
  reservation=v.h._admit('https://mainnet.helius-rpc.com/','POST',json.dumps({'method':'getTransactionsForAddress','params':['wallet',{'transactionDetails':'signatures','limit':1000}]}).encode());v.h._finish(reservation,200)
  with pytest.raises(v.m.BudgetDeferred,match='UNKNOWN_REQUEST_COST'):v.h._admit('https://mainnet.helius-rpc.com/','POST',json.dumps({'method':'getTransactionsForAddress','params':['wallet',{'transactionDetails':'full','limit':1001}]}).encode())
 assert v.g.read()['rpc_budget']['scopes'][v.h._hash('mint:root')]['credits']==110


def test_missing_shared_state_blocks_paid_channel_without_listener_exit(legacy,monkeypatch):
 e=legacy;monkeypatch.setenv('MIGRATION_JOURNAL_PATH',str(e.root/'migration.jsonl'));monkeypatch.setenv('HELIUS_DENIAL_GATE_PATH',str(e.root/'helius-denial-v1.json'));monkeypatch.setattr(e.m,'authority',lambda:'fixture');monkeypatch.setattr(e.m,'current',e.original_current)
 assert e.m.blocked() is True


def test_separate_process_children_share_final_remaining_credit(v):
 import subprocess
 root=v.root_id('root');v.open()
 aliases=[]
 for i in range(3):
  alias=v.h._hash('process-child:'+str(i));v.g.bind_child(root,alias,'8'*64);aliases.append(alias)
 with v.g.locked():
  state=v.g.read();q=state['rpc_budget']['scopes'][root];q.update(credits=99999,warned=True);q['workers']['listener']=99999;v.g.save(state)
 code="""import importlib.util,sys,time,socket
socket.socket.connect=lambda *a,**k:(_ for _ in ()).throw(AssertionError('NETWORK_FORBIDDEN'))
s=importlib.util.spec_from_file_location('g',sys.argv[1]);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
g=m.Gate(sys.argv[2],'fixture',clock=lambda:1000);g.monotonic=lambda:1000
for _ in range(400):
 try:
  g.reserve_http(role=sys.argv[3],scopes=(sys.argv[4],),key=sys.argv[5]*64,selector='rpc:getTransaction',origin='9'*64);print('RESERVED');break
 except m.Deferred as ex:
  if str(ex)!='HELIUS_GATE_BUSY':print(str(ex));break
  time.sleep(.002)
"""
 children=[subprocess.Popen([sys.executable,'-B','-c',code,str(ROOT/'runtime/listener/src/core/helius_denial_gate.py'),str(v.g.path),v.m.ROLES[i],aliases[i],str(i+1)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for i in range(3)]
 answers=[]
 for child in children:
  out,err=child.communicate(timeout=20);assert child.returncode==0,err;answers.append(out.strip())
 assert answers.count('RESERVED')==1 and answers.count('HELIUS_INVESTIGATION_CREDIT_LIMIT')==2
 assert v.g.read()['rpc_budget']['scopes'][root]['credits']==100000
 assert all(v.g.investigation_owner(alias)==root for alias in aliases)


def test_descendant_token_relaunch_cannot_obtain_a_new_allowance(v):
 root=v.root_id('root')
 with sqlite3.connect(v.core) as conn:conn.execute('INSERT INTO token_analysis VALUES(?)',('descendant',))
 v.open()
 with v.h.owned_context('mint:root'):
  with v.h.investigation('mint:descendant'):
   assert v.g.investigation_owner(v.h._scope.get()[0])==root
 t=v.reserve2(alias='mint:descendant',key='before-relaunch');v.g.finish_http(t,200)
 with v.h.owned_context('mint:descendant'):
  assert v.g.investigation_owner(v.h._scope.get()[0])==root
 t=v.reserve2(alias='mint:descendant',key='after-relaunch',role='walkback');v.g.finish_http(t,200)
 b=v.g.read()['rpc_budget'];assert len(b['scopes'])==1 and b['scopes'][root]['credits']==2


def test_existing_independent_origin_is_not_silently_reparented(v):
 first=v.root_id('root');second=v.root_id('other');v.open()
 with v.h.owned_context('mint:root'):
  with pytest.raises(v.m.Integrity,match='REBIND'):
   with v.h.investigation('mint:other'):pass
 t=v.reserve2(alias='mint:other',key='independent');v.g.finish_http(t,200)
 b=v.g.read()['rpc_budget'];assert b['scopes'][first]['credits']==0 and b['scopes'][second]['credits']==1


def test_open_gate_without_policy_defers_before_claims(legacy,monkeypatch):
 e=legacy;e.open();stable=e.root/'helius-denial-v1.json';stable.write_bytes(e.g.path.read_bytes());Path(str(stable)+'.lock').write_bytes(Path(str(e.g.path)+'.lock').read_bytes())
 monkeypatch.setenv('MIGRATION_JOURNAL_PATH',str(e.root/'migration.jsonl'));monkeypatch.setenv('HELIUS_DENIAL_GATE_PATH',str(stable));monkeypatch.setattr(e.m,'authority',lambda:'fixture');monkeypatch.setattr(e.m,'current',e.original_current)
 before=stable.read_bytes();assert e.m.blocked() is True;assert stable.read_bytes()==before


@pytest.mark.parametrize('limit,expected',[(1,10),(100,10),(101,20),(250,30),(1000,100)])
def test_full_history_uses_verified_request_limit_upper_bound(v,limit,expected):
 root=v.root_id('root');v.open();v.h.install('listener')
 with v.h.owned_context('mint:root'):
  reservation=v.h._admit('https://mainnet.helius-rpc.com/','POST',json.dumps({'method':'getTransactionsForAddress','params':['wallet',{'transactionDetails':'full','limit':limit}]}).encode());v.h._finish(reservation,200)
 assert v.g.read()['rpc_budget']['scopes'][root]['credits']==expected


def test_actual_http_wire_stops_capped_origin_and_allows_another(v):
 import http.client
 from tests.test_v2_helius_rpc_budget import Wire
 root=v.root_id('root');other=v.root_id('other');v.open();v.h.install('listener')
 with v.g.locked():
  state=v.g.read();q=state['rpc_budget']['scopes'][root];q.update(credits=99999,warned=True);q['workers']['listener']=99999;v.g.save(state)
 class VerifiedWire(Wire):
  def sendall(self,body):
   assert v.g.read()['rpc_budget']['scopes'][root]['credits']==100000
   return super().sendall(body)
 c=http.client.HTTPConnection('mainnet.helius-rpc.com');c.sock=VerifiedWire()
 with v.h.owned_context('mint:root'):c.request('POST','/',body=b'{"method":"getTransaction","params":["last"]}')
 response=c.getresponse();response.read();response.close()
 blocked=http.client.HTTPConnection('mainnet.helius-rpc.com');blocked.sock=Wire()
 with v.h.owned_context('mint:root'):
  with pytest.raises(v.m.BudgetDeferred,match='CREDIT_LIMIT'):blocked.request('POST','/',body=b'{"method":"getTransaction","params":["over"]}')
 assert blocked.sock.sent==[]
 allowed=http.client.HTTPConnection('mainnet.helius-rpc.com');allowed.sock=Wire()
 with v.h.owned_context('mint:other'):allowed.request('POST','/',body=b'{"method":"getTransaction","params":["other"]}')
 response=allowed.getresponse();response.read();response.close()
 assert v.g.read()['rpc_budget']['scopes'][other]['credits']==1
