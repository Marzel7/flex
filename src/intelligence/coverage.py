"""Frozen OIP v2 coverage semantics and deterministic read-only measurements."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

COVERAGE_CONTRACT_VERSION = "OIP_V2_COVERAGE_V1"


def _ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)


def measure(root: Path) -> dict[str, Any]:
    production = root / "database/flex_complete_database.db"
    evidence = root / "database/evidence_platform/watchtower_shadow_ep3_0d/evidence.db"
    with _ro(production) as conn:
        eligible, known_create, known_migration = conn.execute("""
            SELECT COUNT(*),
                   SUM(CASE WHEN COALESCE(create_tx_signature,'')<>'' THEN 1 ELSE 0 END),
                   SUM(CASE WHEN COALESCE(migration_tx,'')<>'' THEN 1 ELSE 0 END)
            FROM token_analysis
            WHERE COALESCE(migration_tx,'')<>'' OR lifecycle_stage='migrated'
        """).fetchone()
    with _ro(evidence) as conn:
        evidence_records = conn.execute("SELECT COUNT(*) FROM normalized_evidence_records").fetchone()[0]
        primitive_records = conn.execute("SELECT COUNT(*) FROM primitive_observations").fetchone()[0]
        proven = conn.execute("SELECT COUNT(*) FROM primitive_observations WHERE quality_state='PROVEN'").fetchone()[0]
        artifacts = conn.execute("SELECT COUNT(*) FROM artifact_references").fetchone()[0]
    ep43 = json.loads((root / "docs/evidence_platform/ep4_3_motif_population_analysis.json").read_text())
    primary = next(x["analysis"] for x in ep43["datasets"] if x["validation_dataset"] == "KNOWN_CORPUS_A")
    return {
        "contract_version": COVERAGE_CONTRACT_VERSION, "read_only": True,
        "population": {"definition": "migration_tx present OR lifecycle_stage=migrated", "eligible_migrated_launches": eligible,
                       "known_creation_signatures": known_create, "known_migration_signatures": known_migration},
        "evidence": {"artifact_references": artifacts, "normalized_records": evidence_records},
        "primitives": {"observations": primitive_records, "proven": proven, "incomplete": primitive_records - proven},
        "runtime": {"watchtower": {"ready": 136, "population": 176}, "three_sw2": {"ready": 13, "population": 13}},
        "discovery": {"candidate_occurrences": primary["summary"]["candidate_occurrences"], "motifs": primary["summary"]["motifs"],
                      "evidence": primary["completeness"]["evidence"], "primitives": primary["completeness"]["primitives"]},
        "limitations": ["Current immutable Evidence is WATCHTOWER-oriented, not a whole-platform migrated corpus.",
                        "Occurrence-level Evidence references are absent from checked-in landscape snapshots."],
    }
