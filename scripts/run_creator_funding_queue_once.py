#!/usr/bin/env python3
"""
One-shot creator funding queue worker.

Runs outside the listener. Claims 1 pending job, processes it, marks
complete/retry/failed. Safe to run as a cron job every 10 minutes.

Never touches pumpfun_curve_listener.py.
Never acquires TrackedConnection or _DB_WRITE_LOCK.
Uses raw sqlite3.connect with WAL + short timeout for all queue operations.

Usage:
    python3 scripts/run_creator_funding_queue_once.py [--jobs N]

Cron (1 job per 10 min):
    */10 * * * * cd /path/to/flex && python3 scripts/run_creator_funding_queue_once.py >> logs/creator_funding_worker.log 2>&1
"""

import argparse
import asyncio
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone

# ── path setup ───────────────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)

DB_PATH = os.environ.get(
    "DB_PATH",
    os.path.join(_ROOT, "database", "flex_complete_database.db"),
)

LOCK_SECONDS = 300          # how long a claimed job is locked before stale reap
MAX_ATTEMPTS = 5            # permanent failure threshold
JOB_TIMEOUT_SECONDS = 120  # asyncio.wait_for timeout per job


# ── raw queue helpers (no TrackedConnection, no _DB_WRITE_LOCK) ───────────────

def _raw_conn():
    conn = sqlite3.connect(DB_PATH, timeout=2)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=2000")
    conn.row_factory = sqlite3.Row
    return conn


def _claim_one(now: int):
    """
    Claim a single pending job atomically.
    Returns the sqlite3.Row or None if nothing ready.
    Uses raw SQLite — no TrackedConnection, no _DB_WRITE_LOCK.
    """
    conn = _raw_conn()
    try:
        row = conn.execute(
            """
            SELECT creator_address, mint, migration_timestamp, create_tx_signature,
                   attempts, COALESCE(job_priority, 0) AS job_priority,
                   COALESCE(priority_reason, 'unknown') AS priority_reason
            FROM creator_funding_queue
            WHERE status = 'pending'
              AND COALESCE(locked_until, 0) < ?
              AND COALESCE(next_attempt_at, 0) <= ?
            ORDER BY job_priority DESC, COALESCE(next_attempt_at, 0) ASC
            LIMIT 1
            """,
            (now, now),
        ).fetchone()

        if row is None:
            return None

        conn.execute(
            """
            UPDATE creator_funding_queue
            SET status = 'running',
                locked_until = ?,
                updated_at = ?
            WHERE creator_address = ?
              AND mint = ?
              AND status = 'pending'
            """,
            (now + LOCK_SECONDS, now, row["creator_address"], row["mint"]),
        )
        if conn.execute("SELECT changes()").fetchone()[0] == 0:
            # Race: another worker claimed it first
            return None
        conn.commit()
        return row
    finally:
        conn.close()


def _mark_complete(creator: str, mint: str, attempts: int, now: int):
    conn = _raw_conn()
    try:
        conn.execute(
            """
            UPDATE creator_funding_queue
            SET status = 'complete',
                locked_until = 0,
                attempts = ?,
                last_error = NULL,
                funding_extracted_at = ?,
                updated_at = ?
            WHERE creator_address = ? AND mint = ?
            """,
            (attempts + 1, now, now, creator, mint),
        )
        conn.commit()
    finally:
        conn.close()


def _mark_retry(creator: str, mint: str, attempts: int, error: str, now: int):
    backoff = min(900, 120 * (attempts + 1))
    conn = _raw_conn()
    try:
        conn.execute(
            """
            UPDATE creator_funding_queue
            SET status = 'pending',
                locked_until = 0,
                attempts = ?,
                next_attempt_at = ?,
                last_error = ?,
                updated_at = ?
            WHERE creator_address = ? AND mint = ?
            """,
            (attempts + 1, now + backoff, error[:500], now, creator, mint),
        )
        conn.commit()
    finally:
        conn.close()


def _mark_failed(creator: str, mint: str, attempts: int, error: str, now: int):
    conn = _raw_conn()
    try:
        conn.execute(
            """
            UPDATE creator_funding_queue
            SET status = 'failed',
                locked_until = 0,
                attempts = ?,
                last_error = ?,
                updated_at = ?
            WHERE creator_address = ? AND mint = ?
            """,
            (attempts + 1, error[:500], now, creator, mint),
        )
        conn.commit()
    finally:
        conn.close()


def _queue_pending_count():
    conn = _raw_conn()
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM creator_funding_queue WHERE status = 'pending'"
        ).fetchone()[0]
    finally:
        conn.close()


def _funder_count(creator: str) -> int:
    conn = _raw_conn()
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM creator_funders WHERE creator_address = ?", (creator,)
        ).fetchone()[0]
    finally:
        conn.close()


# ── job processor ─────────────────────────────────────────────────────────────

async def _process_job(row) -> tuple[str, str]:
    """
    Call the existing extractor. Returns ('complete', '') or ('retry', err) or ('failed', err).
    This runs in its own process so sync DB calls inside the extractor are fine.
    """
    from src.extractors.realtime_creator_funding_extractor import extract_funding_for_new_token

    creator = row["creator_address"]
    mint = row["mint"]
    migration_ts = row["migration_timestamp"] or datetime.now(timezone.utc).isoformat()
    create_tx = row["create_tx_signature"]
    attempts = int(row["attempts"] or 0)

    try:
        result = await asyncio.wait_for(
            extract_funding_for_new_token(
                creator,
                migration_ts,
                create_tx,
                mint,
            ),
            timeout=JOB_TIMEOUT_SECONDS,
        )
        errored = isinstance(result, dict) and result.get("error")
        funders = _funder_count(creator)

        if errored and funders == 0 and attempts < MAX_ATTEMPTS:
            return "retry", f"extraction_error no_funders attempt={attempts+1}"

        return "complete", ""

    except asyncio.TimeoutError:
        return "retry", f"timeout after {JOB_TIMEOUT_SECONDS}s attempt={attempts+1}"
    except Exception as exc:
        if attempts + 1 >= MAX_ATTEMPTS:
            return "failed", str(exc)
        return "retry", str(exc)


# ── main ──────────────────────────────────────────────────────────────────────

async def run(max_jobs: int = 1):
    t_start = time.monotonic()
    now = int(time.time())

    claimed = 0
    completed = 0
    retried = 0
    failed = 0

    for _ in range(max_jobs):
        now = int(time.time())
        row = _claim_one(now)
        if row is None:
            break

        creator = row["creator_address"]
        mint = row["mint"]
        attempts = int(row["attempts"] or 0)
        claimed += 1

        print(
            f"[FUNDING_WORKER] claimed creator={creator[:12]} mint={mint[:16]} "
            f"attempts={attempts} priority={row['job_priority']} reason={row['priority_reason']}",
            flush=True,
        )

        job_start = time.monotonic()
        outcome, err = await _process_job(row)
        elapsed = round(time.monotonic() - job_start, 1)
        now = int(time.time())

        if outcome == "complete":
            _mark_complete(creator, mint, attempts, now)
            completed += 1
            funders = _funder_count(creator)
            print(
                f"[FUNDING_WORKER] complete creator={creator[:12]} mint={mint[:16]} "
                f"funders={funders} elapsed={elapsed}s",
                flush=True,
            )
        elif outcome == "retry":
            _mark_retry(creator, mint, attempts, err, now)
            retried += 1
            print(
                f"[FUNDING_WORKER] retry creator={creator[:12]} mint={mint[:16]} "
                f"error={err[:120]} elapsed={elapsed}s",
                flush=True,
            )
        else:
            _mark_failed(creator, mint, attempts, err, now)
            failed += 1
            print(
                f"[FUNDING_WORKER] failed creator={creator[:12]} mint={mint[:16]} "
                f"error={err[:120]} elapsed={elapsed}s",
                flush=True,
            )

    if claimed == 0:
        print("[FUNDING_WORKER] no pending jobs", flush=True)

    pending_after = _queue_pending_count()
    total_elapsed = round(time.monotonic() - t_start, 1)

    print(
        f"[FUNDING_WORKER] summary claimed={claimed} completed={completed} "
        f"retried={retried} failed={failed} "
        f"pending_after={pending_after} elapsed={total_elapsed}s",
        flush=True,
    )


def main():
    parser = argparse.ArgumentParser(description="One-shot creator funding queue worker")
    parser.add_argument("--jobs", type=int, default=1, help="Max jobs per run (default 1)")
    args = parser.parse_args()

    asyncio.run(run(max_jobs=args.jobs))


if __name__ == "__main__":
    main()
