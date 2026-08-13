#!/usr/bin/env python3
"""
Creator Resolution Worker — standalone supervised daemon.

Drains creator_resolution_queue independently of Gunicorn. Resolves missing
creators via RPC, commits resolution + funding enqueue atomically, and writes
a heartbeat to wt_worker_heartbeat so /api/health/full can report its state.

Three-layer connection safety:
  1. Every DB open goes through db_connect() and closes in a finally block.
  2. Self-kill guard: exits via os._exit(1) if open handles exceed threshold
     or if uptime exceeds MAX_UPTIME_HOURS while the queue is idle.
  3. WAL watchdog thread: checks WAL size + checkpoint frame progress every
     60s; only a large WAL with a positive, repeatedly stagnant frame gap logs
     CRITICAL and triggers a supervised restart.

Run:
    python -m src.core.creator_resolution_worker          # continuous loop
    python -m src.core.creator_resolution_worker --once   # one pass then exit
    python -m src.core.creator_resolution_worker --status # queue counts then exit
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
import traceback
from typing import Any, Dict

# ── config ────────────────────────────────────────────────────────────────────
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DB_PATH = os.environ.get(
    "DB_PATH",
    os.path.join(_REPO_ROOT, "database", "flex_complete_database.db"),
)

BATCH_SIZE_MAX     = int(os.environ.get("CRQ_BATCH_SIZE",           "25"))
BATCH_SIZE_MID     = int(os.environ.get("CRQ_BATCH_SIZE_MID",       "15"))
BATCH_SIZE_IDLE    = int(os.environ.get("CRQ_BATCH_SIZE_IDLE",       "5"))
INTERVAL_SEC       = int(os.environ.get("CRQ_INTERVAL_SEC",           "5"))
INTERVAL_IDLE_SEC  = int(os.environ.get("CRQ_INTERVAL_IDLE_SEC",     "60"))
BACKLOG_THRESHOLD  = int(os.environ.get("CRQ_BACKLOG_THRESHOLD",     "10"))
ENQUEUE_LIMIT      = int(os.environ.get("CRQ_ENQUEUE_MISSING_LIMIT", "50"))

# Layer 2: self-kill thresholds
MAX_OPEN_HANDLES   = int(os.environ.get("CRQ_MAX_OPEN_HANDLES",     "10"))
MAX_UPTIME_HOURS   = int(os.environ.get("CRQ_MAX_UPTIME_HOURS",      "6"))

# Layer 3: WAL watchdog thresholds
WAL_CHECK_INTERVAL = int(os.environ.get("CRQ_WAL_CHECK_INTERVAL",   "60"))
WAL_ALERT_MB       = int(os.environ.get("CRQ_WAL_ALERT_MB",         "64"))
WAL_BUSY_CYCLES    = int(os.environ.get("CRQ_WAL_BUSY_CYCLES",       "3"))

_DB_SERIALIZER_METRICS_PATH = os.path.join(
    _REPO_ROOT, "logs", "db_serializer_metrics.json"
)

WORKER_NAME = "creator-resolution"
_STOP = False
_started_at = int(time.time())


def _log(msg: str) -> None:
    print(f"[CRQ_WORKER] {msg}", flush=True)


def _handle_signal(signum, frame):
    global _STOP
    _log(f"Signal {signum} received — shutting down after current cycle")
    _STOP = True


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT,  _handle_signal)


# ── DB wrapper ────────────────────────────────────────────────────────────────
def _db_connect(readonly: bool = False, timeout: int = 10):
    """Open a connection via the project's DB wrapper. Always close in finally."""
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


# ── Layer 2: self-kill guard ──────────────────────────────────────────────────
def _open_handle_count() -> int:
    """Count open file handles to the live DB owned by THIS process."""
    pid = os.getpid()
    # Linux fast path
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
    # macOS fallback
    try:
        result = subprocess.run(["lsof", DB_PATH], capture_output=True, text=True, timeout=5)
        return sum(1 for l in result.stdout.splitlines()
                   if not l.startswith("COMMAND") and l.split()[1:2] == [str(pid)])
    except Exception:
        return 0


def _check_self_kill(pending: int) -> None:
    """Exit via os._exit(1) if handle leak or uptime threshold exceeded."""
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


def _wal_checkpoint_sample() -> Dict[str, int]:
    """Return the complete PASSIVE checkpoint result.

    SQLite returns ``(busy, log_frames, checkpointed_frames)``.  ``busy`` by
    itself is not evidence of a pinned reader: another checkpoint may own the
    checkpoint lock, and RESTART can report busy even after every frame was
    copied.  A negative frame count is an unavailable/contended sample, not a
    reader pin.
    """
    conn = None
    try:
        import sqlite3
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=3)
        r = conn.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
        if not r:
            return {"busy": -1, "log_frames": -1, "checkpointed_frames": -1}
        return {
            "busy": int(r[0]),
            "log_frames": int(r[1]),
            "checkpointed_frames": int(r[2]),
        }
    except Exception:
        return {"busy": -1, "log_frames": -1, "checkpointed_frames": -1}
    finally:
        if conn:
            conn.close()


def _wal_sample_is_stalled(sample: Dict[str, int], previous: Dict[str, int] | None) -> bool:
    """True only when a positive checkpoint gap made no progress.

    WAL file size is deliberately absent from this predicate.  A large file
    whose frames are already checkpointed is reusable SQLite state, not a pin.
    """
    if previous is None:
        return False
    log_frames = sample.get("log_frames", -1)
    checkpointed = sample.get("checkpointed_frames", -1)
    previous_log = previous.get("log_frames", -1)
    previous_checkpointed = previous.get("checkpointed_frames", -1)
    if min(log_frames, checkpointed, previous_log, previous_checkpointed) < 0:
        return False
    if log_frames <= checkpointed or previous_log <= previous_checkpointed:
        return False
    return checkpointed <= previous_checkpointed and log_frames >= previous_log


def _wal_is_critically_pinned(size_mb: float, stalled_cycles: int) -> bool:
    """A critical pin requires both the existing size threshold and stagnation."""
    return size_mb >= WAL_ALERT_MB and stalled_cycles >= WAL_BUSY_CYCLES


def _self_connection_summary() -> Dict[str, Any]:
    """Best-effort process-local attribution included with watchdog samples."""
    try:
        from src.utils.db_locking import get_open_connection_summary
        return get_open_connection_summary(limit=25)
    except Exception as exc:
        return {"error": str(exc)[:160]}


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
    stalled_cycles = 0
    previous = None
    while not _STOP:
        time.sleep(WAL_CHECK_INTERVAL)
        if _STOP:
            break
        try:
            mb = _wal_size_mb()
            sample = _wal_checkpoint_sample()
            stalled = _wal_sample_is_stalled(sample, previous)
            if stalled:
                stalled_cycles += 1
            else:
                stalled_cycles = 0

            log_frames = sample["log_frames"]
            checkpointed = sample["checkpointed_frames"]
            gap = log_frames - checkpointed if min(log_frames, checkpointed) >= 0 else -1
            _log(
                f"WAL: {mb:.1f}MB busy={sample['busy']} log={log_frames} "
                f"checkpointed={checkpointed} uncheckpointed={gap} "
                f"stalled_cycles={stalled_cycles}/{WAL_BUSY_CYCLES}"
            )

            if _wal_is_critically_pinned(mb, stalled_cycles):
                holders = _identify_wal_holders()
                self_connections = _self_connection_summary()
                _log(
                    f"CRITICAL_WAL_PINNED: WAL={mb:.1f}MB "
                    f"stalled_cycles={stalled_cycles} log={log_frames} "
                    f"checkpointed={checkpointed} uncheckpointed={gap} "
                    f"holders={holders} self_connections="
                    f"{json.dumps(self_connections, sort_keys=True, default=str)} "
                    "— this worker exiting for clean restart"
                )
                os._exit(1)

            previous = sample

        except Exception as e:
            _log(f"WAL watchdog error: {e}")


# ── helpers ───────────────────────────────────────────────────────────────────
def _read_serializer_p99() -> float:
    try:
        with open(_DB_SERIALIZER_METRICS_PATH) as f:
            return float(json.load(f).get("p99_wait_ms", 0.0))
    except Exception:
        return 0.0


def _adaptive_batch(pending: int) -> int:
    p99 = _read_serializer_p99()
    if p99 > 5000:
        return BATCH_SIZE_IDLE
    if p99 > 1000:
        return BATCH_SIZE_IDLE
    if pending > 500:
        return BATCH_SIZE_MAX
    if pending > 100:
        return BATCH_SIZE_MID
    return BATCH_SIZE_IDLE


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


def _pending_count() -> int:
    conn = None
    try:
        conn = _db_connect(readonly=True, timeout=3)
        n = conn.execute(
            "SELECT COUNT(*) FROM creator_resolution_queue "
            "WHERE status IN ('pending','retry') AND next_attempt_at <= strftime('%s','now')"
        ).fetchone()[0]
        return int(n)
    except Exception:
        return 0
    finally:
        if conn:
            conn.close()


# ── main loop ─────────────────────────────────────────────────────────────────
def run_loop(once: bool = False) -> None:
    from src.core.creator_resolution_queue import (
        initialize_schema,
        process_queue,
        enqueue_missing_migrated_tokens,
        enqueue_missing_funding_jobs,
        promote_recent_missing_creators,
    )

    _log(f"Starting pid={os.getpid()} "
         f"batch_max={BATCH_SIZE_MAX} idle_batch={BATCH_SIZE_IDLE} "
         f"interval={INTERVAL_SEC}s idle={INTERVAL_IDLE_SEC}s "
         f"max_handles={MAX_OPEN_HANDLES} max_uptime={MAX_UPTIME_HOURS}h "
         f"wal_alert={WAL_ALERT_MB}MB wal_busy_cycles={WAL_BUSY_CYCLES}")

    # Schema migration is a startup boundary, never steady-state queue work.
    initialize_schema(DB_PATH)
    _write_heartbeat({
        "phase": "startup_ready",
        "cycles": 0,
        "uptime_s": int(time.time()) - _started_at,
    })

    # Start WAL watchdog thread (layer 3)
    if not once:
        t = threading.Thread(target=_wal_watchdog, daemon=True, name="wal-watchdog")
        t.start()

    total_processed = total_resolved = total_failed = total_skipped = total_enqueued = 0
    cycles = 0

    while not _STOP:
        cycle_start = time.time()
        cycles += 1

        _write_heartbeat({
            "phase": "cycle_start",
            "cycles": cycles,
            "uptime_s": int(time.time()) - _started_at,
        })

        pending = _pending_count()

        # Layer 2: self-kill guard every cycle
        _check_self_kill(pending)

        try:
            new_enqueued = enqueue_missing_migrated_tokens(
                DB_PATH, limit=ENQUEUE_LIMIT, source="crq_worker", schema_ready=True
            )
            if new_enqueued:
                _log(f"auto-enqueued {new_enqueued} missing-creator tokens")

            new_funding = enqueue_missing_funding_jobs(
                DB_PATH, limit=ENQUEUE_LIMIT, source="crq_worker", schema_ready=True
            )
            if new_funding:
                _log(f"auto-enqueued {new_funding} missing funding jobs")

            promoted_recent = promote_recent_missing_creators(DB_PATH, schema_ready=True)
            if promoted_recent:
                _log(f"elevated {promoted_recent} recent missing-creator tokens")

            pending = _pending_count()
            batch   = _adaptive_batch(pending)
            p99     = _read_serializer_p99()

            result = process_queue(DB_PATH, limit=batch, schema_ready=True)

            proc  = result.get("processed",        0)
            res   = result.get("resolved",         0)
            fail  = result.get("failed",           0)
            skip  = result.get("skipped",          0)
            fenq  = result.get("funding_enqueued", 0)
            errs  = result.get("errors",           [])

            total_processed += proc
            total_resolved  += res
            total_failed    += fail
            total_skipped   += skip
            total_enqueued  += fenq

            denom = total_resolved + total_failed + total_skipped
            eff_pct = round(100 * total_resolved / denom, 1) if denom else None

            if proc > 0:
                handles = _open_handle_count()
                _log(
                    f"cycle={cycles} processed={proc} resolved={res} "
                    f"skipped={skip} failed={fail} funding_enqueued={fenq} "
                    f"pending_after={max(0,pending-proc)} batch={batch} "
                    f"db_p99={p99:.0f}ms handles={handles}"
                )
            for e in errs:
                _log(f"  error mint={e.get('mint','?')[:16]} reason={e.get('error','?')}")

            _write_heartbeat({
                "cycles":             cycles,
                "pending":            max(0, pending - proc),
                "total_processed":    total_processed,
                "total_resolved":     total_resolved,
                "total_failed":       total_failed,
                "total_skipped":      total_skipped,
                "total_enqueued":     total_enqueued,
                "recent_promoted":    promoted_recent,
                "resolution_eff_pct": eff_pct,
                "uptime_s":           int(time.time()) - _started_at,
                "batch_size":         batch,
                "db_p99_ms":          p99,
                "open_handles":       _open_handle_count(),
                "wal_mb":             round(_wal_size_mb(), 1),
            })

        except Exception as exc:
            _log(f"cycle error: {exc}")
            traceback.print_exc()
            try:
                _write_heartbeat({"status": "error", "error": str(exc)[:200], "cycles": cycles})
            except Exception:
                pass

        if once:
            break

        pending_after = _pending_count()
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
            time.sleep(wait)

    _log(f"Stopped. total_processed={total_processed} resolved={total_resolved} "
         f"failed={total_failed} cycles={cycles}")


def print_status() -> None:
    conn = hb = None
    try:
        conn = _db_connect(readonly=True, timeout=3)
        rows = conn.execute(
            "SELECT status, COUNT(*) as n FROM creator_resolution_queue "
            "GROUP BY status ORDER BY n DESC"
        ).fetchall()
        print("Creator Resolution Queue status:")
        for r in rows:
            print(f"  {r[0]:<12} {r[1]}")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        if conn:
            conn.close()

    try:
        hb = _db_connect(readonly=True, timeout=3)
        row = hb.execute(
            "SELECT last_seen, meta_json FROM wt_worker_heartbeat WHERE worker_name=?",
            (WORKER_NAME,)
        ).fetchone()
        if row:
            age = int(time.time()) - int(row[0])
            meta = json.loads(row[1] or "{}")
            print(f"\nWorker heartbeat: {age}s ago")
            print(f"  cycles={meta.get('cycles')} pending={meta.get('pending')} "
                  f"resolved={meta.get('total_resolved')} uptime={meta.get('uptime_s')}s "
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
