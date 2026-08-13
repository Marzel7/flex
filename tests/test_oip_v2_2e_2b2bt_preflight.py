import hashlib
import json
from pathlib import Path

from src.acquisition.b2bt_execution_boundary import AppendOnlyJsonl, B2BTRunner
from src.acquisition.b2n_qualification import B2NManifest, B2NMember
from src.acquisition.b2w_projection import B2WInputProjection, B2WRequestInput


ROOT=Path(__file__).parents[1]
PREFLIGHT=ROOT/'docs/evidence_platform/oip_v2_2e_2b2bt_run_preflight.json'


class NeverCall:
    physical_request_count=0
    def get_transaction(self,signature): raise AssertionError('NO_PROVIDER_CALL')
    def get_enhanced_transactions(self,address,*,limit): raise AssertionError('NO_PROVIDER_CALL')


def test_new_preflight_is_immutable_empty_and_distinct_from_sealed_run():
    p=json.loads(PREFLIGHT.read_text()); old=json.loads((ROOT/'docs/evidence_platform/oip_v2_2e_2b2bq_b2z_run_preflight.json').read_text())
    assert p['run_id'] != old['run_id']
    assert p['frozen_manifest_digest']==old['frozen_manifest_digest']
    assert p['projection_digest']==old['projection_digest']
    assert p['member_count']==20 and p['execution_contract']['global_physical_request_ceiling']==60
    assert p['execution_contract']['enhanced_limit']==100
    assert not Path(p['isolated_output_directory']).exists()
    assert not Path(p['attempt_ledger_path']).exists() and not Path(p['projection_ledger_path']).exists()
    assert all(len(e['redacted_fingerprint_sha256'])==64 for e in p['endpoints'])
    assert 'api-key' not in PREFLIGHT.read_text()


def test_preflight_constructs_runner_without_transport_call(tmp_path):
    p=json.loads(PREFLIGHT.read_text()); m=json.loads((ROOT/p['frozen_manifest_path']).read_text()); q=json.loads((ROOT/p['projection_path']).read_text())
    transport=NeverCall()
    runner=B2BTRunner(manifest=B2NManifest(tuple(B2NMember(**x) for x in m['members'])),
        projection=B2WInputProjection(tuple(B2WRequestInput(**x) for x in q['members'])),transport=transport,
        attempts=AppendOnlyJsonl(tmp_path/'a.jsonl'),projections=AppendOnlyJsonl(tmp_path/'p.jsonl'),run_id=p['run_id'])
    assert runner.run_id==p['run_id'] and transport.physical_request_count==0
