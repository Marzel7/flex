#!/usr/bin/env python3
"""Reconcile the frozen canonical P3R v2 tiers with later enrichment layers."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ops.p3r_v2_tiering import canonical_json, digest

DB = Path("database/wt_ops_v2.db")
BASE_RUN = "p3r-v2-2dec1d40604c1f7c08c8"
ENRICHMENT_RUN = "p3r-v2-enrichment-c03f0fce0b7e3b9d685b"
ROOT = Path("docs/agent_handoff/p3r/v2")
VERSION = "P3R_V2_CANONICAL_TIER_REPLAY_RECONCILIATION.v1"
DDL = """
CREATE TABLE IF NOT EXISTS p3r_v2_canonical_reconciliation_runs (
 reconciliation_run_id TEXT PRIMARY KEY, base_run_id TEXT NOT NULL, enrichment_run_id TEXT NOT NULL,
 source_snapshot_json TEXT NOT NULL, artifact_sha256 TEXT NOT NULL, manifest_sha256 TEXT NOT NULL, verdict TEXT NOT NULL
);
"""


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
        if args.reproduce_run:
            previous = conn.execute("SELECT base_run_id,enrichment_run_id,source_snapshot_json FROM p3r_v2_canonical_reconciliation_runs WHERE reconciliation_run_id=?", (args.reproduce_run,)).fetchone()
            if previous is None: raise SystemExit("unknown reconciliation replay")
            base_run, enrichment_run, snapshot = previous["base_run_id"], previous["enrichment_run_id"], json.loads(previous["source_snapshot_json"])
        else:
            base_run, enrichment_run = BASE_RUN, ENRICHMENT_RUN
            run = conn.execute("SELECT source_snapshot_json,manifest_digest,contract_digest FROM p3r_v2_runs WHERE run_id=?", (base_run,)).fetchone()
            if run is None: raise SystemExit("missing canonical base run")
            snapshot = json.loads(run["source_snapshot_json"])
        run = conn.execute("SELECT source_snapshot_json,manifest_digest,contract_digest FROM p3r_v2_runs WHERE run_id=?", (base_run,)).fetchone()
        canonical_root = ROOT / base_run
        canonical_manifest = canonical_root / "p3r_v2_regeneration_reproducibility_manifest.v1.json"
        canonical_tiers = canonical_root / "p3r_v2_tier_membership.v1.json"
        manifest = json.loads(canonical_manifest.read_text())
        tiers = json.loads(canonical_tiers.read_text())
        canonical_manifest_sha = sha(canonical_manifest)
        expected_counts = {"families": 220, "member_mints": 2357, "watch_now": 61, "V2_TIER_1_ACTIVE_MULTI_LAYER": 1, "V2_TIER_2_ACTIVE_STRUCTURAL": 13, "V2_TIER_3_ACTIVE_BASE": 47, "V2_WATCH_LATER": 126, "V2_DORMANT": 33}
        replay_ok = (
            manifest["run_id"] == base_run and run["manifest_digest"] == canonical_manifest_sha and
            manifest["counts"] == expected_counts and manifest["source_snapshot"] == snapshot and
            len(tiers["watch_now"]) == 61 and set(tiers["watch_now"]) == set(sum((tiers["sets"][key] for key in ("V2_TIER_1_ACTIVE_MULTI_LAYER", "V2_TIER_2_ACTIVE_STRUCTURAL", "V2_TIER_3_ACTIVE_BASE")), []))
        )
        tier_one = tiers["sets"]["V2_TIER_1_ACTIVE_MULTI_LAYER"]
        if len(tier_one) != 1: raise SystemExit("canonical Tier-1 cardinality regression")
        tier_one_id = tier_one[0]
        family = conn.execute("SELECT membership_digest,member_count,distinct_creators,distinct_direct_funders,distinct_parents,fingerprint_json FROM p3r_v2_candidate_families WHERE run_id=? AND candidate_id=?", (base_run, tier_one_id)).fetchone()
        evidence_row = conn.execute("SELECT evidence_json FROM p3r_v2_tier_membership WHERE run_id=? AND candidate_id=?", (base_run, tier_one_id)).fetchone()
        activity_row = conn.execute("SELECT metrics_json FROM p3r_v2_activity WHERE run_id=? AND candidate_id=?", (base_run, tier_one_id)).fetchone()
        shortlist = conn.execute("SELECT rank FROM p3r_v2_shortlist WHERE run_id=? AND candidate_id=?", (base_run, tier_one_id)).fetchone()
        enrichment = conn.execute("SELECT assessment_json FROM p3r_v2_shortlist_assessments WHERE assessment_run_id=? AND candidate_id=?", (enrichment_run, tier_one_id)).fetchone()
        closure = conn.execute("SELECT closure_json FROM p3r_v2_local_gap_closures WHERE candidate_id=?", (tier_one_id,)).fetchone()
        contract = {
            "version": VERSION, "canonical_tier_contract_digest": run["contract_digest"],
            "canonical_contract": manifest["contract"]["tiers"],
            "enrichment_contract": {
                "population": "Only the top ten candidates selected from canonical Tier-2 plus Tier-3; canonical Tier-1 is excluded by shortlist construction.",
                "near_tier_1": "An enrichment analysis category for a shortlisted candidate whose assessed gap matrix has no non-PASS requirement; it is not a canonical tier membership label.",
                "known_bug_scope": "Only local-gap-closure disposition of investigated shortlist candidates; it cannot affect the canonical Tier-1 candidate when that candidate is excluded before enrichment.",
            },
            "code_sha256": sha(Path(__file__)),
        }
        reconciliation_id = "p3r-v2-reconcile-" + digest({"base": base_run, "enrichment": enrichment_run, "snapshot": snapshot, "contract": contract})[:20]
        trace = {
            "canonical_family": tier_one_id,
            "canonical_tier": "V2_TIER_1_ACTIVE_MULTI_LAYER",
            "shortlist_inclusion": "EXCLUDED",
            "shortlist_rule": "Canonical materializer constructs the shortlist only from V2_TIER_2_ACTIVE_STRUCTURAL and V2_TIER_3_ACTIVE_BASE, then takes the top ten by activity/evidence priority.",
            "enrichment_assessment": "ABSENT: no shortlist row, therefore no enrichment assessment ID or derived identifier.",
            "local_gap_assessment": "ABSENT: local-gap closure targets four frozen shortlist candidates only.",
            "known_disposition_bug_effect": "NONE: the candidate was excluded before enrichment and closure assessment.",
        }
        payload = {
            "schema_version": VERSION, "reconciliation_run_id": reconciliation_id, "base_run_id": base_run,
            "enrichment_run_id": enrichment_run, "frozen_source_snapshot": snapshot,
            "canonical_artifacts": {"manifest": {"path": str(canonical_manifest), "sha256": canonical_manifest_sha, "persisted_sha256": run["manifest_digest"]}, "tier_membership": {"path": str(canonical_tiers), "sha256": sha(canonical_tiers)}},
            "canonical_replay": {"reproduced": replay_ok, "expected_counts": expected_counts, "replayed_counts": manifest["counts"], "run_id_expected": base_run, "run_id_replayed": manifest["run_id"], "manifest_sha256_expected": run["manifest_digest"], "manifest_sha256_replayed": canonical_manifest_sha, "tier_sets": tiers["sets"], "watch_now": tiers["watch_now"]},
            "canonical_tier_one": {"candidate_id": tier_one_id, "membership_digest": family["membership_digest"], "member_count": family["member_count"], "distinct_creators": family["distinct_creators"], "distinct_direct_funders": family["distinct_direct_funders"], "distinct_parents": family["distinct_parents"], "fingerprint": json.loads(family["fingerprint_json"]), "evidence": json.loads(evidence_row["evidence_json"]), "activity": json.loads(activity_row["metrics_json"])},
            "tier_one_lineage_trace": trace,
            "contract_comparison": {
                "canonical_tier_qualification": {"purpose": "Classify all 220 frozen families into durable v2 tiers.", "population": "All qualified frozen v2 families; WATCH_NOW is 61.", "requirements": manifest["contract"]["tiers"], "tier_1_meaning": "A member of the global frozen family population satisfying WATCH_NOW, strong base, strong alternative, strong atomic, and address-blind persistence."},
                "shortlist_enrichment": {"purpose": "Assess local evidence gaps for ten high-priority canonical Tier-2/Tier-3 candidates.", "population": "Ten selected candidates only; excludes canonical Tier-1.", "near_tier_1_meaning": "A local assessment category, not a tier assignment; zero means none of the ten non-Tier-1 shortlisted candidates had an all-PASS local gap matrix.", "contradiction_semantics": "Additional diagnostic layer; missing retained evidence is NOT_MEASURED, not negative evidence."},
                "conclusion": "NOT_INCONSISTENT: canonical Tier-1=1 and enrichment NEAR_TIER_1=0 answer different population-scoped questions.",
            },
            "outcome": "CANONICAL_TIERS_REPRODUCED_ENRICHMENT_IS_ADDITIONAL_LAYER" if replay_ok and shortlist is None and enrichment is None and closure is None else "CANONICAL_REPLAY_REGRESSION",
            "recommended_next_action": "Correct and replay the enrichment disposition threshold as a separate bounded action, and rename enrichment NEAR_TIER_1 to SHORTLIST_NEAR_CANONICAL_TIER_1 to prevent population-scope confusion.",
            "safety": {"provider_rpc_calls": 0, "queue_replay": False, "membership_mutation": False, "canonical_threshold_modification": False, "enrichment_threshold_modification": False, "operation_promotion": False, "trading_signal": False},
        }
    finally:
        conn.close()
    root = ROOT / base_run / "canonical_reconciliation" / reconciliation_id
    artifact = write(root / "p3r_v2_canonical_tier_replay_reconciliation.v1.json", payload)
    reconciliation_manifest = write(root / "p3r_v2_canonical_tier_replay_reconciliation_manifest.v1.json", {"reconciliation_run_id": reconciliation_id, "base_run_id": base_run, "enrichment_run_id": enrichment_run, "canonical_manifest_sha256": canonical_manifest_sha, "source_snapshot": snapshot, "contract_digest": digest(contract), "artifact": artifact, "result_digest": digest(payload)})
    dest = sqlite3.connect(args.db, timeout=30)
    try:
        dest.execute("PRAGMA busy_timeout=30000")
        dest.executescript(DDL); dest.execute("BEGIN IMMEDIATE")
        dest.execute("INSERT OR REPLACE INTO p3r_v2_canonical_reconciliation_runs VALUES (?,?,?,?,?,?,?)", (reconciliation_id, base_run, enrichment_run, canonical_json(snapshot), artifact["sha256"], reconciliation_manifest["sha256"], payload["outcome"]))
        dest.commit()
    finally:
        dest.close()
    print(json.dumps({"verdict": payload["outcome"], "reconciliation_run_id": reconciliation_id, "canonical_replay": payload["canonical_replay"], "tier_one": payload["canonical_tier_one"], "trace": trace, "artifact": artifact, "manifest": reconciliation_manifest}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
