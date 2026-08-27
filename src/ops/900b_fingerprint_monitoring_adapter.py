"""Read-only behavioural fingerprint monitor for provisional 900b.

The production 900b classifier owns provisional-review writes.  This module
only evaluates retained selected-edge evidence and reports infrastructure as a
separate corroborating observation.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from src.ops.provisional_operations import FROZEN_900B_RECURRENT_FUNDERS

OPERATION = "WSOL_PROVISION_CLOSE_1_SOL_MINUS_15K"
FINGERPRINT_ID = "1SOL-WSOL-PROVISION-CLOSE-15K-v1"
TECHNICAL_DETECTOR = "900B_HYBRID_PROVISIONAL.v1"
QUALIFICATION = "PROVISIONAL"
MONITORING_STRATEGY = "BEHAVIOURAL_FINGERPRINT_DRIFT"
MEMBERSHIP_WRITE_CAPABILITY = "NONE"
PROVISIONAL_STATE_WRITE_CAPABILITY = "NONE"

SELECTED_HOP = 1
SELECTED_SEMANTIC = "WSOL_WRAP_CLOSE"
SELECTED_AMOUNT_LAMPORTS = 999_985_000


def _selected_rows(conn: sqlite3.Connection, mint: str) -> list[sqlite3.Row | tuple[Any, ...]] | None:
    try:
        return conn.execute(
            "SELECT selection_status,hop_depth,mechanism,amount_lamports,candidate_parent "
            "FROM wt_walkback_edge_candidates WHERE mint=? AND selection_status='SELECTED' "
            "ORDER BY hop_depth ASC,last_observed_at DESC",
            (mint,),
        ).fetchall()
    except sqlite3.OperationalError:
        return None


def _value(row: sqlite3.Row | tuple[Any, ...], index: int, name: str) -> Any:
    return row[name] if hasattr(row, "keys") else row[index]


def _infrastructure_observation(row: sqlite3.Row | tuple[Any, ...]) -> str:
    funder = _value(row, 4, "candidate_parent")
    if not funder:
        return "NO_INFRASTRUCTURE_EVIDENCE"
    return "RECURRENT_KNOWN_FUNDER_OBSERVED" if funder in FROZEN_900B_RECURRENT_FUNDERS else "NOVEL_OR_UNKNOWN_FUNDER_OBSERVED"


def observe_900b_behavioural_fingerprint(conn: sqlite3.Connection, mint: str) -> dict[str, Any]:
    """Classify retained evidence without invoking any 900b projector."""
    rows = _selected_rows(conn, mint)
    if not rows:
        return {"classification": "UNOBSERVABLE", "matching_dimensions": [], "differing_dimensions": ["selected_edge"], "infrastructure_observation": "NO_INFRASTRUCTURE_EVIDENCE"}
    row = rows[0]
    hop = _value(row, 1, "hop_depth")
    semantic = _value(row, 2, "mechanism")
    amount = _value(row, 3, "amount_lamports")
    if hop is None or semantic is None or amount is None:
        return {"classification": "UNOBSERVABLE", "matching_dimensions": [], "differing_dimensions": ["required_selected_edge_field"], "infrastructure_observation": _infrastructure_observation(row)}
    dimensions = {
        "selected_hop": int(hop) == SELECTED_HOP,
        "selected_semantic": semantic == SELECTED_SEMANTIC,
        "selected_amount": int(amount) == SELECTED_AMOUNT_LAMPORTS,
    }
    matching = [name for name, matches in dimensions.items() if matches]
    differing = [name for name, matches in dimensions.items() if not matches]
    # Semantic equality is the behavioural anchor.  Generic WSOL activity is
    # therefore not a near match merely because it uses the same asset.
    if all(dimensions.values()):
        classification = "EXACT_MATCH"
    elif dimensions["selected_semantic"] and sum(dimensions.values()) == 2:
        classification = "NEAR_MATCH_ONE_DIMENSION"
    elif dimensions["selected_semantic"] and sum(dimensions.values()) == 1:
        classification = "NEAR_MATCH_MULTI_DIMENSION"
    else:
        classification = "NO_MEANINGFUL_RELATIONSHIP"
    return {"classification": classification, "matching_dimensions": matching, "differing_dimensions": differing, "infrastructure_observation": _infrastructure_observation(row)}


def source_manifest(conn: sqlite3.Connection) -> dict[str, Any]:
    """Return only frozen/local provenance and an optional retained row count."""
    current_rows = 0
    try:
        current_rows = int(conn.execute(
            "SELECT COUNT(*) FROM provisional_operation_matches WHERE detector_version=?",
            (TECHNICAL_DETECTOR,),
        ).fetchone()[0])
    except sqlite3.OperationalError:
        pass
    return {
        "operation": OPERATION,
        "qualification": QUALIFICATION,
        "fingerprint_id": FINGERPRINT_ID,
        "technical_detector": TECHNICAL_DETECTOR,
        "monitoring_strategy": MONITORING_STRATEGY,
        "address_independent_behavioural_status": "YES",
        "behavioural_inputs": [
            {"field": "selection_status", "required": True, "literal_address_required": False, "expected": "SELECTED"},
            {"field": "hop_depth", "required": True, "literal_address_required": False, "expected": SELECTED_HOP},
            {"field": "mechanism", "required": True, "literal_address_required": False, "expected": SELECTED_SEMANTIC},
            {"field": "amount_lamports", "required": True, "literal_address_required": False, "expected": SELECTED_AMOUNT_LAMPORTS},
        ],
        "atomic_lifecycle": {"required_for_behavioural_core": False, "retained_provenance": "createAccountWithSeed -> initializeAccount3 -> transfer -> syncNative -> closeAccount (dominant 31/44; broader WSOL lifecycle 44/44)"},
        "role_structure": "candidate_parent is direct-funder corroboration; selected-edge wallet is rotating creator; neither is a behavioural input",
        "positive_reference_source": "frozen 900b H0 behavioural TP cohort", "positive_reference_count": 44,
        "comparison_source": "frozen 900b H0 behaviour-only false-positive cohort", "comparison_count": 15,
        "baseline_uniqueness": {"capability": "MEASURABLE_FROZEN_REFERENCE", "value_percent": 74.58, "formula": "44 / (44 + 15) * 100"},
        "infrastructure_evidence_source": "candidate_parent compared separately with frozen recurrent-funder set", "infrastructure_treatment": "CORROBORATION_ONLY",
        "current_provisional_source": {"table": "provisional_operation_matches", "retained_row_count": current_rows},
        "membership_write_capability": MEMBERSHIP_WRITE_CAPABILITY,
        "provisional_state_write_capability": PROVISIONAL_STATE_WRITE_CAPABILITY,
        "provenance_quality": "FROZEN_900B_HYBRID_QUALIFICATION_AND_LOCAL_RETAINED_ROWS",
    }
