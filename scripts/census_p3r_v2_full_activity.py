#!/usr/bin/env python3
"""Read-only current-activity census for the frozen canonical P3R v2 families."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.ops.p3r_v2_tiering import canonical_json, digest

DB = Path("database/wt_ops_v2.db")
BASE_RUN = "p3r-v2-2dec1d40604c1f7c08c8"
ROOT = Path("docs/agent_handoff/p3r/v2")
VERSION = "P3R_V2_FULL_ACTIVITY_CENSUS.v1"
DDL = """
CREATE TABLE IF NOT EXISTS p3r_v2_full_activity_census_runs (
 census_run_id TEXT PRIMARY KEY, base_run_id TEXT NOT NULL, census_timestamp INTEGER NOT NULL,
 source_snapshot_json TEXT NOT NULL, artifact_sha256 TEXT NOT NULL, manifest_sha256 TEXT NOT NULL, verdict TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS p3r_v2_full_activity_census_rows (
 census_run_id TEXT NOT NULL, candidate_id TEXT NOT NULL, census_json TEXT NOT NULL,
 PRIMARY KEY(census_run_id,candidate_id)
);
"""


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path: Path, value: object) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    return {"path": str(path), "sha256": sha(path)}


def activity(times: list[int], cutoff: int) -> dict:
    return {
        "last_24h": sum(value > cutoff - 86400 for value in times),
        "last_7d": sum(value > cutoff - 7 * 86400 for value in times),
        "last_30d": sum(value > cutoff - 30 * 86400 for value in times),
        "most_recent_observed_launch": max(times) if times else None,
        "hours_since_most_recent_launch": (cutoff - max(times)) / 3600 if times else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DB)
    parser.add_argument("--reproduce-run")
    args = parser.parse_args()
    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        if args.reproduce_run:
            prior = conn.execute("SELECT base_run_id,census_timestamp,source_snapshot_json FROM p3r_v2_full_activity_census_runs WHERE census_run_id=?", (args.reproduce_run,)).fetchone()
            if prior is None: raise SystemExit("unknown census replay")
            base_run, cutoff, snapshot = prior["base_run_id"], prior["census_timestamp"], json.loads(prior["source_snapshot_json"])
        else:
            base_run, cutoff = BASE_RUN, int(time.time())
            canonical = conn.execute("SELECT source_snapshot_json,manifest_digest FROM p3r_v2_runs WHERE run_id=?", (base_run,)).fetchone()
            if canonical is None: raise SystemExit("missing canonical P3R v2 run")
            snapshot = {"canonical_source_snapshot": json.loads(canonical["source_snapshot_json"]), "canonical_manifest_sha256": canonical["manifest_digest"], "activity_edge_highwater": conn.execute("SELECT MAX(rowid) FROM wt_walkback_edge_candidates").fetchone()[0], "activity_timestamp_source": "explicit Unix UTC census timestamp"}
        members: dict[str, list[str]] = defaultdict(list)
        for row in conn.execute("SELECT candidate_id,mint FROM p3r_v2_candidate_membership WHERE run_id=? ORDER BY candidate_id,mint", (base_run,)):
            members[row["candidate_id"]].append(row["mint"])
        tiers = {row["candidate_id"]: row["tier"] for row in conn.execute("SELECT candidate_id,tier FROM p3r_v2_tier_membership WHERE run_id=?", (base_run,))}
        stored_activity = {row["candidate_id"]: json.loads(row["metrics_json"]) for row in conn.execute("SELECT candidate_id,metrics_json FROM p3r_v2_activity WHERE run_id=?", (base_run,))}
        if len(members) != 220 or len(tiers) != 220: raise SystemExit("canonical family population regression")
        mints = sorted({mint for values in members.values() for mint in values})
        marks = ",".join("?" for _ in mints)
        rows = conn.execute(f"SELECT mint,MIN(block_time) AS launch_time FROM wt_walkback_edge_candidates WHERE mint IN ({marks}) AND selection_status='SELECTED' AND block_time IS NOT NULL AND rowid<=? GROUP BY mint", [*mints, snapshot["activity_edge_highwater"]]).fetchall()
        mint_time = {row["mint"]: int(row["launch_time"]) for row in rows}
    finally:
        conn.close()
    contract = {"version": VERSION, "membership": "frozen p3r_v2_candidate_membership from canonical run", "launch_timestamp": "MIN(selected wt_walkback_edge_candidates.block_time) per frozen member mint, matching canonical activity attribution", "windows": {"last_24h": "timestamp > census_timestamp - 86400", "last_7d": "timestamp > census_timestamp - 604800", "last_30d": "timestamp > census_timestamp - 2592000"}, "ranking": ["last_24h DESC", "last_7d DESC", "last_30d DESC", "most_recent_observed_launch DESC", "candidate_id ASC"], "code_sha256": sha(Path(__file__))}
    census_id = "p3r-v2-census-" + digest({"base": base_run, "snapshot": snapshot, "cutoff": cutoff, "contract": contract})[:20]
    family_rows = []
    for candidate_id in sorted(members):
        times = sorted(mint_time[mint] for mint in members[candidate_id] if mint in mint_time)
        row = {"candidate_id": candidate_id, "canonical_tier": tiers[candidate_id], "lifetime_member_count": len(members[candidate_id]), "members_with_retained_launch_timestamp": len(times), "canonical_activity_class": stored_activity[candidate_id]["activity_state"], **activity(times, cutoff)}
        family_rows.append(row)
    ranking = sorted(family_rows, key=lambda row: (-row["last_24h"], -row["last_7d"], -row["last_30d"], -(row["most_recent_observed_launch"] or -1), row["candidate_id"]))
    for position, row in enumerate(ranking, 1): row["rank"] = position
    bins = {"0": 0, "1": 0, "2": 0, "3": 0, "4": 0, "5-9": 0, "10-19": 0, "20+": 0}
    for row in family_rows:
        value = row["last_24h"]
        key = str(value) if value < 5 else "5-9" if value < 10 else "10-19" if value < 20 else "20+"
        bins[key] += 1
    tier_stats = {}
    for tier in sorted(set(tiers.values())):
        subset = [row for row in family_rows if row["canonical_tier"] == tier]
        tier_stats[tier] = {"families": len(subset), "active_24h": sum(row["last_24h"] > 0 for row in subset), "total_24h_launches": sum(row["last_24h"] for row in subset), "max_24h": max(row["last_24h"] for row in subset), "total_7d_launches": sum(row["last_7d"] for row in subset), "max_7d": max(row["last_7d"] for row in subset)}
    by_id = {row["candidate_id"]: row for row in family_rows}
    validations = {candidate: {"previous": previous, "census": {"last_24h": by_id[candidate]["last_24h"], "last_7d": by_id[candidate]["last_7d"]}, "explanation": "The prior shortlist used the canonical retained-evidence cutoff; this census uses its explicit wall-clock UTC timestamp and current retained edge high-water."} for candidate, previous in {"p3r-v2-5a23bde39497a50696cf": [4, 34], "p3r-v2-6437acd385e566e301a7": [3, 21], "p3r-v2-6f7738e9702395ba8ad3": [2, 12], "p3r-v2-32aa0d5125ef777cfd1e": [2, 32], "p3r-v2-e6462dd56f2e695fcb6c": [2, 29]}.items()}
    payload = {"schema_version": VERSION, "census_run_id": census_id, "canonical_run_id": base_run, "canonical_manifest_sha256": snapshot["canonical_manifest_sha256"], "census_timestamp": cutoff, "source_snapshot": snapshot, "contract": contract, "families": ranking, "top_25": ranking[:25], "distribution": {"max_24h": max(row["last_24h"] for row in family_rows), "max_7d": max(row["last_7d"] for row in family_rows), "median_24h": median(row["last_24h"] for row in family_rows), "median_7d": median(row["last_7d"] for row in family_rows), "bins_24h": bins, "families_active_24h": sum(row["last_24h"] > 0 for row in family_rows), "families_active_7d": sum(row["last_7d"] > 0 for row in family_rows)}, "per_tier": tier_stats, "canonical_tier_1": by_id["p3r-v2-d3de29c88fe0ce5fa309"], "shortlist_validation": validations, "safety": {"provider_rpc_calls": 0, "membership_mutation": False, "tier_mutation": False, "queue_replay": False, "operation_promotion": False, "trading_signal": False}, "verdict": "P3R_V2_FULL_ACTIVITY_CENSUS_COMPLETE"}
    root = ROOT / base_run / "full_activity_census" / census_id
    artifact = write(root / "p3r_v2_full_activity_census.v1.json", payload)
    manifest = write(root / "p3r_v2_full_activity_census_manifest.v1.json", {"census_run_id": census_id, "canonical_run_id": base_run, "canonical_manifest_sha256": snapshot["canonical_manifest_sha256"], "census_timestamp": cutoff, "source_snapshot": snapshot, "contract_digest": digest(contract), "artifact": artifact, "family_count": len(family_rows), "ranking_digest": digest(ranking)})
    dest = sqlite3.connect(args.db, timeout=30)
    try:
        dest.execute("PRAGMA busy_timeout=30000"); dest.executescript(DDL); dest.execute("BEGIN IMMEDIATE")
        dest.execute("INSERT OR REPLACE INTO p3r_v2_full_activity_census_runs VALUES (?,?,?,?,?,?,?)", (census_id, base_run, cutoff, canonical_json(snapshot), artifact["sha256"], manifest["sha256"], payload["verdict"]))
        for row in ranking: dest.execute("INSERT OR REPLACE INTO p3r_v2_full_activity_census_rows VALUES (?,?,?)", (census_id, row["candidate_id"], canonical_json(row)))
        dest.commit()
    finally:
        dest.close()
    print(json.dumps({"verdict": payload["verdict"], "census_run_id": census_id, "distribution": payload["distribution"], "top_25": payload["top_25"], "tier_1": payload["canonical_tier_1"], "artifact": artifact, "manifest": manifest}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
