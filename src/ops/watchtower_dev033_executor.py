"""Bounded manual DEV033 executor for frozen one-minute Watchtower evidence.

This module deliberately has no operational-database dependency.  It is only
invoked explicitly with a frozen manifest and an isolated journal root.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from src.ops.dev_provider_budget import BudgetDenied, DevProviderBudget
from src.ops.token_data_provider_bindings import BirdeyeProductionBinding, build_birdeye_ohlcv_request
from src.ops.watchtower_terminal_ath_finalizer import _candles


SCHEMA = "DEV033_WATCHTOWER_CHRONOLOGICAL_PILOT_MANIFEST_V1"
JOURNAL_SCHEMA = "DEV033_ORDERED_1M_JOURNAL_V1"
MAX_REQUESTS = 30
PACE_SECONDS = 6
MAX_TOKEN_BYTES = 256 * 1024
MAX_JOURNAL_BYTES = 4 * 1024 * 1024
MAX_SESSION_BYTES = 8 * 1024 * 1024
TERMINAL = {"ORDERED_CONTIGUOUS", "ORDERED_SPARSE", "INSUFFICIENT_EVIDENCE", "OUTCOME_UNKNOWN"}


def _canon(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha(value: Any) -> str:
    return hashlib.sha256(_canon(value).encode()).hexdigest()


def _write(path: Path, value: Mapping[str, Any]) -> None:
    encoded = (_canon(value) + "\n").encode()
    if len(encoded) > MAX_JOURNAL_BYTES:
        raise RuntimeError("DEV033_JOURNAL_STORAGE_BOUND_EXCEEDED")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp")
    temporary.write_bytes(encoded)
    os.replace(temporary, path)


def _read_authoritative_birdeye(path: Path) -> str:
    """Read only the approved BIRDEYE binding, without printing or persisting it."""
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if line.startswith("export "):
            line = line[7:].lstrip()
        if line.startswith("BIRDEYE="):
            value = line.split("=", 1)[1].strip().strip("'\"")
            if value:
                return value
    raise RuntimeError("MISSING_AUTHORITATIVE_BIRDEYE_CREDENTIAL")


def load_manifest(path: Path) -> dict[str, Any]:
    """Verify the fixed thirty-record contract before any credential/budget use."""
    document = json.loads(path.read_text())
    content_hash = document.pop("content_hash", None)
    if document.get("schema") != SCHEMA or content_hash != _sha(document):
        raise RuntimeError("DEV033_MANIFEST_INVALID")
    records = document.get("records", [])
    if len(records) != MAX_REQUESTS:
        raise RuntimeError("DEV033_MANIFEST_RECORD_COUNT_INVALID")
    mints: set[str] = set()
    request_ids: set[str] = set()
    for record in records:
        window = record.get("request_window", {})
        request_core = {
            "mint": record.get("mint"), "interval": "1m",
            "time_from": window.get("time_from"), "time_to": window.get("time_to"),
            "chart_type": "mcap", "currency": "usd", "mode": "range",
            "padding": False, "x_chain": "solana",
        }
        expected_id = _sha({
            "schema": SCHEMA, **request_core,
            "opening_timestamp": record.get("opening_timestamp"),
            "observed_peak_timestamp": record.get("observed_peak_timestamp"),
        })
        if (
            not isinstance(record.get("mint"), str)
            or record["mint"] in mints
            or record.get("request_identity") in request_ids
            or window != request_core
            or not isinstance(window["time_from"], int)
            or not isinstance(window["time_to"], int)
            or not window["time_from"] < window["time_to"] <= window["time_from"] + 60_000
            or record.get("request_identity") != expected_id
        ):
            raise RuntimeError("DEV033_MANIFEST_RECORD_INVALID")
        mints.add(record["mint"])
        request_ids.add(record["request_identity"])
    return {**document, "content_hash": content_hash}


def _coverage_gaps(candles: list[dict[str, Any]], window: Mapping[str, Any]) -> tuple[int, int]:
    present = {int(candle["timestamp"]) for candle in candles}
    missing = [stamp for stamp in range(int(window["time_from"]), int(window["time_to"]), 60) if stamp not in present]
    largest = 0
    run_start = previous = None
    for stamp in missing:
        if previous is None or stamp != previous + 60:
            run_start = stamp
        previous = stamp
        largest = max(largest, stamp - int(run_start) + 60)
    return len(missing), largest


def _normalize(outcome: Any, mint: str, window: Mapping[str, Any], request_id: str) -> dict[str, Any]:
    if outcome.status_code != 200:
        return {"state": "INSUFFICIENT_EVIDENCE", "reason": outcome.error_state or f"HTTP_{outcome.status_code}", "provider_provenance": "BIRDEYE_V3_1M_MCAP"}
    try:
        rows = _candles(outcome.payload or {})
    except Exception:
        return {"state": "INSUFFICIENT_EVIDENCE", "reason": "INVALID_1M_MCAP_CANDLES", "provider_provenance": "BIRDEYE_V3_1M_MCAP"}
    normalized = {
        int(row["timestamp"]): {"timestamp": int(row["timestamp"]), "interval_seconds": 60,
            "open_mc_usd": float(row["open"]), "high_mc_usd": float(row["high"]),
            "low_mc_usd": float(row["low"]), "close_mc_usd": float(row["close"])}
        for row in rows if int(window["time_from"]) <= int(row["timestamp"]) < int(window["time_to"])
    }
    candles = [normalized[stamp] for stamp in sorted(normalized)]
    missing_count, largest_gap = _coverage_gaps(candles, window)
    evidence = {
        "mint": mint, "request_identity": request_id, "request_window": dict(window),
        "provider_provenance": "BIRDEYE_V3_1M_MCAP", "candles": candles,
        "returned_candle_count": len(candles), "missing_interval_count": missing_count,
        "largest_gap_seconds": largest_gap,
        "first_observed_timestamp": candles[0]["timestamp"] if candles else None,
        "last_observed_timestamp": candles[-1]["timestamp"] if candles else None,
    }
    if len(_canon(evidence).encode()) > MAX_TOKEN_BYTES:
        raise RuntimeError("DEV033_TOKEN_STORAGE_BOUND_EXCEEDED")
    evidence["coverage_status"] = "ORDERED_CONTIGUOUS" if candles and not missing_count else "ORDERED_SPARSE" if candles else "INSUFFICIENT_EVIDENCE"
    return {"state": evidence["coverage_status"], "evidence": evidence}


def _session_evidence_bytes(journal: Mapping[str, Any]) -> int:
    return sum(len(_canon(record.get("evidence", {})).encode()) for record in journal["records"].values())


def execute(*, manifest_path: Path, journal_path: Path, budget_root: Path, env_path: Path,
            binding_factory: Callable[[], Any] | None = None,
            now: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep,
            execution_authorized: bool = False) -> dict[str, Any]:
    """Run one bounded, serial, no-retry manifest session.

    A post-admission durable state becomes OUTCOME_UNKNOWN after interruption;
    it is consequently never automatically replayed.
    """
    manifest = load_manifest(manifest_path)
    if not execution_authorized:
        raise RuntimeError("DEV033_EXECUTION_AUTHORIZATION_REQUIRED")
    if journal_path.exists():
        journal = json.loads(journal_path.read_text())
    else:
        journal = {"schema": JOURNAL_SCHEMA, "manifest_content_hash": manifest["content_hash"], "records": {
            record["request_identity"]: {"mint": record["mint"], "state": "PENDING", "provider_requests": 0}
            for record in manifest["records"]}}
    expected_ids = {record["request_identity"] for record in manifest["records"]}
    if journal.get("schema") != JOURNAL_SCHEMA or journal.get("manifest_content_hash") != manifest["content_hash"] or set(journal.get("records", {})) != expected_ids:
        raise RuntimeError("DEV033_JOURNAL_MANIFEST_MISMATCH")
    for state in journal["records"].values():
        if state.get("provider_requests") not in {0, 1}:
            raise RuntimeError("DEV033_JOURNAL_REQUEST_COUNT_INVALID")
        if state["state"] in {"ADMISSION_INTENT", "REQUEST_IN_FLIGHT"}:
            state.update(state="OUTCOME_UNKNOWN", reason="DURABLE_POST_ADMISSION_STATE_RECOVERY")
    if sum(state["provider_requests"] for state in journal["records"].values()) > MAX_REQUESTS:
        raise RuntimeError("DEV033_JOURNAL_REQUEST_CAP_INVALID")
    _write(journal_path, journal)

    credential = _read_authoritative_birdeye(env_path)
    binding = binding_factory() if binding_factory else BirdeyeProductionBinding(api_key=credential, credential_label="BIRDEYE")
    last_transport_at: float | None = None
    for record in manifest["records"]:
        state = journal["records"][record["request_identity"]]
        if state["state"] in TERMINAL:
            continue
        if state["state"] != "PENDING" or sum(item["provider_requests"] for item in journal["records"].values()) >= MAX_REQUESTS:
            raise RuntimeError("DEV033_NONRETRYABLE_STATE_OR_CAP")
        if last_transport_at is not None:
            remaining = PACE_SECONDS - (now() - last_transport_at)
            if remaining > 0:
                sleep(remaining)
        state["state"] = "ADMISSION_INTENT"
        _write(journal_path, journal)
        try:
            state["budget_admission"] = DevProviderBudget(budget_root, now=now).admit(record["mint"], "DEV033_ORDERED_1M", global_limit=10)
        except BudgetDenied as error:
            state.update(state="PENDING", reason=str(error))
            _write(journal_path, journal)
            raise
        state["state"] = "REQUEST_IN_FLIGHT"
        _write(journal_path, journal)
        outcome = binding(build_birdeye_ohlcv_request(address=record["mint"], interval="1m",
            time_from=record["request_window"]["time_from"], time_to=record["request_window"]["time_to"]))
        last_transport_at = now()
        state["provider_requests"] += 1
        result = _normalize(outcome, record["mint"], record["request_window"], record["request_identity"])
        if "evidence" in result and _session_evidence_bytes(journal) + len(_canon(result["evidence"]).encode()) > MAX_SESSION_BYTES:
            raise RuntimeError("DEV033_SESSION_STORAGE_BOUND_EXCEEDED")
        state.update(result)
        _write(journal_path, journal)
    return journal
