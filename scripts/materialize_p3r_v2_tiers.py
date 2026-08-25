#!/usr/bin/env python3
"""Materialize the durable, read-only-source P3R v2 candidate tier lineage."""
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

from src.ops.p3r_v2_tiering import (
    DISCOVERY_CONTRACT_VERSION, FINGERPRINT_CONTRACT_VERSION, TIER_CONTRACT_VERSION,
    activity_metrics, alternative_fingerprint, assign_tier, atomic_fingerprint,
    base_fingerprint, canonical_json, digest, recurrence_state, stable_candidate_id,
)

DB_DEFAULT = Path("database/wt_ops_v2.db")
ARTIFACT_DIR = Path("docs/agent_handoff/p3r/v2")


DDL = """
CREATE TABLE IF NOT EXISTS p3r_v2_runs (run_id TEXT PRIMARY KEY, created_at INTEGER NOT NULL, source_snapshot_json TEXT NOT NULL, contract_digest TEXT NOT NULL, manifest_digest TEXT, verdict TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS p3r_v2_contracts (name TEXT NOT NULL, version TEXT NOT NULL, contract_json TEXT NOT NULL, digest TEXT NOT NULL, created_at INTEGER NOT NULL, PRIMARY KEY(name,version,digest));
CREATE TABLE IF NOT EXISTS p3r_v2_candidate_families (run_id TEXT NOT NULL, candidate_id TEXT NOT NULL, contract_version TEXT NOT NULL, fingerprint_json TEXT NOT NULL, fingerprint_hash TEXT NOT NULL, membership_digest TEXT NOT NULL, member_count INTEGER NOT NULL, distinct_creators INTEGER NOT NULL, distinct_direct_funders INTEGER NOT NULL, distinct_parents INTEGER NOT NULL, provenance_json TEXT NOT NULL, status TEXT NOT NULL, created_at INTEGER NOT NULL, PRIMARY KEY(run_id,candidate_id));
CREATE TABLE IF NOT EXISTS p3r_v2_candidate_membership (run_id TEXT NOT NULL, candidate_id TEXT NOT NULL, mint TEXT NOT NULL, membership_reason TEXT NOT NULL, provenance_json TEXT NOT NULL, PRIMARY KEY(run_id,candidate_id,mint));
CREATE TABLE IF NOT EXISTS p3r_v2_activity (run_id TEXT NOT NULL, candidate_id TEXT NOT NULL, timestamp_contract_version TEXT NOT NULL, metrics_json TEXT NOT NULL, PRIMARY KEY(run_id,candidate_id));
CREATE TABLE IF NOT EXISTS p3r_v2_tier_membership (run_id TEXT NOT NULL, candidate_id TEXT NOT NULL, tier TEXT NOT NULL, evidence_json TEXT NOT NULL, PRIMARY KEY(run_id,candidate_id));
CREATE TABLE IF NOT EXISTS p3r_v2_shortlist (run_id TEXT NOT NULL, rank INTEGER NOT NULL, candidate_id TEXT NOT NULL, classification TEXT NOT NULL, next_investigation TEXT NOT NULL, rationale_json TEXT NOT NULL, PRIMARY KEY(run_id,rank));
"""


def sha_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_artifact(run_id: str, name: str, payload: object) -> dict:
    path = ARTIFACT_DIR / run_id / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n")
    return {"path": str(path), "sha256": sha_path(path)}


def source_snapshot(conn: sqlite3.Connection) -> dict:
    tables = ("wt_walkback_queue", "wt_walkback_edge_candidates", "wt_walkback_atomic_flows")
    highwaters = {table: conn.execute(f"SELECT MAX(rowid) FROM {table}").fetchone()[0] for table in tables}
    schema = {table: conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()[0] for table in tables}
    return {"database": "database/wt_ops_v2.db", "highwaters": highwaters, "schema_sha256": digest(schema), "read_only_source": True}


def contracts() -> dict:
    return {
        "discovery": {
            "version": DISCOVERY_CONTRACT_VERSION,
            "source": "Selected retained walkback edges with non-null creator and direct funder from wt_walkback_queue.",
            "base_fingerprint": "Sorted unique (hop_depth, mechanism, amount_lamports); amount 0/null is normalized to null and never positive evidence.",
            "family_minimums": {"members": 3, "distinct_creators": 2, "distinct_direct_funders": 2},
            "topology": "Only documented hop_depth from retained selected edges; no legacy topology vector is used.",
        },
        "fingerprints": {
            "version": FINGERPRINT_CONTRACT_VERSION,
            "base": "Address-free selected edge structure.",
            "alternative": "Address-free ALTERNATIVE edge structure per member.",
            "atomic": "Address-free instruction order, create/sync/close flags, and non-zero transfer amount per atomic flow.",
            "canonical_serialization": "JSON sort_keys=true, compact separators, ASCII.",
        },
        "activity": {
            "version": "P3R_V2_ACTIVITY.v1",
            "timestamp": "MIN(selected wt_walkback_edge_candidates.block_time) per mint; observed activity proxy, not token launch time.",
            "states": {"VERY_HIGH_ACTIVITY": "last_7d >= 7 and max_rolling_24h >= 2", "HIGH_ACTIVITY": "last_7d >= 3 and max_rolling_24h >= 1", "REGULAR_ACTIVITY": "last_30d >= 2", "LOW_ACTIVITY": "remaining observed", "DORMANT": "last_30d = 0"},
        },
        "tiers": {
            "version": TIER_CONTRACT_VERSION,
            "tier_1": "WATCH_NOW + strong base + strong alternative + strong atomic + address blind",
            "tier_2": "WATCH_NOW + strong base + strong alternative, not tier 1",
            "tier_3": "WATCH_NOW + strong base, not tier 1/2",
            "strong_recurrence": "at least 3 observations, at least 50% member coverage, dominant fingerprint at least 3 observations and at least 60% of observed fingerprints",
            "address_blind": "at least 3 distinct creators, direct funders, and selected parents; no one address occupies more than 50% of its populated role observations",
        },
        "promotion": {
            "version": "P3R_V2_PROMOTION_CONTRACT.v1",
            "tier_3_to_tier_2": "Retain WATCH_NOW and strong base; add strong alternative-edge recurrence. Activity alone never promotes.",
            "tier_2_to_tier_1": "Retain tier-2 evidence; add strong atomic recurrence and address-independent persistence. Activity alone never promotes.",
            "watch_later_to_watch_now": "Meet the versioned HIGH_ACTIVITY or VERY_HIGH_ACTIVITY thresholds; this does not promote behavioural tier.",
            "dormant_to_reactivated": "Receive new retained selected-edge activity after the snapshot cutoff; reclassify activity only, then re-evaluate existing evidence.",
        },
    }


def build(conn: sqlite3.Connection, snapshot: dict) -> list[dict]:
    hw = snapshot["highwaters"]
    q = {row[0]: row for row in conn.execute(
        "SELECT mint,creator,funder_wallet FROM wt_walkback_queue WHERE rowid<=?", (hw["wt_walkback_queue"],)
    )}
    selected: dict[str, list[tuple[int, str, int | None, str | None, int | None]]] = defaultdict(list)
    alternative: dict[str, list[tuple[int, str, int | None]]] = defaultdict(list)
    times: dict[str, list[int]] = defaultdict(list)
    for mint, depth, mechanism, amount, parent, block_time, status in conn.execute(
        "SELECT mint,hop_depth,mechanism,amount_lamports,candidate_parent,block_time,selection_status "
        "FROM wt_walkback_edge_candidates WHERE rowid<=? AND selection_status IN ('SELECTED','ALTERNATIVE')", (hw["wt_walkback_edge_candidates"],)
    ):
        if status == "SELECTED":
            selected[mint].append((depth, mechanism, amount, parent, block_time))
            if block_time is not None:
                times[mint].append(int(block_time))
        else:
            alternative[mint].append((depth, mechanism, amount))
    atomic: dict[str, list[dict]] = defaultdict(list)
    for mint, order, amount, create, sync, close in conn.execute(
        "SELECT mint,instruction_order_json,transfer_lamports,has_create,has_sync_native,has_close "
        "FROM wt_walkback_atomic_flows WHERE rowid<=?", (hw["wt_walkback_atomic_flows"],)
    ):
        try:
            parsed_order = json.loads(order)
        except json.JSONDecodeError:
            continue
        atomic[mint].append(atomic_fingerprint(parsed_order, create, sync, close, amount))
    grouped: dict[str, dict] = {}
    for mint, edges in selected.items():
        queue = q.get(mint)
        if not queue or not queue[1] or not queue[2]:
            continue
        fp = base_fingerprint((depth, mechanism, amount) for depth, mechanism, amount, _, _ in edges)
        if not fp["edges"] or not any(edge["amount_lamports"] is not None for edge in fp["edges"]):
            continue
        key = digest(fp)
        entry = grouped.setdefault(key, {"fingerprint": fp, "mints": [], "creators": [], "funders": [], "parents": [], "alternatives": [], "atomic": [], "times": []})
        entry["mints"].append(mint)
        entry["creators"].append(queue[1])
        entry["funders"].append(queue[2])
        entry["parents"].extend(parent for _, _, _, parent, _ in edges if parent)
        if alternative[mint]:
            entry["alternatives"].append(alternative_fingerprint(alternative[mint]))
        entry["atomic"].extend(atomic[mint])
        if times[mint]:
            entry["times"].append(min(times[mint]))
    cutoff = max((value for entry in grouped.values() for value in entry["times"]), default=int(time.time()))
    families = []
    for entry in grouped.values():
        mints = sorted(set(entry["mints"]))
        creators, funders, parents = entry["creators"], entry["funders"], entry["parents"]
        if len(mints) < 3 or len(set(creators)) < 2 or len(set(funders)) < 2:
            continue
        member_count = len(mints)
        alternative_state, alternative_coverage, alternative_dominant = recurrence_state(entry["alternatives"], member_count)
        atomic_state, atomic_coverage, atomic_dominant = recurrence_state(entry["atomic"], member_count)
        def diversified(values: list[str]) -> bool:
            return len(set(values)) >= 3 and max(Counter(values).values()) / len(values) <= .5
        address_blind = diversified(creators) and diversified(funders) and diversified(parents)
        base_strong = member_count >= 5 and len(set(creators)) >= 3 and len(set(funders)) >= 3
        metrics = activity_metrics(entry["times"], cutoff)
        tier = assign_tier(metrics["activity_state"], base_strong, alternative_state, atomic_state, address_blind)
        candidate_id = stable_candidate_id(entry["fingerprint"])
        evidence = {"base_strong": base_strong, "alternative_recurrence": alternative_state, "alternative_coverage": alternative_coverage, "alternative_dominant_count": alternative_dominant, "atomic_recurrence": atomic_state, "atomic_coverage": atomic_coverage, "atomic_dominant_count": atomic_dominant, "address_blind": address_blind}
        missing = []
        if alternative_state != "STRONGLY_RECURRENT": missing.append("ALTERNATIVE_RECURRENCE_NOT_PROVEN")
        if atomic_state == "NOT_OBSERVED": missing.append("ATOMIC_COVERAGE_INSUFFICIENT")
        elif atomic_state != "STRONGLY_RECURRENT": missing.append("ATOMIC_RECURRENCE_NOT_PROVEN")
        if not address_blind: missing.append("ADDRESS_BLINDNESS_NOT_PROVEN")
        if tier == "V2_TIER_2_ACTIVE_STRUCTURAL" and atomic_state == "STRONGLY_RECURRENT" and not address_blind: category = "ADDRESS_VALIDATION_REQUIRED"
        elif tier == "V2_TIER_2_ACTIVE_STRUCTURAL": category = "ATOMIC_ENRICHMENT_REQUIRED"
        elif tier == "V2_TIER_3_ACTIVE_BASE": category = "ACTIVE_BASE_ONLY"
        else: category = "HOLD"
        families.append({"candidate_id": candidate_id, "fingerprint": entry["fingerprint"], "mints": mints, "metrics": metrics, "tier": tier, "evidence": evidence, "missing_tier_1_evidence": missing, "category": category, "distinct_creators": len(set(creators)), "distinct_direct_funders": len(set(funders)), "distinct_parents": len(set(parents))})
    return sorted(families, key=lambda family: family["candidate_id"])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DB_DEFAULT)
    parser.add_argument("--reproduce-run", help="Reuse an existing durable source snapshot exactly.")
    args = parser.parse_args()
    now = int(time.time())
    replay_snapshot = None
    replay_created_at = None
    if args.reproduce_run:
        replay = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
        try:
            row = replay.execute("SELECT source_snapshot_json,created_at FROM p3r_v2_runs WHERE run_id=?", (args.reproduce_run,)).fetchone()
            if row is None:
                raise SystemExit(f"unknown reproducibility run: {args.reproduce_run}")
            replay_snapshot = json.loads(row[0])
            replay_created_at = int(row[1])
        finally:
            replay.close()
    source = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    try:
        source.execute("BEGIN")
        snapshot = replay_snapshot or source_snapshot(source)
        family_rows = build(source, snapshot)
    finally:
        source.close()
    if replay_created_at is not None:
        now = replay_created_at
    contract = contracts()
    contract["code_bindings"] = {
        "version": "P3R_V2_CODE_BINDINGS.v1",
        "materializer_sha256": sha_path(Path(__file__)),
        "constructor_sha256": sha_path(Path(__file__).resolve().parents[1] / "src/ops/p3r_v2_tiering.py"),
    }
    contract_digest = digest(contract)
    run_id = "p3r-v2-" + digest({"snapshot": snapshot, "contract": contract_digest})[:20]
    for family in family_rows:
        family["membership_digest"] = digest(family["mints"])
    tier_sets = {tier: [row["candidate_id"] for row in family_rows if row["tier"] == tier] for tier in ("V2_TIER_1_ACTIVE_MULTI_LAYER", "V2_TIER_2_ACTIVE_STRUCTURAL", "V2_TIER_3_ACTIVE_BASE", "V2_WATCH_LATER", "V2_DORMANT")}
    watch_now = sorted(tier_sets["V2_TIER_1_ACTIVE_MULTI_LAYER"] + tier_sets["V2_TIER_2_ACTIVE_STRUCTURAL"] + tier_sets["V2_TIER_3_ACTIVE_BASE"])
    shortlist_pool = [row for row in family_rows if row["tier"] in {"V2_TIER_2_ACTIVE_STRUCTURAL", "V2_TIER_3_ACTIVE_BASE"}]
    shortlist_pool.sort(key=lambda row: (row["metrics"].get("max_rolling_24h", 0), row["metrics"].get("last_7d", 0), row["evidence"]["atomic_coverage"], len(row["mints"])), reverse=True)
    shortlist = []
    for rank, row in enumerate(shortlist_pool[:10], 1):
        next_step = "local atomic analysis" if row["evidence"]["atomic_recurrence"] != "STRONGLY_RECURRENT" else "address-rotation validation"
        shortlist.append({"rank": rank, "candidate_id": row["candidate_id"], "classification": row["category"], "next_investigation": next_step, "rationale": {"last_7d": row["metrics"].get("last_7d"), "max_rolling_24h": row["metrics"].get("max_rolling_24h"), "atomic_coverage": row["evidence"]["atomic_coverage"], "missing": row["missing_tier_1_evidence"]}})
    membership = {"run_id": run_id, "source_snapshot": snapshot, "contract_digest": contract_digest, "candidate_ids": [row["candidate_id"] for row in family_rows], "families": family_rows}
    artifacts = {}
    artifacts["discovery_contract"] = write_artifact(run_id, "p3r_v2_candidate_discovery_contract.v1.json", contract["discovery"])
    artifacts["fingerprint_contracts"] = write_artifact(run_id, "p3r_v2_fingerprint_contracts.v1.json", contract["fingerprints"])
    artifacts["activity_contract"] = write_artifact(run_id, "p3r_v2_activity_contract.v1.json", contract["activity"])
    artifacts["promotion_contract"] = write_artifact(run_id, "p3r_v2_promotion_contract.v1.json", contract["promotion"])
    artifacts["membership"] = write_artifact(run_id, "p3r_v2_candidate_membership.v1.json", membership)
    artifacts["tiers"] = write_artifact(run_id, "p3r_v2_tier_membership.v1.json", {"run_id": run_id, "source_snapshot": snapshot, "contract_digest": contract_digest, "sets": tier_sets, "watch_now": watch_now})
    artifacts["shortlist"] = write_artifact(run_id, "p3r_v2_next_investigation_shortlist.v1.json", {"run_id": run_id, "shortlist": shortlist})
    artifacts["known_v1_references"] = write_artifact(run_id, "p3r_v2_known_v1_reference_mapping.v1.json", {
        "historical_reference_ids": ["p3r-candidate-13a04d7da7a1fc55", "p3r-candidate-af5004dfe42cfe11", "p3r-candidate-bf30e77ee07e312b", "p3r-candidate-ec1b6cff80643746"],
        "mapping_status": "UNMAPPED_NOT_FORCED", "reason": "The v1 candidate membership artifacts are unrecoverable; v2 identities are intentionally new and address-independent.",
    })
    manifest = {"schema_version": "P3R_V2_REGENERATION_MANIFEST.v1", "run_id": run_id, "created_at": now, "source_snapshot": snapshot, "contract_digest": contract_digest, "contract": contract, "artifacts": artifacts, "counts": {"families": len(family_rows), "member_mints": sum(len(row["mints"]) for row in family_rows), "watch_now": len(watch_now), **{key: len(value) for key, value in tier_sets.items()}}}
    artifacts["manifest"] = write_artifact(run_id, "p3r_v2_regeneration_reproducibility_manifest.v1.json", manifest)
    manifest_digest = artifacts["manifest"]["sha256"]
    dest = sqlite3.connect(args.db)
    try:
        dest.executescript(DDL)
        dest.execute("BEGIN IMMEDIATE")
        dest.execute("INSERT OR REPLACE INTO p3r_v2_runs VALUES (?,?,?,?,?,?)", (run_id, now, canonical_json(snapshot), contract_digest, manifest_digest, "P3R_V2_ACTIVE_CANDIDATE_TIERS_QUALIFIED"))
        for name, payload in contract.items():
            dest.execute("INSERT OR IGNORE INTO p3r_v2_contracts VALUES (?,?,?,?,?)", (name, payload["version"], canonical_json(payload), digest(payload), now))
        for row in family_rows:
            provenance = {"source_snapshot": snapshot, "membership_reason": "v2 address-independent selected-edge fingerprint with creator and direct-funder diversity"}
            dest.execute("INSERT OR REPLACE INTO p3r_v2_candidate_families VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (run_id, row["candidate_id"], DISCOVERY_CONTRACT_VERSION, canonical_json(row["fingerprint"]), digest(row["fingerprint"]), row["membership_digest"], len(row["mints"]), row["distinct_creators"], row["distinct_direct_funders"], row["distinct_parents"], canonical_json(provenance), "QUALIFIED", now))
            for mint in row["mints"]:
                dest.execute("INSERT OR REPLACE INTO p3r_v2_candidate_membership VALUES (?,?,?,?,?)", (run_id, row["candidate_id"], mint, "BASE_SELECTED_EDGE_FINGERPRINT", canonical_json(provenance)))
            dest.execute("INSERT OR REPLACE INTO p3r_v2_activity VALUES (?,?,?,?)", (run_id, row["candidate_id"], contract["activity"]["version"], canonical_json(row["metrics"])))
            dest.execute("INSERT OR REPLACE INTO p3r_v2_tier_membership VALUES (?,?,?,?)", (run_id, row["candidate_id"], row["tier"], canonical_json({"evidence": row["evidence"], "missing_tier_1_evidence": row["missing_tier_1_evidence"]})))
        for item in shortlist:
            dest.execute("INSERT OR REPLACE INTO p3r_v2_shortlist VALUES (?,?,?,?,?,?)", (run_id, item["rank"], item["candidate_id"], item["classification"], item["next_investigation"], canonical_json(item["rationale"])))
        dest.commit()
    finally:
        dest.close()
    print(json.dumps({"verdict": "P3R_V2_ACTIVE_CANDIDATE_TIERS_QUALIFIED", "run_id": run_id, "manifest": artifacts["manifest"], "counts": manifest["counts"], "tiers": tier_sets, "shortlist": shortlist}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
