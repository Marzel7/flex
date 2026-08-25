#!/usr/bin/env python3
"""Export the frozen canonical P3R v2 tier partition with activity annotations."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.ops.p3r_v2_tiering import canonical_json, digest

DB = Path("database/wt_ops_v2.db")
BASE_RUN = "p3r-v2-2dec1d40604c1f7c08c8"
CENSUS_RUN = "p3r-v2-census-b16928146d9b6876c44d"
ROOT = Path("docs/agent_handoff/p3r/v2")
VERSION = "P3R_V2_CANONICAL_TIER_GROUP_EXPORT.v1"
GROUPS = {
    "tier_1": ("V2_TIER_1_ACTIVE_MULTI_LAYER",),
    "tier_2": ("V2_TIER_2_ACTIVE_STRUCTURAL",),
    "tier_3": ("V2_TIER_3_ACTIVE_BASE",),
    "watch_now": ("V2_TIER_1_ACTIVE_MULTI_LAYER", "V2_TIER_2_ACTIVE_STRUCTURAL", "V2_TIER_3_ACTIVE_BASE"),
    "watch_later": ("V2_WATCH_LATER",),
    "dormant": ("V2_DORMANT",),
}
DDL = """
CREATE TABLE IF NOT EXISTS p3r_v2_tier_group_export_runs (
 export_run_id TEXT PRIMARY KEY, base_run_id TEXT NOT NULL, census_run_id TEXT NOT NULL,
 generation_timestamp INTEGER NOT NULL, manifest_sha256 TEXT NOT NULL, verdict TEXT NOT NULL
);
"""


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path: Path, payload: object) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
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
            prior = conn.execute("SELECT base_run_id,census_run_id,generation_timestamp FROM p3r_v2_tier_group_export_runs WHERE export_run_id=?", (args.reproduce_run,)).fetchone()
            if prior is None: raise SystemExit("unknown tier-group export replay")
            base_run, census_run, generated = prior["base_run_id"], prior["census_run_id"], prior["generation_timestamp"]
        else:
            base_run, census_run, generated = BASE_RUN, CENSUS_RUN, int(time.time())
        canonical = conn.execute("SELECT source_snapshot_json,manifest_digest,contract_digest FROM p3r_v2_runs WHERE run_id=?", (base_run,)).fetchone()
        census = conn.execute("SELECT census_timestamp,source_snapshot_json,artifact_sha256 FROM p3r_v2_full_activity_census_runs WHERE census_run_id=?", (census_run,)).fetchone()
        if canonical is None or census is None: raise SystemExit("canonical run or census prerequisite missing")
        manifest_path = ROOT / base_run / "p3r_v2_regeneration_reproducibility_manifest.v1.json"
        if sha(manifest_path) != canonical["manifest_digest"]: raise SystemExit("CANONICAL_TIER_REPLAY_DIVERGENCE")
        members: dict[str, list[str]] = defaultdict(list)
        for row in conn.execute("SELECT candidate_id,mint FROM p3r_v2_candidate_membership WHERE run_id=? ORDER BY candidate_id,mint", (base_run,)):
            members[row["candidate_id"]].append(row["mint"])
        family_rows = {row["candidate_id"]: row for row in conn.execute("SELECT * FROM p3r_v2_candidate_families WHERE run_id=?", (base_run,))}
        tiers = {row["candidate_id"]: row for row in conn.execute("SELECT candidate_id,tier,evidence_json FROM p3r_v2_tier_membership WHERE run_id=?", (base_run,))}
        activity = {row["candidate_id"]: json.loads(row["metrics_json"]) for row in conn.execute("SELECT candidate_id,metrics_json FROM p3r_v2_activity WHERE run_id=?", (base_run,))}
        current = {row["candidate_id"]: json.loads(row["census_json"]) for row in conn.execute("SELECT candidate_id,census_json FROM p3r_v2_full_activity_census_rows WHERE census_run_id=?", (census_run,))}
    finally:
        conn.close()
    if len(family_rows) != 220 or set(family_rows) != set(members) or set(family_rows) != set(tiers) or set(family_rows) != set(current): raise SystemExit("CANONICAL_TIER_REPLAY_DIVERGENCE")
    contract = {"version": VERSION, "canonical_assignment": "Frozen qualification state; current activity is an annotation and does not mutate tier membership.", "canonical_run": base_run, "census_run": census_run, "code_sha256": sha(Path(__file__))}
    export_id = "p3r-v2-tier-groups-" + digest({"base": base_run, "census": census_run, "generated": generated, "contract": contract})[:20]
    records = []
    for candidate_id in sorted(family_rows):
        family, tier = family_rows[candidate_id], tiers[candidate_id]
        evidence = json.loads(tier["evidence_json"])
        ev = evidence["evidence"]
        record = {
            "candidate_id": candidate_id, "canonical_tier": tier["tier"],
            "watch_now": tier["tier"] in GROUPS["watch_now"], "canonical_activity_class": activity[candidate_id]["activity_state"],
            "canonical_member_count": family["member_count"], "member_mints": members[candidate_id],
            "distinct_creators": family["distinct_creators"], "distinct_direct_funders": family["distinct_direct_funders"], "distinct_parents": family["distinct_parents"],
            "base_recurrence": "STRONGLY_RECURRENT" if ev["base_strong"] else "NOT_STRONGLY_RECURRENT",
            "alternative_recurrence": ev["alternative_recurrence"], "alternative_coverage": ev["alternative_coverage"],
            "atomic_recurrence": ev["atomic_recurrence"], "atomic_coverage": ev["atomic_coverage"],
            "address_persistence": "FULLY_ADDRESS_BLIND" if ev["address_blind"] else "ADDRESS_BLINDNESS_NOT_PROVEN",
            "canonical_qualification_gates": evidence, "current_activity_annotation": current[candidate_id],
        }
        records.append(record)
    occurrence = Counter(mint for record in records for mint in record["member_mints"])
    group_records = {name: [record for record in records if record["canonical_tier"] in tiers_for_group] for name, tiers_for_group in GROUPS.items()}
    expected = {"tier_1": 1, "tier_2": 13, "tier_3": 47, "watch_now": 61, "watch_later": 126, "dormant": 33}
    checks = {"family_total": len(records), "group_counts": {name: len(value) for name, value in group_records.items()}, "watch_now_exact_union": set(record["candidate_id"] for record in group_records["watch_now"]) == set(record["candidate_id"] for record in group_records["tier_1"] + group_records["tier_2"] + group_records["tier_3"]), "terminal_groups_disjoint": not (set(record["candidate_id"] for record in group_records["tier_1"]) & set(record["candidate_id"] for record in group_records["tier_2"]) or set(record["candidate_id"] for record in group_records["tier_1"]) & set(record["candidate_id"] for record in group_records["tier_3"]) or set(record["candidate_id"] for record in group_records["tier_2"]) & set(record["candidate_id"] for record in group_records["tier_3"])), "member_total": sum(record["canonical_member_count"] for record in records), "member_unique": len(occurrence), "member_duplicates": sum(count > 1 for count in occurrence.values()), "member_counts_match_lists": all(record["canonical_member_count"] == len(record["member_mints"]) for record in records)}
    if checks["family_total"] != 220 or checks["group_counts"] != expected or not checks["watch_now_exact_union"] or not checks["terminal_groups_disjoint"] or checks["member_total"] != 2357 or not checks["member_counts_match_lists"]: raise SystemExit("CANONICAL_TIER_REPLAY_DIVERGENCE")
    root = ROOT / base_run / "tier_group_exports" / export_id
    artifacts = {}
    for name, selected in group_records.items():
        artifacts[name] = write(root / f"p3r_v2_{name}.v1.json", {"canonical_run_id": base_run, "canonical_manifest_sha256": canonical["manifest_digest"], "source_highwaters": json.loads(canonical["source_snapshot_json"])["highwaters"], "group": name, "family_count": len(selected), "total_member_count": sum(record["canonical_member_count"] for record in selected), "families": selected})
    working = sorted(group_records["watch_now"], key=lambda row: (row["canonical_tier"], -row["current_activity_annotation"]["last_24h"], -row["current_activity_annotation"]["last_7d"], row["candidate_id"]))
    artifacts["watch_now_working_set"] = write(root / "p3r_v2_watch_now_working_set.v1.json", {"canonical_run_id": base_run, "sort": "canonical tier, current 24h desc, current 7d desc, candidate ID", "families": working})
    artifacts["complete_partition"] = write(root / "p3r_v2_complete_canonical_partition.v1.json", {"canonical_run_id": base_run, "canonical_manifest_sha256": canonical["manifest_digest"], "source_highwaters": json.loads(canonical["source_snapshot_json"])["highwaters"], "canonical_assignment_note": contract["canonical_assignment"], "family_count": len(records), "total_member_count": checks["member_total"], "families": records})
    manifest = {"schema_version": VERSION, "export_run_id": export_id, "generated_timestamp": generated, "canonical_run_id": base_run, "canonical_manifest_sha256": canonical["manifest_digest"], "source_highwaters": json.loads(canonical["source_snapshot_json"])["highwaters"], "canonical_replay": "PASS: original run ID and manifest digest present after deterministic materializer replay", "activity_annotation": {"census_run_id": census_run, "census_timestamp": census["census_timestamp"], "edge_highwater": json.loads(census["source_snapshot_json"])["activity_edge_highwater"], "artifact_sha256": census["artifact_sha256"]}, "contract": contract, "artifacts": artifacts, "integrity": checks}
    manifest_artifact = write(root / "p3r_v2_canonical_tier_group_export_manifest.v1.json", manifest)
    dest = sqlite3.connect(args.db, timeout=30)
    try:
        dest.execute("PRAGMA busy_timeout=30000"); dest.executescript(DDL); dest.execute("BEGIN IMMEDIATE")
        dest.execute("INSERT OR REPLACE INTO p3r_v2_tier_group_export_runs VALUES (?,?,?,?,?,?)", (export_id, base_run, census_run, generated, manifest_artifact["sha256"], "P3R_V2_CANONICAL_TIER_GROUPS_RECREATED")); dest.commit()
    finally:
        dest.close()
    anchors = {candidate: next(row for row in records if row["candidate_id"] == candidate) for candidate in ("p3r-v2-d3de29c88fe0ce5fa309", "p3r-v2-c357da9d0d4d560311e4", "p3r-v2-b55dad3b9958cfb69cbc", "p3r-v2-9121fbe9e224ed2e42e8", "p3r-v2-900b89587c6987d582df", "p3r-v2-5a23bde39497a50696cf", "p3r-v2-f35489c6284c60873835")}
    print(json.dumps({"verdict": "P3R_V2_CANONICAL_TIER_GROUPS_RECREATED", "export_run_id": export_id, "integrity": checks, "artifacts": artifacts, "manifest": manifest_artifact, "anchors": anchors}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
