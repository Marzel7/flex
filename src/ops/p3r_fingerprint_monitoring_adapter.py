"""Read-only monitoring adapter for the authoritative unified P3R matcher."""
from __future__ import annotations

import sqlite3
from typing import Any

from src.ops.p3r_profile_candidate_matcher import evaluate_mint

FINGERPRINT_ID = "100SOL-WSOL-CLOSE-v1"
DETECTOR_VERSION = "P3R_UNIFIED_WSOL_WRAP_CLOSE_99_999985_SOL.v1"
SELECTED_AMOUNT = 99_999_985_000
ATOMIC_TRANSFER = 99_997_955_720


def observe_p3r_fingerprint(conn: sqlite3.Connection, mint: str) -> dict[str, Any]:
    """Observe retained P3R evidence without invoking an admission/projector path."""
    selected = conn.execute(
        "SELECT hop_depth,mechanism,amount_lamports FROM wt_walkback_edge_candidates "
        "WHERE mint=? AND selection_status='SELECTED' ORDER BY hop_depth,signature", (mint,)
    ).fetchall()
    atomics = conn.execute(
        "SELECT has_create,has_sync_native,has_close,transfer_lamports "
        "FROM wt_walkback_atomic_flows WHERE mint=?", (mint,)
    ).fetchall()
    if not selected or not atomics:
        return {"classification": "UNOBSERVABLE", "matching_dimensions": [], "differing_dimensions": ["retained_evidence"]}
    exact = evaluate_mint(conn, mint)
    if exact and exact.matching_profiles == ("P3R",):
        return {"classification": "EXACT_MATCH", "matching_dimensions": ["hop_depth", "selected_semantic", "selected_amount", "atomic_lifecycle", "atomic_transfer"], "differing_dimensions": []}
    selected_ok = any(int(r[0]) == 1 and r[1] == "WSOL_WRAP_CLOSE" for r in selected)
    amount_ok = any(int(r[0]) == 1 and r[1] == "WSOL_WRAP_CLOSE" and int(r[2]) == SELECTED_AMOUNT for r in selected if r[2] is not None)
    lifecycle_ok = any(bool(r[0]) and bool(r[1]) and bool(r[2]) for r in atomics)
    transfer_ok = any(bool(r[0]) and bool(r[1]) and bool(r[2]) and int(r[3]) == ATOMIC_TRANSFER for r in atomics if r[3] is not None)
    dimensions = {"hop_depth": selected_ok, "selected_semantic": selected_ok, "selected_amount": amount_ok, "atomic_lifecycle": lifecycle_ok, "atomic_transfer": transfer_ok}
    matching = [name for name, ok in dimensions.items() if ok]
    differing = [name for name, ok in dimensions.items() if not ok]
    if selected_ok and lifecycle_ok and len(differing) == 1:
        return {"classification": "NEAR_MATCH_ONE_DIMENSION", "matching_dimensions": matching, "differing_dimensions": differing}
    if selected_ok and lifecycle_ok and len(matching) >= 3:
        return {"classification": "NEAR_MATCH_MULTI_DIMENSION", "matching_dimensions": matching, "differing_dimensions": differing}
    return {"classification": "NO_MEANINGFUL_RELATIONSHIP", "matching_dimensions": matching, "differing_dimensions": differing}


def p3r_source_manifest(conn: sqlite3.Connection) -> dict[str, Any]:
    """Build a read-only manifest; membership is a source, never match proof."""
    operator = conn.execute("SELECT operator_id FROM operators WHERE display_name='P3R' AND status!='MERGED' LIMIT 1").fetchone()
    if not operator:
        return {"operation": "P3R", "fingerprint_id": FINGERPRINT_ID, "provenance_quality": "INSUFFICIENT"}
    mints = [r[0] for r in conn.execute("SELECT mint FROM operator_launch_membership WHERE operator_id=? ORDER BY mint", (operator[0],))]
    results = [observe_p3r_fingerprint(conn, mint) for mint in mints]
    return {"operation": "P3R", "operation_id": operator[0], "fingerprint_id": FINGERPRINT_ID, "technical_detector": DETECTOR_VERSION, "positive_reference_source": "current confirmed P3R membership", "member_count": len(mints), "observable_count": sum(r["classification"] != "UNOBSERVABLE" for r in results), "exact_match_count": sum(r["classification"] == "EXACT_MATCH" for r in results), "unobservable_count": sum(r["classification"] == "UNOBSERVABLE" for r in results), "comparison_source": "not scanned by this P3R-only adapter task", "address_independent": True, "provenance_quality": "CURRENT_CONFIRMED_MEMBERSHIP"}
