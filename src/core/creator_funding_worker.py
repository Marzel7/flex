#!/usr/bin/env python3
"""
Creator Funding Worker — standalone supervised daemon.

X73.2 — the canonical, sole consumer of creator_funding_queue. Drains it
independently of Gunicorn and independently of pumpfun_curve_listener.py,
mirroring the proven creator_resolution_worker.py pattern (same self-kill
guard, WAL watchdog, adaptive batching, heartbeat contract).

Replaces two prior, both-inert consumer paths:
  - pumpfun_curve_listener.py's _process_creator_funding_queue_periodic(),
    an in-listener asyncio loop gated by LISTENER_CREATOR_FUNDING_QUEUE_ENABLED
    (parked =0 since 2026-06-25 for a WS-recovery reason that has since
    resolved, but never unparked). That method's *code* is left in place
    (still reachable if the env var were ever re-enabled) but is no longer
    the intended consumer -- this worker is. The env var is deliberately
    NOT re-enabled here (X73.2 scope: do not re-enable it, do not install
    the historical cron -- both become legacy/deprecated).
  - scripts/run_creator_funding_queue_once.py, a documented but never-
    scheduled one-shot cron script. Its queue-claim primitives were the
    starting point for this worker's own claim/retry/fail logic. The
    script itself is left in place (harmless if invoked manually) but is
    no longer the intended consumer -- see the deprecation note now in its
    own docstring.

Reuses the existing attribution-relevant extraction call unchanged
(extract_funding_for_new_token from realtime_creator_funding_extractor.py)
plus the same post-extraction enrichment steps pumpfun_curve_listener.py's
retired loop performed (risk scoring, second-hop-lite auto-enqueue,
prediction rescore, live network assignment, targeted intelligence
refresh) so downstream Network Intelligence consumers resume exactly as
before -- no attribution or reconciliation logic changed anywhere in this
file.

Run:
    python -m src.core.creator_funding_worker          # continuous loop
    python -m src.core.creator_funding_worker --once    # one pass then exit
    python -m src.core.creator_funding_worker --status  # queue counts then exit
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from typing import Any, Dict

# ── config ────────────────────────────────────────────────────────────────────
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DB_PATH = os.environ.get(
    "DB_PATH",
    os.path.join(_REPO_ROOT, "database", "flex_complete_database.db"),
)

BATCH_SIZE_MAX     = int(os.environ.get("CFQ_BATCH_SIZE",           "5"))
BATCH_SIZE_IDLE    = int(os.environ.get("CFQ_BATCH_SIZE_IDLE",      "1"))
INTERVAL_SEC       = int(os.environ.get("CFQ_INTERVAL_SEC",          "3"))
INTERVAL_IDLE_SEC  = int(os.environ.get("CFQ_INTERVAL_IDLE_SEC",    "15"))
BACKLOG_THRESHOLD  = int(os.environ.get("CFQ_BACKLOG_THRESHOLD",    "10"))
LOCK_SECONDS       = int(os.environ.get("CFQ_LOCK_SECONDS",        "180"))
MAX_ATTEMPTS       = int(os.environ.get("CFQ_MAX_ATTEMPTS",          "5"))
JOB_TIMEOUT_SECONDS = int(os.environ.get("CFQ_JOB_TIMEOUT_SECONDS",  "90"))
# X78.0 -- bounded wait for a timed-out extraction task's own cancellation
# cleanup (its finally: extraction_conn.close()) to actually complete
# before the next job is claimed on the same reused event-loop thread. See
# _process_job's own comment at the wait_for/shield call site.
EXTRACTION_CANCEL_GRACE_SECONDS = int(os.environ.get("CFQ_EXTRACTION_CANCEL_GRACE_SECONDS", "10"))
INTEL_REFRESH_DEBOUNCE_SEC = int(os.environ.get("CFQ_INTEL_REFRESH_DEBOUNCE_SEC", "60"))
# X78.14 -- post-extraction enrichment (line ~858) previously had no bound
# at all, unlike the primary extraction call above it (which uses
# asyncio.wait_for(..., timeout=JOB_TIMEOUT_SECONDS)). X78.13 traced a
# creator_funding_worker stall to exactly this asymmetry: an unbounded
# to_thread call let a slow enrichment step (build_networks_release, via
# a since-removed in-line infra_wallets sync -- see build_networks_release.py's
# own X78.14 comment) block the worker's single event-loop thread
# indefinitely, with nothing to time it out. Enrichment is best-effort by
# design (every step around it is already its own try/except that logs and
# continues) -- a bounded timeout here is consistent with that intent, not
# a new constraint on it.
INTEL_REFRESH_TIMEOUT_SECONDS = int(os.environ.get("CFQ_INTEL_REFRESH_TIMEOUT_SECONDS", "30"))

# X78.16 Phase A/B -- age promotion, the queue fairness mechanism.
#
# X78.15 measured direct, live evidence of indefinite starvation: the claim
# query (_recover_stale_and_claim below) ordered strictly by
# `job_priority DESC, next_attempt_at ASC, created_at ASC` with no aging --
# a pure priority-then-FIFO order. With 15,861 ready job_priority=1 rows
# continuously replenished by new-creator arrivals (~27/hour) against only
# ~6.8/hour completions, the 1,007-row job_priority=0 population, INCLUDING
# a single row that had sat untouched for 1005.93 hours (~42 days) at the
# time of measurement, was mathematically guaranteed to never be reached --
# priority=0 rows are only considered once the priority=1 ready pool is
# fully drained to empty, which the arrival pattern never allows.
#
# Fix (age promotion, chosen over the other Phase B options):
#   - Quota scheduling (reserve N of every batch for the lowest priority
#     tier) was rejected: it would need to special-case exactly two tiers
#     today but silently misbehave the moment a third priority value is
#     introduced, and reserving capacity is wasteful when the low-priority
#     population is empty (the common case for e.g. job_priority=0, which
#     is far smaller than job_priority=1).
#   - Weighted fair selection (round-robin proportional to tier size) was
#     rejected as unnecessarily complex for a two-tier system and harder
#     to reason about/verify than a single closed-form expression.
#   - Age promotion was chosen: a row's EFFECTIVE priority increases
#     continuously with wait time, capped, so any row is mathematically
#     guaranteed to reach and exceed a fresh higher-priority row's
#     effective priority within a bounded, known interval
#     (AGE_PROMOTION_INTERVAL_SEC * priority_gap seconds of waiting) --
#     regardless of how many priority tiers exist or how they're
#     distributed. It requires only a single ORDER BY expression change
#     (see _recover_stale_and_claim), preserves the exact same claim
#     query shape/index usage, and is trivially measurable (effective
#     priority is a deterministic function of age, verifiable per-row).
#   - Priority itself is preserved, not eliminated: a job_priority=1 row
#     that arrived seconds ago is still claimed before a job_priority=0
#     row that arrived seconds ago -- age promotion only guarantees an
#     UPPER BOUND on how long a lower-priority row can be deferred by
#     continuously-arriving higher-priority work, it does not reverse
#     priority for comparably-aged rows.
#
# One promotion "point" (equal to crossing one full integer priority tier)
# per AGE_PROMOTION_INTERVAL_SEC of wait time. Default 3600s (1 hour): a
# job_priority=0 row becomes as eligible as a freshly-arrived
# job_priority=1 row after waiting 1 hour, guaranteeing the maximum
# possible starvation window for any single priority gap is bounded by
# this interval, not by however long higher-priority arrivals happen to
# keep coming.
#
# AGE_PROMOTION_CAP bounds the promotion contribution so an extremely old
# row cannot produce an unbounded/overflowing effective priority value --
# but it MUST be large relative to the actual spread of job_priority
# values in use, or the cap itself becomes a new, permanent starvation
# ceiling: a first implementation of this fix used a cap of 24 (1 day's
# worth of promotion), which live-tested against the production queue
# revealed a real bug -- 15,247 job_priority=1 rows were ALREADY older
# than 24h (i.e. already capped at effective_priority=1+24=25), which
# permanently outranked EVERY job_priority=0 row's own capped ceiling of
# 0+24=24, no matter how old the job_priority=0 row became. The cap must
# exceed the maximum plausible priority gap by a wide margin so it only
# ever bounds pathological (multi-year) ages against numeric overflow,
# never ordinary queue aging against a real priority tier -- 1000 default
# (≈41.7 days of promotion at the 1-hour interval) comfortably exceeds
# today's 0-1 priority range and any realistically-introduced future tier.
AGE_PROMOTION_INTERVAL_SEC = int(os.environ.get("CFQ_AGE_PROMOTION_INTERVAL_SEC", "3600"))
AGE_PROMOTION_CAP = int(os.environ.get("CFQ_AGE_PROMOTION_CAP", "1000"))

# Layer 2: self-kill thresholds (same contract as creator_resolution_worker.py)
MAX_OPEN_HANDLES   = int(os.environ.get("CFQ_MAX_OPEN_HANDLES",     "10"))
MAX_UPTIME_HOURS   = int(os.environ.get("CFQ_MAX_UPTIME_HOURS",      "6"))

# Layer 3: WAL watchdog thresholds
WAL_CHECK_INTERVAL = int(os.environ.get("CFQ_WAL_CHECK_INTERVAL",   "60"))
WAL_ALERT_MB       = int(os.environ.get("CFQ_WAL_ALERT_MB",         "64"))
WAL_BUSY_CYCLES    = int(os.environ.get("CFQ_WAL_BUSY_CYCLES",       "3"))

_DB_SERIALIZER_METRICS_PATH = os.path.join(_REPO_ROOT, "logs", "db_serializer_metrics.json")

WORKER_NAME = "creator-funding"
_STOP = False
_started_at = int(time.time())
# X78.14 Phase E -- health counters for the enrichment timeout backstop
# introduced alongside the build_networks_release.py lifecycle fix. A
# nonzero _intel_refresh_timeout_count with a healthy heartbeat indicates
# enrichment is occasionally deferred but the worker itself is not
# stalling because of it -- the acceptance criterion this milestone exists
# to satisfy.
_intel_refresh_timeout_count = 0
_intel_refresh_last_run = 0.0


def _log(msg: str) -> None:
    print(f"[CFQ_WORKER] {msg}", flush=True)


def _handle_signal(signum, frame):
    global _STOP
    _log(f"Signal {signum} received — shutting down after current cycle")
    _STOP = True


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT,  _handle_signal)


# ── DB wrapper ────────────────────────────────────────────────────────────────
def _db_connect(readonly: bool = False, timeout: int = 10):
    try:
        from src.utils.db_locking import db_connect
        if readonly:
            import sqlite3
            conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=timeout)
            conn.row_factory = sqlite3.Row
            return conn
        return db_connect(DB_PATH, timeout=timeout)
    except ImportError:
        import sqlite3
        conn = sqlite3.connect(DB_PATH, timeout=timeout)
        conn.row_factory = sqlite3.Row
        return conn


# X78.4 -- bounded retry for a specific, proven-transient failure mode.
# Root cause: extract_for_creator's own timeout/cancellation path
# (JOB_TIMEOUT_SECONDS / EXTRACTION_CANCEL_GRACE_SECONDS) can leave a
# cancelled extraction coroutine's underlying to_thread-dispatched write
# (e.g. _save_outgoing_transfer, which acquires a write lease internally)
# still genuinely running on its own OS thread when _process_job's
# cancellation handling gives up -- and there is PROVABLY no way to
# detect true completion from outside that synchronous function: neither
# asyncio.Task.done()/.cancelled() nor a completion signal set in the
# calling coroutine's own `finally` can be trusted, because BOTH report
# "finished" the instant CancelledError propagates through the awaiting
# `await asyncio.to_thread(...)` line, independent of whether the real
# thread has actually stopped (proven directly, twice: see
# tests/test_x78_4_cancellation_grace_period_reproduction.py and
# tests/test_x78_4_write_retry.py::test_wrapper_finally_signal_is_unreliable).
#
# NestedDatabaseWriteError itself is NOT raised by _db_connect/db_connect
# (opening a connection or running PRAGMA never acquires the write lease)
# -- it is raised by the FIRST write-shaped .execute()/.executemany()
# call on a connection, reporting the tag of whichever connection is
# still holding the lease. So the retry belongs around each write
# helper's full open-write-close body, not around connection-opening
# alone -- this wraps a zero-argument callable (typically a small
# closure over an already-open connection's write, or a full helper
# call) and retries the WHOLE thing, since a fresh attempt needs a fresh
# connection/cursor state after a failed one anyway.
#
# Given detection is provably impossible at this boundary, this
# implements the OTHER half of the safe invariant: isolation in time.
# A NestedDatabaseWriteError caught here is proven transient (the
# straggler's own DB_WRITE_LOCK.acquire has a 60s bound, and every other
# SQLite timeout in this codebase is likewise bounded, so the straggler
# MUST eventually release the lease) -- so this worker's own writes retry
# with backoff instead of racing blind (the pre-X78.4 bug).
_WRITE_RETRY_MAX_ATTEMPTS = int(os.environ.get("CFQ_WRITE_RETRY_MAX_ATTEMPTS", "8"))
_WRITE_RETRY_BASE_SECONDS = float(os.environ.get("CFQ_WRITE_RETRY_BASE_SECONDS", "0.5"))


def _retry_on_nested_write(fn, *args, **kwargs):
    """Call fn(*args, **kwargs), retrying with exponential backoff (capped
    at 30s/attempt) if it raises NestedDatabaseWriteError. Synchronous --
    callers already dispatched via asyncio.to_thread MUST continue to be
    (retrying here sleeps synchronously; doing this on the event-loop
    thread directly would block the whole loop, including the very
    extraction task this retry exists to wait out)."""
    from src.core.database_write_service import NestedDatabaseWriteError
    attempt = 0
    while True:
        try:
            return fn(*args, **kwargs)
        except NestedDatabaseWriteError as e:
            attempt += 1
            if attempt > _WRITE_RETRY_MAX_ATTEMPTS:
                raise
            wait_s = min(30.0, _WRITE_RETRY_BASE_SECONDS * (2 ** (attempt - 1)))
            _log(f"write lease busy (outer={e.outer_command}), retry {attempt}/"
                 f"{_WRITE_RETRY_MAX_ATTEMPTS} in {wait_s:.1f}s -- proven transient "
                 f"(bounded by DB_WRITE_LOCK's own 60s timeout), not a permanent stall")
            time.sleep(wait_s)


# ── Layer 2: self-kill guard (identical contract to creator_resolution_worker.py) ──
def _open_handle_count() -> int:
    pid = os.getpid()
    try:
        fd_dir = f"/proc/{pid}/fd"
        if os.path.isdir(fd_dir):
            target = os.path.abspath(DB_PATH)
            count = 0
            for fd in os.listdir(fd_dir):
                try:
                    if os.readlink(f"{fd_dir}/{fd}") == target:
                        count += 1
                except OSError:
                    pass
            return count
    except Exception:
        pass
    try:
        result = subprocess.run(["lsof", DB_PATH], capture_output=True, text=True, timeout=5)
        return sum(1 for l in result.stdout.splitlines()
                   if not l.startswith("COMMAND") and l.split()[1:2] == [str(pid)])
    except Exception:
        return 0


def _check_self_kill(pending: int) -> None:
    handles = _open_handle_count()
    if handles > MAX_OPEN_HANDLES:
        _log(f"CRITICAL_CONNECTION_LEAK: {handles} open handles to live DB "
             f"(max={MAX_OPEN_HANDLES}) — exiting for supervisord restart")
        os._exit(1)

    uptime_h = (time.time() - _started_at) / 3600
    if uptime_h >= MAX_UPTIME_HOURS and pending == 0:
        _log(f"Uptime {uptime_h:.1f}h with idle queue — clean restart to prevent slow leaks")
        os._exit(0)


# ── Layer 3: WAL watchdog thread ──────────────────────────────────────────────
def _wal_size_mb() -> float:
    wal = DB_PATH + "-wal"
    try:
        return os.path.getsize(wal) / 1_048_576
    except OSError:
        return 0.0


def _wal_busy() -> int:
    conn = None
    try:
        import sqlite3
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=3)
        r = conn.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
        return r[0] if r else -1
    except Exception:
        return -1
    finally:
        if conn:
            conn.close()


def _identify_wal_holders() -> str:
    try:
        result = subprocess.run(["lsof", DB_PATH], capture_output=True, text=True, timeout=5)
        pids = {}
        for l in result.stdout.splitlines():
            if l.startswith("COMMAND"):
                continue
            parts = l.split()
            if len(parts) >= 2:
                pids[parts[1]] = parts[0]
        return ", ".join(f"{cmd}({pid})" for pid, cmd in pids.items()) or "unknown"
    except Exception:
        return "unknown"


def _wal_is_critically_pinned(size_mb: float, busy_cycles: int) -> bool:
    """Require both a large WAL and persistent checkpoint contention."""
    return size_mb >= WAL_ALERT_MB and busy_cycles >= WAL_BUSY_CYCLES


def _wal_watchdog() -> None:
    busy_cycles = 0
    while not _STOP:
        time.sleep(WAL_CHECK_INTERVAL)
        if _STOP:
            break
        try:
            mb = _wal_size_mb()
            busy = _wal_busy()
            if busy > 0:
                busy_cycles += 1
                _log(f"WAL: {mb:.1f}MB busy={busy} (cycle {busy_cycles}/{WAL_BUSY_CYCLES})")
            else:
                busy_cycles = 0
            if _wal_is_critically_pinned(mb, busy_cycles):
                holders = _identify_wal_holders()
                _log(f"CRITICAL_WAL_PINNED: WAL={mb:.1f}MB busy_cycles={busy_cycles} "
                     f"holders={holders} — this worker exiting for clean restart")
                os._exit(1)
        except Exception as e:
            _log(f"WAL watchdog error: {e}")


# ── heartbeat ─────────────────────────────────────────────────────────────────
def _read_serializer_p99() -> float:
    try:
        with open(_DB_SERIALIZER_METRICS_PATH) as f:
            return float(json.load(f).get("p99_wait_ms", 0.0))
    except Exception:
        return 0.0


def _write_heartbeat(meta: Dict[str, Any]) -> None:
    conn = None
    try:
        conn = _db_connect(readonly=False, timeout=10)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS wt_worker_heartbeat (
                worker_name TEXT PRIMARY KEY,
                last_seen   INTEGER,
                status      TEXT,
                meta_json   TEXT
            )
        """)
        conn.execute("""
            INSERT INTO wt_worker_heartbeat (worker_name, last_seen, status, meta_json)
            VALUES (?, strftime('%s','now'), 'ok', ?)
            ON CONFLICT(worker_name) DO UPDATE SET
                last_seen = excluded.last_seen,
                status    = excluded.status,
                meta_json = excluded.meta_json
        """, (WORKER_NAME, json.dumps(meta)))
        conn.commit()
    except Exception as e:
        _log(f"heartbeat write failed: {e}")
    finally:
        if conn:
            conn.close()


# ── queue primitives (adapted from scripts/run_creator_funding_queue_once.py) ──
def _pending_count() -> int:
    conn = None
    try:
        conn = _db_connect(readonly=True, timeout=3)
        return int(conn.execute(
            "SELECT COUNT(*) FROM creator_funding_queue WHERE status IN ('pending','retry')"
        ).fetchone()[0])
    except Exception:
        return 0
    finally:
        if conn:
            conn.close()


def _funder_count(creator: str) -> int:
    conn = None
    try:
        conn = _db_connect(readonly=True, timeout=10)
        return int(conn.execute(
            "SELECT COUNT(*) FROM creator_funders WHERE creator_address=?", (creator,)
        ).fetchone()[0])
    finally:
        if conn:
            conn.close()


def _recover_stale_rows(now: int) -> tuple[int, int]:
    """Apply only crash-recovery mutations and release the write lane."""
    conn = _db_connect(readonly=False, timeout=30)
    try:
        conn.execute("PRAGMA busy_timeout=30000")
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE creator_funding_queue
            SET status = 'complete', locked_until = 0, attempts = attempts + 1,
                last_error = NULL, funding_extracted_at = COALESCE(funding_extracted_at, ?),
                updated_at = ?
            WHERE ((status = 'running' AND locked_until > 0 AND locked_until < ?) OR status = 'retry')
              AND EXISTS (SELECT 1 FROM creator_funders cf
                          WHERE cf.creator_address = creator_funding_queue.creator_address LIMIT 1)
            """,
            (now, now, now),
        )
        recovered = int(cur.rowcount or 0)
        cur.execute(
            """
            UPDATE creator_funding_queue
            SET status = 'retry', locked_until = 0,
                last_error = COALESCE(last_error, 'stale running job recovered'), updated_at = ?
            WHERE status = 'running' AND locked_until > 0 AND locked_until < ?
            """,
            (now, now),
        )
        stale = int(cur.rowcount or 0)
        conn.commit()
        return recovered, stale
    finally:
        conn.close()


def _select_ready_rows(now: int, batch: int) -> list[dict[str, Any]]:
    """Select the next queue candidates through a genuine mode=ro snapshot."""
    conn = _db_connect(readonly=True, timeout=30)
    try:
        # X78.16 Phase A/B -- age promotion. effective_priority adds one
        # promotion point per AGE_PROMOTION_INTERVAL_SEC of wait time
        # (measured from created_at, the row's true original queue-entry
        # time -- NOT next_attempt_at, so a retried row's age isn't reset
        # by its own retry scheduling), capped at AGE_PROMOTION_CAP points
        # so an extremely old row's contribution stays bounded rather than
        # growing without limit. This guarantees any single row reaches
        # and exceeds a fresh higher-priority row's effective priority
        # within AGE_PROMOTION_INTERVAL_SEC * priority_gap seconds of
        # waiting, closing the indefinite-starvation gap X78.15 measured
        # (a 1005.93-hour-old job_priority=0 row perpetually outranked by
        # a continuously-replenished job_priority=1 population) without
        # removing priority itself: two comparably-aged rows still order
        # by their raw job_priority exactly as before.
        rows = conn.execute(
            f"""
            SELECT creator_address, mint, migration_timestamp, create_tx_signature, attempts,
                   COALESCE(job_priority, 0) AS job_priority,
                   COALESCE(priority_reason, 'unknown') AS priority_reason,
                   (COALESCE(job_priority, 0) + MIN(
                       CAST((? - created_at) AS REAL) / {AGE_PROMOTION_INTERVAL_SEC},
                       {AGE_PROMOTION_CAP}
                   )) AS effective_priority
            FROM creator_funding_queue
            WHERE status IN ('pending', 'retry') AND locked_until < ? AND next_attempt_at <= ?
            ORDER BY effective_priority DESC, next_attempt_at ASC, created_at ASC
            LIMIT ?
            """,
            (now, now, now, batch),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _claim_selected_rows(now: int, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Atomically claim a read-selected batch using one short write transaction.

    The eligibility predicate is repeated here so a concurrent claimant cannot
    turn the read/write split into a duplicate claim.
    """
    if not rows:
        return []
    conn = _db_connect(readonly=False, timeout=30)
    claimed: list[dict[str, Any]] = []
    try:
        conn.execute("PRAGMA busy_timeout=30000")
        lock_until = now + LOCK_SECONDS
        for row in rows:
            cur = conn.execute(
                """
                UPDATE creator_funding_queue
                SET status='running', locked_until=?, updated_at=?
                WHERE creator_address=? AND mint=?
                  AND status IN ('pending', 'retry')
                  AND locked_until < ? AND next_attempt_at <= ?
                """,
                (
                    lock_until, now, row["creator_address"], row["mint"],
                    now, now,
                ),
            )
            if int(cur.rowcount or 0) == 1:
                claimed.append(row)
        conn.commit()
        return claimed
    finally:
        conn.close()


def _recover_stale_and_claim(now: int, batch: int):
    """Recover, read-select, and claim without scanning under a write lease.

    X78.17: X78.16 captured this caller holding the global write lane while
    SQLite executed the age-priority SELECT. The preceding recovery UPDATEs
    acquired the lease and the old single transaction retained it through the
    scan. Recovery is now committed first, candidate selection uses mode=ro,
    and the final claim is a separate short mutation with its eligibility
    predicate rechecked for atomicity.
    """
    recovered, stale = _recover_stale_rows(now)
    candidates = _select_ready_rows(now, batch)
    rows = _claim_selected_rows(now, candidates)
    return rows, recovered, stale


def _mark_complete(creator: str, mint: str, attempts: int, now: int, timeout: int = 30) -> None:
    conn = _db_connect(readonly=False, timeout=timeout)
    try:
        conn.execute(f"PRAGMA busy_timeout={int(timeout * 1000)}")
        conn.execute(
            """
            UPDATE creator_funding_queue
            SET status='complete', locked_until=0, attempts=?, last_error=NULL,
                funding_extracted_at=?, updated_at=?
            WHERE creator_address=? AND mint=?
            """,
            (attempts + 1, now, now, creator, mint),
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO token_rescore_queue (mint, reason, created_at)
            SELECT ?, 'funding_complete', ?
            WHERE EXISTS (SELECT 1 FROM token_analysis
                          WHERE mint=? AND COALESCE(lifecycle_stage,'')='migrated'
                            AND migrated_at IS NOT NULL)
            """,
            (mint, now, mint),
        )
        conn.execute("UPDATE token_analysis SET funding_extracted_slot=? WHERE mint=?", (now, mint))
        conn.commit()
    finally:
        conn.close()


def _mark_retry(creator: str, mint: str, attempts: int, error: str, now: int,
                delay: int | None = None, timeout: int = 30) -> None:
    backoff = delay if delay is not None else min(900, 120 * (attempts + 1))
    conn = _db_connect(readonly=False, timeout=timeout)
    try:
        conn.execute(f"PRAGMA busy_timeout={int(timeout * 1000)}")
        conn.execute(
            """
            UPDATE creator_funding_queue
            SET status='retry', locked_until=0, attempts=?, next_attempt_at=?, last_error=?, updated_at=?
            WHERE creator_address=? AND mint=?
            """,
            (attempts + 1, now + backoff, error[:500], now, creator, mint),
        )
        conn.commit()
    finally:
        conn.close()


def _mark_failed(creator: str, mint: str, attempts: int, error: str, now: int,
                 timeout: int = 30) -> None:
    conn = _db_connect(readonly=False, timeout=timeout)
    try:
        conn.execute(f"PRAGMA busy_timeout={int(timeout * 1000)}")
        conn.execute(
            """
            UPDATE creator_funding_queue
            SET status='failed', locked_until=0, attempts=?, last_error=?, updated_at=?
            WHERE creator_address=? AND mint=?
            """,
            (attempts + 1, error[:500], now, creator, mint),
        )
        conn.commit()
    finally:
        conn.close()


def _mark_timeout_terminal(creator: str, mint: str, attempts: int, error: str,
                           now: int, retry_delay: int, failed: bool = False) -> None:
    """Persist a timed-out job without extending its hard wall-time budget."""
    from src.utils.db_locking import bounded_write_wait
    with bounded_write_wait(1.0):
        if failed:
            _mark_failed(creator, mint, attempts, error, now, timeout=1)
        else:
            _mark_retry(
                creator, mint, attempts, error, now, retry_delay, timeout=1
            )


# ── post-extraction enrichment (ported from the retired listener loop, unchanged) ──
def _enqueue_second_hop_lite(creator: str) -> int:
    """Auto-enqueue unclassified funders for second-hop scan — same query/insert
    shape as pumpfun_curve_listener.py's retired _shl_enqueue closure."""
    conn = _db_connect(readonly=False, timeout=10)
    try:
        rows = conn.execute(
            """
            SELECT funder_address FROM creator_funders
            WHERE creator_address = ? AND is_cex = 0 AND is_classified = 0
              AND funder_address NOT IN (SELECT funder_address FROM second_hop_lite_queue)
            """,
            (creator,),
        ).fetchall()
        if not rows:
            return 0
        now = int(time.time())
        conn.executemany(
            """
            INSERT OR IGNORE INTO second_hop_lite_queue
                (funder_address, priority, reason_codes, status, attempts, last_error,
                 rpc_calls_used, created_at, scanned_at, next_attempt_at)
            VALUES (?, 170, '["fresh_creator_auto"]', 'pending', 0, NULL, 0, ?, NULL, ?)
            """,
            [(r[0], now, now) for r in rows],
        )
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def _post_extraction_intelligence_refresh(creator: str) -> None:
    """Synchronous port of pumpfun_curve_listener.py's
    _post_extraction_intelligence_refresh — same debounce contract, same three
    signals (IRC watchlist upsert, NetworksReleaseBuilder, relationship-events
    diff), using a module-level debounce timestamp instead of listener
    instance state since this worker is a separate process."""
    global _intel_refresh_last_run
    now = time.time()
    if now - _intel_refresh_last_run < INTEL_REFRESH_DEBOUNCE_SEC:
        return
    _intel_refresh_last_run = now

    t0 = time.time()
    try:
        from src.core.relationship_events import take_snapshot
        before = take_snapshot(DB_PATH)
    except Exception as e:
        _log(f"[INTEL_REFRESH] snapshot error: {e}")
        before = None

    # X78.0 -- found live during the X78.0 soak: irc_conn.close() was only
    # reached on the success path (the last line inside this try block). Any
    # exception from an earlier statement (the SELECT/INSERT/UPDATE calls
    # below) was caught by the except below without ever closing irc_conn,
    # leaving its write lease held for the rest of this thread's life --
    # this function runs via asyncio.to_thread, on the SAME reused executor
    # pool as every other to_thread-dispatched write in this worker
    # (_mark_complete, _write_heartbeat's own _db_connect calls, etc.), so
    # the leak poisoned every subsequent write on that thread. irc_conn is
    # declared before the try so the finally can close it regardless of
    # which statement raised.
    irc_conn = None
    try:
        from src.core.intelligence_refresh import apply_migration as irc_migrate, _db as irc_db, _score_creator, _now as irc_now
        irc_migrate(DB_PATH)
        irc_conn = irc_db(DB_PATH)
        now_ts = irc_now()
        row = irc_conn.execute(
            """
            SELECT
                COUNT(DISTINCT cf.funder_address) AS funder_count,
                COUNT(DISTINCT ta.mint)           AS token_count,
                SUM(CASE WHEN ta.migrated_at IS NOT NULL THEN 1 ELSE 0 END) AS migrated_count,
                csf.is_self_funding,
                (SELECT COUNT(*) FROM network_membership nm WHERE nm.creator_address = ta.earliest_tx_creator) AS network_count
            FROM token_analysis ta
            LEFT JOIN creator_funders cf ON cf.creator_address = ta.earliest_tx_creator AND cf.is_cex = 0
            LEFT JOIN creator_self_funding csf ON csf.creator_address = ta.earliest_tx_creator
            WHERE ta.earliest_tx_creator = ?
            GROUP BY ta.earliest_tx_creator
            """,
            (creator,),
        ).fetchone()
        non_cex_funders = (row["funder_count"] or 0) if row else 0
        existing = irc_conn.execute(
            "SELECT status FROM intelligence_refresh_candidates WHERE target_type='creator' AND target_address=?",
            (creator,),
        ).fetchone()
        if not existing:
            if non_cex_funders >= 1:
                baseline_reasons = json.dumps(["migrated_creator", "has_non_cex_funder", "baseline_watchlist"])
                irc_conn.execute(
                    """
                    INSERT INTO intelligence_refresh_candidates
                        (target_type, target_address, priority, reason_codes, status, rpc_allowed, created_at, updated_at)
                    VALUES ('creator', ?, 15, ?, 'watchlist', 0, ?, ?)
                    """,
                    (creator, baseline_reasons, now_ts, now_ts),
                )
                irc_conn.commit()
        elif row and non_cex_funders > 0:
            priority, reasons = _score_creator(
                self_funding=bool(row["is_self_funding"]),
                funder_count=non_cex_funders,
                single_creator_ratio=0.0,
                last_scan_age_days=999,
                migrated_count=row["migrated_count"] or 0,
                token_count=row["token_count"] or 0,
                no_network=(row["network_count"] or 0) == 0,
            )
            if priority > 15 and existing["status"] == "watchlist":
                irc_conn.execute(
                    """
                    UPDATE intelligence_refresh_candidates
                    SET priority=?, reason_codes=?, updated_at=?
                    WHERE target_type='creator' AND target_address=? AND priority < ?
                    """,
                    (priority, json.dumps(reasons), now_ts, creator, priority),
                )
                irc_conn.commit()
    except Exception as e:
        _log(f"[INTEL_REFRESH] IRC error: {e}")
    finally:
        if irc_conn is not None:
            try:
                irc_conn.close()
            except Exception:
                pass

    try:
        from src.utils.build_networks_release import build_networks_release
        build_networks_release(DB_PATH)
    except Exception as e:
        _log(f"[INTEL_REFRESH] NetworksRelease error: {e}")

    if before is not None:
        try:
            from src.core.relationship_events import rebuild_after_scan
            rebuild_after_scan(DB_PATH, before=before)
        except Exception as e:
            _log(f"[INTEL_REFRESH] Relationship events error: {e}")

    _log(f"[INTEL_REFRESH] Done in {time.time()-t0:.1f}s creator={creator[:8]}")


# ── job processor ─────────────────────────────────────────────────────────────
# Best-effort wait for extractor-spawned background tasks (see
# _await_orphaned_tasks below) -- these are enrichment, not attribution-
# critical, so a bounded wait that gives up rather than blocking the queue
# indefinitely is the right tradeoff for OBSERVING them. It is NOT the right
# tradeoff for STARTING THE NEXT JOB'S OWN WRITES while they're still
# mid-transaction -- see _STRAGGLER_TASKS / _await_stragglers_before_next_write
# below (X78.2).
ORPHAN_TASK_WAIT_SECONDS = int(os.environ.get("CFQ_ORPHAN_TASK_WAIT_SECONDS", "20"))

# X78.2 -- job-boundary write-lease gate. _await_orphaned_tasks' bounded wait
# below is intentionally allowed to expire and move on (never cancels a task
# that might be mid-write, per its own docstring) -- but "moved on" used to
# mean _process_job returned with the straggler still holding (or about to
# acquire) a write lease on the SAME reused event-loop thread, and the very
# next _process_job call would attempt its OWN write moments later. Since
# TrackedConnection's write lease is a threading.local() reentrancy guard
# (src/core/database_write_service.py::_thread_write_lease), any two
# unreleased acquisitions on that one thread collide with
# NestedDatabaseWriteError -- proven deterministically in
# tests/test_x78_2_detached_descendant_reproduction.py.
#
# The fix is NOT to cancel stragglers (a cancelled write mid-commit is worse
# than a slow one -- see wait_for_background_tasks' own docstring in
# realtime_creator_funding_extractor.py, whose design this preserves
# unchanged) and NOT to make the lease task-scoped (that would weaken the
# guard's actual job: only one write-capable execution on this thread at a
# time, ever). Instead: track every task that either bounded-wait sweep left
# pending in a set that survives past _process_job's return, and require the
# NEXT _process_job call to wait -- unboundedly, since we refuse to cancel --
# for all of them to finish before IT is allowed to start its own writes.
# This is the one boundary where the collision can actually occur, so it is
# the one boundary that needs to gate, instead of every log call or the
# outer loop's liveness.
_STRAGGLER_TASKS: set = set()


async def _await_stragglers_before_next_write() -> None:
    """Block (unboundedly -- see module docstring above) until every
    write-capable background task left over from a PRIOR job's bounded
    supervision sweep has actually finished, before this job is allowed to
    perform its own first write. Called at the very top of _process_job,
    before extraction (and therefore before any write) begins.

    This does not cancel anything and does not change how long a straggler
    is allowed to run -- it only changes what "next job" is allowed to do
    while one is still running: wait, not race it."""
    stragglers = {t for t in _STRAGGLER_TASKS if not t.done()}
    _STRAGGLER_TASKS.difference_update({t for t in _STRAGGLER_TASKS if t.done()})
    if not stragglers:
        return
    _log(f"waiting for {len(stragglers)} straggler background task(s) from a "
         f"prior job to finish before starting this job's own writes "
         f"(job-boundary write-lease gate, X78.2)")
    done, _pending = await asyncio.wait(stragglers)  # unbounded: never race, only wait
    for t in done:
        exc = t.exception() if not t.cancelled() else None
        if exc:
            _log(f"straggler background task raised (non-fatal, enrichment only): {exc}")
    _STRAGGLER_TASKS.difference_update(done)


async def _await_orphaned_tasks(tasks_before: set) -> None:
    """extract_funding_for_new_token() fires several asyncio.create_task()
    calls (CEX detection, BlockSec batching, post-launch automation) that it
    deliberately does not await -- by design, so the listener's own event
    loop (long-lived, many concurrent creators) isn't blocked waiting for
    non-critical enrichment. That design assumes a persistent event loop
    where orphaned tasks eventually get scheduled and finish on their own
    time. It breaks down in a tight polling worker: this worker's own loop
    keeps opening new SQLite write connections every cycle, and if one of
    those orphaned tasks is still mid-write on the same thread pool when the
    next cycle starts, the two collide (NestedDatabaseWriteError) -- and
    while orphaned, their connections count against this worker's own
    connection-leak self-kill guard, since nothing ever closes them.

    Rather than changing the extractor's fire-and-forget design (used
    identically by the listener, which does not hit this problem), this
    worker explicitly tracks and awaits whatever tasks the extraction call
    spawned, with a bounded timeout, before moving on to the next queue
    operation -- turning "orphaned" into "supervised" for this call site
    only. Anything still pending when the bounded wait expires is handed to
    _STRAGGLER_TASKS (X78.2) so the NEXT job's own _await_stragglers_before_next_write()
    call is guaranteed to wait for it before that job starts writing --
    closing the gap this function's own bounded timeout intentionally
    leaves open."""
    loop = asyncio.get_event_loop()
    spawned = asyncio.all_tasks(loop) - tasks_before
    spawned = {t for t in spawned if not t.done()}
    if not spawned:
        return
    _log(f"awaiting {len(spawned)} extractor-spawned background task(s) "
         f"(cex detection / blocksec batch / post-launch automation)")
    done, pending = await asyncio.wait(spawned, timeout=ORPHAN_TASK_WAIT_SECONDS)
    for t in done:
        exc = t.exception() if not t.cancelled() else None
        if exc:
            _log(f"background task raised (non-fatal, enrichment only): {exc}")
    if pending:
        _log(f"{len(pending)} background task(s) still running after "
             f"{ORPHAN_TASK_WAIT_SECONDS}s — handing off to the job-boundary "
             f"write-lease gate (X78.2); NOT cancelling (would corrupt "
             f"whatever they're mid-writing)")
        _STRAGGLER_TASKS.update(pending)


async def _process_job(row: dict) -> None:
    """Claim -> extract -> enrich -> mark. Same attribution-relevant call
    (extract_funding_for_new_token) as both retired consumers; identical
    retry/fail semantics to the listener's retired loop (retry on
    no-funders-written or transient error while attempts < MAX_ATTEMPTS,
    permanent fail once exhausted)."""
    from src.extractors.realtime_creator_funding_extractor import extract_funding_for_new_token

    creator = str(row["creator_address"])
    mint = str(row["mint"])
    migration_timestamp = row["migration_timestamp"]
    if not migration_timestamp:
        try:
            conn = _db_connect(readonly=True, timeout=5)
            try:
                r = conn.execute("SELECT migrated_at FROM token_analysis WHERE mint=? LIMIT 1", (mint,)).fetchone()
            finally:
                conn.close()
            if r and r[0]:
                migration_timestamp = datetime.utcfromtimestamp(int(r[0])).replace(tzinfo=timezone.utc).isoformat()
            else:
                migration_timestamp = datetime.now(timezone.utc).isoformat()
        except Exception:
            migration_timestamp = datetime.now(timezone.utc).isoformat()
    create_tx_signature = row["create_tx_signature"]
    attempts = int(row["attempts"] or 0)
    job_priority = int(row["job_priority"] or 0)
    priority_reason = str(row["priority_reason"] or "unknown")

    # X78.16 Phase D -- claim_occupancy_started marks the FULL span this
    # job occupies a worker claim slot, including time this job spends
    # waiting on a PRIOR job's still-running straggler tasks (X78.2's
    # unbounded _await_stragglers_before_next_write). Previously
    # job_started was set AFTER that wait, so straggler-wait time was
    # invisible in every elapsed= log line -- the true claim-slot
    # occupancy cost of a job was measured incompletely. execution_started
    # marks when this job's OWN extraction work begins, so
    # (execution_started - claim_occupancy_started) isolates queue-wait-
    # for-a-prior-job's-cleanup from this job's own execution time, per
    # Phase D's explicit requirement to measure these separately.
    claim_occupancy_started = time.time()
    _log(f"claimed creator={creator[:12]} mint={mint[:16]} attempts={attempts} "
         f"priority={'HIGH' if job_priority else 'normal'} reason={priority_reason}")

    # X78.14 cancellation recovery: new work is owned by the extractor's
    # per-job scope.  There is deliberately no process-global straggler gate
    # here; that legacy gate was unbounded and could make an unrelated job
    # inherit a prior job's cleanup latency.

    job_started = time.time()

    try:
        # X78.0 -- root cause of a recurring NestedDatabaseWriteError pattern
        # that survived 24 earlier leak-source fixes: asyncio.wait_for()
        # cancels its inner coroutine on timeout, but does not GUARANTEE the
        # cancelled coroutine has actually finished running (including its
        # own finally: extraction_conn.close()) before wait_for's own
        # TimeoutError propagates to this caller. _process_job used to catch
        # that TimeoutError and immediately move on to claim + start the
        # NEXT job -- so a still-cleaning-up, cancelled extract_for_creator
        # call and a brand-new one could genuinely race on the SAME reused
        # event-loop thread, each with their own extraction_conn tagged at
        # the identical source line (hence outer_command==inner_command in
        # every one of these traces). Explicitly creating the Task ourselves
        # (rather than passing a bare coroutine to wait_for) gives us a
        # durable reference to explicitly await, with a bounded grace
        # period, AFTER the timeout fires -- so extraction_conn's own
        # finally-block cleanup is guaranteed to have actually completed
        # before this function returns and the next job is claimed.
        _extraction_task = asyncio.ensure_future(
            extract_funding_for_new_token(creator, migration_timestamp, create_tx_signature, mint)
        )
        try:
            extraction_result = await asyncio.wait_for(
                asyncio.shield(_extraction_task),
                timeout=JOB_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError as timeout_exc:
            # X78.16 Phase D -- separate execution time (the JOB_TIMEOUT_SECONDS
            # budget, already spent by the time we get here) from cancellation
            # cleanup time (EXTRACTION_CANCEL_GRACE_SECONDS, spent below),
            # since both were previously folded into one undifferentiated
            # elapsed= figure in the retry log line.
            _cleanup_started = time.time()
            _extraction_task.cancel()
            try:
                await asyncio.wait_for(_extraction_task, timeout=EXTRACTION_CANCEL_GRACE_SECONDS)
            except asyncio.CancelledError:
                # Expected terminal state: Task.cancel() propagated through
                # the complete owned work scope and the task acknowledged it.
                _log(f"extraction cleanup complete creator={creator[:12]} "
                     f"mint={mint[:16]} cancelled=true")
            except asyncio.TimeoutError:
                _log(f"extraction task for creator={creator[:12]} mint={mint[:16]} did not "
                     f"finish cleanup within {EXTRACTION_CANCEL_GRACE_SECONDS}s of cancellation "
                     f"-- its connection may still be open; proceeding anyway (bounded wait, "
                     f"never block the queue indefinitely)")
            except Exception as cleanup_exc:
                _log(f"extraction task for creator={creator[:12]} mint={mint[:16]} raised during "
                     f"cancellation cleanup (non-fatal, already timed out): {cleanup_exc}")
            cleanup_elapsed_s = time.time() - _cleanup_started
            raise TimeoutError(
                f"creator funding timed out after {JOB_TIMEOUT_SECONDS}s "
                f"(execution={JOB_TIMEOUT_SECONDS}.0s cleanup={cleanup_elapsed_s:.1f}s)"
            ) from timeout_exc
        finally:
            # The extractor now owns an explicit per-job work scope.  Do not
            # diff the process-wide asyncio task set here: that heuristic could
            # capture unrelated long-lived work and add an extra 20 seconds (or
            # an unbounded next-job gate) after the advertised cleanup budget.
            pass

        extraction_errored = bool(isinstance(extraction_result, dict) and extraction_result.get("error"))
        funders = await asyncio.to_thread(_funder_count, creator)
        now = int(time.time())

        if extraction_errored and funders == 0 and attempts < MAX_ATTEMPTS:
            await asyncio.to_thread(_retry_on_nested_write, _mark_retry, creator, mint, attempts, "no_funders_written", now, 60)
            _log(f"retry creator={creator[:12]} mint={mint[:16]} reason=no_funders_written "
                 f"attempt={attempts+1} elapsed={time.time()-job_started:.1f}s "
                 f"claim_slot={time.time()-claim_occupancy_started:.1f}s")
            return

        await asyncio.to_thread(_retry_on_nested_write, _mark_complete, creator, mint, attempts, now)
        _log(f"complete creator={creator[:12]} mint={mint[:16]} funders={funders} "
             f"elapsed={time.time()-job_started:.1f}s "
             f"claim_slot={time.time()-claim_occupancy_started:.1f}s")

        # Post-extraction enrichment — best-effort, never blocks queue progress.
        try:
            shl_count = await asyncio.to_thread(_enqueue_second_hop_lite, creator)
            if shl_count:
                _log(f"second-hop-lite enqueued={shl_count} creator={creator[:12]}")
        except Exception as e:
            _log(f"second-hop-lite enqueue failed creator={creator[:12]}: {e}")

        try:
            from src.core.risk_scoring_builder import RiskScoringBuilder
            await asyncio.to_thread(lambda: RiskScoringBuilder(DB_PATH).score_creator_now(creator))
        except Exception as e:
            _log(f"risk score failed creator={creator[:12]}: {e}")

        try:
            def _rescore():
                from src.core.token_prediction_builder import TokenPredictionBuilder
                conn = _db_connect(readonly=False, timeout=60)
                try:
                    TokenPredictionBuilder(DB_PATH).score_single(conn, mint, "FUNDING_COMPLETE")
                finally:
                    conn.close()
            await asyncio.to_thread(_rescore)
        except Exception as e:
            _log(f"prediction rescore failed mint={mint[:16]}: {e}")

        try:
            from src.core.network_membership_builder import assign_live_network_for_creator
            await asyncio.to_thread(assign_live_network_for_creator, DB_PATH, creator)
        except Exception as e:
            _log(f"live network assignment failed creator={creator[:12]}: {e}")

        try:
            await asyncio.wait_for(
                asyncio.to_thread(_post_extraction_intelligence_refresh, creator),
                timeout=INTEL_REFRESH_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            global _intel_refresh_timeout_count
            _intel_refresh_timeout_count += 1
            _log(f"intelligence refresh deferred (timeout={INTEL_REFRESH_TIMEOUT_SECONDS}s) "
                 f"creator={creator[:12]} -- worker continuing, refresh is best-effort")
        except Exception as e:
            _log(f"intelligence refresh failed creator={creator[:12]}: {e}")

    except Exception as e:
        now = int(time.time())
        extraction_timed_out = isinstance(e, TimeoutError) and str(e).startswith(
            "creator funding timed out"
        )
        if extraction_timed_out:
            retry_delay = min(900, 120 * (attempts + 1))
            terminal_failed = attempts + 1 >= MAX_ATTEMPTS
            try:
                await asyncio.wait_for(
                    asyncio.to_thread(
                        _mark_timeout_terminal, creator, mint, attempts,
                        str(e), now, retry_delay, terminal_failed,
                    ),
                    timeout=3.0,
                )
                _log(f"{'failed' if terminal_failed else 'retry'} "
                     f"creator={creator[:12]} mint={mint[:16]} "
                     f"error={str(e)[:160]} attempt={attempts+1} "
                     f"elapsed={time.time()-job_started:.1f}s "
                     f"claim_slot={time.time()-claim_occupancy_started:.1f}s")
            except Exception as terminal_exc:
                # The existing stale-running reaper remains the durable
                # fallback.  Do not let terminal bookkeeping recreate the
                # cancellation stall it is recording.
                _log(f"timeout state persistence deferred creator={creator[:12]} "
                     f"mint={mint[:16]} error={terminal_exc}; stale reaper owns recovery")
            return
        if attempts + 1 >= MAX_ATTEMPTS:
            await asyncio.to_thread(_retry_on_nested_write, _mark_failed, creator, mint, attempts, str(e), now)
            _log(f"failed creator={creator[:12]} mint={mint[:16]} error={str(e)[:160]} "
                 f"elapsed={time.time()-job_started:.1f}s "
                 f"claim_slot={time.time()-claim_occupancy_started:.1f}s")
        else:
            # X78.16 Phase C -- retry_delay was already a real, growing
            # backoff (120s * attempt, capped at 900s) before this
            # milestone; X78.15 found it working correctly (all retry rows
            # had next_attempt_at eligible, none artificially delayed). The
            # queue-fairness problem retries contributed to was never a
            # missing backoff -- it was that a just-failed job re-enters
            # the SAME unified, unaged priority ordering as fresh arrivals
            # (see AGE_PROMOTION_* / _recover_stale_and_claim's ORDER BY),
            # so a job whose underlying cost hasn't changed can consume
            # another full claim-slot cycle for zero net completions. Age
            # promotion (Phase A/B) bounds how long ANY row -- retry or
            # pending -- can be crowded out, which is the actual lever on
            # "retries must not monopolize claim capacity": a backlog of
            # retries no longer permanently outranks older pending work,
            # or vice versa.
            retry_delay = min(900, 120 * (attempts + 1))
            await asyncio.to_thread(_retry_on_nested_write, _mark_retry, creator, mint, attempts, str(e), now, retry_delay)
            _log(f"retry creator={creator[:12]} mint={mint[:16]} error={str(e)[:160]} "
                 f"attempt={attempts+1} elapsed={time.time()-job_started:.1f}s "
                 f"claim_slot={time.time()-claim_occupancy_started:.1f}s")


def _adaptive_batch(pending: int) -> int:
    p99 = _read_serializer_p99()
    if p99 > 5000:
        return BATCH_SIZE_IDLE
    if pending >= BACKLOG_THRESHOLD:
        return BATCH_SIZE_MAX
    return BATCH_SIZE_IDLE


# ── main loop ─────────────────────────────────────────────────────────────────
async def _run_loop_async(once: bool = False) -> None:
    _log(f"Starting pid={os.getpid()} batch_max={BATCH_SIZE_MAX} idle_batch={BATCH_SIZE_IDLE} "
         f"interval={INTERVAL_SEC}s idle={INTERVAL_IDLE_SEC}s max_handles={MAX_OPEN_HANDLES} "
         f"max_uptime={MAX_UPTIME_HOURS}h wal_alert={WAL_ALERT_MB}MB wal_busy_cycles={WAL_BUSY_CYCLES}")

    if not once:
        t = threading.Thread(target=_wal_watchdog, daemon=True, name="wal-watchdog")
        t.start()

    total_claimed = total_completed = total_retried = total_failed = 0
    cycles = 0
    last_completed_cycle_at = 0

    while not _STOP:
        cycle_start = time.time()
        cycles += 1
        now = int(time.time())

        pending = await asyncio.to_thread(_pending_count)
        _check_self_kill(pending)

        try:
            batch = _adaptive_batch(pending)
            rows, recovered, stale = await asyncio.to_thread(_retry_on_nested_write, _recover_stale_and_claim, now, batch)
            if recovered:
                _log(f"recovered {recovered} stale running job(s) with extracted funders")
            if stale:
                _log(f"reaped {stale} stale running job(s) to retry")

            claimed = len(rows)
            total_claimed += claimed
            cycle_completed = cycle_retried = cycle_failed = 0

            for row in rows:
                # A single job (large funding history, many RPC pages) can run
                # far longer than the outer cycle's own heartbeat cadence --
                # write one before starting the job too, so a slow-but-healthy
                # job is never misreported as a stalled/dead worker.
                # X78.4 -- dispatched via to_thread + retry-on-nested-write
                # (was a direct synchronous call): a straggling cancelled
                # extraction's write lease can still be held here; retrying
                # off the event-loop thread avoids blocking the whole loop
                # (including that same extraction task) while it clears.
                await asyncio.to_thread(_retry_on_nested_write, _write_heartbeat, {
                    "cycles": cycles,
                    "status": "processing",
                    "processing_mint": str(row.get("mint") or "")[:16],
                    "uptime_s": int(time.time()) - _started_at,
                    "open_handles": _open_handle_count(),
                    # Carry the last known cumulative totals so dashboards
                    # reading this mid-job heartbeat (a long single job can
                    # run for minutes) still show a real processing rate and
                    # last-cycle time instead of blanking out while healthy.
                    "total_claimed": total_claimed,
                    "total_completed": total_completed,
                    "total_retried": total_retried,
                    "total_failed": total_failed,
                    "last_cycle_at": last_completed_cycle_at or None,
                })
                before_pending = await asyncio.to_thread(_pending_count)
                await _process_job(row)
                after_pending = await asyncio.to_thread(_pending_count)
                # Coarse per-row outcome inference for cycle-level counters only
                # (exact outcome already logged per-row inside _process_job).
                if after_pending < before_pending:
                    cycle_completed += 1
                elif after_pending > before_pending:
                    cycle_retried += 1

            total_completed += cycle_completed
            total_retried += cycle_retried

            p99 = _read_serializer_p99()
            pending_after = await asyncio.to_thread(_pending_count)

            if claimed:
                _log(f"cycle={cycles} claimed={claimed} pending_after={pending_after} "
                     f"batch={batch} db_p99={p99:.0f}ms handles={_open_handle_count()}")

            # X78.4 -- dispatched via to_thread + retry, see comment at the
            # per-row heartbeat call site above.
            await asyncio.to_thread(_retry_on_nested_write, _write_heartbeat, {
                "cycles": cycles,
                "pending": pending_after,
                "total_claimed": total_claimed,
                "total_completed": total_completed,
                "total_retried": total_retried,
                "total_failed": total_failed,
                "uptime_s": int(time.time()) - _started_at,
                "batch_size": batch,
                "db_p99_ms": p99,
                "open_handles": _open_handle_count(),
                "wal_mb": round(_wal_size_mb(), 1),
                "last_cycle_at": now,
                "intel_refresh_timeout_count": _intel_refresh_timeout_count,
            })
            last_completed_cycle_at = now

        except Exception as exc:
            _log(f"cycle error: {exc}")
            traceback.print_exc()
            try:
                await asyncio.to_thread(_retry_on_nested_write, _write_heartbeat, {"status": "error", "error": str(exc)[:200], "cycles": cycles})
            except Exception:
                pass

        if once:
            break

        pending_after = await asyncio.to_thread(_pending_count)
        p99_after = _read_serializer_p99()
        if p99_after > 5000:
            sleep_s = INTERVAL_IDLE_SEC
        elif pending_after >= BACKLOG_THRESHOLD:
            sleep_s = INTERVAL_SEC
        else:
            sleep_s = INTERVAL_IDLE_SEC
        elapsed = time.time() - cycle_start
        wait = max(0.0, sleep_s - elapsed)
        if wait > 0 and not _STOP:
            await asyncio.sleep(wait)

    _log(f"Stopped. total_claimed={total_claimed} completed={total_completed} "
         f"retried={total_retried} failed={total_failed} cycles={cycles}")


def run_loop(once: bool = False) -> None:
    asyncio.run(_run_loop_async(once=once))


def print_status() -> None:
    """X78.16 Phase F -- replaces a single undifferentiated "oldest pending
    item" figure (which, pre-X78.16, could mean either "about to be
    claimed" or "starved indefinitely" with no way to tell which from the
    number alone) with the specific breakdowns needed to expose starvation
    directly: oldest by priority tier, oldest by state (pending vs retry),
    oldest ELIGIBLE (ready to claim right now -- what age promotion
    actually operates on), and oldest BLOCKED (locked_until still in the
    future, i.e. actually claimed/in-flight, a structurally different
    condition from "waiting to be claimed" that the old single figure
    could not distinguish)."""
    conn = None
    try:
        conn = _db_connect(readonly=True, timeout=3)
        rows = conn.execute(
            "SELECT status, COUNT(*) as n FROM creator_funding_queue GROUP BY status ORDER BY n DESC"
        ).fetchall()
        print("Creator Funding Queue status:")
        for r in rows:
            print(f"  {r[0]:<12} {r[1]}")

        now = int(time.time())

        print("\nOldest pending by priority tier (eligible now vs blocked):")
        tiers = conn.execute(
            "SELECT DISTINCT COALESCE(job_priority, 0) AS jp FROM creator_funding_queue "
            "WHERE status IN ('pending', 'retry') ORDER BY jp DESC"
        ).fetchall()
        for t in tiers:
            jp = t[0]
            eligible = conn.execute(
                "SELECT creator_address, mint, created_at FROM creator_funding_queue "
                "WHERE status IN ('pending','retry') AND COALESCE(job_priority,0)=? "
                "AND locked_until < ? AND next_attempt_at <= ? "
                "ORDER BY created_at ASC LIMIT 1",
                (jp, now, now),
            ).fetchone()
            blocked = conn.execute(
                "SELECT creator_address, mint, created_at FROM creator_funding_queue "
                "WHERE status IN ('pending','retry') AND COALESCE(job_priority,0)=? "
                "AND (locked_until >= ? OR next_attempt_at > ?) "
                "ORDER BY created_at ASC LIMIT 1",
                (jp, now, now),
            ).fetchone()
            eff_priority = jp + min(
                (now - eligible[2]) / AGE_PROMOTION_INTERVAL_SEC, AGE_PROMOTION_CAP
            ) if eligible else None
            if eligible:
                age_h = (now - eligible[2]) / 3600.0
                print(f"  priority={jp} oldest ELIGIBLE: age={age_h:.1f}h "
                      f"effective_priority={eff_priority:.2f} creator={eligible[0][:12]}")
            else:
                print(f"  priority={jp} oldest ELIGIBLE: none (no eligible rows at this tier)")
            if blocked:
                age_h = (now - blocked[2]) / 3600.0
                print(f"  priority={jp} oldest BLOCKED (locked/deferred): age={age_h:.1f}h "
                      f"creator={blocked[0][:12]}")

        print("\nOldest pending by state:")
        for st in ("pending", "retry"):
            r = conn.execute(
                "SELECT creator_address, mint, created_at FROM creator_funding_queue "
                "WHERE status=? ORDER BY created_at ASC LIMIT 1",
                (st,),
            ).fetchone()
            if r:
                age_h = (now - r[2]) / 3600.0
                print(f"  {st:<8} oldest: age={age_h:.1f}h creator={r[0][:12]}")
            else:
                print(f"  {st:<8} oldest: none")

        starved_1h = conn.execute(
            "SELECT COUNT(*) FROM creator_funding_queue "
            "WHERE status IN ('pending','retry') AND locked_until < ? AND next_attempt_at <= ? "
            "AND (? - created_at) > 3600",
            (now, now, now),
        ).fetchone()[0]
        print(f"\nEligible rows waiting > 1h (starvation-exposure count): {starved_1h}")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        if conn:
            conn.close()

    hb = None
    try:
        hb = _db_connect(readonly=True, timeout=3)
        row = hb.execute(
            "SELECT last_seen, meta_json FROM wt_worker_heartbeat WHERE worker_name=?",
            (WORKER_NAME,),
        ).fetchone()
        if row:
            age = int(time.time()) - int(row[0])
            meta = json.loads(row[1] or "{}")
            print(f"\nWorker heartbeat: {age}s ago")
            print(f"  cycles={meta.get('cycles')} pending={meta.get('pending')} "
                  f"completed={meta.get('total_completed')} uptime={meta.get('uptime_s')}s "
                  f"open_handles={meta.get('open_handles','?')} wal_mb={meta.get('wal_mb','?')}")
        else:
            print("\nNo heartbeat found — worker not running")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        if hb:
            hb.close()


if __name__ == "__main__":
    if "--status" in sys.argv:
        print_status()
    elif "--once" in sys.argv:
        run_loop(once=True)
    else:
        run_loop(once=False)
