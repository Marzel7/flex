"""X78.40: bounded rotation for the X78.19/X78.22/X78.23 diagnostic JSONL writers.

Proves the RotatingFileHandler-backed append path used by db_locking.py:
  - still appends valid JSONL when enabled;
  - can be explicitly disabled for X78.22/X78.23;
  - bounds file growth via rotation instead of unbounded append;
  - never raises into callers on rotation/write failure;
  - does not touch sqlite/DB state.
"""
import importlib
import json
import os
import sqlite3

import pytest


@pytest.fixture
def db_locking(monkeypatch, tmp_path):
    monkeypatch.delenv("DB_CONNECTION_LIFECYCLE_DIAGNOSTICS_PATH", raising=False)
    monkeypatch.delenv("X78_CF_SQL_DIAGNOSTICS_ENABLED", raising=False)
    monkeypatch.delenv("X78_CF_SQL_DIAGNOSTICS_MAX_BYTES", raising=False)
    monkeypatch.delenv("X78_CF_SQL_DIAGNOSTICS_BACKUP_COUNT", raising=False)
    monkeypatch.delenv("DB_CONNECTION_LIFECYCLE_DIAGNOSTICS_MAX_BYTES", raising=False)
    monkeypatch.delenv("DB_CONNECTION_LIFECYCLE_DIAGNOSTICS_BACKUP_COUNT", raising=False)
    import src.utils.db_locking as mod
    importlib.reload(mod)
    mod._DIAGNOSTIC_ROTATING_HANDLERS.clear()
    yield mod
    mod._DIAGNOSTIC_ROTATING_HANDLERS.clear()


class _FakeConn:
    def __init__(self, caller):
        self._db_caller = caller
        self._db_connection_id = "conn-1"
        self._write_transaction_id = None
        self._holds_write_lock = False
        self.in_transaction = False


def test_append_diagnostic_jsonl_writes_valid_lines(db_locking, tmp_path):
    path = str(tmp_path / "diag.jsonl")
    db_locking._append_diagnostic_jsonl(
        path, {"a": 1}, max_bytes_env="X78_TEST_MAX_BYTES", backup_count_env="X78_TEST_BACKUP_COUNT"
    )
    db_locking._append_diagnostic_jsonl(
        path, {"a": 2}, max_bytes_env="X78_TEST_MAX_BYTES", backup_count_env="X78_TEST_BACKUP_COUNT"
    )
    lines = open(path).read().splitlines()
    assert [json.loads(l) for l in lines] == [{"a": 1}, {"a": 2}]


def test_bounded_retention_rotates_instead_of_growing_unbounded(db_locking, tmp_path, monkeypatch):
    path = str(tmp_path / "diag.jsonl")
    monkeypatch.setenv("X78_TEST_MAX_BYTES", "500")
    monkeypatch.setenv("X78_TEST_BACKUP_COUNT", "2")
    for i in range(200):
        db_locking._append_diagnostic_jsonl(
            path, {"i": i, "pad": "x" * 20},
            max_bytes_env="X78_TEST_MAX_BYTES", backup_count_env="X78_TEST_BACKUP_COUNT",
        )
    current_size = os.path.getsize(path)
    assert current_size < 5000, f"current file grew unbounded: {current_size} bytes"
    backups = [p for p in os.listdir(tmp_path) if p.startswith("diag.jsonl.")]
    assert len(backups) <= 2, f"backup count exceeded configured cap: {backups}"


def test_rotation_never_raises_into_caller_on_write_failure(db_locking, tmp_path, monkeypatch):
    path = str(tmp_path / "nested" / "diag.jsonl")

    def boom(*a, **kw):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(db_locking.os, "makedirs", boom)
    # Must not raise despite the underlying failure — diagnostics are fail-open.
    db_locking._append_diagnostic_jsonl(
        path, {"a": 1}, max_bytes_env="X78_TEST_MAX_BYTES", backup_count_env="X78_TEST_BACKUP_COUNT"
    )


def test_x78_22_disabled_via_explicit_env_emits_nothing(db_locking, tmp_path, monkeypatch):
    monkeypatch.setenv("X78_CF_SQL_DIAGNOSTICS_ENABLED", "0")
    conn = _FakeConn("realtime_creator_funding_extractor.py:123")
    assert db_locking._cf_sql_diagnostics_enabled(conn) is False


def test_x78_22_default_preserves_existing_qualifying_caller_behavior(db_locking):
    qualifying = _FakeConn("realtime_creator_funding_extractor.py:123")
    other = _FakeConn("some_other_module.py:5")
    assert db_locking._cf_sql_diagnostics_enabled(qualifying) is True
    assert db_locking._cf_sql_diagnostics_enabled(other) is False
    predictor = _FakeConn("token_prediction_builder.py:9")
    assert db_locking._cf_sql_diagnostics_enabled(predictor) is True


def test_x78_22_statement_lifecycle_appends_and_rotates(db_locking, tmp_path, monkeypatch):
    target = tmp_path / "x78_22_creator_funding_sql.jsonl"
    monkeypatch.setattr(db_locking, "_CF_SQL_DIAGNOSTICS_PATH", str(target))
    monkeypatch.setenv("X78_CF_SQL_DIAGNOSTICS_MAX_BYTES", "2000")
    monkeypatch.setenv("X78_CF_SQL_DIAGNOSTICS_BACKUP_COUNT", "1")
    conn = _FakeConn("realtime_creator_funding_extractor.py:1")
    for _ in range(100):
        state = db_locking._cf_statement_start(conn, "SELECT 1")
        db_locking._cf_statement_end(conn, state, success=True, rowcount=1)
    assert os.path.getsize(target) < 5000
    lines = open(target).read().splitlines()
    assert lines
    for l in lines:
        json.loads(l)  # every retained line is valid JSON


def test_connection_lifecycle_diagnostic_compatible_and_bounded(db_locking, tmp_path, monkeypatch):
    target = tmp_path / "x78_19_listener_connections.jsonl"
    monkeypatch.setenv("DB_CONNECTION_LIFECYCLE_DIAGNOSTICS_PATH", str(target))
    monkeypatch.setenv("DB_CONNECTION_LIFECYCLE_DIAGNOSTICS_MAX_BYTES", "2000")
    monkeypatch.setenv("DB_CONNECTION_LIFECYCLE_DIAGNOSTICS_BACKUP_COUNT", "1")
    for i in range(150):
        db_locking._append_connection_lifecycle({"event": "opened", "connection_id": f"c{i}"})
    assert os.path.getsize(target) < 5000
    lines = open(target).read().splitlines()
    for l in lines:
        payload = json.loads(l)
        assert payload["schema"] == "x78.19.connection_lifecycle.v1"


def test_connection_lifecycle_unset_path_emits_nothing(db_locking, tmp_path, monkeypatch):
    monkeypatch.delenv("DB_CONNECTION_LIFECYCLE_DIAGNOSTICS_PATH", raising=False)
    # Should be a pure no-op: no file created anywhere under tmp_path.
    db_locking._append_connection_lifecycle({"event": "opened"})
    assert list(tmp_path.iterdir()) == []


def test_rotation_introduces_no_sqlite_access(db_locking, tmp_path, monkeypatch):
    calls = []
    orig_connect = sqlite3.connect

    def spy_connect(*a, **kw):
        calls.append((a, kw))
        return orig_connect(*a, **kw)

    monkeypatch.setattr(sqlite3, "connect", spy_connect)
    path = str(tmp_path / "diag.jsonl")
    monkeypatch.setenv("X78_TEST_MAX_BYTES", "300")
    monkeypatch.setenv("X78_TEST_BACKUP_COUNT", "1")
    for i in range(50):
        db_locking._append_diagnostic_jsonl(
            path, {"i": i}, max_bytes_env="X78_TEST_MAX_BYTES", backup_count_env="X78_TEST_BACKUP_COUNT"
        )
    assert calls == []
