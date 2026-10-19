"""Temporary fingerprint bypass: exact diff, primary equivalence, retained contracts."""
import ast
import hashlib
import importlib.util
from pathlib import Path
import sqlite3
import sys
import pytest
from src.core import walkback_worker as w
from src.ops import operation_fingerprint_drift as drift, attribution_outcome, watchtower_candidates, post_walkback_projection
ROOT=Path(__file__).resolve().parents[1]
BYPASS="    # DEV090 temporary bypass: fingerprint health/drift views may become stale.\n    return\n"

def _load(name, filename):
    spec=importlib.util.spec_from_file_location(name,ROOT/'tests'/filename)
    module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module);return module
parent=_load('_fingerprint_dev002_parent','test_dev090_dev002_contract.py')
configured=_load('_fingerprint_target_parent','test_dev090_configured_target.py')
for prefix,module,excluded in [('dev002',parent,{'test_target_reverse_and_all_dependency_hashes'}),('target',configured,{'test_configured_worker_exact_reversal'})]:
    for name,function in vars(module).copy().items():
        if name.startswith('test_') and callable(function) and name not in excluded:
            globals()['test_'+prefix+'_'+name[5:]]=function

def test_exact_bypass_and_unchanged_original_source_assertions(monkeypatch):
    path=ROOT/'src/core/walkback_worker.py';current=path.read_text();assert current.count(BYPASS)==1
    preimage=current.replace(BYPASS,'',1)
    assert hashlib.sha256(preimage.encode()).hexdigest()=='bca037dfd7194f304493cac051ec894916ef3eac2d897871570dafbdaa56023d'
    original=Path.read_text
    with monkeypatch.context() as scoped:
        scoped.setattr(Path,'read_text',lambda p,*a,**k:preimage if p==path else original(p,*a,**k))
        parent.test_target_reverse_and_all_dependency_hashes()
        configured.test_configured_worker_exact_reversal()
    assert hashlib.sha256((ROOT/'src/core/deep_walkback.py').read_bytes()).hexdigest()=='9913a0db3e056a301b88e60308b00657e513c1fd8c14c7f3f21b0079739a468f'
    assert hashlib.sha256((ROOT/'src/ops/operation_fingerprint_drift.py').read_bytes()).hexdigest()=='cf0345ccec1f8a4578d09fede013ad59634adc10f9efdf6f9bfede25adc6ba55'

def _enabled_observer():
    source=(ROOT/'src/core/walkback_worker.py').read_text().replace(BYPASS,'',1)
    node=next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name=='_observe_fingerprint_drift')
    ns={'sqlite3':sqlite3,'json':__import__('json')};exec(compile(ast.Module(body=[node],type_ignores=[]),'<qualified-observer-preimage>','exec'),ns);return ns[node.name]

@pytest.mark.parametrize('outcome,confirmed,treasury',[
    ('NO_ATTRIBUTION_FOUND',False,None),('LINEAGE_GAP',False,None),('WATCHTOWER_CONFIRMED',True,'treasury')])
def test_primary_commit_equivalence_and_no_monitor_queries(monkeypatch,outcome,confirmed,treasury):
    results=[]
    for bypass in (False,True):
        conn=sqlite3.connect(':memory:');conn.row_factory=sqlite3.Row
        conn.executescript("CREATE TABLE wt_walkback_queue(mint TEXT PRIMARY KEY,creator TEXT,status TEXT,intelligence_outcome TEXT,subprov TEXT,treasury TEXT,rpc_used INTEGER,completed_at INTEGER,updated_at INTEGER,enqueued_at INTEGER,funder_wallet TEXT,funder_sig TEXT,funder_amount_sol REAL);CREATE TABLE watchtower_token_attribution(mint TEXT PRIMARY KEY,creator TEXT,matched_subprov TEXT,matched_treasury TEXT,score INTEGER,tier TEXT,scored_at INTEGER);CREATE TABLE wt_walkback_edge_candidates(mint TEXT,selection_status TEXT);INSERT INTO wt_walkback_queue(mint,creator,status,rpc_used,enqueued_at) VALUES('mint','creator','running',0,10);INSERT INTO wt_walkback_edge_candidates VALUES('mint','SELECTED');")
        events=[];statements=[];conn.set_trace_callback(statements.append)
        def monitor(c,mint):
            assert not c.in_transaction
            c.execute("SELECT COUNT(DISTINCT mint) FROM wt_walkback_edge_candidates WHERE selection_status='SELECTED'").fetchone()
            return {}
        def promote(c,mint,materialized):
            assert not c.in_transaction;events.append(('admission',mint,materialized['outcome']))
            w._observe_fingerprint_drift(c,mint)
        with monkeypatch.context() as scoped:
            scoped.setattr(w.time,'time',lambda:100)
            scoped.setattr(drift,'observe_completed_walkback',monitor)
            scoped.setattr(w,'_observe_fingerprint_drift',w._observe_fingerprint_drift if bypass else _enabled_observer())
            scoped.setattr(w,'_is_known_subprov',lambda *a:False);scoped.setattr(w,'_is_known_infrastructure',lambda *a:False)
            scoped.setattr(w,'_ensure_subprov_lead',lambda *a:events.append(('review-lead',a[1])))
            scoped.setattr(attribution_outcome,'materialize_outcome',lambda c,m:{'outcome':outcome})
            scoped.setattr(watchtower_candidates,'sync_walkback_result',lambda c,m:events.append(('candidate-sync',m)))
            scoped.setattr(w,'_detect_topology_treasury_candidate_after_commit',lambda *a:events.append(('topology',a[1])))
            scoped.setattr(w,'_notify_living_after_walkback_commit',lambda *a:events.append(('living',a[1])))
            scoped.setattr(sys.modules['src.ops.post_walkback_projection'],'project_retained_operation_evidence',lambda *a:events.append(('retained-projection',a[1])))
            scoped.setattr(w,'_promote_if_canonical_watchtower',promote)
            w._mark_complete(conn,'mint',outcome,'subprov' if outcome!='NO_ATTRIBUTION_FOUND' else None,treasury,13,confirmed_subprov=confirmed)
        primary={'queue':[tuple(r) for r in conn.execute('SELECT * FROM wt_walkback_queue')], 'attribution':[tuple(r) for r in conn.execute('SELECT * FROM watchtower_token_attribution')], 'calls':events,'in_transaction':conn.in_transaction}
        counts=[q for q in statements if 'COUNT(DISTINCT mint)' in q]
        assert len(counts)==(0 if bypass else 1)
        results.append(primary);conn.close()
    assert results[0]==results[1]

def test_bypassed_callback_never_imports_or_calls_monitor(monkeypatch):
    calls=[];monkeypatch.setattr(drift,'observe_completed_walkback',lambda *a:calls.append(a))
    w._observe_fingerprint_drift(None,'mint');assert calls==[]
