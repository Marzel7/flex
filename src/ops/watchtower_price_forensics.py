"""Provider-free contracts for continuous Watchtower price forensics.

This module deliberately has no transport, queue, database, scheduler, or
lifecycle-fact dependency.  It freezes a read-only historical cohort and
defines the only admissible shapes for future append-only evidence.  It is a
contract layer, not a runtime integration point.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from typing import Any, Iterable, Mapping


CONTRACT_VERSION = "WATCHTOWER_CONTINUOUS_PRICE_FORENSICS_V1"
HISTORICAL_COHORT_VERSION = "WATCHTOWER_HISTORICAL_BASELINE_20261009_V1"
AUTHORITATIVE_EVIDENCE_CLASS = "AUTHORITATIVE_OBSERVED_MINIMUM"
RESEARCH_EVIDENCE_CLASS = "RESEARCH_PILOT_OBSERVATION"
MAX_HISTORICAL_ALLOWLIST = 10
MAX_COMPACT_FILE_BYTES = 1_000_000
WATERMARK = 1791446333


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _identity(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _positive(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def historical_baseline_manifest(records: Iterable[Mapping[str, Any]], *, captured_at: int) -> dict[str, Any]:
    """Freeze qualified Watchtower Entries without inventing birth evidence.

    The caller supplies only read-only records.  A row remains ineligible if
    its assignment or qualified Entry identity is absent.  Birth provenance is
    represented exactly as available (including ``UNAVAILABLE``), never made
    up from a mint or a later observation.
    """
    frozen: list[dict[str, Any]] = []
    for row in records:
        entry_mc = _positive(row.get("entry_mc_usd"))
        try:
            entry_timestamp = int(row["entry_timestamp"])
            assignment_timestamp = int(row["assignment_timestamp"])
        except (KeyError, TypeError, ValueError):
            continue
        if (str(row.get("operation_id")) != "watchtower" or
                str(row.get("entry_status")) != "QUALIFIED" or
                not row.get("mint") or entry_timestamp <= 0 or assignment_timestamp <= 0 or
                entry_mc is None or not row.get("assignment_provenance") or
                not row.get("entry_provenance")):
            continue
        birth = row.get("birth_provenance")
        frozen.append({
            "mint": str(row["mint"]),
            "assignment_timestamp": assignment_timestamp,
            "assignment_provenance": str(row["assignment_provenance"]),
            "entry_timestamp": entry_timestamp,
            "entry_mc_usd": entry_mc,
            "entry_method": str(row.get("entry_method") or "UNAVAILABLE"),
            "entry_provenance": str(row["entry_provenance"]),
            "birth_provenance": str(birth) if birth else "UNAVAILABLE",
        })
    frozen.sort(key=lambda item: item["mint"])
    if len({row["mint"] for row in frozen}) != len(frozen):
        raise ValueError("DUPLICATE_HISTORICAL_MINT")
    manifest = {"contract_version": CONTRACT_VERSION, "cohort_version": HISTORICAL_COHORT_VERSION,
                "captured_at": int(captured_at), "records": frozen}
    manifest["cohort_identity"] = _identity(manifest)
    return manifest


def admit_prospective_capture(*, fact: Mapping[str, Any], birth: Mapping[str, Any], now: int,
                              watermark: int = WATERMARK) -> dict[str, Any]:
    """Return one deterministic append-only admission, or a precise no-op.

    This is intentionally narrower than worker dispatch: it cannot enqueue or
    acquire and explicitly rejects historical/replayed Entries.
    """
    if str(fact.get("operation_id")) != "watchtower" or not fact.get("durably_committed"):
        return {"status": "NOT_ADMITTED_UNQUALIFIED"}
    if str(fact.get("entry_status")) != "QUALIFIED" or not fact.get("newly_committed"):
        return {"status": "NOT_ADMITTED_HISTORICAL_OR_REPLAY"}
    entry_mc = _positive(fact.get("entry_mc_usd"))
    try:
        entry_timestamp = int(fact["entry_timestamp"])
    except (KeyError, TypeError, ValueError):
        return {"status": "NOT_ADMITTED_UNQUALIFIED"}
    required = (fact.get("mint"), fact.get("assignment_provenance"), fact.get("entry_provenance"),
                birth.get("birth_provenance") or birth.get("birth_evidence_id"))
    if entry_mc is None or entry_timestamp <= watermark or not all(required):
        return {"status": "NOT_ADMITTED_INELIGIBLE"}
    body = {"contract_version": CONTRACT_VERSION, "mint": str(fact["mint"]),
            "entry_timestamp": entry_timestamp, "entry_mc_usd": entry_mc,
            "assignment_provenance": str(fact["assignment_provenance"]),
            "entry_provenance": str(fact["entry_provenance"]),
            "birth_provenance": str(birth.get("birth_provenance") or birth.get("birth_evidence_id")),
            "admitted_at": int(now), "capture_mode": "PROSPECTIVE_APPEND_ONLY",
            "provider_acquisition": "DEFERRED_TO_MONITOR_WORKER"}
    body["admission_identity"] = _identity({key: value for key, value in body.items() if key != "admitted_at"})
    return {"status": "ADMITTED", "admission": body}


def lifecycle_projection(*, opening: Mapping[str, Any], authoritative: Iterable[Mapping[str, Any]] = (),
                         research: Iterable[Mapping[str, Any]] = (), sharp_exit: Iterable[Mapping[str, Any]] = ()) -> dict[str, Any]:
    """Make evidence classes visible without coalescing them into one metric."""
    if not opening.get("mint") or not opening.get("entry_identity"):
        raise ValueError("MISSING_OPENING_IDENTITY")
    authoritative_input = tuple(authoritative)
    research_input = tuple(research)
    authoritative_rows = [dict(row) for row in authoritative_input if row.get("evidence_class") == AUTHORITATIVE_EVIDENCE_CLASS]
    research_rows = [dict(row) for row in research_input if row.get("evidence_class") == RESEARCH_EVIDENCE_CLASS]
    if len(authoritative_rows) != len(authoritative_input) or len(research_rows) != len(research_input):
        raise ValueError("INVALID_EVIDENCE_CLASS")
    return {"contract_version": CONTRACT_VERSION, "opening": dict(opening),
            "authoritative_observed_minimum": authoritative_rows,
            "research_pilot_observations": research_rows,
            "sharp_exit_research": [dict(row) for row in sharp_exit],
            "aggregation_rule": "EVIDENCE_CLASSES_NEVER_COALESCED"}


def historical_backfill_plan(*, allowlist: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Validate a separately authorized, bounded historical plan; never run it."""
    rows = tuple(dict(row) for row in allowlist)
    mints = [str(row.get("mint") or "") for row in rows]
    if not rows or len(rows) > MAX_HISTORICAL_ALLOWLIST or not all(mints) or len(set(mints)) != len(mints):
        raise ValueError("INVALID_HISTORICAL_ALLOWLIST")
    if any(not row.get("request_identity") for row in rows):
        raise ValueError("MISSING_HISTORICAL_REQUEST_IDENTITY")
    return {"mode": "HISTORICAL_RESEARCH_ONLY", "allowlist": rows, "max_requests": len(rows),
            "concurrency": 1, "retries": 0, "pagination": 0, "fallback": 0,
            "persistence": "COMPACT_RESEARCH_EVIDENCE_ONLY", "runtime_action": "NOT_EXECUTED"}


def prospective_capture_contract() -> dict[str, Any]:
    """The future worker contract, expressed as data and not a scheduler."""
    return {"mode": "PROSPECTIVE_AUTOMATIC", "worker_owner": "MonitorWorker",
            "priority": "LOWER_THAN_ENTRY_TERMINAL_AND_CURRENT_MC", "provider_budget": {"global": 20, "per_mint": 4},
            "admission": "POST_COMMIT_ONLY", "historical_backfill": False, "provider_acquisition": "ASYNC_ONLY",
            "raw_payload_retention": False, "max_single_store_bytes": MAX_COMPACT_FILE_BYTES}


def cohort_statistics(rows: Iterable[Mapping[str, Any]], *, evidence_class: str) -> dict[str, Any]:
    """Return coverage-stratified, cohort-local counts without extrapolation."""
    filtered = [row for row in rows if row.get("evidence_class") == evidence_class]
    coverage = Counter(str(row.get("coverage_status") or "UNAVAILABLE") for row in filtered)
    measured = [float(row["observed_drop_percent"]) for row in filtered if _positive(row.get("observed_minimum_mc_usd")) is not None and isinstance(row.get("observed_drop_percent"), (int, float))]
    return {"contract_version": CONTRACT_VERSION, "evidence_class": evidence_class,
            "numerator_denominator": {"measured": len(measured), "cohort": len(filtered)},
            "coverage": dict(sorted(coverage.items())), "median_observed_drop_percent": _median(measured),
            "population_inference": "PROHIBITED"}


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    values = sorted(values)
    middle = len(values) // 2
    return values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2
