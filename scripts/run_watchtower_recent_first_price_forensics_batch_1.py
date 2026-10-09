#!/usr/bin/env python3
"""Execute the single frozen nine-request DEV-014 Batch 1 research window."""

from __future__ import annotations

import hashlib
import json
import math
import os
import sys
from pathlib import Path
from typing import Any, Mapping

from src.ops.token_data_provider_bindings import BirdeyeProductionBinding, ProviderTransportOutcome
from src.ops.watchtower_observed_minimum import observed_minima


ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "docs/audits/dev014_watchtower_recent_first_price_forensics_plan_20261009.v1.json"
POPULATION = ROOT / "docs/audits/dev014_watchtower_forensic_population_v2_20261009.v1.json"
DEFAULT_BATCH_NAME = "BATCH_1"
DEFAULT_OUTPUT = ROOT / "docs/audits/dev014_watchtower_recent_first_price_forensics_batch_1_20261009.v1.json"
MAX_REQUESTS = 9
MAX_ARTIFACT_BYTES = 1_000_000
HEADER_ALLOWLIST = {"x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset", "x-birdeye-cu", "x-compute-units"}


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def positive(value: Any) -> float | None:
    try: number = float(value)
    except (TypeError, ValueError): return None
    return number if math.isfinite(number) and number > 0 else None


def timestamp(value: Any) -> int | None:
    if isinstance(value, bool): return None
    try: parsed = int(value)
    except (TypeError, ValueError): return None
    return parsed if isinstance(value, int) or str(parsed) == str(value) else None


def normalize(outcome: ProviderTransportOutcome, *, window_start: int, window_end: int) -> tuple[list[dict[str, float | int]], dict[str, Any] | None]:
    if not isinstance(outcome, ProviderTransportOutcome): return [], {"category": "INVALID_TRANSPORT_OUTCOME"}
    if outcome.status_code != 200 or outcome.error_state is not None: return [], {"category": "HTTP_OR_TRANSPORT_FAILURE", "http_status": outcome.status_code, "error_state": outcome.error_state}
    if not isinstance(outcome.payload, Mapping) or outcome.payload.get("success") is not True: return [], {"category": "PROVIDER_SUCCESS_STATE_INVALID"}
    data = outcome.payload.get("data")
    items = data.get("items") if isinstance(data, Mapping) else None
    if not isinstance(items, list): return [], {"category": "UNSUPPORTED_RESPONSE_SHAPE"}
    rows: list[dict[str, float | int]] = []
    previous: int | None = None
    lower = (window_start // 60) * 60
    for index, item in enumerate(items):
        if not isinstance(item, Mapping): return [], {"category": "UNSUPPORTED_ROW", "index": index}
        raw_ts = next((item[key] for key in ("unixTime", "unix_time", "timestamp") if key in item), None)
        parsed_ts = timestamp(raw_ts)
        values = [next((item[key] for key in aliases if key in item), None) for aliases in (("o", "open"), ("h", "high"), ("l", "low"), ("c", "close"))]
        parsed = [positive(value) for value in values]
        if parsed_ts is None or parsed_ts % 60 or parsed_ts < lower or parsed_ts >= window_end:
            return [], {"category": "INVALID_TIMESTAMP", "index": index}
        if previous is not None and parsed_ts <= previous: return [], {"category": "DUPLICATE_OR_OUT_OF_ORDER_TIMESTAMP", "index": index}
        if any(value is None for value in parsed): return [], {"category": "INVALID_OHLC_VALUE", "index": index}
        o, h, l, c = parsed
        if l > min(o, h, c) or h < max(o, l, c): return [], {"category": "OHLC_INCONSISTENCY", "index": index}
        rows.append({"timestamp": parsed_ts, "open_mc_usd": o, "high_mc_usd": h, "low_mc_usd": l, "close_mc_usd": c})
        previous = parsed_ts
    return rows, None


def header_metadata(outcome: ProviderTransportOutcome) -> dict[str, str]:
    return {str(key).lower(): str(value) for key, value in outcome.response_headers.items() if str(key).lower() in HEADER_ALLOWLIST}


def main() -> int:
    batch_name = os.environ.get("DEV014_BATCH_NAME", DEFAULT_BATCH_NAME)
    output = Path(os.environ.get("DEV014_BATCH_OUTPUT", str(DEFAULT_OUTPUT)))
    if output.parent != ROOT / "docs/audits": raise SystemExit("INVALID_BATCH_OUTPUT_PATH")
    if output.exists(): raise SystemExit("BATCH_OUTPUT_ALREADY_EXISTS")
    if not os.environ.get("BIRDEYE", "").strip(): raise SystemExit("AUTHORITATIVE_BIRDEYE_CREDENTIAL_REQUIRED")
    plan = json.loads(PLAN.read_text())
    batch = next(item for item in plan["batches"] if item["name"] == batch_name)
    selected = [item for item in batch["allowlist"] if item.get("proposed_birdeye_window")]
    if len(selected) != MAX_REQUESTS or batch["max_requests"] != MAX_REQUESTS:
        raise SystemExit("FROZEN_BATCH_1_ALLOWLIST_INVALID")
    if len({item["mint"] for item in selected}) != MAX_REQUESTS or len({item["proposed_birdeye_window"]["request_identity"] for item in selected}) != MAX_REQUESTS:
        raise SystemExit("FROZEN_BATCH_1_IDENTITY_INVALID")
    population = {item["mint"]: item for item in json.loads(POPULATION.read_text())["launches"]}
    transport = BirdeyeProductionBinding(credential_label="BIRDEYE")
    records = []
    for item in selected:  # deliberate concurrency one / one attempt per mint
        mint, request = item["mint"], item["proposed_birdeye_window"]
        params = request["params"]
        outcome = transport({"endpoint": "/defi/v3/ohlcv", "request_parameters": params})
        rows, failure = normalize(outcome, window_start=int(params["time_from"]), window_end=int(params["time_to"]))
        launch = population[mint]; opening = (launch.get("evidence") or {}).get("opening") or {}
        entry_qualified = opening.get("status") == "QUALIFIED"
        record: dict[str, Any] = {"mint": mint, "creation_timestamp": item["creation_timestamp"], "original_classification": item["original_classification"], "requested_window": {"time_from": params["time_from"], "time_to": params["time_to"], "interval": "1m"}, "request_identity": request["request_identity"], "provider": "BIRDEYE", "http_status": outcome.status_code, "provider_metadata": header_metadata(outcome), "qualified_entry_identity": None, "normalization_status": "FAILED", "failure": failure}
        if entry_qualified:
            record["qualified_entry_identity"] = {"timestamp": opening.get("entry_timestamp"), "mc_usd": opening.get("entry_mc_usd"), "provenance": opening.get("provenance")}
        if failure is None:
            if entry_qualified:
                candles = [{"timestamp": int(row["timestamp"]), "low_mc_usd": float(row["low_mc_usd"])} for row in rows]
                normalized = observed_minima(entry_timestamp=int(opening["entry_timestamp"]), entry_mc_usd=float(opening["entry_mc_usd"]), candles=candles, provider_provenance="BIRDEYE_1M_MCAP")
                record["observed_minima"] = normalized["results"]
                record["observed_drawdown_scope"] = "QUALIFIED_ENTRY_REFERENCE"
            else:
                first = min(rows, key=lambda row: int(row["timestamp"]), default=None)
                record["earliest_observed_mc"] = None if first is None else {"timestamp": first["timestamp"], "open_mc_usd": first["open_mc_usd"], "label": "RESEARCH_EARLIEST_OBSERVED_MC"}
                record["observed_minima"] = None; record["observed_drawdown_scope"] = "NO_QUALIFIED_ENTRY_REFERENCE"
            record["returned_candle_count"] = len(rows)
            record["normalization_status"] = "NORMALIZED"
            record["failure"] = None
        record["evidence_identity"] = digest({key: value for key, value in record.items() if key != "evidence_identity"})
        if len(canonical(record)) > 120_000: raise ValueError("COMPACT_RECORD_BOUND_EXCEEDED")
        records.append(record)
    artifact = {"artifact_type": f"DEV014_WATCHTOWER_RECENT_FIRST_PRICE_FORENSICS_{batch_name}", "version": 1, "provider": "BIRDEYE", "research_class": "NON_CANONICAL_HISTORICAL_PRICE_FORENSICS", "source_plan_sha256": hashlib.sha256(PLAN.read_bytes()).hexdigest(), "request_contract": {"endpoint": "/defi/v3/ohlcv", "max_requests": MAX_REQUESTS, "concurrency": 1, "retries": 0, "pagination": False, "fallback": False, "raw_provider_payload_retention": False}, "records": records, "summary": {"request_count": len(records), "normalized_count": sum(record["normalization_status"] == "NORMALIZED" for record in records), "qualified_entry_count": sum(record["qualified_entry_identity"] is not None for record in records), "missing_bucket_count": sum(len(result.get("missing_bucket_timestamps") or []) for record in records for result in (record.get("observed_minima") or {}).values())}, "retention": {"aggregate_max_bytes": MAX_ARTIFACT_BYTES, "raw_candle_retention": False, "unbounded_growth_paths": 0}}
    raw = json.dumps(artifact, indent=2, sort_keys=True) + "\n"
    if len(raw.encode()) > MAX_ARTIFACT_BYTES: raise ValueError("ARTIFACT_BOUND_EXCEEDED")
    output.write_text(raw)
    print(json.dumps({"output": str(output), "bytes": len(raw.encode()), **artifact["summary"]}, sort_keys=True))
    return 0


if __name__ == "__main__": sys.exit(main())
