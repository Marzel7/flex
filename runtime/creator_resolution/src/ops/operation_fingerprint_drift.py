"""Secondary, membership-neutral fingerprint drift monitoring.

This module observes completed retained walkbacks *after* the normal detectors.
It never writes ``operator_launch_membership`` or any detector state.
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from collections import Counter
from typing import Any

from src.ops.operation_fingerprint_monitoring import DEFINITIONS

DDL = """
CREATE TABLE IF NOT EXISTS operation_fingerprint_drift_evidence (
 drift_id TEXT PRIMARY KEY, operator_id TEXT NOT NULL, fingerprint_id TEXT NOT NULL,
 mint TEXT NOT NULL, classification TEXT NOT NULL, matching_dimensions_json TEXT NOT NULL,
 differing_dimensions_json TEXT NOT NULL, observed_json TEXT NOT NULL, expected_json TEXT NOT NULL,
 infrastructure_json TEXT NOT NULL, drift_signature TEXT, first_seen INTEGER NOT NULL,
 latest_seen INTEGER NOT NULL, UNIQUE(operator_id,fingerprint_id,mint)
);
CREATE TABLE IF NOT EXISTS operation_fingerprint_health_snapshots (
 snapshot_id TEXT PRIMARY KEY, operator_id TEXT NOT NULL, fingerprint_id TEXT NOT NULL,
 observed_at INTEGER NOT NULL, accepted_exact_matches INTEGER NOT NULL,
 external_exact_matches INTEGER NOT NULL, observable_comparison_count INTEGER NOT NULL,
 uniqueness_percent REAL, trend TEXT NOT NULL, UNIQUE(operator_id,fingerprint_id,observed_at)
);
CREATE TABLE IF NOT EXISTS operation_fingerprint_drift_clusters (
 cluster_id TEXT PRIMARY KEY, operator_id TEXT NOT NULL, fingerprint_id TEXT NOT NULL,
 drift_signature TEXT NOT NULL, classification TEXT NOT NULL, mint_count INTEGER NOT NULL,
 first_seen INTEGER NOT NULL, latest_seen INTEGER NOT NULL, related_potential_operation_id TEXT,
 relationship_type TEXT NOT NULL, reason TEXT NOT NULL, UNIQUE(operator_id,fingerprint_id,drift_signature)
);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(DDL)


def _rows(conn: sqlite3.Connection, mint: str) -> list[dict[str, Any]] | None:
    try:
        rows = conn.execute("SELECT hop_depth,mechanism,amount_lamports,wallet,candidate_parent,signature FROM wt_walkback_edge_candidates WHERE mint=? AND selection_status='SELECTED' ORDER BY hop_depth,signature", (mint,)).fetchall()
    except sqlite3.OperationalError:
        return None
    return [dict(row) if hasattr(row, "keys") else dict(zip(("hop_depth", "mechanism", "amount_lamports", "wallet", "candidate_parent", "signature"), row)) for row in rows]


def _route(rows: list[dict[str, Any]] | None) -> tuple[tuple[int, str, int], ...] | None:
    if rows is None or not rows or any(row.get("amount_lamports") is None for row in rows):
        return None
    return tuple((int(row["hop_depth"]), str(row["mechanism"]), int(row["amount_lamports"])) for row in rows)


def compare_route(expected: tuple[tuple[int, str, int], ...], observed: tuple[tuple[int, str, int], ...] | None) -> tuple[str, list[str], list[str]]:
    """Explainable structural comparison; no address is a dimension."""
    if observed is None:
        return "UNOBSERVABLE", [], ["route"]
    dimensions = {
        "topology": len(expected) == len(observed) and tuple(x[0] for x in expected) == tuple(x[0] for x in observed),
        "semantic_sequence": tuple(x[1] for x in expected) == tuple(x[1] for x in observed),
        "amount_vector": tuple(x[2] for x in expected) == tuple(x[2] for x in observed),
    }
    matching = [name for name, ok in dimensions.items() if ok]
    differing = [name for name, ok in dimensions.items() if not ok]
    if not differing:
        return "EXACT_MATCH", matching, differing
    # A one-hop WSOL amount difference is generic common behaviour, not a
    # mutation signal. Rich route/atomic contracts must supply the continuity.
    if len(expected) < 2:
        return "NO_MEANINGFUL_RELATIONSHIP", matching, differing
    if len(differing) == 1 and len(matching) >= 2:
        return "NEAR_MATCH_ONE_DIMENSION", matching, differing
    if len(matching) >= 2:
        return "NEAR_MATCH_MULTI_DIMENSION", matching, differing
    return "NO_MEANINGFUL_RELATIONSHIP", matching, differing


def _active_operations(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT o.operator_id,o.display_name,COALESCE(q.detector_version,'') detector_version,COALESCE(q.qualification_category,'CONFIRMED') qualification FROM operators o JOIN operation_registry_dispositions d USING(operator_id) LEFT JOIN operation_qualification_contracts q ON q.operator_id=o.operator_id WHERE d.disposition='ACTIVE_MANUAL' AND o.status!='MERGED'", ()).fetchall()
    return [dict(row) if hasattr(row, "keys") else dict(zip(("operator_id", "display_name", "detector_version", "qualification"), row)) for row in rows]


def _exact_profiles(conn: sqlite3.Connection, mint: str) -> set[str]:
    """Observe exact status through the existing detector predicates only."""
    result: set[str] = set()
    from src.ops.d3de_operation import is_d0_match, selected_evidence as d3de_evidence
    from src.ops.wsol_10_sol_four_step_operation import is_strict_match, selected_evidence as byz_evidence
    from src.ops.p3r_profile_candidate_matcher import evaluate_mint
    if is_d0_match(d3de_evidence(conn, mint)):
        result.add("FOUR_STEP_30_SOL_14_479K_WSOL_LADDER")
    if is_strict_match(byz_evidence(conn, mint)):
        result.add("Byzantine")
    p3r = evaluate_mint(conn, mint)
    if p3r:
        result.update(p3r.matching_profiles)
    # WATCHTOWER remains owned by its strict canonical integration; observing
    # existing membership is intentionally the monitor's only exact signal.
    return result


def _expected_route(conn: sqlite3.Connection, display_name: str) -> tuple[tuple[int, str, int], ...] | None:
    if display_name == "FOUR_STEP_30_SOL_14_479K_WSOL_LADDER":
        from src.ops.d3de_operation import SELECTED_ROUTE
        return SELECTED_ROUTE
    if display_name == "Byzantine":
        from src.ops.wsol_10_sol_four_step_operation import AMOUNT_LAMPORTS
        return ((1, "WSOL_WRAP_CLOSE", AMOUNT_LAMPORTS),)
    if display_name in {"P3R", "P3R_13A04"}:
        from src.ops.p3r_profile_candidate_matcher import load_contracts
        for contract in load_contracts(conn):
            if contract.display_name == display_name:
                return contract.route
    if display_name == "WSOL_PROVISION_CLOSE_1_SOL_MINUS_15K":
        return ((1, "WSOL_WRAP_CLOSE", 999985000),)
    return None


def _upsert_evidence(conn: sqlite3.Connection, operation: dict[str, Any], mint: str, classification: str, matching: list[str], differing: list[str], observed: Any, expected: Any, rows: list[dict[str, Any]] | None, now: int) -> None:
    if classification not in {"EXACT_MATCH", "NEAR_MATCH_ONE_DIMENSION", "NEAR_MATCH_MULTI_DIMENSION"}:
        return
    fingerprint_id = DEFINITIONS[operation["display_name"]].fingerprint_id
    signature = None if classification == "EXACT_MATCH" else json.dumps([fingerprint_id, differing, observed], sort_keys=True)
    infra = {"direct_funders": sorted({row.get("candidate_parent") for row in rows or [] if row.get("candidate_parent")}), "parents": sorted({row.get("wallet") for row in rows or [] if row.get("wallet")})}
    drift_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{operation['operator_id']}:{fingerprint_id}:{mint}"))
    conn.execute("INSERT INTO operation_fingerprint_drift_evidence(drift_id,operator_id,fingerprint_id,mint,classification,matching_dimensions_json,differing_dimensions_json,observed_json,expected_json,infrastructure_json,drift_signature,first_seen,latest_seen) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(operator_id,fingerprint_id,mint) DO UPDATE SET latest_seen=excluded.latest_seen", (drift_id, operation["operator_id"], fingerprint_id, mint, classification, json.dumps(matching), json.dumps(differing), json.dumps(observed), json.dumps(expected), json.dumps(infra), signature, now, now))


def _refresh_health(conn: sqlite3.Connection, operation: dict[str, Any], now: int) -> None:
    fingerprint_id = DEFINITIONS[operation["display_name"]].fingerprint_id
    accepted = conn.execute("SELECT COUNT(DISTINCT mint) FROM operator_launch_membership WHERE operator_id=?", (operation["operator_id"],)).fetchone()[0]
    external = conn.execute("SELECT COUNT(DISTINCT mint) FROM operation_fingerprint_drift_evidence WHERE operator_id=? AND fingerprint_id=? AND classification='EXACT_MATCH' AND mint NOT IN (SELECT mint FROM operator_launch_membership WHERE operator_id=?)", (operation["operator_id"], fingerprint_id, operation["operator_id"])).fetchone()[0]
    observable = conn.execute("SELECT COUNT(DISTINCT mint) FROM wt_walkback_edge_candidates WHERE selection_status='SELECTED'", ()).fetchone()[0]
    uniqueness = round(accepted * 100 / (accepted + external), 2) if accepted + external else None
    previous = conn.execute("SELECT uniqueness_percent FROM operation_fingerprint_health_snapshots WHERE operator_id=? AND fingerprint_id=? ORDER BY observed_at DESC LIMIT 1", (operation["operator_id"], fingerprint_id)).fetchone()
    if previous and previous[0] == uniqueness:
        return
    trend = "INSUFFICIENT_HISTORY" if not previous or uniqueness is None or previous[0] is None else ("STABLE" if abs(uniqueness - previous[0]) < 0.1 else ("INCREASING" if uniqueness > previous[0] else "DECREASING"))
    conn.execute("INSERT INTO operation_fingerprint_health_snapshots(snapshot_id,operator_id,fingerprint_id,observed_at,accepted_exact_matches,external_exact_matches,observable_comparison_count,uniqueness_percent,trend) VALUES(?,?,?,?,?,?,?,?,?)", (str(uuid.uuid4()), operation["operator_id"], fingerprint_id, now, accepted, external, observable, uniqueness, trend))


def _refresh_clusters(conn: sqlite3.Connection, operation: dict[str, Any], now: int) -> None:
    fingerprint_id = DEFINITIONS[operation["display_name"]].fingerprint_id
    rows = conn.execute("SELECT drift_signature,classification,COUNT(DISTINCT mint) mint_count,MIN(first_seen),MAX(latest_seen) FROM operation_fingerprint_drift_evidence WHERE operator_id=? AND fingerprint_id=? AND drift_signature IS NOT NULL GROUP BY drift_signature,classification", (operation["operator_id"], fingerprint_id)).fetchall()
    for signature, classification, count, first_seen, latest_seen in rows:
        state = "RECURRING" if count >= 2 else "OBSERVED"
        cluster_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{operation['operator_id']}:{signature}"))
        conn.execute("INSERT INTO operation_fingerprint_drift_clusters(cluster_id,operator_id,fingerprint_id,drift_signature,classification,mint_count,first_seen,latest_seen,related_potential_operation_id,relationship_type,reason) VALUES(?,?,?,?,?,?,?,?,NULL,?,?) ON CONFLICT(operator_id,fingerprint_id,drift_signature) DO UPDATE SET mint_count=excluded.mint_count,latest_seen=excluded.latest_seen,relationship_type=excluded.relationship_type", (cluster_id, operation["operator_id"], fingerprint_id, signature, classification, count, first_seen, latest_seen, state, "Address-independent drift signature; no automatic candidate promotion."))


def observe_completed_walkback(conn: sqlite3.Connection, mint: str, *, now: int | None = None) -> dict[str, int]:
    """Best-effort secondary projection. It has no membership write statements."""
    ensure_schema(conn)
    now = int(now or time.time())
    rows = _rows(conn, mint)
    observed = _route(rows)
    exact_profiles = _exact_profiles(conn, mint) if rows is not None else set()
    counts = Counter()
    for operation in _active_operations(conn):
        name = operation["display_name"]
        if name not in DEFINITIONS:
            continue
        expected = _expected_route(conn, name)
        if expected is None:
            _refresh_health(conn, operation, now)
            continue
        classification, matching, differing = compare_route(expected, observed)
        if name in exact_profiles:
            classification, matching, differing = "EXACT_MATCH", ["topology", "semantic_sequence", "amount_vector"], []
        _upsert_evidence(conn, operation, mint, classification, matching, differing, observed, expected, rows, now)
        counts[classification] += 1
        _refresh_health(conn, operation, now)
        _refresh_clusters(conn, operation, now)
    return dict(counts)


def latest_health(conn: sqlite3.Connection, operator_id: str, fingerprint_id: str) -> dict[str, Any] | None:
    try:
        row = conn.execute("SELECT accepted_exact_matches,external_exact_matches,observable_comparison_count,uniqueness_percent,trend FROM operation_fingerprint_health_snapshots WHERE operator_id=? AND fingerprint_id=? ORDER BY observed_at DESC LIMIT 1", (operator_id, fingerprint_id)).fetchone()
        drift = conn.execute("SELECT COUNT(DISTINCT mint) FROM operation_fingerprint_drift_evidence WHERE operator_id=? AND fingerprint_id=? AND classification LIKE 'NEAR_MATCH%'", (operator_id, fingerprint_id)).fetchone()[0]
        cluster = conn.execute("SELECT relationship_type,related_potential_operation_id,reason FROM operation_fingerprint_drift_clusters WHERE operator_id=? AND fingerprint_id=? ORDER BY mint_count DESC,latest_seen DESC LIMIT 1", (operator_id, fingerprint_id)).fetchone()
    except sqlite3.OperationalError:
        return None
    if not row:
        return None
    value = dict(row) if hasattr(row, "keys") else dict(zip(("accepted_exact_matches", "external_exact_matches", "observable_comparison_count", "uniqueness_percent", "trend"), row))
    value.update({"near_match_count": drift, "drift_status": "RECURRING" if cluster and cluster[0] == "RECURRING" else ("OBSERVED" if drift else "NO_RECURRING_NEAR_MATCH_OBSERVED"), "potential_relation": None if not cluster or not cluster[1] else {"type": cluster[0], "label": cluster[1], "reason": cluster[2], "href": f"/intelligence/potential-operations/{cluster[1]}"}})
    return value
