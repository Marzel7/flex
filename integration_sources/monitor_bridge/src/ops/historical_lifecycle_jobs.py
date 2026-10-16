"""DEV-only durable jobs for bounded observed historical lifecycles.

This module deliberately keeps observed lifecycle evidence separate from the
frozen Watchtower strategy entry.  Admission is a post-commit local write; a
provider transport can occur only in :meth:`run_one`, never in admission or a
read projection.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Callable

from src.ops.acquisition_modes import HISTORICAL_RESEARCH
from src.ops.historical_15m_reconstruction import (
    BUCKET_SECONDS, MAX_429_RETRIES_PER_REQUEST, MAX_PROVEN_BUCKETS_PER_REQUEST,
    Historical15mReconstructor, ProviderCapacityBlocked, ceil_bucket,
    ensure_historical_coverage_schema,
)
from src.ops.dev_provider_budget import BudgetDenied

BLUEPRINT_ID = "HISTORICAL_LIFECYCLE_EVIDENCE_V1"
WINDOW_VERSION = "MIGRATION_4H_15M_V1"
RESULT_SCHEMA_VERSION = "historical-lifecycle-result.v1"
DEFAULT_WINDOW_SECONDS = 4 * 60 * 60
PER_JOB_BASE_REQUEST_CEILING = 1
TERMINAL_STATES = {"COMPLETE", "PARTIAL", "INSUFFICIENT_EVIDENCE"}
ELIGIBLE_OPERATIONS = {"watchtower", "watchtower_deep"}


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def job_identity(*, mint: str, operation_id: str, anchor_type: str,
                 anchor_identity: str, window_version: str = WINDOW_VERSION) -> str:
    """Stable logical identity; duplicate admission is harmless."""
    return _digest({"mint": mint, "operation_id": operation_id.lower(),
                    "blueprint_id": BLUEPRINT_ID, "anchor_type": anchor_type,
                    "anchor_identity": anchor_identity, "window_version": window_version})


def plan_window(*, anchor_timestamp: int, window_seconds: int = DEFAULT_WINDOW_SECONDS) -> dict[str, int]:
    """Use the executor's exact bucket primitive, never a parallel calculation."""
    start = ceil_bucket(anchor_timestamp)
    end = ceil_bucket(int(anchor_timestamp) + int(window_seconds))
    buckets = len(range(start, end, BUCKET_SECONDS))
    return {"start": start, "end": end, "bucket_count": buckets,
            "planned_base_requests": math.ceil(buckets / MAX_PROVEN_BUCKETS_PER_REQUEST)}


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS historical_lifecycle_jobs (
        job_id TEXT PRIMARY KEY, mint TEXT NOT NULL, operation_id TEXT NOT NULL,
        blueprint_id TEXT NOT NULL, anchor_type TEXT NOT NULL, anchor_identity TEXT NOT NULL,
        anchor_timestamp INTEGER NOT NULL, window_version TEXT NOT NULL,
        window_start INTEGER NOT NULL, window_end INTEGER NOT NULL,
        status TEXT NOT NULL, base_request_ceiling INTEGER NOT NULL,
        base_request_count INTEGER NOT NULL DEFAULT 0, retry_count INTEGER NOT NULL DEFAULT 0,
        transport_outcome TEXT, result_json TEXT, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_historical_lifecycle_jobs_due ON historical_lifecycle_jobs(status,updated_at)")


def _row_result(row: sqlite3.Row | tuple[Any, ...] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    out = dict(row)
    for key in ("result_json",):
        if out.get(key):
            out[key[:-5]] = json.loads(out[key])
        out.pop(key, None)
    return out


def _lifecycle_metrics(result: dict[str, Any] | list[dict[str, Any]], *, anchor_timestamp: int) -> dict[str, Any]:
    candles = list(result if isinstance(result, list) else (result.get("candles") or []))
    if not candles:
        return {"first_observed_mcap": None, "peak_mcap": None, "peak_timestamp": None,
                "seconds_to_peak": None, "peak_multiple_from_first_15m": None,
                "post_peak_low_mcap": None, "post_peak_low_timestamp": None,
                "drawdown_from_peak_percent": None, "collapse_85_reached": False,
                "collapse_85_timestamp": None, "seconds_peak_to_collapse": None,
                "final_mcap": None, "final_timestamp": None, "final_to_peak_ratio": None}
    first = candles[0]
    peak = max(candles, key=lambda c: (float(c["high"]), -int(c["timestamp"])))
    later = [c for c in candles if int(c["timestamp"]) > int(peak["timestamp"])]
    low = min(later, key=lambda c: (float(c["low"]), int(c["timestamp"]))) if later else None
    drawdown = ((float(peak["high"]) - float(low["low"])) * 100 / float(peak["high"])) if low else None
    collapse = low if drawdown is not None and drawdown >= 85 else None
    final = candles[-1]
    return {"first_observed_mcap": float(first["close"]), "first_timestamp": int(first["timestamp"]),
            "peak_mcap": float(peak["high"]), "peak_timestamp": int(peak["timestamp"]),
            "seconds_to_peak": int(peak["timestamp"]) - int(anchor_timestamp),
            "peak_multiple_from_first_15m": float(peak["high"]) / float(first["close"]),
            "post_peak_low_mcap": float(low["low"]) if low else None,
            "post_peak_low_timestamp": int(low["timestamp"]) if low else None,
            "drawdown_from_peak_percent": drawdown, "collapse_85_reached": bool(collapse),
            "collapse_85_timestamp": int(collapse["timestamp"]) if collapse else None,
            "seconds_peak_to_collapse": int(collapse["timestamp"]) - int(peak["timestamp"]) if collapse else None,
            "final_mcap": float(final["close"]), "final_timestamp": int(final["timestamp"]),
            "final_to_peak_ratio": float(final["close"]) / float(peak["high"])}


@dataclass
class HistoricalLifecycleJobs:
    db_path: str
    now: Callable[[], float] = time.time

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        ensure_schema(conn)
        ensure_historical_coverage_schema(conn)
        return conn

    def admit_after_commit(self, *, mint: str, operation_id: str, anchor_type: str,
                           anchor_identity: str, anchor_timestamp: int,
                           enabled: bool = True) -> dict[str, Any]:
        """Create one historical job after a qualifying state is durable.

        Only existing WT/Deep operation identities with an explicit anchor are
        eligible.  This confers no membership and performs no network work.
        """
        operation_id = str(operation_id).lower()
        if not enabled:
            return {"status": "DISABLED", "provider_calls": 0}
        if operation_id not in ELIGIBLE_OPERATIONS:
            return {"status": "NOT_ELIGIBLE_OPERATION", "provider_calls": 0}
        if not mint or not anchor_identity or int(anchor_timestamp) <= 0:
            return {"status": "NOT_ELIGIBLE_ANCHOR", "provider_calls": 0}
        plan = plan_window(anchor_timestamp=int(anchor_timestamp))
        if plan["planned_base_requests"] > PER_JOB_BASE_REQUEST_CEILING:
            return {"status": "REFUSED_REQUEST_CEILING", "provider_calls": 0, **plan}
        ident = job_identity(mint=mint, operation_id=operation_id, anchor_type=anchor_type,
                             anchor_identity=anchor_identity)
        stamp = int(self.now())
        with self._connect() as conn:
            existed = conn.execute("SELECT 1 FROM historical_lifecycle_jobs WHERE job_id=?", (ident,)).fetchone() is not None
            conn.execute("INSERT OR IGNORE INTO historical_lifecycle_jobs(job_id,mint,operation_id,blueprint_id,anchor_type,anchor_identity,anchor_timestamp,window_version,window_start,window_end,status,base_request_ceiling,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (ident, mint, operation_id, BLUEPRINT_ID, anchor_type, anchor_identity,
                          int(anchor_timestamp), WINDOW_VERSION, plan["start"], plan["end"],
                          "PENDING", PER_JOB_BASE_REQUEST_CEILING, stamp, stamp))
        return {"status": "ALREADY_EXISTS" if existed else "ENQUEUED", "job_id": ident,
                "provider_calls": 0, **plan}

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            return _row_result(conn.execute("SELECT * FROM historical_lifecycle_jobs WHERE job_id=?", (job_id,)).fetchone())

    def readback(self, *, operation_id: str, mint: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM historical_lifecycle_jobs WHERE operation_id=? AND mint=? ORDER BY created_at DESC LIMIT 1", (operation_id.lower(), mint)).fetchone()
        return _row_result(row)

    def execute(self, job_id: str, *, binding: Callable[[dict[str, Any]], Any],
                before_dispatch: Callable[[str, str], Any] | None = None,
                on_429: Callable[[dict[str, str], int, int], Any] | None = None,
                wait_for_capacity: Callable[[], Any] | None = None) -> dict[str, Any]:
        job = self.get(job_id)
        if not job:
            raise KeyError("HISTORICAL_JOB_NOT_FOUND")
        if job["status"] in TERMINAL_STATES:
            return {"status": "ALREADY_TERMINAL", "job": job, "provider_calls": 0}
        # Existing complete coverage is terminal evidence; it must not be reacquired.
        with self._connect() as conn:
            existing = conn.execute("SELECT requested_bucket_count,returned_valid_candle_count,no_provider_candle_count,operation_id FROM historical_price_coverage WHERE operation_id IN (?, 'historical_lifecycle_evidence') AND mint=? AND resolution='15m' AND window_start=? AND window_end=? ORDER BY CASE operation_id WHEN ? THEN 0 ELSE 1 END LIMIT 1", (job["operation_id"], job["mint"], job["window_start"], job["window_end"], job["operation_id"])).fetchone()
        if existing:
            status = "COMPLETE" if int(existing[2]) == 0 and int(existing[1]) else ("PARTIAL" if int(existing[1]) else "INSUFFICIENT_EVIDENCE")
            with self._connect() as conn:
                rows = [dict(row) for row in conn.execute("SELECT observation_timestamp AS timestamp,open_mc_usd AS open,high_mc_usd AS high,low_mc_usd AS low,close_mc_usd AS close FROM operation_monitor_observations WHERE operation_id=? AND mint=? AND resolution='15m' AND observation_timestamp>=? AND observation_timestamp<? ORDER BY observation_timestamp", (existing[3], job["mint"], job["window_start"], job["window_end"]))]
            compact = {"schema_version": RESULT_SCHEMA_VERSION, "mode": HISTORICAL_RESEARCH,
                       "blueprint_id": BLUEPRINT_ID, "anchor_type": job["anchor_type"],
                       "anchor_identity": job["anchor_identity"], "window_start": job["window_start"],
                       "window_end": job["window_end"], "candle_count": len(rows),
                       "gap_count": int(existing[2]), "window_completeness": status,
                       "provider_request_count": 0, **_lifecycle_metrics(rows, anchor_timestamp=int(job["anchor_timestamp"]))}
            with self._connect() as conn:
                conn.execute("UPDATE historical_lifecycle_jobs SET status=?,transport_outcome=?,result_json=?,updated_at=? WHERE job_id=?", (status, "REUSED_EXISTING_COVERAGE", _canonical(compact), int(self.now()), job_id))
            return {"status": status, "provider_calls": 0, "reused_existing_coverage": True, "result": compact}
        try:
            result = Historical15mReconstructor(self.db_path, binding, before_dispatch=before_dispatch,
                on_429=on_429, wait_for_capacity=wait_for_capacity,
                now=self.now, max_429_retries_per_request=MAX_429_RETRIES_PER_REQUEST).reconstruct(
                    job["operation_id"], job["mint"], int(job["anchor_timestamp"]), end_timestamp=int(job["window_end"]))
        except ProviderCapacityBlocked as exc:
            with self._connect() as conn:
                conn.execute("UPDATE historical_lifecycle_jobs SET status='WAITING_BUDGET',transport_outcome=?,retry_count=retry_count+1,updated_at=? WHERE job_id=?", ("HTTP_429", int(self.now()), job_id))
            return {"status": "WAITING_BUDGET", "provider_calls": 0, "headers": dict(exc.headers)}
        except BudgetDenied:
            # Admission occurs immediately before transport.  A denial is a
            # durable pause, not a retry loop and not an attempted request.
            with self._connect() as conn:
                conn.execute("UPDATE historical_lifecycle_jobs SET status='WAITING_BUDGET',transport_outcome='BUDGET_DENIED',updated_at=? WHERE job_id=?", (int(self.now()), job_id))
            return {"status": "WAITING_BUDGET", "provider_calls": 0, "transport": "NOT_ATTEMPTED"}
        status = str(result["completeness"])
        compact = {"schema_version": RESULT_SCHEMA_VERSION, "mode": HISTORICAL_RESEARCH,
                   "blueprint_id": BLUEPRINT_ID, "anchor_type": job["anchor_type"],
                   "anchor_identity": job["anchor_identity"], "window_start": result["start"],
                   "window_end": result["end"], "candle_count": len(result["candles"]),
                   "gap_count": len(result["gaps"]), "gaps": result["gaps"],
                   "window_completeness": status, "provider_request_count": result["provider_requests"],
                   **_lifecycle_metrics(result, anchor_timestamp=int(job["anchor_timestamp"]))}
        with self._connect() as conn:
            conn.execute("UPDATE historical_lifecycle_jobs SET status=?,base_request_count=?,transport_outcome='HTTP_200',result_json=?,updated_at=? WHERE job_id=?", (status, int(result["provider_requests"]), _canonical(compact), int(self.now()), job_id))
        return {"status": status, "provider_calls": int(result["provider_requests"]), "result": compact}


def auto_history_enabled() -> bool:
    return os.getenv("DEV_HISTORICAL_LIFECYCLE_MODE", "0").lower() in {"1", "true", "yes", "on"} and os.getenv("MONITOR_RUNTIME") == "dev"
