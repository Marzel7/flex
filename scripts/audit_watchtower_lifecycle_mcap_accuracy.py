#!/usr/bin/env python3
"""Provider-free lifecycle-MCAP accuracy audit for the retained DEV-014 evidence.

This deliberately consumes compact reconciled facts only.  It neither opens a
credential nor reaches Birdeye, and it does not reinterpret an observed range
as a chronological collapse when the retained extrema ordering is unavailable.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


VERSION = "watchtower-lifecycle-mcap-accuracy.v1"


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def chronological_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Classify retained extrema without inventing intrabar ordering."""
    result = {"PEAK_THEN_TROUGH": 0, "TROUGH_BEFORE_PEAK": 0,
              "INTRA_CANDLE_ORDER_UNKNOWN": 0, "UNAVAILABLE": 0}
    severe = {key: 0 for key in result}
    for row in rows:
        peak, trough = row.get("observed_peak_timestamp"), row.get("observed_minimum_timestamp")
        drawdown = row.get("range_drawdown_percent")
        if not (_number(peak) and _number(trough) and _number(drawdown)):
            bucket = "UNAVAILABLE"
        elif peak < trough:
            bucket = "PEAK_THEN_TROUGH"
        elif peak > trough:
            bucket = "TROUGH_BEFORE_PEAK"
        else:
            bucket = "INTRA_CANDLE_ORDER_UNKNOWN"
        result[bucket] += 1
        if _number(drawdown) and drawdown >= 90:
            severe[bucket] += 1
    return {"counts": result, "severe_90pct_or_more": severe,
            "chronological_collapse_eligible": result["PEAK_THEN_TROUGH"],
            "rule": "only a strictly earlier retained peak and later retained trough qualify; same-minute OHLC ordering is unknown"}


def build_report(reconciliation: dict[str, Any], missing: dict[str, Any]) -> dict[str, Any]:
    coverage = reconciliation["coverage"]
    severity = missing["collapse_severity"]
    partial = severity["partial"]
    complete = severity["complete"]
    chronology = chronological_summary(missing["collapse_before_missing"]["representative"] +
                                       missing["collapse_before_missing"]["contradictory_cases"])
    return {
        "artifact_type": VERSION,
        "source_inputs": {
            "reconciliation_sha256": reconciliation.get("inputs", {}).get("reconciliation_sha256"),
            "missing_candle_analysis_version": missing.get("version"),
        },
        "opening_collapse_audit": {
            "verdict": "RANGE_STATISTIC_SUPPORTED_CHRONOLOGICAL_92_9_PERCENT_CLAIM_NOT_SUPPORTED",
            "finding": "92.9% is 290/312 partial-window observed high-low ranges at or above 90%; it is not a cohort chronological-collapse prevalence.",
            "chronology_rule": chronology,
            "reason": "The retained compact artifact has only extrema timestamps for selected examples, while most partial records have missing buckets and no retained full candle path. A range cannot establish the order of peak and trough.",
        },
        "mcap_calculation_validity": {
            "provider_field": "Birdeye OHLCV market-cap candle fields (open/high/low/close), retained directly; no price-times-supply conversion in DEV-014.",
            "positive_finite_guard": "normalizer excludes non-positive and non-finite market-cap values before extrema calculation.",
            "regime_rule": "creation/curve observations, post-migration AMM observations, and Entry-relative observations remain separate regimes; no cross-regime extrema are permitted.",
            "near_zero_rule": "small positive values remain observations, not invalid zeros; they require provenance and coverage qualification rather than value imputation.",
        },
        "complete_window_denominator": {
            "all_qualified_anchor_complete_60m": coverage["first_hour_coverage_by_status"]["COMPLETE_OBSERVED_WINDOW"],
            "creation_time_complete_60m": 222,
            "difference": 20,
            "explanation": "242 counts all qualified anchor types; 222 is the creation-time-only subset. Neither is the denominator for the 312 partial-window range statistic.",
        },
        "corrected_collapse_statistics": {
            "partial_observed_range_90pct_or_more": {"numerator": partial["severe_90pct_or_more"], "denominator": partial["n"], "proportion": partial["proportion"]},
            "complete_observed_range_90pct_or_more": {"numerator": complete["severe_90pct_or_more"], "denominator": complete["n"], "proportion": complete["proportion"]},
            "claim_boundary": "Call these observed-range severities only. Do not label 92.9% a chronological collapse rate, recovery rate, ATH decline, or lifecycle loss.",
        },
        "lifecycle_metric_contract": {
            "fields": ["creation_timestamp", "first_observed_mc_and_timestamp", "migration_timestamp_if_qualified", "first_post_migration_mc_and_timestamp_if_qualified", "maximum_observed_mc_and_timestamp_by_regime", "chronological_peak_to_subsequent_trough_only_when_ordered", "decline_and_recovery_events_with_coverage", "last_observed_mc_and_timestamp", "gap_topology_and_confidence"],
            "prohibitions": ["observed maximum is not ATH", "last observed is not terminal valuation", "missing periods are not flat prices", "no curve/AMM regime mixing"],
        },
        "existing_lifecycle_coverage": {
            "retained": "Opening-hour compact OHLCV extrema/minima and explicit bucket coverage only.",
            "not_retained": "Full candle paths, durable last-observed MC, lifecycle-wide peak/terminal series, and complete migration-to-terminal coverage.",
            "capability_verdict": "UNQUALIFIED_FOR_LIFETIME_ATH_OR_TERMINAL_RECONSTRUCTION: the current retained one-hour evidence cannot establish it, and provider capability has not been proven for bounded long-range historical windows.",
        },
        "minimum_additional_request_plan": {
            "immediate_requests": 0,
            "future_gate": "Separate paid authority after a provider-free contract review and a bounded capability probe.",
            "probe": "One representative, exact mint and fixed coarse interval; retain only response metadata, normalized milestone rows, coverage counts, and content hash. First prove supported range, interval, pagination/cap semantics, and mcap field availability before any cohort scale-up.",
            "refinement": "Only targeted subranges around proven coarse extrema/gaps; each request bounded, identity-authorized, no raw payload retention, no wallet-history discovery, no unbounded pagination.",
        },
        "storage_contract": {"new_artifact_max_bytes": 1_000_000, "transient_max_bytes": 2_000_000, "new_lifecycle_cohort_max_bytes": 100_000_000, "raw_provider_retention": False, "unbounded_growth_paths": 0},
        "safety": {"provider_free": True, "credential_reads": 0, "live_database_writes": 0, "runtime_deployment_required": False,
                   "runtime_deployment_reason": "research-only derived audit; no executable runtime path changed"},
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reconciliation", type=Path, required=True)
    parser.add_argument("--missing-analysis", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    reconciliation = json.loads(args.reconciliation.read_text())
    missing = json.loads(args.missing_analysis.read_text())
    report = build_report(reconciliation, missing)
    report["source_inputs"]["reconciliation_sha256"] = sha256_path(args.reconciliation)
    report["source_inputs"]["missing_analysis_sha256"] = sha256_path(args.missing_analysis)
    report["content_sha256"] = hashlib.sha256(canonical_json(report).encode()).hexdigest()
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if len(payload.encode()) >= 1_000_000:
        raise SystemExit("DERIVED_AUDIT_FILE_LIMIT_EXCEEDED")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temp = args.output.with_suffix(args.output.suffix + ".tmp")
    temp.write_text(payload)
    temp.replace(args.output)


if __name__ == "__main__":
    main()
