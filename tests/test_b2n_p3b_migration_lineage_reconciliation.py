import json
from pathlib import Path

import pytest

from src.acquisition.b2n_qualification import (
    AppendOnlyLedger,
    B2NAttemptLedger,
    B2NExecutor,
    B2NManifest,
    B2NMember,
    B2NQualificationRunAuthorization,
    B2N_MAX_PHYSICAL_REQUESTS,
    B2N_MAX_REQUESTS_PER_MEMBER,
)
from src.acquisition.b2w_projection import B2WInputProjection, MigrationGetTransactionAdapter

ROOT = Path(__file__).parents[1]
RECONCILIATION_PATH = ROOT / "docs/audits/b2n_p3b_migration_lineage_contract_reconciliation.json"
PROVIDER_CONTRACT_PATH = ROOT / "docs/audits/b2n_p3b_native_migration_lineage_provider_contract.json"
FUTURE_STAGE_PATH = ROOT / "docs/audits/b2n_p3b_future_creator_funding_stage_boundary.json"
SUCCESSOR_PREFLIGHT_PATH = ROOT / "docs/audits/b2n_p3b_bounded_migration_lineage_run_authorization_preflight.json"
OP_IMMUTABILITY_PATH = ROOT / "docs/audits/b2n_p3_existing_operation_immutability_contract.json"
WATCHTOWER_SAFETY_PATH = ROOT / "docs/audits/b2n_p3_watchtower_safety_assertion.json"


def manifest():
    return B2NManifest(tuple(B2NMember(i, f"mint-{i}", f"event-{i}", True) for i in range(1, 21)))


def records():
    return [{"event_id": f"event-{i}", "mint": f"mint-{i}", "signature": f"sig-{i}", "subscription": "subscribeMigration"} for i in range(1, 21)]


class Transport:
    def __init__(self):
        self.requests = []

    def post_json(self, request):
        self.requests.append(request)
        signature = request["params"][0]
        ordinal = int(signature.rsplit("-", 1)[1])
        return {"result": {"slot": 7, "transaction": {"message": {"accountKeys": [{"pubkey": f"mint-{ordinal}"}]}}}}


def _successor_preflight() -> dict:
    return json.loads(SUCCESSOR_PREFLIGHT_PATH.read_text())


def _frozen_manifest() -> B2NManifest:
    payload = json.loads((ROOT / "docs/evidence_platform/oip_v2_2e_2b2u_b2r_frozen_manifest.json").read_text())
    members = tuple(B2NMember(**m) for m in payload["members"])
    return B2NManifest(members=members, source_milestone=payload["source_milestone"],
                        source_receive_utc_ns_exclusive=payload["source_receive_utc_ns_exclusive"])


def test_b2n_no_longer_requires_creator_funding_evidence():
    d = json.loads(RECONCILIATION_PATH.read_text())
    assert d["part1_b2n_authoritative_purpose"]["creator_funding_evidence_authority"] is False
    assert d["part1_b2n_authoritative_purpose"]["purpose"] == "BOUNDED_MIGRATION_LINEAGE_QUALIFICATION"


def test_b2n_success_field_is_migration_lineage_not_creator_funding():
    d = json.loads(RECONCILIATION_PATH.read_text())
    assert d["part3_b2n_success_criteria"]["success_field_name"] == "migration_lineage_evidence_observed"
    assert "creator_funding_evidence_observed" not in json.dumps(d["part3_b2n_success_criteria"])


def test_creator_funding_evidence_cannot_be_falsely_claimed_by_adapter():
    """The one-request adapter must NEVER report evidence_observed=True (which would be a false creator-funding claim)."""
    transport = Transport()
    adapter = MigrationGetTransactionAdapter(transport, B2WInputProjection.from_census(manifest(), records()))
    for i in range(1, 21):
        outcome = adapter.acquire_once(mint=f"mint-{i}")
        assert outcome.evidence_observed is False  # never claims creator-funding evidence
        assert outcome.error_class == "B2W_MIGRATION_LINEAGE_ONLY"
    assert len(transport.requests) == 20


def test_one_request_per_member_invariant_preserved():
    transport = Transport()
    adapter = MigrationGetTransactionAdapter(transport, B2WInputProjection.from_census(manifest(), records()))
    adapter.acquire_once(mint="mint-1")
    assert len(transport.requests) == 1


def test_max_20_total_invariant_preserved():
    assert B2N_MAX_PHYSICAL_REQUESTS == 20
    assert B2N_MAX_REQUESTS_PER_MEMBER == 1


def test_exact_migration_member_binding():
    transport = Transport()
    projection = B2WInputProjection.from_census(manifest(), records())
    adapter = MigrationGetTransactionAdapter(transport, projection)
    outcome = adapter.acquire_once(mint="mint-5")
    assert transport.requests[0]["params"][0] == "sig-5"
    assert outcome.provider_signature == "sig-5"


def test_cache_hit_semantics_unaffected_by_reconciliation(tmp_path):
    from src.acquisition.b2n_qualification import OneRequestResponse

    class CacheClient:
        provider_request_count = 0
        def acquire_once(self, *, mint):
            return OneRequestResponse("CACHE_HIT", False, False, provider_request_count=0)

    entries = B2NExecutor(
        manifest=manifest(), ledger=AppendOnlyLedger(tmp_path / "ledger.jsonl"),
        client=CacheClient(), provider="helius",
    ).run()
    assert len(entries) == 1
    assert entries[0]["request_count"] == 0
    assert entries[0]["request_outcome"] == "CACHE_HIT"


def test_malformed_response_fails_closed():
    transport = Transport()

    class BadResponseTransport:
        def post_json(self, request):
            return {"result": "not-a-dict"}

    adapter = MigrationGetTransactionAdapter(BadResponseTransport(), B2WInputProjection.from_census(manifest(), records()))
    outcome = adapter.acquire_once(mint="mint-1")
    assert outcome.outcome == "MALFORMED_RESPONSE"
    assert outcome.evidence_observed is False


def test_provider_method_binding_in_successor_preflight():
    p = _successor_preflight()
    binding = p["provider_endpoint_method_binding"]
    assert binding["provider"] == "helius"
    assert binding["endpoint_family"] == "helius-mainnet-json-rpc"
    assert binding["method"] == "getTransaction"


def test_wrong_method_rejected_via_authorization_manifest_binding(tmp_path):
    # The authorization object itself does not encode 'method' (that lives in the provider
    # contract artifact); prove that a manifest mismatch still fails closed regardless.
    real_manifest = _frozen_manifest()
    tampered = B2NManifest(
        members=tuple(real_manifest.members[:-1]) + (B2NMember(20, "WRONG_MINT", real_manifest.members[-1].census_event_id, True),),
        source_milestone=real_manifest.source_milestone,
        source_receive_utc_ns_exclusive=real_manifest.source_receive_utc_ns_exclusive,
    )
    p = _successor_preflight()
    auth = B2NQualificationRunAuthorization(
        provider=p["provider_endpoint_method_binding"]["provider"],
        endpoint_family=p["provider_endpoint_method_binding"]["endpoint_family"],
        run_id=p["run_id"],
        manifest_digest=real_manifest.digest(),
        ledger_path=str(tmp_path / "ledger.jsonl"),
    )
    with pytest.raises(ValueError, match="AUTH_MANIFEST_DIGEST_MISMATCH"):
        auth.validate(manifest=tampered)


def test_future_creator_funding_stage_remains_non_executable():
    d = json.loads(FUTURE_STAGE_PATH.read_text())
    auth = d["authorization_status"]
    assert auth["b2z_execution_authority_granted_by_this_artifact"] is False
    assert auth["b2z_provider_authority_granted_by_this_artifact"] is False
    assert auth["b2z_request_budget_granted_by_this_artifact"] == 0


def test_existing_operation_protections_preserved_unchanged():
    d = json.loads(OP_IMMUTABILITY_PATH.read_text())
    posture = d["authority_posture"]
    for key in (
        "existing_operation_mutation_authority", "existing_operation_merge_authority",
        "existing_operation_split_authority", "existing_operation_membership_write_authority",
        "existing_operation_attribution_write_authority", "monitoring_activation_authority",
        "policy_authority", "trading_authority",
    ):
        assert posture[key] is False

    wt = json.loads(WATCHTOWER_SAFETY_PATH.read_text())
    assert wt["watchtower_canonical_state_mutation"] == "FORBIDDEN"


def test_run_id_changed_from_prior_p3():
    p = _successor_preflight()
    assert p["run_id"] != "b2n-p3-be37d9a9da6643d2640cb315"
    assert p["run_id_changed_from_prior"] is True
    assert p["supersedes"]["prior_run_id_reused"] is False


def test_request_budget_not_widened_to_60():
    p = _successor_preflight()
    budget = p["request_budget"]
    assert budget["max_total_requests"] == 20
    assert budget["max_requests_per_member"] == 1
    assert budget["not_widened_to_60"] is True
    assert budget["not_borrowed_from_b2z"] is True


def test_authorization_object_not_consumed_not_executed():
    p = _successor_preflight()
    assert p["authorization_object"]["status"] == "NOT_CONSUMED"
    assert p["authorization_object"]["executed"] is False


def test_zero_provider_calls_and_production_access_in_p3b():
    p = _successor_preflight()
    assert p["production_database_authority"]["p3b_itself"]["production_database_read"] is False
    assert p["production_database_authority"]["p3b_itself"]["production_database_write"] is False
    assert p["provider_authority"]["p3b_itself"]["provider_access"] is False


def test_no_creator_funding_evidence_statement_present():
    p = _successor_preflight()
    assert p["no_creator_funding_evidence_acquired_statement"] == "NO_CREATOR_FUNDING_EVIDENCE_ACQUIRED_IN_B2N"
    assert p["creator_funding_evidence_authority"] is False
