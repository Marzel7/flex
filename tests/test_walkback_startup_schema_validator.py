"""Read-only, fail-closed contract for ordinary Walkback startup."""
import sqlite3

import pytest

from src.core import walkback_queue


def _current():
    conn = sqlite3.connect(":memory:")
    walkback_queue.ensure_schema(conn)
    return conn


def test_legacy_schema_is_the_frozen_validator_contract():
    conn = _current()
    assert walkback_queue.validate_schema(conn).code == "VALID"


@pytest.mark.parametrize("table", sorted(walkback_queue.WALKBACK_STARTUP_SCHEMA_TABLES))
def test_every_required_table_fails_closed_when_missing(table):
    conn = _current()
    conn.execute(f'DROP TABLE "{table}"')
    result = walkback_queue.validate_schema(conn)
    assert result.code == "SCHEMA_MIGRATION_REQUIRED"
    assert table in result.mismatch["missing_tables"]


@pytest.mark.parametrize("index", sorted(walkback_queue.WALKBACK_STARTUP_SCHEMA_INDEXES))
def test_every_required_index_fails_closed_when_missing(index):
    conn = _current()
    conn.execute(f'DROP INDEX "{index}"')
    result = walkback_queue.validate_schema(conn)
    assert result.code == "SCHEMA_MIGRATION_REQUIRED"
    assert index in result.mismatch["missing_indexes"]


def test_wrong_column_property_and_index_shape_fail_closed():
    conn = _current()
    conn.execute("DROP INDEX ix_wbq_status")
    conn.execute("CREATE UNIQUE INDEX ix_wbq_status ON wt_walkback_queue(enqueued_at, status)")
    result = walkback_queue.validate_schema(conn)
    assert result.code == "SCHEMA_MIGRATION_REQUIRED"
    assert result.mismatch["structural_mismatch"] is True


def test_validator_uses_only_metadata_reads_and_preserves_connection():
    conn = _current()
    before = conn.total_changes
    result = walkback_queue.validate_schema(conn)
    assert result.valid is True
    assert conn.total_changes == before
    assert conn.execute("SELECT 1").fetchone()[0] == 1


def test_stale_schema_fails_closed_without_repair():
    conn = _current()
    conn.execute("DROP INDEX ix_wbq_status")
    assert walkback_queue.validate_schema(conn).code == "SCHEMA_MIGRATION_REQUIRED"
    assert "ix_wbq_status" not in {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
