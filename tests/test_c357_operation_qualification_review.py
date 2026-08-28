import json
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
PATH=ROOT/'docs/audits/c357_operation_qualification_review.v1.json'

def review(): return json.loads(PATH.read_text())

def test_qualification_contract_and_safety():
 d=review(); p=d['population']; safety=d['safety']
 assert (p['independently_supported'],p['compatible_unresolved'],p['exact_compatible_total'])==(56,105,161)
 assert len({x['mint'] for x in p['supported_launches']})==56
 assert [x['compatible_launches'] for x in d['branch_evidence']]==[48,5,2,1]
 assert all(x['exact_compatible_behaviour'] and x['independent_evidence'] for x in p['supported_launches'])
 assert d['dutb_recurrence']['provisioned_wallets']==d['dutb_recurrence']['wallets_later_used_for_compatible_launches']==35
 assert d['dutb_recurrence']['compatible_launches_reached']==48 and d['dutb_recurrence']['creator_count']==48
 assert d['post_dutb_continuity']=='OPERATION_CONTINUITY_BEYOND_ORIGINAL_PROVISIONER'
 assert d['infrastructure_rotation_supported']=='YES' and d['alternative_explanation']=='ALTERNATIVE_WEAK'
 assert d['common_human_controller']=='NOT_PROVEN'
 assert d['operation_existence_verdict']=='C357_OPERATION_EXISTENCE_STRONGLY_SUPPORTED'
 assert d['attribution_verdict']=='C357_ATTRIBUTION_PARTIAL'
 assert d['behaviour_detector']=='QUALIFIED_C357_COMPATIBLE'
 assert d['detector_readiness_verdict']=='PRODUCTION_ATTRIBUTION_DETECTOR_NOT_READY'
 assert d['safe_initial_operation_population']==56 and d['promotion_review_verdict']=='QUALIFIED_FOR_OPERATION_REGISTRATION_REVIEW'
 assert 'outside confirmed membership' in d['unresolved_disposition'] and 'no automatic membership' in d['future_branch_policy']
 assert not any(safety[x] for x in ('membership_mutation','detector_activation','fingerprint_change','snapshot_v2_change','queue_change','production_change','trading_change'))
 assert safety['workflow']=='PAUSED' and d['provider_calls']==0

def test_offline_replay_is_exact_and_network_free():
 r=subprocess.run([sys.executable,'scripts/review_c357_operation_qualification.py','--replay'],cwd=ROOT,text=True,capture_output=True,check=True)
 assert 'C357_OPERATION_QUALIFICATION_REPLAY_PASS' in r.stdout
 assert 'provider_calls_during_replay=0' in r.stdout
