from dataclasses import replace
from pathlib import Path
import sqlite3

import pytest

from src.evidence.contracts.operational_family_retained_input_store import (
    OperationalFamilyRetainedInputStoreError,
    build_operational_family_retained_input_store_contract,
    export_operational_family_retained_inputs,
    publish_fixture_operational_family_retained_inputs,
    verify_operational_family_retained_input_store_contract,
)
from tests.test_psi0f_f5_operational_family_source_materialization import material


def review_metadata(values):
    return [
        {
            "review_id": row["review_id"],
            "reviewer_class": "FIXTURE_REVIEW",
            "reason_codes": ["EVIDENCE_COMPLETE", "RECURRING_BEHAVIOUR"]
            if row["nomination_state"] == "SUPPORTED" else ["CONFLICT_PRESENT", "EVIDENCE_INCOMPLETE"],
            "reviewed_sequence": position,
        }
        for position, row in enumerate(values["dispositions"])
    ]


def publish(path: Path, values=None):
    values = values or material()
    return publish_fixture_operational_family_retained_inputs(
        path, **values, review_metadata=review_metadata(values), logical_capture_sequence=7,
    )


def test_contract_is_fixture_scoped_query_only_and_authority_free():
    contract = build_operational_family_retained_input_store_contract()
    assert verify_operational_family_retained_input_store_contract(contract)
    assert contract.fixture_publisher_only and contract.publisher_requires_new_path
    assert contract.exporter_query_only and not contract.exporter_writes_files
    assert not contract.real_store_capture_authorized and not any(contract.authority.values())
    with pytest.raises(OperationalFamilyRetainedInputStoreError, match="CONTRACT_REPLAY_MISMATCH"):
        verify_operational_family_retained_input_store_contract(
            replace(contract, real_store_capture_authorized=True)
        )


def test_fixture_publish_and_query_only_export_reconstruct_exact_f9_and_f5(tmp_path):
    published = publish(tmp_path / "retained.db")
    exported = export_operational_family_retained_inputs(published.path, published.retention_id)
    assert exported.query_count == 7
    assert exported.manifest_digest == published.manifest_digest
    assert exported.bundle.bundle_digest == published.bundle_digest
    assert exported.bundle.source_digest == published.source_digest


def test_published_store_has_complete_counts_and_no_companions(tmp_path):
    published = publish(tmp_path / "retained.db")
    connection = sqlite3.connect(published.path)
    try:
        counts = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "retention_manifest", "operation_cohort", "evaluation_summaries",
                "normalized_runtime_projections", "candidate_payloads",
                "nomination_dispositions", "nomination_disposition_members",
            )
        }
    finally:
        connection.close()
    assert counts == {
        "retention_manifest": 1, "operation_cohort": 4, "evaluation_summaries": 4,
        "normalized_runtime_projections": 4, "candidate_payloads": 2,
        "nomination_dispositions": 2, "nomination_disposition_members": 4,
    }
    assert not any(Path(f"{published.path}{suffix}").exists() for suffix in ("-wal", "-shm", "-journal"))


@pytest.mark.parametrize("table", [
    "retention_manifest", "operation_cohort", "evaluation_summaries",
    "normalized_runtime_projections", "candidate_payloads", "nomination_dispositions",
    "nomination_disposition_members",
])
def test_every_retained_table_is_update_and_delete_immutable(tmp_path, table):
    published = publish(tmp_path / "retained.db")
    connection = sqlite3.connect(published.path)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(f"UPDATE {table} SET retention_id=retention_id")
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(f"DELETE FROM {table}")
    finally:
        connection.close()


def test_destination_must_be_new_and_is_never_overwritten(tmp_path):
    destination = tmp_path / "retained.db"
    destination.write_text("owned")
    with pytest.raises(OperationalFamilyRetainedInputStoreError, match="DESTINATION_NOT_NEW"):
        publish(destination)
    assert destination.read_text() == "owned"


@pytest.mark.parametrize("mutation,code", [
    (lambda rows: rows[0].update(reviewer_class="OPERATOR"), "REVIEW_METADATA_INVALID"),
    (lambda rows: rows[0].update(reason_codes=[]), "REVIEW_METADATA_INVALID"),
    (lambda rows: rows[0].update(reason_codes=["UNKNOWN"]), "REVIEW_METADATA_INVALID"),
    (lambda rows: rows[1].update(reviewed_sequence=rows[0]["reviewed_sequence"]), "REVIEW_METADATA_COVERAGE_DRIFT"),
    (lambda rows: rows.pop(), "REVIEW_METADATA_COVERAGE_DRIFT"),
])
def test_review_metadata_fails_closed(tmp_path, mutation, code):
    values = material()
    rows = review_metadata(values)
    mutation(rows)
    with pytest.raises(OperationalFamilyRetainedInputStoreError, match=code):
        publish_fixture_operational_family_retained_inputs(
            tmp_path / "retained.db", **values, review_metadata=rows, logical_capture_sequence=7,
        )


def test_candidate_lifecycle_cannot_replace_explicit_nomination(tmp_path):
    values = material()
    values["dispositions"][0]["nomination_state"] = values["candidates"][0]["lifecycle"]
    with pytest.raises(Exception, match="INVALID_NOMINATION_STATE"):
        publish(tmp_path / "retained.db", values)
    assert not (tmp_path / "retained.db").exists()


def test_wrong_retention_identity_fails_closed(tmp_path):
    published = publish(tmp_path / "retained.db")
    with pytest.raises(OperationalFamilyRetainedInputStoreError, match="RETENTION_MANIFEST_COUNT_INVALID"):
        export_operational_family_retained_inputs(published.path, "wrong")


def test_schema_trigger_drift_fails_closed(tmp_path):
    published = publish(tmp_path / "retained.db")
    connection = sqlite3.connect(published.path)
    connection.execute("DROP TRIGGER candidate_payloads_no_update")
    connection.close()
    with pytest.raises(OperationalFamilyRetainedInputStoreError, match="SCHEMA_OBJECT_MISMATCH"):
        export_operational_family_retained_inputs(published.path, published.retention_id)


def test_recomputed_schema_preserving_payload_tamper_fails_closed(tmp_path):
    published = publish(tmp_path / "retained.db")
    connection = sqlite3.connect(published.path)
    connection.execute("DROP TRIGGER candidate_payloads_no_update")
    connection.execute("UPDATE candidate_payloads SET payload_digest=?", ("0" * 64,))
    connection.execute(
        "CREATE TRIGGER candidate_payloads_no_update BEFORE UPDATE ON candidate_payloads "
        "BEGIN SELECT RAISE(ABORT, 'immutable candidate_payloads'); END"
    )
    connection.commit()
    connection.close()
    with pytest.raises(OperationalFamilyRetainedInputStoreError, match="PAYLOAD_REPLAY_MISMATCH"):
        export_operational_family_retained_inputs(published.path, published.retention_id)


def test_companion_file_and_symlink_sources_fail_closed(tmp_path):
    published = publish(tmp_path / "retained.db")
    companion = Path(f"{published.path}-wal")
    companion.write_bytes(b"")
    with pytest.raises(OperationalFamilyRetainedInputStoreError, match="SOURCE_COMPANION_PRESENT"):
        export_operational_family_retained_inputs(published.path, published.retention_id)
    companion.unlink()
    link = tmp_path / "link.db"
    link.symlink_to(published.path)
    with pytest.raises(OperationalFamilyRetainedInputStoreError, match="SOURCE_OR_BOUND_INVALID"):
        export_operational_family_retained_inputs(link, published.retention_id)


def test_invalid_query_bound_and_timeout_fail_closed(tmp_path):
    published = publish(tmp_path / "retained.db")
    with pytest.raises(OperationalFamilyRetainedInputStoreError, match="SOURCE_OR_BOUND_INVALID"):
        export_operational_family_retained_inputs(published.path, published.retention_id, max_query_seconds=31)
    ticks = iter((0.0, 2.0))
    with pytest.raises(OperationalFamilyRetainedInputStoreError, match="QUERY_TIMEOUT"):
        export_operational_family_retained_inputs(
            published.path, published.retention_id, max_query_seconds=1, clock=lambda: next(ticks),
        )
