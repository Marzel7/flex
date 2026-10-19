"""Frozen OIP v2 historical coverage contract.

The OIP baseline is historical-only.  Its compact fixture deliberately avoids
using the retired shadow corpus as a production fallback.
"""
from __future__ import annotations

import json
import hashlib
from pathlib import Path
from typing import Any

COVERAGE_CONTRACT_VERSION = "OIP_V2_COVERAGE_V1"


FIXTURE = "docs/evidence_platform/oip_v2_foundation_coverage_fixture.v1.json"


def _fixture(root: Path) -> dict[str, Any]:
    payload = json.loads((root / FIXTURE).read_text())
    body = dict(payload)
    digest = body.pop("fixture_sha256")
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    if hashlib.sha256(encoded).hexdigest() != digest:
        raise ValueError("historical OIP coverage fixture digest mismatch")
    return payload


def measure_historical(root: Path) -> dict[str, Any]:
    """Return only the frozen OIP foundation contract; no live DB access."""
    fixture = _fixture(root)
    ep43 = json.loads((root / "docs/evidence_platform/ep4_3_motif_population_analysis.json").read_text())
    primary = next(x["analysis"] for x in ep43["datasets"] if x["validation_dataset"] == "KNOWN_CORPUS_A")
    return {
        "contract_version": COVERAGE_CONTRACT_VERSION, "read_only": True,
        "evidence": fixture["evidence"],
        "primitives": fixture["primitives"],
        "runtime": {"watchtower": {"ready": 136, "population": 176}, "three_sw2": {"ready": 13, "population": 13}},
        "discovery": {"candidate_occurrences": primary["summary"]["candidate_occurrences"], "motifs": primary["summary"]["motifs"],
                      "evidence": primary["completeness"]["evidence"], "primitives": primary["completeness"]["primitives"]},
        "limitations": ["Current immutable Evidence is WATCHTOWER-oriented, not a whole-platform migrated corpus.",
                        "Occurrence-level Evidence references are absent from checked-in landscape snapshots."],
    }


def measure(root: Path) -> dict[str, Any]:
    """Historical contract plus the separately-current migrated-token census."""
    result = measure_historical(root)
    production = root / "database/flex_complete_database.db"
    import sqlite3
    with sqlite3.connect(f"file:{production}?mode=ro", uri=True) as conn:
        eligible, known_create, known_migration = conn.execute("""
            SELECT COUNT(*),
                   SUM(CASE WHEN COALESCE(create_tx_signature,'')<>'' THEN 1 ELSE 0 END),
                   SUM(CASE WHEN COALESCE(migration_tx,'')<>'' THEN 1 ELSE 0 END)
            FROM token_analysis
            WHERE COALESCE(migration_tx,'')<>'' OR lifecycle_stage='migrated'
        """).fetchone()
    result["population"] = {"definition": "migration_tx present OR lifecycle_stage=migrated", "eligible_migrated_launches": eligible,
                            "known_creation_signatures": known_create, "known_migration_signatures": known_migration}
    return result
