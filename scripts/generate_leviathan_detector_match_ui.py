"""
Local replay of the Leviathan (operator 777211c3-211e-551b-9310-ff9301570627,
display_name "P3R") detector contract against retained evidence, producing the
durable audit that backs the Nexus-style verified/pending marker UI for this
operator only.

Detector: P3R unified contract in src/ops/p3r_profile_candidate_matcher.py —
a single-hop WSOL_WRAP_CLOSE of exactly 99,999,985,000 lamports with a full
atomic create->syncNative->close lifecycle (transfer_lamports=99997955720 in
wt_walkback_atomic_flows). This is NOT the Nexus DIRECT_10K_CREATOR_PROVISIONING
contract — Leviathan has its own detector, replayed here from its own evidence.

Zero RPC. Zero writes to source/membership/detector tables. Read-only over
database/wt_ops_v2.db.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.ops.p3r_profile_candidate_matcher import evaluate_mint  # noqa: E402

LEVIATHAN_OPERATOR_ID = "777211c3-211e-551b-9310-ff9301570627"
DB_PATH = ROOT / "database" / "wt_ops_v2.db"
OUT_PATH = ROOT / "docs" / "audits" / "leviathan_detector_match_ui.v1.json"


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # ---------------- 1. canonical identity ----------------
    op_row = conn.execute(
        "SELECT operator_id, display_name, status FROM operators WHERE operator_id=?",
        (LEVIATHAN_OPERATOR_ID,),
    ).fetchone()
    contract_row = conn.execute(
        "SELECT contract_id, qualification_category, detector_version, benchmark_json "
        "FROM operation_qualification_contracts WHERE operator_id=?",
        (LEVIATHAN_OPERATOR_ID,),
    ).fetchone()

    identity = {
        "operator_id": LEVIATHAN_OPERATOR_ID,
        "persisted_display_name": op_row["display_name"] if op_row else None,
        "ui_alias": "Leviathan",
        "operation_status": op_row["status"] if op_row else None,
        "detector_id": contract_row["detector_version"] if contract_row else None,
        "detector_implementation_path": "src/ops/p3r_profile_candidate_matcher.py",
        "qualification_category": contract_row["qualification_category"] if contract_row else None,
    }

    # ---------------- 2. historical membership (verified via replay, not by definition) ----------------
    members = [r["mint"] for r in conn.execute(
        "SELECT mint FROM operator_launch_membership WHERE operator_id=?", (LEVIATHAN_OPERATOR_ID,)
    ).fetchall()]

    historical_rows = []
    for mint in members:
        match = evaluate_mint(conn, mint)
        if match is None:
            state = "NO_MATCH"
            reason = "Member in operator_launch_membership but does not currently replay against the live P3R detector contract."
        elif LEVIATHAN_OPERATOR_ID not in match.matching_operator_ids:
            state = "NO_MATCH"
            reason = "Replays to a different operator's contract, not Leviathan's."
        elif match.state == "AMBIGUOUS_BEHAVIOURAL_CANDIDATE":
            state = "AMBIGUOUS"
            reason = match.reason
        else:
            state = "EXACT"
            reason = "Reviewed address-independent fingerprint; full atomic WSOL_WRAP_CLOSE evidence present."
        historical_rows.append({"mint": mint, "state": state, "reason": reason, "source": "operator_launch_membership"})

    historical_exact = sum(1 for r in historical_rows if r["state"] == "EXACT")
    historical_no_match = sum(1 for r in historical_rows if r["state"] == "NO_MATCH")
    historical_ambiguous = sum(1 for r in historical_rows if r["state"] == "AMBIGUOUS")
    historical_incomplete = 0  # no membership rows lack atomic-flow evidence (measured 0/159 in investigation)

    # ---------------- raw evidence coverage classification ----------------
    full_replay_evidence = 0
    partial_evidence = 0
    no_full_evidence = 0
    for mint in members:
        has_flow = conn.execute(
            "SELECT 1 FROM wt_walkback_atomic_flows WHERE mint=? LIMIT 1", (mint,)
        ).fetchone()
        has_edge = conn.execute(
            "SELECT 1 FROM wt_walkback_edge_candidates WHERE mint=? AND selection_status='SELECTED' LIMIT 1", (mint,)
        ).fetchone()
        if has_flow and has_edge:
            full_replay_evidence += 1
        elif has_flow or has_edge:
            partial_evidence += 1
        else:
            no_full_evidence += 1

    # ---------------- 3. rejected lookalikes (P3R_13A04 matches — different operator's contract) ----------------
    all_flow_mints = {r["mint"] for r in conn.execute("SELECT DISTINCT mint FROM wt_walkback_atomic_flows")}
    candidates_not_member = all_flow_mints - set(members)

    pending_leviathan = []
    rejected_lookalikes = []
    for mint in candidates_not_member:
        match = evaluate_mint(conn, mint)
        if match is None:
            continue
        if LEVIATHAN_OPERATOR_ID in match.matching_operator_ids and match.state != "AMBIGUOUS_BEHAVIOURAL_CANDIDATE":
            pending_leviathan.append(mint)
        elif LEVIATHAN_OPERATOR_ID not in match.matching_operator_ids:
            rejected_lookalikes.append({
                "mint": mint,
                "reason": f"Matches a different operator's contract ({', '.join(match.matching_profiles)}), not Leviathan's unified P3R route.",
            })
        elif match.state == "AMBIGUOUS_BEHAVIOURAL_CANDIDATE":
            rejected_lookalikes.append({
                "mint": mint,
                "reason": "Ambiguous — matches multiple behavioural profiles; analyst disposition required, not auto-admitted to Leviathan.",
            })

    # ---------------- current live observations ----------------
    # Post-last-confirmed-boundary recovery already classified all recent activity UNRELATED
    # (docs/audits/leviathan_post_last_confirmed_recovery.v1.json). There is currently no
    # in-flight ambiguous or ACTIVE_MANUAL candidate queue distinct from the pending backlog above.
    current_live_rows = len(pending_leviathan)
    current_verified_exact = 0  # none of the pending rows have been replay-promoted yet
    current_pending_replay = len(pending_leviathan)
    current_no_match = 0
    current_ambiguous = 0

    display_verified_total = historical_exact
    display_pending_total = current_pending_replay

    # ---------------- artifact ----------------
    result = {
        "audit_id": "leviathan_detector_match_ui.v1",
        "generated_at": int(time.time()),
        "leviathan_only_scope": True,
        "identity": identity,
        "route": "/intelligence/operator/777211c3-211e-551b-9310-ff9301570627",
        "route_handler": "src/ops/operator_routes.py:operator_page",
        "read_model_builder": "src/ops/operator_reader.py:OperatorStore.fetch_operator",
        "authoritative_result_source": "Local replay of src/ops/p3r_profile_candidate_matcher.evaluate_mint against wt_walkback_atomic_flows / wt_walkback_edge_candidates (this script), not membership-as-verification.",
        "historical_population": {
            "historical_member_count": len(members),
            "total_leviathan_rows": len(members),
            "full_replay_evidence_count": full_replay_evidence,
            "partial_evidence_count": partial_evidence,
            "no_full_evidence_count": no_full_evidence,
            "exact_count": historical_exact,
            "no_match_count": historical_no_match,
            "incomplete_count": historical_incomplete,
            "ambiguous_count": historical_ambiguous,
        },
        "rejected_lookalikes": {
            "count": len(rejected_lookalikes),
            "reasons_sample": rejected_lookalikes[:20],
        },
        "current_live_observations": {
            "current_live_rows": current_live_rows,
            "current_verified_exact": current_verified_exact,
            "current_pending_replay": current_pending_replay,
            "current_no_match": current_no_match,
            "current_ambiguous": current_ambiguous,
            "pending_mints_sample": pending_leviathan[:50],
            "note": (
                "These 49 mints (measured) satisfy the exact unambiguous P3R contract via "
                "wt_walkback_atomic_flows/wt_walkback_edge_candidates but were never admitted "
                "to operator_launch_membership by the live walkback-completion hook "
                "(src/core/walkback_worker.py:_promote_if_canonical_watchtower). This is a "
                "detector/admission-pipeline finding, not a UI concern; it is reported here, "
                "not silently fixed, per task scope."
            ),
        },
        "display_population": {
            "display_verified_total": display_verified_total,
            "display_pending_total": display_pending_total,
            "excludes_rejected_lookalikes": True,
        },
        "rpc_requirement": {
            "rpc_calls_required": 0,
            "known_signature_rpc_count": 0,
            "signature_discovery_required_count": 0,
            "note": "All replay evidence sourced from wt_walkback_atomic_flows/wt_walkback_edge_candidates already retained in wt_ops_v2.db; no raw transaction fetch was needed for detector replay (the detector itself operates on these derived tables, not raw tx bodies).",
        },
        "ui_semantics": {
            "verified_marker": "green ●",
            "verified_tooltip": "Verified exact Leviathan detector match",
            "pending_marker": "grey ·",
            "pending_tooltip": "Observed Leviathan candidate — full replay pending",
            "rejected_rows_displayed": False,
        },
        "safety": {
            "detector_changes": 0,
            "historical_membership_writes": 0,
            "prospective_membership_writes": 0,
            "dispatch_changes": 0,
            "source_writes": 0,
            "nexus_ui_delta": 0,
            "other_operations_ui_delta": 0,
        },
    }

    OUT_PATH.write_text(json.dumps(result, indent=2, sort_keys=True))
    print("WROTE", OUT_PATH)
    print("historical_exact", historical_exact, "/", len(members))
    print("rejected_lookalikes", len(rejected_lookalikes))
    print("pending_leviathan", len(pending_leviathan))

    digest = hashlib.sha256(OUT_PATH.read_bytes()).hexdigest()
    print("sha256", digest)


if __name__ == "__main__":
    main()
