"""X77.5: three ensure_schema() functions reachable from walkback_worker's
own startup/hot path called database_write_service.execute_script(conn, DDL)
without ever calling conn.commit(). execute_script() is DDL-only and never
commits by design (the caller owns the transaction boundary) -- these three
callers never fulfilled that contract, so even a FULLY SUCCESSFUL call left
TrackedConnection's write lease held (acquired on the first CREATE/ALTER
inside the script, released only by commit()/rollback()/close()).

Found live: walkback_worker crash-looped twice in 70 seconds
(NestedDatabaseWriteError, outer_command==inner_command==walkback_worker.py:482)
because run_loop() calls attribution_outcome.ensure_schema() on its startup
connection immediately after _ensure_walkback_schema, before ever reaching
the main loop -- the leaked lease from a fully-successful ensure_schema()
call self-nested against the very next write on that thread.

provisioning_edges.ensure_schema and watchtower_alignment.ensure_schema
share the exact same bug shape and are also reachable from walkback_worker's
hot path (_capture_provisioning_facts every FULL_WALKBACK cycle; the
promotion/reconciliation path respectively) -- fixed identically.
"""
from __future__ import annotations

import os
import tempfile

import pytest

import src.utils.db_locking as db_locking
from src.core.database_write_service import _thread_write_lease
from src.ops import attribution_outcome, provisioning_edges, watchtower_alignment


@pytest.fixture(autouse=True)
def _write_serialize_enabled(monkeypatch):
    monkeypatch.setenv("DB_WRITE_SERIALIZE", "1")
    monkeypatch.setattr(db_locking, "_DB_WRITE_SERIALIZE", True)
    yield


@pytest.fixture
def tmp_db():
    path = tempfile.mktemp(suffix=".db")
    yield path
    for suffix in ("", "-wal", "-shm", ".write.lock", ".write.lock.owner"):
        try:
            os.remove(path + suffix)
        except FileNotFoundError:
            pass


@pytest.mark.parametrize("module", [attribution_outcome, provisioning_edges, watchtower_alignment])
def test_ensure_schema_releases_lease_on_success(module, tmp_db):
    """The core fix: a fully-successful ensure_schema() call must not leave
    the write lease held -- proven directly against the real TrackedConnection
    lease mechanism, not a mock."""
    conn = db_locking.db_connect(tmp_db, timeout=5)
    module.ensure_schema(conn)
    assert getattr(_thread_write_lease, "owner", None) is None, (
        f"{module.__name__}.ensure_schema() left the write lease held after a "
        f"fully successful call")
    conn.close()


@pytest.mark.parametrize("module", [attribution_outcome, provisioning_edges, watchtower_alignment])
def test_ensure_schema_then_real_write_succeeds(module, tmp_db):
    """The actual failure mode this bug caused: a write immediately AFTER
    ensure_schema(), on the SAME connection, must succeed -- this is exactly
    what walkback_worker.run_loop() does (ensure_schema calls, then
    _write_heartbeat, all on the same startup connection)."""
    conn = db_locking.db_connect(tmp_db, timeout=5)
    module.ensure_schema(conn)
    # A real write immediately after, matching run_loop()'s own shape.
    conn.execute(
        "CREATE TABLE IF NOT EXISTS wt_worker_heartbeat "
        "(worker TEXT PRIMARY KEY, last_seen INTEGER)")
    conn.execute(
        "INSERT OR REPLACE INTO wt_worker_heartbeat VALUES ('walkback_worker', ?)", (1,))
    conn.commit()  # must not raise NestedDatabaseWriteError
    conn.close()


@pytest.mark.parametrize("module", [attribution_outcome, provisioning_edges, watchtower_alignment])
def test_ensure_schema_idempotent_across_restarts(module, tmp_db):
    """Restart simulation: ensure_schema() called on 5 successive fresh
    connections against the same on-disk DB (matching walkback_worker's own
    run_loop() startup happening again after a supervisor restart) must
    never leak across restarts."""
    for i in range(5):
        conn = db_locking.db_connect(tmp_db, timeout=5)
        module.ensure_schema(conn)
        assert getattr(_thread_write_lease, "owner", None) is None, (
            f"{module.__name__}.ensure_schema() leaked the lease on restart {i}")
        conn.close()
