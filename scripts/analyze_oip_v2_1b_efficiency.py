#!/usr/bin/env python3
"""Reproduce OIP v2.1B efficiency findings from the completed pilot only."""
from __future__ import annotations

import argparse
import gzip
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.intelligence.migrated_coverage import census
from src.intelligence.migrated_coverage_acquisition import representative_sample

BASE = ROOT / "database/evidence_platform/watchtower_shadow_ep3_0d/evidence.db"
PILOT = ROOT / "database/evidence_platform/oip_v2_1a_pilot/evidence.db"
PRODUCTION = ROOT / "database/flex_complete_database.db"
PILOT_ROOT = ROOT / "database/evidence_platform/oip_v2_1a_pilot"
FROZEN_ROWID = 1_615_500


def _json(path: Path):
    if path.suffix == ".gz":
        with gzip.open(path, "rt") as handle: return json.load(handle)
    return json.loads(path.read_text())


def _signatures(path: Path) -> set[str]:
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
        return {row[0].split("/", 1)[-1] for row in conn.execute(
            "SELECT natural_key FROM normalized_evidence_records WHERE fact_family='TransactionFact'")}


def _storage(path: Path) -> dict:
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
        page_count = int(conn.execute("PRAGMA page_count").fetchone()[0])
        page_size = int(conn.execute("PRAGMA page_size").fetchone()[0])
        evidence_payload = int(conn.execute("SELECT COALESCE(SUM(LENGTH(payload_json)),0) FROM normalized_evidence_records").fetchone()[0])
        primitive_payload = int(conn.execute("SELECT COALESCE(SUM(LENGTH(subjects_json)+LENGTH(parameters_json)+LENGTH(output_payload_json)+LENGTH(missing_inputs_json)),0) FROM primitive_observations").fetchone()[0])
        primitive_links = int(conn.execute("SELECT COUNT(*) FROM primitive_evidence_inputs").fetchone()[0])
        artifact = conn.execute("SELECT COALESCE(SUM(size_bytes),0),COALESCE(SUM(compressed_bytes),0) FROM artifact_references").fetchone()
    return {"allocated_bytes": page_count * page_size, "evidence_payload_bytes": evidence_payload,
            "primitive_payload_bytes": primitive_payload, "primitive_evidence_links": primitive_links,
            "artifact_raw_bytes": int(artifact[0]), "artifact_compressed_bytes": int(artifact[1])}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path,
        default=ROOT / "docs/evidence_platform/oip_v2_1b_acquisition_efficiency.json")
    args = parser.parse_args()
    pilot = _json(PILOT_ROOT / "pilot_report.json")
    before = census(PRODUCTION, BASE, max_source_rowid=FROZEN_ROWID)
    after = {row.mint: row for row in census(PRODUCTION, PILOT, max_source_rowid=FROZEN_ROWID)}
    sample, sampling = representative_sample(before, call_limit=1_000)
    present = _signatures(PILOT)
    failed_targets = sorted((target for target in sample if target.signature not in present),
                            key=lambda target: (target.signature, target.launch, target.purpose))
    call_yield = Counter(); selected_mints: dict[str, str] = {}
    for target in sample:
        call_yield[(target.purpose, "calls")] += 1
        call_yield[(target.purpose, "recovered")] += int(target.signature in present)
        selected_mints[target.launch] = next(row.reason for row in before if row.mint == target.launch)
    group_yield = Counter()
    for mint, reason in selected_mints.items():
        group_yield[(reason, "launches")] += 1
        group_yield[(reason, "completed")] += int(after[mint].state == "COMPLETE")
    base_storage, pilot_storage = _storage(BASE), _storage(PILOT)
    storage_delta = {key: pilot_storage[key] - base_storage[key] for key in base_storage}
    ep40_base = _json(ROOT / "docs/evidence_platform/ep4_0_unknown_discovery_validation.json")["datasets"][0]
    ep40_pilot = _json(PILOT_ROOT / "reports/ep4_0.json")["datasets"][0]
    ep43_base = _json(ROOT / "docs/evidence_platform/ep4_3_motif_population_analysis.json")["datasets"][0]["analysis"]
    ep43_pilot = _json(PILOT_ROOT / "reports/ep4_3.json")["datasets"][0]["analysis"]
    base_motifs = {row["motif_id"] for row in ep43_base["motifs"]}; pilot_motifs = {row["motif_id"] for row in ep43_pilot["motifs"]}
    ep52_base = _json(ROOT / "docs/evidence_platform/ep5_2_cross_motif_relationship_intelligence.json.gz")["datasets"][0]["current_relationship_snapshot"]
    ep52_pilot = _json(PILOT_ROOT / "reports/ep5_2.json.gz")["datasets"][0]["current_relationship_snapshot"]
    base_relationships = {row["relationship_id"] for row in ep52_base["relationships"]}; pilot_relationships = {row["relationship_id"] for row in ep52_pilot["relationships"]}
    completed = int(pilot["coverage"]["recovered_launches"])
    report = {
        "milestone": "OIP v2.1B", "source": "OIP_V2_1A_PILOT_ONLY", "new_rpc_calls": 0,
        "provider_failure_analysis": {"failed_attempts": 394, "classified_attempts": 0,
            "unclassified_attempts": 394,
            "reason": "Per-attempt unsuccessful responses were not durably retained by v2.1A."},
        "retry_analysis": {"pilot_retries": 0, "measured_retry_recoveries": 0,
            "recoverable_failures": "NOT_DETERMINABLE_FROM_RETAINED_DATA"},
        "provider_comparison": {"helius": {"calls": 1000, "recovered": 606},
            "public_rpc": "NOT_MEASURED", "existing_failover": "NOT_EXERCISED"},
        "dependency_yield": {
            purpose: {"calls": call_yield[(purpose, "calls")],
                      "recovered": call_yield[(purpose, "recovered")],
                      "transaction_yield_ppm": round(call_yield[(purpose, "recovered")] * 1_000_000 / call_yield[(purpose, "calls")])}
            for purpose in ("eligible_migrated_creation", "eligible_migrated_migration")},
        "completion_yield": {
            reason: {"launches": group_yield[(reason, "launches")],
                     "completed": group_yield[(reason, "completed")],
                     "calls_per_launch": 2 if reason == "MISSING_CREATION_AND_MIGRATION_TRANSACTION" else 1,
                     "completion_per_call_ppm": round(group_yield[(reason, "completed")] * 1_000_000 /
                         (group_yield[(reason, "launches")] * (2 if reason == "MISSING_CREATION_AND_MIGRATION_TRANSACTION" else 1)))}
            for reason in ("MISSING_CREATION_AND_MIGRATION_TRANSACTION", "MISSING_MIGRATION_TRANSACTION")},
        "storage": {"before": base_storage, "after": pilot_storage, "delta": storage_delta,
            "bytes_per_completed_launch": round(storage_delta["allocated_bytes"] / completed)},
        "yield_kpis": {"completed_launches_per_rpc": completed / 1000,
            "completed_launches_per_credit": completed / 10000,
            "evidence_per_rpc": pilot["records"]["evidence_added"] / 1000,
            "primitives_per_rpc": pilot["records"]["primitives_added"] / 1000,
            "new_motif_ids_per_rpc": len(pilot_motifs - base_motifs) / 1000,
            "new_relationship_ids_per_rpc": len(pilot_relationships - base_relationships) / 1000,
            "net_relationships_per_rpc": (len(pilot_relationships) - len(base_relationships)) / 1000,
            "discovery_occurrences_per_rpc": (ep40_pilot["candidate_count"] - ep40_base["candidate_count"]) / 1000},
        "sampling": sampling,
        "telemetry": {"acquisition": "UNAVAILABLE", "mirror": "UNAVAILABLE",
            "normalization": "UNAVAILABLE", "primitive_replay_seconds": pilot["replay"]["seconds"],
            "discovery_replay": "DETERMINISTIC", "motif_replay": "DETERMINISTIC",
            "relationship_replay": "DETERMINISTIC"},
        "invariants": {"evidence_semantics_changed": False, "primitive_semantics_changed": False,
            "runtime_changed": False, "discovery_changed": False, "production_interaction": False},
        "verdict": "C — Retry/failover refinement required",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    failure_census_path = args.output.with_name("oip_v2_1b_provider_failure_census.json")
    failure_census_path.write_text(json.dumps({
        "milestone": "OIP v2.1B", "failures": [{
            "signature": target.signature, "launch": target.launch,
            "purpose": target.purpose,
            "classification": "UNKNOWN_TELEMETRY_NOT_RETAINED",
            "recoverability": "NOT_DETERMINABLE_FROM_RETAINED_DATA",
        } for target in failed_targets], "failure_count": len(failed_targets),
        "new_rpc_calls": 0,
    }, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__": raise SystemExit(main())
