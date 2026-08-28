import json,subprocess,sys
from pathlib import Path
R=Path(__file__).resolve().parents[1]
def test_identity_reconciliation_preserves_subtype_boundary():
 d=json.loads((R/'docs/audits/p3r_c357_operation_identity_reconciliation.v1.json').read_text());p=d['populations']
 assert (len(p['p3r_and_c357_supported']),len(p['c357_supported_not_p3r']),len(p['p3r_not_c357_supported']),p['c357_compatible_unresolved'])==(50,6,59,105)
 assert all(x['p3r_admission_basis']=='BEHAVIOURAL_FAMILY_MEMBERSHIP_MANUAL_ADMISSION' for x in d['overlap_admission'].values())
 assert d['identity_verdict']=='P3R_PARENT_C357_SUBTYPE' and d['registry_membership_model']=='PARENT_CHILD_OPERATION_MODEL_NEEDED'
 assert d['six_only_registration']=='MISLEADING' and d['membership_blocker_next_action'].startswith('no reassignment')
 assert d['hypotheses']['C357_SUBTYPE_OF_P3R']=='STRONG' and d['hypotheses']['P3R_MEMBERSHIP_OVERAGGREGATED']=='WEAK_EVIDENCE'
 assert not any(d['safety'].values())
def test_identity_replay_is_offline():
 r=subprocess.run([sys.executable,'scripts/reconcile_p3r_c357_identity.py','--replay'],cwd=R,text=True,capture_output=True,check=True)
 assert 'REPLAY_PASS' in r.stdout and 'provider_calls_during_replay=0' in r.stdout
