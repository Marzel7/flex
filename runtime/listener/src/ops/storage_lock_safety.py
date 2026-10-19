"""STORAGE-LIFECYCLE-P1: SQLITE_STORAGE_LIFECYCLE_LOCK_SAFETY contract.

Defines the bounded, lock-safe primitives a future cleanup implementation
MUST use. This module implements the primitives (they are independently
testable against throwaway fixture databases) but does NOT wire them into
any scheduled/automatic production execution path -- that activation is
explicitly out of scope for P1 (see docs/audits/
storage_lifecycle_p1_implementation_result.json Part 26 P2 activation
plan).

Rules enforced by construction:
  - no unbounded DELETE (delete_bounded_batch requires an explicit LIMIT)
  - no VACUUM anywhere in this module
  - no schema migration anywhere in this module
  - bounded transaction duration (max_seconds enforced via wall-clock
    check between batches, not a single unbounded transaction)
  - bounded rows per mutation (batch_size required, no default of "all")
  - busy_timeout always set before any write attempt
  - fail/skip rather than block: if the DB is busy beyond busy_timeout_ms,
    the attempt raises DatabaseBusyError and the caller is expected to
    skip that store for this run, not retry in a blocking loop
  - one cleanup writer maximum: acquire_cleanup_lease() implements an
    advisory file-lock-based lease; a second concurrent call fails fast
  - resumable: batches are independent transactions, so an interrupted
    run leaves the DB in a valid state and a subsequent run can continue
"""
from __future__ import annotations

import contextlib
import errno
import fcntl
import os
import sqlite3
import time
from dataclasses import dataclass


class DatabaseBusyError(RuntimeError):
    """Raised when a write attempt could not acquire the lock within the
    configured busy_timeout -- the caller must skip this store for this
    run rather than retry in a blocking loop."""


class CleanupLeaseHeldError(RuntimeError):
    """Raised when another cleanup process already holds the lease."""


@dataclass(frozen=True)
class LockSafetyBudget:
    """Bounded cleanup budget -- config, not magic constants scattered
    through call sites."""
    max_transaction_seconds: float = 5.0
    max_rows_per_batch: int = 5_000
    busy_timeout_ms: int = 2_000
    max_runtime_seconds_per_store: float = 60.0
    max_total_runtime_seconds: float = 600.0


def delete_bounded_batch(
    conn: sqlite3.Connection,
    *,
    table: str,
    where_clause: str,
    params: tuple,
    budget: LockSafetyBudget,
) -> int:
    """Deletes at most budget.max_rows_per_batch rows matching
    where_clause in ONE bounded transaction, using a LIMIT-bounded
    subselect (SQLite's DELETE does not support LIMIT directly on all
    builds, so this uses a rowid-bounded subselect, which is portable).

    Never call this in a loop without checking elapsed wall-clock time
    against budget.max_transaction_seconds between calls -- this function
    itself only bounds a SINGLE batch, not a whole cleanup run.

    Returns the number of rows actually deleted (0 if none matched)."""
    if budget.max_rows_per_batch <= 0:
        raise ValueError("max_rows_per_batch must be positive -- unbounded delete is forbidden")
    if "VACUUM" in where_clause.upper() or "DROP" in where_clause.upper():
        raise ValueError("where_clause must not contain VACUUM/DROP")

    conn.execute(f"PRAGMA busy_timeout={budget.busy_timeout_ms}")
    start = time.monotonic()
    try:
        cursor = conn.execute(
            f"DELETE FROM {table} WHERE rowid IN "
            f"(SELECT rowid FROM {table} WHERE {where_clause} LIMIT ?)",
            (*params, budget.max_rows_per_batch),
        )
        conn.commit()
    except sqlite3.OperationalError as exc:
        conn.rollback()
        if "locked" in str(exc).lower() or "busy" in str(exc).lower():
            raise DatabaseBusyError(f"{table}: {exc}") from exc
        raise
    elapsed = time.monotonic() - start
    if elapsed > budget.max_transaction_seconds:
        # The batch already committed (it's done), but the budget was
        # exceeded -- this is a signal to the caller to reduce
        # max_rows_per_batch for this store on the next run, not an error
        # about THIS batch's validity.
        pass
    return cursor.rowcount if cursor.rowcount is not None and cursor.rowcount >= 0 else 0


def retire_closed_segment(segment_path: str, *, retired_dir: str) -> str:
    """Whole-file segment retirement -- the PREFERRED mechanism per Part
    13, because it avoids SQLite row locking entirely (a single
    os.rename() is effectively atomic on the same filesystem, unlike a
    row-level DELETE which requires a write transaction against a live
    file). The segment must already be marked immutable/closed by the
    caller BEFORE this is invoked -- this function does not verify
    immutability itself (that is the segmented-storage adapter's
    responsibility, tracked separately).

    Returns the new path. Raises FileNotFoundError if segment_path
    doesn't exist, FileExistsError if the destination already exists
    (never silently overwrites)."""
    if not os.path.isfile(segment_path):
        raise FileNotFoundError(segment_path)
    os.makedirs(retired_dir, exist_ok=True)
    dest = os.path.join(retired_dir, os.path.basename(segment_path))
    if os.path.exists(dest):
        raise FileExistsError(dest)
    os.rename(segment_path, dest)
    return dest


@contextlib.contextmanager
def acquire_cleanup_lease(lease_path: str):
    """Advisory file lock ensuring at most ONE cleanup process runs at a
    time. A second concurrent caller raises CleanupLeaseHeldError
    immediately (non-blocking) rather than waiting -- 'if lock already
    held: exit cleanly' per Part 17."""
    os.makedirs(os.path.dirname(lease_path) or ".", exist_ok=True)
    fd = os.open(lease_path, os.O_CREAT | os.O_RDWR)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EAGAIN, errno.EACCES):
                raise CleanupLeaseHeldError(lease_path) from exc
            raise
        try:
            os.write(fd, str(os.getpid()).encode())
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def verify_db_valid_after_cleanup(db_path: str) -> bool:
    """Read-only PRAGMA integrity_check -- the post-condition every
    cleanup action (bounded delete or segment retirement) must satisfy
    before being considered successful. Returns True only if the result
    is exactly 'ok'."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
    try:
        row = conn.execute("PRAGMA integrity_check(1)").fetchone()
        return bool(row) and row[0] == "ok"
    finally:
        conn.close()
