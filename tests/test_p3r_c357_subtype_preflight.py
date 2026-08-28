import json,subprocess,sys
from pathlib import Path
R=Path(__file__).resolve().parents[1]
def test_subtype_preflight_preserves_primary_membership():
 d=json.loads((R/'docs/audits/p3r_c357_subtype_representation_preflight.v1.json').read_text())
 assert d['existing_subtype_model']=='PARTIALLY_REUSABLE'
 assert d['primary_membership_invariant']['p3r_primary_membership']==109
 assert d['projection']['supported_count']==56 and len(d['projection']['six'])==6
 assert sum(x['primary_p3r_member'] for x in d['projection']['members'])==50
 assert d['exclusion']['verdict']=='REQUIRES_GUARD' and d['recommendation']=='OPTION_A_DEDICATED_NON_OWNING_SUBTYPE_PROJECTION'
 assert d['detectors']['c357_attribution']=='OFF_NOT_READY' and not any(d['safety'].values())
def test_subtype_preflight_replays_offline():
 r=subprocess.run([sys.executable,'scripts/preflight_p3r_c357_subtype.py','--replay'],cwd=R,text=True,capture_output=True,check=True)
 assert 'REPLAY_PASS' in r.stdout and 'provider_calls_during_replay=0' in r.stdout
