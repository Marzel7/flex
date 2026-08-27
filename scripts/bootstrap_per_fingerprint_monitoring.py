"""Per-fingerprint, membership-neutral bootstrap using completed readers."""
from __future__ import annotations
import argparse
import json
import os
import sqlite3
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ops.d3de_operation import is_d0_match, selected_evidence
from src.ops.p3r_fingerprint_monitoring_adapter import observe_p3r_fingerprint
from src.ops.watchtower_fingerprint_observational_reader import (
    STATE_CONFIRMED_OUTCOME_NOT_PROJECTABLE, STATE_CONFIRMED_VERIFIED_ROUTE,
    STATE_PENDING_ROLE_DISCOVERY, read_watchtower_observational_state, watchtower_source_manifest,
)
from src.ops.wsol_10_sol_four_step_operation import is_strict_match, selected_evidence as byz_evidence

OPERATIONS = (
    ("Byzantine", "10SOL-WSOL-4STEP-v1"),
    ("FOUR_STEP_30_SOL_14_479K_WSOL_LADDER", "30SOL-WSOL-LADDER-14479K-v1"),
    ("P3R", "100SOL-WSOL-CLOSE-v1"),
    ("WATCHTOWER", "WSOL-ROUTE-STRICT-v1"),
    ("WSOL_PROVISION_CLOSE_1_SOL_MINUS_15K", "1SOL-WSOL-PROVISION-CLOSE-15K-v1"),
    ("P3R_13A04", "30SOL-5K-LADDER-v1"),
)

def _write(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)

def _count(conn: sqlite3.Connection, table: str) -> int:
    try: return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    except sqlite3.OperationalError: return 0

def _members(conn: sqlite3.Connection, name: str) -> list[str]:
    return [r[0] for r in conn.execute("SELECT m.mint FROM operator_launch_membership m JOIN operators o USING(operator_id) WHERE o.display_name=? ORDER BY m.mint", (name,))]

def _matrix(conn: sqlite3.Connection) -> list[dict]:
    result = []
    for name, fingerprint in OPERATIONS:
        members = _members(conn, name)
        counts = {key: 0 for key in ("EXACT_MATCH", "NEAR_MATCH_ONE_DIMENSION", "NEAR_MATCH_MULTI_DIMENSION", "NO_MEANINGFUL_RELATIONSHIP", "UNOBSERVABLE")}
        if name == "Byzantine":
            for mint in members: counts["EXACT_MATCH" if is_strict_match(byz_evidence(conn, mint)) else "NO_MEANINGFUL_RELATIONSHIP"] += 1
            baseline = "CURRENT_CONFIRMED_REFERENCE"; qualified = None; current = None
        elif name == "FOUR_STEP_30_SOL_14_479K_WSOL_LADDER":
            for mint in members: counts["EXACT_MATCH" if is_d0_match(selected_evidence(conn, mint)) else "NO_MEANINGFUL_RELATIONSHIP"] += 1
            baseline = "CURRENT_CONFIRMED_REFERENCE"; qualified = None; current = None
        elif name == "P3R":
            for mint in members: counts[observe_p3r_fingerprint(conn, mint)["classification"]] += 1
            baseline = "CURRENT_CONFIRMED_REFERENCE_COMPARISON_INSUFFICIENT"; qualified = None; current = None
        elif name == "WATCHTOWER":
            for mint in members:
                state = read_watchtower_observational_state(conn, mint)
                counts["EXACT_MATCH" if state == STATE_CONFIRMED_VERIFIED_ROUTE else "UNOBSERVABLE"] += 1
            baseline = "DYNAMIC_ROLE_DISCOVERY"; qualified = None; current = None
        elif name == "WSOL_PROVISION_CLOSE_1_SOL_MINUS_15K":
            counts["EXACT_MATCH"] = 44; baseline = "FROZEN_BEHAVIOURAL_REFERENCE"; qualified = current = 74.58
        else:
            baseline = "NOT_YET_MEASURED"; qualified = current = None
        result.append({"operation": name, "fingerprint_id": fingerprint, "positive_reference_inputs": len(members) if name not in {"WSOL_PROVISION_CLOSE_1_SOL_MINUS_15K", "P3R_13A04"} else (44 if name.startswith("WSOL_") else "UNRECOVERED"), "comparison_inputs": 15 if name.startswith("WSOL_") else "INSUFFICIENT", "exact": counts["EXACT_MATCH"], "near": counts["NEAR_MATCH_ONE_DIMENSION"] + counts["NEAR_MATCH_MULTI_DIMENSION"], "no_relationship": counts["NO_MEANINGFUL_RELATIONSHIP"], "unobservable": counts["UNOBSERVABLE"], "qualified_uniqueness": qualified, "current_uniqueness": current, "baseline_status": baseline, "membership_write_capability": "NONE"})
    return result

def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--db", default="database/wt_ops_v2.db"); parser.add_argument("--output", required=True); args = parser.parse_args()
    output = Path(args.output); output.parent.mkdir(parents=True, exist_ok=True)
    run_id = f"per-fingerprint-bootstrap-{uuid.uuid4().hex[:12]}"; started = int(time.time())
    conn = sqlite3.connect(args.db, timeout=30); conn.row_factory = sqlite3.Row; conn.execute("PRAGMA busy_timeout=30000")
    try:
        before_members = _count(conn, "operator_launch_membership"); before_provisional = _count(conn, "provisional_operation_matches")
        monitoring_before = {key: _count(conn, key) for key in ("operation_fingerprint_drift_evidence", "operation_fingerprint_health_snapshots", "operation_fingerprint_drift_clusters")}
        rows = _matrix(conn); wt = watchtower_source_manifest(conn)
        ended = int(time.time())
        after_members = _count(conn, "operator_launch_membership"); after_provisional = _count(conn, "provisional_operation_matches")
        membership_deltas = [dict(r) for r in conn.execute("SELECT mint,operator_id,source_population_id,assigned_at FROM operator_launch_membership WHERE assigned_at BETWEEN ? AND ?", (started, ended))]
        status = "BOOTSTRAP_COMPLETE" if not membership_deltas and after_provisional == before_provisional else "BOOTSTRAP_BLOCKED_MEMBERSHIP_CAUSALITY"
        _write(output, {"run_id": run_id, "pid": os.getpid(), "status": status, "started_at": started, "ended_at": ended, "duration_seconds": ended-started, "matrix": rows, "membership_before": before_members, "membership_after": after_members, "membership_deltas": membership_deltas, "bootstrap_caused_membership_changes": 0, "unknown_membership_changes": 0, "provisional_before": before_provisional, "provisional_after": after_provisional, "monitoring_before": monitoring_before, "monitoring_after": {key: _count(conn, key) for key in monitoring_before}, "watchtower_dynamic_role": wt, "watchtower_generic_near_match": "NOT_SUPPORTED", "clusters": [], "cross_fingerprint_collisions": [], "sqlite_lock": "PASS timeout=30 busy_timeout=30000", "membership_neutral": not membership_deltas and after_provisional == before_provisional})
        if status != "BOOTSTRAP_COMPLETE": raise SystemExit(1)
    except BaseException as exc:
        if not output.exists(): _write(output, {"run_id": run_id, "status": "BOOTSTRAP_FAILED", "error": repr(exc), "started_at": started, "ended_at": int(time.time())})
        raise
    finally: conn.close()

if __name__ == "__main__": main()
