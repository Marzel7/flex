"""Offline qualification of the 6437 runner's real acquisition orchestration."""
import importlib.util
from pathlib import Path
import pytest
from src.ops.rpc_acquisition_checkpoint import RunLedger

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('reconstruct6437',ROOT/'scripts/reconstruct_6437_funder_infrastructure.py')
runner=importlib.util.module_from_spec(spec); spec.loader.exec_module(runner)

def page(*rows): return [{'signature':s,'blockTime':t} for s,t in rows]
def cache(): return {'schema_version':'6437_compact_decoded_cache.v2','transactions':{},'transaction_status':{},'histories':{},'calls':{}}
def acquirer(responses, ceiling=20, snapshots=None):
    ledger=RunLedger(ceiling); snapshots=[] if snapshots is None else snapshots
    def call(method, params):
        return ledger.call(method,params[0],lambda: responses.pop(0))
    def persist(c):
        import copy; snapshots.append(copy.deepcopy(c))
    return runner.FunderAcquirer(cache(),ledger,call,persist,now=lambda:123),ledger,snapshots

def test_page_checkpoint_is_atomic_and_resume_does_not_repeat_completed_page():
    a,l,s=acquirer([page(('new',300),('old',200))],ceiling=1)
    with pytest.raises(RuntimeError,match='RPC_BUDGET_EXHAUSTED'): a.acquire_history('f',[(100,400)])
    h=a.cache['histories']['f']; assert h['state']=='PARTIAL' and h['next_before_cursor']=='old' and h['page_count']==1
    assert s[-1]['histories']['f']['signatures_retained'][1]['signature']=='old' # same durable image
    responses=[page(('older',90))]
    a2,l2,_=acquirer(responses); a2.cache=a.cache
    h=a2.acquire_history('f',[(100,400)])
    assert h['state']=='COMPLETE' and l2.network_calls==1 and h['page_count']==2

def test_empty_page_completes_and_is_not_requested_again():
    a,l,_=acquirer([[]]); assert a.acquire_history('f',[(100,400)])['history_exhausted']
    assert a.acquire_history('f',[(100,400)])['state']=='COMPLETE' and l.network_calls==1

def test_legacy_cache_migration_uses_boundary_not_signature_presence():
    a,l,_=acquirer([]); a.cache['histories']['complete']=page(('old',99)); a.cache['histories']['partial']=page(('recent',200))
    assert a.acquire_history('complete',[(100,400)])['state']=='COMPLETE' and l.network_calls==0
    with pytest.raises(IndexError): a.acquire_history('partial',[(100,400)])
    assert a.cache['histories']['partial']['state']=='PARTIAL'

def test_non_progress_is_terminal_partial_checkpoint():
    a,_,_=acquirer([page(('a',300),('b',200)),page(('b',199))])
    with pytest.raises(RuntimeError,match='PAGINATION_NO_PROGRESS'): a.acquire_history('f',[(100,400)])
    h=a.cache['histories']['f']; assert h['state']=='PARTIAL' and h['terminal_state']=='PAGINATION_NO_PROGRESS'

def test_decode_uses_persisted_signatures_and_budget_is_shared():
    a,l,_=acquirer([page(('s1',100)),{'transaction':{'signatures':['s1'],'message':{'accountKeys':[]}},'meta':{},'blockTime':100}],ceiling=2)
    a.acquire_history('f',[(101,400)]) # history exhausted by required boundary
    a.decode('f',[{'launch_time':150}])
    assert l.network_calls==2 and a.cache['transaction_status']['s1']=='DECODED'
    a.decode('f',[{'launch_time':150}]); assert l.network_calls==2

def test_budget_exhaustion_during_decode_leaves_remaining_not_decoded():
    a,l,_=acquirer([page(('s1',100),('s2',99)),{'transaction':{'signatures':['s1'],'message':{'accountKeys':[]}},'meta':{},'blockTime':100}],ceiling=2)
    a.acquire_history('f',[(101,400)])
    with pytest.raises(RuntimeError,match='RPC_BUDGET_EXHAUSTED'): a.decode('f',[{'launch_time':150}])
    assert a.cache['transaction_status']['s1']=='DECODED'
    assert a.cache['transaction_status'].get('s2','NOT_DECODED')=='NOT_DECODED'

def test_unique_atomic_cache_temp_and_revision_conflict(tmp_path):
    p=tmp_path/'cache.json'; a=runner.load_cache(p); runner.save_cache(a,p)
    first=runner.load_cache(p); second=runner.load_cache(p); first['histories']['a']={'state':'COMPLETE'}; runner.save_cache(first,p)
    second['histories']['b']={'state':'COMPLETE'}
    with pytest.raises(RuntimeError,match='CHECKPOINT_REVISION_CONFLICT'): runner.save_cache(second,p)
    import json
    assert json.loads(p.read_text())['histories']['a']['state']=='COMPLETE'
    assert not list(tmp_path.glob('cache.json.checkpoint.*.tmp'))
