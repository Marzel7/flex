import importlib.util, json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def data(): return json.loads((ROOT/'docs/audits/c357_hxuf_live_infrastructure.v1.json').read_text())
def mod():
 s=importlib.util.spec_from_file_location('hxuf',ROOT/'scripts/audit_c357_hxuf_live.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
def test_repeated_cycles_and_delta_interpretation():
 x=data();assert x['cycle_summary']['count']==17;assert x['cycle_summary']['classification']=='REPEATED_STRUCTURED_PROVISIONING';assert x['cycle_summary']['minimum_lamports']==997_960_720
def test_live_role_and_safety():
 x=data();assert x['hxuf']['current_status']=='ACTIVE';assert x['hxuf']['infrastructure_role']=='ROLE_ROTATING_HUB';assert x['safety']['workflow']=='PAUSED';assert not x['safety']['detector_changed']
def test_replay_is_offline():
 x=data();d=x.pop('deterministic_digest');assert d==mod().dig(x)
