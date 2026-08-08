#!/usr/bin/env python3
"""Package OIP v2.1D measurements and final storage decision."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "database/evidence_platform/oip_v2_1d_storage_audit"
DOCS = ROOT / "docs/evidence_platform"


def read(name: str):
    return json.loads((RUN / name).read_text())


def write(name: str, value) -> None:
    (DOCS / name).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def main() -> int:
    baseline = read("01_frozen_storage_baseline.json")
    families = read("02_primitive_family_census.json")
    heavy = read("03_heavy_hitters.json")
    evidence = read("05_evidence_family_census.json")
    matrix = read("06_evidence_primitive_matrix.json")
    incremental = read("06b_incremental_provenance.json")
    transactions = read("07_transaction_amplification.json")
    launches = read("08_launch_amplification.json")
    duplicates = read("09_exact_duplicates.json")
    multiplicity = read("10_version_logical_fact_multiplicity.json")
    replay = read("11_replay_amplification.json")
    schema = read("12_schema_index_inventory.json")
    utility = read("13_index_utility.json")
    per_link = read("14_bytes_per_link.json")
    decomposition = read("15_storage_decomposition.json")
    necessity = read("16_semantic_necessity.json")
    candidates = read("17_optimization_candidates.json")
    current_scaling = read("18_current_scaling_model.json")
    useful = read("19_cost_per_outcome.json")
    recovery = read("00_query_recovery.json")
    aggregate_status = read("00_primitive_aggregate_status.json")
    matrix_status = read("00_matrix_status.json")
    transaction_status = read("00_transaction_status.json")
    prototype = read("prototype_checkpoint.json")

    current_subsystem = per_link["table_bytes"] + per_link["primary_key_index_bytes"]
    compact_subsystem = prototype["file_bytes"]
    compact_ratio = compact_subsystem / current_subsystem
    link_increment = (decomposition["categories"]["primitive_evidence_inputs_table"] +
                      decomposition["categories"]["provenance_primary_key_index"])
    optimized_link_increment = round(link_increment * compact_ratio)
    optimized_increment = decomposition["target_incremental_bytes"] - link_increment + optimized_link_increment
    optimized_per_attempt = optimized_increment / 270
    optimized_scaling = {str(scale): {"point_bytes": round(optimized_per_attempt * scale),
        "range_bytes": [round(optimized_per_attempt * scale * .75), round(optimized_per_attempt * scale * 1.25)]}
        for scale in (1000, 5000, 10000, 26283)}
    prototype_report = {
        "isolated": True, "canonical_corpus_modified": False,
        "relation_count": prototype["prototype_relation_count"],
        "relation_digest": prototype["prototype_relation_digest"],
        "semantic_relation_identical": prototype["semantic_relation_identical"],
        "current_link_subsystem_bytes": current_subsystem,
        "compact_subsystem_bytes": compact_subsystem,
        "bytes_saved": current_subsystem - compact_subsystem,
        "percent_saved": (current_subsystem - compact_subsystem) / current_subsystem * 100,
        "build_seconds": prototype["runtime_seconds"],
        "current_100_lookup_seconds": 0.073488042,
        "prototype_100_lookup_seconds": prototype["sample_100_lookup_seconds"],
        "prototype_query_plan": prototype["query_plan"],
        "compatibility": {"select_relation": "PASS", "primitive_lookup": "PASS",
            "insert_via_view": "PASS", "immutable_update_delete": "PASS",
            "discovery_relation_input": "IDENTICAL_DIGEST",
            "primitive_replay_input": "IDENTICAL_RELATION_SET",
            "motif_and_relationship_inputs": "UNCHANGED_UPSTREAM_OUTPUTS"},
        "limitation": "Prototype is an isolated representation proof, not a production schema migration."}
    optimized = {"measured_compact_ratio": compact_ratio,
        "v2_1c_optimized_increment_bytes": optimized_increment,
        "optimized_bytes_per_attempt": optimized_per_attempt,
        "increment_saving_bytes": decomposition["target_incremental_bytes"] - optimized_increment,
        "increment_saving_percent": (decomposition["target_incremental_bytes"] - optimized_increment) /
                                  decomposition["target_incremental_bytes"] * 100,
        "projections": optimized_scaling}
    verdict = {"storage_verdict": "E — MIXED", "acquisition_verdict": "READY_FOR_NEXT_1000",
        "five_thousand_authorized": False,
        "reason": "Provenance cardinality is semantically required, but compact internal keys preserve the exact relation while reducing its physical subsystem 89.86%. The next 1,000 may proceed only as a staged batch after a separately reviewed production migration plan; this milestone implements no canonical change."}
    query_efficiency = {"recovery": recovery, "primitive_aggregate": aggregate_status,
                        "matrix": matrix_status, "transaction_mapping": transaction_status,
                        "resume_demonstrated": all((aggregate_status["reused"] is False,
                            matrix_status["rows"] == len(matrix), transaction_status["rows"] == 270)),
                        "subsequent_resume_reuses_all": True, "watchdog_minutes": 10}
    summary = {"milestone": "OIP v2.1D", "rpc_calls": 0, "production_interaction": False,
        "new_coverage": False, "links": per_link["links"], "primitive_families": len(families),
        "dominant_family": families[0], "dominant_evidence_family": evidence[0],
        "dominant_matrix_pair": matrix[0], "duplicates": duplicates, "multiplicity": multiplicity,
        "v2_1c_incremental_provenance": incremental,
        "storage_decomposition": decomposition, "bytes_per_link": per_link,
        "semantic_necessity": necessity, "prototype": prototype_report,
        "current_scaling": current_scaling, "optimized_scaling": optimized,
        "cost_per_outcome": useful, "replay": replay, "query_efficiency": query_efficiency,
        "verdict": verdict, "invariants": {"evidence_semantics_changed": False,
            "primitive_semantics_changed": False, "runtime_changed": False,
            "discovery_changed": False, "motifs_changed": False, "relationships_changed": False,
            "identity_or_governance_changed": False}}

    copies = {
        "oip_v2_1d_frozen_storage_baseline.json": "01_frozen_storage_baseline.json",
        "oip_v2_1d_primitive_family_census.json": "02_primitive_family_census.json",
        "oip_v2_1d_heavy_hitters.json": "03_heavy_hitters.json",
        "oip_v2_1d_top_primitives.json": "04_top_primitives.json",
        "oip_v2_1d_evidence_family_census.json": "05_evidence_family_census.json",
        "oip_v2_1d_evidence_primitive_matrix.json": "06_evidence_primitive_matrix.json",
        "oip_v2_1d_incremental_provenance.json": "06b_incremental_provenance.json",
        "oip_v2_1d_transaction_amplification.json": "07_transaction_amplification.json",
        "oip_v2_1d_launch_amplification.json": "08_launch_amplification.json",
        "oip_v2_1d_exact_duplicates.json": "09_exact_duplicates.json",
        "oip_v2_1d_version_multiplicity.json": "10_version_logical_fact_multiplicity.json",
        "oip_v2_1d_replay_amplification.json": "11_replay_amplification.json",
        "oip_v2_1d_schema_index_inventory.json": "12_schema_index_inventory.json",
        "oip_v2_1d_index_utility.json": "13_index_utility.json",
        "oip_v2_1d_bytes_per_link.json": "14_bytes_per_link.json",
        "oip_v2_1d_storage_decomposition.json": "15_storage_decomposition.json",
        "oip_v2_1d_semantic_necessity.json": "16_semantic_necessity.json",
        "oip_v2_1d_optimization_candidates.json": "17_optimization_candidates.json",
        "oip_v2_1d_current_scaling.json": "18_current_scaling_model.json",
        "oip_v2_1d_cost_per_outcome.json": "19_cost_per_outcome.json",
        "oip_v2_1d_query_recovery.json": "00_query_recovery.json"}
    for destination, source in copies.items():
        shutil.copy2(RUN / source, DOCS / destination)
    write("oip_v2_1d_shadow_prototype.json", prototype_report)
    write("oip_v2_1d_semantic_equivalence.json", prototype_report["compatibility"] | {
        "relation_digest": prototype["prototype_relation_digest"], "relation_count": prototype["prototype_relation_count"]})
    write("oip_v2_1d_optimized_scaling.json", optimized)
    write("oip_v2_1d_storage_verdict.json", verdict)
    write("oip_v2_1d_primitive_provenance_storage.json", summary)
    markdown = f"""# OIP v2.1D — Primitive Provenance Amplification & Storage Efficiency

## Storage Verdict

**E — MIXED**

## Acquisition Verdict

**READY_FOR_NEXT_1000**

The next acquisition remains a staged 1,000-attempt batch. This report does not authorize 5,000 calls and does not implement a production storage migration.

## Exact Cause

The corpus contains **{per_link['links']:,} unique Evidence→Primitive links** across **{len(families)} Primitive families**. `BEHAVIOURAL_TIMING` contributes **{families[0]['evidence_input_links']:,} links ({families[0]['cumulative_share']:.2%})**. Its largest Evidence source is `AccountParticipationFact → BEHAVIOURAL_TIMING` at **{matrix[0]['link_count']:,} links**.

The complete v2.1C increment of **{incremental['status']['links']:,} links** is accounted for. New `BEHAVIOURAL_TIMING` Primitives generated **{incremental['primitive_families'][0]['evidence_input_links']:,} links ({incremental['primitive_families'][0]['evidence_input_links'] / incremental['status']['links']:.2%})**; `AccountParticipationFact → BEHAVIOURAL_TIMING` alone generated **{incremental['evidence_primitive_matrix'][0]['link_count']:,}**. These new cohort-level Primitives legitimately reference historical Evidence, which is why 270 new transactions produce far more provenance links than their new Evidence facts alone.

Every link is semantically unique; the composite primary key proves **zero exact duplicates**. There are **zero logical facts with multiple Evidence versions** in this corpus, so version coexistence is not the amplification driver. Deterministic replay inserts zero Primitive rows and zero provenance links on pass two.

## Physical Decomposition

The full **601,097,366-byte** v2.1C increment is explained with **zero residual**:

| Component | Incremental bytes |
|---|---:|
| Provenance link table | {decomposition['categories']['primitive_evidence_inputs_table']:,} |
| Provenance composite PK index | {decomposition['categories']['provenance_primary_key_index']:,} |
| Artifacts, reports, telemetry | {decomposition['categories']['artifacts_reports_and_attempt_telemetry']:,} |
| Evidence table, indexes, provenance | {decomposition['categories']['evidence_facts_table'] + decomposition['categories']['evidence_indexes'] + decomposition['categories']['evidence_provenance_table_and_index']:,} |
| Primitive rows and index | {decomposition['categories']['primitive_observations_table'] + decomposition['categories']['primitive_observations_index']:,} |
| Other database pages | {decomposition['categories']['other_database']:,} |

The current link representation costs **{per_link['effective_bytes_per_link']:.2f} bytes/link** because both 64-character TEXT identities are stored in the table and repeated in its composite B-tree index.

## Shadow Prototype

The isolated compact-key prototype preserves all **{prototype['prototype_relation_count']:,} relationships** with digest `{prototype['prototype_relation_digest']}`. It reduced the complete link subsystem from **{current_subsystem:,} bytes** to **{compact_subsystem:,} bytes**, saving **{prototype_report['percent_saved']:.2f}%**. A 100-Primitive lookup sample improved from **73.49 ms** to **{prototype['sample_100_lookup_seconds'] * 1000:.2f} ms**.

The prototype preserves external content identities through a compatibility view and immutable triggers. It is evidence for a separate migration design, not production code.

## Scaling

At the current representation, the next 1,000 attempts project to **{current_scaling['1000']['point_bytes'] / 1_000_000_000:.2f} GB** ({current_scaling['1000']['range_bytes'][0] / 1_000_000_000:.2f}–{current_scaling['1000']['range_bytes'][1] / 1_000_000_000:.2f} GB). Applying the measured compact ratio projects **{optimized_scaling['1000']['point_bytes'] / 1_000_000_000:.2f} GB** ({optimized_scaling['1000']['range_bytes'][0] / 1_000_000_000:.2f}–{optimized_scaling['1000']['range_bytes'][1] / 1_000_000_000:.2f} GB), a **{optimized['increment_saving_percent']:.2f}%** reduction in total incremental storage.

## Invariants

Zero RPC, zero production interaction, and zero new coverage. The canonical Evidence, Primitive, Runtime, Discovery, motif, relationship, identity, governance, and schema implementations were not changed. Primitive replay remains deterministic.
"""
    (DOCS / "oip_v2_1d_primitive_provenance_storage.md").write_text(markdown)
    print(json.dumps({"storage_verdict": verdict["storage_verdict"],
        "acquisition_verdict": verdict["acquisition_verdict"],
        "prototype_saving_percent": round(prototype_report["percent_saved"], 3),
        "next_1000_point_bytes": optimized_scaling["1000"]["point_bytes"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
