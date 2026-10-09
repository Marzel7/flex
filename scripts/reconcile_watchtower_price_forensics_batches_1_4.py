#!/usr/bin/env python3
"""Build the provider-free DEV-014 Batch 1--4 reconciliation and next plans."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
AUDITS = ROOT / "docs" / "audits"
POPULATION = AUDITS / "dev014_watchtower_forensic_population_v2_20261009.v1.json"
ANCHORS = AUDITS / "dev014_watchtower_historical_research_anchor_qualification_20261009.v1.json"
OUTPUT = AUDITS / "dev014_watchtower_recent_first_price_forensics_reconciliation_and_batch_5_20261009.v1.json"
BATCHES = tuple(AUDITS / f"dev014_watchtower_recent_first_price_forensics_batch_{number}_20261009.v1.json" for number in range(1, 5))
MAX_BYTES = 1_000_000


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def request(mint: str, timestamp: int, batch: str) -> dict[str, Any]:
    params = {
        "address": mint,
        "chart_type": "mcap",
        "currency": "usd",
        "type": "1m",
        "mode": "range",
        "padding": "false",
        "time_from": timestamp,
        "time_to": timestamp + 3600,
    }
    return {
        "endpoint": "GET /defi/v3/ohlcv",
        "params": params,
        "request_identity": digest({"batch": batch, "mint": mint, "params": params}),
    }


def minima(record: dict[str, Any]) -> dict[str, Any]:
    for key in ("observed_minima", "entry_relative_observed_minima", "observed_price_relative_observed_minima", "observed_price_relative_extrema"):
        value = record.get(key)
        if isinstance(value, dict):
            return value
    return {}


def artifact() -> dict[str, Any]:
    population = load(POPULATION)
    anchors = load(ANCHORS)
    batch_payloads = [load(path) for path in BATCHES]
    records = [record for payload in batch_payloads for record in payload["records"]]
    by_mint = {launch["mint"]: launch for launch in population["launches"]}
    ordered = population["reconciliation"]["most_recent_70_mints"]
    rank_by_mint = {mint: rank for rank, mint in enumerate(ordered, 1)}

    request_ids = [record["request_identity"] for record in records]
    evidence_ids = [record["evidence_identity"] for record in records]
    coverage_by_status: dict[str, int] = {"COMPLETE_OBSERVED_WINDOW": 0, "NO_VALID_CANDLES": 0}
    complete_windows = partial_windows = no_valid_candle_outcomes = 0
    missing_bucket_count = 0
    for record in records:
        for value in minima(record).values():
            status = value["coverage_status"]
            coverage_by_status[status] = coverage_by_status.get(status, 0) + 1
            if status == "COMPLETE_OBSERVED_WINDOW":
                complete_windows += 1
            elif value.get("valid_bucket_count", 0) == 0:
                no_valid_candle_outcomes += 1
            else:
                partial_windows += 1
            missing_bucket_count += len(value.get("missing_bucket_timestamps", []))

    acquired = {record["mint"] for record in records}
    equivalent = {"9JtPfLbN32CszHmstoXYdWaazFLaoxhfqf9mKgQMpump", "CQP3cfwbUCfBJ6UUY6GS2pozcn6uNJv6WYYVAW2fpump", "EFGKcRHyLbcUv9hfSDd1m3oEdJcs8DBu9wYA13Qpump"}
    ranks_1_40 = ordered[:40]
    missing_ranks = [rank for rank, mint in enumerate(ranks_1_40, 1) if mint not in acquired and mint not in equivalent]

    catchup = []
    for item in anchors["skipped_batch_3"]:
        start = item["anchor_timestamp"]
        catchup.append({
            "rank": item["rank"], "mint": item["mint"], "creation_timestamp": item["creation_timestamp"],
            "anchor": {"class": item["anchor_class"], "timestamp": start, "source": item["anchor_source"]},
            "proposed_request": request(item["mint"], start, "DEV014_BATCH_3_CATCHUP"),
            "disposition": "ELIGIBLE_FROZEN_CATCHUP_REQUEST",
        })

    batch5 = []
    for rank in range(41, 51):
        mint = ordered[rank - 1]
        launch = by_mint[mint]
        creation = launch["creation"]
        opening = launch.get("evidence", {}).get("opening", {})
        if opening.get("status") == "QUALIFIED":
            timestamp = opening["entry_timestamp"]
            anchor = {"class": "QUALIFIED_ENTRY_ANCHOR", "timestamp": timestamp, "provenance": opening["provenance"]}
            disposition = "ELIGIBLE_FROZEN_BATCH_5_REQUEST"
            proposed = request(mint, timestamp, "DEV014_BATCH_5")
            skip_reason = None
        else:
            anchor = {"class": "NO_QUALIFIED_ENTRY_OR_OBSERVED_PRICE_ANCHOR", "timestamp": None, "provenance": None}
            disposition = "INELIGIBLE_NO_QUALIFIED_RESEARCH_ANCHOR"
            proposed = None
            skip_reason = "No qualified Entry anchor or separately qualified observed-price anchor is retained."
        batch5.append({
            "rank": rank, "mint": mint, "creation_timestamp": creation["timestamp"],
            "creation_timestamp_status": creation["chronology_status"], "anchor": anchor,
            "existing_evidence": {"opening_status": opening.get("status", "UNAVAILABLE"), "lifecycle_status": launch["lifecycle_status"]},
            "disposition": disposition, "skip_reason": skip_reason, "proposed_request": proposed,
        })

    return {
        "artifact_type": "DEV014_WATCHTOWER_HISTORICAL_RECONCILIATION_AND_BATCH_5_PLAN",
        "version": "v1", "frozen_population": "WATCHTOWER_FORENSIC_POPULATION_V2_20261009",
        "inputs": {str(path.relative_to(ROOT)): sha256(path) for path in (POPULATION, ANCHORS, *BATCHES)},
        "reconciliation": {
            "chronological_rank_range": [1, 40], "total_mints": 40,
            "newly_acquired_mints": len(acquired.intersection(ranks_1_40)),
            "equivalent_retained_research_mints": sorted(equivalent.intersection(ranks_1_40)),
            "missing_one_minute_evidence_ranks": missing_ranks,
            "qualified_entry_anchor_records": sum(1 for record in records if record.get("observed_drawdown_scope") == "QUALIFIED_ENTRY_REFERENCE" or record.get("anchor", {}).get("type", "").startswith("QUALIFIED_ENTRY")),
            "observed_price_anchor_records": sum(1 for record in records if record.get("anchor", {}).get("type") == "OBSERVED_PRICE_ANCHOR"),
            "complete_windows": complete_windows, "partial_windows": partial_windows,
            "no_valid_candle_outcomes": no_valid_candle_outcomes, "coverage_by_status": coverage_by_status,
            "missing_bucket_count": missing_bucket_count,
            "duplicate_request_identities": len(request_ids) - len(set(request_ids)),
            "duplicate_evidence_identities": len(evidence_ids) - len(set(evidence_ids)),
        },
        "catchup_batch": {"batch": "DEV014_BATCH_3_CATCHUP", "max_provider_requests": 4, "records": catchup},
        "batch_5": {"batch": "DEV014_BATCH_5", "chronological_rank_range": [41, 50], "max_provider_requests": 10, "records": batch5},
        "operational_contract": {
            "provider_free_plan_only": True, "shared_budget": "20-global / 4-per-mint via existing atomic admission", "health_gate_before_each_request": True,
            "concurrency": 1, "retries": 0, "pagination": False, "fallback": False, "raw_provider_payload_retention": False,
            "max_compact_artifact_bytes": MAX_BYTES, "single_file_500mb_or_more_allowed": False, "unbounded_growth_paths": 0,
            "forensic_semantics": "Entry-relative drawdown only for qualified Entry anchors; observed-price research remains separate; missing candles remain unresolved.",
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    rendered = json.dumps(artifact(), sort_keys=True, indent=2) + "\n"
    if len(rendered.encode()) > MAX_BYTES:
        raise SystemExit("artifact exceeds compact storage limit")
    args.output.write_text(rendered, encoding="utf-8")


if __name__ == "__main__":
    main()
