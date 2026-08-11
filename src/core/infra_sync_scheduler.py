#!/usr/bin/env python3
"""X78.8 — Standalone Infrastructure Wallet Sync Scheduler.

Root cause this fixes: sync_infra_wallets() (src/utils/infra_mapping.py) --
three full SELECT DISTINCT scans of token_analysis (~1.4-1.6M rows each,
measured ~48s combined in isolation, substantially worse under real
concurrent DB load -- see docs/audits/x78_6_risk_scoring_runtime_reentrancy.md
and x78_8's own audit) -- was called synchronously from 15+ call sites
across the codebase, including RiskScoringBuilder.score_creator_now(),
every single time each of those builders ran. For score_creator_now
specifically (creator_funding_worker's post-extraction enrichment step),
this meant a single-creator scoring call could occasionally become
responsible for a full ecosystem-wide infrastructure rebuild -- X78.7's
300s debounce reduced how OFTEN this happened but did not change the
per-call cost when it did, and the worker's real job cadence (~15-20 min
per completed job under RPC-bound load) meant the debounce window
routinely went cold between calls anyway.

Caller census (X78.8 Part A): every one of the 15+ callers treats
sync_infra_wallets' output (the infra_wallets exclusion table) as
tolerant of "last successful state" -- none check a freshness timestamp
or require transaction-current data, and the underlying source
(token_analysis.bonding_curve_pda/pool_address/pumpswap_pool_address) is
append-only (new rows appear as tokens launch; existing values never
change or get removed), so bounded staleness only means a brand-new
infra wallet is briefly treated as a non-infra address until the next
refresh -- a self-correcting classification lag, not a correctness break.

Architecture: identical pattern to src.core.intelligence_snapshot_scheduler
(X67.28) and src.core.operation_scheduler -- a single standalone process,
supervised independently of any request/job-processing worker, refreshes
infra_wallets on its own fixed cadence via the EXISTING, unchanged
sync_infra_wallets() function. Single-flight via a PID-liveness-checked
lock file (identical mechanism to operation_scheduler.acquire_lock(), a
local copy per this codebase's convention of small independent
standalone processes with no cross-import coupling). Staleness/health is
exposed via a small infra_wallets_sync_status table (last attempt/success
timestamp, duration, status, rows processed) -- read-only for every
consumer, written only by this scheduler.

Run:
    python -m src.core.infra_sync_scheduler --loop
    python -m src.core.infra_sync_scheduler --once
    python -m src.core.infra_sync_scheduler --status
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sqlite3
import sys
import time

_REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from src.utils.infra_mapping import (  # noqa: E402
    collect_infra_wallet_rows,
    write_infra_wallet_deltas,
)

DB_PATH = os.environ.get(
    "DB_PATH", os.path.join(_REPO_ROOT, "database", "flex_complete_database.db"))

LOCK_PATH = os.environ.get(
    "INFRA_SYNC_SCHEDULER_LOCK_PATH",
    os.path.join(_REPO_ROOT, "database", "infra_sync_scheduler.lock"),
)

# X78.8 Phase 21 -- cadence derived from evidence, not carried over from
# X78.7's 300s per-score debounce. The underlying source data is
# append-only and changes only as new tokens launch (continuous but not
# bursty); every consumer already tolerates last-successful-state with no
# stated freshness requirement tighter than "eventually." 10 minutes
# balances staleness (at most ~10 min before a newly-launched bonding
# curve/pool is excluded) against load (a ~48s+ full-table operation
# running 6x/hour instead of on every scoring call, which could
# previously mean many times per minute under load).
REFRESH_INTERVAL_SEC = int(os.environ.get("INFRA_SYNC_REFRESH_INTERVAL_SEC", "600"))

# Phase 18 -- staleness classification threshold, looser than the refresh
# interval itself (a run can legitimately be in flight one interval late
# without indicating genuine failure), same relationship as
# intelligence_snapshot_scheduler's MAX_ACCEPTABLE_AGE_SEC vs
# REFRESH_INTERVAL_SEC.
MAX_ACCEPTABLE_AGE_SEC = int(os.environ.get("INFRA_SYNC_MAX_AGE_SEC", "1800"))  # 30 min

_log = logging.getLogger(__name__)
_STOP = False


def _handle_signal(signum, _frame) -> None:
    global _STOP
    _STOP = True


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def acquire_lock(path: str) -> bool:
    """Single-instance lock, stale-owner reclamation -- identical pattern
    to operation_scheduler.acquire_lock()/intelligence_snapshot_scheduler's
    acquire_window_lock(). Returns False only if a LIVE owner holds it (a
    refresh is genuinely already running); a dead owner's lock is
    reclaimed automatically."""
    if os.path.exists(path):
        try:
            with open(path) as f:
                owner = int((f.read().strip() or "0"))
        except (ValueError, OSError):
            owner = 0
        if owner and _pid_alive(owner) and owner != os.getpid():
            return False
        try:
            os.remove(path)
        except OSError:
            pass
    try:
        with open(path, "w") as f:
            f.write(str(os.getpid()))
        return True
    except OSError as exc:
        _log.warning("could not write lock file %s: %s", path, exc)
        return False


def release_lock(path: str) -> None:
    try:
        if os.path.exists(path):
            with open(path) as f:
                if (f.read().strip() or "0") == str(os.getpid()):
                    os.remove(path)
    except OSError:
        pass


def _ensure_status_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS infra_wallets_sync_status (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            last_attempt_at INTEGER,
            last_success_at INTEGER,
            last_duration_ms INTEGER,
            last_status TEXT,
            last_error TEXT,
            rows_processed INTEGER
        )
    """)


def _record_status(conn: sqlite3.Connection, *, attempt_at: int, success: bool,
                    duration_ms: int, rows_processed: int, error: str | None) -> None:
    _ensure_status_table(conn)
    conn.execute("""
        INSERT INTO infra_wallets_sync_status (id, last_attempt_at, last_success_at,
            last_duration_ms, last_status, last_error, rows_processed)
        VALUES (1, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            last_attempt_at = excluded.last_attempt_at,
            last_success_at = CASE WHEN excluded.last_status = 'success'
                                    THEN excluded.last_success_at
                                    ELSE infra_wallets_sync_status.last_success_at END,
            last_duration_ms = excluded.last_duration_ms,
            last_status = excluded.last_status,
            last_error = excluded.last_error,
            rows_processed = CASE WHEN excluded.last_status = 'success'
                                   THEN excluded.rows_processed
                                   ELSE infra_wallets_sync_status.rows_processed END
    """, (
        attempt_at,
        attempt_at if success else None,
        duration_ms,
        "success" if success else "failed",
        error,
        rows_processed,
    ))
    conn.commit()


def run_once() -> dict:
    """Perform exactly one infra_wallets refresh, split into a read phase
    (no write lease, runs on a read-only connection for the full ~2min
    scan) and a short write phase (only actual deltas, separate
    connection). Failure does NOT raise -- per Phase 17's explicit
    requirement, a failed refresh must never crash creator scoring (or any
    other consumer); it is recorded and the caller (score_creator_now etc.)
    simply continues using whatever state is already persisted from the
    last success."""
    attempt_at = int(time.time())
    t0 = time.monotonic()
    result = {"status": "error", "rows_processed": 0, "duration_ms": 0}
    scan_ms = 0
    write_ms = 0
    try:
        read_conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=30)
        try:
            t_scan0 = time.monotonic()
            rows = collect_infra_wallet_rows(read_conn)
            scan_ms = int((time.monotonic() - t_scan0) * 1000)
        finally:
            read_conn.close()

        write_conn = sqlite3.connect(DB_PATH, timeout=90)
        try:
            write_conn.row_factory = sqlite3.Row
            write_conn.execute("PRAGMA journal_mode=WAL")
            t_write0 = time.monotonic()
            rows_processed = write_infra_wallet_deltas(write_conn, rows)
            write_conn.commit()
            write_ms = int((time.monotonic() - t_write0) * 1000)
            duration_ms = int((time.monotonic() - t0) * 1000)
            _record_status(write_conn, attempt_at=attempt_at, success=True,
                            duration_ms=duration_ms, rows_processed=rows_processed, error=None)
            result = {
                "status": "success",
                "rows_processed": rows_processed,
                "duration_ms": duration_ms,
                "scan_ms": scan_ms,
                "write_ms": write_ms,
            }
            _log.info(
                "infra_wallets sync succeeded: %d rows changed (scan=%dms write=%dms total=%dms)",
                rows_processed, scan_ms, write_ms, duration_ms,
            )
        finally:
            write_conn.close()
    except Exception as exc:
        duration_ms = int((time.monotonic() - t0) * 1000)
        _log.exception("infra_wallets sync failed after %dms", duration_ms)
        try:
            status_conn = sqlite3.connect(DB_PATH, timeout=30)
            try:
                _record_status(status_conn, attempt_at=attempt_at, success=False,
                                duration_ms=duration_ms, rows_processed=0, error=str(exc))
            finally:
                status_conn.close()
        except Exception:
            _log.exception("failed to record infra_wallets sync failure status")
        result = {"status": "error", "error": str(exc), "duration_ms": duration_ms,
                  "scan_ms": scan_ms, "write_ms": write_ms}
    return result


def get_status() -> dict:
    """Read-only status check -- safe to call from any process (Mission
    Control, a health endpoint, etc.) without needing the write lease."""
    conn = None
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        row = conn.execute("""
            SELECT last_attempt_at, last_success_at, last_duration_ms,
                   last_status, last_error, rows_processed
            FROM infra_wallets_sync_status WHERE id = 1
        """).fetchone()
        if not row:
            return {"status": "never_run"}
        now = int(time.time())
        age = (now - row["last_success_at"]) if row["last_success_at"] else None
        health = "healthy"
        if row["last_status"] != "success":
            health = "failed"
        elif age is not None and age > MAX_ACCEPTABLE_AGE_SEC:
            health = "stale"
        elif row["last_success_at"] is None:
            health = "never_succeeded"
        return {
            "health": health,
            "last_attempt_at": row["last_attempt_at"],
            "last_success_at": row["last_success_at"],
            "last_duration_ms": row["last_duration_ms"],
            "last_status": row["last_status"],
            "last_error": row["last_error"],
            "rows_processed": row["rows_processed"],
            "age_seconds": age,
        }
    except Exception as exc:
        return {"status": "error", "error": str(exc)}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def run_loop() -> None:
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    if not acquire_lock(LOCK_PATH):
        _log.info("another infra_sync_scheduler instance is running; exiting.")
        return

    try:
        _log.info("infra_sync_scheduler starting (interval=%ds)", REFRESH_INTERVAL_SEC)
        while not _STOP:
            run_once()
            for _ in range(REFRESH_INTERVAL_SEC):
                if _STOP:
                    break
                time.sleep(1)
        _log.info("infra_sync_scheduler stopping (signal received).")
    finally:
        release_lock(LOCK_PATH)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="[INFRA_SYNC] %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()

    if args.status:
        print(json.dumps(get_status(), indent=2))
    elif args.once:
        print(json.dumps(run_once(), indent=2))
    elif args.loop:
        run_loop()
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
