from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path

import pytest

from src.evidence.contracts import production_shadow_projection_publisher as publisher
from src.evidence.contracts.production_shadow_assessment import QUERY_IDS
from src.evidence.contracts.production_shadow_assessment_summary_consumer import (
    PRODUCTION_DERIVED_PROVENANCE_CLASS,
    build_assessment_summary_consumer_contract,
)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode() + b"\n"


def projection_document():
    consumer = build_assessment_summary_consumer_contract()
    surfaces = {}
    for query_id in QUERY_IDS:
        surfaces[query_id] = {
            "row_count": 2,
            "unique_mint_count": 1,
            "coverage_numerator": 1,
            "coverage_denominator": 3,
            "duplicate_row_count": 1,
            "unmatched_row_count": 0,
            "missingness_semantics": "ABSENT_NOT_NEGATIVE",
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
    payload = canonical(projection_document() if document is None else document)
    monkeypatch.setattr(publisher, "EXPECTED_PROJECTION_DIGEST", sha256(payload).hexdigest())
    return publisher.build_projection_publication_contract(), payload


def assert_no_staging(parent: Path):
    assert not [path for path in parent.iterdir() if path.name.startswith(".bundle.tmp-")]


def test_success_is_canonical_atomic_and_exactly_replayable(monkeypatch, tmp_path):
    contract, payload = fixture_contract(monkeypatch)
    output = tmp_path / "bundle"
    result = publisher.publish_projection_fixture(
        contract, projection_bytes=payload, output_directory=output,
    )
    replay = publisher.verify_published_projection_bundle(contract, output)
    assert result == replay
    assert tuple(sorted(path.name for path in output.iterdir())) == publisher.FILES
    assert (output / "projection.json").read_bytes() == payload
    assert_no_staging(tmp_path)


def test_deterministic_bundle_and_input_order_independence(monkeypatch, tmp_path):
    first = projection_document()
    second = dict(reversed(list(first.items())))
    contract, first_payload = fixture_contract(monkeypatch, first)
    first_result = publisher.publish_projection_fixture(
        contract, projection_bytes=first_payload, output_directory=tmp_path / "one",
    )
    second_payload = canonical(second)
    second_result = publisher.publish_projection_fixture(
        contract, projection_bytes=second_payload, output_directory=tmp_path / "two",
    )
    assert first_payload == second_payload
    assert first_result.bundle_digest == second_result.bundle_digest


@pytest.mark.parametrize("mutation", ("missing", "extra", "noncanonical", "malformed"))
def test_projection_schema_and_canonical_faults_fail_before_publication(monkeypatch, tmp_path, mutation):
    document = projection_document()
    if mutation == "missing":
        document.pop("surfaces")
        payload = canonical(document)
    elif mutation == "extra":
        document["score"] = 1
        payload = canonical(document)
    elif mutation == "noncanonical":
        payload = json.dumps(document).encode()
    else:
        payload = b"{"
    monkeypatch.setattr(publisher, "EXPECTED_PROJECTION_DIGEST", sha256(payload).hexdigest())
    contract = publisher.build_projection_publication_contract()
    with pytest.raises(publisher.ProjectionPublicationError):
        publisher.publish_projection_fixture(
            contract, projection_bytes=payload, output_directory=tmp_path / "bundle",
        )
    assert not (tmp_path / "bundle").exists()


@pytest.mark.parametrize("mutation,reason", (
    ("digest", "PROJECTION_DIGEST"),
    ("lineage", "LINEAGE_OR_PROVENANCE"),
    ("authority", "AUTHORITY_OR_INTERPRETATION"),
    ("accounting", "INCONSISTENT_ACCOUNTING"),
    ("missingness", "INCONSISTENT_ACCOUNTING"),
))
def test_digest_lineage_authority_and_accounting_drift_fail(monkeypatch, tmp_path, mutation, reason):
    document = projection_document()
    payload = canonical(document)
    if mutation == "digest":
        monkeypatch.setattr(publisher, "EXPECTED_PROJECTION_DIGEST", "0" * 64)
    else:
        if mutation == "lineage":
            document["input_lineage"]["psi0c_d_digest"] = "0" * 64
        elif mutation == "authority":
            document["authority"]["integration"] = True
        elif mutation == "accounting":
            document["surfaces"][QUERY_IDS[0]]["duplicate_row_count"] = 0
        else:
            document["surfaces"][QUERY_IDS[0]]["missingness_semantics"] = "ABSENT"
        payload = canonical(document)
        monkeypatch.setattr(publisher, "EXPECTED_PROJECTION_DIGEST", sha256(payload).hexdigest())
    contract = publisher.build_projection_publication_contract()
    with pytest.raises(publisher.ProjectionPublicationError, match=reason):
        publisher.publish_projection_fixture(
            contract, projection_bytes=payload, output_directory=tmp_path / "bundle",
        )
    assert not (tmp_path / "bundle").exists()


def test_contract_authority_bypass_fails(monkeypatch, tmp_path):
    contract, payload = fixture_contract(monkeypatch)
    altered = replace(contract, grants_integration_authority=True)
    with pytest.raises(publisher.ProjectionPublicationError, match="CONTRACT_REPLAY"):
        publisher.publish_projection_fixture(
            altered, projection_bytes=payload, output_directory=tmp_path / "bundle",
        )


def test_preexisting_output_fails_without_mutation(monkeypatch, tmp_path):
    contract, payload = fixture_contract(monkeypatch)
    output = tmp_path / "bundle"
    output.mkdir()
    marker = output / "owned.txt"
    marker.write_text("preserve")
    with pytest.raises(publisher.ProjectionPublicationError, match="OUTPUT_REUSE"):
        publisher.publish_projection_fixture(
            contract, projection_bytes=payload, output_directory=output,
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
    with pytest.raises(publisher.ProjectionPublicationError, match="IO_FAILURE"):
        publisher.publish_projection_fixture(
            contract, projection_bytes=payload, output_directory=tmp_path / "bundle",
        )
    assert not (tmp_path / "bundle").exists()
    assert_no_staging(tmp_path)


def test_directory_fsync_failure_cleans_staging(monkeypatch, tmp_path):
    contract, payload = fixture_contract(monkeypatch)
    monkeypatch.setattr(publisher, "_fsync_directory", lambda path: (_ for _ in ()).throw(OSError("fsync fault")))
    with pytest.raises(publisher.ProjectionPublicationError, match="IO_FAILURE"):
        publisher.publish_projection_fixture(
            contract, projection_bytes=payload, output_directory=tmp_path / "bundle",
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
    with pytest.raises(publisher.ProjectionPublicationError, match="IO_FAILURE"):
        publisher.publish_projection_fixture(
            contract, projection_bytes=payload, output_directory=tmp_path / "bundle",
        )
    assert not (tmp_path / "bundle").exists()
    assert_no_staging(tmp_path)


def test_rename_failure_cleans_staging(monkeypatch, tmp_path):
    contract, payload = fixture_contract(monkeypatch)
    monkeypatch.setattr(publisher.os, "replace", lambda source, target: (_ for _ in ()).throw(OSError("rename fault")))
    with pytest.raises(publisher.ProjectionPublicationError, match="IO_FAILURE"):
        publisher.publish_projection_fixture(
            contract, projection_bytes=payload, output_directory=tmp_path / "bundle",
        )
    assert not (tmp_path / "bundle").exists()
    assert_no_staging(tmp_path)


def test_post_rename_replay_failure_removes_output(monkeypatch, tmp_path):
    contract, payload = fixture_contract(monkeypatch)
    monkeypatch.setattr(
        publisher,
        "verify_published_projection_bundle",
        lambda *args, **kwargs: (_ for _ in ()).throw(publisher.ProjectionPublicationError("tamper")),
    )
    with pytest.raises(publisher.ProjectionPublicationError, match="tamper"):
        publisher.publish_projection_fixture(
            contract, projection_bytes=payload, output_directory=tmp_path / "bundle",
        )
    assert not (tmp_path / "bundle").exists()
    assert_no_staging(tmp_path)


def test_published_tamper_fails_replay(monkeypatch, tmp_path):
    contract, payload = fixture_contract(monkeypatch)
    output = tmp_path / "bundle"
    publisher.publish_projection_fixture(contract, projection_bytes=payload, output_directory=output)
    hashes = json.loads((output / "hashes.json").read_bytes())
    hashes["bundle_digest"] = "0" * 64
    (output / "hashes.json").write_bytes(canonical(hashes))
    with pytest.raises(publisher.ProjectionPublicationError, match="HASH_REPLAY"):
        publisher.verify_published_projection_bundle(contract, output)
