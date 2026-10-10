import json
from pathlib import Path
import pytest
from src.ops.watchtower_historical_execution_binding import BindingDenied, HistoricalExecutionBinding

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
def test_live_and_health_stop_before_admission(tmp_path):
 b=HistoricalExecutionBinding(tmp_path/'journal.json'); calls=[]
 assert b.execute(item(),health_gate=lambda:None,live_pending=lambda:True,admit=lambda **k:calls.append(k),transport=lambda _:None)['status']=='LIVE_PRIORITY_PENDING'
 with pytest.raises(RuntimeError): b.execute(item(),health_gate=lambda:(_ for _ in ()).throw(RuntimeError('health')),live_pending=lambda:False,admit=lambda **k:calls.append(k),transport=lambda _:None)
 assert calls==[]
