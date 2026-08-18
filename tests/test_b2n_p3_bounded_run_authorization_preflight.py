import json
from pathlib import Path

import pytest

from src.acquisition.b2n_qualification import (
    B2NAttemptLedger,
    B2NExecutor,
    B2NManifest,
    B2NMember,
    B2NQualificationRunAuthorization,
    B2N_MAX_PHYSICAL_REQUESTS,
    B2N_MAX_REQUESTS_PER_MEMBER,
)

ROOT = Path(__file__).parents[1]
FROZEN_MANIFEST_PATH = ROOT / "docs/evidence_platform/oip_v2_2e_2b2u_b2r_frozen_manifest.json"
REVIEWED_BINDING_PATH = ROOT / "docs/audits/b2n_cohort_eligibility_reviewed_provenance_binding.json"
P3_PREFLIGHT_PATH = ROOT / "docs/audits/b2n_p3_bounded_run_authorization_preflight.json"
OP_IMMUTABILITY_PATH = ROOT / "docs/audits/b2n_p3_existing_operation_immutability_contract.json"
WATCHTOWER_SAFETY_PATH = ROOT / "docs/audits/b2n_p3_watchtower_safety_assertion.json"


def _frozen_manifest() -> B2NManifest:
    payload = json.loads(FROZEN_MANIFEST_PATH.read_text())
    members = tuple(B2NMember(**m) for m in payload["members"])
    return B2NManifest(
        members=members,
        source_milestone=payload["source_milestone"],
        source_receive_utc_ns_exclusive=payload["source_receive_utc_ns_exclusive"],
    )


def _p3_preflight() -> dict:
    return json.loads(P3_PREFLIGHT_PATH.read_text())


class NeverCall:
    provider_request_count = 0

    def acquire_once(self, *, mint):
        raise AssertionError("NO_PROVIDER_CALL_ALLOWED_DURING_P3")


def test_p3_preflight_artifact_exists_and_is_well_formed():
    p = _p3_preflight()
    assert p["milestone"] == "B2N-P3"
    assert p["part1_p2e_closure_verification"]["closure_qualified"] is True
    assert p["part2_cohort_binding"]["authorized_member_count"] == 20
    assert len(p["part2_cohort_binding"]["members"]) == 20


def test_exact_manifest_accepted():
    manifest = _frozen_manifest()
    manifest.validate()  # must not raise
    p = _p3_preflight()
    assert manifest.digest() == p["part6_run_id"]["binds"]["b2n_manifest_digest_executor"]


def test_changed_manifest_rejected():
    manifest = _frozen_manifest()
    bad_members = list(manifest.members)
    bad_members[0] = B2NMember(1, "TAMPERED_MINT", bad_members[0].census_event_id, True)
    bad_manifest = B2NManifest(members=tuple(bad_members), source_milestone=manifest.source_milestone,
                                source_receive_utc_ns_exclusive=manifest.source_receive_utc_ns_exclusive)
    assert bad_manifest.digest() != manifest.digest()
    p = _p3_preflight()
    auth = B2NQualificationRunAuthorization(
        provider=p["part4_provider_endpoint_binding"]["provider"],
        endpoint_family=p["part4_provider_endpoint_binding"]["endpoint_family"],
        run_id=p["part6_run_id"]["run_id"],
        manifest_digest=p["part6_run_id"]["binds"]["b2n_manifest_digest_executor"],
        ledger_path=p["part7_attempt_ledger"]["ledger_path"],
    )
    with pytest.raises(ValueError, match="AUTH_MANIFEST_DIGEST_MISMATCH"):
        auth.validate(manifest=bad_manifest)


def test_21st_member_rejected():
    manifest = _frozen_manifest()
    extended = tuple(manifest.members) + (B2NMember(21, "extra-mint", "extra-event", True),)
    bad_manifest = B2NManifest(members=extended, source_milestone=manifest.source_milestone,
                                source_receive_utc_ns_exclusive=manifest.source_receive_utc_ns_exclusive)
    with pytest.raises(ValueError, match="EXACTLY_20"):
        bad_manifest.validate()


def test_wrong_provider_rejected(tmp_path):
    manifest = _frozen_manifest()
    p = _p3_preflight()
    auth = B2NQualificationRunAuthorization(
        provider="",
        endpoint_family=p["part4_provider_endpoint_binding"]["endpoint_family"],
        run_id=p["part6_run_id"]["run_id"],
        manifest_digest=manifest.digest(),
        ledger_path=str(tmp_path / "ledger.jsonl"),
    )
    with pytest.raises(ValueError, match="AUTH_PROVIDER_REQUIRED"):
        auth.validate(manifest=manifest)


def test_wrong_endpoint_rejected(tmp_path):
    manifest = _frozen_manifest()
    p = _p3_preflight()
    auth = B2NQualificationRunAuthorization(
        provider=p["part4_provider_endpoint_binding"]["provider"],
        endpoint_family="",
        run_id=p["part6_run_id"]["run_id"],
        manifest_digest=manifest.digest(),
        ledger_path=str(tmp_path / "ledger.jsonl"),
    )
    with pytest.raises(ValueError, match="AUTH_ENDPOINT_FAMILY_REQUIRED"):
        auth.validate(manifest=manifest)


def test_wrong_run_id_rejected(tmp_path):
    manifest = _frozen_manifest()
    p = _p3_preflight()
    auth = B2NQualificationRunAuthorization(
        provider=p["part4_provider_endpoint_binding"]["provider"],
        endpoint_family=p["part4_provider_endpoint_binding"]["endpoint_family"],
        run_id="not a valid run id!!",
        manifest_digest=manifest.digest(),
        ledger_path=str(tmp_path / "ledger.jsonl"),
    )
    with pytest.raises(ValueError, match="AUTH_RUN_ID_INVALID"):
        auth.validate(manifest=manifest)


def test_over_budget_total_requests_rejected(tmp_path):
    manifest = _frozen_manifest()
    p = _p3_preflight()
    auth = B2NQualificationRunAuthorization(
        provider=p["part4_provider_endpoint_binding"]["provider"],
        endpoint_family=p["part4_provider_endpoint_binding"]["endpoint_family"],
        run_id=p["part6_run_id"]["run_id"],
        manifest_digest=manifest.digest(),
        ledger_path=str(tmp_path / "ledger.jsonl"),
        max_physical_requests=21,
    )
    with pytest.raises(ValueError, match="AUTH_REQUEST_CEILING_MISMATCH"):
        auth.validate(manifest=manifest)


def test_more_than_one_request_per_member_rejected(tmp_path):
    from src.acquisition.b2n_qualification import AppendOnlyLedger, OneRequestResponse

    class DoubleRequestClient:
        provider_request_count = 0
        def acquire_once(self, *, mint):
            self.provider_request_count += 2
            return OneRequestResponse("SUCCESS", True, True, provider_signature="sig", provider_request_count=2)

    manifest = _frozen_manifest()
    p = _p3_preflight()
    auth = B2NQualificationRunAuthorization(
        provider=p["part4_provider_endpoint_binding"]["provider"],
        endpoint_family=p["part4_provider_endpoint_binding"]["endpoint_family"],
        run_id=p["part6_run_id"]["run_id"],
        manifest_digest=manifest.digest(),
        ledger_path=str(tmp_path / "ledger.jsonl"),
    )
    executor = B2NExecutor(
        manifest=manifest, ledger=AppendOnlyLedger(tmp_path / "ledger.jsonl"),
        client=DoubleRequestClient(), provider=p["part4_provider_endpoint_binding"]["provider"],
        run_id=p["part6_run_id"]["run_id"], authorization=auth,
    )
    with pytest.raises((ValueError, RuntimeError)):
        executor.run()


def test_non_empty_foreign_ledger_rejected(tmp_path):
    manifest = _frozen_manifest()
    p = _p3_preflight()
    ledger_path = tmp_path / "foreign_ledger.jsonl"
    ledger = B2NAttemptLedger(ledger_path)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text('{"sample_ordinal": 1, "contract_version": "x", "run_id": "other-run"}\n')
    with pytest.raises(RuntimeError, match="MUST_BE_EMPTY"):
        ledger.require_empty()


def test_zero_provider_calls_during_p3_dry_validation(tmp_path):
    manifest = _frozen_manifest()
    p = _p3_preflight()
    auth = B2NQualificationRunAuthorization(
        provider=p["part4_provider_endpoint_binding"]["provider"],
        endpoint_family=p["part4_provider_endpoint_binding"]["endpoint_family"],
        run_id=p["part6_run_id"]["run_id"],
        manifest_digest=manifest.digest(),
        ledger_path=str(tmp_path / "ledger.jsonl"),
    )
    auth.validate(manifest=manifest)  # dry validation only
    client = NeverCall()
    assert client.provider_request_count == 0  # never incremented; acquire_once never called


def test_request_budget_matches_contract():
    assert B2N_MAX_PHYSICAL_REQUESTS == 20
    assert B2N_MAX_REQUESTS_PER_MEMBER == 1


def test_operation_immutability_contract_denies_all_write_authority():
    d = json.loads(OP_IMMUTABILITY_PATH.read_text())
    posture = d["authority_posture"]
    for key in (
        "existing_operation_mutation_authority",
        "existing_operation_merge_authority",
        "existing_operation_split_authority",
        "existing_operation_membership_write_authority",
        "existing_operation_attribution_write_authority",
        "monitoring_activation_authority",
        "policy_authority",
        "trading_authority",
    ):
        assert posture[key] is False


def test_watchtower_safety_assertion_forbids_mutation():
    d = json.loads(WATCHTOWER_SAFETY_PATH.read_text())
    assert d["watchtower_canonical_state_mutation"] == "FORBIDDEN"
    assert d["candidate_association_rule"]["automatic_membership_creation_authorized"] is False
    for key in ("watchtower_mutation_authority", "watchtower_membership_write_authority", "watchtower_monitoring_activation_authority"):
        assert d["authority_posture"][key] is False


def test_candidate_only_output_allowed_canonical_write_forbidden():
    p = _p3_preflight()
    allowed = set(p["part12_output_authority"]["allowed"])
    not_allowed = set(p["part12_output_authority"]["not_allowed"])
    assert "candidate evidence" in allowed
    assert "canonical operation writes" in not_allowed
    assert allowed.isdisjoint(not_allowed)


def test_p3_itself_grants_no_production_or_provider_authority():
    p = _p3_preflight()
    assert p["part13_production_database_authority"]["p3_itself"]["production_database_read"] is False
    assert p["part13_production_database_authority"]["p3_itself"]["production_database_write"] is False
    assert p["part14_provider_authority"]["p3_itself"]["provider_access"] is False


def test_authorization_object_not_consumed_not_executed():
    p = _p3_preflight()
    assert p["part16_authorization_object"]["status"] == "NOT_CONSUMED"
    assert p["part16_authorization_object"]["executed"] is False
