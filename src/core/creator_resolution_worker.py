#!/usr/bin/env python3
"""
Creator Resolution Worker — standalone supervised daemon.

Drains creator_resolution_queue independently of Gunicorn. Resolves missing
creators via RPC, commits resolution + funding enqueue atomically, and writes
a heartbeat to wt_worker_heartbeat so /api/health/full can report its state.

Architecture:
  - One process, one loop — no threads, no shared state with the API.
  - Adaptive cadence: fast when backlog exists, slow when idle.
  - Configurable batch size and intervals via env vars.
  - Heartbeat every cycle to wt_worker_heartbeat (worker_name='creator-resolution').
  - Cumulative throughput metrics printed + written to heartbeat meta.

Run:
    python -m src.core.creator_resolution_worker          # continuous loop
    python -m src.core.creator_resolution_worker --once   # one pass then exit
    python -m src.core.creator_resolution_worker --status # queue counts then exit

Env vars:
    DB_PATH                          path to live DB (default: flex_complete_database.db)
    CRQ_BATCH_SIZE                   jobs per cycle when backlog exists (default: 25)
    CRQ_BATCH_SIZE_IDLE              jobs per cycle when queue is quiet (default: 5)
    CRQ_INTERVAL_SEC                 sleep between cycles when backlog exists (default: 5)
    CRQ_INTERVAL_IDLE_SEC            sleep between cycles when queue is empty (default: 60)
    CRQ_BACKLOG_THRESHOLD            pending count above which fast mode kicks in (default: 10)
    CRQ_ENQUEUE_MISSING_LIMIT        tokens to auto-enqueue per cycle (default: 50)
"""

from __future__ import annotations

import json
import os
import signal
import sys
import time
import traceback
from typing import Any, Dict

# ── config ────────────────────────────────────────────────────────────────────
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DB_PATH = os.environ.get(
    "DB_PATH",
    os.path.join(_REPO_ROOT, "database", "flex_complete_database.db"),
)

# Batch size bounds — adaptive selection happens inside _adaptive_batch()
BATCH_SIZE_MAX     = int(os.environ.get("CRQ_BATCH_SIZE",          "25"))
BATCH_SIZE_MID     = int(os.environ.get("CRQ_BATCH_SIZE_MID",      "15"))
BATCH_SIZE_IDLE    = int(os.environ.get("CRQ_BATCH_SIZE_IDLE",      "5"))
INTERVAL_SEC       = int(os.environ.get("CRQ_INTERVAL_SEC",          "5"))
INTERVAL_IDLE_SEC  = int(os.environ.get("CRQ_INTERVAL_IDLE_SEC",    "60"))
BACKLOG_THRESHOLD  = int(os.environ.get("CRQ_BACKLOG_THRESHOLD",    "10"))
ENQUEUE_LIMIT      = int(os.environ.get("CRQ_ENQUEUE_MISSING_LIMIT","50"))

_DB_SERIALIZER_METRICS_PATH = os.path.join(
    _REPO_ROOT, "logs", "db_serializer_metrics.json"
)


def _read_serializer_p99() -> float:
    """Read the listener's DB-write p99 from the shared metrics file. Returns 0.0 on any error."""
    try:
        import json as _json
        with open(_DB_SERIALIZER_METRICS_PATH) as _f:
            return float(_json.load(_f).get("p99_wait_ms", 0.0))
    except Exception:
        return 0.0


def _adaptive_batch(pending: int) -> int:
    """
    Return batch size based on pending queue depth and current DB write pressure.

    DB pressure tiers (read from listener's serializer metrics snapshot):
      p99 > 5000ms → system under heavy contention → smallest batch
      p99 > 1000ms → moderate pressure → mid batch
      else         → healthy → size by queue depth

    Queue-depth tiers (when DB is healthy):
      pending > 500 → max batch
      pending 100–500 → mid batch
      pending < 100 → idle batch
    """
    p99 = _read_serializer_p99()
    if p99 > 5000:
        return BATCH_SIZE_IDLE     # DB under heavy pressure — back off
    if p99 > 1000:
        return BATCH_SIZE_IDLE     # Moderate pressure — conservative

    if pending > 500:
        return BATCH_SIZE_MAX
    if pending > 100:
        return BATCH_SIZE_MID
    return BATCH_SIZE_IDLE

WORKER_NAME = "creator-resolution"

_STOP = False


def _log(msg: str) -> None:
    print(f"[CRQ_WORKER] {msg}", flush=True)


def _handle_signal(signum, frame):
    global _STOP
    _log(f"Signal {signum} received — shutting down after current cycle")
    _STOP = True


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT,  _handle_signal)


# ── heartbeat ─────────────────────────────────────────────────────────────────
def _write_heartbeat(meta: Dict[str, Any]) -> None:
    try:
        import sqlite3
        conn = sqlite3.connect(DB_PATH, timeout=10)
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
                last_seen  = excluded.last_seen,
                status     = excluded.status,
                meta_json  = excluded.meta_json
        """, (WORKER_NAME, json.dumps(meta)))
        conn.commit()
        conn.close()
    except Exception as e:
        _log(f"heartbeat write failed: {e}")


# ── queue depth helper ────────────────────────────────────────────────────────
def _pending_count() -> int:
    try:
        import sqlite3
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=3)
        n = conn.execute(
            "SELECT COUNT(*) FROM creator_resolution_queue "
            "WHERE status IN ('pending','retry') AND next_attempt_at <= strftime('%s','now')"
        ).fetchone()[0]
        conn.close()
        return int(n)
    except Exception:
        return 0


# ── main loop ─────────────────────────────────────────────────────────────────
def run_loop(once: bool = False) -> None:
    from src.core.creator_resolution_queue import (
        process_queue,
        enqueue_missing_migrated_tokens,
        enqueue_missing_funding_jobs,
    )

    _log(f"Starting (batch_max={BATCH_SIZE_MAX} batch_mid={BATCH_SIZE_MID} idle_batch={BATCH_SIZE_IDLE} "
         f"interval={INTERVAL_SEC}s idle={INTERVAL_IDLE_SEC}s "
         f"backlog_threshold={BACKLOG_THRESHOLD})")

    # cumulative totals for the heartbeat
    total_processed  = 0
    total_resolved   = 0
    total_failed     = 0
    total_skipped    = 0
    total_enqueued   = 0
    cycles           = 0
    started_at       = int(time.time())

    while not _STOP:
        cycle_start = time.time()
        cycles += 1

        try:
            # 1. auto-enqueue tokens that migrated without a creator
            new_enqueued = enqueue_missing_migrated_tokens(
                DB_PATH, limit=ENQUEUE_LIMIT, source="crq_worker"
            )
            if new_enqueued:
                _log(f"auto-enqueued {new_enqueued} missing-creator tokens")

            # 2. enqueue funding jobs for already-resolved creators missing them
            new_funding = enqueue_missing_funding_jobs(
                DB_PATH, limit=ENQUEUE_LIMIT, source="crq_worker"
            )
            if new_funding:
                _log(f"auto-enqueued {new_funding} missing funding jobs")

            # 3. adaptive batch size (queue depth + DB pressure)
            pending = _pending_count()
            batch   = _adaptive_batch(pending)
            p99     = _read_serializer_p99()

            # 4. process a batch
            result = process_queue(DB_PATH, limit=batch)

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

            # Resolution efficiency (resolved / (resolved + failed + skipped))
            denom = total_resolved + total_failed + total_skipped
            eff_pct = round(100 * total_resolved / denom, 1) if denom else None

            if proc > 0:
                _log(
                    f"cycle={cycles} processed={proc} resolved={res} "
                    f"skipped={skip} failed={fail} funding_enqueued={fenq} "
                    f"pending_after={max(0, pending - proc)} batch={batch} db_p99={p99:.0f}ms"
                )
            if errs:
                for e in errs:
                    _log(f"  error mint={e.get('mint','?')[:16]} reason={e.get('error','?')}")

            # 5. heartbeat
            _write_heartbeat({
                "cycles":            cycles,
                "pending":           max(0, pending - proc),
                "total_processed":   total_processed,
                "total_resolved":    total_resolved,
                "total_failed":      total_failed,
                "total_skipped":     total_skipped,
                "total_enqueued":    total_enqueued,
                "resolution_eff_pct": eff_pct,
                "uptime_s":          int(time.time()) - started_at,
                "batch_size":        batch,
                "db_p99_ms":         p99,
                "interval_s":        INTERVAL_SEC if pending >= BACKLOG_THRESHOLD else INTERVAL_IDLE_SEC,
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

        # 6. adaptive sleep — also back off if DB is under pressure
        pending_after = _pending_count()
        p99_after = _read_serializer_p99()
        if p99_after > 5000:
            sleep_s = INTERVAL_IDLE_SEC  # DB hot — give the write lane room
        elif pending_after >= BACKLOG_THRESHOLD:
            sleep_s = INTERVAL_SEC
        else:
            sleep_s = INTERVAL_IDLE_SEC
        elapsed = time.time() - cycle_start
        wait    = max(0.0, sleep_s - elapsed)
        if wait > 0 and not _STOP:
            time.sleep(wait)

    _log(f"Stopped. total_processed={total_processed} resolved={total_resolved} "
         f"failed={total_failed} cycles={cycles}")


def print_status() -> None:
    try:
        import sqlite3
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=3)
        rows = conn.execute(
            "SELECT status, COUNT(*) as n FROM creator_resolution_queue GROUP BY status ORDER BY n DESC"
        ).fetchall()
        conn.close()
        print("Creator Resolution Queue status:")
        for r in rows:
            print(f"  {r[0]:<12} {r[1]}")
        hb = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=3)
        hb.row_factory = sqlite3.Row
        row = hb.execute(
            "SELECT last_seen, meta_json FROM wt_worker_heartbeat WHERE worker_name=?",
            (WORKER_NAME,)
        ).fetchone()
        hb.close()
        if row:
            age = int(time.time()) - int(row["last_seen"])
            meta = json.loads(row["meta_json"] or "{}")
            print(f"\nWorker heartbeat: {age}s ago")
            print(f"  cycles={meta.get('cycles')} pending={meta.get('pending')} "
                  f"resolved={meta.get('total_resolved')} uptime={meta.get('uptime_s')}s")
        else:
            print("\nNo heartbeat found — worker not running")
    except Exception as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    if "--status" in sys.argv:
        print_status()
    elif "--once" in sys.argv:
        run_loop(once=True)
    else:
        run_loop(once=False)
