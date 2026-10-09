#!/usr/bin/env python3
"""Provider-free audit of DEV-014 same-slot Watchtower event pairs."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RECONCILIATION = ROOT / "docs/audits/dev014_watchtower_creation_migration_reconciliation_20261009.v1.json"
COUNTERPARTS = ROOT / "docs/audits/dev014_watchtower_counterpart_validation_batch_20261009.v1.json"
OUTPUT = ROOT / "docs/audits/dev014_watchtower_same_slot_identity_ordering_audit_20261009.v1.json"
MAX_ARTIFACT_BYTES = 1_000_000


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def main() -> None:
    if OUTPUT.exists():
        raise SystemExit("AUDIT_OUTPUT_ALREADY_EXISTS")
    reconciliation = json.loads(RECONCILIATION.read_text())
    counterpart = json.loads(COUNTERPARTS.read_text())
    pairs = []
    prior = next(row for row in reconciliation["pairs"] if row.get("chronology") == "SAME_SLOT_ORDER_UNRESOLVED")
    pairs.append({
        "mint": prior["mint"], "creation_signature": prior["creation_signature"], "migration_signature": prior["migration_signature"],
        "creation_slot": prior["creation_slot"], "migration_slot": prior["migration_slot"],
        "signature_distinct": prior["event_identities_distinct"], "source": "prior_pilots",
    })
    for row in counterpart["records"]:
        pairs.append({
            "mint": row["mint"], "creation_signature": row["creation_signature"], "migration_signature": row["migration_signature"],
            "creation_slot": row["creation_slot"], "migration_slot": row["migration_slot"],
            "signature_distinct": row["creation_signature"] != row["migration_signature"], "source": "counterpart_batch",
        })
    if len(pairs) != 11 or len({row["mint"] for row in pairs}) != 11:
        raise ValueError("expected eleven unique same-slot pairs")
    if not all(row["signature_distinct"] and row["creation_slot"] == row["migration_slot"] for row in pairs):
        raise ValueError("same-slot pair invariant failed")
    slots = sorted({row["creation_slot"] for row in pairs})
    artifact = {
        "artifact_type": "DEV014_WATCHTOWER_SAME_SLOT_IDENTITY_ORDERING_AUDIT",
        "version": 1,
        "provider_free": True,
        "inputs": {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in (RECONCILIATION, COUNTERPARTS)},
        "pair_count": len(pairs), "pairs": pairs,
        "event_validator_audit": {
            "creation": "requires Pump.fun program account, global 'Instruction: Create' log, no global migrate marker, and mint anywhere in transaction account keys or pre/post token balances",
            "migration": "requires global 'Instruction: Migrate' plus broad PumpSwap/initialize log patterns, no buy/sell markers, and mint anywhere in transaction account keys or pre/post token balances",
            "instruction_to_mint_binding": False,
            "inner_instruction_to_mint_binding": False,
            "balance_change_to_specific_instruction_binding": False,
            "retained_raw_transaction_available": False,
            "conclusion": "EVENT_BINDING_UNQUALIFIED for chronology/canonical reconstruction; retained records remain candidate transaction associations, not disproven identities",
        },
        "transaction_position_coverage": {"distinct_slots": len(slots), "transaction_index_retained": 0, "block_order_retained": 0, "qualified_instruction_order_retained": 0, "coverage": "NONE"},
        "strict_ordering_contract": {
            "different_slots": "creation_slot < migration_slot required",
            "same_slot_distinct_signatures": "independently retrieved transaction positions from the same block required; creation_position < migration_position",
            "same_signature": "explicitly qualified ordered creation and migration instructions bound to the mint required",
            "missing_evidence": "SAME_SLOT_ORDER_UNRESOLVED",
            "ambiguous_event_binding": "EVENT_BINDING_UNQUALIFIED",
        },
        "minimum_future_acquisition": {
            "authorized_now": False, "method": "getBlock", "request_count": len(slots), "one_request_per_distinct_slot": True,
            "required_normalized_fields": ["slot", "creation_signature", "migration_signature", "creation_transaction_index", "migration_transaction_index", "ordering_verdict", "provider_provenance"],
            "validation": "both signatures must occur exactly once in the retrieved slot and direct mint-to-event instruction binding must pass before ordering is accepted",
            "limits": {"concurrency": 1, "retries": 0, "pagination": False, "fallback": False, "raw_block_retention": False, "raw_transaction_retention": False, "response_size_fail_closed_bytes": 16_000_000, "aggregate_artifact_max_bytes": MAX_ARTIFACT_BYTES},
        },
        "impact": {
            "chronology_qualified_pairs": 0,
            "prior_migration_claim_correction": "Prior migration PASS establishes only transaction-level candidate association until direct mint-event binding is qualified; no canonical fact was written.",
            "wider_639_cohort": "Existing migration/creation provenance coverage uses the same retained identity class and cannot establish strict chronology without direct binding plus ordering evidence.",
            "historical_entry_reconstruction": "remains blocked: provenance is not independently chronology-qualified and price evidence remains separate.",
            "research_price_lifecycle": "may remain explicitly research-only and must not fabricate canonical chronology.",
        },
        "retention": {"raw_transaction_retained": False, "raw_block_retained": False, "aggregate_max_bytes": MAX_ARTIFACT_BYTES, "unbounded_growth_paths": 0},
    }
    artifact["evidence_identity"] = digest({key: value for key, value in artifact.items() if key != "evidence_identity"})
    raw = json.dumps(artifact, indent=2, sort_keys=True) + "\n"
    if len(raw.encode()) > MAX_ARTIFACT_BYTES:
        raise ValueError("aggregate bound exceeded")
    OUTPUT.write_text(raw)
    print(json.dumps({"output": str(OUTPUT), "bytes": len(raw.encode()), "pairs": len(pairs), "distinct_slots": len(slots)}, sort_keys=True))


if __name__ == "__main__":
    main()
