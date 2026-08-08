"""X78.11 -- rpc_metrics_recorder.py permanent write-lease poisoning.

Root cause, established during X78.10 live production validation and
reproduced twice on creator_resolution_worker: _try_claim_reset_day (and
two sibling functions with the identical lifecycle shape,
_ensure_rpc_metrics_table and _set_state) open a connection via bare
sqlite3.connect(), which -- in production -- is transparently routed
through db_locking.py's global sqlite3.connect monkeypatch into
TrackedConnection, so it silently acquires the cross-process write lane
despite looking like a plain unmanaged connection. conn.close() (the only
path that triggers TrackedConnection._release_write_lane(), which in turn
clears database_write_service._thread_write_lease.owner) was called ONLY
in the success path. If anything between acquiring the connection and that
conn.close() call raised -- in production, a CrossProcessDatabaseWriteTimeout
from conn.commit() under contention -- execution jumped straight to a bare
`except Exception:` that never closed the connection. This left
_thread_write_lease.owner permanently set on whichever thread called the
function, so every SUBSEQUENT write attempt on that exact thread
self-collided forever as NestedDatabaseWriteError, regardless of the
platform's actual lock availability.

These tests prove the actual _thread_write_lease.owner state after the
forced failure and after a subsequent same-thread write -- not merely that
conn.close() was called -- per the explicit requirement that the
regression validate real lease state, not implementation detail.

Test mechanics note: the global sqlite3.connect monkeypatch
(db_locking._patched_connect) only intercepts the one real, configured flex
DB path -- it does not intercept arbitrary tmp_path test databases. So
these tests instead call db_locking.db_connect() directly (which builds a
REAL TrackedConnection for any path) and monkeypatch rpc_metrics_recorder's
`sqlite3.connect` to return that real TrackedConnection with only its
commit() wrapped to raise -- exercising the actual
_acquire_write_lane/_release_write_lane machinery end to end, not a mock
standing in for it.
"""
import sqlite3
import threading

import pytest

import src.metrics.rpc_metrics_recorder as rmr
from src.core.database_write_service import (
    CrossProcessDatabaseWriteTimeout,
    _thread_write_lease,
)
from src.utils import db_locking


def _make_db(path: str) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute("""
            CREATE TABLE rpc_metrics_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE rpc_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                section TEXT NOT NULL,
                provider TEXT NOT NULL,
                method TEXT NOT NULL
            )
        """)


@pytest.fixture(autouse=True)
def _clean_thread_lease_state():
    """Every test starts and ends with a clean thread-local lease guard --
    this file is specifically about proving that state, so it must never
    leak between tests or contaminate other test files run in the same
    process/thread."""
    if hasattr(_thread_write_lease, "owner"):
        del _thread_write_lease.owner
    yield
    if hasattr(_thread_write_lease, "owner"):
        del _thread_write_lease.owner


def _do_a_tracked_write(db_path: str, label: str) -> None:
    """A minimal, unrelated tracked write on the CURRENT thread -- this is
    the same-thread continuation check: after a poisoning event, does the
    next ordinary write on this exact OS thread succeed or self-collide?"""
    conn = db_locking.db_connect(db_path)
    conn._db_path = db_path
    conn._db_caller = label
    try:
        conn.execute("INSERT INTO rpc_metrics_state(key, value) VALUES(?, ?)", (label, "1"))
        conn.commit()
    finally:
        conn.close()


def _make_forced_timeout_connect(rollback_also_fails: bool = False):
    """Returns a sqlite3.connect-shaped callable that hands back a REAL
    TrackedConnection (via db_locking.db_connect) whose commit() is wrapped
    to raise CrossProcessDatabaseWriteTimeout AFTER the connection's write-
    lane acquisition has already genuinely happened -- mirroring the exact
    production sequence: TrackedConnection.execute() on a write statement
    acquires _thread_write_lease.owner for real, then the failure
    (simulated here in commit(), matching where it happens in production)
    occurs after acquisition already succeeded."""

    def _connect(path, timeout=30):
        conn = db_locking.db_connect(path, timeout=timeout)
        conn._db_path = path
        conn._db_caller = "test-forced-timeout"

        def _failing_commit():
            raise CrossProcessDatabaseWriteTimeout(
                database="tracked", lock_path="/fake/path", waiting_pid=1,
                waiting_thread=threading.current_thread().name,
                command="forced-test-timeout", wait_seconds=60.0,
                current_owner={"command": "other-writer", "process_pid": 999},
            )

        conn.commit = _failing_commit

        if rollback_also_fails:
            def _failing_rollback():
                raise sqlite3.OperationalError("simulated rollback failure")
            conn.rollback = _failing_rollback

        return conn

    return _connect


# ── Phase 3: pre-fix reproduction (also serves as the permanent regression) ──

def test_try_claim_reset_day_does_not_poison_thread_after_commit_timeout(tmp_path, monkeypatch):
    """The primary reproduction. Forces the connection _try_claim_reset_day
    opens to raise CrossProcessDatabaseWriteTimeout from commit(), then
    verifies:
      1. _thread_write_lease.owner is NOT left set after the call.
      2. A subsequent ordinary tracked write on the SAME thread succeeds.
    """
    db_path = str(tmp_path / "flex.db")
    _make_db(db_path)
    monkeypatch.setattr(rmr, "DB_PATH", db_path)
    monkeypatch.setattr(rmr.sqlite3, "connect", _make_forced_timeout_connect())

    result = rmr._try_claim_reset_day("2026-08-08")

    assert result is False, "on failure, _try_claim_reset_day must report no claim, not raise"
    assert not hasattr(_thread_write_lease, "owner"), (
        "_thread_write_lease.owner was left set after a failed claim attempt -- "
        "this IS the production poisoning bug"
    )

    # Same-thread continuation: prove a real subsequent write succeeds.
    monkeypatch.undo()
    _do_a_tracked_write(db_path, "post-timeout-write")  # must not raise
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT value FROM rpc_metrics_state WHERE key='post-timeout-write'"
        ).fetchone()
    assert row is not None and row[0] == "1"


def test_ensure_rpc_metrics_table_does_not_poison_thread_after_commit_timeout(tmp_path, monkeypatch):
    """Sibling defect #1: _ensure_rpc_metrics_table has the identical shape."""
    db_path = str(tmp_path / "flex.db")
    with sqlite3.connect(db_path):
        pass  # start from a bare file; the function creates its own tables
    monkeypatch.setattr(rmr, "DB_PATH", db_path)
    monkeypatch.setattr(rmr, "_rpc_metrics_schema_ready", lambda: False)
    monkeypatch.setattr(rmr.sqlite3, "connect", _make_forced_timeout_connect())

    rmr._ensure_rpc_metrics_table()  # must not raise (existing contract: prints a warning)

    assert not hasattr(_thread_write_lease, "owner"), (
        "_ensure_rpc_metrics_table left _thread_write_lease.owner set after a failed table-ensure"
    )

    monkeypatch.undo()
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS rpc_metrics_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
    _do_a_tracked_write(db_path, "post-ensure-timeout-write")


def test_set_state_does_not_poison_thread_after_commit_timeout(tmp_path, monkeypatch):
    """Sibling defect #2: _set_state has the identical shape."""
    db_path = str(tmp_path / "flex.db")
    _make_db(db_path)
    monkeypatch.setattr(rmr, "DB_PATH", db_path)
    monkeypatch.setattr(rmr.sqlite3, "connect", _make_forced_timeout_connect())

    rmr._set_state("some_key", "some_value")  # must not raise (existing contract: prints, returns None)

    assert not hasattr(_thread_write_lease, "owner"), (
        "_set_state left _thread_write_lease.owner set after a failed state write"
    )

    monkeypatch.undo()
    _do_a_tracked_write(db_path, "post-set-state-timeout-write")


# ── Phase 13: rollback-failure regression ───────────────────────────────────

def test_rollback_failure_does_not_prevent_lease_release(tmp_path, monkeypatch):
    """commit() raises; rollback() ALSO raises. close() must still execute
    and the lease must still clear -- rollback failure must never prevent
    cleanup."""
    db_path = str(tmp_path / "flex.db")
    _make_db(db_path)
    monkeypatch.setattr(rmr, "DB_PATH", db_path)
    monkeypatch.setattr(rmr.sqlite3, "connect", _make_forced_timeout_connect(rollback_also_fails=True))

    result = rmr._try_claim_reset_day("2026-08-08")
    assert result is False

    assert not hasattr(_thread_write_lease, "owner"), (
        "a rollback failure must not prevent the lease from being released"
    )

    monkeypatch.undo()
    _do_a_tracked_write(db_path, "post-rollback-failure-write")


# ── Phase 16: repeated stress ───────────────────────────────────────────────

def test_repeated_timeout_and_success_cycles_never_leak_lease(tmp_path, monkeypatch):
    """A substantial deterministic sequence mixing successes and forced
    timeouts. Requires zero stale thread-local owners and zero permanent
    poisoning across the whole run -- this is the stress-test equivalent of
    the 1,342-cycle production failure, compressed to run fast in CI."""
    db_path = str(tmp_path / "flex.db")
    _make_db(db_path)
    monkeypatch.setattr(rmr, "DB_PATH", db_path)

    real_connect = rmr.sqlite3.connect
    forced_timeout_connect = _make_forced_timeout_connect()
    call_count = {"n": 0}

    def sometimes_failing_connect(path, timeout=30):
        call_count["n"] += 1
        if call_count["n"] % 3 == 0:
            return forced_timeout_connect(path, timeout=timeout)
        return real_connect(path, timeout=timeout)

    monkeypatch.setattr(rmr.sqlite3, "connect", sometimes_failing_connect)

    iterations = 200
    for i in range(iterations):
        rmr._try_claim_reset_day(f"day-{i}")
        # After EVERY attempt (success or forced failure), the lease must
        # be clear -- this is the invariant, checked every single cycle,
        # not just at the end.
        assert not hasattr(_thread_write_lease, "owner"), (
            f"lease left set after iteration {i} (call_count={call_count['n']})"
        )

    # Final same-thread continuation proof.
    monkeypatch.undo()
    _do_a_tracked_write(db_path, "post-stress-write")
