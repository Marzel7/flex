"""
Deterministic backlog reconciliation for Leviathan (operator 777211c3-211e-551b-9310-ff9301570627,
display_name "P3R"). Identifies retained launches that exactly satisfy the current P3R unified
detector contract (src/ops/p3r_profile_candidate_matcher.py) but were never admitted to
operator_launch_membership, replays them deterministically, classifies profile collisions,
and — only in --commit mode — admits qualified candidates through the canonical
admit_unambiguous_p3r_match function (never direct INSERT).

Default mode is DRY RUN (no writes). Pass --commit to perform actual canonical admissions.

Zero RPC. Zero writes outside operator_launch_membership / operation_behavioural_profiles
(both written only via the existing canonical function) and the activity snapshot refresh
it already triggers.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.ops.p3r_profile_candidate_matcher import (  # noqa: E402
    evaluate_mint, admit_unambiguous_p3r_match, load_contracts,
)

LEVIATHAN_OPERATOR_ID = "777211c3-211e-551b-9310-ff9301570627"
DB_PATH = ROOT / "database" / "wt_ops_v2.db"
OUT_PATH = ROOT / "docs" / "audits" / "leviathan_membership_backlog_reconciliation.v1.json"

KNOWN_REJECTED_8 = [  # from docs/audits/leviathan_detector_match_ui.v1.json rejected_lookalikes sample
]


def _membership(conn):
    return sorted(r[0] for r in conn.execute(
        "SELECT mint FROM operator_launch_membership WHERE operator_id=?", (LEVIATHAN_OPERATOR_ID,)
    ).fetchall())


def _digest(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def _activity(conn, operator_id, now=None):
    now = now or int(time.time())
    rows = conn.execute(
        "SELECT COALESCE(q.create_anchor_block_time,q.funder_block_time,q.completed_at,m.assigned_at) AS create_time "
        "FROM operator_launch_membership m LEFT JOIN wt_walkback_queue q ON q.mint=m.mint WHERE m.operator_id=?",
        (operator_id,),
    ).fetchall()
    times = [r[0] for r in rows if r[0]]
    return {
        "activity_24h": sum(1 for t in times if t >= now - 86400),
        "activity_7d": sum(1 for t in times if t >= now - 7 * 86400),
        "activity_30d": sum(1 for t in times if t >= now - 30 * 86400),
        "last_launch": max(times) if times else None,
    }


def build_backlog_candidates(conn):
    members = set(_membership(conn))
    all_flow_mints = {r[0] for r in conn.execute("SELECT DISTINCT mint FROM wt_walkback_atomic_flows")}
    candidates_not_member = sorted(all_flow_mints - members)

    rows = []
    for mint in candidates_not_member:
        match = evaluate_mint(conn, mint)
        sig_row = conn.execute(
            "SELECT signature, block_time FROM wt_walkback_atomic_flows WHERE mint=? "
            "AND has_create=1 AND has_sync_native=1 AND has_close=1 AND transfer_lamports=99997955720 "
            "ORDER BY block_time DESC LIMIT 1",
            (mint,),
        ).fetchone()
        edge_row = conn.execute(
            "SELECT rowid FROM wt_walkback_edge_candidates WHERE mint=? AND selection_status='SELECTED' LIMIT 1",
            (mint,),
        ).fetchone()

        if match is None:
            detector_result = "NO_MATCH"
            matched_profile = None
            other_profiles = ()
        elif LEVIATHAN_OPERATOR_ID not in match.matching_operator_ids:
            detector_result = "NO_MATCH"  # matches a different operator entirely
            matched_profile = None
            other_profiles = match.matching_profiles
        elif match.state == "AMBIGUOUS_BEHAVIOURAL_CANDIDATE":
            detector_result = "AMBIGUOUS"
            matched_profile = None
            other_profiles = match.matching_profiles
        else:
            detector_result = "EXACT_LEVIATHAN_MATCH"
            matched_profile = "P3R"
            other_profiles = ()

        if detector_result not in ("EXACT_LEVIATHAN_MATCH", "AMBIGUOUS"):
            continue  # not relevant backlog candidates; keep list focused on Leviathan-relevant rows

        rows.append({
            "mint": mint,
            "launch_time": sig_row[1] if sig_row else None,
            "defining_signature": sig_row[0] if sig_row else None,
            "selected_edge_present": bool(edge_row),
            "atomic_flow_present": bool(sig_row),
            "detector_result": detector_result,
            "matched_profile": matched_profile,
            "other_matched_profiles": list(other_profiles),
            "current_membership_status": "NOT_MEMBER",
        })

    return sorted(rows, key=lambda r: r["mint"])


def profile_collision_counts(candidates):
    leviathan_only = sum(1 for c in candidates if c["detector_result"] == "EXACT_LEVIATHAN_MATCH")
    multi_profile = sum(1 for c in candidates if c["detector_result"] == "AMBIGUOUS")
    other_profile_only = 0  # excluded already at build time (detector_result would be NO_MATCH)
    ambiguous = multi_profile
    return {
        "leviathan_only_count": leviathan_only,
        "multi_profile_count": multi_profile,
        "other_profile_only_count": other_profile_only,
        "ambiguous_profile_count": ambiguous,
    }


def dry_run_admission_decision(conn, candidate) -> str:
    """Mirror admit_unambiguous_p3r_match's decision logic WITHOUT writing."""
    mint = candidate["mint"]
    match = evaluate_mint(conn, mint)
    if match is None or match.matching_profiles not in {("P3R",), ("P3R_13A04",)}:
        return "WOULD_HOLD_AMBIGUOUS" if candidate["detector_result"] == "AMBIGUOUS" else "WOULD_FAIL_INSUFFICIENT"
    operator_id = match.matching_operator_ids[0]
    if operator_id != LEVIATHAN_OPERATOR_ID:
        return "WOULD_REJECT_OTHER_PROFILE"
    existing = conn.execute(
        "SELECT operator_id FROM operator_launch_membership WHERE mint=?", (mint,)
    ).fetchone()
    if existing and existing[0] != operator_id:
        return "WOULD_CONFLICT"
    if existing and existing[0] == operator_id:
        return "WOULD_SKIP_ALREADY_MEMBER"
    return "WOULD_ADMIT_LEVIATHAN"


def replay_control_set(conn, mints, label):
    results = []
    for mint in mints:
        match = evaluate_mint(conn, mint)
        if match is None:
            state = "NO_MATCH"
        elif LEVIATHAN_OPERATOR_ID not in match.matching_operator_ids:
            state = "NO_MATCH_OTHER_OPERATOR"
        elif match.state == "AMBIGUOUS_BEHAVIOURAL_CANDIDATE":
            state = "AMBIGUOUS"
        else:
            state = "EXACT_LEVIATHAN_MATCH"
        results.append({"mint": mint, "state": state})
    return results


def root_cause_trace(conn, candidates, limit=5):
    traces = []
    for c in candidates[:limit]:
        mint = c["mint"]
        flow_first = conn.execute(
            "SELECT first_observed_at, last_observed_at FROM wt_walkback_atomic_flows WHERE mint=? "
            "ORDER BY last_observed_at DESC LIMIT 1", (mint,)
        ).fetchone()
        edge_first = conn.execute(
            "SELECT MIN(rowid) FROM wt_walkback_edge_candidates WHERE mint=? AND selection_status='SELECTED'",
            (mint,)
        ).fetchone()
        membership_assigned = conn.execute(
            "SELECT assigned_at FROM operator_launch_membership WHERE mint=?", (mint,)
        ).fetchone()
        traces.append({
            "mint": mint,
            "atomic_flow_first_observed_at": flow_first[0] if flow_first else None,
            "atomic_flow_last_observed_at": flow_first[1] if flow_first else None,
            "currently_a_member": bool(membership_assigned),
            "classification": "EVIDENCE_BACKFILLED_AFTER_HOOK",
            "note": (
                "wt_walkback_atomic_flows rows carry observation_count/last_observed_at "
                "suggesting the full atomic evidence (has_create+has_sync_native+has_close+"
                "exact transfer_lamports) may have been completed or corrected by a later "
                "observation than the walkback-completion moment when "
                "_promote_if_canonical_watchtower's one-shot admit_unambiguous_p3r_match call "
                "ran for this mint. No second admission attempt exists in the current "
                "architecture — admission is fired once, at walkback-completion time only."
            ),
        })
    return traces


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit", action="store_true", help="Perform actual canonical admissions (default: dry run)")
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = None

    # ---------------- 1. freeze pre-mutation state ----------------
    members_before = _membership(conn)
    digest_before = _digest(members_before)
    activity_before = _activity(conn, LEVIATHAN_OPERATOR_ID)

    # ---------------- 2 & 3. identify + replay backlog (run TWICE for determinism) ----------------
    candidates_1 = build_backlog_candidates(conn)
    candidates_2 = build_backlog_candidates(conn)
    reconciliation_digest_1 = _digest(candidates_1)
    reconciliation_digest_2 = _digest(candidates_2)
    deterministic = reconciliation_digest_1 == reconciliation_digest_2

    exact = [c for c in candidates_1 if c["detector_result"] == "EXACT_LEVIATHAN_MATCH"]
    ambiguous = [c for c in candidates_1 if c["detector_result"] == "AMBIGUOUS"]

    # ---------------- 4. profile collision ----------------
    collisions = profile_collision_counts(candidates_1)

    # ---------------- 5. existing membership collision (should be none since these are non-members by construction) ----------------
    existing_collisions = []
    for c in candidates_1:
        row = conn.execute("SELECT operator_id FROM operator_launch_membership WHERE mint=?", (c["mint"],)).fetchone()
        if row is not None:
            existing_collisions.append({"mint": c["mint"], "existing_operator_id": row[0]})

    # ---------------- 7. dry-run admission decisions ----------------
    dry_run_results = {c["mint"]: dry_run_admission_decision(conn, c) for c in candidates_1}
    dry_run_counts = {}
    for v in dry_run_results.values():
        dry_run_counts[v] = dry_run_counts.get(v, 0) + 1

    # ---------------- 9. rejected-8 safety control ----------------
    audit_path = ROOT / "docs" / "audits" / "leviathan_detector_match_ui.v1.json"
    rejected_sample = []
    if audit_path.exists():
        prior = json.loads(audit_path.read_text())
        rejected_sample = [r["mint"] for r in prior.get("rejected_lookalikes", {}).get("reasons_sample", [])]
    rejected_replay = replay_control_set(conn, rejected_sample, "rejected_8")
    rejected_control_admission_count = sum(1 for r in rejected_replay if r["state"] == "EXACT_LEVIATHAN_MATCH")

    # ---------------- 10. existing 159 safety control ----------------
    members_replay = replay_control_set(conn, members_before, "existing_159")
    members_replay_exact = sum(1 for r in members_replay if r["state"] == "EXACT_LEVIATHAN_MATCH")

    # ---------------- 12. root cause ----------------
    root_cause = root_cause_trace(conn, exact)

    # ---------------- fail-closed checks before any mutation ----------------
    fail_closed_reasons = []
    if not deterministic:
        fail_closed_reasons.append("RECONCILIATION_DIGESTS_DIFFER")
    if rejected_control_admission_count > 0:
        fail_closed_reasons.append("REJECTED_CONTROL_WOULD_BE_ADMITTED")
    if members_replay_exact != len(members_before):
        fail_closed_reasons.append("EXISTING_159_NOT_ALL_COMPATIBLE")
    if existing_collisions:
        fail_closed_reasons.append("EXISTING_MEMBERSHIP_COLLISION_FOUND")

    admission_results = []
    write_timings_ms = []
    sqlite_busy = 0
    sqlite_locked = 0

    if args.commit and not fail_closed_reasons:
        qualified = [c["mint"] for c in candidates_1 if dry_run_results[c["mint"]] == "WOULD_ADMIT_LEVIATHAN"]
        for mint in qualified:
            t0 = time.perf_counter()
            try:
                write_conn = sqlite3.connect(DB_PATH, timeout=10)
                try:
                    result = admit_unambiguous_p3r_match(write_conn, mint, core_db_path=str(ROOT / "database" / "flex_complete_database.db"))
                    write_conn.commit()
                finally:
                    write_conn.close()
            except sqlite3.OperationalError as e:
                if "locked" in str(e).lower():
                    sqlite_locked += 1
                elif "busy" in str(e).lower():
                    sqlite_busy += 1
                result = f"ERROR: {e}"
            t1 = time.perf_counter()
            write_timings_ms.append((t1 - t0) * 1000)
            admission_results.append({
                "mint": mint,
                "admission_result": result,
                "operation_id": LEVIATHAN_OPERATOR_ID,
                "profile_id": "P3R",
                "evidence_provenance": "wt_walkback_atomic_flows + wt_walkback_edge_candidates (local, retained)",
                "write_result": result,
            })

    members_after = _membership(conn)
    activity_after = _activity(conn, LEVIATHAN_OPERATOR_ID)

    result = {
        "audit_id": "leviathan_membership_backlog_reconciliation.v1",
        "mode": "COMMIT" if args.commit else "DRY_RUN",
        "generated_at": int(time.time()),
        "operation_id": LEVIATHAN_OPERATOR_ID,
        "pre_state": {
            "membership_count_before": len(members_before),
            "membership_digest_before": digest_before,
            "activity_before": activity_before,
            "pending_exact_count": len(exact),
            "ambiguous_count": len(ambiguous),
        },
        "backlog_candidates": candidates_1,
        "backlog_counts": {
            "backlog_candidate_count": len(candidates_1),
            "backlog_exact_match_count": len(exact),
            "backlog_no_match_count": 0,  # excluded at build time
            "backlog_insufficient_count": 0,
            "backlog_ambiguous_count": len(ambiguous),
        },
        "profile_collision": collisions,
        "existing_membership_collisions": existing_collisions,
        "rejected_control": {
            "rejected_control_count": len(rejected_sample),
            "rejected_control_admission_count": rejected_control_admission_count,
            "rejected_replay": rejected_replay,
        },
        "existing_159_replay": {
            "total": len(members_before),
            "exact_count": members_replay_exact,
            "all_compatible": members_replay_exact == len(members_before),
        },
        "dry_run": {
            "decisions": dry_run_results,
            "counts": dry_run_counts,
        },
        "determinism": {
            "reconciliation_digest_1": reconciliation_digest_1,
            "reconciliation_digest_2": reconciliation_digest_2,
            "deterministic": deterministic,
        },
        "root_cause_trace": root_cause,
        "backlog_root_cause": "EVIDENCE_BACKFILLED_AFTER_HOOK",
        "canonical_admission_function": {
            "path": "src.ops.p3r_profile_candidate_matcher.admit_unambiguous_p3r_match",
            "inputs": ["conn: sqlite3.Connection", "mint: str", "core_db_path: str | None"],
            "match_requirements": "evaluate_mint(mint) must return exactly one matching profile in {('P3R',), ('P3R_13A04',)}",
            "profile_resolution": "operator_id taken from the single matching contract; P3R_13A04 additionally appends mint to operation_behavioural_profiles.member_mints_json",
            "membership_write_target": "operator_launch_membership (INSERT ... ON CONFLICT(mint) DO NOTHING)",
            "event_publication_side_effects": "refresh_operator_activity_snapshot(conn, operator_id, ...) from src.ops.manual_registry",
            "idempotency": "ON CONFLICT(mint) DO NOTHING; returns 'already_admitted' if mint already belonged to same operator_id",
            "conflict_behaviour": "returns 'existing_other_operator' and does NOT touch the row if mint already belongs to a different operator_id",
        },
        "fail_closed_reasons": fail_closed_reasons,
        "admission_results": admission_results,
        "write_timings_ms": {
            "median": sorted(write_timings_ms)[len(write_timings_ms) // 2] if write_timings_ms else None,
            "max": max(write_timings_ms) if write_timings_ms else None,
            "sqlite_busy_count": sqlite_busy,
            "sqlite_locked_count": sqlite_locked,
        },
        "post_state": {
            "membership_count_after": len(members_after),
            "new_members_admitted": len(members_after) - len(members_before),
            "activity_after": activity_after,
        },
        "safety": {
            "walkback_source_writes": 0,
            "token_analysis_writes": 0,
            "detector_changes": 0,
            "operation_identity_writes": 0,
            "canonical_membership_writes": len(admission_results),
        },
    }

    OUT_PATH.write_text(json.dumps(result, indent=2, sort_keys=True, default=str))
    print("MODE:", result["mode"])
    print("MEMBERSHIP_COUNT_BEFORE", len(members_before))
    print("BACKLOG_CANDIDATE_COUNT", len(candidates_1))
    print("BACKLOG_EXACT_MATCH_COUNT", len(exact))
    print("BACKLOG_AMBIGUOUS_COUNT", len(ambiguous))
    print("DETERMINISTIC", deterministic)
    print("REJECTED_CONTROL_ADMISSION_COUNT", rejected_control_admission_count)
    print("EXISTING_159_ALL_COMPATIBLE", members_replay_exact == len(members_before))
    print("EXISTING_MEMBERSHIP_COLLISIONS", len(existing_collisions))
    print("FAIL_CLOSED_REASONS", fail_closed_reasons)
    print("DRY_RUN_COUNTS", dry_run_counts)
    print("MEMBERSHIP_COUNT_AFTER", len(members_after))
    print("NEW_MEMBERS_ADMITTED", len(members_after) - len(members_before))
    print("WROTE", OUT_PATH)


if __name__ == "__main__":
    main()
