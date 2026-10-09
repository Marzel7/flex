#!/usr/bin/env python3
"""Produce a compact, provider-free reconciliation of DEV-014 pilot evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CREATION = ROOT / "docs/audits/dev014_watchtower_creation_validation_pilot_20261009.v1.json"
MIGRATION = ROOT / "docs/audits/dev014_watchtower_migration_validation_pilot_20261009.v1.json"
COVERAGE = ROOT / "docs/audits/dev014_watchtower_provenance_coverage_20261009.v1.json"
PLAN = ROOT / "docs/audits/dev014_watchtower_staged_provenance_recovery_plan_20261009.v1.json"
OUTPUT = ROOT / "docs/audits/dev014_watchtower_creation_migration_reconciliation_20261009.v1.json"
MAX_ARTIFACT_BYTES = 1_000_000
MAX_NEXT_REQUESTS = 10


def stable(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def main() -> None:
    if OUTPUT.exists():
        raise SystemExit("RECONCILIATION_OUTPUT_ALREADY_EXISTS")
    creation_doc, migration_doc, coverage_doc, plan_doc = map(load, (CREATION, MIGRATION, COVERAGE, PLAN))
    creations = creation_doc["records"]
    migrations = migration_doc["records"]
    if len(creations) != 10 or len(migrations) != 10:
        raise ValueError("both pilots must contain exactly ten records")
    if any(record["qualification_status"] != "PUMPFUN_CREATE_EVENT_VERIFIED" for record in creations):
        raise ValueError("creation pilot contains unqualified event")
    if any(record["status"] != "MIGRATION_EVENT_VERIFIED" for record in migrations):
        raise ValueError("migration pilot contains unqualified event")
    creation_by_mint = {record["mint"]: record for record in creations}
    migration_by_mint = {record["mint"]: record for record in migrations}
    if len(creation_by_mint) != 10 or len(migration_by_mint) != 10:
        raise ValueError("duplicate mint inside a pilot")
    event_ids = [record["evidence_identity"] for record in creations + migrations]
    if len(event_ids) != len(set(event_ids)):
        raise ValueError("duplicate pilot evidence identity")
    coverage_by_mint = {record["mint"]: record for record in coverage_doc["records"]}
    plan_by_mint = {record["mint"]: record for record in plan_doc["records"]}

    pairs = []
    for mint in sorted(set(creation_by_mint) | set(migration_by_mint)):
        creation, migration = creation_by_mint.get(mint), migration_by_mint.get(mint)
        baseline = coverage_by_mint[mint]
        row: dict[str, Any] = {"mint": mint, "creation_validated": creation is not None, "migration_validated": migration is not None}
        if creation:
            row.update({"creation_signature": creation["creation_signature"], "creation_slot": creation["validated_creation_slot"], "creation_evidence_identity": creation["evidence_identity"]})
        if migration:
            row.update({"migration_signature": migration["migration_signature"], "migration_slot": migration["validated_slot"], "migration_evidence_identity": migration["evidence_identity"]})
        if creation and migration:
            row["event_identities_distinct"] = creation["creation_signature"] != migration["migration_signature"]
            row["chronology"] = "CHRONOLOGY_QUALIFIED" if creation["validated_creation_slot"] < migration["validated_slot"] else "SAME_SLOT_ORDER_UNRESOLVED"
        elif creation:
            row.update({"missing_event_type": "MIGRATION", "candidate_signature": baseline["migration"]["signature"], "expected_slot": baseline["migration"]["slot"]})
        else:
            row.update({"missing_event_type": "CREATION", "candidate_signature": baseline["creation"]["signature"], "expected_slot": baseline["creation"]["slot"]})
        pairs.append(row)

    candidates = []
    for row in pairs:
        if not row.get("missing_event_type") or not row.get("candidate_signature"):
            continue
        baseline, plan = coverage_by_mint[row["mint"]], plan_by_mint[row["mint"]]
        missing = row["missing_event_type"]
        timestamp = baseline["migration"]["timestamp"] if missing == "MIGRATION" else baseline["creation"]["timestamp"]
        priority = (
            int(bool(baseline["qualified_entry"])),
            int(bool(baseline["strict_offset_price_retained"])),
            int(bool(plan["early_minimum_evidence"])),
            int(bool(plan["sharp_exit_evidence"])),
            int(timestamp or 0),
        )
        candidates.append((priority, row, baseline, plan))
    candidates.sort(key=lambda item: (item[0], item[1]["mint"]), reverse=True)
    selected = candidates[:MAX_NEXT_REQUESTS]
    allowlist = []
    for ordinal, (priority, row, baseline, plan) in enumerate(selected, start=1):
        request_identity = stable({"contract": "DEV014_CREATION_MIGRATION_RECONCILIATION_NEXT_BATCH_V1", "ordinal": ordinal, "mint": row["mint"], "missing_event_type": row["missing_event_type"], "signature": row["candidate_signature"]})
        rationale = "one validated event plus retained counterpart; " + ("qualified Entry MC; " if baseline["qualified_entry"] else "") + ("strict opening-offset evidence; " if baseline["strict_offset_price_retained"] else "") + ("early-minimum research evidence; " if plan["early_minimum_evidence"] else "") + ("sharp-exit research evidence; " if plan["sharp_exit_evidence"] else "") + "then deterministic priority"
        allowlist.append({
            "ordinal": ordinal,
            "mint": row["mint"],
            "already_qualified_event": "CREATION" if row["creation_validated"] else "MIGRATION",
            "missing_event_type": row["missing_event_type"],
            "candidate_signature": row["candidate_signature"],
            "expected_slot": row["expected_slot"],
            "existing_price_evidence": {"qualified_entry_mc": baseline["qualified_entry"], "strict_opening_offset": baseline["strict_offset_price_retained"], "early_minimum_research": plan["early_minimum_evidence"], "sharp_exit_research": plan["sharp_exit_evidence"]},
            "request_identity": request_identity,
            "selection_rationale": rationale.rstrip("; "),
        })

    artifact = {
        "artifact_type": "DEV014_WATCHTOWER_CREATION_MIGRATION_RECONCILIATION",
        "version": 1,
        "provider_free": True,
        "inputs": {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in (CREATION, MIGRATION, COVERAGE, PLAN)},
        "validated_counts": {"creation": 10, "migration": 10, "both_events": sum(row["creation_validated"] and row["migration_validated"] for row in pairs), "chronology_qualified": sum(row.get("chronology") == "CHRONOLOGY_QUALIFIED" for row in pairs), "one_sided": sum(row["creation_validated"] != row["migration_validated"] for row in pairs)},
        "pairs": pairs,
        "next_batch": {"max_requests": MAX_NEXT_REQUESTS, "selection_order": "one-sided validated event, Entry MC, opening-offset evidence, early-minimum evidence, sharp-exit evidence, retained timestamp, mint", "allowlist": allowlist},
        "entry_price_readiness": {"allowlist_qualified_entry_mc": sum(item["existing_price_evidence"]["qualified_entry_mc"] for item in allowlist), "allowlist_strict_opening_offset": sum(item["existing_price_evidence"]["strict_opening_offset"] for item in allowlist)},
        "retention": {"raw_transaction_retained": False, "canonical_lifecycle_mutation": False, "aggregate_max_bytes": MAX_ARTIFACT_BYTES},
    }
    raw = json.dumps(artifact, indent=2, sort_keys=True) + "\n"
    if len(raw.encode()) > MAX_ARTIFACT_BYTES:
        raise ValueError("aggregate artifact bound exceeded")
    OUTPUT.write_text(raw)
    print(json.dumps({"output": str(OUTPUT), "bytes": len(raw.encode()), **artifact["validated_counts"]}, sort_keys=True))


if __name__ == "__main__":
    main()
