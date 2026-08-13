import hashlib
import json
from pathlib import Path

from src.acquisition.b2n_qualification import B2NManifest, B2NMember
from src.acquisition.b2w_projection import B2WInputProjection, B2WRequestInput
from src.acquisition.b2z_execution_boundary import B2ZRunner, PhysicalAttemptLedger


ROOT = Path(__file__).parents[1]
MANIFEST_PATH = ROOT / "docs/evidence_platform/oip_v2_2e_2b2u_b2r_frozen_manifest.json"
PROJECTION_PATH = ROOT / "docs/evidence_platform/oip_v2_2e_2b2bq_b2z_frozen_projection.json"
PREFLIGHT_PATH = ROOT / "docs/evidence_platform/oip_v2_2e_2b2bq_b2z_run_preflight.json"


def canonical_digest(value):
    payload = (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()
    return hashlib.sha256(payload).hexdigest()


class NeverCallTransport:
    physical_request_count = 0

    def __init__(self):
        self.calls = 0

    def post_json(self, request):
        self.calls += 1
        raise AssertionError("B2BQ_MUST_NOT_INVOKE_TRANSPORT")


def test_preflight_binds_exact_frozen_cohort_and_signatures():
    manifest = json.loads(MANIFEST_PATH.read_text())
    projection = json.loads(PROJECTION_PATH.read_text())
    assert manifest["manifest_digest"] == projection["source_manifest_digest"]
    assert len(manifest["members"]) == len(projection["members"]) == 20
    assert [row["sample_ordinal"] for row in projection["members"]] == list(range(1, 21))
    assert len({row["migration_signature"] for row in projection["members"]}) == 20
    for frozen, projected in zip(manifest["members"], projection["members"], strict=True):
        assert projected["sample_ordinal"] == frozen["sample_ordinal"]
        assert projected["mint"] == frozen["mint"]
        assert projected["census_event_id"] == frozen["census_event_id"]
        assert projected["migration_signature"]
    assert canonical_digest(projection["members"]) == projection["projection_digest"]


def test_preflight_is_credential_free_isolated_and_empty():
    preflight = json.loads(PREFLIGHT_PATH.read_text())
    serialized = PREFLIGHT_PATH.read_text()
    assert preflight["member_count"] == 20
    assert preflight["frozen_manifest_digest"] == "82bbda32d25a9951a8d8475528d7db3a92b675aae90ce2d55e13391a6b69eedc"
    assert preflight["endpoint"]["identifier"] == "helius-mainnet-json-rpc"
    assert preflight["endpoint"]["redacted"].endswith("<redacted>")
    assert preflight["endpoint"]["redacted_fingerprint_sha256"] == hashlib.sha256(
        preflight["endpoint"]["redacted"].encode()
    ).hexdigest()
    assert "api-key=<redacted>" in serialized
    assert preflight["isolated_output_directory"].startswith("/private/tmp/flex-oip-v2-2e-b2z/")
    assert not Path(preflight["isolated_output_directory"]).exists()
    assert not Path(preflight["attempt_ledger_path"]).exists()
    assert preflight["initial_state"]["attempt_ledger"] == "ABSENT_EMPTY"
    assert preflight["qualification"]["provider_requests"] == 0


def test_b2bp_construction_is_inert_and_uses_bound_run_id():
    manifest_json = json.loads(MANIFEST_PATH.read_text())
    projection_json = json.loads(PROJECTION_PATH.read_text())
    preflight = json.loads(PREFLIGHT_PATH.read_text())
    manifest = B2NManifest(tuple(B2NMember(**row) for row in manifest_json["members"]))
    projection = B2WInputProjection(tuple(B2WRequestInput(**row) for row in projection_json["members"]))
    transport = NeverCallTransport()
    runner = B2ZRunner(
        manifest=manifest,
        projection=projection,
        transport=transport,
        ledger=PhysicalAttemptLedger(Path(preflight["attempt_ledger_path"])),
        run_id=preflight["run_id"],
    )
    assert runner.run_id == preflight["run_id"]
    assert runner.digest == preflight["frozen_manifest_digest"]
    assert runner.physical_count == transport.physical_request_count == transport.calls == 0
    assert not Path(preflight["attempt_ledger_path"]).exists()


def test_request_contract_is_exact_and_stop_safe():
    contract = json.loads(PREFLIGHT_PATH.read_text())["execution_contract"]
    assert contract["global_physical_request_ceiling"] == 60
    assert contract["per_member_request_ceiling"] == 3
    assert len(contract["per_member_sequence"]) == 3
    assert contract["ordered_sequential"] is True
    assert contract["stop_on_first_non_success"] is True
    assert all(contract[name] is False for name in ("retry", "failover", "pagination", "concurrency", "cache"))
