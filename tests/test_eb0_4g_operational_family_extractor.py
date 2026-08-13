import json
import sqlite3

import pytest

from src.evidence.contracts.operational_family_extractor import OperationalFamilyExtractorError, extract_operational_families


def _database(path, *, extra=False):
    connection = sqlite3.connect(path)
    connection.executescript("""
    CREATE TABLE operation_cohort(position INTEGER, operation_id TEXT);
    CREATE TABLE normalized_operation_runtime(schema_version TEXT,identity_basis TEXT,operation_id TEXT,primary_role TEXT,contract_id TEXT,contract_version TEXT,module_id TEXT,module_version TEXT,topology_revision_id TEXT,behaviour_observation_id TEXT,input_digest TEXT,edge_features_json TEXT,mechanism_features_json TEXT,temporal_features_json TEXT,quality_state TEXT,completeness_state TEXT,conflict_group_id TEXT);
    CREATE TABLE nomination_candidates(group_id TEXT,position INTEGER,operation_id TEXT,nomination_state TEXT);
    """)
    for position, operation in enumerate(("operation-alpha", "operation-beta")):
        connection.execute("INSERT INTO operation_cohort VALUES (?,?)", (position, operation))
        connection.execute("INSERT INTO normalized_operation_runtime VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            "eb0.4c.normalized-runtime.v1", "PLATFORM_OPERATION_ID", operation, "PROVISIONING_OPERATION",
            "watchtower_v1", "1.0", f"module-{position}", "1.0", f"topology-{position}", f"behaviour-{position}", f"input-{position}",
            json.dumps(["TREASURY->FUNDING->CREATOR"]), json.dumps(["WSOL_WRAP_CLOSE"]), json.dumps(["BURST_THEN_DORMANT"]),
            "OBSERVED", "COMPLETE", None,
        ))
        connection.execute("INSERT INTO nomination_candidates VALUES (?,?,?,?)", ("group-1", position, operation, "SUPPORTED"))
    if extra:
        connection.execute("CREATE TABLE forbidden(value TEXT)")
    connection.commit(); connection.close()


def test_fixture_only_extraction_is_deterministic_accounted_and_query_only(tmp_path):
    path = tmp_path / "source.db"; _database(path)
    first = extract_operational_families(path)
    second = extract_operational_families(path)
    assert first == second
    assert first.selected_operation_ids == first.qualified_operation_ids
    assert first.candidate_group_count == 1
    assert first.fact_count == 2 and first.nomination_count == 1
    assert first.manifests[0].nomination_state_counts == {"SUPPORTED": 1}


def test_schema_drift_and_invalid_query_bound_fail_closed(tmp_path):
    path = tmp_path / "source.db"; _database(path, extra=True)
    with pytest.raises(OperationalFamilyExtractorError, match="SCHEMA_OBJECT_MISMATCH"):
        extract_operational_families(path)
    with pytest.raises(OperationalFamilyExtractorError, match="INVALID_QUERY_BOUND"):
        extract_operational_families(path, max_query_seconds=31)


def test_orphan_and_invalid_candidate_groups_fail_closed(tmp_path):
    path = tmp_path / "source.db"; _database(path)
    connection = sqlite3.connect(path)
    connection.execute("DELETE FROM normalized_operation_runtime WHERE operation_id='operation-beta'")
    connection.commit(); connection.close()
    with pytest.raises(OperationalFamilyExtractorError, match="ORPHAN_CANDIDATE_MEMBERSHIP"):
        extract_operational_families(path)


def test_missing_source_and_malformed_feature_json_fail_closed(tmp_path):
    with pytest.raises(OperationalFamilyExtractorError, match="SOURCE_NOT_FOUND"):
        extract_operational_families(tmp_path / "missing.db")
    path = tmp_path / "source.db"; _database(path)
    connection = sqlite3.connect(path)
    connection.execute("UPDATE normalized_operation_runtime SET mechanism_features_json='{}' WHERE operation_id='operation-alpha'")
    connection.commit(); connection.close()
    with pytest.raises(OperationalFamilyExtractorError, match="INVALID_FEATURE_JSON"):
        extract_operational_families(path)
