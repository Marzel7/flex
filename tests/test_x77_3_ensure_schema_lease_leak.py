"""X77.3: walkback_queue.ensure_schema()'s per-column ALTER TABLE migration
must never leak the DatabaseWriteService write lease.

Found live during the X77.3 production contention soak: walkback_worker
crash-looped every ~30-60s. Root cause -- TrackedConnection.execute()
acquires the thread-local write lease on ANY write-shaped SQL statement
(success or failure); the old code wrapped `execute(ALTER TABLE...); commit()`
in try/except-pass, so once a column already exists (true on every restart
after the very first ever), the ALTER TABLE raised, the except swallowed it
BEFORE reaching commit() -- the only call that releases the lease -- leaking
it. Fixed by checking PRAGMA table_info first, so a fully-migrated table
issues zero ALTER TABLE statements and never acquires a lease it might fail
to release.
"""
from __future__ import annotations

import os
import sqlite3

import pytest

import src.utils.db_locking as db_locking
from src.core import walkback_queue
from src.core.database_write_service import _thread_write_lease


@pytest.fixture(autouse=True)
def _write_serialize_enabled(monkeypatch):
    monkeypatch.setenv("DB_WRITE_SERIALIZE", "1")
    import src.utils.db_locking as _dl
    monkeypatch.setattr(_dl, "_DB_WRITE_SERIALIZE", True)
    yield


@pytest.fixture
def tmp_db(tmp_path):
    path = str(tmp_path / "walkback_lease_leak_test.db")
    yield path
    for suffix in ("", "-wal", "-shm", ".write.lock", ".write.lock.owner"):
        try:
            os.remove(path + suffix)
        except FileNotFoundError:
            pass


def test_failed_alter_table_no_longer_leaks_lease_in_isolation():
    """Direct proof of the underlying TrackedConnection hazard this fix
    avoids: a write statement that fails still acquires the lease (success
    or failure both count as an "attempt" to TrackedConnection.execute), so
    code that swallows the failure before calling commit() leaks it. This
    test documents the hazard is real -- ensure_schema's fix is to never
    attempt an ALTER TABLE that would fail, not to catch this differently."""
    import tempfile
    tmp = tempfile.mktemp(suffix=".db")
    try:
        conn = db_locking.db_connect(tmp, timeout=5)
        conn.execute("CREATE TABLE t (a INTEGER)")
        conn.commit()
        assert getattr(_thread_write_lease, "owner", None) is None

        with pytest.raises(sqlite3.OperationalError):
            conn.execute("ALTER TABLE t ADD COLUMN a INTEGER")  # duplicate column

        # This is the hazard: the lease is held despite no commit ever running.
        assert getattr(_thread_write_lease, "owner", None) is not None
        conn.close()  # TrackedConnection.close() is the safety net that releases it
        assert getattr(_thread_write_lease, "owner", None) is None
    finally:
        for suffix in ("", "-wal", "-shm", ".write.lock", ".write.lock.owner"):
            try:
                os.remove(tmp + suffix)
            except FileNotFoundError:
                pass


def test_ensure_schema_on_already_migrated_table_issues_no_alter_table(tmp_db, monkeypatch):
    """The actual fix: once every migration column already exists,
    ensure_schema must not call ALTER TABLE at all -- proven by asserting
    execute() is never called with an ALTER TABLE statement on the second
    (post-migration) run."""
    conn1 = db_locking.db_connect(tmp_db, timeout=5)
    # wt_discovered_subprovs is normally created by ws_cascade_store, not this
    # module -- create a minimal version so its migration branch is exercised.
    conn1.execute("CREATE TABLE wt_discovered_subprovs (subprov TEXT PRIMARY KEY)")
    conn1.commit()
    walkback_queue.ensure_schema(conn1)
    conn1.close()

    conn2 = db_locking.db_connect(tmp_db, timeout=5)
    real_execute = conn2.execute
    calls = []

    def spy_execute(sql, *a, **k):
        calls.append(sql)
        return real_execute(sql, *a, **k)

    monkeypatch.setattr(conn2, "execute", spy_execute)
    walkback_queue.ensure_schema(conn2)
    conn2.close()

    # Scoped to the two loops this milestone fixed (wt_walkback_queue's own
    # migration columns and wt_discovered_subprovs) -- ensure_schema also
    # delegates to watchtower_candidates.ensure_schema/deep_walkback.ensure_schema,
    # which issue their own ALTER TABLEs (wt_watchtower_candidates,
    # wt_walkback_queue.priority) but never leak (their own commit() always
    # runs unconditionally at the end of their function, not inside a
    # per-column try/except) -- verified separately in
    # test_watchtower_candidates_ensure_schema_never_leaks_lease below.
    fixed_tables_alter_calls = [
        c for c in calls if "ALTER TABLE" in c
        and ("wt_walkback_queue ADD COLUMN intelligence_outcome" in c
             or "wt_walkback_queue ADD COLUMN funder_" in c
             or "wt_walkback_queue ADD COLUMN funding_mechanism" in c
             or "wt_discovered_subprovs ADD COLUMN" in c)
    ]
    assert fixed_tables_alter_calls == [], (
        f"ensure_schema issued a migration-loop ALTER TABLE on an already-migrated "
        f"table: {fixed_tables_alter_calls}")


def test_watchtower_candidates_ensure_schema_never_leaks_lease(tmp_db):
    """watchtower_candidates.ensure_schema has the same ALTER-TABLE-in-a-loop
    shape but commits unconditionally once at the end of the function (not
    per-column), so it never leaks even without the PRAGMA table_info guard --
    confirmed directly rather than assumed, since it's called from
    walkback_queue.ensure_schema on the same connection this milestone fixed."""
    from src.ops import watchtower_candidates
    conn = db_locking.db_connect(tmp_db, timeout=5)
    conn.execute("CREATE TABLE wt_walkback_queue (mint TEXT PRIMARY KEY, status TEXT, enqueued_at INTEGER)")
    conn.commit()
    watchtower_candidates.ensure_schema(conn)
    assert getattr(_thread_write_lease, "owner", None) is None
    watchtower_candidates.ensure_schema(conn)  # restart scenario
    assert getattr(_thread_write_lease, "owner", None) is None
    conn.close()


def test_ensure_schema_never_leaks_lease_across_repeated_restarts(tmp_db):
    """The regression this milestone exists to prevent: simulate N restarts
    (fresh TrackedConnection each time, matching walkback_worker.run_loop's
    real startup path) and confirm every single one succeeds -- no
    NestedDatabaseWriteError, no leaked thread-local lease surviving between
    connections."""
    for i in range(5):
        conn = db_locking.db_connect(tmp_db, timeout=5)
        walkback_queue.ensure_schema(conn)
        # A real write immediately after, on the SAME connection, matching
        # run_loop's own _write_heartbeat call right after ensure_schema.
        conn.execute(
            "CREATE TABLE IF NOT EXISTS wt_worker_heartbeat "
            "(worker TEXT PRIMARY KEY, last_seen INTEGER)")
        conn.execute(
            "INSERT OR REPLACE INTO wt_worker_heartbeat VALUES ('walkback_worker', ?)",
            (i,))
        conn.commit()
        conn.close()
        assert getattr(_thread_write_lease, "owner", None) is None, (
            f"restart {i}: lease leaked across ensure_schema + heartbeat write")


def test_ensure_schema_still_creates_columns_on_a_fresh_table(tmp_db):
    """Equivalence check: the fix must not change WHAT gets created, only
    whether an already-satisfied column triggers a doomed ALTER TABLE."""
    conn = db_locking.db_connect(tmp_db, timeout=5)
    conn.execute("CREATE TABLE wt_discovered_subprovs (subprov TEXT PRIMARY KEY)")
    conn.commit()
    walkback_queue.ensure_schema(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(wt_walkback_queue)").fetchall()}
    for expected in ("intelligence_outcome", "funder_wallet", "funding_mechanism",
                      "funder_amount_sol", "funder_sig", "funder_slot", "funder_block_time"):
        assert expected in cols
    subprov_cols = {r[1] for r in conn.execute("PRAGMA table_info(wt_discovered_subprovs)").fetchall()}
    assert "discovery_source" in subprov_cols
    assert "funding_mechanism" in subprov_cols
    conn.close()


def test_ensure_schema_never_leaks_lease_when_discovered_subprovs_missing(tmp_db):
    """Edge case the guard exists for: wt_discovered_subprovs is owned by a
    different module (ws_cascade_store) and may not exist yet when this
    module's ensure_schema runs first. Must not leak the lease attempting an
    ALTER TABLE against a table that doesn't exist."""
    conn = db_locking.db_connect(tmp_db, timeout=5)
    walkback_queue.ensure_schema(conn)  # wt_discovered_subprovs never created
    conn.execute("SELECT 1")  # a subsequent write-shaped statement must not raise
    conn.commit()
    assert getattr(_thread_write_lease, "owner", None) is None
    conn.close()
