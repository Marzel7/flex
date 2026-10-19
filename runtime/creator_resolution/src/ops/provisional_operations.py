"""Review-only provisional operation contracts; never confirmed membership."""
from __future__ import annotations
import json
import sqlite3
import time
import uuid

PROVISIONAL_900B_OPERATOR_ID = "70f27e37-83eb-5c97-831c-48189ef98f6c"
PROVISIONAL_900B_DETECTOR_VERSION = "900B_HYBRID_PROVISIONAL.v1"
FROZEN_900B_RECURRENT_FUNDERS = frozenset({
    "5YJkZrwNVtrjQjuKStFNfuavWNZ2GdDsK3rW8m6dzq45", "6sSFfF9dtpSTx4CgK5XfN7uVxKd6or9sBvvcnPRK76xq",
    "71ptwE72WhnoAnpPw73RTr8bz5E9PraUyWkXhyhAkNrC", "ByZc7RNeYowEg2jKo2giytWb9WmNyZPrQ1hXhnGSzHTY",
    "GHDVSqV5TAXXgHUWpFgAeTuuvQ91ixkXDjPYyeYjaTKh",
})

DDL = """
CREATE TABLE IF NOT EXISTS operation_qualification_contracts (
 contract_id TEXT PRIMARY KEY, operator_id TEXT NOT NULL REFERENCES operators(operator_id), qualification_category TEXT NOT NULL CHECK(qualification_category IN ('CONFIRMED','PROVISIONAL')), automation_eligibility TEXT NOT NULL CHECK(automation_eligibility IN ('ELIGIBLE','REVIEW_ONLY')), detector_version TEXT NOT NULL, parent_mechanism TEXT, source_candidate_id TEXT, benchmark_json TEXT NOT NULL, contract_json TEXT NOT NULL, evidence_lineage_json TEXT NOT NULL, frozen_edge_highwater INTEGER, created_at INTEGER NOT NULL, UNIQUE(operator_id,detector_version)
);
CREATE TABLE IF NOT EXISTS provisional_operation_matches (
 match_id TEXT PRIMARY KEY, operator_id TEXT NOT NULL REFERENCES operators(operator_id), mint TEXT NOT NULL, detector_version TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('PROVISIONAL_MATCH_PENDING','PROVISIONAL_MATCH_CONFIRMED','PROVISIONAL_MATCH_REJECTED','BEHAVIOURAL_MATCH_UNKNOWN_INFRASTRUCTURE')), evidence_json TEXT NOT NULL, detected_at INTEGER NOT NULL, reviewed_at INTEGER, reviewer TEXT, review_note TEXT, UNIQUE(operator_id,mint,detector_version)
);
CREATE TABLE IF NOT EXISTS provisional_operation_activity_observations (
 observation_id TEXT PRIMARY KEY, operator_id TEXT NOT NULL REFERENCES operators(operator_id), mint TEXT NOT NULL,
 observed_at INTEGER NOT NULL, state TEXT NOT NULL CHECK(state IN ('HISTORICAL_PROVISIONAL_REFERENCE','PROVISIONAL_MATCH_PENDING','PROVISIONAL_MATCH_CONFIRMED','PROVISIONAL_MATCH_REJECTED')),
 provenance_json TEXT NOT NULL, UNIQUE(operator_id,mint,state)
);
"""

def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(DDL)

def classify_900b(edge: dict, recurrent_funders: set[str]) -> str | None:
    if not (edge.get("selection_status") == "SELECTED" and edge.get("hop_depth") == 1 and edge.get("mechanism") == "WSOL_WRAP_CLOSE" and edge.get("amount_lamports") == 999985000): return None
    return "PROVISIONAL_MATCH_PENDING" if edge.get("candidate_parent") in recurrent_funders else "BEHAVIOURAL_MATCH_UNKNOWN_INFRASTRUCTURE"

def record_provisional_match(conn: sqlite3.Connection, operator_id: str, mint: str, detector_version: str, state: str, evidence: dict) -> str:
    if state not in {"PROVISIONAL_MATCH_PENDING", "BEHAVIOURAL_MATCH_UNKNOWN_INFRASTRUCTURE"}: raise ValueError("provisional matches are pending/discovery only")
    match_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"provisional:{operator_id}:{mint}:{detector_version}"))
    conn.execute("INSERT OR IGNORE INTO provisional_operation_matches(match_id,operator_id,mint,detector_version,state,evidence_json,detected_at) VALUES(?,?,?,?,?,?,?)", (match_id,operator_id,mint,detector_version,state,json.dumps(evidence,sort_keys=True),int(time.time())))
    return match_id


_SELECTED_EDGE_COLUMNS = ("mint", "candidate_parent", "signature", "anchor_signature", "block_time", "anchor_block_time", "hop_depth", "mechanism", "amount_lamports", "evidence_key", "selection_status")


def _selected_edge_mapping(row: object) -> dict | None:
    """Normalize the worker's tuple rows and sqlite3.Row test/read rows."""
    if hasattr(row, "keys"):
        value = {name: row[name] for name in _SELECTED_EDGE_COLUMNS if name in row.keys()}
    elif isinstance(row, tuple) and len(row) == len(_SELECTED_EDGE_COLUMNS):
        value = dict(zip(_SELECTED_EDGE_COLUMNS, row))
    else:
        return None
    required = {"mint", "candidate_parent", "hop_depth", "mechanism", "amount_lamports", "evidence_key", "selection_status"}
    return value if required.issubset(value) else None


def project_900b_completed_walkback(conn: sqlite3.Connection, mint: str, *, core_db_path: str | None = None) -> str:
    """Project one completed walkback into the review-only 900b workflow.

    The selected edge is already persisted when this runs.  It never creates
    operator membership and never overwrites a review decision for this
    detector version.
    """
    try:
        edge = conn.execute(
            "SELECT mint,candidate_parent,signature,anchor_signature,block_time,anchor_block_time,hop_depth,mechanism,amount_lamports,evidence_key,selection_status "
            "FROM wt_walkback_edge_candidates WHERE mint=? AND selection_status='SELECTED' "
            "ORDER BY hop_depth ASC,last_observed_at DESC LIMIT 1", (mint,),
        ).fetchone()
    except sqlite3.OperationalError as exc:
        print(f"[900B] retained selected-edge schema unavailable mint={mint}: {exc}", flush=True)
        return "unobservable_selected_edge_schema"
    if edge is None:
        return "no_selected_edge"
    evidence = _selected_edge_mapping(edge)
    if evidence is None:
        return "malformed_selected_edge"
    state = classify_900b(evidence, FROZEN_900B_RECURRENT_FUNDERS)
    if state is None:
        return "not_900b"
    existing = conn.execute(
        "SELECT state FROM provisional_operation_matches WHERE operator_id=? AND mint=? AND detector_version=?",
        (PROVISIONAL_900B_OPERATOR_ID, mint, PROVISIONAL_900B_DETECTOR_VERSION),
    ).fetchone()
    if existing:
        return "preserved_terminal" if existing[0] in {"PROVISIONAL_MATCH_CONFIRMED", "PROVISIONAL_MATCH_REJECTED"} else "already_recorded"
    now = int(time.time())
    evidence.update({
        "source": "completed_walkback_selected_edge", "detector_version": PROVISIONAL_900B_DETECTOR_VERSION,
        "direct_funder": evidence["candidate_parent"], "classification_timestamp": now,
        "frozen_recurrent_infrastructure": evidence["candidate_parent"] in FROZEN_900B_RECURRENT_FUNDERS,
    })
    record_provisional_match(conn, PROVISIONAL_900B_OPERATOR_ID, mint, PROVISIONAL_900B_DETECTOR_VERSION, state, evidence)
    if state == "PROVISIONAL_MATCH_PENDING":
        observation_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"provisional-observation:{PROVISIONAL_900B_OPERATOR_ID}:{mint}:{state}"))
        conn.execute(
            "INSERT OR IGNORE INTO provisional_operation_activity_observations(observation_id,operator_id,mint,observed_at,state,provenance_json) VALUES(?,?,?,?,?,?)",
            (observation_id, PROVISIONAL_900B_OPERATOR_ID, mint, int(evidence.get("anchor_block_time") or evidence.get("block_time") or now), state, json.dumps(evidence, sort_keys=True)),
        )
        try:
            from src.ops.manual_registry import refresh_operator_activity_snapshot
            refresh_operator_activity_snapshot(conn, PROVISIONAL_900B_OPERATOR_ID, core_db_path=core_db_path, now=now)
        except Exception:
            pass
    return state
