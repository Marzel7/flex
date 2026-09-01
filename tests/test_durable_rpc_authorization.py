import pytest
from src.ops.rpc_acquisition_checkpoint import DurableAuthorizationLedger,require_resolved_cache_provenance

def new(tmp,budget=750,run='r1',purpose='p',candidate='c'):
 return DurableAuthorizationLedger.new(tmp/'ledger.json',run,purpose,candidate,budget)

def test_recovery_restart_is_exactly_717(tmp_path):
 x=new(tmp_path); x.recover([{'rpc_method':'getSignaturesForAddress','target':'wallet'}]*2+[{'rpc_method':'getTransaction','target':'UNKNOWN'}]*31)
 y=DurableAuthorizationLedger.resume(tmp_path/'ledger.json','r1','p','c')
 assert (y.data['calls_attempted'],y.remaining)==(33,717)

def test_hard_limit_across_processes(tmp_path):
 x=new(tmp_path,3); calls=[]
 for i in range(2): x.call('fake','m',str(i),lambda i=i: calls.append(i))
 y=DurableAuthorizationLedger.resume(tmp_path/'ledger.json','r1','p','c'); y.call('fake','m','2',lambda:calls.append(2))
 with pytest.raises(RuntimeError,match='RPC_AUTHORIZATION_EXHAUSTED'): y.call('fake','m','3',lambda:calls.append(3))
 assert calls==[0,1,2] and y.data['calls_attempted']==3

def test_crashed_reservation_remains_spent(tmp_path):
 x=new(tmp_path,2); x.reserve('fake','m','one')
 y=DurableAuthorizationLedger.resume(tmp_path/'ledger.json','r1','p','c')
 assert y.remaining==1 and y.data['attempts'][0]['state']=='RESERVED'

def test_new_authorization_is_explicit_and_independent(tmp_path):
 old=new(tmp_path,2); old.reserve('fake','m','x')
 with pytest.raises(RuntimeError,match='EXISTING_AUTHORIZATION_REQUIRES_RESUME'): new(tmp_path,2,run='r2')
 fresh=DurableAuthorizationLedger.new(tmp_path/'other.json','r2','p','c',2)
 assert fresh.remaining==2 and fresh.data['run_id']!='r1'

def test_no_implicit_renewal_and_purpose_mismatch(tmp_path):
 x=new(tmp_path,10); x.recover([{'rpc_method':'m'}]*3)
 assert DurableAuthorizationLedger.resume(tmp_path/'ledger.json','r1','p','c').remaining==7
 with pytest.raises(RuntimeError,match='AUTHORIZATION_PURPOSE_MISMATCH'): DurableAuthorizationLedger.resume(tmp_path/'ledger.json','r1','wrong','c')

def test_ambiguous_legacy_cache_counter_fails_closed(tmp_path):
 x=new(tmp_path); x.recover([{'rpc_method':'m'}]*33)
 with pytest.raises(RuntimeError,match='RPC_AUTHORIZATION_PROVENANCE_UNRESOLVED'):
  require_resolved_cache_provenance({'calls':{'total':750}},x)
