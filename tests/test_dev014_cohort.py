import json
from pathlib import Path
import pytest
from src.ops.watchtower_historical_cohort import manifests,run_finite,cohort_authorization,cli_executor
from src.ops.watchtower_historical_backfill_controller import HistoricalBackfillController
ROOT=Path(__file__).resolve().parents[1]
def test_cohort_manifests_are_bounded_and_explicit(tmp_path):
 r=json.loads((ROOT/'docs/audits/dev014_watchtower_recent_first_price_forensics_reconciliation_and_batch_5_20261009.v1.json').read_text());p=json.loads((ROOT/'docs/audits/dev014_watchtower_forensic_population_v2_20261009.v1.json').read_text())
 c=HistoricalBackfillController(tmp_path/'state',r,p)
 with pytest.raises(ValueError): manifests(c,tmp_path/'journal','')
 out=manifests(c,tmp_path/'journal','fixture')
 assert all(len(x['records'])<=50 and x['content_hash'] for x in out)

def test_synthetic_multi_session_cadence_cooldown_and_exhaustion():
 clock=[0]; calls=[]
 def tick(n): clock[0]+=n
 records=[{'request_identity':str(i)} for i in range(100)]
 manifests=[{'records':records[:50]},{'records':records[50:]}]
 result=run_finite(manifests,execute=lambda r:calls.append(r['request_identity']),health=lambda:None,clock=lambda:clock[0],sleep=tick)
 assert result['status']=='EXHAUSTED' and calls==[str(i) for i in range(100)]
 assert len(result['sessions'])==2 and clock[0]>=540

def test_synthetic_cohort_health_storage_and_cancel_stop():
 records=[{'request_identity':'x'}]; manifest=[{'records':records}]
 import pytest
 with pytest.raises(RuntimeError): run_finite(manifest,execute=lambda _:None,health=lambda:(_ for _ in ()).throw(RuntimeError('health')))
 assert run_finite(manifest,execute=lambda _:None,health=lambda:None,storage_ok=lambda:False)['status']=='STORAGE_HOLD'
 assert run_finite(manifest,execute=lambda _:None,health=lambda:None,cancelled=lambda:True)['status']=='CANCELLED'

def test_content_hashed_authorization_and_cli_adapter(tmp_path):
 auth=cohort_authorization(chronology_hash='frozen',identities=['a','b'],authority_id='operator')
 assert auth['content_hash'] and auth['max_requests_per_session']==50
 class R: returncode=0; stdout='{"status":"COMPLETED"}'; stderr=''
 assert cli_executor(tmp_path/'cli',['--mode','execute'],runner=lambda *a,**k:R())(tmp_path/'manifest')['status']=='COMPLETED'
