#!/usr/bin/env python3
"""Run only frozen DEV-014 Batch 4 through the shared Watchtower budget gate.

This is an operator-invoked research tool, not a monitor job.  It has one
request path, serial execution, no retries/pagination/fallback, and records no
raw provider payload.  It never writes the canonical database or queue; its
only future queue-root mutation is the existing atomic provider-budget debit.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sys
import urllib.request
from pathlib import Path
from typing import Any, Mapping

from src.ops.dev_provider_budget import BudgetDenied
from src.ops.token_data_provider_bindings import BirdeyeProductionBinding, ProviderTransportOutcome
from src.ops.watchtower_historical_budget import HistoricalForensicsBudgetAdmission
from src.ops.watchtower_observed_minimum import observed_minima


ROOT = Path(__file__).resolve().parents[1]
ANCHORS = ROOT / "docs/audits/dev014_watchtower_historical_research_anchor_qualification_20261009.v1.json"
POPULATION = ROOT / "docs/audits/dev014_watchtower_forensic_population_v2_20261009.v1.json"
OUTPUT = ROOT / "docs/audits/dev014_watchtower_recent_first_price_forensics_batch_4_20261009.v1.json"
MAX_ARTIFACT_BYTES = 1_000_000
EXPECTED_RANKS = tuple(range(31, 41))
HEADER_ALLOWLIST = {"x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset", "x-birdeye-cu", "x-compute-units"}


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _positive(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _normalize(outcome: ProviderTransportOutcome, *, start: int, end: int) -> tuple[list[dict[str, int | float]], dict[str, Any] | None]:
    if not isinstance(outcome, ProviderTransportOutcome):
        return [], {"category": "INVALID_TRANSPORT_OUTCOME"}
    if outcome.status_code != 200 or outcome.error_state:
        return [], {"category": "HTTP_OR_TRANSPORT_FAILURE", "http_status": outcome.status_code}
    if not isinstance(outcome.payload, Mapping) or outcome.payload.get("success") is not True:
        return [], {"category": "INVALID_PROVIDER_SUCCESS"}
    data = outcome.payload.get("data")
    items = data.get("items") if isinstance(data, Mapping) else None
    if not isinstance(items, list):
        return [], {"category": "UNSUPPORTED_RESPONSE_SHAPE"}
    result: list[dict[str, int | float]] = []
    previous: int | None = None
    for index, item in enumerate(items):
        if not isinstance(item, Mapping):
            return [], {"category": "UNSUPPORTED_ROW", "index": index}
        raw_time = next((item[key] for key in ("unixTime", "unix_time", "timestamp") if key in item), None)
        try:
            timestamp = int(raw_time)
        except (TypeError, ValueError):
            return [], {"category": "INVALID_TIMESTAMP", "index": index}
        values = [_positive(next((item[key] for key in keys if key in item), None)) for keys in (("o", "open"), ("h", "high"), ("l", "low"), ("c", "close"))]
        if timestamp % 60 or timestamp < start // 60 * 60 or timestamp >= end or previous is not None and timestamp <= previous:
            return [], {"category": "INVALID_OR_UNORDERED_TIMESTAMP", "index": index}
        if any(value is None for value in values):
            return [], {"category": "INVALID_MCAP_OHLC", "index": index}
        opening, high, low, close = values
        if low > min(opening, high, close) or high < max(opening, low, close):
            return [], {"category": "INCONSISTENT_MCAP_OHLC", "index": index}
        result.append({"timestamp": timestamp, "open_mc_usd": opening, "high_mc_usd": high, "low_mc_usd": low, "close_mc_usd": close})
        previous = timestamp
    return result, None


def _health_gate() -> None:
    url = os.environ.get("DEV014_HEALTH_URL", "http://127.0.0.1:8080/healthz")
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            payload = json.loads(response.read())
    except Exception as exc:  # A health read failure is a hard stop before debit.
        raise SystemExit("HEALTH_GATE_UNAVAILABLE") from exc
    if response.status != 200 or payload.get("healthy") is not True or payload.get("db") != "ok" or payload.get("wal_warn") is not False:
        raise SystemExit("HEALTH_GATE_DENIED")


def _write_artifact(output: Path, artifact: dict[str, Any]) -> None:
    raw = json.dumps(artifact, indent=2, sort_keys=True) + "\n"
    if len(raw.encode()) > MAX_ARTIFACT_BYTES:
        raise SystemExit("ARTIFACT_BOUND_EXCEEDED")
    temporary = output.with_name("." + output.name + ".tmp")
    temporary.write_text(raw)
    os.replace(temporary, output)


def _records() -> list[dict[str, Any]]:
    anchors = json.loads(ANCHORS.read_text())
    records = anchors.get("batch_4_provider_free_plan")
    contract = anchors.get("batch_4_contract") or {}
    if not isinstance(records, list) or tuple(item.get("rank") for item in records) != EXPECTED_RANKS:
        raise SystemExit("FROZEN_BATCH_4_RANKS_INVALID")
    if contract.get("max_requests") != 10 or contract.get("concurrency") != 1 or contract.get("retries") != 0 or contract.get("pagination") is not False or contract.get("fallback") is not False:
        raise SystemExit("FROZEN_BATCH_4_CONTRACT_INVALID")
    return records


def main() -> int:
    output = Path(os.environ.get("DEV014_BATCH4_OUTPUT", str(OUTPUT)))
    if output.parent != ROOT / "docs/audits" or output.exists():
        raise SystemExit("BATCH_4_OUTPUT_PATH_UNAVAILABLE")
    if not os.environ.get("BIRDEYE", "").strip():
        raise SystemExit("AUTHORITATIVE_BIRDEYE_CREDENTIAL_REQUIRED")
    queue_root = Path(os.environ.get("OPERATION_MONITOR_QUEUE_PATH", "database/evidence_platform/operation_monitor_jobs"))
    selected = _records()
    population = {item["mint"]: item for item in json.loads(POPULATION.read_text())["launches"]}
    if len({item["mint"] for item in selected}) != 10 or any(item["mint"] not in population for item in selected):
        raise SystemExit("FROZEN_BATCH_4_IDENTITY_INVALID")

    artifact: dict[str, Any] = {
        "artifact_type": "DEV014_WATCHTOWER_RECENT_FIRST_PRICE_FORENSICS_BATCH_4",
        "version": 1,
        "provider": "BIRDEYE",
        "research_class": "NON_CANONICAL_HISTORICAL_PRICE_FORENSICS",
        "source_anchor_sha256": hashlib.sha256(ANCHORS.read_bytes()).hexdigest(),
        "request_contract": {"endpoint": "/defi/v3/ohlcv", "chart_type": "mcap", "currency": "usd", "type": "1m", "mode": "range", "padding": False, "max_requests": 10, "concurrency": 1, "retries": 0, "pagination": False, "fallback": False},
        "records": [],
        "retention": {"aggregate_max_bytes": MAX_ARTIFACT_BYTES, "raw_provider_payload_retention": False, "unbounded_growth_paths": 0},
    }
    budget = HistoricalForensicsBudgetAdmission(queue_root)
    transport = BirdeyeProductionBinding(credential_label="BIRDEYE")
    for anchor in selected:  # Deliberate serial, one-attempt execution.
        mint, start = anchor["mint"], int(anchor["anchor_timestamp"])
        params = {"address": mint, "chart_type": "mcap", "currency": "usd", "type": "1m", "mode": "range", "padding": "false", "time_from": start, "time_to": start + 3600}
        request_identity = _digest({"batch": "DEV014_BATCH_4", "mint": mint, "params": params})
        # Persist an intent before debit.  If the operator process disappears,
        # the existing output blocks a rerun rather than risking a duplicate
        # paid request.  This is compact research evidence, not a second budget
        # ledger and contains neither payload nor credentials.
        intent = {"mint": mint, "chronological_rank": anchor["rank"], "request_identity": request_identity, "admission_state": "INTENT_RECORDED"}
        artifact["records"].append(intent)
        _write_artifact(output, artifact)
        _health_gate()
        try:
            budget.admit(mint=mint, request_identity=request_identity)
        except BudgetDenied as exc:
            intent["admission_state"] = "DENIED"
            intent["admission_error"] = str(exc)
            _write_artifact(output, artifact)
            raise SystemExit("SHARED_PROVIDER_BUDGET_DENIED") from exc
        outcome = transport({"endpoint": "/defi/v3/ohlcv", "request_parameters": params})
        rows, failure = _normalize(outcome, start=start, end=start + 3600)
        launch = population[mint]; opening = ((launch.get("evidence") or {}).get("opening") or {})
        record = {"mint": mint, "chronological_rank": anchor["rank"], "anchor": {"type": anchor["anchor_class"], "timestamp": start, "provenance": anchor.get("anchor_source", opening.get("provenance"))}, "request_identity": request_identity, "requested_window": {"time_from": start, "time_to": start + 3600, "interval": "1m"}, "provider": "BIRDEYE", "http_status": outcome.status_code, "provider_metadata": {str(key).lower(): str(value) for key, value in outcome.response_headers.items() if str(key).lower() in HEADER_ALLOWLIST}, "normalization_status": "FAILED", "failure": failure}
        if failure is None:
            candles = [{"timestamp": int(row["timestamp"]), "low_mc_usd": float(row["low_mc_usd"])} for row in rows]
            if anchor["anchor_class"] == "QUALIFIED_ENTRY_ANCHOR":
                record["entry_relative_observed_minima"] = observed_minima(entry_timestamp=start, entry_mc_usd=float(opening["entry_mc_usd"]), candles=candles, provider_provenance="BIRDEYE_1M_MCAP")["results"]
            else:
                record["observed_price_relative_extrema"] = observed_minima(entry_timestamp=start, entry_mc_usd=float(rows[0]["open_mc_usd"]), candles=candles, provider_provenance="BIRDEYE_1M_MCAP")["results"]
            record["returned_candle_count"] = len(rows); record["normalization_status"] = "NORMALIZED"; record["failure"] = None
        record["evidence_identity"] = _digest(record)
        artifact["records"][-1] = record
        _write_artifact(output, artifact)
    artifact["summary"] = {"request_count": len(artifact["records"]), "normalized_count": sum(item.get("normalization_status") == "NORMALIZED" for item in artifact["records"])}
    _write_artifact(output, artifact)
    print(json.dumps({"output": str(output), **artifact["summary"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
