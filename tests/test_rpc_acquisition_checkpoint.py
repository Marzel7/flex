import pytest
from src.ops.rpc_acquisition_checkpoint import RunLedger,remaining_funders,next_transactions

def test_budget_has_no_off_by_one():
 l=RunLedger(2); l.call('p','1',lambda:1); l.call('p','2',lambda:2)
 with pytest.raises(RuntimeError,match='RPC_BUDGET_EXHAUSTED'): l.call('p','3',lambda:3)
 assert l.network_calls==2 and l.successes==2
def test_failure_and_retry_count_each_attempt():
 l=RunLedger(3)
 with pytest.raises(ValueError): l.call('p','x',lambda:(_ for _ in ()).throw(ValueError('x')))
 l.call('p','x',lambda:1); assert (l.network_calls,l.successes,l.failures)==(2,1,1)
def test_canonical_remaining_and_decoded_cache():
 assert remaining_funders({'a','b','c'},{'a':{'history_complete':True},'b':{'history_complete':False}})==['b','c']
 assert next_transactions([str(i) for i in range(100)],{str(i):'DECODED' for i in range(80)})==[str(i) for i in range(80,100)]
 assert next_transactions(['decoded','terminal','retry','new'],{'decoded':'DECODED','terminal':'FAILED_TERMINAL','retry':'FAILED_RETRYABLE'})==['retry','new']
