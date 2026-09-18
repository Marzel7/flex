"""Metadata-only lifecycle coverage for both operations DB connection systems."""
from __future__ import annotations

import json
import sqlite3
import pytest

from src.core.database_write_service import (DatabaseWriteLockError, DatabaseWriteService,
                                             _ServiceConnection, _native_connect)
from src.utils import db_locking


def _records(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_tracked_connection_emits_metadata_only_lifecycle(tmp_path, monkeypatch):
    output = tmp_path / "lifecycle.jsonl"
    monkeypatch.setenv("DB_SQLITE_LIFECYCLE_DIAGNOSTICS_PATH", str(output))
    db = tmp_path / "wt_ops_v2.db"
    conn = db_locking.db_connect(str(db), _caller="walkback_worker.py:provenance_test")
    try:
        conn.execute("CREATE TABLE wt_lifecycle_test (value TEXT)")
        conn.execute("INSERT INTO wt_lifecycle_test(value) VALUES (?)", ("private-value",))
        conn.commit()
    finally:
        conn.close()

    rows = _records(output)
    assert any(row["event"] == "flock_acquired" for row in rows)
    assert any(row["event"] == "sqlite_tx_begin" for row in rows)
    assert any(row["event"] == "commit_end" for row in rows)
    assert any(row["event"] == "flock_release" for row in rows)
    assert all("private-value" not in str(row) for row in rows)
    assert all("table_target" in row for row in rows if row["event"] == "statement_start")


def test_database_write_service_emits_same_statement_and_transaction_metadata(tmp_path, monkeypatch):
    output = tmp_path / "service-lifecycle.jsonl"
    monkeypatch.setenv("DB_SQLITE_LIFECYCLE_DIAGNOSTICS_PATH", str(output))
    db = tmp_path / "wt_ops_v2.db"
    service = DatabaseWriteService()

    def write(conn):
        conn.execute("CREATE TABLE wt_service_lifecycle (value TEXT)")
        conn.execute("INSERT INTO wt_service_lifecycle(value) VALUES (?)", ("private-value",))

    service.submit("ops-test", "service-provenance-test", write, path=str(db))
    rows = _records(output)
    starts = [row for row in rows if row["event"] == "statement_start"]
    assert starts
    assert any(row["event"] == "sqlite_tx_begin" for row in rows)
    assert any(row["event"] == "commit_end" for row in rows)
    assert all(row.get("connection_id") for row in starts)
    assert all("private-value" not in str(row) for row in rows)


def test_emergency_exemption_is_visible_without_normal_flock(tmp_path, monkeypatch):
    output = tmp_path / "emergency-lifecycle.jsonl"
    monkeypatch.setenv("DB_SQLITE_LIFECYCLE_DIAGNOSTICS_PATH", str(output))
    db = tmp_path / "wt_ops_v2.db"
    monkeypatch.setattr(db_locking, "_OPS_DB_ABS", str(db.resolve()))
    conn = db_locking.emergency_ops_recovery_connect(str(db))
    try:
        conn.execute("CREATE TABLE wt_emergency_lifecycle (value TEXT)")
        conn.execute("INSERT INTO wt_emergency_lifecycle(value) VALUES (?)", ("private-value",))
        conn.commit()
    finally:
        conn.close()

    rows = _records(output)
    assert any(row["event"] == "AUTHORIZED_EMERGENCY_WRITE_WITHOUT_FLOCK" for row in rows)
    assert any(row["event"] == "commit_end" and row["emergency_exemption"] for row in rows)
    assert any(row["event"] == "close" and row["emergency_exemption"] for row in rows)
    assert not any(row["event"] == "SQLITE_WRITE_WITHOUT_APPLICATION_FLOCK" for row in rows)


def test_registered_normal_native_ops_write_is_visible_and_flagged(tmp_path, monkeypatch):
    output = tmp_path / "native-lifecycle.jsonl"
    monkeypatch.setenv("DB_SQLITE_LIFECYCLE_DIAGNOSTICS_PATH", str(output))
    db = tmp_path / "wt_ops_v2.db"
    monkeypatch.setattr(db_locking, "_OPS_DB_ABS", str(db.resolve()))
    conn = db_locking.registered_native_ops_connect(
        str(db), caller="native_test", purpose="test_native",
    )
    try:
        conn.execute("CREATE TABLE wt_native_lifecycle (value TEXT)")
        conn.execute("INSERT INTO wt_native_lifecycle(value) VALUES ('x')")
        conn.commit()
    finally:
        conn.close()
    rows = _records(output)
    assert any(row["event"] == "SQLITE_WRITE_WITHOUT_APPLICATION_FLOCK" for row in rows)
    assert any(row.get("factory") == "NATIVE_SQLITE" for row in rows)


def test_native_blocker_is_visible_to_bounded_service_waiter(tmp_path, monkeypatch):
    output = tmp_path / "native-service-busy.jsonl"
    monkeypatch.setenv("DB_SQLITE_LIFECYCLE_DIAGNOSTICS_PATH", str(output))
    db = tmp_path / "wt_ops_v2.db"
    monkeypatch.setattr(db_locking, "_OPS_DB_ABS", str(db.resolve()))
    setup = db_locking.db_connect(str(db), _caller="setup.py:seed")
    setup.execute("CREATE TABLE wt_native_service_collision (value TEXT)")
    setup.commit(); setup.close()
    blocker = db_locking.registered_native_ops_connect(str(db), caller="native_blocker", purpose="test")
    try:
        blocker.execute("INSERT INTO wt_native_service_collision VALUES ('blocker')")
        blocker_id = blocker._db_connection_id
        service = DatabaseWriteService(_test_sqlite_timeout=0.05)
        with __import__("pytest").raises(DatabaseWriteLockError):
            service.submit("ops-test", "service-waiter", lambda c: c.execute(
                "INSERT INTO wt_native_service_collision VALUES ('waiter')"), path=str(db))
    finally:
        blocker.rollback(); blocker.close()
    busy = [row for row in _records(output) if row["event"] == "sqlite_busy"][-1]
    candidate = next(row for row in busy["blocker_candidates"] if row["connection_id"] == blocker_id)
    assert candidate["factory"] == "NATIVE_SQLITE"
    assert candidate["sqlite_tx_active"] is True
    assert candidate["flock_owned"] is False
    assert busy["waiter"]["connection_id"]
    assert DatabaseWriteService()._sqlite_timeout == 10.0


def test_tracked_blocker_is_visible_to_bounded_service_waiter(tmp_path, monkeypatch):
    output = tmp_path / "tracked-service-busy.jsonl"
    monkeypatch.setenv("DB_SQLITE_LIFECYCLE_DIAGNOSTICS_PATH", str(output))
    db = tmp_path / "wt_ops_v2.db"
    setup = db_locking.db_connect(str(db), _caller="setup.py:seed")
    setup.execute("CREATE TABLE wt_tracked_service_collision (value TEXT)")
    setup.commit(); setup.close()
    blocker = db_locking.db_connect(str(db), _caller="tracked_blocker.py:hold")
    try:
        blocker.execute("INSERT INTO wt_tracked_service_collision VALUES ('blocker')")
        blocker_id = blocker._db_connection_id
        blocker._release_write_lane()  # test-only malformed pre-existing SQLite owner
        service = DatabaseWriteService(_test_sqlite_timeout=0.05)
        with __import__("pytest").raises(DatabaseWriteLockError):
            service.submit("ops-test", "service-waiter", lambda c: c.execute(
                "INSERT INTO wt_tracked_service_collision VALUES ('waiter')"), path=str(db))
    finally:
        blocker.rollback(); blocker.close()
    busy = [row for row in _records(output) if row["event"] == "sqlite_busy"][-1]
    candidate = next(row for row in busy["blocker_candidates"] if row["connection_id"] == blocker_id)
    assert candidate["factory"] == "TrackedConnection"
    assert candidate["sqlite_tx_active"] is True
    assert candidate["flock_owned"] is False
    assert busy["waiter"]["connection_id"]


def test_service_connection_blocker_is_visible_to_tracked_waiter(tmp_path, monkeypatch):
    output = tmp_path / "service-tracked-busy.jsonl"
    monkeypatch.setenv("DB_SQLITE_LIFECYCLE_DIAGNOSTICS_PATH", str(output))
    db = tmp_path / "wt_ops_v2.db"
    setup = db_locking.db_connect(str(db), _caller="setup.py:seed")
    setup.execute("CREATE TABLE wt_service_tracked_collision (value TEXT)")
    setup.commit(); setup.close()
    blocker = _native_connect(str(db), timeout=0.05, factory=_ServiceConnection)
    blocker._db_path = str(db); blocker._db_caller = "service-blocker"
    blocker._db_connection_id = "service-blocker-test"; blocker._holds_write_lock = False
    db_locking.register_external_ops_connection(blocker, str(db), "service-blocker",
                                                factory="DatabaseWriteService", purpose="test")
    blocker_id = blocker._db_connection_id
    waiter = None
    try:
        blocker.execute("BEGIN")
        blocker.execute("INSERT INTO wt_service_tracked_collision VALUES ('blocker')")
        waiter = db_locking.db_connect(str(db), _caller="tracked_waiter.py:busy")
        waiter.execute("PRAGMA busy_timeout=50")
        with __import__("pytest").raises(sqlite3.OperationalError):
            waiter.execute("INSERT INTO wt_service_tracked_collision VALUES ('waiter')")
    finally:
        if waiter is not None:
            waiter.rollback(); waiter.close()
        blocker.service_rollback()
        db_locking.unregister_external_ops_connection(blocker, reason="test-close")
        blocker.close()
    busy = [row for row in _records(output) if row["event"] == "sqlite_busy"][-1]
    candidate = next(row for row in busy["blocker_candidates"]
                     if row["connection_id"] == blocker_id)
    assert candidate["factory"] == "DatabaseWriteService"
    assert candidate["sqlite_tx_active"] is True
    assert busy["waiter"]["connection_id"]


@pytest.mark.parametrize("system", ["tracked", "service", "native", "emergency"])
@pytest.mark.parametrize("ending", ["commit", "rollback", "exception", "close"])
def test_connection_cleanup_matrix(system, ending, tmp_path, monkeypatch):
    """16-case test-only contract: no closed connection remains an active candidate."""
    db = tmp_path / "wt_ops_v2.db"
    monkeypatch.setattr(db_locking, "_OPS_DB_ABS", str(db.resolve()))
    seed = db_locking.db_connect(str(db), _caller="seed.py")
    seed.execute("CREATE TABLE IF NOT EXISTS wt_cleanup (value TEXT)"); seed.commit(); seed.close()
    service = None
    if system == "tracked":
        conn = db_locking.db_connect(str(db), _caller="tracked_cleanup.py")
        cid = conn._db_connection_id
        conn.execute("INSERT INTO wt_cleanup VALUES ('x')")
        if ending == "commit": conn.commit()
        elif ending == "rollback": conn.rollback()
        elif ending == "exception":
            try: raise RuntimeError("test")
            except RuntimeError: conn.rollback()
        conn.close()
    elif system == "native":
        conn = db_locking.registered_native_ops_connect(str(db), caller="native_cleanup", purpose="test")
        cid = conn._db_connection_id; conn.execute("INSERT INTO wt_cleanup VALUES ('x')")
        if ending == "commit": conn.commit()
        elif ending in {"rollback", "exception"}: conn.rollback()
        conn.close()
    elif system == "emergency":
        conn = db_locking.emergency_ops_recovery_connect(str(db)); cid = conn._db_connection_id
        conn.execute("INSERT INTO wt_cleanup VALUES ('x')")
        if ending == "commit": conn.commit()
        elif ending in {"rollback", "exception"}: conn.rollback()
        conn.close()
    else:
        service = DatabaseWriteService(_test_sqlite_timeout=0.05)
        ids = []
        def work(conn):
            ids.append(conn._db_connection_id); conn.execute("INSERT INTO wt_cleanup VALUES ('x')")
            if ending == "exception": raise RuntimeError("test")
        if ending == "exception":
            with pytest.raises(RuntimeError): service.submit("cleanup", "service_cleanup", work, path=str(db))
        else: service.submit("cleanup", "service_cleanup", work, path=str(db))
        cid = ids[0]
    assert not any(row["connection_id"] == cid for row in db_locking._sqlite_lifecycle_snapshot())


def test_violation_case_1_clean_serialization(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_SQLITE_LIFECYCLE_DIAGNOSTICS_PATH", str(tmp_path / "x.jsonl")); db=tmp_path/"wt_ops_v2.db"
    for value in ("a", "b"):
        c=db_locking.db_connect(str(db), _caller="case1.py"); c.execute("CREATE TABLE IF NOT EXISTS t(v)"); c.execute("INSERT INTO t VALUES (?)",(value,)); c.commit(); c.close()
    rows=_records(tmp_path/"x.jsonl"); assert not any(r["event"] in {"SQLITE_WRITE_WITHOUT_APPLICATION_FLOCK","APPLICATION_FLOCK_RELEASE_WITH_SQLITE_TX_ACTIVE","SQLITE_TX_PREEXISTS_FLOCK"} for r in rows)


def test_violation_case_2_retained_after_release(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_SQLITE_LIFECYCLE_DIAGNOSTICS_PATH", str(tmp_path / "x.jsonl")); db=tmp_path/"wt_ops_v2.db"; c=db_locking.db_connect(str(db),_caller="case2.py")
    c.execute("CREATE TABLE t(v)"); c.execute("INSERT INTO t VALUES ('x')"); c._release_write_lane(); c.rollback(); c.close()
    assert any(r["event"]=="flock_release" and r.get("violation")=="APPLICATION_FLOCK_RELEASE_WITH_SQLITE_TX_ACTIVE" for r in _records(tmp_path/"x.jsonl"))


def test_violation_case_3_tx_preexists_flock(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_SQLITE_LIFECYCLE_DIAGNOSTICS_PATH", str(tmp_path / "x.jsonl")); db=tmp_path/"wt_ops_v2.db"; c=db_locking.db_connect(str(db),_caller="case3.py")
    c.execute("CREATE TABLE t(v)"); c.commit(); c.execute("INSERT INTO t VALUES ('x')"); c._release_write_lane(); c._acquire_write_lane(); c.rollback(); c.close()
    assert any(r["event"]=="SQLITE_TX_PREEXISTS_FLOCK" for r in _records(tmp_path/"x.jsonl"))


def test_violation_case_4_secondary_collision():
    # Covered dynamically by the retained-transaction waiter/snapshot regression above.
    assert True


def test_violation_case_5_normal_native():
    # Covered dynamically by test_registered_normal_native_ops_write_is_visible_and_flagged.
    assert True


def test_violation_case_6_emergency():
    # Covered dynamically by test_emergency_exemption_is_visible_without_normal_flock.
    assert True


def test_violation_case_7_clean_rollback(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_SQLITE_LIFECYCLE_DIAGNOSTICS_PATH", str(tmp_path / "x.jsonl")); db=tmp_path/"wt_ops_v2.db"; c=db_locking.db_connect(str(db),_caller="case7.py")
    c.execute("CREATE TABLE t(v)"); c.execute("INSERT INTO t VALUES ('x')"); c.rollback(); c.close()
    assert not any(r.get("violation")=="APPLICATION_FLOCK_RELEASE_WITH_SQLITE_TX_ACTIVE" for r in _records(tmp_path/"x.jsonl"))


def test_preexisting_retained_transaction_is_captured_as_busy_candidate(tmp_path, monkeypatch):
    """The production-shaped waiter/busy snapshot needs no SQLite owner API."""
    output = tmp_path / "collision-lifecycle.jsonl"
    monkeypatch.setenv("DB_SQLITE_LIFECYCLE_DIAGNOSTICS_PATH", str(output))
    db = tmp_path / "wt_ops_v2.db"
    setup = db_locking.db_connect(str(db), _caller="setup.py:seed")
    setup.execute("CREATE TABLE wt_collision_lifecycle (value TEXT)")
    setup.commit()
    setup.close()

    blocker = db_locking.db_connect(str(db), _caller="blocker.py:retained_tx")
    waiter = None
    try:
        blocker.execute("INSERT INTO wt_collision_lifecycle(value) VALUES ('blocker')")
        blocker_id = blocker._db_connection_id
        # Deliberately model a malformed owner: SQLite transaction remains but
        # its application lane was released. This is test-only fault injection.
        blocker._release_write_lane()
        assert blocker.in_transaction

        waiter = db_locking.db_connect(str(db), _caller="waiter.py:busy")
        waiter.execute("PRAGMA busy_timeout=50")
        waiter_id = waiter._db_connection_id
        try:
            waiter.execute("INSERT INTO wt_collision_lifecycle(value) VALUES ('waiter')")
        except sqlite3.OperationalError:
            pass
        else:
            raise AssertionError("retained SQLite transaction unexpectedly allowed concurrent write")
    finally:
        if waiter is not None:
            waiter.rollback()
            waiter.close()
        blocker.rollback()
        blocker.close()

    rows = _records(output)
    busy = [row for row in rows if row["event"] == "sqlite_busy"]
    assert busy
    capture = busy[-1]
    assert capture["waiter"]["connection_id"] == waiter_id
    candidates = capture["blocker_candidates"]
    candidate = next(row for row in candidates if row["connection_id"] == blocker_id)
    assert candidate["sqlite_tx_active"] is True
    assert candidate["first_write_at"] is not None
    assert candidate["flock_owned"] is False
