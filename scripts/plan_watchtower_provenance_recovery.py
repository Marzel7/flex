#!/usr/bin/env python3
"""Create a provider-free staged recovery plan for frozen Watchtower evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUDITS = ROOT / "docs/audits"
POPULATION = AUDITS / "dev014_watchtower_forensic_population_v2_20261009.v1.json"
COVERAGE = AUDITS / "dev014_watchtower_provenance_coverage_20261009.v1.json"
MIGRATION_CONTRACT = AUDITS / "dev014_watchtower_migration_provenance_recovery_contract_20261009.v1.json"
PILOT = AUDITS / "dev014_watchtower_migration_validation_pilot_20261009.v1.json"
OUTPUT = AUDITS / "dev014_watchtower_staged_provenance_recovery_plan_20261009.v1.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def batches(count: int) -> int:
    return (count + 9) // 10


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    population = {row["mint"]: row for row in json.loads(POPULATION.read_text())["launches"]}
    coverage = json.loads(COVERAGE.read_text())
    contract = json.loads(MIGRATION_CONTRACT.read_text())
    pilot = json.loads(PILOT.read_text())
    if len(population) != len(coverage["records"]) != 639:
        raise SystemExit("FROZEN_COHORT_CARDINALITY_MISMATCH")
    pilot_records = pilot["records"]
    if pilot["request_count"] != len(pilot_records) != 10:
        raise SystemExit("PILOT_CARDINALITY_MISMATCH")
    if len({row["migration_signature"] for row in pilot_records}) != 10:
        raise SystemExit("PILOT_SIGNATURE_DUPLICATION")
    if not all(row["status"] == "MIGRATION_EVENT_VERIFIED" and row["request_identity"] and row["evidence_identity"] for row in pilot_records):
        raise SystemExit("PILOT_VALIDATION_INCOMPLETE")
    pilot_by_mint = {row["mint"]: row for row in pilot_records}
    rows = []
    for source in sorted(coverage["records"], key=lambda row: row["mint"]):
        mint = source["mint"]
        launch = population[mint]
        creation_class = source["creation"]["class"]
        migration_class = source["migration"]["class"]
        if creation_class == "CREATION_QUALIFIED":
            creation_plan = "QUALIFIED"
        elif source["creation"]["signature"]:
            creation_plan = "GETTRANSACTION_VALIDATION_CANDIDATE_FIXTURE_SIGNATURE"
        else:
            creation_plan = "INDEPENDENT_DISCOVERY_REQUIRED"
        if mint in pilot_by_mint:
            migration_plan = "PILOT_VALIDATED_RESEARCH_EVIDENCE"
        elif migration_class == "MIGRATION_QUALIFIED":
            migration_plan = "PREVIOUSLY_QUALIFIED"
        elif migration_class == "MIGRATION_SLOT_MISSING":
            migration_plan = "GETTRANSACTION_VALIDATION_CANDIDATE"
        else:
            migration_plan = "INDEPENDENT_DISCOVERY_REQUIRED"
        evidence = launch.get("evidence") or {}
        terminal = launch.get("lifecycle_status") == "PRICE_MONITOR_COMPLETE_COLLAPSED"
        both = creation_plan == "QUALIFIED" and migration_plan in {"PILOT_VALIDATED_RESEARCH_EVIDENCE", "PREVIOUSLY_QUALIFIED"}
        rows.append({
            "mint": mint, "original_label": launch["original_classification"],
            "creation_provenance": creation_plan, "migration_provenance": migration_plan,
            "strict_entry_price_evidence": bool(source["strict_offset_price_retained"]),
            "early_minimum_evidence": bool(evidence.get("early_minimum")),
            "peak_evidence": bool(evidence.get("peak_ath")),
            "sharp_exit_evidence": bool(evidence.get("sharp_exit")),
            "terminal_evidence": terminal,
            "research_full_lifecycle_prerequisites": both and bool(source["strict_offset_price_retained"]),
            "canonical_promotion_authorized": False,
        })
    if len(rows) != 639:
        raise SystemExit("READINESS_MATRIX_CARDINALITY_MISMATCH")
    count = lambda condition: sum(bool(condition(row)) for row in rows)
    creation_candidates = [row for row in rows if row["creation_provenance"] == "GETTRANSACTION_VALIDATION_CANDIDATE_FIXTURE_SIGNATURE"]
    migration_candidates = [row for row in rows if row["migration_provenance"] == "GETTRANSACTION_VALIDATION_CANDIDATE"]
    if len(creation_candidates) != 533 or len(migration_candidates) != 620:
        raise SystemExit("RECOVERY_PLAN_COUNT_MISMATCH")
    artifact = {
        "schema_version": 1,
        "artifact_type": "DEV014_WATCHTOWER_STAGED_PROVENANCE_RECOVERY_PLAN",
        "frozen_population": "WATCHTOWER_FORENSIC_POPULATION_V2_20261009",
        "inputs": {"population_sha256": sha(POPULATION), "coverage_sha256": sha(COVERAGE),
                   "migration_contract_sha256": sha(MIGRATION_CONTRACT), "pilot_sha256": sha(PILOT)},
        "scope": {"provider_requests": 0, "birdeye_requests": 0, "live_database_writes": 0,
                  "queue_writes": 0, "lifecycle_mutations": 0, "raw_transaction_retention": False,
                  "max_artifact_bytes": 1_000_000, "unbounded_growth_paths": 0},
        "preserved_pilot": {"validated_records": 10, "duplicate_validations": 0,
                            "recovered_bhy2f_slot": 440508840, "canonical_promotion": False},
        "creation_recovery": {"qualified": count(lambda r: r["creation_provenance"] == "QUALIFIED"),
                              "signature_validation_candidates": len(creation_candidates),
                              "independent_discovery_required": count(lambda r: r["creation_provenance"] == "INDEPENDENT_DISCOVERY_REQUIRED"),
                              "contract": {"method": "getTransaction", "requests_per_retained_signature": 1,
                                           "acceptance": ["finalized successful transaction", "meta.err=null", "positive slot", "exact mint involvement", "Pump.fun Create semantics"],
                                           "rejected": ["migration signature substitution", "fixture timestamp as birth proof", "slot alone"]}},
        "migration_recovery": {"pilot_validated": count(lambda r: r["migration_provenance"] == "PILOT_VALIDATED_RESEARCH_EVIDENCE"),
                               "previously_qualified": count(lambda r: r["migration_provenance"] == "PREVIOUSLY_QUALIFIED"),
                               "remaining_signature_validation_candidates": len(migration_candidates),
                               "batch_size": 10, "batches_required": batches(len(migration_candidates)),
                               "contract": {"concurrency": 1, "retries": 0, "pagination": False, "fallback": False,
                                            "automatic_batch_expansion": False, "raw_transaction_retention": False}},
        "provider_budgets": {"helius_creation": {"spent": 0, "prospective_requests": len(creation_candidates), "batches": batches(len(creation_candidates)), "requires_explicit_batch_authorization": True},
                             "helius_migration": {"spent": 10, "remaining_prospective_requests": len(migration_candidates), "batches": batches(len(migration_candidates)), "requires_explicit_batch_authorization": True},
                             "birdeye_historical_mcap": {"spent": 0, "prospective_requests": 0, "in_scope": False, "budget_separate_from_helius": True}},
        "lifecycle_summary": {"creation_qualified": count(lambda r: r["creation_provenance"] == "QUALIFIED"),
                              "migration_research_or_qualified": count(lambda r: r["migration_provenance"] in {"PILOT_VALIDATED_RESEARCH_EVIDENCE", "PREVIOUSLY_QUALIFIED"}),
                              "strict_entry_price_evidence": count(lambda r: r["strict_entry_price_evidence"]),
                              "early_minimum_evidence": count(lambda r: r["early_minimum_evidence"]),
                              "peak_evidence": count(lambda r: r["peak_evidence"]),
                              "sharp_exit_evidence": count(lambda r: r["sharp_exit_evidence"]),
                              "terminal_evidence": count(lambda r: r["terminal_evidence"]),
                              "research_full_lifecycle_prerequisites": count(lambda r: r["research_full_lifecycle_prerequisites"]),
                              "interpretation": "This is planning-only readiness. Event identity and price evidence do not authorize Entry or lifecycle promotion."},
        "records": rows,
        "verdict": "STAGED_RECOVERY_PLANNED_NO_ADDITIONAL_ACQUISITION_AUTHORIZED",
    }
    rendered = json.dumps(artifact, sort_keys=True, separators=(",", ":")) + "\n"
    if len(rendered.encode()) >= 1_000_000:
        raise SystemExit("COMPACT_BOUND_EXCEEDED")
    if args.check:
        if not args.output.exists() or args.output.read_text() != rendered:
            raise SystemExit("ARTIFACT_NOT_DETERMINISTIC")
    else:
        args.output.write_text(rendered)


if __name__ == "__main__":
    main()
