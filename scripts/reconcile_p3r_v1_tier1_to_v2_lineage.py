#!/usr/bin/env python3
"""Read-only provenance reconciliation for unrecoverable P3R v1 Tier-1 references."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

BASE_RUN = "p3r-v2-2dec1d40604c1f7c08c8"
V1_IDS = (
    "p3r-candidate-13a04d7da7a1fc55",
    "p3r-candidate-af5004dfe42cfe11",
    "p3r-candidate-bf30e77ee07e312b",
    "p3r-candidate-ec1b6cff80643746",
)
DB = Path("database/wt_ops_v2.db")
ROOT = Path("docs/agent_handoff/p3r/v2")
V1_ROOT = Path("/tmp/p3r-clean-20260824T092959Z")
V1_MEMBERS = V1_ROOT / "behavioural_corpus/p3r_candidate_operational_family_membership.v1.json"
V1_TIERS = V1_ROOT / "activity/tiers/p3r_tier1_candidate_membership.v1.json"
VERSION = "P3R_V1_TIER1_TO_V2_LINEAGE_RECONCILIATION.v1"
DDL = """
CREATE TABLE IF NOT EXISTS p3r_v2_v1_lineage_reconciliation_runs (
 reconciliation_run_id TEXT PRIMARY KEY, base_run_id TEXT NOT NULL,
 artifact_sha256 TEXT NOT NULL, manifest_sha256 TEXT NOT NULL, verdict TEXT NOT NULL
);
"""


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path: Path, value: object) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    return {"path": str(path), "sha256": sha(path)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DB)
    parser.add_argument("--reproduce-run")
    args = parser.parse_args()
    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        canonical = conn.execute("SELECT manifest_digest,source_snapshot_json FROM p3r_v2_runs WHERE run_id=?", (BASE_RUN,)).fetchone()
        tier_one = conn.execute("SELECT candidate_id,evidence_json FROM p3r_v2_tier_membership WHERE run_id=? AND tier='V2_TIER_1_ACTIVE_MULTI_LAYER'", (BASE_RUN,)).fetchall()
        v1_tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND lower(name) LIKE '%p3r%v1%'")]
    finally:
        conn.close()
    if canonical is None or len(tier_one) != 1: raise SystemExit("canonical v2 prerequisite missing")
    provenance = {
        "historical_v1_member_artifact": {"path": str(V1_MEMBERS), "exists": V1_MEMBERS.exists()},
        "historical_v1_tier_artifact": {"path": str(V1_TIERS), "exists": V1_TIERS.exists()},
        "v1_database_tables": v1_tables,
        "v2_reference_mapping": {"path": str(ROOT / BASE_RUN / "p3r_v2_known_v1_reference_mapping.v1.json"), "mapping_status": "UNMAPPED_NOT_FORCED", "reason": "v1 candidate membership artifacts are unrecoverable; v2 identities are intentionally new and address-independent."},
    }
    run_id = "p3r-v2-v1-lineage-" + digest({"base": BASE_RUN, "canonical_manifest": canonical["manifest_digest"], "v1_ids": V1_IDS, "provenance": provenance, "version": VERSION})[:20]
    rows = [{"historical_candidate_id": candidate, "v1_member_count": None, "v2_members_found": None, "overlap_matrix": "NOT_COMPUTABLE: exact v1 member mint list is unavailable", "mechanism_comparison": "INSUFFICIENT_RETAINED_EVIDENCE", "v2_activity_mapping": "NOT_COMPUTABLE without a member correspondence", "disposition": "INSUFFICIENT_EVIDENCE_TO_MAP"} for candidate in V1_IDS]
    payload = {
        "schema_version": VERSION, "reconciliation_run_id": run_id, "canonical_v2_run_id": BASE_RUN,
        "canonical_manifest_sha256": canonical["manifest_digest"], "canonical_source_snapshot": json.loads(canonical["source_snapshot_json"]),
        "historical_candidates": rows, "historical_recovery": provenance,
        "canonical_v2_tier_1": {"candidate_id": tier_one[0]["candidate_id"], "evidence": json.loads(tier_one[0]["evidence_json"])},
        "tier_one_correspondence": "INSUFFICIENT_RETAINED_EVIDENCE: no historical v1 member/behaviour payload survives for comparison.",
        "four_to_one_explanation": {"observed": "The v1 Tier-1 member artifacts are unrecoverable; canonical v2 independently reproduced one Tier-1 family under its own frozen contract.", "not_determined": ["whether any v1 family split", "whether any v1 families merged", "whether differing thresholds or evidence coverage caused the count change", "whether any current high-activity v2 family descends from a v1 Tier-1 family"]},
        "outcome": "INSUFFICIENT_DURABLE_EVIDENCE_TO_REPRODUCE",
        "recommended_next_action": "Do not map or rank the four v1 references. Recover an immutable historical v1 membership artifact from an independently retained backup before a member-overlap or mechanism reconciliation is authorized.",
        "safety": {"provider_rpc_calls": 0, "membership_mutation": False, "tier_mutation": False, "queue_replay": False, "operation_promotion": False, "trading_signal": False},
    }
    root = ROOT / BASE_RUN / "v1_tier1_lineage_reconciliation" / run_id
    artifact = write(root / "p3r_v1_tier1_to_v2_lineage_reconciliation.v1.json", payload)
    manifest = write(root / "p3r_v1_tier1_to_v2_lineage_reconciliation_manifest.v1.json", {"reconciliation_run_id": run_id, "canonical_v2_run_id": BASE_RUN, "canonical_manifest_sha256": canonical["manifest_digest"], "provenance": provenance, "artifact": artifact, "result_digest": digest(payload)})
    dest = sqlite3.connect(args.db, timeout=30)
    try:
        dest.execute("PRAGMA busy_timeout=30000"); dest.executescript(DDL); dest.execute("BEGIN IMMEDIATE")
        dest.execute("INSERT OR REPLACE INTO p3r_v2_v1_lineage_reconciliation_runs VALUES (?,?,?,?,?)", (run_id, BASE_RUN, artifact["sha256"], manifest["sha256"], payload["outcome"]))
        dest.commit()
    finally:
        dest.close()
    print(json.dumps({"verdict": payload["outcome"], "reconciliation_run_id": run_id, "historical_recovery": provenance, "artifact": artifact, "manifest": manifest}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
