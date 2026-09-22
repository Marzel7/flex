"""Offline contract for fingerprint monitoring after a real Walkback completion."""

import sqlite3

import pytest

from src.ops import operation_fingerprint_drift as drift
from tests.test_x65_44_walkback_worker_promotion_hook import ops  # noqa: F401
from tests.test_walkback_worker_startup_resilience import stub_run_loop_dependencies  # noqa: F401
import src.core.walkback_worker as worker


def test_fingerprint_schema_validator_is_read_only_and_fails_closed():
    conn = sqlite3.connect(":memory:")
    statements = []
    conn.set_trace_callback(statements.append)
    assert drift.validate_schema(conn).startswith("FINGERPRINT_SCHEMA_MISSING:")
    conn.set_trace_callback(None)
    drift.ensure_schema(conn)  # explicit provisioning, never completion-time
    conn.set_trace_callback(statements.append)
    assert drift.validate_schema(conn) == "VALID"
    assert not any(sql.lstrip().upper().startswith(("CREATE", "ALTER", "DROP")) for sql in statements)
    conn.execute("DROP TABLE operation_fingerprint_drift_clusters")
    assert drift.validate_schema(conn).startswith("FINGERPRINT_SCHEMA_MISSING:")


def test_nonempty_walkback_completion_emits_no_fingerprint_ddl(ops):
    drift.ensure_schema(ops)  # established schema contract before runtime
    assert drift.validate_schema(ops) == "VALID"
    statements = []
    ops.set_trace_callback(statements.append)
    row = ops.execute("SELECT * FROM wt_walkback_queue WHERE mint='mintX'").fetchone()
    worker._process_row(ops, row)
    ops.set_trace_callback(None)
    status = ops.execute("SELECT status FROM wt_walkback_queue WHERE mint='mintX'").fetchone()[0]
    assert status == "complete"
    completion = next(i for i, sql in enumerate(statements) if "UPDATE wt_walkback_queue" in sql and "status='complete'" in sql)
    fingerprint_ddl = [sql for sql in statements[completion + 1:] if sql.lstrip().upper().startswith(("CREATE", "ALTER", "DROP", "REINDEX")) and "operation_fingerprint" in sql.lower()]
    assert fingerprint_ddl == []


def test_absent_schema_cannot_be_created_by_completion(monkeypatch):
    conn = sqlite3.connect(":memory:")
    statements = []
    conn.set_trace_callback(statements.append)
    monkeypatch.setattr(drift, "_rows", lambda _conn, _mint: [])
    monkeypatch.setattr(drift, "_exact_profiles", lambda _conn, _mint: set())
    monkeypatch.setattr(drift, "_active_operations", lambda _conn: [{"operator_id": "op", "display_name": "Byzantine"}])
    monkeypatch.setattr(drift, "_expected_route", lambda _conn, _name: None)
    with pytest.raises(sqlite3.OperationalError):
        drift.observe_completed_walkback(conn, "mint")
    assert drift.validate_schema(conn).startswith("FINGERPRINT_SCHEMA_MISSING:")
    assert not any(sql.lstrip().upper().startswith("CREATE") for sql in statements)


def test_run_loop_fails_closed_before_work_without_fingerprint_schema(stub_run_loop_dependencies, monkeypatch):
    monkeypatch.setattr(drift, "validate_schema", lambda _conn: "FINGERPRINT_SCHEMA_MISSING:operation_fingerprint_drift_evidence")
    with pytest.raises(RuntimeError, match="FINGERPRINT_SCHEMA_MISSING"):
        worker.run_loop()
