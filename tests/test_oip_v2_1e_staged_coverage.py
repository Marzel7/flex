from dataclasses import dataclass
import json
import sqlite3

from src.intelligence.staged_coverage_expansion import construct_manifest
from src.intelligence.migrated_coverage import reclassify_census_snapshot


@dataclass
class Row:
    mint: str
    reason: str
    creation_signature: str
    migration_signature: str


def test_manifest_is_migration_first_paired_and_deterministic():
    rows = [Row("a", "MISSING_MIGRATION_TRANSACTION", "ca", "ma"),
            Row("b", "MISSING_CREATION_TRANSACTION", "cb", "mb"),
            Row("c", "MISSING_CREATION_AND_MIGRATION_TRANSACTION", "cc", "mc"),
            Row("d", "MISSING_CREATION_AND_MIGRATION_TRANSACTION", "cd", "md")]
    first, summary = construct_manifest(rows, limit=5)
    second, _ = construct_manifest(list(reversed(rows)), limit=5)
    assert first == second
    assert [(row.dependency_type, row.expected_completion_effect) for row in first[:2]] == [
        ("MIGRATION", "COMPLETE_LAUNCH"), ("CREATION", "COMPLETE_LAUNCH")]
    assert first[2].dependency_type == "MIGRATION" and first[3].dependency_type == "CREATION"
    assert summary["completion_opportunities"] == 3


def test_manifest_never_exceeds_physical_target_limit():
    rows = [Row(str(index), "MISSING_CREATION_AND_MIGRATION_TRANSACTION", f"c{index}", f"m{index}")
            for index in range(1000)]
    targets, _ = construct_manifest(rows, limit=1000)
    assert len(targets) == 1000
    assert targets[-1].manifest_position == 1000


def test_manifest_can_label_a_repeated_stage_without_changing_order():
    rows = [Row("a", "MISSING_CREATION_AND_MIGRATION_TRANSACTION", "ca", "ma")]
    baseline, _ = construct_manifest(rows, milestone="OIP v2.1E")
    repeated, summary = construct_manifest(rows, milestone="OIP v2.1F")
    assert baseline == repeated
    assert summary["milestone"] == "OIP v2.1F"


def test_snapshot_reclassification_does_not_consult_mutable_source(tmp_path):
    evidence = tmp_path / "evidence.db"
    with sqlite3.connect(evidence) as connection:
        connection.execute("CREATE TABLE normalized_evidence_records (fact_family TEXT, natural_key TEXT, payload_json TEXT)")
        connection.execute("CREATE TABLE normalization_status (raw_artifact_digest TEXT, status TEXT)")
        for signature in ("create", "migration"):
            connection.execute("INSERT INTO normalized_evidence_records VALUES ('TransactionFact', ?, '{}')", (f"tx/{signature}",))
        connection.execute("INSERT INTO normalized_evidence_records VALUES ('LaunchFact', 'launch/mint', ?)",
                           (json.dumps({"mint": "mint", "creation_signature": "create"}),))
    rows = reclassify_census_snapshot([{"mint": "mint", "creation_signature": "create",
        "migration_signature": "migration"}], evidence)
    assert rows[0].state == "COMPLETE"
