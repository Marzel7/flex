#!/usr/bin/env python3
"""Produce the deterministic EP3.1 shadow parity report (no RPC or production reads)."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, default=Path("database/evidence_platform/watchtower_shadow_ep3_0d"))
    parser.add_argument("--output", type=Path, default=Path("docs/evidence_platform/ep3_1_watchtower_parity.json"))
    args = parser.parse_args()
    population = json.loads((args.corpus / "population.json").read_text())
    connection = sqlite3.connect(args.corpus / "evidence.db")
    primitive_counts = dict(connection.execute(
        "SELECT primitive_type,COUNT(*) FROM primitive_observations GROUP BY primitive_type ORDER BY primitive_type"
    ))
    launch_mints = {item["mint"] for item in population["launches"]}
    signer_mints = {
        json.loads(row[0]).get("mint") for row in connection.execute(
            "SELECT output_payload_json FROM primitive_observations WHERE primitive_type='LAUNCH_SIGNER'"
        )
    }
    recovered = sorted(launch_mints & signer_mints)
    report = {
        "milestone": "EP3.1",
        "authority": "SHADOW_ONLY",
        "population": {"treasuries": len(population["treasuries"]),
                       "launches": len(population["launches"]),
                       "provisioning_edges": len(population["provisioning_edges"]),
                       "canonical_entities": len(population["entities"])},
        "runtime_inputs": {"primitive_counts": primitive_counts,
                           "launches_with_launch_signer": len(recovered),
                           "launches_without_launch_signer": len(launch_mints - signer_mints)},
        "parity_differences": [
            {"area": "launch coverage", "classification": "Missing Evidence",
             "legacy": len(launch_mints), "shadow": len(recovered),
             "detail": "Frozen launches without a materialized LAUNCH_SIGNER remain unavailable."},
            {"area": "provisioning edge parity", "classification": "Known legacy limitation",
             "legacy": len(population["provisioning_edges"]), "shadow": None,
             "detail": "Legacy edge rows are comparison data; Primitive Contract v1 does not reproduce legacy edge identity."},
            {"area": "campaign grouping", "classification": "Known legacy limitation",
             "legacy": "authoritative", "shadow": "not emitted",
             "detail": "No approved generic primitive establishes legacy campaign membership."}
        ],
        "invariants": {"production_authority_changed": False, "governance_executed": False,
                       "rpc_performed": False, "production_storage_read_by_evaluation": False},
    }
    canonical = json.dumps(report, sort_keys=True, separators=(",", ":")).encode()
    report["report_digest"] = hashlib.sha256(canonical).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
