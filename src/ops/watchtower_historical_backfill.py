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
MAX_BATCH1_JOBS = 2
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


def promote_recovered_opening(db_path: str, boundary: dict[str, Any], opening: dict[str, Any], *, now: int) -> dict[str, Any]:
    """Promote only a compatible waiting fact for bounded historical recovery.

    This deliberately creates neither a live admission nor future monitor work.
    The historical executor may consume the resulting fact only through its
    bounded backfill job and terminal materialization.
    """
    required = ("mint", "assignment_id", "migration_signature", "migration_slot", "migration_timestamp", "pumpswap_pool")
    if any(boundary.get(key) in (None, "") for key in required):
        raise ValueError("INCOMPLETE_CANONICAL_BOUNDARY")
    required_opening = ("entry_timestamp", "entry_mc_usd", "entry_method", "entry_exactness")
    if any(opening.get(key) in (None, "") for key in required_opening):
        raise ValueError("INCOMPLETE_RECOVERED_OPENING")
    if int(opening["entry_timestamp"]) not in {int(boundary["migration_timestamp"]), int(boundary["migration_timestamp"]) + 1}:
        raise ValueError("RECOVERED_OPENING_TIMESTAMP_INVALID")
    if float(opening["entry_mc_usd"]) <= 0:
        raise ValueError("RECOVERED_OPENING_MC_INVALID")
    provenance = _digest({"kind": "WATCHTOWER_RECOVERED_OPENING_V1", "boundary": boundary, "opening": opening})
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT entry_status,monitor_state,assignment_timestamp,assignment_provenance,entry_timestamp,entry_mc_usd,entry_method,entry_exactness FROM operation_monitor_facts WHERE operation_id='watchtower' AND mint=?", (boundary["mint"],)).fetchone()
        if not row:
            raise ValueError("WAITING_MONITOR_FACT_REQUIRED")
        waiting = row[0] == "WAITING_FOR_ENTRY_REFERENCE" and row[1] == "WAITING_FOR_ENTRY_REFERENCE" and row[2] is not None and bool(row[3])
        same = row[0] == "QUALIFIED" and row[1] == "HISTORICAL_RECOVERY_ACQUIRING" and row[4] == int(opening["entry_timestamp"]) and row[5] == float(opening["entry_mc_usd"]) and row[6] == opening["entry_method"] and row[7] == opening["entry_exactness"]
        if not waiting and not same:
            raise ValueError("INCOMPATIBLE_EXISTING_MONITOR_FACT")
        if waiting:
            conn.execute("UPDATE operation_monitor_facts SET entry_method=?,entry_timestamp=?,entry_mc_usd=?,entry_status='QUALIFIED',entry_exactness=?,monitor_state='HISTORICAL_RECOVERY_ACQUIRING',monitor_started_at=NULL,next_observation_at=NULL,monitor_completed_at=NULL,evidence_status='HISTORICAL_RECOVERY_OPENING_QUALIFIED',provenance_digest=?,updated_at=? WHERE operation_id='watchtower' AND mint=? AND entry_status='WAITING_FOR_ENTRY_REFERENCE' AND monitor_state='WAITING_FOR_ENTRY_REFERENCE'", (opening["entry_method"], int(opening["entry_timestamp"]), float(opening["entry_mc_usd"]), opening["entry_exactness"], provenance, now, boundary["mint"]))
    return {**opening, "state": "HISTORICAL_RECOVERY_OPENING_QUALIFIED", "provenance_digest": provenance}


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
    finalized = commit_to_existing_watchtower_terminal(db_path, result, now=now, historical_job_id=job_id)
    terminal_json = json.dumps({"entry_method": result["entry_method"], "entry_exactness": result["entry_exactness"], "finalized": finalized.get("state")}, sort_keys=True)
    with sqlite3.connect(db_path) as conn:
        ensure_schema(conn)
        row = conn.execute("SELECT retained_checkpoint_bytes,COALESCE(LENGTH(terminal_result_json),0) FROM watchtower_historical_backfill_jobs WHERE job_id=?", (job_id,)).fetchone()
        if not row: raise KeyError(job_id)
        projected = int(row[0]) - int(row[1]) + len(terminal_json.encode())
        if projected > MAX_BATCH1_CHECKPOINT_BYTES: raise ValueError("REFUSED_STORAGE_BOUND")
        conn.execute("UPDATE watchtower_historical_backfill_jobs SET state='COMPLETED',terminal_result_json=?,retained_checkpoint_bytes=?,updated_at=? WHERE job_id=? AND state='TERMINAL_COMMIT_PENDING'", (terminal_json, projected, now, job_id))
    return {**finalized, "job_state": "COMPLETED"}


def _sparse_terminal_eligible(conn: sqlite3.Connection, job_id: str) -> bool:
    job = conn.execute("SELECT state FROM watchtower_historical_backfill_jobs WHERE job_id=?", (job_id,)).fetchone()
    states = [row[0] for row in conn.execute("SELECT state FROM watchtower_historical_backfill_ranges WHERE job_id=?", (job_id,))]
    return bool(job and job[0] == "TERMINAL_COMMIT_PENDING" and len(states) == 6 and all(state == "COMPLETED" for state in states))


def _historical_sparse_terminal(conn: sqlite3.Connection, result: dict[str, Any], candles: list[dict[str, Any]], reduced: dict[str, Any], *, now: int, job_id: str) -> dict[str, Any]:
    """Promote only the compatible waiting fact using retained sparse evidence.

    This is deliberately separate from the live finalizer: a completed historical
    acquisition owns no provider calls and reports observed-only rather than
    continuous 15m coverage.
    """
    if not _sparse_terminal_eligible(conn, job_id):
        raise ValueError("HISTORICAL_SPARSE_TERMINAL_INELIGIBLE")
    mint, entry_timestamp, entry_mc = result["mint"], int(result["entry_timestamp"]), float(result["entry_mc_usd"])
    current = conn.execute("SELECT entry_status,monitor_state,assignment_timestamp,assignment_provenance,entry_timestamp,entry_mc_usd,entry_method,entry_exactness FROM operation_monitor_facts WHERE operation_id='watchtower' AND mint=?", (mint,)).fetchone()
    if not current:
        raise ValueError("WAITING_MONITOR_FACT_REQUIRED")
    waiting = current[0] == "WAITING_FOR_ENTRY_REFERENCE" and current[1] == "WAITING_FOR_ENTRY_REFERENCE" and current[2] is not None and bool(current[3])
    qualified_same = current[0] == "QUALIFIED" and current[4] == entry_timestamp and current[5] == entry_mc and current[6] == result["entry_method"] and current[7] == result["entry_exactness"]
    if not waiting and not qualified_same:
        raise ValueError("INCOMPATIBLE_EXISTING_MONITOR_FACT")
    terminal_at = int(candles[-1]["timestamp"])
    provenance = _digest({"kind": "HISTORICAL_SPARSE_TERMINAL_V1", "job_id": job_id, "mint": mint, "entry_method": result["entry_method"], "candles": candles})
    conn.execute("""UPDATE operation_monitor_facts SET entry_method=?,entry_timestamp=?,entry_mc_usd=?,entry_status='QUALIFIED',entry_exactness=?,latest_mc_usd=?,latest_mc_timestamp=?,current_multiple=?,running_peak_mc_usd=?,running_peak_timestamp=?,running_peak_multiple=?,drawdown_percent=?,reached_2x=?,reached_5x=?,reached_10x=?,monitor_state='PRICE_MONITOR_COMPLETE_COLLAPSED',monitor_started_at=?,last_observation_at=?,next_observation_at=NULL,monitor_completed_at=?,provider_call_count=0,candles_retained=?,candle_resolution='15m',evidence_status='HISTORICAL_SPARSE_OBSERVED_ONLY',provenance_digest=?,final_proven_ath_mc=?,final_ath_multiple=?,final_ath_evidence='HISTORICAL_SPARSE_OBSERVED_15M_HIGH',final_ath_resolution='15m:SPARSE:OBSERVED_ONLY',final_ath_bucket_start=?,final_ath_bucket_end=?,ath_finalized_at=?,ath_finalization_request_id='HISTORICAL_RETAINED_ONLY',ath_finalization_provenance_digest=?,updated_at=? WHERE operation_id='watchtower' AND mint=?""", (result["entry_method"], entry_timestamp, entry_mc, result["entry_exactness"], float(candles[-1]["close"]), terminal_at, float(candles[-1]["close"])/entry_mc, float(reduced["peak"]["value"]), int(reduced["peak"]["timestamp"]), float(reduced["peak_multiple"]), float(reduced["drawdown_percent"]), int(reduced["peak_multiple"] >= 2), int(reduced["peak_multiple"] >= 5), int(reduced["peak_multiple"] >= 10), entry_timestamp, terminal_at, terminal_at, len(candles), provenance, float(reduced["peak"]["value"]), float(reduced["peak_multiple"]), int(reduced["peak"]["timestamp"]), int(reduced["peak"]["timestamp"]) + 900, now, provenance, now, mint))
    return {"state": "FINALIZED", "provider_calls": 0, "coverage_class": "SPARSE", "terminal_metric_exactness": "OBSERVED_ONLY"}


def commit_to_existing_watchtower_terminal(db_path: str, result: dict[str, Any], *, now: int, historical_job_id: str | None = None) -> dict[str, Any]:
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
        if historical_job_id is not None:
            existing = conn.execute("SELECT entry_status,monitor_state FROM operation_monitor_facts WHERE operation_id='watchtower' AND mint=?", (result["mint"],)).fetchone()
            if existing and ((existing[0] == "WAITING_FOR_ENTRY_REFERENCE" and existing[1] == "WAITING_FOR_ENTRY_REFERENCE") or (existing[0] == "QUALIFIED" and existing[1] == "HISTORICAL_RECOVERY_ACQUIRING")):
                for candle in candles:
                    conn.execute("""INSERT OR IGNORE INTO operation_monitor_observations(operation_id,mint,observation_timestamp,mc_usd,resolution,source,request_identity,provenance_digest,created_at,open_mc_usd,high_mc_usd,low_mc_usd,close_mc_usd) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", ("watchtower", result["mint"], int(candle["timestamp"]), float(candle["close"]), "15m", "HISTORICAL_BACKFILL", provenance, _digest(candle), now, float(candle["open"]), float(candle["high"]), float(candle["low"]), float(candle["close"])))
                return _historical_sparse_terminal(conn, result, candles, reduced, now=now, job_id=historical_job_id) | {"entry_method": result["entry_method"], "entry_exactness": result["entry_exactness"]}
        conn.execute("""INSERT OR IGNORE INTO operation_monitor_facts(operation_id,mint,cohort_class,entry_method,entry_timestamp,entry_mc_usd,entry_status,entry_exactness,latest_mc_usd,latest_mc_timestamp,current_multiple,running_peak_mc_usd,running_peak_timestamp,running_peak_multiple,drawdown_percent,monitor_state,monitor_started_at,last_observation_at,next_observation_at,monitor_completed_at,provider_call_count,candles_retained,candle_resolution,evidence_status,provenance_digest,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", ("watchtower", result["mint"], "PROSPECTIVE_MONITOR_COHORT", result["entry_method"], entry_timestamp, entry_mc, "QUALIFIED", result["entry_exactness"], float(candles[-1]["close"]), terminal_at, float(candles[-1]["close"])/entry_mc, float(reduced["peak"]["value"]), int(reduced["peak"]["timestamp"]), float(reduced["peak_multiple"]), float(reduced["drawdown_percent"]), "PRICE_MONITOR_COMPLETE_COLLAPSED", entry_timestamp, terminal_at, None, terminal_at, 0, len(candles), "15m", "QUALIFIED_HISTORICAL_BACKFILL", provenance, now, now))
        for candle in candles:
            conn.execute("""INSERT OR IGNORE INTO operation_monitor_observations(operation_id,mint,observation_timestamp,mc_usd,resolution,source,request_identity,provenance_digest,created_at,open_mc_usd,high_mc_usd,low_mc_usd,close_mc_usd) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", ("watchtower", result["mint"], int(candle["timestamp"]), float(candle["close"]), "15m", "HISTORICAL_BACKFILL", provenance, _digest(candle), now, float(candle["open"]), float(candle["high"]), float(candle["low"]), float(candle["close"])))
    def retained_only_binding(_request: dict[str, Any]) -> Any:
        raise RuntimeError("HISTORICAL_TERMINAL_RETAINED_OHLC_REQUIRED")
    finalized = WatchtowerTerminalAthFinalizer(db_path, binding=retained_only_binding, now=lambda: now).finalize(result["mint"], interval="15m", watchtower_price_fact_contract=True)
    return {**finalized, "entry_method": result["entry_method"], "entry_exactness": result["entry_exactness"]}


def resume_terminal_from_retained(db_path: str, job_id: str, mint: str, *, now: int = 0) -> dict[str, Any]:
    """Materialize a terminal-pending job from durable compact state only."""
    with sqlite3.connect(db_path) as conn:
        opening = conn.execute("SELECT result_json FROM watchtower_historical_openings WHERE mint=? AND state='QUALIFIED'", (mint,)).fetchone()
        checkpoints = conn.execute("SELECT checkpoint_json FROM watchtower_historical_backfill_ranges WHERE job_id=? AND state='COMPLETED' ORDER BY ordinal", (job_id,)).fetchall()
    if not opening or len(checkpoints) != 6:
        raise ValueError("HISTORICAL_TERMINAL_RETAINED_EVIDENCE_REQUIRED")
    result = json.loads(opening[0])
    candles = [candle for row in checkpoints for candle in json.loads(row[0]).get("candles", [])]
    return complete_after_terminal_history(db_path, job_id, {**result, "mint": mint, "candles": candles}, now=now)
