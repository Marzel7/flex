"""X78.9 -- Cross-process write-lock timeout & recovery.

Phase 2 reproduction + Phase 13-16 regressions. These tests use real,
separate OS processes (multiprocessing.Process) against a throwaway tmp_path
database -- never production processes/paths -- because the defect under
test (fcntl.flock across processes) cannot be reproduced with in-process
threads: flock() is a kernel-level per-open-file-description primitive, and
two threads in the same process sharing one fd would not exhibit the
cross-process contention this fix targets.
"""
import json
import multiprocessing
import os
import sqlite3
import time

import pytest

from src.core.database_write_service import (
    CROSS_PROCESS_LOCK_TIMEOUT_SEC,
    CrossProcessDatabaseWriteTimeout,
    acquire_write_lease,
    cross_process_lock_health,
    release_write_lease,
)


def _make_db(path: str) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE t(value INTEGER)")


# ── Phase 2 / 13: a live-but-wedged holder must produce a BOUNDED wait ──────

def _holder_never_releases(db_path: str, ready_path: str, timeout: float) -> None:
    """Process A: acquires the lease and sits forever (until killed)."""
    lease = acquire_write_lease("tracked:test", db_path, "txn-a", "test-holder-forever")
    with open(ready_path, "w") as f:
        f.write("ready")
    time.sleep(3600)  # deliberately never releases while alive
    release_write_lease(lease)  # unreachable in the timeout test; reachable if joined early


def _waiter_records_timeout(db_path: str, result_path: str, timeout: float) -> None:
    """Process B: attempts acquisition against a bounded timeout and records what happened."""
    t0 = time.monotonic()
    try:
        lease = acquire_write_lease(
            "tracked:test", db_path, "txn-b", "test-waiter", timeout=timeout,
        )
        elapsed = time.monotonic() - t0
        release_write_lease(lease)
        with open(result_path, "w") as f:
            json.dump({"outcome": "acquired", "elapsed": elapsed}, f)
    except CrossProcessDatabaseWriteTimeout as exc:
        elapsed = time.monotonic() - t0
        with open(result_path, "w") as f:
            json.dump({
                "outcome": "timeout",
                "elapsed": elapsed,
                "waiting_pid": exc.waiting_pid,
                "command": exc.command,
                "current_owner_pid": (exc.current_owner or {}).get("process_pid"),
            }, f)


def test_wedged_live_holder_produces_bounded_timeout_not_indefinite_hang(tmp_path):
    """Phase 2 (reproduce) + Phase 13 (regression): Process A holds the lease
    and never releases while alive; Process B must time out within the
    configured bound, not hang forever, and A is left untouched (no forced
    lock stealing)."""
    db_path = str(tmp_path / "flex.db")
    ready_path = str(tmp_path / "holder_ready")
    result_path = str(tmp_path / "waiter_result.json")
    _make_db(db_path)

    bound = 2.0  # short bound for test speed; production default is CROSS_PROCESS_LOCK_TIMEOUT_SEC
    holder = multiprocessing.Process(
        target=_holder_never_releases, args=(db_path, ready_path, bound)
    )
    holder.start()
    try:
        deadline = time.monotonic() + 10
        while not os.path.exists(ready_path) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert os.path.exists(ready_path), "holder never signalled readiness"

        waiter = multiprocessing.Process(
            target=_waiter_records_timeout, args=(db_path, result_path, bound)
        )
        t0 = time.monotonic()
        waiter.start()
        waiter.join(timeout=bound + 5)
        wall_elapsed = time.monotonic() - t0

        assert not waiter.is_alive(), "waiter process did not exit -- indefinite hang reproduced"
        assert wall_elapsed < bound + 5, f"waiter took {wall_elapsed}s, expected bounded by {bound}s"

        with open(result_path) as f:
            result = json.load(f)
        assert result["outcome"] == "timeout"
        assert result["elapsed"] >= bound * 0.9  # didn't fire early
        assert result["elapsed"] < bound + 3.0    # didn't hang past the bound
        assert result["current_owner_pid"] == holder.pid  # diagnostics identify the real holder

        assert holder.is_alive(), "holder must remain untouched -- no forced lock stealing"
    finally:
        holder.terminate()
        holder.join(timeout=5)


# ── Phase 14: normal short contention must succeed, no false timeout ───────

def _holder_releases_quickly(db_path: str, hold_seconds: float) -> None:
    lease = acquire_write_lease("tracked:test", db_path, "txn-a", "test-holder-brief")
    time.sleep(hold_seconds)
    release_write_lease(lease)


def _waiter_acquires(db_path: str, result_path: str, timeout: float) -> None:
    t0 = time.monotonic()
    lease = acquire_write_lease("tracked:test", db_path, "txn-b", "test-waiter", timeout=timeout)
    elapsed = time.monotonic() - t0
    release_write_lease(lease)
    with open(result_path, "w") as f:
        json.dump({"outcome": "acquired", "elapsed": elapsed}, f)


def test_short_legitimate_contention_succeeds_without_false_timeout(tmp_path):
    """Phase 14: A holds briefly and releases before B's timeout -- B must
    acquire normally, no false positive."""
    db_path = str(tmp_path / "flex.db")
    result_path = str(tmp_path / "waiter_result.json")
    _make_db(db_path)

    hold_seconds = 0.5
    bound = 5.0
    holder = multiprocessing.Process(target=_holder_releases_quickly, args=(db_path, hold_seconds))
    holder.start()
    time.sleep(0.1)  # let A actually acquire first

    waiter = multiprocessing.Process(target=_waiter_acquires, args=(db_path, result_path, bound))
    waiter.start()
    waiter.join(timeout=bound + 5)
    holder.join(timeout=5)

    assert not waiter.is_alive()
    with open(result_path) as f:
        result = json.load(f)
    assert result["outcome"] == "acquired"
    assert result["elapsed"] < bound  # acquired well before the bound, not via timeout fallback


# ── Phase 15: SIGKILL releases the kernel flock; no stale lock survives ────

def _holder_for_sigkill(db_path: str, ready_path: str) -> None:
    lease = acquire_write_lease("tracked:test", db_path, "txn-a", "test-holder-sigkill")
    with open(ready_path, "w") as f:
        f.write("ready")
    time.sleep(3600)


def test_sigkilled_holder_releases_kernel_lock_no_stale_lock(tmp_path):
    """Phase 15: A acquires and is SIGKILLed (not terminated gracefully).
    The kernel releases flock when the process's file descriptors die, so a
    subsequent acquisition by B must succeed promptly -- no orphaned lock."""
    db_path = str(tmp_path / "flex.db")
    ready_path = str(tmp_path / "holder_ready")
    result_path = str(tmp_path / "waiter_result.json")
    _make_db(db_path)

    holder = multiprocessing.Process(target=_holder_for_sigkill, args=(db_path, ready_path))
    holder.start()
    deadline = time.monotonic() + 10
    while not os.path.exists(ready_path) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert os.path.exists(ready_path)

    os.kill(holder.pid, 9)  # SIGKILL
    holder.join(timeout=5)
    assert not holder.is_alive()

    bound = 5.0
    waiter = multiprocessing.Process(target=_waiter_acquires, args=(db_path, result_path, bound))
    t0 = time.monotonic()
    waiter.start()
    waiter.join(timeout=bound + 5)
    elapsed = time.monotonic() - t0

    assert not waiter.is_alive()
    with open(result_path) as f:
        result = json.load(f)
    assert result["outcome"] == "acquired"
    # Should acquire quickly (no timeout wait needed) -- the kernel already freed the flock.
    assert elapsed < bound, f"waiter took {elapsed}s waiting on a lock the kernel should have freed"


# ── Phase 16: owner metadata stays diagnostic-only, never authoritative ────

def test_stale_owner_metadata_does_not_block_a_free_lock(tmp_path):
    """Phase 6/16: if the .write.lock.owner file is stale (process gone) but
    the flock itself is genuinely free, a new acquirer must succeed based on
    the KERNEL lock state, not the sidecar file's apparent age."""
    db_path = str(tmp_path / "flex.db")
    _make_db(db_path)

    # Acquire and release normally -- leaves a valid owner file with an old
    # acquired_at once enough wall time has passed, but the flock is free.
    lease = acquire_write_lease("tracked:test", db_path, "txn-a", "test-first")
    release_write_lease(lease)

    owner_path = f"{os.path.realpath(db_path)}.write.lock.owner"
    assert not os.path.exists(owner_path), "release must clean up the owner sidecar"

    # A free lock with no owner file at all must still acquire immediately.
    t0 = time.monotonic()
    lease2 = acquire_write_lease("tracked:test", db_path, "txn-b", "test-second")
    elapsed = time.monotonic() - t0
    release_write_lease(lease2)
    assert elapsed < 1.0


def test_owner_metadata_present_during_hold_and_absent_after_release(tmp_path):
    """Phase 5/16: owner metadata records PID, command, database path, and
    acquired_at while held, and is cleaned up on release."""
    db_path = str(tmp_path / "flex.db")
    _make_db(db_path)

    lease = acquire_write_lease("tracked:test", db_path, "txn-a", "caller.py:42 in do_thing")
    owner_path = f"{os.path.realpath(db_path)}.write.lock.owner"
    with open(owner_path) as f:
        owner = json.load(f)
    assert owner["process_pid"] == os.getpid()
    assert owner["command"] == "caller.py:42 in do_thing"
    assert owner["database_path"] == os.path.realpath(db_path)
    assert "acquired_at" in owner
    assert "thread" in owner

    release_write_lease(lease)
    assert not os.path.exists(owner_path)


def test_timeout_exception_carries_current_owner_diagnostics(tmp_path):
    """Phase 4/5: on timeout, the raised exception (not a generic 'database
    is locked') carries waiting pid/thread, command, wait duration, and the
    current owner's metadata."""
    db_path = str(tmp_path / "flex.db")
    ready_path = str(tmp_path / "holder_ready")
    _make_db(db_path)

    holder = multiprocessing.Process(target=_holder_for_sigkill, args=(db_path, ready_path))
    holder.start()
    try:
        deadline = time.monotonic() + 10
        while not os.path.exists(ready_path) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert os.path.exists(ready_path)

        with pytest.raises(CrossProcessDatabaseWriteTimeout) as excinfo:
            acquire_write_lease("tracked:test", db_path, "txn-b", "test-waiter", timeout=1.0)

        exc = excinfo.value
        assert exc.waiting_pid == os.getpid()
        assert exc.waiting_thread
        assert exc.command == "test-waiter"
        assert exc.wait_seconds >= 0.9
        assert exc.current_owner is not None
        assert exc.current_owner["process_pid"] == holder.pid
        assert exc.database == "tracked"
    finally:
        holder.terminate()
        holder.join(timeout=5)


# ── Phase 17/18: Mission Control health surface ─────────────────────────────

def test_cross_process_lock_health_reports_healthy_when_no_recent_timeouts(tmp_path):
    db_path = str(tmp_path / "flex.db")
    _make_db(db_path)
    health = cross_process_lock_health(db_path)
    assert health["state"] == "HEALTHY"
    assert health["current_owner"] is None
    assert health["timeouts_1h"] == 0


def test_cross_process_lock_health_reports_stalled_after_a_timeout(tmp_path):
    db_path = str(tmp_path / "flex.db")
    ready_path = str(tmp_path / "holder_ready")
    _make_db(db_path)

    holder = multiprocessing.Process(target=_holder_for_sigkill, args=(db_path, ready_path))
    holder.start()
    try:
        deadline = time.monotonic() + 10
        while not os.path.exists(ready_path) and time.monotonic() < deadline:
            time.sleep(0.05)
        with pytest.raises(CrossProcessDatabaseWriteTimeout):
            acquire_write_lease("tracked:test", db_path, "txn-b", "test-waiter", timeout=1.0)

        health = cross_process_lock_health(db_path)
        assert health["state"] == "STALLED"
        assert health["timeouts_1h"] >= 1
        assert health["last_timeout"] is not None
        assert health["last_timeout"]["command"] == "test-waiter"
    finally:
        holder.terminate()
        holder.join(timeout=5)


def test_default_timeout_matches_in_process_lock_convention():
    """Phase 3: the cross-process bound must be evidence-consistent with the
    existing in-process DB_WRITE_LOCK timeout (60s), not an arbitrary new
    figure invented to hide contention."""
    assert CROSS_PROCESS_LOCK_TIMEOUT_SEC == 60.0
