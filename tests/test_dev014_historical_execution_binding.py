import json
from pathlib import Path
import pytest
import src.ops.watchtower_historical_execution_binding as binding_module
from src.ops.watchtower_historical_execution_binding import BindingDenied, HistoricalExecutionBinding, CohortStorageGuard, MAX_COHORT_BYTES, MIN_FREE_BYTES

class Disk:
 def __init__(self, free): self.free=free

def test_cohort_storage_and_disk_boundaries_are_fail_closed(tmp_path):
 root=tmp_path/'cohort'; root.mkdir()
 guard=CohortStorageGuard(root,disk_usage=lambda _:Disk(MIN_FREE_BYTES+100),size_accounting=lambda:MAX_COHORT_BYTES-10)
 guard.check(reserve=10)
 with pytest.raises(BindingDenied,match='STORAGE_LIMIT'): guard.check(reserve=11)
 with pytest.raises(BindingDenied,match='DISK_HEADROOM'): CohortStorageGuard(root,disk_usage=lambda _:Disk(MIN_FREE_BYTES),size_accounting=lambda:0).check(reserve=1)
 with pytest.raises(BindingDenied,match='STORAGE_UNAVAILABLE'): CohortStorageGuard(root,disk_usage=lambda _:(_ for _ in ()).throw(OSError()),size_accounting=lambda:0).check()

def test_cohort_rejects_symlink_and_preserves_existing_evidence(tmp_path):
 root=tmp_path/'cohort'; root.mkdir(); evidence=root/'prior.log.1'; evidence.write_text('retained')
 (root/'escape').symlink_to(tmp_path)
 with pytest.raises(BindingDenied,match='SYMLINK'): CohortStorageGuard(root).check()
 assert evidence.read_text()=='retained'

ROOT=Path(__file__).resolve().parents[1]
def item():
 d=json.loads((ROOT/'docs/audits/dev014_watchtower_recent_first_price_forensics_reconciliation_and_batch_5_20261009.v1.json').read_text())
 x=d['batch_5']['records'][0]; return {'mint':x['mint'],'rank':x['rank'],'anchor':x['anchor'],'request':x['proposed_request'],'entry_mc_usd':142292.761106}
def test_crash_after_admission_never_retries(tmp_path):
 b=HistoricalExecutionBinding(tmp_path/'journal.json'); calls=[]
 with pytest.raises(RuntimeError): b.execute(item(),health_gate=lambda:None,live_pending=lambda:False,admit=lambda **k:calls.append(k),transport=lambda _:None,crash_at='AFTER_BUDGET')
 assert b.recover()['records'][0]['state']=='OUTCOME_UNKNOWN'
 assert len(calls)==1
 with pytest.raises(BindingDenied): b.execute(item(),health_gate=lambda:None,live_pending=lambda:False,admit=lambda **_:None,transport=lambda _:None)
def test_admitted_and_attempted_recover_unknown(tmp_path):
 for point in ('ADMITTED','ATTEMPTED'):
  b=HistoricalExecutionBinding(tmp_path/(point+'.json'))
  with pytest.raises(RuntimeError): b.execute(item(),health_gate=lambda:None,live_pending=lambda:False,admit=lambda **_:None,transport=lambda _:None,crash_at=point)
  assert b.recover()['records'][0]['state']=='OUTCOME_UNKNOWN'

def test_proven_provider_gate_denial_restores_only_pre_admission_intent(tmp_path):
 b=HistoricalExecutionBinding(tmp_path/'journal.json')
 with pytest.raises(BindingDenied,match='PROVIDER_GATE_CLOSED'):
  b.execute(item(),health_gate=lambda:None,admit=lambda **_:(_ for _ in ()).throw(BindingDenied('PROVIDER_GATE_CLOSED')),transport=lambda _:None)
 identity=b.read()['records'][0]['request_identity']; mint=b.read()['records'][0]['mint']
 with pytest.raises(BindingDenied,match='PRE_ADMISSION_INTENT_BUDGET_SIDE_EFFECT'):
  b.restore_proven_pre_admission_intent(request_identity=identity,mint=mint,budget_calls=[{'mint':mint}])
 assert b.restore_proven_pre_admission_intent(request_identity=identity,mint=mint,budget_calls=[])['records'][0]['state']=='PENDING'
def test_live_and_health_stop_before_admission(tmp_path):
 b=HistoricalExecutionBinding(tmp_path/'journal.json'); calls=[]
 assert b.execute(item(),health_gate=lambda:None,live_pending=lambda:True,admit=lambda **k:calls.append(k),transport=lambda _:None)['status']=='LIVE_PRIORITY_PENDING'
 with pytest.raises(RuntimeError): b.execute(item(),health_gate=lambda:(_ for _ in ()).throw(RuntimeError('health')),live_pending=lambda:False,admit=lambda **k:calls.append(k),transport=lambda _:None)
 assert calls==[]


@pytest.mark.parametrize("field,value", (("entry_mc_usd",None),("entry_mc_usd",0),("entry_mc_usd",float("nan")),("anchor",{"class":"QUALIFIED_ENTRY_ANCHOR","timestamp":1,"provenance":"wrong"})))
def test_entry_evidence_readiness_denies_before_admission(tmp_path,field,value):
 candidate=item(); candidate[field]=value; calls=[]
 binding=HistoricalExecutionBinding(tmp_path/'journal.json')
 with pytest.raises(BindingDenied): binding.execute(candidate,health_gate=lambda:None,admit=lambda **k:calls.append(k),transport=lambda _:None)
 assert calls==[] and not (tmp_path/'journal.json').exists()

def test_segment_rotation_preserves_zero_and_reconstructs_complete_identity_set(tmp_path,monkeypatch):
 monkeypatch.setattr(binding_module,'MAX_BYTES',800)
 path=tmp_path/'journal.json'
 zero={'version':1,'records':[{'request_identity':f'complete-{n}','mint':f'mint-{n}','state':'COMPLETED'} for n in range(20)] + [{'request_identity':'attempted','mint':'mint-attempted','state':'ATTEMPTED'}]}
 raw=json.dumps(zero,sort_keys=True,separators=(',',':')).encode(); path.write_bytes(raw)
 before=path.read_bytes(); binding=HistoricalExecutionBinding(path)
 journal=binding.read(); journal['records'].append({'request_identity':'next','mint':'mint-next','state':'PENDING'})
 binding._write(journal)
 assert path.read_bytes()==before
 assert (tmp_path/'journal.segment.001.json').is_file()
 recovered=HistoricalExecutionBinding(path).recover()
 assert {record['request_identity'] for record in recovered['records']}=={record['request_identity'] for record in zero['records']}|{'next'}
 assert next(record for record in recovered['records'] if record['request_identity']=='attempted')['state']=='ATTEMPTED'

def test_segment_creation_crash_artifacts_are_non_authoritative_and_same_root_is_conservative(tmp_path,monkeypatch):
 monkeypatch.setattr(binding_module,'MAX_BYTES',800)
 path=tmp_path/'journal.json'; path.write_text(json.dumps({'version':1,'records':[{'request_identity':f'id-{n}','mint':f'm-{n}','state':'COMPLETED'} for n in range(20)]}))
 (tmp_path/'journal.segment.001.json.tmp').write_text('partial')
 binding=HistoricalExecutionBinding(path); journal=binding.read(); journal['records'].append({'request_identity':'new','mint':'new-mint','state':'PENDING'}); binding._write(journal)
 assert len(HistoricalExecutionBinding(path).read()['records'])==21
 (tmp_path/'journal.segment.002.json').write_text(json.dumps({'version':1,'records':[{'request_identity':'new','mint':'other','state':'COMPLETED'}]}))
 with pytest.raises(BindingDenied,match='IDENTITY_CONFLICT'):
  HistoricalExecutionBinding(path).read()
