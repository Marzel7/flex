#!/usr/bin/env python3
"""Freeze the provider-free DEV-014 recent-first historical research plan."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
POPULATION = ROOT / "docs/audits/dev014_watchtower_forensic_population_v2_20261009.v1.json"
PROVENANCE = ROOT / "docs/audits/dev014_watchtower_provenance_coverage_20261009.v1.json"
STAGED = ROOT / "docs/audits/dev014_watchtower_staged_provenance_recovery_plan_20261009.v1.json"
BASELINE = ROOT / "docs/audits/dev014_watchtower_price_forensics_historical_baseline_20261009.v1.json"
EARLY_PILOT = ROOT / "docs/audits/dev014_watchtower_early_minimum_mcap_pilot_20261009.v1.json"
OUTPUT = ROOT / "docs/audits/dev014_watchtower_recent_first_price_forensics_plan_20261009.v1.json"
MAX_BATCH_MINTS = 10
MAX_BATCH_ARTIFACT_BYTES = 1_000_000


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def compact_evidence(launch: dict[str, Any], provenance: dict[str, Any], staged: dict[str, Any], baseline: dict[str, Any] | None, early: dict[str, Any] | None) -> tuple[dict[str, Any], list[str]]:
    evidence = launch.get("evidence") or {}
    opening = evidence.get("opening") or {}
    peak = (evidence.get("peak_ath") or {}).get("observed_running_peak") or {}
    sharp = (evidence.get("sharp_exit") or {}).get("fifteen_minute_candidate") or {}
    output = {
        "qualified_entry_mc": bool(opening.get("status") == "QUALIFIED" or baseline),
        "entry_reference": "FROZEN_ENTRY_CONTRACT" if opening.get("status") == "QUALIFIED" else "UNAVAILABLE",
        "fifteen_minute_ohlc_candidate": bool(sharp),
        "early_minimum_observation": bool(evidence.get("early_minimum") or early),
        "observed_running_peak": bool(peak.get("status") == "QUALIFIED"),
        "qualified_final_ath": bool((evidence.get("peak_ath") or {}).get("final_ath", {}).get("status") == "QUALIFIED"),
        "thirty_second_sharp_exit_research": bool(staged.get("sharp_exit_evidence")),
        "terminal_collapse": bool((evidence.get("post_exit") or {}).get("terminal_collapse", {}).get("status") == "QUALIFIED"),
        "creation_provenance": (provenance.get("creation") or {}).get("class"),
        "migration_provenance": (provenance.get("migration") or {}).get("class"),
    }
    missing = []
    if not output["qualified_entry_mc"]: missing.append("OPENING_OR_EARLIEST_OBSERVED_MC")
    if not output["early_minimum_observation"]: missing.append("OBSERVED_MINIMA_5M_15M_30M_60M")
    if not output["observed_running_peak"]: missing.append("OBSERVED_PEAK")
    if not output["qualified_final_ath"]: missing.append("QUALIFIED_FINAL_ATH")
    if not output["fifteen_minute_ohlc_candidate"]: missing.append("15M_SHARP_EXIT_CANDIDATE")
    if not output["thirty_second_sharp_exit_research"]: missing.append("30S_SHARP_EXIT_RESEARCH")
    if not output["terminal_collapse"]: missing.append("TERMINAL_COLLAPSE")
    return output, missing


def request_plan(mint: str, creation_timestamp: int, ordinal: int) -> dict[str, Any]:
    params = {"address": mint, "chart_type": "mcap", "currency": "usd", "type": "1m", "mode": "range", "padding": "false", "time_from": creation_timestamp, "time_to": creation_timestamp + 3600}
    return {
        "request_identity": digest({"contract": "DEV014_RECENT_FIRST_EARLY_OBSERVED_WINDOW_V1", "ordinal": ordinal, **params}),
        "endpoint": "GET /defi/v3/ohlcv", "params": params,
        "purpose": "research-only earliest observed MC plus 5m/15m/30m/60m observed minima and exact gap coordinates",
        "limits": {"requests_for_mint": 1, "retries": 0, "pagination": False, "fallback": False},
    }


def main() -> None:
    if OUTPUT.exists(): raise SystemExit("PLAN_OUTPUT_ALREADY_EXISTS")
    population = json.loads(POPULATION.read_text())
    provenance = {row["mint"]: row for row in json.loads(PROVENANCE.read_text())["records"]}
    staged = {row["mint"]: row for row in json.loads(STAGED.read_text())["records"]}
    baseline = {row["mint"]: row for row in json.loads(BASELINE.read_text())["records"]}
    early = {row["mint"]: row for row in json.loads(EARLY_PILOT.read_text())["records"]}
    launches = population["launches"]
    qualified, unresolved = [], []
    for launch in launches:
        mint = launch["mint"]; creation = launch["creation"]
        if creation.get("chronology_status") == "QUALIFIED_CREATION_TIMESTAMP" and isinstance(creation.get("timestamp"), int):
            item = dict(launch)
            item["_provenance"] = provenance[mint]; item["_staged"] = staged[mint]; item["_baseline"] = baseline.get(mint); item["_early"] = early.get(mint)
            qualified.append(item)
        else:
            unresolved.append({"mint": mint, "original_classification": launch["original_classification"], "creation_status": creation.get("chronology_status"), "reason": "creation timestamp not qualified; assignment timestamp is prohibited as a substitute"})
    qualified.sort(key=lambda row: (-row["creation"]["timestamp"], row["mint"]))
    unresolved.sort(key=lambda row: row["mint"])
    if len(qualified) != 597 or len(unresolved) != 42 or len(qualified) + len(unresolved) != 639:
        raise ValueError("frozen population timestamp partition mismatch")
    priority_queue = []
    for rank, launch in enumerate(qualified, start=1):
        existing, missing = compact_evidence(launch, launch["_provenance"], launch["_staged"], launch["_baseline"], launch["_early"])
        priority_queue.append({"rank": rank, "mint": launch["mint"], "original_classification": launch["original_classification"], "creation_timestamp": launch["creation"]["timestamp"], "creation_source": launch["creation"].get("source"), "existing_evidence": existing, "missing_lifecycle_components": missing})
    eligible = [row for row in priority_queue if row["missing_lifecycle_components"]]
    def batch(name: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
        items = []
        for ordinal, row in enumerate(rows, start=1):
            request = request_plan(row["mint"], row["creation_timestamp"], ordinal) if "OBSERVED_MINIMA_5M_15M_30M_60M" in row["missing_lifecycle_components"] else None
            items.append({**row, "proposed_birdeye_window": request, "request_not_proposed_reason": None if request else "existing early-minimum evidence retained; no duplicate acquisition"})
        return {"name": name, "mint_count": len(items), "max_mints": MAX_BATCH_MINTS, "max_requests": sum(item["proposed_birdeye_window"] is not None for item in items), "concurrency": 1, "compute_unit_cost": "UNVERIFIED_BIND_TO_PAID_BIRDEYE_ACCOUNT_BEFORE_AUTHORIZATION", "storage": {"raw_provider_payload_retention": False, "compact_artifact_max_bytes": MAX_BATCH_ARTIFACT_BYTES, "unbounded_growth_paths": 0}, "stop_conditions": ["provider budget unavailable", "disk health below approved gate", "unexpected response shape", "coverage/gap ambiguity", "response exceeds compact-normalization contract", "attempted retry/pagination/fallback"], "allowlist": items}
    artifact = {
        "artifact_type": "DEV014_WATCHTOWER_RECENT_FIRST_PRICE_FORENSICS_PLAN", "version": 1, "provider_free": True,
        "inputs": {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in (POPULATION, PROVENANCE, STAGED, BASELINE, EARLY_PILOT)},
        "population": {"identity": population["cohort_identity"], "total": 639, "unified_operation": True, "labels_are_provenance_only": ["WATCHTOWER", "WATCHTOWER_DEEP"], "qualified_creation_timestamps": 597, "chronology_unqualified": 42},
        "recent_first_policy": {"sort": "creation_timestamp_desc_then_mint_asc", "assignment_timestamp_substitution": False, "historical_queue_admission": "PLAN_ONLY_NO_RUNTIME_QUEUE_WRITE", "priority_queue": priority_queue, "unresolved_queue": unresolved},
        "research_lifecycle_contract": {"class": "NON_CANONICAL_HISTORICAL_PRICE_FORENSICS", "requires": ["verified Watchtower membership", "exact mint identity", "independently timestamped provider MCAP observation", "explicit source/provenance", "coverage and missing-interval coordinates"], "prohibits": ["Entry qualification without frozen Entry contract", "lifetime ATH claim without strict coverage", "complete lifecycle claim from sparse candles", "inferred creation/migration timestamp substitution"], "evidence_classes": ["AUTHORITATIVE_OBSERVED_MINIMUM", "RESEARCH_PILOT_OBSERVATION", "NON_CANONICAL_HISTORICAL_PRICE_FORENSICS"]},
        "batches": [batch("BATCH_1", eligible[:MAX_BATCH_MINTS]), batch("BATCH_2", eligible[MAX_BATCH_MINTS:MAX_BATCH_MINTS * 2])],
        "future_target_order": ["earliest_observed_mc", "observed_minima_5m_15m_30m_60m", "early_drawdown", "chronological_recovery", "observed_peak_and_time", "sharpest_observed_red_candle", "pre_exit_and_exit_close", "entry_to_exit_when_qualified", "post_exit_recovery", "coverage_gap_coordinates"],
        "prospective_continuity": {"historical_backfill_runtime_admission": False, "new_launch_admission": "qualified Entry post-commit only", "early_minimum_feature_default": "WATCHTOWER_EARLY_MINIMUM_TRACKING_ENABLED=0", "priority": "early-minimum work defers whenever any non-early-minimum work is pending/retry/processing", "provider_budget": "shared existing 20-global/4-per-mint gate", "preserved": ["Current MC overlay", "Policy C", "terminal monitoring"]},
        "retention": {"raw_provider_payload_retention": False, "plan_artifact_max_bytes": MAX_BATCH_ARTIFACT_BYTES, "unbounded_growth_paths": 0},
    }
    artifact["evidence_identity"] = digest({key: value for key, value in artifact.items() if key != "evidence_identity"})
    raw = json.dumps(artifact, indent=2, sort_keys=True) + "\n"
    if len(raw.encode()) > MAX_BATCH_ARTIFACT_BYTES: raise ValueError("plan artifact bound exceeded")
    OUTPUT.write_text(raw)
    print(json.dumps({"output": str(OUTPUT), "bytes": len(raw.encode()), "batch_1": len(artifact["batches"][0]["allowlist"]), "batch_2": len(artifact["batches"][1]["allowlist"])}, sort_keys=True))


if __name__ == "__main__": main()
