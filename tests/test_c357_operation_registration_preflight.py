import json,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def test_c357_registration_preflight_is_read_only_and_fails_closed_on_conflicts():
 d=json.loads((ROOT/'docs/audits/c357_operation_registration_preflight.v1.json').read_text())
 assert d['qualification_input']['digest']=='52a61e90b3f41188543b9ab241ad1bc8bd6e9c75e0de160ad1e63e29c45872b2'
 assert d['safe_population']['count']==56 and d['safe_population']['branches']=={'A':5,'B':2,'C':1,'DUTB_WALLET_POOL':48}
 assert d['membership_conflict_count']==50 and d['readiness_verdict']=='C357_REGISTRATION_PREFLIGHT_BLOCKED'
 assert d['exclusion_invariant']['count']==105 and d['exclusion_invariant']['verdict']=='PASS'
 assert d['proposed']['category']=='CONFIRMED' and d['proposed']['automation_eligibility']=='REVIEW_ONLY'
 assert d['proposed']['behaviour_detector']=='QUALIFIED_COMPATIBILITY_ONLY' and d['proposed']['attribution_detector']=='OFF_NOT_READY'
 assert d['proposed']['trading']=='OFF' and d['registration_mutation_authorized']=='NO'
 assert not any(d['safety'].values())
def test_c357_registration_preflight_replay_is_network_free():
 r=subprocess.run([sys.executable,'scripts/preflight_c357_operation_registration.py','--replay'],cwd=ROOT,text=True,capture_output=True,check=True)
 assert 'C357_REGISTRATION_PREFLIGHT_REPLAY_PASS' in r.stdout and 'provider_calls_during_replay=0' in r.stdout
