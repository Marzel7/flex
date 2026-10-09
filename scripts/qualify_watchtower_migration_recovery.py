#!/usr/bin/env python3
"""Create a provider-free recovery contract for retained Watchtower migrations."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "docs/audits/dev014_watchtower_provenance_coverage_20261009.v1.json"
OUTPUT = ROOT / "docs/audits/dev014_watchtower_migration_provenance_recovery_contract_20261009.v1.json"
BASE58 = set("123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz")


def read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    connection.execute("PRAGMA query_only=ON")
    return connection


def build(*, core_db: Path) -> dict[str, Any]:
    coverage = json.loads(INPUT.read_text())
    candidates = [row for row in coverage["records"] if row["migration"]["class"] == "MIGRATION_SLOT_MISSING"]
    if len(candidates) != 630:
        raise ValueError("EXPECTED_630_MIGRATION_SLOT_GAPS")
    signatures = [str(row["migration"]["signature"]) for row in candidates]
    if len(set(signatures)) != 630:
        raise ValueError("MIGRATION_SIGNATURE_REUSE")
    if any(not (64 <= len(signature) <= 96 and set(signature) <= BASE58) for signature in signatures):
        raise ValueError("INVALID_BASE58_MIGRATION_SIGNATURE")
    by_signature = {str(row["migration"]["signature"]): row for row in candidates}
    connection = read_only(core_db)
    qmarks = ",".join("?" for _ in signatures)
    rows = connection.execute(f"""
        SELECT mint, migration_tx, migrated_at, migration_slot, pumpswap_pool_address, lifecycle_stage
        FROM token_analysis WHERE migration_tx IN ({qmarks})
    """, signatures).fetchall()
    local = {str(row[1]): row for row in rows}
    records: list[dict[str, Any]] = []
    for signature in sorted(signatures):
        source = by_signature[signature]
        row = local.get(signature)
        if row is None:
            slot_state = "SLOT_NOT_RETAINED"
            local_slot = None
            mint_match = False
            timestamp_delta = None
            lifecycle = None
            pool_present = False
        else:
            mint, _, migrated_at, local_slot, pool, lifecycle = row
            mint_match = mint == source["mint"]
            timestamp_delta = int(migrated_at) - int(source["migration"]["timestamp"])
            pool_present = bool(pool)
            slot_state = "RETAINED_SLOT_MINT_BOUND" if mint_match and local_slot else "LOCAL_IDENTITY_CONFLICT"
        records.append({
            "mint": source["mint"], "signature": signature,
            "retained_timestamp": source["migration"]["timestamp"],
            "retained_source": source["migration"]["source"],
            "local_slot": local_slot, "slot_state": slot_state,
            "local_mint_matches": mint_match, "local_timestamp_delta_seconds": timestamp_delta,
            "local_lifecycle_stage": lifecycle, "local_pool_present": pool_present,
            "rpc_validation_required": True,
        })
    retained = [record for record in records if record["slot_state"] == "RETAINED_SLOT_MINT_BOUND"]
    unresolved = [record for record in records if record["slot_state"] != "RETAINED_SLOT_MINT_BOUND"]
    if len(retained) != 629 or len(unresolved) != 1:
        raise ValueError("UNEXPECTED_RETAINED_SLOT_COVERAGE")
    pilot = unresolved + sorted(retained, key=lambda row: (str(row["mint"]), str(row["signature"])))[:9]
    artifact: dict[str, Any] = {
        "schema_version": 1,
        "artifact_type": "DEV014_WATCHTOWER_MIGRATION_PROVENANCE_RECOVERY_CONTRACT",
        "source_coverage_sha256": hashlib.sha256(INPUT.read_bytes()).hexdigest(),
        "scope": {"candidate_signatures": 630, "provider_requests": 0, "live_database_writes": 0,
                  "queue_writes": 0, "lifecycle_mutations": 0, "raw_transaction_retention": False,
                  "max_compact_artifact_bytes": 1_000_000, "unbounded_growth_paths": 0},
        "signature_quality": {"base58_syntax_valid": 630, "unique_signatures": 630,
                              "signature_lengths": dict(sorted(Counter(map(len, signatures)).items()))},
        "retained_slot_assessment": {"slot_mint_bound": len(retained), "slot_unresolved": len(unresolved),
                                      "timestamp_delta_seconds": dict(sorted(Counter(r["local_timestamp_delta_seconds"] for r in retained).items())),
                                      "interpretation": "Mint/signature/slot agreement is retained locally, but timestamp disagreement and absent raw transaction semantics prevent independent migration-event qualification."},
        "local_cache_assessment": {"transaction_cache_hits": 0, "raw_transaction_cache_present": False,
                                    "retained_slot_source": "token_analysis", "slot_inference_from_timestamp": False},
        "rpc_contract": {"method": "getTransaction", "requests_per_signature": 1, "pagination": False, "block_range_scans": False,
                         "retries": False, "fallback": False, "request_params": {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0, "commitment": "finalized"},
                         "acceptance": ["non-null successful result", "meta.err is null", "positive transaction slot", "exact mint involvement", "verified Pump.fun-to-PumpSwap migration instruction/event", "derived pool/transition evidence is internally consistent"],
                         "fail_closed": ["slot alone is insufficient", "timestamp disagreement is retained, never overwritten", "missing migration event semantics is not promoted"]},
        "provider_budget": {"full_validation_minimum_requests": 630, "slot_only_gap_minimum_requests": 1,
                            "initial_pilot_maximum_requests": 10, "concurrency": 1, "retries": 0,
                            "compute_unit_cost": "UNVERIFIED; bind to the selected RPC account before authorization",
                            "limits": "Do not assume Birdeye 20/4 market-data limits apply to RPC; require an explicit isolated RPC budget."},
        "storage": {"success_record_max_bytes": 1024, "failure_record_max_bytes": 512,
                    "pilot_aggregate_max_bytes": 10240, "full_630_aggregate_max_bytes": 645120,
                    "raw_transaction_retention": False, "provider_response_retention": False},
        "pilot": {"selection": "one unresolved-slot signature plus nine deterministic mint-sorted retained-slot signatures",
                  "allowlist": [{"mint": row["mint"], "signature": row["signature"], "local_slot": row["local_slot"]} for row in pilot],
                  "stop_conditions": ["any provider/transport ambiguity", "validation contract failure", "budget exhaustion", "attempt to retain raw transaction data"]},
        "creation_strategy": {"missing_independent_creation": 571, "with_retained_signature_but_no_qualified_slot": 533,
                              "fixture_only": 532, "conflicting_identity": 1, "signature_unavailable": 38,
                              "rule": "Migration lookups do not establish creation provenance; creation requires its own exact creation-signature lookup contract."},
        "records": records,
        "verdict": "RETAINED_SLOT_RECOVERY_FEASIBLE_FOR_629_BUT_FULL_MIGRATION_EVENT_QUALIFICATION_REQUIRES_ONE_BOUNDED_GETTRANSACTION_LOOKUP_PER_SIGNATURE",
    }
    return artifact


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--core-db", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    rendered = json.dumps(build(core_db=args.core_db), sort_keys=True, separators=(",", ":")) + "\n"
    if len(rendered.encode()) >= 1_000_000:
        raise SystemExit("COMPACT_ARTIFACT_BOUND_EXCEEDED")
    if args.check:
        if not args.output.exists() or args.output.read_text() != rendered:
            raise SystemExit("ARTIFACT_NOT_DETERMINISTIC")
    else:
        args.output.write_text(rendered)


if __name__ == "__main__":
    main()
