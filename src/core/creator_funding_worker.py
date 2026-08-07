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
            if mb >= WAL_ALERT_MB or busy_cycles >= WAL_BUSY_CYCLES:
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


def _recover_stale_and_claim(now: int, batch: int):
    """One connection, one transaction: recover stale running/retry rows that
    already have funders (crash-safe completion), reap genuinely stale
    running rows back to retry, then claim up to `batch` ready rows. Mirrors
    the exact recovery semantics the retired listener loop used, just against
    a single lightweight connection instead of the listener's shared pool."""
    conn = _db_connect(readonly=False, timeout=30)
    try:
        import sqlite3
        conn.row_factory = sqlite3.Row
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
        rows = cur.execute(
            """
            SELECT creator_address, mint, migration_timestamp, create_tx_signature, attempts,
                   COALESCE(job_priority, 0) AS job_priority,
                   COALESCE(priority_reason, 'unknown') AS priority_reason
            FROM creator_funding_queue
            WHERE status IN ('pending', 'retry') AND locked_until < ? AND next_attempt_at <= ?
            ORDER BY job_priority DESC, next_attempt_at ASC, created_at ASC
            LIMIT ?
            """,
            (now, now, batch),
        ).fetchall()
        rows = [dict(r) for r in rows]
        if rows:
            lock_until = now + LOCK_SECONDS
            cur.executemany(
                "UPDATE creator_funding_queue SET status='running', locked_until=?, updated_at=? "
                "WHERE creator_address=? AND mint=?",
                [(lock_until, now, r["creator_address"], r["mint"]) for r in rows],
            )
        conn.commit()
        return rows, recovered, stale
    finally:
        conn.close()


def _mark_complete(creator: str, mint: str, attempts: int, now: int) -> None:
    conn = _db_connect(readonly=False, timeout=30)
    try:
        conn.execute("PRAGMA busy_timeout=30000")
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


def _mark_retry(creator: str, mint: str, attempts: int, error: str, now: int, delay: int | None = None) -> None:
    backoff = delay if delay is not None else min(900, 120 * (attempts + 1))
    conn = _db_connect(readonly=False, timeout=30)
    try:
        conn.execute("PRAGMA busy_timeout=30000")
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


def _mark_failed(creator: str, mint: str, attempts: int, error: str, now: int) -> None:
    conn = _db_connect(readonly=False, timeout=30)
    try:
        conn.execute("PRAGMA busy_timeout=30000")
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

    job_started = time.time()
    _log(f"claimed creator={creator[:12]} mint={mint[:16]} attempts={attempts} "
         f"priority={'HIGH' if job_priority else 'normal'} reason={priority_reason}")

    # X78.2 -- must happen before ANY write this job makes (including the
    # extraction call below), since that is precisely the boundary the
    # collision occurs at: see _await_stragglers_before_next_write's
    # docstring and tests/test_x78_2_detached_descendant_reproduction.py.
    await _await_stragglers_before_next_write()

    try:
        _tasks_before = asyncio.all_tasks(asyncio.get_event_loop())
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
            _extraction_task.cancel()
            try:
                await asyncio.wait_for(_extraction_task, timeout=EXTRACTION_CANCEL_GRACE_SECONDS)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                _log(f"extraction task for creator={creator[:12]} mint={mint[:16]} did not "
                     f"finish cleanup within {EXTRACTION_CANCEL_GRACE_SECONDS}s of cancellation "
                     f"-- its connection may still be open; proceeding anyway (bounded wait, "
                     f"never block the queue indefinitely)")
            except Exception as cleanup_exc:
                _log(f"extraction task for creator={creator[:12]} mint={mint[:16]} raised during "
                     f"cancellation cleanup (non-fatal, already timed out): {cleanup_exc}")
            raise TimeoutError(f"creator funding timed out after {JOB_TIMEOUT_SECONDS}s") from timeout_exc
        finally:
            # Always supervise spawned background tasks, even on timeout/error
            # above -- they were already fired by the extractor regardless of
            # whether the primary extraction call itself succeeded.
            await _await_orphaned_tasks(_tasks_before)

        extraction_errored = bool(isinstance(extraction_result, dict) and extraction_result.get("error"))
        funders = await asyncio.to_thread(_funder_count, creator)
        now = int(time.time())

        if extraction_errored and funders == 0 and attempts < MAX_ATTEMPTS:
            await asyncio.to_thread(_mark_retry, creator, mint, attempts, "no_funders_written", now, 60)
            _log(f"retry creator={creator[:12]} mint={mint[:16]} reason=no_funders_written "
                 f"attempt={attempts+1} elapsed={time.time()-job_started:.1f}s")
            return

        await asyncio.to_thread(_mark_complete, creator, mint, attempts, now)
        _log(f"complete creator={creator[:12]} mint={mint[:16]} funders={funders} "
             f"elapsed={time.time()-job_started:.1f}s")

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
            await asyncio.to_thread(_post_extraction_intelligence_refresh, creator)
        except Exception as e:
            _log(f"intelligence refresh failed creator={creator[:12]}: {e}")

    except Exception as e:
        now = int(time.time())
        if attempts + 1 >= MAX_ATTEMPTS:
            await asyncio.to_thread(_mark_failed, creator, mint, attempts, str(e), now)
            _log(f"failed creator={creator[:12]} mint={mint[:16]} error={str(e)[:160]} "
                 f"elapsed={time.time()-job_started:.1f}s")
        else:
            retry_delay = min(900, 120 * (attempts + 1))
            await asyncio.to_thread(_mark_retry, creator, mint, attempts, str(e), now, retry_delay)
            _log(f"retry creator={creator[:12]} mint={mint[:16]} error={str(e)[:160]} "
                 f"attempt={attempts+1} elapsed={time.time()-job_started:.1f}s")


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
            rows, recovered, stale = await asyncio.to_thread(_recover_stale_and_claim, now, batch)
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
                _write_heartbeat({
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

            _write_heartbeat({
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
            })
            last_completed_cycle_at = now

        except Exception as exc:
            _log(f"cycle error: {exc}")
            traceback.print_exc()
            try:
                _write_heartbeat({"status": "error", "error": str(exc)[:200], "cycles": cycles})
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
    conn = None
    try:
        conn = _db_connect(readonly=True, timeout=3)
        rows = conn.execute(
            "SELECT status, COUNT(*) as n FROM creator_funding_queue GROUP BY status ORDER BY n DESC"
        ).fetchall()
        print("Creator Funding Queue status:")
        for r in rows:
            print(f"  {r[0]:<12} {r[1]}")
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
