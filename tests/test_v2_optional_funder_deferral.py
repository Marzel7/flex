"""Exact runtime functions, disposable OPS/Core/ledger and blocked network."""
import ast,contextlib,copy,hashlib,json,os,pathlib,sqlite3,time,types,typing,urllib.request
import pytest
from tests.test_v2_helius_investigation_budget import v,legacy
ROOT=pathlib.Path(__file__).resolve().parents[1]
WORKER=ROOT/'runtime/walkback/src/core/walkback_worker.py'
FUNCTIONS={'promote_recurring_funders','_is_known_treasury','_is_known_subprov','_is_program_owned','_get_account_owner','_rpc','drain_batch'}

@pytest.fixture
def case(v,monkeypatch):
 e=v;e.root_id('root');e.open();e.h._role='walkback';e.now[0]+=120
 ticket=e.reserve2(key='retained-history',role='walkback');e.g.finish_http(ticket,200)
 tree=ast.parse(WORKER.read_text());nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in FUNCTIONS]
 ns={'_SYSTEM_PROGRAM':'11111111111111111111111111111111','sqlite3':sqlite3,'Optional':typing.Optional,'RpcTransport':typing.Callable,'json':json,'os':os,'urllib':urllib,'time':time,'RPC_URL':'https://mainnet.helius-rpc.com/?api-key=FIXTURE_NOT_REAL','RPC_TIMEOUT':1,'BATCH_SIZE':5,'RPC_BUDGET_BATCH':25,'MAX_ATTEMPTS':5,'_FUNDER_BLOCKLIST':set(),'_is_known_infrastructure':lambda _:False,'AcquisitionUnavailable':type('AcquisitionUnavailable',(RuntimeError,),{}),'deep_walkback':types.SimpleNamespace(ensure_schema=lambda _:None)}
 exec(compile(ast.Module(body=nodes,type_ignores=[]),str(WORKER),'exec'),ns)
 schema_tree=ast.parse((ROOT/'tests/test_x26_3_subprov_infrastructure_exclusion.py').read_text());schema=ast.literal_eval(next(n.value for n in schema_tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='SCHEMA' for t in n.targets)))
 path=e.root/'ops.sqlite';ops=sqlite3.connect(path);ops.row_factory=sqlite3.Row;ops.executescript(schema);ops.executescript('ALTER TABLE wt_walkback_queue ADD COLUMN lease_expires_at INTEGER;ALTER TABLE wt_walkback_queue ADD COLUMN next_retry_at INTEGER;ALTER TABLE wt_walkback_queue ADD COLUMN priority INTEGER DEFAULT 0;CREATE TABLE wt_treasury_review(detected_via TEXT,status TEXT);')
 def seed(wallet,n=2):
  for i in range(n):ops.execute("INSERT INTO wt_walkback_queue(mint,creator,funder_wallet,funder_block_time,funding_mechanism,intelligence_outcome,status,attempts,enqueued_at) VALUES(?,?,?,?,?,?,'complete',0,1)",(wallet+'-'+str(i),'creator-'+str(i),wallet,100+i,'PLAIN_XFER','NO_ATTRIBUTION_FOUND'))
  ops.commit()
 wire=[];response_owner=['11111111111111111111111111111111']
 @contextlib.contextmanager
 def fake_transport(req,*,timeout):
  reservation=e.h._admit(req.full_url,'POST',req.data)
  wire.append(json.loads(req.data));response=types.SimpleNamespace(read=lambda:json.dumps({'result':{'value':{'owner':response_owner[0]}}}).encode())
  try:yield response
  finally:e.h._finish(reservation,200)
 monkeypatch.setattr(e.m,'urlopen',fake_transport)
 e.ops=ops;e.ns=ns;e.seed=seed;e.wire=wire;e.owner=response_owner;e.path=path
 yield e
 ops.close()

def budgets(e):return e.g.path.read_bytes()
def queue(e):return [tuple(r) for r in e.ops.execute('SELECT * FROM wt_walkback_queue ORDER BY mint')]

@pytest.mark.parametrize('wallet',['case-A','case-B'])
def test_two_unowned_candidates_are_retained_without_spend(case,wallet,capsys):
 e=case;e.seed(wallet);before=budgets(e);evidence=queue(e)
 assert e.ns['promote_recurring_funders'](e.ops)==0
 assert not e.wire and budgets(e)==before and queue(e)==evidence
 assert e.ops.execute('select count(*) from wt_discovered_subprovs').fetchone()[0]==0
 assert 'HELIUS_INVESTIGATION_REQUIRED' in capsys.readouterr().out

def test_both_deferrals_continue_to_known_local_candidate(case,capsys):
 e=case;e.seed('a-unowned');e.seed('b-unowned');e.seed('z-known',3)
 e.ops.execute("INSERT INTO wt_discovered_subprovs(subprov,creator_count,treasury,treasury_known,confidence,state) VALUES('z-known',1,'retained-treasury',1,0.8,'PROVISION_CANDIDATE')");e.ops.commit();before=budgets(e);evidence=queue(e)
 assert e.ns['promote_recurring_funders'](e.ops)==1
 assert capsys.readouterr().out.count('ownership check deferred')==2
 row=e.ops.execute('select creator_count,treasury,confidence,state from wt_discovered_subprovs').fetchone();assert tuple(row)==(3,'retained-treasury',0.8,'PROVISION_CANDIDATE')
 assert not e.wire and budgets(e)==before and queue(e)==evidence

def test_real_batch_returns_after_optional_deferral(case):
 e=case;e.seed('unowned')
 for i in range(3):e.ops.execute("INSERT INTO wt_walkback_queue(mint,creator,status,walkback_class,attempts,enqueued_at) VALUES(?,?,'pending','FULL_WALKBACK',0,1)",(f'pending-{i}',f'creator-pending-{i}'))
 e.ops.commit()
 def claim(ops,mint):ops.execute("UPDATE wt_walkback_queue SET status='running',attempts=attempts+1 WHERE mint=?",(mint,));ops.commit();return True
 def process(ops,row):ops.execute("UPDATE wt_walkback_queue SET status='complete',intelligence_outcome='UNCHANGED_FIXTURE_RESULT' WHERE mint=?",(row['mint'],));ops.commit();return 0
 e.ns['_mark_running']=claim;e.ns['_process_row']=process
 before=budgets(e);result=e.ns['drain_batch'](e.ops)
 assert result['processed']==3 and result['outcomes']=={'UNCHANGED_FIXTURE_RESULT':3} and result['leads_promoted']==0
 assert not e.wire and budgets(e)==before

def test_valid_durable_owner_acquisition_and_repeat_dedup(case):
 e=case;e.seed('owned-candidate');before=e.g.read()['rpc_budget'];alias=e.h._hash('mint:root')
 with e.h.resume_investigation(alias):assert e.ns['promote_recurring_funders'](e.ops)==1
 assert len(e.wire)==1 and e.wire[0]['method']=='getAccountInfo'
 after=e.g.read()['rpc_budget'];assert after['scopes'][alias]['credits']==before['scopes'][alias]['credits']+1;assert after['policy']['lifetime_credits']==100000;assert after['owners']==before['owners']
 e.ns['promote_recurring_funders'](e.ops);assert len(e.wire)==1
 assert e.ops.execute('select count(*) from wt_discovered_subprovs').fetchone()[0]==1

def test_program_owned_response_never_promotes(case):
 e=case;e.seed('program-account');e.owner[0]='FixtureTokenProgram'
 with e.h.resume_investigation(e.h._hash('mint:root')):assert e.ns['promote_recurring_funders'](e.ops)==0
 assert len(e.wire)==1 and e.ops.execute('select count(*) from wt_discovered_subprovs').fetchone()[0]==0

@pytest.mark.parametrize('code',['HELIUS_GATE_BUSY','HELIUS_INVESTIGATION_CREDIT_LIMIT','HELIUS_REQUEST_COOLDOWN'])
def test_other_typed_deferrals_propagate_unchanged(case,code):
 e=case;e.seed('candidate');error=e.m.BudgetDeferred(code)
 def fail(*_):raise error
 e.ns['_is_program_owned']=fail;before=budgets(e)
 with pytest.raises(e.m.BudgetDeferred) as caught:e.ns['promote_recurring_funders'](e.ops)
 assert caught.value is error and budgets(e)==before and not e.wire

def test_integrity_failure_not_masked(case):
 e=case;e.seed('candidate');error=e.m.Integrity('HELIUS_GATE_PUBLICATION_AMBIGUOUS')
 def fail(*_):raise error
 e.ns['_is_program_owned']=fail
 with pytest.raises(e.m.Integrity) as caught:e.ns['promote_recurring_funders'](e.ops)
 assert caught.value is error and not e.wire

def test_repeated_restart_keeps_unowned_evidence_and_history(case,monkeypatch):
 e=case;e.seed('candidate');before=budgets(e);evidence=queue(e)
 for _ in range(3):
  fresh=e.m.Gate(e.g.path,'fixture',clock=lambda:e.now[0]);fresh.monotonic=lambda:e.now[0];monkeypatch.setattr(e.m,'current',lambda:fresh)
  assert e.ns['promote_recurring_funders'](e.ops)==0
 assert budgets(e)==before and queue(e)==evidence and not e.wire

def test_unchanged_row_attribution_and_gate_owners():
 # Only the optional candidate error boundary may differ from engineering base.
 import subprocess
 before=ast.parse(subprocess.check_output(['git','show','7cda51e3809c74d149f599870c01dbf276a18e64:runtime/walkback/src/core/walkback_worker.py'],cwd=ROOT,text=True));after=ast.parse(WORKER.read_text())
 b={n.name:ast.dump(n) for n in before.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))};a={n.name:ast.dump(n) for n in after.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))}
 assert {k for k in b if b[k]!=a[k]}=={'promote_recurring_funders'}

@pytest.mark.parametrize('guard',['static_blocklist','known_infrastructure','confirmed_treasury'])
def test_existing_exclusions_unchanged(case,guard):
 e=case;e.seed('excluded-candidate');before=budgets(e)
 if guard=='static_blocklist':e.ns['_FUNDER_BLOCKLIST'].add('excluded-candidate')
 elif guard=='known_infrastructure':e.ns['_is_known_infrastructure']=lambda _:True
 else:e.ops.execute("INSERT INTO wt_confirmed_treasuries(treasury) VALUES('excluded-candidate')");e.ops.commit()
 assert e.ns['promote_recurring_funders'](e.ops)==0
 assert budgets(e)==before and not e.wire and e.ops.execute('select count(*) from wt_discovered_subprovs').fetchone()[0]==0

def test_rejected_candidate_protected_when_unowned(case):
 e=case;e.seed('rejected-candidate');e.ops.execute("INSERT INTO wt_discovered_subprovs(subprov,creator_count,confidence,state,rejected_reason) VALUES('rejected-candidate',2,0.1,'REJECTED_INFRASTRUCTURE','retained-counterevidence')");e.ops.commit();before=tuple(e.ops.execute('select * from wt_discovered_subprovs').fetchone());money=budgets(e)
 assert e.ns['promote_recurring_funders'](e.ops)==0
 assert tuple(e.ops.execute('select * from wt_discovered_subprovs').fetchone())==before and budgets(e)==money and not e.wire

def test_owned_program_check_restart_cannot_duplicate_request(case,monkeypatch):
 e=case;e.seed('program-candidate');e.owner[0]='FixtureTokenProgram';alias=e.h._hash('mint:root')
 with e.h.resume_investigation(alias):assert e.ns['promote_recurring_funders'](e.ops)==0
 money=budgets(e);fresh=e.m.Gate(e.g.path,'fixture',clock=lambda:e.now[0]);fresh.monotonic=lambda:e.now[0];monkeypatch.setattr(e.m,'current',lambda:fresh)
 with e.h.resume_investigation(alias):
  with pytest.raises(e.m.BudgetDeferred,match='HELIUS_REQUEST_COOLDOWN'):e.ns['promote_recurring_funders'](e.ops)
 assert len(e.wire)==1 and budgets(e)==money
