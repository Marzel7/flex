#!/usr/bin/env python3
"""Read-only, bounded census of the current retained walkback queue.

This is deliberately a new current-queue lineage.  It never attempts to
recreate the lost 28,883-mint upstream selector and never writes the source
database, membership, monitoring, or workflow tables.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import time
import uuid
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ops.operation_fingerprint_drift import compare_route
from src.ops.operation_fingerprint_monitoring import DEFINITIONS
from src.ops.p3r_v2_tiering import activity_metrics, base_fingerprint, digest, stable_candidate_id

LEGACY_MINTS = 28_883
TABLES = ("wt_walkback_queue", "wt_walkback_edge_candidates", "wt_walkback_atomic_flows")
OPERATIONS = ("WATCHTOWER", "Byzantine", "FOUR_STEP_30_SOL_14_479K_WSOL_LADDER", "P3R", "P3R_13A04", "WSOL_PROVISION_CLOSE_1_SOL_MINUS_15K")


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha(value: object) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def highwaters(conn: sqlite3.Connection) -> dict[str, int | None]:
    return {table: conn.execute(f"SELECT MAX(rowid) FROM {table}").fetchone()[0] for table in TABLES}


def route(rows: list[tuple[int, str, int | None]]) -> tuple[tuple[int, str, int], ...] | None:
    if not rows or any(amount is None for _, _, amount in rows):
        return None
    return tuple((int(depth), str(mechanism), int(amount)) for depth, mechanism, amount in rows)


def expected_routes(conn: sqlite3.Connection) -> dict[str, tuple[tuple[int, str, int], ...] | None]:
    from src.ops.operation_fingerprint_drift import _expected_route
    return {name: _expected_route(conn, name) for name in OPERATIONS if name != "WATCHTOWER"}


def exact_matches(conn: sqlite3.Connection, mint: str, observed: tuple[tuple[int, str, int], ...] | None,
                  expected: dict[str, tuple[tuple[int, str, int], ...] | None]) -> set[str]:
    """Use the existing detector predicates, without persisting their results."""
    from src.ops.d3de_operation import is_d0_match, selected_evidence as sentinel_evidence
    from src.ops.p3r_profile_candidate_matcher import evaluate_mint
    from src.ops.wsol_10_sol_four_step_operation import is_strict_match, selected_evidence as byzantine_evidence
    matches: set[str] = set()
    # The authoritative predicates are comparatively expensive.  A detector
    # cannot possibly match unless its retained route projection is exact, so
    # use that read-only prefilter before invoking it.
    if observed == expected.get("Byzantine") and is_strict_match(byzantine_evidence(conn, mint)):
        matches.add("Byzantine")
    if observed == expected.get("FOUR_STEP_30_SOL_14_479K_WSOL_LADDER") and is_d0_match(sentinel_evidence(conn, mint)):
        matches.add("FOUR_STEP_30_SOL_14_479K_WSOL_LADDER")
    if observed in {expected.get("P3R"), expected.get("P3R_13A04")}:
        profile = evaluate_mint(conn, mint)
        if profile:
            matches.update(profile.matching_profiles)
    return matches


def run(db: Path, fixed_highwaters: dict[str, int | None] | None = None,
        fixed_source_identity: dict | None = None) -> dict:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        hw = fixed_highwaters or highwaters(conn)
        qhw, ehw, ahw = (hw[name] for name in TABLES)
        queue = [dict(row) for row in conn.execute(
            "SELECT rowid,mint,creator,funder_wallet,status,intelligence_outcome,enqueued_at,completed_at,updated_at,create_anchor_block_time,funder_block_time "
            "FROM wt_walkback_queue WHERE rowid<=? ORDER BY mint,rowid", (qhw,))]
        q_by_mint = {row["mint"]: row for row in queue}
        selected: dict[str, list[tuple[int, str, int | None]]] = defaultdict(list)
        times: dict[str, list[int]] = defaultdict(list)
        for row in conn.execute(
            "SELECT mint,hop_depth,mechanism,amount_lamports,block_time FROM wt_walkback_edge_candidates "
            "WHERE rowid<=? AND selection_status='SELECTED' ORDER BY mint,hop_depth,signature", (ehw,)):
            selected[row["mint"]].append((row["hop_depth"], row["mechanism"], row["amount_lamports"]))
            if row["block_time"] is not None:
                times[row["mint"]].append(int(row["block_time"]))
        known_members = {row["mint"]: row["display_name"] for row in conn.execute(
            "SELECT m.mint,o.display_name FROM operator_launch_membership m JOIN operators o USING(operator_id)")}
        membership_total = conn.execute("SELECT COUNT(*) FROM operator_launch_membership").fetchone()[0]
        provisional_total = conn.execute("SELECT COUNT(*) FROM provisional_operation_matches").fetchone()[0]
        potential_workflow_total = conn.execute("SELECT COUNT(*) FROM potential_operation_workflows").fetchone()[0]
        status = Counter(str(row["status"] or "NULL") for row in queue)
        outcomes = Counter(str(row["intelligence_outcome"] or "NULL") for row in queue)
        observed_routes = {mint: route(rows) for mint, rows in selected.items()}
        expected = expected_routes(conn)
        metrics = {name: Counter() for name in OPERATIONS}
        exact_mints = {name: [] for name in OPERATIONS}
        for mint in sorted(q_by_mint):
            exact = exact_matches(conn, mint, observed_routes.get(mint), expected) if mint in selected else set()
            for name in OPERATIONS:
                if name == "WATCHTOWER":
                    # Dynamic role discovery is intentionally not projected as a
                    # generic route/near-match detector. Existing confirmed
                    # membership is the only safe retained exact reference here.
                    classification = "EXACT_MATCH" if known_members.get(mint) == "WATCHTOWER" else "UNOBSERVABLE"
                elif name in exact:
                    classification = "EXACT_MATCH"
                else:
                    classification = compare_route(expected[name], observed_routes.get(mint))[0] if expected.get(name) else "UNOBSERVABLE"
                metrics[name][classification] += 1
                if classification == "EXACT_MATCH":
                    exact_mints[name].append(mint)
        cutoff = max((value for values in times.values() for value in values), default=None)
        operation_rows = []
        for name in OPERATIONS:
            values = [min(times[mint]) for mint in exact_mints[name] if times.get(mint)]
            activity = activity_metrics(values, cutoff) if cutoff is not None else {"activity_state": "ACTIVITY_UNKNOWN"}
            operation_rows.append({"operation": name, "fingerprint_id": DEFINITIONS[name].fingerprint_id,
                                   "strategy": "DYNAMIC_ROLE_DISCOVERY" if name == "WATCHTOWER" else "EXISTING_DETECTOR_AND_READ_ONLY_ROUTE_COMPARISON",
                                   "exact": metrics[name]["EXACT_MATCH"],
                                   "near": metrics[name]["NEAR_MATCH_ONE_DIMENSION"] + metrics[name]["NEAR_MATCH_MULTI_DIMENSION"],
                                   "no_relationship": metrics[name]["NO_MEANINGFUL_RELATIONSHIP"],
                                   "unobservable": metrics[name]["UNOBSERVABLE"], **activity})
        grouped: dict[str, dict] = {}
        for mint, rows in selected.items():
            q = q_by_mint.get(mint)
            if not q or not q.get("creator") or not q.get("funder_wallet"):
                continue
            fingerprint = base_fingerprint(rows)
            if not fingerprint["edges"] or not any(edge["amount_lamports"] is not None for edge in fingerprint["edges"]):
                continue
            item = grouped.setdefault(sha(fingerprint), {"fingerprint": fingerprint, "mints": [], "creators": set(), "funders": set(), "times": []})
            item["mints"].append(mint); item["creators"].add(q["creator"]); item["funders"].add(q["funder_wallet"])
            item["times"].extend(times.get(mint, []))
        families = []
        for item in grouped.values():
            mints = sorted(set(item["mints"]))
            if len(mints) < 3 or len(item["creators"]) < 2 or len(item["funders"]) < 2:
                continue
            activity = activity_metrics(item["times"], cutoff) if item["times"] and cutoff else {"activity_state": "ACTIVITY_UNKNOWN"}
            overlaps = sorted({known_members[mint] for mint in mints if mint in known_members})
            families.append({"candidate_id": stable_candidate_id(item["fingerprint"]), "members": len(mints),
                             "creators": len(item["creators"]), "direct_funders": len(item["funders"]),
                             "known_operation_overlaps": overlaps, **activity})
        families.sort(key=lambda x: (-x["members"], x["candidate_id"]))
        queue_mints = [row["mint"] for row in queue]
        result = {
            "schema_version": "P3R_CURRENT_QUEUE_CENSUS.v1", "source": str(db),
            "source_snapshot": {"highwaters": hw, "read_only": True, "queue_order": "mint,rowid",
                                "database_stat": fixed_source_identity or {"size": os.stat(db).st_size, "mtime_ns": os.stat(db).st_mtime_ns},
                                "extractor_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},
            "legacy_reference": {"unique_mints": LEGACY_MINTS, "comparison": "NOT_COHORT_NORMALIZED", "historical_mint_set": "UNRECOVERED", "exact_post_reference_tail": "UNAVAILABLE"},
            "queue": {"rows": len(queue), "unique_mints": len(set(queue_mints)), "duplicates": len(queue)-len(set(queue_mints)),
                      "net_population_growth": len(set(queue_mints))-LEGACY_MINTS, "min_rowid": min((r["rowid"] for r in queue), default=None),
                      "max_rowid": max((r["rowid"] for r in queue), default=None), "min_enqueued_at": min((r["enqueued_at"] for r in queue if r["enqueued_at"] is not None), default=None),
                      "max_updated_at": max((r["updated_at"] for r in queue if r["updated_at"] is not None), default=None),
                      "status": dict(sorted(status.items())), "outcomes": dict(sorted(outcomes.items())),
                      "selected_edge_observable": len(observed_routes), "selected_edge_unobservable": len(queue)-len(observed_routes)},
            "operators": operation_rows, "families": {"count": len(families), "active": sum(f["activity_state"] in {"HIGH_ACTIVITY", "VERY_HIGH_ACTIVITY"} for f in families), "top": families[:20]},
            "membership_neutrality": {"confirmed_membership_before_after": [membership_total, membership_total], "provisional_before_after": [provisional_total, provisional_total], "potential_workflow_before_after": [potential_workflow_total, potential_workflow_total], "census_caused_delta": 0},
            "safety": {"source_database_writes": 0, "membership_writes": 0, "workflow_writes": 0, "provider_calls": 0, "queue_replay": False},
        }
        result["census_digest"] = sha(result)
        return result
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=Path("database/wt_ops_v2.db"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--replay-output", type=Path, required=True)
    args = parser.parse_args()
    first = run(args.db)
    replay = run(args.db, first["source_snapshot"]["highwaters"], first["source_snapshot"]["database_stat"])
    reproducible = first["census_digest"] == replay["census_digest"]
    run_id = "p3r-current-queue-" + uuid.uuid4().hex[:12]
    envelope = {"run_id": run_id, "first": first, "replay": {"census_digest": replay["census_digest"], "equal": reproducible},
                "verdict": "P3R_CURRENT_QUEUE_CENSUS_COMPLETE" if reproducible else "P3R_CURRENT_QUEUE_CENSUS_NON_REPRODUCIBLE"}
    for path, value in ((args.output, envelope), (args.replay_output, replay)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    if not reproducible:
        raise SystemExit(2)
    print(json.dumps({"run_id": run_id, "digest": first["census_digest"], "rows": first["queue"]["rows"], "families": first["families"]["count"]}))


if __name__ == "__main__":
    main()
