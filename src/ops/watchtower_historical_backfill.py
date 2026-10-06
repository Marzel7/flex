"""Disabled, bounded Watchtower historical-backfill admission and range state."""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import time
from typing import Any

from src.ops.historical_15m_reconstruction import BUCKET_SECONDS, MAX_429_RETRIES_PER_REQUEST, MAX_PROVEN_BUCKETS_PER_REQUEST, ceil_bucket
from src.ops.operator_lifecycle_projection import ensure_schema as ensure_history_schema
from src.ops.watchtower_terminal_ath_finalizer import WatchtowerTerminalAthFinalizer
from src.ops.watchtower_policy_c import POLICY_C_CHECKPOINTS
from src.ops.watchtower_price_fact_contract import reduce_watchtower_price_facts

HORIZON_SECONDS = 24 * 3600
MAX_OPENING_PROVIDER_CALLS = 1
MAX_BATCH1_JOBS = 1
# This is a per-job retained-checkpoint budget, not a ceiling on unrelated
# SQLite base pages.  The project-wide file ceiling remains independently 500MB.
MAX_BATCH1_CHECKPOINT_BYTES = 1024 * 1024


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def range_chunks(start: int, end: int) -> list[dict[str, int]]:
    if end <= start or (end - start) % BUCKET_SECONDS:
        raise ValueError("HISTORICAL_WINDOW_INVALID")
    width = MAX_PROVEN_BUCKETS_PER_REQUEST * BUCKET_SECONDS
    return [{"start": left, "end": min(left + width, end), "bucket_count": len(range(left, min(left + width, end), BUCKET_SECONDS))} for left in range(start, end, width)]


def preflight(boundary: dict[str, Any]) -> dict[str, Any]:
    required = ("mint", "assignment_id", "migration_signature", "migration_slot", "migration_timestamp", "pumpswap_pool")
    missing = [key for key in required if boundary.get(key) in (None, "")]
    if missing:
        return {"status": "INELIGIBLE_CANONICAL_BOUNDARY", "missing": missing, "provider_calls": 0}
    start = ceil_bucket(int(boundary["migration_timestamp"]))
    chunks = range_chunks(start, start + HORIZON_SECONDS)
    count = len(chunks)
    return {"status": "ELIGIBLE", "provider_calls": 0, "window_start": start, "window_end": start + HORIZON_SECONDS,
            "range_chunks": chunks, "historical_bucket_count": HORIZON_SECONDS // BUCKET_SECONDS,
            "minimum_historical_range_request_count": count, "maximum_historical_range_request_count": count,
            "chunk_bucket_cap": MAX_PROVEN_BUCKETS_PER_REQUEST, "max_opening_provider_calls": MAX_OPENING_PROVIDER_CALLS,
            "max_historical_price_calls": count * (1 + MAX_429_RETRIES_PER_REQUEST),
            "max_total_provider_calls": MAX_OPENING_PROVIDER_CALLS + count * (1 + MAX_429_RETRIES_PER_REQUEST),
            "concurrency": 1, "uses_live_scheduler": False, "forward_watchtower_priority": True,
            "raw_provider_payload_retention": False}


def policy_c_observations(candles: list[dict[str, Any]], *, entry_timestamp: int) -> list[dict[str, Any]]:
    by_timestamp = {int(c["timestamp"]): c for c in candles}
    return [by_timestamp[int(entry_timestamp) + offset]
            for start, end, every in POLICY_C_CHECKPOINTS
            for offset in range(start + every, end + 1, every)
            if int(entry_timestamp) + offset in by_timestamp]


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS watchtower_historical_backfill_jobs (job_id TEXT PRIMARY KEY, boundary_digest TEXT NOT NULL, checkpoint_json TEXT NOT NULL, retained_checkpoint_bytes INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL DEFAULT 'ACQUIRING', terminal_result_json TEXT, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)")
    columns = {row[1] for row in conn.execute("PRAGMA table_info(watchtower_historical_backfill_jobs)")}
    if "state" not in columns: conn.execute("ALTER TABLE watchtower_historical_backfill_jobs ADD COLUMN state TEXT NOT NULL DEFAULT 'ACQUIRING'")
    if "terminal_result_json" not in columns: conn.execute("ALTER TABLE watchtower_historical_backfill_jobs ADD COLUMN terminal_result_json TEXT")
    if "updated_at" not in columns: conn.execute("ALTER TABLE watchtower_historical_backfill_jobs ADD COLUMN updated_at INTEGER NOT NULL DEFAULT 0")
    added_retained_bytes = "retained_checkpoint_bytes" not in columns
    if added_retained_bytes: conn.execute("ALTER TABLE watchtower_historical_backfill_jobs ADD COLUMN retained_checkpoint_bytes INTEGER NOT NULL DEFAULT 0")
    conn.execute("CREATE TABLE IF NOT EXISTS watchtower_historical_backfill_ranges (range_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, ordinal INTEGER NOT NULL, range_start INTEGER NOT NULL, range_end INTEGER NOT NULL, state TEXT NOT NULL, attempt_count INTEGER NOT NULL DEFAULT 0, checkpoint_json TEXT, updated_at INTEGER NOT NULL, UNIQUE(job_id,ordinal))")
    if added_retained_bytes:
        conn.execute("UPDATE watchtower_historical_backfill_jobs SET retained_checkpoint_bytes=COALESCE(LENGTH(checkpoint_json),0)+COALESCE(LENGTH(terminal_result_json),0)+COALESCE((SELECT SUM(LENGTH(checkpoint_json)) FROM watchtower_historical_backfill_ranges r WHERE r.job_id=watchtower_historical_backfill_jobs.job_id),0)")


def admit(db_path: str, boundary: dict[str, Any], *, now: int | None = None) -> dict[str, Any]:
    plan = preflight(boundary)
    if plan["status"] != "ELIGIBLE": return plan
    checkpoint_json = json.dumps({"plan": plan, "raw_provider_payload_retention": False}, sort_keys=True)
    if len(checkpoint_json.encode()) > MAX_BATCH1_CHECKPOINT_BYTES:
        return {"status": "REFUSED_STORAGE_BOUND", "provider_calls": 0}
    stamp = int(time.time() if now is None else now)
    digest = _digest({key: boundary[key] for key in ("mint", "assignment_id", "migration_signature", "migration_slot", "migration_timestamp", "pumpswap_pool")})
    job_id = _digest({"kind": "WATCHTOWER_HISTORICAL_BACKFILL_V1", "boundary": digest})
    with sqlite3.connect(db_path) as conn:
        ensure_schema(conn)
        existing = conn.execute("SELECT retained_checkpoint_bytes FROM watchtower_historical_backfill_jobs WHERE job_id=?", (job_id,)).fetchone()
        existed = existing is not None
        if existing and int(existing[0]) >= MAX_BATCH1_CHECKPOINT_BYTES:
            return {"status": "REFUSED_STORAGE_BOUND", "provider_calls": 0}
        if not existed and conn.execute("SELECT count(*) FROM watchtower_historical_backfill_jobs").fetchone()[0] >= MAX_BATCH1_JOBS:
            return {"status": "REFUSED_BATCH1_CAPACITY", "provider_calls": 0}
        conn.execute("INSERT OR IGNORE INTO watchtower_historical_backfill_jobs(job_id,boundary_digest,checkpoint_json,retained_checkpoint_bytes,state,created_at,updated_at) VALUES(?,?,?,?,?,?,?)", (job_id, digest, checkpoint_json, len(checkpoint_json.encode()), "ACQUIRING", stamp, stamp))
        for ordinal, chunk in enumerate(plan["range_chunks"], 1):
            range_id = _digest({"job_id": job_id, "mint": boundary["mint"], "window_start": plan["window_start"], "window_end": plan["window_end"], "range_start": chunk["start"], "range_end": chunk["end"], "ordinal": ordinal, "resolution": "15m", "purpose": "WATCHTOWER_HISTORICAL_RANGE"})
            conn.execute("INSERT OR IGNORE INTO watchtower_historical_backfill_ranges(range_id,job_id,ordinal,range_start,range_end,state,updated_at) VALUES(?,?,?,?,?,?,?)", (range_id, job_id, ordinal, chunk["start"], chunk["end"], "PENDING", stamp))
    return {**plan, "job_id": job_id, "status": "ALREADY_EXISTS" if existed else "PREFLIGHTED"}


def ranges(db_path: str, job_id: str) -> list[dict[str, Any]]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row; ensure_schema(conn)
        return [dict(row) for row in conn.execute("SELECT * FROM watchtower_historical_backfill_ranges WHERE job_id=? ORDER BY ordinal", (job_id,))]


def recover_interrupted(db_path: str, job_id: str, *, now: int) -> int:
    with sqlite3.connect(db_path) as conn:
        ensure_schema(conn)
        return conn.execute("UPDATE watchtower_historical_backfill_ranges SET state='RETRYABLE',updated_at=? WHERE job_id=? AND state='IN_PROGRESS'", (now, job_id)).rowcount


def begin_next_range(db_path: str, job_id: str, *, now: int) -> dict[str, Any] | None:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row; ensure_schema(conn)
        row = conn.execute("SELECT * FROM watchtower_historical_backfill_ranges WHERE job_id=? AND state IN ('PENDING','RETRYABLE') ORDER BY ordinal LIMIT 1", (job_id,)).fetchone()
        if not row: return None
        if int(row["attempt_count"]) >= 2:
            conn.execute("UPDATE watchtower_historical_backfill_ranges SET state='FAILED_CLOSED',updated_at=? WHERE range_id=?", (now, row["range_id"])); return None
        conn.execute("UPDATE watchtower_historical_backfill_ranges SET state='IN_PROGRESS',attempt_count=attempt_count+1,updated_at=? WHERE range_id=?", (now, row["range_id"]))
        return dict(conn.execute("SELECT * FROM watchtower_historical_backfill_ranges WHERE range_id=?", (row["range_id"],)).fetchone())


def complete_range(db_path: str, range_id: str, evidence: dict[str, Any], *, now: int) -> str:
    raw = json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()
    if len(raw) > 64 * 1024: raise ValueError("RANGE_CHECKPOINT_TOO_LARGE")
    with sqlite3.connect(db_path) as conn:
        ensure_schema(conn)
        row = conn.execute("SELECT state,job_id,COALESCE(LENGTH(checkpoint_json),0) FROM watchtower_historical_backfill_ranges WHERE range_id=?", (range_id,)).fetchone()
        if not row or row[0] != "IN_PROGRESS": raise ValueError("RANGE_NOT_IN_PROGRESS")
        retained = conn.execute("SELECT retained_checkpoint_bytes FROM watchtower_historical_backfill_jobs WHERE job_id=?", (row[1],)).fetchone()
        if not retained: raise KeyError(row[1])
        projected = int(retained[0]) - int(row[2]) + len(raw)
        if projected > MAX_BATCH1_CHECKPOINT_BYTES:
            conn.execute("UPDATE watchtower_historical_backfill_ranges SET state='FAILED_CLOSED',updated_at=? WHERE range_id=?", (now, range_id))
            return "REFUSED_STORAGE_BOUND"
        conn.execute("UPDATE watchtower_historical_backfill_ranges SET state='COMPLETED',checkpoint_json=?,updated_at=? WHERE range_id=?", (raw.decode(), now, range_id))
        conn.execute("UPDATE watchtower_historical_backfill_jobs SET retained_checkpoint_bytes=?,updated_at=? WHERE job_id=?", (projected, now, row[1]))
    return "COMPLETED"


def fail_range(db_path: str, range_id: str, *, now: int) -> None:
    with sqlite3.connect(db_path) as conn:
        ensure_schema(conn)
        row = conn.execute("SELECT attempt_count FROM watchtower_historical_backfill_ranges WHERE range_id=?", (range_id,)).fetchone()
        if not row: raise KeyError(range_id)
        conn.execute("UPDATE watchtower_historical_backfill_ranges SET state=?,updated_at=? WHERE range_id=?", ("FAILED_CLOSED" if int(row[0]) >= 2 else "RETRYABLE", now, range_id))


def job_state(db_path: str, job_id: str) -> str:
    with sqlite3.connect(db_path) as conn:
        ensure_schema(conn)
        row = conn.execute("SELECT state FROM watchtower_historical_backfill_jobs WHERE job_id=?", (job_id,)).fetchone()
        if not row: raise KeyError(job_id)
        return str(row[0])


def mark_acquisition_complete(db_path: str, job_id: str, *, now: int) -> str:
    """Close provider ownership once every required range is durably complete."""
    with sqlite3.connect(db_path) as conn:
        ensure_schema(conn)
        state = conn.execute("SELECT state FROM watchtower_historical_backfill_jobs WHERE job_id=?", (job_id,)).fetchone()
        if not state: raise KeyError(job_id)
        if state[0] in {"TERMINAL_COMMIT_PENDING", "COMPLETED"}: return str(state[0])
        incomplete = conn.execute("SELECT count(*) FROM watchtower_historical_backfill_ranges WHERE job_id=? AND state!='COMPLETED'", (job_id,)).fetchone()[0]
        if incomplete: raise ValueError("HISTORICAL_ACQUISITION_INCOMPLETE")
        conn.execute("UPDATE watchtower_historical_backfill_jobs SET state='TERMINAL_COMMIT_PENDING',updated_at=? WHERE job_id=?", (now, job_id))
        return "TERMINAL_COMMIT_PENDING"


def complete_after_terminal_history(db_path: str, job_id: str, result: dict[str, Any], *, now: int) -> dict[str, Any]:
    """Run only provider-free terminal materialization after acquisition is closed."""
    state = mark_acquisition_complete(db_path, job_id, now=now)
    if state == "COMPLETED": return {"state": "COMPLETED", "provider_calls": 0}
    finalized = commit_to_existing_watchtower_terminal(db_path, result, now=now)
    terminal_json = json.dumps({"entry_method": result["entry_method"], "entry_exactness": result["entry_exactness"], "finalized": finalized.get("state")}, sort_keys=True)
    with sqlite3.connect(db_path) as conn:
        ensure_schema(conn)
        row = conn.execute("SELECT retained_checkpoint_bytes,COALESCE(LENGTH(terminal_result_json),0) FROM watchtower_historical_backfill_jobs WHERE job_id=?", (job_id,)).fetchone()
        if not row: raise KeyError(job_id)
        projected = int(row[0]) - int(row[1]) + len(terminal_json.encode())
        if projected > MAX_BATCH1_CHECKPOINT_BYTES: raise ValueError("REFUSED_STORAGE_BOUND")
        conn.execute("UPDATE watchtower_historical_backfill_jobs SET state='COMPLETED',terminal_result_json=?,retained_checkpoint_bytes=?,updated_at=? WHERE job_id=? AND state='TERMINAL_COMMIT_PENDING'", (terminal_json, projected, now, job_id))
    return {**finalized, "job_state": "COMPLETED"}


def commit_to_existing_watchtower_terminal(db_path: str, result: dict[str, Any], *, now: int) -> dict[str, Any]:
    """Materialize completed retained 15m evidence, then use the existing finalizer."""
    required = ("mint", "entry_timestamp", "entry_mc_usd", "entry_method", "entry_exactness", "candles")
    if any(result.get(key) in (None, "") for key in required):
        raise ValueError("HISTORICAL_TERMINAL_RESULT_INCOMPLETE")
    candles = sorted(result["candles"], key=lambda row: int(row["timestamp"]))
    if not candles:
        raise ValueError("HISTORICAL_TERMINAL_CANDLES_REQUIRED")
    entry_timestamp, entry_mc = int(result["entry_timestamp"]), float(result["entry_mc_usd"])
    reduced = reduce_watchtower_price_facts(entry_mc, entry_timestamp, candles, current_close=float(candles[-1]["close"]))
    terminal_at = int(candles[-1]["timestamp"])
    if not entry_timestamp < terminal_at or float(reduced["drawdown_percent"]) < 85:
        raise ValueError("HISTORICAL_TERMINAL_EVIDENCE_INSUFFICIENT")
    provenance = _digest({"kind": "HISTORICAL_TERMINAL_ADAPTER_V1", "mint": result["mint"], "entry_method": result["entry_method"], "candles": candles})
    with sqlite3.connect(db_path) as conn:
        ensure_history_schema(conn)
        conn.execute("""INSERT OR IGNORE INTO operation_monitor_facts(operation_id,mint,cohort_class,entry_method,entry_timestamp,entry_mc_usd,entry_status,entry_exactness,latest_mc_usd,latest_mc_timestamp,current_multiple,running_peak_mc_usd,running_peak_timestamp,running_peak_multiple,drawdown_percent,monitor_state,monitor_started_at,last_observation_at,next_observation_at,monitor_completed_at,provider_call_count,candles_retained,candle_resolution,evidence_status,provenance_digest,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", ("watchtower", result["mint"], "PROSPECTIVE_MONITOR_COHORT", result["entry_method"], entry_timestamp, entry_mc, "QUALIFIED", result["entry_exactness"], float(candles[-1]["close"]), terminal_at, float(candles[-1]["close"])/entry_mc, float(reduced["peak"]["value"]), int(reduced["peak"]["timestamp"]), float(reduced["peak_multiple"]), float(reduced["drawdown_percent"]), "PRICE_MONITOR_COMPLETE_COLLAPSED", entry_timestamp, terminal_at, None, terminal_at, 0, len(candles), "15m", "QUALIFIED_HISTORICAL_BACKFILL", provenance, now, now))
        for candle in candles:
            conn.execute("""INSERT OR IGNORE INTO operation_monitor_observations(operation_id,mint,observation_timestamp,mc_usd,resolution,source,request_identity,provenance_digest,created_at,open_mc_usd,high_mc_usd,low_mc_usd,close_mc_usd) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", ("watchtower", result["mint"], int(candle["timestamp"]), float(candle["close"]), "15m", "HISTORICAL_BACKFILL", provenance, _digest(candle), now, float(candle["open"]), float(candle["high"]), float(candle["low"]), float(candle["close"])))
    def retained_only_binding(_request: dict[str, Any]) -> Any:
        raise RuntimeError("HISTORICAL_TERMINAL_RETAINED_OHLC_REQUIRED")
    finalized = WatchtowerTerminalAthFinalizer(db_path, binding=retained_only_binding, now=lambda: now).finalize(result["mint"], interval="15m", watchtower_price_fact_contract=True)
    return {**finalized, "entry_method": result["entry_method"], "entry_exactness": result["entry_exactness"]}
