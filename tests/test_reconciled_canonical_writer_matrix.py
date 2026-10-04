"""Test-only failure matrix for the three non-monitor canonical writers."""
import json, sqlite3
import pytest
from src.ops import wsol_10_sol_four_step_operation as wsol
from src.core import watchtower_registry_promotion as wt
from src.ops import operator_identity_governance as gov
from src.ops.operator_writer import OperatorWriter
from src.ops.canonical_membership_outbox import ensure_schema

CASES=("success","after_membership","append_failure","after_outbox")
def counts(c):
 return (c.execute("select count(*) from operator_launch_membership where mint='m'").fetchone()[0],c.execute("select count(*) from canonical_membership_outbox where mint='m'").fetchone()[0])
def assert_case(c,case): assert counts(c)==((1,1) if case=='success' else (0,0))
def inject(monkeypatch,case):
 if case in ('after_membership','append_failure'):
  monkeypatch.setattr('src.ops.canonical_membership_outbox.append_transition',lambda *a,**k:(_ for _ in ()).throw(RuntimeError('TEST_ONLY_APPEND_FAILURE')))
 if case=='after_outbox':
  from src.ops.canonical_membership_outbox import append_transition
  def boom(*a,**k): append_transition(*a,**k);raise RuntimeError('TEST_ONLY_AFTER_OUTBOX')
  monkeypatch.setattr('src.ops.canonical_membership_outbox.append_transition',boom)

def wdb():
 c=sqlite3.connect(':memory:');c.executescript('''
 create table operators(operator_id text,status text);create table operator_launch_membership(mint text primary key,operator_id text,source_population_id text,assigned_at int,event_id text);create table confirmed_operation_matches(match_id text primary key,operator_id text,mint text,detector_version text,state text,evidence_json text,detected_at int);create table wt_walkback_edge_candidates(mint text,candidate_parent text,signature text,anchor_signature text,block_time int,anchor_block_time int,hop_depth int,mechanism text,amount_lamports int,selection_status text,last_observed_at int);create table wt_walkback_atomic_flows(mint text,signature text,instruction_order_json text,has_create int,has_sync_native int,has_close int,evidence_key text);create table operation_activity_snapshots(snapshot_id text,operator_id text,observed_at int,timestamp_semantics text,metrics_json text,activity_state text);''')
 c.execute("insert into operators values(?, 'CONFIRMED')",(wsol.OPERATOR_ID,));c.execute("insert into wt_walkback_edge_candidates values('m','p','s','a',1,1,1,'WSOL_WRAP_CLOSE',?,'SELECTED',1)",(wsol.AMOUNT_LAMPORTS,));c.execute("insert into wt_walkback_atomic_flows values('m','s',?,1,1,1,'a')",(json.dumps(wsol.ATOMIC_SEQUENCE),));ensure_schema(c);c.commit();return c
@pytest.mark.parametrize('case',CASES)
def test_wsol_matrix(case,monkeypatch):
 c=wdb();monkeypatch.setattr('src.ops.manual_registry.refresh_operator_activity_snapshot',lambda *a,**k:None);inject(monkeypatch,case);c.execute('begin')
 try:
  assert wsol.project_completed_walkback(c,'m',now=1)=='admitted'
  c.commit()
 except RuntimeError:c.rollback()
 assert_case(c,case)

def tdb():
 c=sqlite3.connect(':memory:');c.row_factory=sqlite3.Row;c.executescript('''create table operators(operator_id text primary key,display_name text,status text);create table wt_walkback_queue(mint text primary key,intelligence_outcome text,completed_at int,creator text,treasury text,subprov text,funder_sig text,funding_mechanism text);create table wt_provisioning_sessions(source_mint text,treasury text,subprov text,creator text,treasury_to_subprov_mechanism text,subprov_to_creator_mechanism text);create table operator_launch_membership(mint text primary key,operator_id text,source_population_id text,assigned_at int,event_id text);''');c.execute("insert into operators values('w','WATCHTOWER','CONFIRMED')");c.execute("insert into wt_walkback_queue values('m','WATCHTOWER_CONFIRMED',1,'c','t','s','f','WSOL_WRAP_CLOSE')");c.execute("insert into wt_provisioning_sessions values('m','t','s','c','PLAIN_XFER','WSOL_WRAP_CLOSE')");ensure_schema(c);c.commit();return c
@pytest.mark.parametrize('case',CASES)
def test_watchtower_matrix(case,monkeypatch):
 c=tdb();inject(monkeypatch,case);c.execute('begin')
 try:
  assert wt.project_watchtower_confirmed_membership(c,'m',now=1,refresh_activity=False)['action']=='projected'
  c.commit()
 except RuntimeError:c.rollback()
 assert_case(c,case)

def gdb(tmp):
 p=tmp/'o.db';w=OperatorWriter(str(p));w.initialize_schema();w.transaction('seed',lambda c:c.execute("insert into operators(operator_id,status,confidence,first_seen,last_seen,summary,review_state,display_name,created_at,updated_at) values('p','CONFIRMED','CERTAIN',1,1,'p','REVIEWED','p',1,1)"));s=gov.OperatorIdentityGovernanceService(str(p));s.bootstrap_confirmed();c=sqlite3.connect(p);ensure_schema(c);c.close();return p,s
@pytest.mark.parametrize('case',CASES)
def test_governance_matrix(case,monkeypatch,tmp_path):
 p,s=gdb(tmp_path);inject(monkeypatch,case)
 try:
  s.split('p',[{'display_name':'a','launches':['m']},{'display_name':'b','launches':[]}],{'analyst':'a','evidence_revision':'r','reason':'t'})
 except RuntimeError:pass
 c=sqlite3.connect(p);assert_case(c,case)
