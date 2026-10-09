"""Lower-priority, worker-owned Watchtower early-minimum job contract.

This isolated adapter intentionally has no monitor-fact mutation path.  A
future MonitorWorker call site may invoke ``admit_after_qualified_entry`` only
after its Entry write commits; acquisition stays asynchronous and lower than
normal monitor work.
"""
from __future__ import annotations

import hashlib
import time
from typing import Any, Iterable, Mapping

from src.ops.watchtower_observed_minimum import observed_minima
from src.ops.watchtower_observed_minimum_evidence import observed_minimum_records, request_identity

WORK_TYPE = "WATCHTOWER_EARLY_OBSERVED_MINIMUM"
PRIORITY = 4  # after entry, terminal/lifecycle, and current-MC overlay
WINDOW_SECONDS = 3600


def _hash(value: object) -> str:
    import json
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def job_identity(*, mint: str, entry_timestamp: int, entry_provenance: str) -> str:
    return _hash({"work_type": WORK_TYPE, "mint": mint, "entry_timestamp": entry_timestamp,
                  "entry_provenance": entry_provenance, "window_seconds": WINDOW_SECONDS})


def admit_after_qualified_entry(*, fact: Mapping[str, Any], now: int) -> dict[str, Any]:
    """Build one delayed, idempotent job; historical and unqualified input fail closed."""
    if str(fact.get("operation_id")) != "watchtower" or not fact.get("durably_committed"):
        return {"status": "NOT_ADMITTED_UNQUALIFIED"}
    if str(fact.get("entry_status")) != "QUALIFIED" or not fact.get("mint"):
        return {"status": "NOT_ADMITTED_UNQUALIFIED"}
    try:
        entry_timestamp = int(fact["entry_timestamp"])
        entry_mc = float(fact["entry_mc_usd"])
    except (KeyError, TypeError, ValueError):
        return {"status": "NOT_ADMITTED_UNQUALIFIED"}
    if entry_timestamp <= 0 or entry_mc <= 0 or not fact.get("assignment_provenance") or not fact.get("birth_provenance"):
        return {"status": "NOT_ADMITTED_UNQUALIFIED"}
    # Existing entries must be deliberately marked newly_committed: there is
    # no reconciliation/backfill loop in this module.
    if not fact.get("newly_committed"):
        return {"status": "NOT_ADMITTED_HISTORICAL"}
    ident = job_identity(mint=str(fact["mint"]), entry_timestamp=entry_timestamp,
                         entry_provenance=str(fact["entry_provenance"]))
    return {"status": "ADMITTED", "job_id": ident, "work_type": WORK_TYPE, "priority": PRIORITY,
            "next_eligible_at": entry_timestamp + WINDOW_SECONDS,
            "envelope": {"mint": str(fact["mint"]), "operation_id": "watchtower",
                         "entry_timestamp": entry_timestamp, "entry_mc_usd": entry_mc,
                         "entry_provenance": str(fact["entry_provenance"]),
                         "assignment_provenance": str(fact["assignment_provenance"]),
                         "birth_provenance": str(fact["birth_provenance"]), "work_type": WORK_TYPE,
                         "priority": PRIORITY, "next_eligible_at": entry_timestamp + WINDOW_SECONDS}}


def eligible_for_acquisition(job: Mapping[str, Any], *, now: int, higher_priority_pending: bool) -> str:
    if str(job.get("work_type")) != WORK_TYPE: return "REJECTED_INVALID_JOB"
    if higher_priority_pending: return "DEFERRED_HIGHER_PRIORITY"
    if int(job.get("next_eligible_at") or 0) > int(now): return "NOT_DUE"
    return "ELIGIBLE"


def chronological_recovery(*, minimum_timestamp: int | None, candles: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Use only later valid observed highs; never borrow Entry or running peak."""
    if minimum_timestamp is None: return {"status": "INSUFFICIENT_EVIDENCE"}
    later=[]
    for candle in candles:
        try: ts=int(candle["timestamp"]); high=float(candle["high_mc_usd"])
        except (KeyError, TypeError, ValueError): continue
        if ts > minimum_timestamp and high > 0: later.append((ts, high))
    if not later: return {"status": "INSUFFICIENT_EVIDENCE"}
    ts, high=max(later, key=lambda item: (item[1], -item[0]))
    return {"status": "OBSERVED_RECOVERY", "subsequent_peak_mc_usd": high,
            "subsequent_peak_timestamp": ts, "seconds_from_minimum": ts - minimum_timestamp}


def normalize_and_build_records(*, job: Mapping[str, Any], candles: Iterable[Mapping[str, Any]], provenance: str) -> tuple[dict[str, Any], ...]:
    """One completed 60m response becomes four lower-bound records only."""
    result = observed_minima(entry_timestamp=int(job["entry_timestamp"]), entry_mc_usd=float(job["entry_mc_usd"]),
                              candles=candles, provider_provenance=provenance)
    request = request_identity(mint=str(job["mint"]), entry_timestamp=int(job["entry_timestamp"]),
                               window_end=int(job["entry_timestamp"]) + WINDOW_SECONDS,
                               provider_provenance=provenance)
    return observed_minimum_records(mint=str(job["mint"]), entry_identity={"timestamp": int(job["entry_timestamp"]),
        "mc_usd": float(job["entry_mc_usd"]), "provenance": str(job["entry_provenance"])}, request_id=request, contract_result=result)


def persist_records(*, store: Any, records: Iterable[Mapping[str, Any]]) -> tuple[bool, ...]:
    """The only persistence seam: append-only evidence, never monitor facts."""
    return store.append_all(records)


def read_projection(*, store: Any, mint: str) -> dict[int, dict[str, Any]]:
    """Read-only presentation model with explicit observed-evidence labels."""
    rows = store.read(mint=str(mint))
    return {int(row["window_seconds"]): {**row, "metric_label": "OBSERVED_MINIMUM_LOWER_BOUND"}
            for row in rows}
