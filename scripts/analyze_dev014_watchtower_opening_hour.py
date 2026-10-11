#!/usr/bin/env python3
"""Provider-free descriptive analysis of DEV-014 opening-hour evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from statistics import median
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "docs/audits/dev014_watchtower_opening_hour_reconciliation_20261011.v1.json"
OUTPUT = ROOT / "docs/audits/dev014_watchtower_opening_hour_price_behaviour_20261011.v1.json"
MAX_BYTES = 1_000_000


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _summary(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "min": None, "p10": None, "p25": None, "median": None, "p75": None, "p90": None, "max": None}
    ordered = sorted(values)
    def q(fraction: float) -> float: return ordered[round((len(ordered) - 1) * fraction)]
    return {"count": len(ordered), "min": ordered[0], "p10": q(.10), "p25": q(.25),
            "median": median(ordered), "p75": q(.75), "p90": q(.90), "max": ordered[-1]}


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _qualified_creation(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if row["acquisition_status"] == "COMPLETED_QUALIFIED_OBSERVATION"
            and row["anchor_type"] == "CREATION_TIME_ANCHORED_OBSERVATION"]


def _hour_values(rows: list[dict[str, Any]], coverage: str | None = None) -> list[dict[str, Any]]:
    out = []
    for row in _qualified_creation(rows):
        hour = row["windows"].get("3600")
        first = _number(row.get("first_observed_mc_usd"))
        if not hour or first is None or first <= 0 or (coverage and hour.get("coverage") != coverage):
            continue
        low, high, high_at = _number(hour.get("minimum_mc_usd")), _number(hour.get("maximum_mc_usd")), hour.get("maximum_timestamp")
        if low is None or high is None:
            continue
        out.append({"row": row, "first": first, "low": low, "high": high,
                    "drawdown_pct": (first - low) * 100 / first, "peak_multiple": high / first,
                    "time_to_peak_s": int(high_at - row["creation_timestamp"]) if isinstance(high_at, int) else None,
                    "missing_candles": int(hour.get("missing_candles") or 0)})
    return out


def _window_statistics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    result = {}
    for window in ("300", "900", "1800", "3600"):
        values = []
        for row in _qualified_creation(rows):
            item = row["windows"].get(window)
            if not item or item.get("coverage") != "COMPLETE_OBSERVED_WINDOW":
                continue
            low, high = _number(item.get("minimum_mc_usd")), _number(item.get("maximum_mc_usd"))
            first = _number(row.get("first_observed_mc_usd"))
            if low is not None and high is not None and first and first > 0:
                values.append((low, high, (first-low)*100/first, high/first))
        result[window] = {"complete_records": len(values), "minimum_mc_usd": _summary([x[0] for x in values]),
                          "maximum_mc_usd": _summary([x[1] for x in values]),
                          "drawdown_percent": _summary([x[2] for x in values]),
                          "peak_multiple": _summary([x[3] for x in values])}
    return result


def _chronological_bands(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(_qualified_creation(rows), key=lambda row: row["rank"])
    bands = []
    for start in range(0, len(ordered), 100):
        part = ordered[start:start+100]
        bands.append({"rank_range": [part[0]["rank"], part[-1]["rank"]], "records": len(part),
                      "first_observed_mc_usd": _summary([float(row["first_observed_mc_usd"]) for row in part if _number(row.get("first_observed_mc_usd")) is not None])})
    return bands


def _outliers(items: list[dict[str, Any]], key: str, reverse: bool) -> list[dict[str, Any]]:
    selected = sorted(items, key=lambda item: item[key], reverse=reverse)[:5]
    return [{"rank": item["row"]["rank"], "mint": item["row"]["mint"], "value": item[key],
             "evidence_identity": item["row"]["evidence_identity"]} for item in selected]


def build(dataset: dict[str, Any], input_hash: str) -> dict[str, Any]:
    rows = dataset["records"]
    creation = _qualified_creation(rows)
    opening = [float(row["first_observed_mc_usd"]) for row in creation if _number(row.get("first_observed_mc_usd")) is not None]
    complete = _hour_values(rows, "COMPLETE_OBSERVED_WINDOW")
    partial = _hour_values(rows)
    partial = [item for item in partial if item["row"]["windows"]["3600"].get("coverage") != "COMPLETE_OBSERVED_WINDOW"]
    drawdown = [item["drawdown_pct"] for item in complete]
    multiple = [item["peak_multiple"] for item in complete]
    draw_q, multiple_q = _summary(drawdown), _summary(multiple)
    clusters = {"LIMITED_DRAWDOWN_EARLY_EXPANSION": 0, "HIGH_DRAWDOWN": 0, "LOW_EXPANSION": 0, "MIXED_MIDRANGE": 0}
    for item in complete:
        if item["drawdown_pct"] <= draw_q["p25"] and item["peak_multiple"] >= multiple_q["p75"]:
            clusters["LIMITED_DRAWDOWN_EARLY_EXPANSION"] += 1
        elif item["drawdown_pct"] >= draw_q["p75"]:
            clusters["HIGH_DRAWDOWN"] += 1
        elif item["peak_multiple"] <= multiple_q["p25"]:
            clusters["LOW_EXPANSION"] += 1
        else:
            clusters["MIXED_MIDRANGE"] += 1
    opening_s = _summary(opening)
    repeated_bands = {"at_or_below_p10": sum(v <= opening_s["p10"] for v in opening),
                      "p10_to_p25": sum(opening_s["p10"] < v <= opening_s["p25"] for v in opening),
                      "p25_to_p75": sum(opening_s["p25"] < v <= opening_s["p75"] for v in opening),
                      "p75_to_p90": sum(opening_s["p75"] < v <= opening_s["p90"] for v in opening),
                      "above_p90": sum(v > opening_s["p90"] for v in opening)}
    coverage_bias = {}
    for name, values in (("complete_first_hour", complete), ("partial_first_hour", partial)):
        coverage_bias[name] = {"records": len(values), "first_observed_mc_usd": _summary([x["first"] for x in values]),
                               "observed_minimum_mc_usd": _summary([x["low"] for x in values]),
                               "observed_maximum_mc_usd": _summary([x["high"] for x in values]),
                               "observed_drawdown_percent": _summary([x["drawdown_pct"] for x in values]),
                               "observed_peak_multiple": _summary([x["peak_multiple"] for x in values]),
                               "missing_candles": _summary([float(x["missing_candles"]) for x in values])}
    return {
        "artifact_type": "DEV014_WATCHTOWER_OPENING_HOUR_PRICE_BEHAVIOUR_ANALYSIS", "version": "v1",
        "input_dataset_sha256": input_hash, "frozen_population": dataset["frozen_population"],
        "method": {"provider_free": True, "creation_time_statistics_only": True,
                   "complete_window_rule": "Only COMPLETE_OBSERVED_WINDOW 60-minute creation-time observations support first-hour extrema/drawdown/peak-timing findings.",
                   "clusters": "Empirical p25/p75 thresholds; descriptive only and not operation attribution.",
                   "no_imputation": True, "no_lifecycle_ath_or_terminal_claims": True},
        "opening_mc_distribution": {"qualified_creation_time_records": len(creation), "first_observed_mc_usd": opening_s,
                                    "repeated_empirical_valuation_bands": repeated_bands, "chronological_rank_bands": _chronological_bands(rows)},
        "opening_hour_behaviour": {"complete_first_hour_records": len(complete), "window_statistics": _window_statistics(rows),
                                    "time_to_observed_first_hour_peak_seconds": _summary([float(x["time_to_peak_s"]) for x in complete if x["time_to_peak_s"] is not None])},
        "repeated_price_patterns": {"complete_window_drawdown_percent": draw_q, "complete_window_peak_multiple": multiple_q,
                                    "clusters": {"thresholds": {"drawdown_p25": draw_q["p25"], "drawdown_p75": draw_q["p75"], "peak_multiple_p25": multiple_q["p25"], "peak_multiple_p75": multiple_q["p75"]}, "counts": clusters}},
        "coverage_bias": {"comparison": coverage_bias,
                          "limitation": "Partial windows report only observed extrema. Missing candles can hide lower minima, higher maxima, and change peak timing; they are excluded from complete-window conclusions."},
        "outliers": {"highest_opening_mc": _outliers([{"row": r, "first": float(r["first_observed_mc_usd"]), "low": 0, "high": 0, "drawdown_pct": 0, "peak_multiple": 0} for r in creation if _number(r.get("first_observed_mc_usd")) is not None], "first", True),
                     "lowest_opening_mc": _outliers([{"row": r, "first": float(r["first_observed_mc_usd"]), "low": 0, "high": 0, "drawdown_pct": 0, "peak_multiple": 0} for r in creation if _number(r.get("first_observed_mc_usd")) is not None], "first", False),
                     "largest_complete_hour_drawdown": _outliers(complete, "drawdown_pct", True),
                     "largest_complete_hour_peak_multiple": _outliers(complete, "peak_multiple", True)},
        "evidence_quality": dataset["coverage"],
        "storage_contract": {"raw_provider_payload_retention": False, "max_artifact_bytes": MAX_BYTES,
                             "single_file_500mb_or_more_allowed": False, "unbounded_growth_paths": 0},
    }


def write(path: Path, value: dict[str, Any]) -> None:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
    if len(raw.encode()) >= MAX_BYTES: raise ValueError("COMPACT_ANALYSIS_BOUND_EXCEEDED")
    temporary = path.with_suffix(path.suffix + ".tmp")
    if temporary.exists() or path.is_symlink(): raise ValueError("OUTPUT_PATH_UNSAFE")
    temporary.write_text(raw, encoding="utf-8"); os.replace(temporary, path)


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--input", type=Path, default=INPUT); parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args(); write(args.output, build(json.loads(args.input.read_text()), _sha(args.input)))


if __name__ == "__main__": main()
