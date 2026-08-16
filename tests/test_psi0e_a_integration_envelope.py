from dataclasses import replace
from hashlib import sha256
import json

import pytest

from src.evidence.contracts import production_shadow_integration_envelope as envelope
from src.evidence.contracts import production_shadow_projection_publisher as publisher
from src.evidence.contracts.production_shadow_assessment import QUERY_IDS
from src.evidence.contracts.production_shadow_assessment_summary_consumer import (
    PRODUCTION_DERIVED_PROVENANCE_CLASS,
    build_assessment_summary_consumer_contract,
)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode() + b"\n"


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def projection_document(*, cohort=3, present=1, rows=1):
    consumer = build_assessment_summary_consumer_contract()
    surfaces = {
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
    }
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
        "surfaces": surfaces,
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
            "policy": False,
            "ranking": False,
            "integration": False,
            "deployment": False,
            "activation": False,
        },
    }


def injected_bundle(monkeypatch, document=None):
    document = projection_document() if document is None else document
    projection_bytes = canonical(document)
    projection_digest = sha256(projection_bytes).hexdigest()
    monkeypatch.setattr(publisher, "EXPECTED_PROJECTION_DIGEST", projection_digest)
    publication_contract = publisher.build_projection_publication_contract()
    contract_bytes = publisher._canonical(publisher._manifest(publication_contract))
    file_digests = {
        "contract.json": sha256(contract_bytes).hexdigest(),
        "projection.json": projection_digest,
    }
    hashes_bytes = canonical({"file_digests": file_digests, "bundle_digest": digest(file_digests)})
    files = {
        "contract.json": contract_bytes,
        "hashes.json": hashes_bytes,
        "projection.json": projection_bytes,
    }
    monkeypatch.setattr(envelope, "PSI0D_PROJECTION_DIGEST", projection_digest)
    monkeypatch.setattr(envelope, "PSI0D_BUNDLE_DIGEST", digest(file_digests))
    monkeypatch.setattr(envelope, "PSI0D_HASHES_FILE_DIGEST", sha256(hashes_bytes).hexdigest())
    monkeypatch.setattr(envelope, "PSI0D_H_CONTRACT_DIGEST", publication_contract.contract_digest)
    return envelope.build_integration_envelope_contract(), files


def execute(monkeypatch, document=None):
    contract, files = injected_bundle(monkeypatch, document)
    result = envelope.project_published_projection_fixture(contract, bundle_files=files)
    assert envelope.verify_integration_envelope(contract, bundle_files=files, envelope=result)
    return result, json.loads(result.canonical_envelope), files


def test_valid_synthetic_bundle_emits_default_off_non_authoritative_envelope(monkeypatch):
    result, document, _ = execute(monkeypatch)
    assert document["default_off"] is True
    assert document["consumer_enabled"] is False
    assert not any(document["authority"].values())
    assert not any(document["interpretation"].values())
    assert document["provenance_class"] == envelope.OUTPUT_PROVENANCE
    assert result.contract_digest == document["contract_digest"]


@pytest.mark.parametrize("present,rows", ((0, 0), (1, 1), (3, 3)))
def test_empty_partial_and_full_coverage_are_descriptive_only(monkeypatch, present, rows):
    _, document, _ = execute(monkeypatch, projection_document(present=present, rows=rows))
    assert all(item["coverage_numerator"] == present for item in document["surfaces"].values())
    assert all(item["coverage_denominator"] == 3 for item in document["surfaces"].values())
    assert all(item["missingness_semantics"] == "ABSENT_NOT_NEGATIVE" for item in document["surfaces"].values())


def test_duplicates_conflicts_and_unmatched_are_preserved(monkeypatch):
    projection = projection_document()
    projection["surfaces"][QUERY_IDS[0]]["row_count"] = 3
    projection["surfaces"][QUERY_IDS[0]]["duplicate_row_count"] = 2
    projection["surfaces"][QUERY_IDS[1]]["row_count"] = 2
    projection["surfaces"][QUERY_IDS[1]]["unique_mint_count"] = 2
    projection["surfaces"][QUERY_IDS[1]]["unmatched_row_count"] = 1
    projection["unresolved_conflict_count"] = 1
    projection["orphan_unmatched_count"] = 1
    projection["reason_codes"] += [
        "PSI0C_B_CONFLICT_PRESERVED_UNRESOLVED",
        "PSI0C_B_UNMATCHED_KEY_RECORDED",
    ]
    _, document, _ = execute(monkeypatch, projection)
    assert document["surfaces"][QUERY_IDS[0]]["duplicate_row_count"] == 2
    assert document["surfaces"][QUERY_IDS[1]]["unmatched_row_count"] == 1
    assert document["unresolved_conflict_count"] == 1
    assert document["orphan_unmatched_count"] == 1


@pytest.mark.parametrize("mutation", ("missing", "extra", "altered", "noncanonical"))
def test_bundle_file_and_canonical_faults_fail_closed(monkeypatch, mutation):
    contract, files = injected_bundle(monkeypatch)
    files = dict(files)
    if mutation == "missing":
        files.pop("contract.json")
    elif mutation == "extra":
        files["extra.json"] = b"{}\n"
    elif mutation == "altered":
        files["hashes.json"] = canonical({})
    else:
        files["projection.json"] = files["projection.json"].rstrip(b"\n")
    with pytest.raises(envelope.IntegrationEnvelopeError):
        envelope.project_published_projection_fixture(contract, bundle_files=files)


@pytest.mark.parametrize("mutation,reason", (
    ("lineage", "PROJECTION_VALIDATION"),
    ("provenance", "PROJECTION_VALIDATION"),
    ("authority", "PROJECTION_VALIDATION"),
    ("accounting", "PROJECTION_VALIDATION"),
    ("reason", "PROJECTION_VALIDATION"),
))
def test_lineage_provenance_authority_accounting_and_reason_drift_fail(monkeypatch, mutation, reason):
    projection = projection_document()
    if mutation == "lineage":
        projection["input_lineage"]["psi0c_d_digest"] = "0" * 64
    elif mutation == "provenance":
        projection["provenance_class"] = "FIXTURE"
    elif mutation == "authority":
        projection["authority"]["integration"] = True
    elif mutation == "accounting":
        projection["surfaces"][QUERY_IDS[0]]["duplicate_row_count"] = 1
    else:
        projection["reason_codes"] = []
    contract, files = injected_bundle(monkeypatch, projection)
    with pytest.raises(envelope.IntegrationEnvelopeError, match=reason):
        envelope.project_published_projection_fixture(contract, bundle_files=files)


def test_prohibited_value_field_is_rejected_and_never_emitted(monkeypatch):
    projection = projection_document()
    projection["mint"] = "SECRET_MINT"
    contract, files = injected_bundle(monkeypatch, projection)
    with pytest.raises(envelope.IntegrationEnvelopeError, match="PROJECTION_VALIDATION"):
        envelope.project_published_projection_fixture(contract, bundle_files=files)


def test_contract_bypass_and_envelope_replay_tamper_fail(monkeypatch):
    contract, files = injected_bundle(monkeypatch)
    with pytest.raises(envelope.IntegrationEnvelopeError, match="CONTRACT_REPLAY"):
        envelope.verify_integration_envelope_contract(replace(contract, grants_integration_authority=True))
    result = envelope.project_published_projection_fixture(contract, bundle_files=files)
    with pytest.raises(envelope.IntegrationEnvelopeError, match="ENVELOPE_REPLAY"):
        envelope.verify_integration_envelope(
            contract, bundle_files=files,
            envelope=replace(result, envelope_digest="0" * 64),
        )


def test_input_order_independence(monkeypatch):
    first = projection_document()
    second = dict(reversed(list(first.items())))
    result_a, _, _ = execute(monkeypatch, first)
    result_b, _, _ = execute(monkeypatch, second)
    assert result_a.canonical_envelope == result_b.canonical_envelope
    assert result_a.envelope_digest == result_b.envelope_digest


def test_pure_boundary_performs_zero_io(monkeypatch):
    contract, files = injected_bundle(monkeypatch)
    monkeypatch.setattr("builtins.open", lambda *a, **k: (_ for _ in ()).throw(AssertionError("file I/O")))
    monkeypatch.setattr("sqlite3.connect", lambda *a, **k: (_ for _ in ()).throw(AssertionError("database I/O")))
    monkeypatch.setattr("socket.create_connection", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network I/O")))
    result = envelope.project_published_projection_fixture(contract, bundle_files=files)
    assert result.canonical_envelope
