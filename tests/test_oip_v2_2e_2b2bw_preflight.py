import json
from pathlib import Path

ROOT=Path(__file__).parents[1]


def test_oldest_sentinel_preflight_is_fresh_empty_and_preserves_prior_runs():
    p=json.loads((ROOT/'docs/evidence_platform/oip_v2_2e_2b2bw_run_preflight.json').read_text())
    old1=json.loads((ROOT/'docs/evidence_platform/oip_v2_2e_2b2bq_b2z_run_preflight.json').read_text())
    old2=json.loads((ROOT/'docs/evidence_platform/oip_v2_2e_2b2bt_run_preflight.json').read_text())
    assert p['run_id'] not in {old1['run_id'],old2['run_id']}
    assert p['frozen_manifest_digest']==old1['frozen_manifest_digest']==old2['frozen_manifest_digest']
    assert p['execution_contract']['enhanced']=={'limit':1,'sort_order':'asc','commitment':'finalized'}
    assert p['execution_contract']['global_physical_request_ceiling']==60
    assert not Path(p['isolated_output_directory']).exists()
    assert Path(old1['isolated_output_directory']).exists() and Path(old2['isolated_output_directory']).exists()
    assert 'api-key' not in (ROOT/'docs/evidence_platform/oip_v2_2e_2b2bw_run_preflight.json').read_text()
