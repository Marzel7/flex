"""Forward-only, read-only behavioural monitor for P3R_13A04."""
from __future__ import annotations

import sqlite3
from typing import Any

OPERATION = "P3R_13A04"
OPERATION_ID = "ccb7b1b0-56e1-4543-9e95-3f284bed3943"
FINGERPRINT_ID = "30SOL-5K-LADDER-v1"
TECHNICAL_DETECTOR = "P3R_13A04_FOUR_STEP_30_SOL_LADDER.v1"
MEMBERSHIP_WRITE_CAPABILITY = "NONE"
ROUTE = (
    (1, "PLAIN_XFER", 29_999_975_000),
    (2, "WSOL_WRAP_CLOSE", 29_999_980_000),
    (3, "PLAIN_XFER", 29_999_985_000),
    (4, "WSOL_WRAP_CLOSE", 29_999_990_000),
)


def _route_rows(conn: sqlite3.Connection, mint: str) -> tuple[tuple[int, str, int], ...] | None:
    try:
        rows = conn.execute(
            "SELECT hop_depth,mechanism,amount_lamports FROM wt_walkback_edge_candidates "
            "WHERE mint=? AND selection_status='SELECTED' ORDER BY hop_depth",
            (mint,),
        ).fetchall()
    except sqlite3.OperationalError:
        return None
    if any(row[0] is None or row[1] is None or row[2] is None for row in rows):
        return None
    return tuple((int(row[0]), str(row[1]), int(row[2])) for row in rows)


def observe_p3r_13a04_fingerprint(conn: sqlite3.Connection, mint: str) -> dict[str, Any]:
    """Classify retained route evidence without invoking the production matcher."""
    observed = _route_rows(conn, mint)
    if observed is None or len(observed) != len(ROUTE):
        return {"classification": "UNOBSERVABLE", "matching_dimensions": [], "differing_dimensions": ["complete_four_hop_route"]}
    topology_ok = tuple(row[0] for row in observed) == tuple(row[0] for row in ROUTE)
    semantic_matches = [row[1] == expected[1] for row, expected in zip(observed, ROUTE)]
    amount_matches = [row[2] == expected[2] for row, expected in zip(observed, ROUTE)]
    dimensions = {"route_topology": topology_ok}
    dimensions.update({f"hop_{index}_semantic": value for index, value in enumerate(semantic_matches, 1)})
    dimensions.update({f"hop_{index}_amount": value for index, value in enumerate(amount_matches, 1)})
    matching = [name for name, value in dimensions.items() if value]
    differing = [name for name, value in dimensions.items() if not value]
    if all(dimensions.values()):
        classification = "EXACT_MATCH"
    elif topology_ok and sum(semantic_matches) >= 3:
        classification = "NEAR_MATCH_ONE_DIMENSION" if len(differing) == 1 else "NEAR_MATCH_MULTI_DIMENSION"
    else:
        classification = "NO_MEANINGFUL_RELATIONSHIP"
    return {"classification": classification, "matching_dimensions": matching, "differing_dimensions": differing}


def source_manifest() -> dict[str, Any]:
    """Return the immutable forward-monitoring contract, not historical members."""
    return {
        "operation_id": OPERATION_ID,
        "display_name": OPERATION,
        "technical_detector": TECHNICAL_DETECTOR,
        "fingerprint_id": FINGERPRINT_ID,
        "qualification": "CONFIRMED",
        "exact_behavioural_contract": [
            {"hop_depth": hop, "mechanism": mechanism, "amount_lamports": amount, "literal_address_required": False}
            for hop, mechanism, amount in ROUTE
        ],
        "observability_requirements": "four retained SELECTED edges with hop_depth, mechanism, and amount_lamports in the exact ordered route",
        "historical_positive_reference_source": "UNRECOVERED",
        "historical_positive_provenance_quality": "INSUFFICIENT",
        "current_membership_count": 0,
        "baseline_uniqueness_capability": "NOT_YET_MEASURED",
        "forward_monitoring": "ENABLED_BY_ADAPTER",
        "address_independent": True,
        "membership_write_capability": MEMBERSHIP_WRITE_CAPABILITY,
        "comparison_fixture_source": "existing d3de exact-route regression fixture only; not a uniqueness denominator",
        "provenance_quality": "CURRENT_DETECTOR_CONTRACT_WITH_UNRECOVERED_HISTORICAL_POSITIVES",
    }
