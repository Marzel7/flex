#!/usr/bin/env python3
"""Provider-free topology and observed-collapse analysis for DEV-014 evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from pathlib import Path
from statistics import median
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.reconcile_dev014_watchtower_opening_hour import (
    COHORT_JOURNAL, HISTORICAL_JOURNALS, _journal_records,
)
from src.ops.watchtower_observed_minimum import _expected_buckets


INPUT = ROOT / "docs/audits/dev014_watchtower_opening_hour_reconciliation_20261011.v1.json"
OUTPUT = ROOT / "docs/audits/dev014_watchtower_missing_candle_collapse_analysis_20261011.v1.json"
MAX_BYTES = 1_000_000


def _sha(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest()
def _num(value: Any) -> float | None: return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None
def _median(values: list[float]) -> float | None: return median(values) if values else None


def _observation(record: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    if isinstance(record.get("creation_time_anchored_observation"), dict): return "CREATION_TIME", record["creation_time_anchored_observation"]
    if isinstance(record.get("entry_relative_observed_minima"), dict): return "ENTRY", {"results": record["entry_relative_observed_minima"]}
    if isinstance(record.get("observed_price_relative_extrema"), dict): return "OBSERVED_PRICE", {"results": record["observed_price_relative_extrema"]}
    return None, None


def _runs(items: list[int]) -> list[list[int]]:
    out: list[list[int]] = []
    for value in sorted(items):
        if not out or value != out[-1][-1] + 60: out.append([value])
        else: out[-1].append(value)
    return out


def _topology(expected: tuple[int, ...], missing: list[int], invalid: list[int]) -> dict[str, Any]:
    missing_set, invalid_set = set(missing), set(invalid)
    valid = [bucket for bucket in expected if bucket not in missing_set and bucket not in invalid_set]
    regions = _runs([bucket for bucket in expected if bucket in missing_set])
    if not missing_set: category = "INDETERMINATE"
    else:
        kinds = set()
        for region in regions:
            if region[0] == expected[0]: kinds.add("LEADING")
            elif region[-1] == expected[-1]: kinds.add("TRAILING")
            else: kinds.add("INTERNAL")
        category = next(iter(kinds)) if len(kinds) == 1 else "MIXED"
    trailing = len(regions[-1]) if regions and regions[-1][-1] == expected[-1] else 0
    first_sustained = next((region[0] for region in regions if len(region) >= 2), None)
    if first_sustained is None and regions: first_sustained = regions[0][0]
    last_valid = valid[-1] if valid else None
    after_last = sum(bucket > last_valid for bucket in missing_set) if last_valid is not None else 0
    return {"topology": category, "first_valid_offset_s": valid[0] - expected[0] if valid else None,
            "last_valid_offset_s": last_valid - expected[0] if last_valid is not None else None,
            "last_valid_timestamp": last_valid, "missing_minutes": len(missing_set),
            "longest_missing_run_minutes": max((len(x) for x in regions), default=0),
            "trailing_missing_seconds": trailing * 60, "first_sustained_missing_timestamp": first_sustained,
            "missing_after_last_valid_percent": (after_last * 100 / len(missing_set)) if missing_set else None,
            "valid_candles_resume_after_first_missing": bool(first_sustained is not None and any(bucket > first_sustained for bucket in valid))}


def _range(record: dict[str, Any], topology: dict[str, Any]) -> dict[str, Any]:
    _, observation = _observation(record)
    hour = (observation or {}).get("results", {}).get("3600", {})
    peak, trough = _num(hour.get("observed_maximum_mc_usd")), _num(hour.get("observed_minimum_mc_usd"))
    peak_at, trough_at = hour.get("observed_maximum_timestamp"), hour.get("observed_minimum_timestamp")
    if peak is None or trough is None or peak <= 0:
        return {"range_drawdown_percent": None, "timing": "INSUFFICIENT_TIMING_EVIDENCE",
                "collapse_before_missing": "INSUFFICIENT_TIMING_EVIDENCE", "last_observed_mc_usd": None}
    drawdown = (peak - trough) * 100 / peak
    if not isinstance(peak_at, int) or not isinstance(trough_at, int): timing = "INSUFFICIENT_TIMING_EVIDENCE"
    elif trough_at < peak_at: timing = "TROUGH_BEFORE_PEAK"
    else: timing = "PEAK_THEN_TROUGH"
    first_missing = topology["first_sustained_missing_timestamp"]
    if timing != "PEAK_THEN_TROUGH" or first_missing is None: classification = "INSUFFICIENT_TIMING_EVIDENCE"
    elif drawdown < 90: classification = "NO_SEVERE_COLLAPSE"
    elif trough_at < first_missing: classification = "COLLAPSE_BEFORE_MISSING"
    elif first_missing < peak_at: classification = "MISSING_BEFORE_COLLAPSE"
    else: classification = "INTERLEAVED"
    return {"observed_peak_mc_usd": peak, "observed_peak_timestamp": peak_at, "observed_minimum_mc_usd": trough,
            "observed_minimum_timestamp": trough_at, "range_drawdown_percent": drawdown, "timing": timing,
            "peak_to_minimum_seconds": trough_at - peak_at if timing == "PEAK_THEN_TROUGH" else None,
            "collapse_to_last_valid_seconds": topology["last_valid_timestamp"] - trough_at if timing == "PEAK_THEN_TROUGH" and topology["last_valid_timestamp"] is not None else None,
            "last_observed_mc_usd": None, "collapse_before_missing": classification}


def _severity(value: float | None) -> str:
    if value is None: return "UNAVAILABLE"
    if value < 50: return "LT_50"
    if value < 80: return "50_TO_80"
    if value < 90: return "80_TO_90"
    if value < 95: return "90_TO_95"
    if value < 99: return "95_TO_99"
    return "GTE_99"


def _wilson(success: int, total: int) -> list[float | None]:
    if not total: return [None, None]
    z, n, p = 1.96, float(total), success / total
    denom = 1 + z*z/n; centre = (p + z*z/(2*n))/denom
    delta = z * ((p*(1-p)/n + z*z/(4*n*n)) ** .5) / denom
    return [max(0., centre-delta), min(1., centre+delta)]


def build(records: dict[str, dict[str, Any]], dataset_hash: str) -> dict[str, Any]:
    partial, complete = [], []
    for record in records.values():
        if record.get("state") != "COMPLETED" or record.get("normalization_status") != "NORMALIZED": continue
        anchor, observation = _observation(record)
        if not observation: continue
        hour = observation.get("results", {}).get("3600")
        timestamp = record.get("anchor", {}).get("timestamp")
        if not isinstance(hour, dict) or not isinstance(timestamp, int): continue
        coverage = hour.get("coverage_status")
        if coverage not in {"COMPLETE_OBSERVED_WINDOW", "PROVIDER_MISSING_COVERAGE", "TRUNCATION_SUSPECTED"}: continue
        topology = _topology(_expected_buckets(timestamp, 3600), list(hour.get("missing_bucket_timestamps", [])), list(hour.get("invalid_bucket_timestamps", [])))
        entry = {"rank": record["chronological_rank"], "mint": record["mint"], "anchor_type": anchor,
                 "coverage_status": coverage, "evidence_identity": record["evidence_identity"], **topology, **_range(record, topology)}
        (complete if coverage == "COMPLETE_OBSERVED_WINDOW" else partial).append(entry)
    partial.sort(key=lambda item: item["rank"]); complete.sort(key=lambda item: item["rank"])
    for entry in partial + complete: entry["severity"] = _severity(entry["range_drawdown_percent"])
    complete_severe = sum((entry["range_drawdown_percent"] or 0) >= 90 for entry in complete)
    partial_severe = sum((entry["range_drawdown_percent"] or 0) >= 90 for entry in partial)
    classifications = Counter(entry["collapse_before_missing"] for entry in partial)
    topology = Counter(entry["topology"] for entry in partial)
    severe = [entry for entry in partial if (entry["range_drawdown_percent"] or 0) >= 90]
    nonsevere = [entry for entry in partial if (entry["range_drawdown_percent"] or 0) < 90]
    severe_trailing = sum(entry["trailing_missing_seconds"] > 0 for entry in severe)
    nonsevere_trailing = sum(entry["trailing_missing_seconds"] > 0 for entry in nonsevere)
    ordered = [entry for entry in partial if entry["collapse_before_missing"] == "COLLAPSE_BEFORE_MISSING"]
    contradict = [entry for entry in partial if entry["collapse_before_missing"] in {"MISSING_BEFORE_COLLAPSE", "NO_SEVERE_COLLAPSE"}]
    return {"artifact_type":"DEV014_WATCHTOWER_MISSING_CANDLE_COLLAPSE_ANALYSIS", "version":"v1", "input_dataset_sha256":dataset_hash,
            "method":{"provider_free":True,"expected_bucket_semantics":"Existing wholly-post-anchor minute buckets; missing/invalid timestamps only.",
                      "range_drawdown":"(observed 60-minute high - observed 60-minute low) / observed high; coverage does not prove true extrema.",
                      "temporal_collapse":"Requires retained peak timestamp before retained trough timestamp; no causal inference.","last_observed_mc_usd":"Not retained; deliberately unavailable."},
            "missing_candle_topology":{"partial_windows":len(partial),"counts":dict(topology),"trailing_missing_frequency":sum(e["trailing_missing_seconds"]>0 for e in partial),
                "median_missing_minutes":_median([float(e["missing_minutes"]) for e in partial]),"median_trailing_missing_seconds":_median([float(e["trailing_missing_seconds"]) for e in partial]),
                "median_last_valid_offset_s":_median([float(e["last_valid_offset_s"]) for e in partial if e["last_valid_offset_s"] is not None])},
            "collapse_severity":{"complete":{"n":len(complete),"counts":dict(Counter(e["severity"] for e in complete)),"severe_90pct_or_more":complete_severe,"proportion":complete_severe/len(complete) if complete else None,"wilson_95":_wilson(complete_severe,len(complete))},
                "partial":{"n":len(partial),"counts":dict(Counter(e["severity"] for e in partial)),"severe_90pct_or_more":partial_severe,"proportion":partial_severe/len(partial) if partial else None,"wilson_95":_wilson(partial_severe,len(partial))},
                "partial_vs_complete":{"risk_difference":partial_severe/len(partial)-complete_severe/len(complete) if partial and complete else None,"risk_ratio":(partial_severe/len(partial))/(complete_severe/len(complete)) if complete_severe else None}},
            "collapse_before_missing":{"counts":dict(classifications),"sensitivity":{"severe_80pct_or_more":sum((e["range_drawdown_percent"] or 0)>=80 for e in partial),"severe_95pct_or_more":sum((e["range_drawdown_percent"] or 0)>=95 for e in partial)},
                "median_peak_to_minimum_seconds":_median([float(e["peak_to_minimum_seconds"]) for e in ordered if e["peak_to_minimum_seconds"] is not None]),"representative":ordered[:5],"contradictory_cases":contradict[:5]},
            "severe_trailing_association":{"severe_90pct_or_more":{"n":len(severe),"trailing":severe_trailing,"proportion":severe_trailing/len(severe) if severe else None,"wilson_95":_wilson(severe_trailing,len(severe))},
                "below_90pct":{"n":len(nonsevere),"trailing":nonsevere_trailing,"proportion":nonsevere_trailing/len(nonsevere) if nonsevere else None,"wilson_95":_wilson(nonsevere_trailing,len(nonsevere))},
                "risk_difference":severe_trailing/len(severe)-nonsevere_trailing/len(nonsevere) if severe and nonsevere else None,
                "risk_ratio":(severe_trailing/len(severe))/(nonsevere_trailing/len(nonsevere)) if nonsevere_trailing else None},
            "post_collapse_observability":{"valid_candles_resume_after_first_missing":sum(e["valid_candles_resume_after_first_missing"] for e in partial),"partial_windows":len(partial),"last_observed_mc_unavailable":True},
            "limitations":["Missing buckets are evidence gaps, not proof of no trading or no price movement.","Compact evidence retains extrema and bucket topology, not full candle paths or last observed MC.","Partial-window highs/lows can understate or overstate true extrema; temporal ordering is observational, not causal.","No common-controller, manipulation, or membership inference is made from price similarity or missingness."],
            "storage_contract":{"raw_provider_payload_retention":False,"max_artifact_bytes":MAX_BYTES,"single_file_500mb_or_more_allowed":False,"unbounded_growth_paths":0}}


def write(path: Path, value: dict[str, Any]) -> None:
    raw=json.dumps(value,sort_keys=True,separators=(",", ":"))+"\n"
    if len(raw.encode())>=MAX_BYTES: raise ValueError("COMPACT_ANALYSIS_BOUND_EXCEEDED")
    temporary=path.with_suffix(path.suffix+".tmp")
    if temporary.exists() or path.is_symlink(): raise ValueError("OUTPUT_PATH_UNSAFE")
    temporary.write_text(raw,encoding="utf-8"); os.replace(temporary,path)


def main() -> None:
    parser=argparse.ArgumentParser(); parser.add_argument("--output",type=Path,default=OUTPUT); parser.add_argument("--input",type=Path,default=INPUT)
    args=parser.parse_args(); records,_=_journal_records(HISTORICAL_JOURNALS,COHORT_JOURNAL); write(args.output,build(records,_sha(args.input)))


if __name__ == "__main__": main()
