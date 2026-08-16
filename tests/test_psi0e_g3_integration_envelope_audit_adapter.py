from dataclasses import replace
from hashlib import sha256
import json

import pytest

from src.evidence.contracts import production_shadow_integration_envelope_audit_adapter as adapter
from src.evidence.contracts.production_shadow_assessment import QUERY_IDS
from src.evidence.contracts.production_shadow_assessment_summary_consumer import (
    PRODUCTION_DERIVED_PROVENANCE_CLASS,
    build_assessment_summary_consumer_contract,
)
from src.evidence.contracts.production_shadow_integration_envelope import (
    _project_envelope,
    build_integration_envelope_contract,
)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode() + b"\n"


def projection_document(*, cohort=3, rows=2, present=1):
    consumer = build_assessment_summary_consumer_contract()
    return {
        "schema_version": "psi0d-b.descriptive-projection.v1",
        "contract_digest": consumer.contract_digest,
        "input_lineage": {
            "psi0d_a_digest": consumer.psi0d_a_digest,
            "psi0c_d_digest": consumer.psi0c_d_digest,
            "psi0c_c_assessment_identity": consumer.psi0c_c_assessment_identity,
            "psi0c_c_bundle_identity": consumer.psi0c_c_bundle_identity,
            "psi0c_b_digest": consumer.psi0c_b_digest,
        },
        "fixture_only": False,
        "default_off": True,
        "provenance_class": PRODUCTION_DERIVED_PROVENANCE_CLASS,
        "cohort_count": cohort,
        "surfaces": {
            query_id: {
                "row_count": rows,
                "unique_mint_count": present,
                "coverage_numerator": present,
                "coverage_denominator": cohort,
                "duplicate_row_count": rows - present,
                "unmatched_row_count": 0,
                "missingness_semantics": "ABSENT_NOT_NEGATIVE",
            }
            for query_id in QUERY_IDS
        },
        "unresolved_conflict_count": 0,
        "orphan_unmatched_count": 0,
        "reason_codes": ["PSI0C_B_ABSENCE_IS_NOT_NEGATIVE"],
        "interpretation": {
            "threshold_applied": False,
            "negative_outcome_inferred": False,
            "duplicates_collapsed": False,
            "conflicts_resolved": False,
            "entities_ranked_or_selected": False,
        },
        "authority": {
            "policy": False, "ranking": False, "integration": False,
            "deployment": False, "activation": False,
        },
    }


def audit_document(projection=None):
    projection = projection_document() if projection is None else projection
    descriptive = {query_id: projection["surfaces"][query_id] for query_id in QUERY_IDS}
    descriptive.update({
        "unresolved_conflict_count": projection["unresolved_conflict_count"],
        "orphan_unmatched_count": projection["orphan_unmatched_count"],
        "reason_codes": projection["reason_codes"],
    })
    return {
        "schema_version": "psi0d-f.in-memory-application.v1",
        "milestone": "PSI0D-F",
        "applied_at_utc": "2026-08-16T12:00:00Z",
        "engineering_commit": adapter.PSI0D_F_ENGINEERING_COMMIT,
        "status": "PASS",
        "verdict": "IN_MEMORY_PROJECTION_REPLAY_VERIFIED",
        "bound_inputs": dict(adapter.EXPECTED_BOUND_INPUTS),
        "execution": {
            "source_files_read_once": True,
            "adapter_applications": 1,
            "normalized_input_digest": "1" * 64,
            "projection_digest": sha256(canonical(projection)).hexdigest(),
            "projection_replay_method": "DIRECT_CANONICAL_BYTES",
            "cohort_denominator": projection["cohort_count"],
            "provenance_class": PRODUCTION_DERIVED_PROVENANCE_CLASS,
        },
        "descriptive_projection": descriptive,
        "authority": {
            "policy": False, "ranking": False, "integration": False,
            "deployment": False, "activation": False,
        },
        "scope_proof": {
            "source_bundle_altered": False,
            "source_values_recorded_or_disclosed": False,
            "consumer_output_published": False,
            "consumer_output_directory_created": False,
            "production_or_runtime_access": False,
            "database_or_network_calls": 0,
        },
        "next_action": "HUMAN_APPROVAL_REQUIRED",
    }


def fixture(monkeypatch, document=None):
    document = audit_document() if document is None else document
    payload = canonical(document)
    projection = projection_document(
        cohort=document["execution"]["cohort_denominator"],
        rows=document["descriptive_projection"][QUERY_IDS[0]]["row_count"],
        present=document["descriptive_projection"][QUERY_IDS[0]]["unique_mint_count"],
    )
    projection["surfaces"] = {
        query_id: dict(document["descriptive_projection"][query_id]) for query_id in QUERY_IDS
    }
    projection["unresolved_conflict_count"] = document["descriptive_projection"]["unresolved_conflict_count"]
    projection["orphan_unmatched_count"] = document["descriptive_projection"]["orphan_unmatched_count"]
    projection["reason_codes"] = document["descriptive_projection"]["reason_codes"]
    projection_digest = sha256(canonical(projection)).hexdigest()
    document["execution"]["projection_digest"] = projection_digest
    payload = canonical(document)
    envelope_contract = build_integration_envelope_contract()
    expected_input = "2" * 64
    expected = _project_envelope(envelope_contract, projection, input_digest=expected_input)
    monkeypatch.setattr(adapter, "EXPECTED_AUDIT_DIGEST", sha256(payload).hexdigest())
    monkeypatch.setattr(adapter, "PSI0D_PROJECTION_DIGEST", projection_digest)
    monkeypatch.setattr(adapter, "EXPECTED_INPUT_DIGEST", expected_input)
    monkeypatch.setattr(adapter, "EXPECTED_ENVELOPE_DIGEST", expected.envelope_digest)
    return adapter.build_integration_envelope_audit_adapter_contract(), envelope_contract, payload, expected


def execute(monkeypatch, document=None):
    contract, envelope_contract, payload, expected = fixture(monkeypatch, document)
    actual = adapter.adapt_psi0d_f_audit_to_integration_envelope(
        contract, envelope_contract, audit_bytes=payload,
    )
    assert actual == expected
    return actual


def test_contract_is_pure_bound_and_non_authoritative():
    contract = adapter.build_integration_envelope_audit_adapter_contract()
    assert adapter.verify_integration_envelope_audit_adapter_contract(contract)
    assert not any((
        contract.performs_io, contract.retries_allowed, contract.retains_source_values,
        contract.grants_policy_authority, contract.grants_ranking_authority,
        contract.grants_integration_authority, contract.grants_deployment_authority,
        contract.grants_activation_authority,
    ))


def test_valid_audit_shape_reconstructs_exact_default_off_envelope(monkeypatch):
    result = execute(monkeypatch)
    document = json.loads(result.canonical_envelope)
    assert document["default_off"] is True
    assert document["consumer_enabled"] is False
    assert not any(document["authority"].values())
    assert not any(document["interpretation"].values())


def test_input_order_independence_and_deterministic_replay(monkeypatch):
    document = audit_document()
    reversed_document = dict(reversed(list(document.items())))
    first = execute(monkeypatch, document)
    second = execute(monkeypatch, reversed_document)
    assert first.canonical_envelope == second.canonical_envelope
    assert first.envelope_digest == second.envelope_digest


@pytest.mark.parametrize("mutation", ("missing", "extra", "malformed", "wrong_type"))
def test_audit_schema_and_payload_faults_fail_closed(monkeypatch, mutation):
    document = audit_document()
    if mutation == "missing":
        document.pop("scope_proof")
        contract, envelope_contract, payload, _ = fixture(monkeypatch, document)
    elif mutation == "extra":
        document["source_values"] = []
        contract, envelope_contract, payload, _ = fixture(monkeypatch, document)
    elif mutation == "wrong_type":
        document["milestone"] = 1
        contract, envelope_contract, payload, _ = fixture(monkeypatch, document)
    else:
        payload = b"{"
        monkeypatch.setattr(adapter, "EXPECTED_AUDIT_DIGEST", sha256(payload).hexdigest())
        contract = adapter.build_integration_envelope_audit_adapter_contract()
        envelope_contract = build_integration_envelope_contract()
    with pytest.raises(adapter.IntegrationEnvelopeAuditAdapterError):
        adapter.adapt_psi0d_f_audit_to_integration_envelope(
            contract, envelope_contract, audit_bytes=payload,
        )


@pytest.mark.parametrize("mutation,reason", (
    ("audit_digest", "AUDIT_DIGEST"),
    ("engineering", "AUDIT_IDENTITY"),
    ("lineage", "LINEAGE"),
    ("projection", "EXECUTION_DRIFT"),
    ("provenance", "EXECUTION_DRIFT"),
    ("cohort", "INCONSISTENT_ACCOUNTING"),
    ("surface", "INCONSISTENT_ACCOUNTING"),
    ("reason", "REASON_CODE"),
    ("authority", "AUTHORITY_DRIFT"),
    ("scope", "SCOPE_PROOF"),
))
def test_identity_lineage_projection_accounting_authority_and_scope_drift_fail(
    monkeypatch, mutation, reason,
):
    document = audit_document()
    contract, envelope_contract, payload, _ = fixture(monkeypatch, document)
    if mutation == "audit_digest":
        contract = replace(contract, expected_audit_digest="0" * 64)
        reason = "CONTRACT_REPLAY"
    else:
        if mutation == "engineering":
            document["engineering_commit"] = "0" * 40
        elif mutation == "lineage":
            document["bound_inputs"]["psi0d_d_adapter_contract_digest"] = "0" * 64
        elif mutation == "projection":
            document["execution"]["projection_digest"] = "0" * 64
        elif mutation == "provenance":
            document["execution"]["provenance_class"] = "FIXTURE"
        elif mutation == "cohort":
            document["execution"]["cohort_denominator"] = 4
        elif mutation == "surface":
            document["descriptive_projection"][QUERY_IDS[0]]["duplicate_row_count"] = 0
        elif mutation == "reason":
            document["descriptive_projection"]["reason_codes"] = ["UNKNOWN"]
        elif mutation == "authority":
            document["authority"]["integration"] = True
        else:
            document["scope_proof"]["consumer_output_published"] = True
        payload = canonical(document)
        monkeypatch.setattr(adapter, "EXPECTED_AUDIT_DIGEST", sha256(payload).hexdigest())
        contract = adapter.build_integration_envelope_audit_adapter_contract()
    with pytest.raises(adapter.IntegrationEnvelopeAuditAdapterError, match=reason):
        adapter.adapt_psi0d_f_audit_to_integration_envelope(
            contract, envelope_contract, audit_bytes=payload,
        )


def test_adapter_and_envelope_contract_bypass_fail(monkeypatch):
    contract, envelope_contract, payload, _ = fixture(monkeypatch)
    with pytest.raises(adapter.IntegrationEnvelopeAuditAdapterError, match="CONTRACT_REPLAY"):
        adapter.adapt_psi0d_f_audit_to_integration_envelope(
            replace(contract, grants_integration_authority=True),
            envelope_contract,
            audit_bytes=payload,
        )
    with pytest.raises(adapter.IntegrationEnvelopeAuditAdapterError, match="ENVELOPE_CONTRACT"):
        adapter.adapt_psi0d_f_audit_to_integration_envelope(
            contract,
            replace(envelope_contract, contract_digest="0" * 64),
            audit_bytes=payload,
        )


def test_envelope_digest_mismatch_fails(monkeypatch):
    contract, envelope_contract, payload, _ = fixture(monkeypatch)
    monkeypatch.setattr(adapter, "EXPECTED_ENVELOPE_DIGEST", "0" * 64)
    altered = adapter.build_integration_envelope_audit_adapter_contract()
    with pytest.raises(adapter.IntegrationEnvelopeAuditAdapterError, match="ENVELOPE_REPLAY"):
        adapter.adapt_psi0d_f_audit_to_integration_envelope(
            altered, envelope_contract, audit_bytes=payload,
        )
