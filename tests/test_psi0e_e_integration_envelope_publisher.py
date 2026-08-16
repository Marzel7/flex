from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path

import pytest

from src.evidence.contracts import production_shadow_integration_envelope_publisher as publisher
from src.evidence.contracts.production_shadow_assessment import QUERY_IDS
from src.evidence.contracts.production_shadow_integration_envelope import (
    OUTPUT_PROVENANCE,
    SOURCE_PROVENANCE,
    build_integration_envelope_contract,
)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode() + b"\n"


def envelope_document():
    upstream = build_integration_envelope_contract()
    surfaces = {
        query_id: {
            "coverage_numerator": 1,
            "coverage_denominator": 3,
            "row_count": 2,
            "unique_mint_count": 1,
            "duplicate_row_count": 1,
            "unmatched_row_count": 0,
            "missingness_semantics": "ABSENT_NOT_NEGATIVE",
        }
        for query_id in QUERY_IDS
    }
    return {
        "schema_version": "psi0e-a.descriptive-integration-envelope.v1",
        "contract_digest": upstream.contract_digest,
        "source_identities": {
            "psi0d_bundle_digest": upstream.psi0d_bundle_digest,
            "psi0d_projection_digest": upstream.psi0d_projection_digest,
            "psi0d_hashes_file_digest": upstream.psi0d_hashes_file_digest,
            "psi0d_h_contract_digest": upstream.psi0d_h_contract_digest,
            "psi0d_b_consumer_digest": upstream.psi0d_b_consumer_digest,
        },
        "default_off": True,
        "consumer_enabled": False,
        "provenance_class": OUTPUT_PROVENANCE,
        "source_provenance_class": SOURCE_PROVENANCE,
        "cohort_count": 3,
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


def fixture_contract(monkeypatch, document=None):
    payload = canonical(envelope_document() if document is None else document)
    monkeypatch.setattr(publisher, "EXPECTED_ENVELOPE_DIGEST", sha256(payload).hexdigest())
    return publisher.build_integration_envelope_publication_contract(), payload


def assert_no_staging(parent: Path):
    assert not [path for path in parent.iterdir() if path.name.startswith(".bundle.tmp-")]


def test_contract_is_replayable_non_authoritative_and_binds_closure():
    contract = publisher.build_integration_envelope_publication_contract()
    assert publisher.verify_integration_envelope_publication_contract(contract)
    assert contract.psi0e_d_digest == publisher.PSI0E_D_DIGEST
    assert contract.expected_envelope_digest == publisher.EXPECTED_ENVELOPE_DIGEST
    assert not any((
        contract.retries_allowed, contract.overwrite_allowed, contract.retains_source_values,
        contract.grants_policy_authority, contract.grants_ranking_authority,
        contract.grants_integration_authority, contract.grants_deployment_authority,
        contract.grants_activation_authority,
    ))


def test_success_is_canonical_atomic_and_exactly_replayable(monkeypatch, tmp_path):
    contract, payload = fixture_contract(monkeypatch)
    output = tmp_path / "bundle"
    result = publisher.publish_integration_envelope_fixture(
        contract, envelope_bytes=payload, output_directory=output,
    )
    replay = publisher.verify_published_integration_envelope_bundle(contract, output)
    assert result == replay
    assert tuple(sorted(path.name for path in output.iterdir())) == publisher.FILES
    assert (output / "envelope.json").read_bytes() == payload
    assert_no_staging(tmp_path)


def test_deterministic_bundle_and_input_order_independence(monkeypatch, tmp_path):
    first = envelope_document()
    second = dict(reversed(list(first.items())))
    contract, first_payload = fixture_contract(monkeypatch, first)
    first_result = publisher.publish_integration_envelope_fixture(
        contract, envelope_bytes=first_payload, output_directory=tmp_path / "one",
    )
    second_payload = canonical(second)
    second_result = publisher.publish_integration_envelope_fixture(
        contract, envelope_bytes=second_payload, output_directory=tmp_path / "two",
    )
    assert first_payload == second_payload
    assert first_result.bundle_digest == second_result.bundle_digest


@pytest.mark.parametrize("mutation", ("missing", "extra", "noncanonical", "malformed"))
def test_schema_and_canonical_faults_fail_before_publication(monkeypatch, tmp_path, mutation):
    document = envelope_document()
    if mutation == "missing":
        document.pop("surfaces")
        payload = canonical(document)
    elif mutation == "extra":
        document["ranking_score"] = 1
        payload = canonical(document)
    elif mutation == "noncanonical":
        payload = json.dumps(document).encode()
    else:
        payload = b"{"
    monkeypatch.setattr(publisher, "EXPECTED_ENVELOPE_DIGEST", sha256(payload).hexdigest())
    contract = publisher.build_integration_envelope_publication_contract()
    with pytest.raises(publisher.IntegrationEnvelopePublicationError):
        publisher.publish_integration_envelope_fixture(
            contract, envelope_bytes=payload, output_directory=tmp_path / "bundle",
        )
    assert not (tmp_path / "bundle").exists()


@pytest.mark.parametrize("mutation,reason", (
    ("digest", "ENVELOPE_DIGEST"),
    ("lineage", "LINEAGE_OR_PROVENANCE"),
    ("provenance", "LINEAGE_OR_PROVENANCE"),
    ("default_off", "LINEAGE_OR_PROVENANCE"),
    ("authority", "AUTHORITY_OR_INTERPRETATION"),
    ("interpretation", "AUTHORITY_OR_INTERPRETATION"),
    ("accounting", "INCONSISTENT_ACCOUNTING"),
    ("missingness", "INCONSISTENT_ACCOUNTING"),
))
def test_digest_lineage_state_authority_and_accounting_drift_fail(
    monkeypatch, tmp_path, mutation, reason,
):
    document = envelope_document()
    payload = canonical(document)
    if mutation == "digest":
        monkeypatch.setattr(publisher, "EXPECTED_ENVELOPE_DIGEST", "0" * 64)
    else:
        if mutation == "lineage":
            document["source_identities"]["psi0d_bundle_digest"] = "0" * 64
        elif mutation == "provenance":
            document["provenance_class"] = "FIXTURE"
        elif mutation == "default_off":
            document["default_off"] = False
        elif mutation == "authority":
            document["authority"]["integration"] = True
        elif mutation == "interpretation":
            document["interpretation"]["threshold_applied"] = True
        elif mutation == "accounting":
            document["surfaces"][QUERY_IDS[0]]["duplicate_row_count"] = 0
        else:
            document["surfaces"][QUERY_IDS[0]]["missingness_semantics"] = "ABSENT"
        payload = canonical(document)
        monkeypatch.setattr(publisher, "EXPECTED_ENVELOPE_DIGEST", sha256(payload).hexdigest())
    contract = publisher.build_integration_envelope_publication_contract()
    with pytest.raises(publisher.IntegrationEnvelopePublicationError, match=reason):
        publisher.publish_integration_envelope_fixture(
            contract, envelope_bytes=payload, output_directory=tmp_path / "bundle",
        )
    assert not (tmp_path / "bundle").exists()


def test_contract_authority_bypass_fails(monkeypatch, tmp_path):
    contract, payload = fixture_contract(monkeypatch)
    altered = replace(contract, grants_integration_authority=True)
    with pytest.raises(publisher.IntegrationEnvelopePublicationError, match="CONTRACT_REPLAY"):
        publisher.publish_integration_envelope_fixture(
            altered, envelope_bytes=payload, output_directory=tmp_path / "bundle",
        )


def test_preexisting_output_fails_without_mutation(monkeypatch, tmp_path):
    contract, payload = fixture_contract(monkeypatch)
    output = tmp_path / "bundle"
    output.mkdir()
    marker = output / "owned.txt"
    marker.write_text("preserve")
    with pytest.raises(publisher.IntegrationEnvelopePublicationError, match="OUTPUT_REUSE"):
        publisher.publish_integration_envelope_fixture(
            contract, envelope_bytes=payload, output_directory=output,
        )
    assert marker.read_text() == "preserve"


def test_file_write_failure_cleans_staging_and_publishes_nothing(monkeypatch, tmp_path):
    contract, payload = fixture_contract(monkeypatch)
    original = publisher._write_fsynced
    calls = 0

    def fail_second(path, value):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("write fault")
        original(path, value)

    monkeypatch.setattr(publisher, "_write_fsynced", fail_second)
    with pytest.raises(publisher.IntegrationEnvelopePublicationError, match="IO_FAILURE"):
        publisher.publish_integration_envelope_fixture(
            contract, envelope_bytes=payload, output_directory=tmp_path / "bundle",
        )
    assert not (tmp_path / "bundle").exists()
    assert_no_staging(tmp_path)


def test_directory_fsync_failure_cleans_staging(monkeypatch, tmp_path):
    contract, payload = fixture_contract(monkeypatch)
    monkeypatch.setattr(
        publisher, "_fsync_directory",
        lambda path: (_ for _ in ()).throw(OSError("fsync fault")),
    )
    with pytest.raises(publisher.IntegrationEnvelopePublicationError, match="IO_FAILURE"):
        publisher.publish_integration_envelope_fixture(
            contract, envelope_bytes=payload, output_directory=tmp_path / "bundle",
        )
    assert not (tmp_path / "bundle").exists()
    assert_no_staging(tmp_path)


def test_post_rename_parent_fsync_failure_removes_output(monkeypatch, tmp_path):
    contract, payload = fixture_contract(monkeypatch)
    original = publisher._fsync_directory
    calls = 0

    def fail_second(path):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("parent fsync fault")
        original(path)

    monkeypatch.setattr(publisher, "_fsync_directory", fail_second)
    with pytest.raises(publisher.IntegrationEnvelopePublicationError, match="IO_FAILURE"):
        publisher.publish_integration_envelope_fixture(
            contract, envelope_bytes=payload, output_directory=tmp_path / "bundle",
        )
    assert not (tmp_path / "bundle").exists()
    assert_no_staging(tmp_path)


def test_rename_failure_cleans_staging(monkeypatch, tmp_path):
    contract, payload = fixture_contract(monkeypatch)
    monkeypatch.setattr(
        publisher.os, "replace",
        lambda source, target: (_ for _ in ()).throw(OSError("rename fault")),
    )
    with pytest.raises(publisher.IntegrationEnvelopePublicationError, match="IO_FAILURE"):
        publisher.publish_integration_envelope_fixture(
            contract, envelope_bytes=payload, output_directory=tmp_path / "bundle",
        )
    assert not (tmp_path / "bundle").exists()
    assert_no_staging(tmp_path)


def test_post_rename_replay_failure_removes_output(monkeypatch, tmp_path):
    contract, payload = fixture_contract(monkeypatch)
    monkeypatch.setattr(
        publisher,
        "verify_published_integration_envelope_bundle",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            publisher.IntegrationEnvelopePublicationError("tamper")
        ),
    )
    with pytest.raises(publisher.IntegrationEnvelopePublicationError, match="tamper"):
        publisher.publish_integration_envelope_fixture(
            contract, envelope_bytes=payload, output_directory=tmp_path / "bundle",
        )
    assert not (tmp_path / "bundle").exists()
    assert_no_staging(tmp_path)


def test_published_tamper_fails_replay(monkeypatch, tmp_path):
    contract, payload = fixture_contract(monkeypatch)
    output = tmp_path / "bundle"
    publisher.publish_integration_envelope_fixture(
        contract, envelope_bytes=payload, output_directory=output,
    )
    hashes = json.loads((output / "hashes.json").read_bytes())
    hashes["bundle_digest"] = "0" * 64
    (output / "hashes.json").write_bytes(canonical(hashes))
    with pytest.raises(publisher.IntegrationEnvelopePublicationError, match="HASH_REPLAY"):
        publisher.verify_published_integration_envelope_bundle(contract, output)
