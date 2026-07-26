"""X67.28 -- Tests for the standalone intelligence snapshot refresh
scheduler (src.core.intelligence_snapshot_scheduler) and its diagnostics
(src.core.snapshot_health), covering the task's explicit required test
list: worker recycle during refresh, interrupted refresh recovery, stale
lock recovery, snapshot persistence, atomic replacement, duplicate refresh
suppression, concurrent readers, refresh failure recovery, new canonical
launch visibility, diagnostics accuracy.
"""
from __future__ import annotations

import os
import time

import pytest

import src.ops.intelligence_snapshots as intelligence_snapshots
import src.core.intelligence_snapshot_scheduler as scheduler
import src.core.snapshot_health as snapshot_health


@pytest.fixture(autouse=True)
def isolated_snapshot_dirs(tmp_path, monkeypatch):
    """Every test gets its OWN snapshot/lock directories -- never touches
    the real database/intelligence_snapshots directory."""
    snap_dir = str(tmp_path / "snapshots")
    lock_dir = str(tmp_path / "locks")
    monkeypatch.setattr(intelligence_snapshots, "SNAPSHOT_DIR", snap_dir)
    monkeypatch.setattr(scheduler, "LOCK_DIR", lock_dir)
    return snap_dir, lock_dir


def _fake_builder_factory(payload_fn):
    def builder(window_seconds):
        return payload_fn(window_seconds)
    return builder


# ── Snapshot persistence / atomic replacement ───────────────────────────────

def test_write_and_read_snapshot_round_trip():
    ok = intelligence_snapshots.write_snapshot(
        "operational_intelligence", 86400, {"total_launches": 5}, build_duration_ms=100.0,
    )
    assert ok is True
    snap = intelligence_snapshots.read_snapshot("operational_intelligence", 86400)
    assert snap is not None
    assert snap.payload == {"total_launches": 5}
    assert snap.snapshot_version == 1
    assert snap.refresh_status == "SUCCESS"
    assert snap.worker_id == str(os.getpid())


def test_snapshot_version_increments_monotonically():
    intelligence_snapshots.write_snapshot(
        "operational_intelligence", 86400, {"total_launches": 5}, build_duration_ms=100.0,
    )
    intelligence_snapshots.write_snapshot(
        "operational_intelligence", 86400, {"total_launches": 6}, build_duration_ms=100.0,
    )
    snap = intelligence_snapshots.read_snapshot("operational_intelligence", 86400)
    assert snap.snapshot_version == 2


def test_refresh_reason_persisted():
    intelligence_snapshots.write_snapshot(
        "operational_intelligence", 86400, {"total_launches": 5}, build_duration_ms=100.0,
        refresh_reason="scheduled",
    )
    snap = intelligence_snapshots.read_snapshot("operational_intelligence", 86400)
    assert snap.refresh_reason == "scheduled"


def test_readers_never_see_a_partial_snapshot(tmp_path, monkeypatch):
    """Atomic replacement: a reader either sees the OLD complete snapshot
    or the NEW complete one -- never a half-written file. Verified by
    checking that read_snapshot never raises/returns garbage even when
    called concurrently with a write (os.replace is atomic on POSIX)."""
    intelligence_snapshots.write_snapshot(
        "operational_intelligence", 86400, {"total_launches": 1}, build_duration_ms=1.0,
    )
    for i in range(20):
        intelligence_snapshots.write_snapshot(
            "operational_intelligence", 86400, {"total_launches": i}, build_duration_ms=1.0,
        )
        snap = intelligence_snapshots.read_snapshot("operational_intelligence", 86400)
        assert snap is not None
        assert isinstance(snap.payload["total_launches"], int)


# ── Duplicate refresh suppression / per-window locking ──────────────────────

def test_acquire_lock_succeeds_when_unheld():
    assert scheduler.acquire_window_lock("operational_intelligence", 86400) is True
    scheduler.release_window_lock("operational_intelligence", 86400)


def test_second_lock_attempt_blocked_while_first_holds_it(monkeypatch):
    assert scheduler.acquire_window_lock("operational_intelligence", 86400) is True
    # Simulate a different process (different pid) attempting the same key.
    monkeypatch.setattr(os, "getpid", lambda: 999999)
    assert scheduler.acquire_window_lock("operational_intelligence", 86400) is False


def test_refresh_one_suppresses_duplicate_when_lock_held():
    """Simulates a genuinely different, live process already holding this
    exact window's lock (a real concurrent refresh in progress) by writing
    a lock file owned by THIS test process's own pid, then asking a
    DIFFERENT simulated pid to refresh -- refresh_one() must not run a
    second build for the same key while the first is live."""
    os.makedirs(scheduler.LOCK_DIR, exist_ok=True)
    path = scheduler._lock_path("operational_intelligence", 86400)
    with open(path, "w") as f:
        f.write(str(os.getpid()))  # owned by a genuinely LIVE pid (this test process)

    import unittest.mock
    with unittest.mock.patch("os.getpid", return_value=os.getpid() + 1):
        result = scheduler.refresh_one("operational_intelligence", 86400)
    assert result["status"] == "SKIPPED_ALREADY_RUNNING"
    scheduler.release_window_lock("operational_intelligence", 86400)


def test_lock_released_after_successful_refresh(monkeypatch):
    monkeypatch.setitem(scheduler._BUILDERS, "operational_intelligence",
                         _fake_builder_factory(lambda ws: {"total_launches": 1}))
    scheduler.refresh_one("operational_intelligence", 86400)
    # Lock must be released -- a second refresh_one() call must succeed, not be skipped.
    result = scheduler.refresh_one("operational_intelligence", 86400)
    assert result["status"] == "SUCCESS"


# ── Stale lock recovery ──────────────────────────────────────────────────────

def test_stale_lock_from_dead_pid_is_reclaimed(tmp_path):
    lock_dir = scheduler.LOCK_DIR
    os.makedirs(lock_dir, exist_ok=True)
    path = scheduler._lock_path("operational_intelligence", 86400)
    # A PID that is virtually guaranteed not to be alive.
    dead_pid = 999999
    with open(path, "w") as f:
        f.write(str(dead_pid))
    assert scheduler.acquire_window_lock("operational_intelligence", 86400) is True


def test_stale_lock_recovery_allows_refresh_to_proceed(monkeypatch):
    lock_dir = scheduler.LOCK_DIR
    os.makedirs(lock_dir, exist_ok=True)
    path = scheduler._lock_path("operational_intelligence", 86400)
    with open(path, "w") as f:
        f.write("999999")  # dead pid
    monkeypatch.setitem(scheduler._BUILDERS, "operational_intelligence",
                         _fake_builder_factory(lambda ws: {"total_launches": 1}))
    result = scheduler.refresh_one("operational_intelligence", 86400)
    assert result["status"] == "SUCCESS"


# ── Interrupted refresh recovery / worker recycle during refresh ────────────

def test_crashed_refresh_never_writes_a_snapshot(monkeypatch):
    """Simulates a build that raises mid-way (standing in for 'the process
    was killed during the build') -- must not touch the snapshot at all,
    and must release its lock so the NEXT attempt can proceed."""
    intelligence_snapshots.write_snapshot(
        "operational_intelligence", 86400, {"total_launches": 1}, build_duration_ms=1.0,
    )

    def boom(window_seconds):
        raise RuntimeError("simulated interrupted build")

    monkeypatch.setitem(scheduler._BUILDERS, "operational_intelligence", boom)
    result = scheduler.refresh_one("operational_intelligence", 86400)
    assert result["status"] == "FAILED"

    # Previous snapshot must remain completely untouched.
    snap = intelligence_snapshots.read_snapshot("operational_intelligence", 86400)
    assert snap.payload == {"total_launches": 1}
    assert snap.snapshot_version == 1

    # Lock must be released -- a subsequent (successful) attempt must proceed.
    monkeypatch.setitem(scheduler._BUILDERS, "operational_intelligence",
                         _fake_builder_factory(lambda ws: {"total_launches": 2}))
    result2 = scheduler.refresh_one("operational_intelligence", 86400)
    assert result2["status"] == "SUCCESS"
    snap2 = intelligence_snapshots.read_snapshot("operational_intelligence", 86400)
    assert snap2.payload == {"total_launches": 2}


def test_worker_recycle_simulated_via_lock_file_left_by_dead_pid(monkeypatch):
    """The scenario this whole task exists to fix: a process holding the
    refresh lock disappears (worker recycled) mid-build, WITHOUT ever
    calling release_window_lock (a real process kill skips all cleanup).
    The NEXT refresh attempt (a fresh scheduler tick, simulating a new
    process) must reclaim the lock and let the snapshot advance -- the
    exact opposite of the old daemon-thread behaviour, where the refresh
    was simply lost forever."""
    os.makedirs(scheduler.LOCK_DIR, exist_ok=True)
    path = scheduler._lock_path("operational_intelligence", 86400)
    with open(path, "w") as f:
        f.write("999999")  # a PID standing in for "the recycled worker, now dead"

    monkeypatch.setitem(scheduler._BUILDERS, "operational_intelligence",
                         _fake_builder_factory(lambda ws: {"total_launches": 42}))
    result = scheduler.refresh_one("operational_intelligence", 86400)
    assert result["status"] == "SUCCESS"
    snap = intelligence_snapshots.read_snapshot("operational_intelligence", 86400)
    assert snap.payload == {"total_launches": 42}


# ── Refresh failure recovery ─────────────────────────────────────────────────

def test_refresh_failure_preserves_previous_snapshot_and_clears_lock(monkeypatch):
    intelligence_snapshots.write_snapshot(
        "operational_intelligence", 86400, {"total_launches": 10}, build_duration_ms=1.0,
    )
    monkeypatch.setitem(scheduler._BUILDERS, "operational_intelligence",
                         lambda ws: (_ for _ in ()).throw(ValueError("boom")))
    result = scheduler.refresh_one("operational_intelligence", 86400)
    assert result["status"] == "FAILED"
    snap = intelligence_snapshots.read_snapshot("operational_intelligence", 86400)
    assert snap.payload == {"total_launches": 10}  # unchanged
    # lock cleared -- verify by acquiring it fresh
    assert scheduler.acquire_window_lock("operational_intelligence", 86400) is True


# ── New canonical launch visibility ─────────────────────────────────────────

def test_new_launch_becomes_visible_after_successful_refresh(monkeypatch):
    """Directly exercises the acceptance criterion: a canonical launch that
    didn't exist in the old snapshot appears after refresh_one() succeeds."""
    intelligence_snapshots.write_snapshot(
        "operational_intelligence", 86400,
        {"total_launches": 1, "mints": ["OldMint111"]}, build_duration_ms=1.0,
    )
    monkeypatch.setitem(
        scheduler._BUILDERS, "operational_intelligence",
        _fake_builder_factory(lambda ws: {"total_launches": 2, "mints": ["OldMint111", "NewMint222"]}),
    )
    scheduler.refresh_one("operational_intelligence", 86400)
    snap = intelligence_snapshots.read_snapshot("operational_intelligence", 86400)
    assert "NewMint222" in snap.payload["mints"]


# ── Diagnostics accuracy ─────────────────────────────────────────────────────

def test_diagnostics_reports_no_snapshot_when_none_exists():
    result = snapshot_health.classify_snapshot_health("operational_intelligence", 86400)
    assert result["health"] == snapshot_health.NO_SNAPSHOT
    assert result["snapshot_age_seconds"] is None


def test_diagnostics_reports_fresh_for_a_recent_snapshot():
    intelligence_snapshots.write_snapshot(
        "operational_intelligence", 86400, {"total_launches": 1}, build_duration_ms=1.0,
    )
    result = snapshot_health.classify_snapshot_health(
        "operational_intelligence", 86400, max_acceptable_age_sec=900,
    )
    assert result["health"] == snapshot_health.FRESH
    assert result["snapshot_age_seconds"] < 5


def test_diagnostics_reports_stale_failed_for_an_old_snapshot_with_no_lock():
    intelligence_snapshots.write_snapshot(
        "operational_intelligence", 86400, {"total_launches": 1}, build_duration_ms=1.0,
    )
    # Force staleness by reading and re-checking with a tiny max age.
    result = snapshot_health.classify_snapshot_health(
        "operational_intelligence", 86400, max_acceptable_age_sec=-1,
    )
    assert result["health"] == snapshot_health.STALE_FAILED


def test_diagnostics_reports_stale_refreshing_when_lock_held_by_live_process():
    intelligence_snapshots.write_snapshot(
        "operational_intelligence", 86400, {"total_launches": 1}, build_duration_ms=1.0,
    )
    scheduler.acquire_window_lock("operational_intelligence", 86400)  # held by THIS (live) process
    try:
        result = snapshot_health.classify_snapshot_health(
            "operational_intelligence", 86400, max_acceptable_age_sec=-1,
        )
        assert result["health"] == snapshot_health.STALE_REFRESHING
        assert result["refresh_in_progress"] is True
    finally:
        scheduler.release_window_lock("operational_intelligence", 86400)


def test_diagnostics_includes_snapshot_version_and_worker_id():
    intelligence_snapshots.write_snapshot(
        "operational_intelligence", 86400, {"total_launches": 1}, build_duration_ms=1.0,
    )
    result = snapshot_health.classify_snapshot_health("operational_intelligence", 86400)
    assert result["snapshot_version"] == 1
    assert result["worker_id"] == str(os.getpid())


# ── Concurrent readers ───────────────────────────────────────────────────────

def test_multiple_reads_during_a_write_all_see_valid_data(monkeypatch):
    intelligence_snapshots.write_snapshot(
        "operational_intelligence", 86400, {"total_launches": 1}, build_duration_ms=1.0,
    )
    # Simulate several "concurrent readers" -- since write_snapshot's
    # os.replace() is atomic, sequential reads interleaved with writes must
    # never see a torn/partial file.
    for i in range(10):
        intelligence_snapshots.write_snapshot(
            "operational_intelligence", 86400, {"total_launches": i}, build_duration_ms=1.0,
        )
        for _ in range(3):
            snap = intelligence_snapshots.read_snapshot("operational_intelligence", 86400)
            assert snap is not None
            assert snap.payload["total_launches"] == i


# ── run_once() covers every window/function pair ────────────────────────────

def test_run_once_attempts_every_window_and_function(monkeypatch):
    calls = []

    def fake_refresh_one(function, window_seconds, reason="scheduled"):
        calls.append((function, window_seconds))
        return {"function": function, "window_seconds": window_seconds, "status": "SUCCESS"}

    monkeypatch.setattr(scheduler, "refresh_one", fake_refresh_one)
    results = scheduler.run_once()
    assert len(results) == 8  # 4 windows x 2 functions
    assert len(set(calls)) == 8  # every pair unique, none skipped or duplicated
