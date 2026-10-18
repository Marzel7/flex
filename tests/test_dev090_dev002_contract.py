"""DEV-002 regression selection. Foreign suites remain intact for their own worker."""
import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace
import pytest
from src.core import walkback_worker as w, deep_walkback
from src.ops import p3r_profile_candidate_matcher as p3r, d3de_operation as d3de
from src.ops import provisional_operations as provisional, wsol_10_sol_four_step_operation as byzantine
from src.core import watchtower_registry_promotion as registry
from src.ops import watchtower_canonical_integration as integration, watchtower_canonical_adapters as adapters

ROOT=Path(__file__).resolve().parents[1]
CONTRACT=ROOT/'docs/audits/dev090/dev002-test-selection/contract.json'


def _load(name,filename):
    spec=importlib.util.spec_from_file_location(name,ROOT/'tests'/filename)
    mod=importlib.util.module_from_spec(spec);sys.modules[name]=mod;spec.loader.exec_module(mod)
    return mod

old79=_load('_dev002_foreign79','test_dev079_walkback_v1.py')
old82=_load('_dev002_foreign82','test_dev082_current_walkback.py')
EXCLUDED79={'test_budget_constants_and_nonpatch_source_unchanged'}
EXCLUDED82={'test_exact_reverse_patch_and_dependency_hashes','test_midwalk_admission_runs_after_edge_commit_and_failure_isolated','test_newer_promotion_order_and_enforcement_preserved'}
# Delegate unchanged applicable functions, including all their original parameter marks.
for _prefix,_module,_excluded in [('dev079',old79,EXCLUDED79),('dev082',old82,EXCLUDED82)]:
    for _name,_fn in vars(_module).copy().items():
        if _name.startswith('test_') and callable(_fn) and _name not in _excluded:
            globals()['test_'+_prefix+'_'+_name[5:]]=_fn


def test_target_selection_complete_and_foreign_sources_preserved():
    c=json.loads(CONTRACT.read_text())
    assert len(c['nine_foreign_cases'])==9
    assert c['authority']['worker_retirement']=='afbe9e58b11eb2e4d616246da123ce31ab45700c'
    for path,digest in c['foreign_test_hashes'].items():
        assert hashlib.sha256((ROOT/path).read_bytes()).hexdigest()==digest
    for prefix,module,excluded in [('dev079',old79,EXCLUDED79),('dev082',old82,EXCLUDED82)]:
        selected={n for n,f in vars(module).items() if n.startswith('test_') and callable(f)}-excluded
        assert selected
        assert all(globals()['test_'+prefix+'_'+n[5:]] is getattr(module,n) for n in selected)


def test_target_reverse_and_all_dependency_hashes():
    c=json.loads(CONTRACT.read_text())
    source=(ROOT/'src/core/walkback_worker.py').read_text()
    assert hashlib.sha256(source.encode()).hexdigest()==c['worker_overlay_sha256']=='bca037dfd7194f304493cac051ec894916ef3eac2d897871570dafbdaa56023d'
    hook='                from src.ops.walkback_create_handoff import maintenance_tick\n                maintenance_tick(LIVE_DB_PATH, OPS_DB_PATH)\n\n'
    assert source.count(hook)==1
    source=source.replace(hook,'',1)
    n=next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name=='_get_tx')
    lines=source.splitlines(True)
    lines[n.lineno-1:n.end_lineno]=(ROOT/'docs/audits/dev082/get-tx-preimage.py.txt').read_text().splitlines(True)
    assert hashlib.sha256(''.join(lines).encode()).hexdigest()==c['worker_preimage_sha256']=='a45ccc0db0a34269b8811975fef1b9297a27acad745074a3674a11f8f771e5b9'
    for manifest in ['docs/audits/dev079/installed-source-composition.json','docs/audits/dev082/source-composition.json']:
        for item in json.loads((ROOT/manifest).read_text())['files']:
            if item['path']!='src/core/walkback_worker.py':
                assert hashlib.sha256((ROOT/item['path']).read_bytes()).hexdigest()==item['candidate_sha256']==item['installed_sha256']
    assert w.RPC_BUDGET_BATCH==80 and w.TX_FETCH_LIMIT==5
    assert w.RPC_TIMEOUT==8 and w.MAX_ATTEMPTS==3 and w.BATCH_SIZE==8 and w.INTERVAL_SEC==45

@pytest.mark.parametrize('retired_raises',[False,True])
def test_target_midwalk_commit_without_retired_admission(monkeypatch,retired_raises):
    conn=sqlite3.connect(':memory:');deep_walkback.ensure_schema(conn)
    retired=[]
    def trap(*a,**k):
        retired.append('retired')
        if retired_raises:raise RuntimeError('retired matcher must never be invoked')
        return 'admitted'
    monkeypatch.setattr(p3r,'admit_unambiguous_p3r_match',trap)
    monkeypatch.setattr(d3de,'project_completed_walkback',trap)
    monkeypatch.setattr(w,'_is_known_spam_sender',lambda *a:False)
    monkeypatch.setattr(w,'_is_known_treasury',lambda *a:False)
    monkeypatch.setattr(w,'_is_known_subprov',lambda *a:False)
    calls=[]
    def transport(method,params):
        calls.append(method)
        if method=='getSignaturesForAddress':
            assert params[1]['before']=='CREATE_BOUNDARY'
            return [{'signature':old82.S,'slot':10,'err':None}]
        if method=='getTransaction':return old82.funding_tx()
        if method=='getAccountInfo':return {'value':{'owner':'11111111111111111111111111111111'}}
        raise AssertionError(method)
    counter=[0]
    result=w._find_funder_via_rpc('CREATOR',counter,conn,before_signature='CREATE_BOUNDARY',source_mint='MINT',rpc_transport=transport)
    assert result[:2]==('PARENT',old82.S)
    assert counter==[3] and calls==['getSignaturesForAddress','getTransaction','getAccountInfo']
    assert retired==[]
    assert conn.execute('select count(*) from wt_walkback_edge_candidates where mint=?',('MINT',)).fetchone()[0]==1
    assert not conn.in_transaction
    conn.close()


def _promotion(monkeypatch,mode,decision,failing=None):
    conn=sqlite3.connect(':memory:')
    conn.executescript('create table receipt(event text);create table terminal(value text);create table operator_launch_membership(mint text);')
    conn.execute("insert into terminal values('COMMITTED')");conn.commit()
    events=[];retired=[]
    def trap(*a,**k):retired.append(True);raise AssertionError('retired projector invoked')
    monkeypatch.setattr(p3r,'admit_unambiguous_p3r_match',trap)
    monkeypatch.setattr(d3de,'project_completed_walkback',trap)
    def project(name):
        def call(ops,mint,**kwargs):
            events.append(name)
            if failing==name:raise RuntimeError('active projector fixture failure')
            ops.execute('insert into receipt values (?)',(name,))
            return 'not_matched'
        return call
    monkeypatch.setattr(provisional,'project_900b_completed_walkback',project('900b'))
    monkeypatch.setattr(byzantine,'project_completed_walkback',project('byzantine'))
    monkeypatch.setattr(registry,'is_canonical_watchtower_outcome',lambda *a:False)
    monkeypatch.setattr(integration,'get_canonical_predicate_mode',lambda:mode)
    monkeypatch.setattr(adapters,'build_evidence_for_walkback_queue',lambda *a,**k:{})
    monkeypatch.setattr(integration,'evaluate_canonical_decision',lambda **k:SimpleNamespace(mode=mode,authoritative_decision=decision,authoritative_reason='fixture'))
    monkeypatch.setattr(integration,'record_comparison_telemetry',lambda ops,*a,**k:ops.execute("insert into receipt values('telemetry')"))
    def promote(*a,**k):events.append('watchtower');return {'action':'not_eligible'}
    def membership(*a,**k):events.append('membership');return {'action':'not_eligible'}
    monkeypatch.setattr(registry,'promote_walkback_confirmed_watchtower',promote)
    monkeypatch.setattr(registry,'project_watchtower_confirmed_membership',membership)
    monkeypatch.setattr(w,'_observe_fingerprint_drift',lambda ops,mint:events.append(('drift',ops.in_transaction)))
    w._promote_if_canonical_watchtower(conn,'MINT',{'outcome_type':'NOT_CANONICAL','operator_id':None})
    assert retired==[]
    assert events[:2]==['900b','byzantine']
    assert events[-1]==('drift',False)
    assert conn.execute('select value from terminal').fetchone()==('COMMITTED',)
    assert conn.execute('select count(*) from operator_launch_membership').fetchone()[0]==0
    return conn,events

@pytest.mark.parametrize('mode,decision,promotes',[('legacy','REJECTED',True),('shadow','REJECTED',True),('enforce','REJECTED',False),('enforce','REVIEW_REQUIRED',False),('enforce','ACCEPTED',True)])
def test_target_promotion_order_and_enforcement_preserved(monkeypatch,mode,decision,promotes):
    conn,events=_promotion(monkeypatch,mode,decision)
    assert ('watchtower' in events) is promotes
    assert ('membership' in events) is promotes
    assert conn.execute('select count(*) from receipt').fetchone()[0]==3
    assert not conn.in_transaction
    conn.close()

@pytest.mark.parametrize('failing',['900b','byzantine'])
def test_target_active_projector_failure_isolation(monkeypatch,failing):
    conn,events=_promotion(monkeypatch,'enforce','REJECTED',failing)
    assert 'watchtower' not in events and 'membership' not in events
    assert conn.execute("select count(*) from receipt where event='telemetry'").fetchone()[0]==1
    assert not conn.in_transaction
    conn.close()
