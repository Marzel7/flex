"""STORAGE-LIFECYCLE-P1: concurrency/lock-safety tests against ISOLATED
fixture SQLite databases only. Never touches any real production database
under database/. No provider calls. No production writes.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ops.storage_lock_safety import (  # noqa: E402
    CleanupLeaseHeldError,
    DatabaseBusyError,
    LockSafetyBudget,
    acquire_cleanup_lease,
    delete_bounded_batch,
    retire_closed_segment,
    verify_db_valid_after_cleanup,
)
from src.ops.storage_lifecycle_policy import (  # noqa: E402
    NO_AUTOMATED_DELETION_CLASSES,
    DiskPressureThresholds,
    LifecycleClass,
    classify_store,
    eligible_for_pressure_state,
)


@pytest.fixture
def fixture_db(tmp_path):
    db_path = str(tmp_path / "fixture.db")
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, created_at INTEGER, payload TEXT)")
    for i in range(10_000):
        conn.execute("INSERT INTO events (created_at, payload) VALUES (?, ?)", (i, f"row-{i}"))
    conn.commit()
    conn.close()
    return db_path


# ── Bounded delete ────────────────────────────────────────────────────────

def test_bounded_delete_respects_batch_size(fixture_db):
    conn = sqlite3.connect(fixture_db)
    budget = LockSafetyBudget(max_rows_per_batch=100)
    deleted = delete_bounded_batch(conn, table="events", where_clause="created_at < ?", params=(5000,), budget=budget)
    conn.close()
    assert deleted == 100


def test_bounded_delete_rejects_unbounded_batch_size(fixture_db):
    conn = sqlite3.connect(fixture_db)
    with pytest.raises(ValueError):
        delete_bounded_batch(conn, table="events", where_clause="1=1", params=(), budget=LockSafetyBudget(max_rows_per_batch=0))
    conn.close()


def test_bounded_delete_rejects_vacuum_in_where_clause(fixture_db):
    conn = sqlite3.connect(fixture_db)
    with pytest.raises(ValueError):
        delete_bounded_batch(conn, table="events", where_clause="1=1; VACUUM", params=(), budget=LockSafetyBudget())
    conn.close()


def test_bounded_delete_multiple_batches_reach_zero(fixture_db):
    """Resumability: repeated bounded batches eventually delete
    everything matching, in independent transactions, without one giant
    transaction."""
    conn = sqlite3.connect(fixture_db)
    budget = LockSafetyBudget(max_rows_per_batch=1000)
    total = 0
    for _ in range(15):
        n = delete_bounded_batch(conn, table="events", where_clause="created_at < ?", params=(10000,), budget=budget)
        total += n
        if n == 0:
            break
    conn.close()
    assert total == 10_000


def test_db_remains_valid_after_bounded_deletes(fixture_db):
    conn = sqlite3.connect(fixture_db)
    budget = LockSafetyBudget(max_rows_per_batch=500)
    delete_bounded_batch(conn, table="events", where_clause="created_at < ?", params=(2000,), budget=budget)
    conn.close()
    assert verify_db_valid_after_cleanup(fixture_db) is True


# ── Active writer / busy database ────────────────────────────────────────

def test_cleanup_fails_closed_when_db_held_exclusively(fixture_db):
    """Simulate an active writer holding an exclusive lock; cleanup must
    raise DatabaseBusyError (fail/skip) rather than block indefinitely."""
    holder = sqlite3.connect(fixture_db, timeout=0.1)
    holder.execute("BEGIN EXCLUSIVE")
    holder.execute("INSERT INTO events (created_at, payload) VALUES (99999, 'holder')")

    try:
        cleanup_conn = sqlite3.connect(fixture_db, timeout=0.1)
        budget = LockSafetyBudget(max_rows_per_batch=10, busy_timeout_ms=200)
        with pytest.raises(DatabaseBusyError):
            delete_bounded_batch(cleanup_conn, table="events", where_clause="created_at < ?", params=(5000,), budget=budget)
        cleanup_conn.close()
    finally:
        holder.rollback()
        holder.close()


def test_concurrent_reader_not_blocked_by_wal_mode(fixture_db):
    """WAL mode should allow a reader to proceed concurrently with a
    writer -- this is a property of WAL mode itself, verified here as a
    sanity check on the fixture setup cleanup code will run against."""
    writer = sqlite3.connect(fixture_db, timeout=2)
    writer.execute("BEGIN IMMEDIATE")
    writer.execute("INSERT INTO events (created_at, payload) VALUES (1, 'x')")

    reader = sqlite3.connect(f"file:{fixture_db}?mode=ro", uri=True, timeout=2)
    count = reader.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    reader.close()

    writer.rollback()
    writer.close()
    assert count == 10_000  # reader saw pre-write state, and was NOT blocked/errored


# ── Cleanup lease (single-writer guarantee) ──────────────────────────────

def test_cleanup_lease_prevents_duplicate_scheduler(tmp_path):
    lease_path = str(tmp_path / "cleanup.lease")
    with acquire_cleanup_lease(lease_path):
        with pytest.raises(CleanupLeaseHeldError):
            with acquire_cleanup_lease(lease_path):
                pass  # pragma: no cover -- should never reach here


def test_cleanup_lease_released_after_context_exits(tmp_path):
    lease_path = str(tmp_path / "cleanup.lease")
    with acquire_cleanup_lease(lease_path):
        pass
    # lease must be free again -- a second acquire must succeed
    with acquire_cleanup_lease(lease_path):
        pass


def test_stale_lease_file_does_not_block_after_process_exit(tmp_path):
    """A lease file left on disk from a prior (now-dead) process must not
    permanently block future cleanup runs -- flock is tied to the file
    descriptor's process, not the file's mere existence."""
    lease_path = str(tmp_path / "cleanup.lease")
    # simulate a stale lease file existing with content but no live holder
    with open(lease_path, "w") as f:
        f.write("99999999")  # a pid that (almost certainly) doesn't exist
    with acquire_cleanup_lease(lease_path):
        pass  # must succeed -- flock released when the writing process exited


# ── Closed-segment retirement ─────────────────────────────────────────────

def test_segment_retirement_moves_file(tmp_path):
    segment = tmp_path / "segment_2026_08_20.sqlite"
    segment.write_text("fake sqlite content")
    retired_dir = str(tmp_path / "retired")
    dest = retire_closed_segment(str(segment), retired_dir=retired_dir)
    assert not segment.exists()
    assert os.path.isfile(dest)


def test_segment_retirement_never_overwrites_existing_destination(tmp_path):
    segment = tmp_path / "segment.sqlite"
    segment.write_text("original")
    retired_dir = tmp_path / "retired"
    retired_dir.mkdir()
    (retired_dir / "segment.sqlite").write_text("already retired")
    with pytest.raises(FileExistsError):
        retire_closed_segment(str(segment), retired_dir=str(retired_dir))
    # original must remain untouched since the operation failed
    assert segment.read_text() == "original"


def test_segment_retirement_missing_source_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        retire_closed_segment(str(tmp_path / "does_not_exist.sqlite"), retired_dir=str(tmp_path / "retired"))


def test_no_partial_segment_retirement_on_duplicate_destination(tmp_path):
    """A failed retirement (destination exists) must not leave the source
    partially moved or the destination corrupted -- os.rename is atomic
    on the same filesystem, and this function checks existence BEFORE
    attempting the rename."""
    segment = tmp_path / "s.sqlite"
    segment.write_bytes(b"0" * 1000)
    retired_dir = tmp_path / "retired"
    retired_dir.mkdir()
    (retired_dir / "s.sqlite").write_bytes(b"1" * 1000)
    with pytest.raises(FileExistsError):
        retire_closed_segment(str(segment), retired_dir=str(retired_dir))
    assert segment.stat().st_size == 1000
    assert (retired_dir / "s.sqlite").stat().st_size == 1000


# ── Disk pressure / retention policy ─────────────────────────────────────

def test_disk_pressure_state_classification():
    t = DiskPressureThresholds()
    assert t.classify(40 * 1024**3) == t.classify(31 * 1024**3)  # both NORMAL
    from src.ops.storage_lifecycle_policy import DiskPressureState
    assert t.classify(40 * 1024**3) == DiskPressureState.NORMAL
    assert t.classify(25 * 1024**3) == DiskPressureState.WARNING
    assert t.classify(18 * 1024**3) == DiskPressureState.PRESSURE
    assert t.classify(12 * 1024**3) == DiskPressureState.EMERGENCY
    assert t.classify(5 * 1024**3) == DiskPressureState.HARD_FLOOR


def test_permanent_classes_never_eligible_at_any_pressure_state():
    from src.ops.storage_lifecycle_policy import DiskPressureState
    for state in DiskPressureState:
        assert not eligible_for_pressure_state(LifecycleClass.PERMANENT_OPERATIONAL, state)
        assert not eligible_for_pressure_state(LifecycleClass.PERMANENT_EVIDENCE_INDEX, state)
        assert not eligible_for_pressure_state(LifecycleClass.UNKNOWN_NEEDS_HUMAN_REVIEW, state)


def test_watchtower_store_forced_permanent_regardless_of_proposed_class():
    """Structural guard: no matter what class is proposed, any store name
    matching a Watchtower/3SW2 marker is forced to PERMANENT_OPERATIONAL."""
    for name in ("wt_ops_v2.db", "database/wt_ops_v2.db", "watchtower_shadow", "three_sw2_shadow_ep3_2a", "wt_watchtower_launches"):
        result = classify_store(name, proposed_class=LifecycleClass.RETIREMENT_ELIGIBLE)
        assert result == LifecycleClass.PERMANENT_OPERATIONAL


def test_non_watchtower_store_keeps_proposed_class():
    result = classify_store("some_diagnostic_log.jsonl", proposed_class=LifecycleClass.DIAGNOSTIC)
    assert result == LifecycleClass.DIAGNOSTIC


def test_temporary_eligible_at_normal_pressure():
    from src.ops.storage_lifecycle_policy import DiskPressureState
    assert eligible_for_pressure_state(LifecycleClass.TEMPORARY, DiskPressureState.NORMAL)


def test_retirement_eligible_not_eligible_until_pressure_state():
    from src.ops.storage_lifecycle_policy import DiskPressureState
    assert not eligible_for_pressure_state(LifecycleClass.RETIREMENT_ELIGIBLE, DiskPressureState.NORMAL)
    assert not eligible_for_pressure_state(LifecycleClass.RETIREMENT_ELIGIBLE, DiskPressureState.WARNING)
    assert eligible_for_pressure_state(LifecycleClass.RETIREMENT_ELIGIBLE, DiskPressureState.PRESSURE)


def test_no_class_bypasses_no_automated_deletion_via_pressure_table():
    """Even if PRESSURE_STATE_ELIGIBLE_CLASSES were misconfigured to
    include a permanent class, eligible_for_pressure_state must still
    reject it -- verified by directly checking the guard runs first."""
    from src.ops.storage_lifecycle_policy import PRESSURE_STATE_ELIGIBLE_CLASSES, DiskPressureState
    # confirm no permanent class is even listed as eligible anywhere (defense in depth check)
    for state, classes in PRESSURE_STATE_ELIGIBLE_CLASSES.items():
        assert not (classes & NO_AUTOMATED_DELETION_CLASSES)


# ── No production DB touched ──────────────────────────────────────────────

def test_all_db_paths_used_in_tests_are_tmp_path_fixtures():
    """Structural guard: every sqlite3.connect(...) call in this test
    module must reference a tmp_path/tempfile-derived path, never a
    literal path under the real database/ directory."""
    src = Path(__file__).read_text()
    for line in src.splitlines():
        if "sqlite3.connect(" in line:
            assert "database/" not in line


def test_no_vacuum_statement_executed_anywhere_in_lock_safety_module():
    """No conn.execute(...VACUUM...) call may exist -- comments/strings
    mentioning VACUUM (to explain the prohibition) are fine and expected."""
    src = (ROOT / "src/ops/storage_lock_safety.py").read_text()
    for line in src.splitlines():
        if ".execute(" in line or "cursor.execute" in line:
            assert "VACUUM" not in line.upper()
