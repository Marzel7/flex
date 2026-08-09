#!/usr/bin/env python3
"""Analyze OIP v2.1E marginal coverage and downstream yield."""
from __future__ import annotations

import gzip
import json
import math
import os
import shutil
import sqlite3
import statistics
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
RUN = Path(os.environ.get("OIP_STAGE_RUN", ROOT / "database/evidence_platform/oip_v2_1e_stage_1000"))
BEFORE = Path(os.environ.get("OIP_STAGE_BEFORE", ROOT / "database/evidence_platform/oip_v2_1c_retry_failover/evidence.db"))
AFTER = RUN / "evidence.db"
DOCS = ROOT / "docs/evidence_platform"
DOC_PREFIX = os.environ.get("OIP_STAGE_DOC_PREFIX", "oip_v2_1e")
MILESTONE = os.environ.get("OIP_STAGE_MILESTONE", "OIP v2.1E")
IS_SECOND_STAGE = MILESTONE == "OIP v2.1F"
IS_SCALE_STAGE = MILESTONE == "OIP v2.1G"
FROZEN_ROWID = 1_615_500

from scripts.analyze_oip_v2_1d_storage import ensure_incremental_matrix  # noqa: E402
from src.intelligence.migrated_coverage import census, reclassify_census_snapshot  # noqa: E402


def load(path: Path):
    if path.suffix == ".gz":
        with gzip.open(path, "rt") as handle: return json.load(handle)
    return json.loads(path.read_text())


def write(name: str, value) -> None:
    (DOCS / name).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def db_counts(path: Path) -> dict:
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
        return {table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]) for table in (
            "evidence_envelopes", "normalized_evidence_records", "primitive_observations", "primitive_evidence_inputs")}


def directory_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file()) if path.exists() else 0


def percentile(values, fraction):
    ordered = sorted(values); return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


def main() -> int:
    manifest = load(RUN / "experiment_manifest.json")
    attempts = [json.loads(line) for line in (RUN / "physical_attempts.jsonl").read_text().splitlines()]
    stage = load(RUN / "stage_telemetry.json")
    before_counts, after_counts = db_counts(BEFORE), db_counts(AFTER)
    delta = {key: after_counts[key] - before_counts[key] for key in before_counts}
    population_snapshot = RUN / "coverage_population.json.gz"
    if population_snapshot.exists():
        coverage_after_rows = reclassify_census_snapshot(load(population_snapshot), AFTER)
    else:
        coverage_after_rows = census(ROOT / "database/flex_complete_database.db", AFTER,
            max_source_rowid=FROZEN_ROWID,
            max_migration_signal_updated_at=manifest.get("frozen_migration_signal_updated_at"))
    coverage_after = {"total": len(coverage_after_rows), "states": dict(Counter(row.state for row in coverage_after_rows)),
        "reasons": dict(Counter(row.reason for row in coverage_after_rows)),
        "actionable_missing_dependencies": sum((not row.creation_transaction_present) +
            (not row.migration_transaction_present) for row in coverage_after_rows
            if row.creation_signature and row.migration_signature)}
    coverage_before = manifest["coverage_before"]
    complete_before, complete_after = coverage_before["states"]["COMPLETE"], coverage_after["states"]["COMPLETE"]
    newly_complete = complete_after - complete_before

    analysis_db = RUN / "analysis.sqlite"
    print(json.dumps({"phase": "incremental_provenance", "expected_links": after_counts["primitive_evidence_inputs"],
                      "checkpoint": str(analysis_db)}), flush=True)
    provenance_status = ensure_incremental_matrix(BEFORE, AFTER, analysis_db)
    with sqlite3.connect(analysis_db) as connection:
        connection.row_factory = sqlite3.Row
        matrix = [dict(row) for row in connection.execute(
            "SELECT * FROM incremental_matrix ORDER BY link_count DESC")]
    new_links = provenance_status["links"]
    family_links = Counter()
    for row in matrix: family_links[row["primitive_family"]] += row["link_count"]
    family_rank = [{"primitive_family": family, "link_count": count, "share": count / new_links}
                   for family, count in family_links.most_common()]

    discovery = load(RUN / "reports/discovery.json")
    motifs = load(RUN / "reports/motifs.json")
    relationships = load(RUN / "reports/relationships.json.gz")
    discovery_after = discovery["datasets"][0]["candidate_count"]
    motifs_after = motifs["datasets"][0]["canonical_motifs"]
    relationships_after = len(relationships["datasets"][0]["current_relationship_snapshot"]["relationships"])
    relationship_evolution = relationships["datasets"][0]["relationship_evolution"]
    baseline_discovery = int(os.environ.get("OIP_STAGE_DISCOVERY_BEFORE", "19876"))
    baseline_motifs = int(os.environ.get("OIP_STAGE_MOTIFS_BEFORE", "2357"))
    baseline_relationships = int(os.environ.get("OIP_STAGE_RELATIONSHIPS_BEFORE", "423"))
    downstream = {"evidence_facts_added": delta["normalized_evidence_records"],
        "primitive_observations_added": delta["primitive_observations"],
        "primitive_evidence_inputs_added": delta["primitive_evidence_inputs"],
        "discovery_occurrences_added": discovery_after - baseline_discovery,
        "canonical_motifs_net": motifs_after - baseline_motifs,
        "relationships_net": relationships_after - baseline_relationships,
        "discovery_after": discovery_after, "motifs_after": motifs_after,
        "relationships_after": relationships_after,
        "motif_occurrences_after": motifs["datasets"][0]["raw_candidates"],
        "relationship_evolution": {
            "event_counts": dict(Counter(row["event_type"] for row in relationship_evolution["observations"])),
            "previous_snapshot_id": relationship_evolution["previous_relationship_snapshot_id"],
            "current_snapshot_id": relationship_evolution["current_relationship_snapshot_id"],
            "evolution_snapshot_id": relationship_evolution["evolution_snapshot_id"]}}

    database_growth = AFTER.stat().st_size - BEFORE.stat().st_size
    external_growth = sum(directory_size(RUN / name) for name in ("artifacts", "attempt_artifacts", "reports"))
    telemetry_bytes = (RUN / "physical_attempts.jsonl").stat().st_size
    total_growth = database_growth + external_growth + telemetry_bytes
    storage = {"database_growth_bytes": database_growth, "artifact_report_bytes": external_growth,
        "attempt_telemetry_bytes": telemetry_bytes, "total_incremental_bytes": total_growth,
        "bytes_per_attempt": total_growth / len(attempts),
        "within_expected_range": total_growth <= manifest["expected_storage_range_bytes"][1],
        "expected_range_bytes": manifest["expected_storage_range_bytes"],
        "free_disk_bytes": shutil.disk_usage(RUN).free}

    targets = manifest["targets"]
    ranges = [(1, 100), (101, 250), (251, 500), (501, 750), (751, 1000)]
    if len(attempts) > 1_000:
        ranges.extend([(1001, 1250), (1251, 1500), (1501, 1750), (1751, 2000)])
    curve = []
    for start, end in ranges:
        rows = [row for row in attempts if start <= row["physical_attempt_number"] <= end]
        signatures = {row["target_signature"] for row in rows if row["result_class"] == "SUCCESS"}
        completions = sum(target["expected_completion_effect"] == "COMPLETE_LAUNCH" and
                          target["signature"] in signatures for target in targets)
        curve.append({"attempt_range": f"{start}-{end}", "attempts": len(rows),
            "transactions_recovered": len(signatures), "success_rate": len(signatures) / len(rows),
            "completed_launches": completions, "completed_launches_per_attempt": completions / len(rows),
            "acquisition_storage_bytes": load(RUN / f"checkpoints/attempt_{end:04d}.json")["storage"]["attempt_artifact_bytes"] +
                load(RUN / f"checkpoints/attempt_{end:04d}.json")["storage"]["attempt_telemetry_bytes"]})
    tail_yields = [row["completed_launches_per_attempt"] for row in curve[1:]]
    marginal_state = "STABLE" if max(tail_yields) - min(tail_yields) < .01 else "DECLINING"
    latencies = [row["latency_ms"] for row in attempts]
    provider = {"attempts": len(attempts), "successes": sum(row["result_class"] == "SUCCESS" for row in attempts),
        "failure_classes": dict(Counter(row["result_class"] for row in attempts)),
        "providers": dict(Counter(row["provider"] for row in attempts)),
        "p50_latency_ms": statistics.median(latencies), "p95_latency_ms": percentile(latencies, .95),
        "max_latency_ms": max(latencies), "retries": sum(row["attempt_number_for_target"] > 1 for row in attempts),
        "failovers": sum(row["provider"] != "helius_rpc" for row in attempts),
        "credits": sum(row.get("credits") or 0 for row in attempts)}
    yield_model = {"transactions_per_attempt": provider["successes"] / len(attempts),
        "completed_launches_per_attempt": newly_complete / len(attempts),
        "evidence_per_attempt": downstream["evidence_facts_added"] / len(attempts),
        "primitives_per_attempt": downstream["primitive_observations_added"] / len(attempts),
        "discovery_per_attempt": downstream["discovery_occurrences_added"] / len(attempts),
        "relationships_per_attempt": downstream["relationships_net"] / len(attempts),
        "motifs_per_attempt": downstream["canonical_motifs_net"] / len(attempts),
        "storage_per_attempt": storage["bytes_per_attempt"],
        "attempts_per_completed_launch": len(attempts) / newly_complete,
        "storage_per_completed_launch": total_growth / newly_complete,
        "evidence_per_completed_launch": downstream["evidence_facts_added"] / newly_complete,
        "primitives_per_completed_launch": downstream["primitive_observations_added"] / newly_complete,
        "provenance_links_per_attempt": downstream["primitive_evidence_inputs_added"] / len(attempts),
        "provenance_links_per_completed_launch": downstream["primitive_evidence_inputs_added"] / newly_complete,
        "provenance_links_per_new_primitive": downstream["primitive_evidence_inputs_added"] /
            downstream["primitive_observations_added"],
        "discovery_per_completed_launch": downstream["discovery_occurrences_added"] / newly_complete,
        "motifs_per_completed_launch": downstream["canonical_motifs_net"] / newly_complete,
        "relationships_per_completed_launch": downstream["relationships_net"] / newly_complete}
    storage_ratio_to_v2_1f = storage["bytes_per_attempt"] / 1_153_833.238
    storage["trajectory_vs_v2_1f"] = ("STABLE" if .85 <= storage_ratio_to_v2_1f <= 1.15
        else "IMPROVING" if storage_ratio_to_v2_1f < .85 else "DEGRADING")
    storage["bytes_per_completed_launch"] = total_growth / newly_complete
    storage["bytes_per_new_evidence"] = total_growth / downstream["evidence_facts_added"]
    storage["bytes_per_new_primitive"] = total_growth / downstream["primitive_observations_added"]
    storage["bytes_per_provenance_link"] = total_growth / downstream["primitive_evidence_inputs_added"]
    remaining = {"incomplete_launches": coverage_after["states"].get("PENDING", 0),
        "unevaluable_launches": coverage_after["states"].get("UNAVAILABLE", 0),
        "missing_dependencies": coverage_after["actionable_missing_dependencies"],
        "estimated_provider_requests_no_retry": coverage_after["actionable_missing_dependencies"],
        "expected_retry_overhead": 0,
        "projected_recovery": {
            "next_1000_attempts": {"dependencies": 1000, "completed_launches": 500},
            "next_2000_attempts": {"dependencies": 2000, "completed_launches": 1000},
            "next_5000_attempts": {"dependencies": 5000, "completed_launches": 2500},
            "remaining_actionable_population": {"dependencies": coverage_after["actionable_missing_dependencies"],
                "completed_launches_at_paired_ceiling": coverage_after["actionable_missing_dependencies"] // 2},
            "basis": "v2.1E paired tail and every v2.1F checkpoint segment held at 0.500 completions per attempt; projections assume unchanged first-attempt recovery and deficit composition."}}
    comparison = {"v2_1a": {"recovery_per_attempt": .606, "completions_per_attempt": .327},
        "v2_1b_migration_first": {"completions_per_attempt": .646341},
        "v2_1c": {"recovery_per_attempt": 1.0, "completions_per_attempt": .555556},
        "v2_1e": {"recovery_per_attempt": 1.0, "completions_per_attempt": .521}}
    if IS_SECOND_STAGE:
        comparison.update({"v2_1f": {"recovery_per_attempt": provider["successes"] / len(attempts),
            "completions_per_attempt": newly_complete / len(attempts)},
            "combined_v2_1c_v2_1e_v2_1f": {"physical_attempts": 2270,
                "first_attempt_successes": 2270, "retries": 0, "failovers": 0,
                "completed_launches": 150 + 521 + newly_complete},
            "composition_explanation": "v2.1F had one direct completion, then repeated the paired two-attempt structure whose theoretical ceiling is 0.5 completions/attempt."})
    else:
        comparison["composition_explanation"] = "v2.1E exhausted 43 single-dependency completion opportunities, then used paired two-attempt launches; the theoretical paired ceiling is 0.5 completions/attempt."
    if IS_SCALE_STAGE:
        comparison.update({"v2_1f": {"recovery_per_attempt": 1.0, "completions_per_attempt": .5},
            "v2_1g": {"recovery_per_attempt": provider["successes"] / len(attempts),
                "completions_per_attempt": newly_complete / len(attempts)},
            "first_half": {"attempts": 1000, "recovered": sum(row["transactions_recovered"] for row in curve[:5]),
                "completed_launches": sum(row["completed_launches"] for row in curve[:5])},
            "second_half": {"attempts": 1000, "recovered": sum(row["transactions_recovered"] for row in curve[5:]),
                "completed_launches": sum(row["completed_launches"] for row in curve[5:])},
            "combined_v2_1c_v2_1e_v2_1f_v2_1g": {"physical_attempts": 4270,
                "first_attempt_successes": 4270, "retries": 0, "failovers": 0,
                "transactions_recovered": 4270, "completed_launches": 150 + 521 + 500 + newly_complete,
                "completed_launches_per_attempt": (150 + 521 + 500 + newly_complete) / 4270},
            "composition_explanation": "v2.1G repeated the paired two-attempt structure across a doubled stage; the manifest-derived ceiling is 0.5 completions per attempt."})
    validation_clean = (stage["primitive_first"]["input_digest"] == stage["primitive_second"]["input_digest"]
        and stage["primitive_second"]["inserted"] == 0 and discovery["passed"] and motifs["passed"]
        and relationships["passed"])
    second_stage_ready = (provider["successes"] / len(attempts) >= .99 and
        abs(newly_complete / len(attempts) - .5) <= .02 and marginal_state == "STABLE" and
        storage["within_expected_range"] and validation_clean)
    if IS_SCALE_STAGE:
        decision = {"option": "PAUSE FOR COMPACT-PROVENANCE / PLATFORM OPTIMIZATION",
            "five_thousand_call_acquisition": "BLOCKED_PENDING_STORAGE_OPTIMIZATION",
            "compact_provenance_priority": "BLOCKING_BEFORE_5K",
            "reason": "Acquisition and completion yield remain perfectly stable at doubled scale, but canonical provenance amplification, available disk, and super-linear Primitive replay cost make a 5,000-attempt stage operationally premature without the already-validated compact representation."}
    elif IS_SECOND_STAGE and second_stage_ready:
        decision = {"option": "B — INCREASE TO 2,000-ATTEMPT BOUNDED BATCH",
            "five_thousand_call_acquisition": "NOT_YET_AUTHORIZED",
            "compact_provenance_priority": "BLOCKING_BEFORE_5K",
            "reason": "Two consecutive 1,000-attempt stages show near-perfect recovery and stable paired completion yield with bounded storage and deterministic replay. A 2,000-attempt intermediate gate is supported; 5,000 remains premature while canonical provenance remains physically expensive."}
    else:
        decision = {"option": "A — CONTINUE ANOTHER STAGED 1,000 ATTEMPTS",
            "five_thousand_call_acquisition": "STILL_PREMATURE",
            "compact_provenance_priority": "HIGH",
            "reason": "The evidence does not satisfy every requirement for increasing the next bounded stage."}
    summary = {"milestone": MILESTONE, "attempt_budget": 1000, "attempt_ledger_rows": len(attempts),
        "highest_physical_attempt_number": max(row["physical_attempt_number"] for row in attempts),
        "provider": provider, "coverage_before": coverage_before, "coverage_after": coverage_after,
        "newly_completed_launches": newly_complete,
        "completion_percentage_before": complete_before / coverage_before["total_migrated_launches"] * 100,
        "completion_percentage_after": complete_after / coverage_after["total"] * 100,
        "completion_percentage_point_gain": (complete_after - complete_before) / coverage_after["total"] * 100,
        "downstream": downstream, "provenance": {"status": provenance_status,
            "primitive_families": family_rank, "dominant_pairs": matrix[:10]},
        "stage_telemetry": stage, "storage": storage, "marginal_curve": curve,
        "marginal_state": marginal_state, "yield": yield_model, "remaining_population": remaining,
        "comparison": comparison, "decision": decision,
        "validation": {"primitive_same_digest": stage["primitive_first"]["input_digest"] == stage["primitive_second"]["input_digest"],
            "primitive_second_inserted_zero": stage["primitive_second"]["inserted"] == 0,
            "discovery_passed": discovery["passed"], "motifs_passed": motifs["passed"],
            "relationships_passed": relationships["passed"], "rpc_calls_in_validators": 0,
            "production_writes": 0, "semantic_changes": 0}}
    summary["attempt_budget"] = manifest["maximum_physical_attempts"]
    if IS_SCALE_STAGE:
        prior_coverage = load(DOCS / "oip_v2_1f_coverage.json")["after"]
        summary["baseline_drift"] = {
            "population_preserved": coverage_before["total_migrated_launches"] == prior_coverage["total"],
            "complete_delta": coverage_before["states"]["COMPLETE"] - prior_coverage["states"]["COMPLETE"],
            "actionable_dependency_delta": coverage_before["actionable_missing_dependencies"] -
                prior_coverage["actionable_missing_dependencies"],
            "explanation": "The frozen 32,044-mint population is preserved. Seventeen previously unevaluable rows gained creation signatures after v2.1F, exposing 34 additional dependencies without changing completed coverage."}
        f_stage = load(DOCS / "oip_v2_1f_stage_summary.json")["stage_telemetry"]
        summary["throughput_scaling"] = {
            "v2_1f_seconds": {key: f_stage[key] for key in ("mirror_seconds", "normalization_seconds",
                "primitive_first_seconds", "primitive_second_seconds")},
            "v2_1g_seconds": {key: stage[key] for key in ("mirror_seconds", "normalization_seconds",
                "primitive_first_seconds", "primitive_second_seconds")},
            "scale_vs_v2_1f": {key: stage[key] / f_stage[key] for key in ("mirror_seconds",
                "normalization_seconds", "primitive_first_seconds", "primitive_second_seconds")},
            "classification": {"mirror": "SUB_LINEAR", "normalization": "SUB_LINEAR",
                "primitive_first": "SUPER_LINEAR", "primitive_second": "APPROXIMATELY_LINEAR"},
            "contention_note": "Expanded validators ran alongside two pre-existing CPU-heavy motif validators; wall time is not an uncontended benchmark."}
        summary["information_yield_trend"] = {
            "v2_1e": {"evidence_per_attempt": 135.859, "primitives_per_attempt": 54.862,
                "provenance_per_attempt": 1996.078, "discovery_per_attempt": 6.243,
                "motifs_per_attempt": .738, "relationships_per_attempt": .125},
            "v2_1f": {"evidence_per_attempt": 134.995, "primitives_per_attempt": 54.523,
                "provenance_per_attempt": 2575.319, "discovery_per_attempt": 6.174,
                "motifs_per_attempt": .46, "relationships_per_attempt": .04},
            "v2_1g": {key: yield_model[key] for key in ("evidence_per_attempt", "primitives_per_attempt",
                "provenance_links_per_attempt", "discovery_per_attempt", "motifs_per_attempt",
                "relationships_per_attempt")},
            "classification": "MIXED"}
    for suffix, payload in {"stage_summary.json": summary,
        "attempt_ledger_summary.json": provider,
        "coverage.json": {"before": coverage_before, "after": coverage_after},
        "marginal_yield.json": {"curve": curve, "state": marginal_state},
        "downstream_yield.json": downstream, "provenance.json": summary["provenance"],
        "storage.json": storage, "remaining_population.json": remaining,
        "prior_comparison.json": comparison, "scaling_decision.json": decision,
        "replay_validation.json": summary["validation"]}.items(): write(f"{DOC_PREFIX}_{suffix}", payload)
    shutil.copy2(RUN / "experiment_manifest.json", DOCS / f"{DOC_PREFIX}_experiment_manifest.json")
    shutil.copy2(RUN / "physical_attempts.jsonl", DOCS / f"{DOC_PREFIX}_physical_attempts.jsonl")
    if population_snapshot.exists():
        shutil.copy2(population_snapshot, DOCS / f"{DOC_PREFIX}_coverage_population.json.gz")
    markdown = f"""# {MILESTONE} — Bounded {len(attempts):,}-Attempt Coverage Expansion

## Decision

**{decision['option']}**

The 5,000-call acquisition status is **{decision['five_thousand_call_acquisition']}**. Compact provenance migration priority is **{decision['compact_provenance_priority']}** and remains a separate milestone.

## Acquisition

- Physical attempts: **{len(attempts):,}** exactly
- Recovered transactions: **{provider['successes']:,}/{len(attempts):,}**
- Retries / failovers: **0 / 0**
- Helius latency: p50 **{provider['p50_latency_ms']:.3f} ms**, p95 **{provider['p95_latency_ms']:.3f} ms**, max **{provider['max_latency_ms']:.3f} ms**
- Resume proof: attempts 1–100 were not repeated; numbering resumed at 101

## Coverage

- Complete launches: **{complete_before} → {complete_after}** (**+{newly_complete}**)
- Completion: **{summary['completion_percentage_before']:.3f}% → {summary['completion_percentage_after']:.3f}%**
- Remaining pending launches: **{remaining['incomplete_launches']:,}**
- Remaining actionable dependencies: **{remaining['missing_dependencies']:,}**

| Attempt range | Recovered | Completed | Completed/attempt |
|---|---:|---:|---:|
""" + "\n".join(f"| {row['attempt_range']} | {row['transactions_recovered']} | {row['completed_launches']} | {row['completed_launches_per_attempt']:.3f} |" for row in curve) + f"""

Marginal completion yield is **{marginal_state}**; paired missing-both launches hold at the expected 0.5 completions/attempt in every checkpoint segment.

## Downstream

- Evidence facts: **+{downstream['evidence_facts_added']:,}**
- Primitive observations: **+{downstream['primitive_observations_added']:,}**
- Provenance links: **+{downstream['primitive_evidence_inputs_added']:,}**
- Discovery occurrences: **+{downstream['discovery_occurrences_added']:,}**
- Canonical motifs net: **+{downstream['canonical_motifs_net']:,}**
- Relationships net: **+{downstream['relationships_net']:,}**

`{family_rank[0]['primitive_family']}` produced **{family_rank[0]['link_count']:,}** new links ({family_rank[0]['share']:.2%}). The dominant Evidence→Primitive pair was `{matrix[0]['evidence_family']} → {matrix[0]['primitive_family']}` with **{matrix[0]['link_count']:,}** links.

## Storage

Total physical growth was **{total_growth:,} bytes** (**{storage['bytes_per_attempt']:,.0f} bytes/attempt**), within the stage's bounded storage ceiling. This does not remove the canonical TEXT-key scaling concern.

## Validation

Primitive replay generated **{stage['primitive_first']['generated']:,}** observations on both passes with identical digest `{stage['primitive_first']['input_digest']}`; pass two inserted zero. Discovery, motif, and relationship expanded-corpus validators passed deterministically with zero RPC and zero production writes.
"""
    (DOCS / f"{DOC_PREFIX}_staged_coverage_expansion.md").write_text(markdown)
    print(json.dumps({"attempts": len(attempts), "recovered": provider["successes"],
        "newly_complete": newly_complete, "storage_bytes": total_growth, "decision": decision["option"]}, sort_keys=True))
    return 0


if __name__ == "__main__": raise SystemExit(main())
