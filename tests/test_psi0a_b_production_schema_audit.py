import sqlite3

import pytest

from src.evidence.contracts.production_shadow_boundary import build_production_shadow_boundary
from src.evidence.contracts.production_shadow_schema_audit import (
    ProductionShadowSchemaAuditError,
    RequiredRelation,
    audit_production_schema,
    verify_production_schema_audit,
)


def _database(path, indexed=True):
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE source_records(id INTEGER PRIMARY KEY,mint TEXT NOT NULL,observed_at INTEGER)")
    if indexed:
        connection.execute("CREATE INDEX idx_source_mint ON source_records(mint,observed_at)")
    connection.commit(); connection.close()


def _inputs(path):
    boundary = build_production_shadow_boundary(
        engineering_revision="491aa4ce",
        surfaces=({"database_id": "main", "relation_name": "source_records", "relation_type": "TABLE"},),
    )
    requirements = (RequiredRelation(
        "main", "source_records", "TABLE",
        (("id", "INTEGER"), ("mint", "TEXT"), ("observed_at", "INTEGER")),
        (("mint",),),
    ),)
    return boundary, {"main": path}, requirements


def test_fixture_schema_audit_is_query_only_compatible_and_replayable(tmp_path):
    path = tmp_path / "source.db"; _database(path)
    result = audit_production_schema(*_inputs(path))
    assert result.verdict == "SCHEMA_COMPATIBLE"
    assert result.compatible_relation_count == 1
    assert result.production_rows_read == 0
    assert result.evidence_extractions == 0
    assert verify_production_schema_audit(result)


def test_missing_index_or_type_fails_compatibility_without_mutation(tmp_path):
    path = tmp_path / "source.db"; _database(path, indexed=False)
    boundary, paths, requirements = _inputs(path)
    result = audit_production_schema(boundary, paths, requirements)
    assert result.verdict == "SCHEMA_INCOMPATIBLE"
    assert result.findings[0].missing_index_prefixes == (("mint",),)
    connection = sqlite3.connect(path)
    assert connection.execute("SELECT COUNT(*) FROM source_records").fetchone()[0] == 0
    connection.close()


def test_unknown_source_or_boundary_drift_fails_closed(tmp_path):
    path = tmp_path / "source.db"; _database(path)
    boundary, paths, requirements = _inputs(path)
    with pytest.raises(ProductionShadowSchemaAuditError, match="SOURCE_SET"):
        audit_production_schema(boundary, {**paths, "other": path}, requirements)
    with pytest.raises(ProductionShadowSchemaAuditError, match="BOUNDARY_REQUIREMENT"):
        audit_production_schema(boundary, paths, ())
