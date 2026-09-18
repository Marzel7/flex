"""Regression gate for the operations-DB writer boundary."""
from __future__ import annotations

import sqlite3
import threading
import time

from src.utils import db_locking


def test_ops_db_path_is_intercepted_by_tracked_connection(tmp_path, monkeypatch):
    path = tmp_path / "wt_ops_v2.db"
    monkeypatch.setattr(db_locking, "_OPS_DB_ABS", str(path.resolve()))
    conn = db_locking._patched_connect(str(path), timeout=1)
    try:
        assert isinstance(conn, db_locking.TrackedConnection)
        conn.execute("CREATE TABLE writer_gate (id INTEGER PRIMARY KEY)")
        conn.commit()
    finally:
        conn.close()


def test_known_ops_bypasses_are_migrated_or_explicitly_named():
    walkback = open("src/core/walkback_worker.py").read()
    scheduler = open("src/core/operation_scheduler.py").read()
    operator_routes = open("src/ops/operator_routes.py").read()
    assert "sqlite3.connect(OPS_DB_PATH)" not in walkback
    assert "_sqlite3_connect_orig(OPS_DB_PATH" not in walkback
    assert "emergency_ops_recovery_connect(OPS_DB_PATH" in walkback
    assert "_sq_sched.connect(OPS_DB_PATH" not in scheduler
    assert "conn = db_connect(OPS_DB_PATH, timeout=30)" in scheduler
    canonical_start = operator_routes.index("def _canonical_membership_connection")
    canonical_end = operator_routes.index("\n\n@operator_bp.route", canonical_start)
    canonical = operator_routes[canonical_start:canonical_end]
    assert "sqlite3.connect(" not in canonical
    assert "conn=db_connect(path, timeout=1)" in canonical


def test_emergency_exemption_is_path_bound(tmp_path):
    try:
        db_locking.emergency_ops_recovery_connect(str(tmp_path / "other.db"))
    except ValueError as exc:
        assert str(exc) == "EMERGENCY_OPS_RECOVERY_PATH_MISMATCH"
    else:  # pragma: no cover
        raise AssertionError("unexpected emergency writer escape")


def test_competing_ops_writers_share_serializer_without_sqlite_busy(tmp_path, monkeypatch):
    path = tmp_path / "wt_ops_v2.db"
    monkeypatch.setattr(db_locking, "_OPS_DB_ABS", str(path.resolve()))
    setup = db_locking._patched_connect(str(path), timeout=2)
    setup.execute("CREATE TABLE claims (id INTEGER PRIMARY KEY, worker TEXT)")
    setup.commit(); setup.close()
    failures: list[Exception] = []

    def writer(worker: str, delay: float) -> None:
        try:
            # Preparation is deliberately outside the writer connection.
            time.sleep(delay)
            conn = db_locking._patched_connect(str(path), timeout=2)
            try:
                conn.execute("INSERT INTO claims(worker) VALUES(?)", (worker,))
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:  # pragma: no cover - asserted below
            failures.append(exc)

    a = threading.Thread(target=writer, args=("walkback", 0.01))
    b = threading.Thread(target=writer, args=("scheduler", 0.01))
    a.start(); b.start(); a.join(); b.join()
    assert failures == []
    reader = sqlite3.connect(path)
    try:
        assert reader.execute("SELECT COUNT(*) FROM claims").fetchone()[0] == 2
    finally:
        reader.close()
