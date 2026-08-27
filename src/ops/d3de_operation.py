"""Confirmed, address-independent projector for canonical P3R v2 d3de."""
from __future__ import annotations

import json
import sqlite3
import time
import uuid


OPERATOR_ID = "f560f4fa-770b-57aa-83be-954d11d1a3c1"
DISPLAY_NAME = "FOUR_STEP_30_SOL_14_479K_WSOL_LADDER"
SOURCE_CANDIDATE_ID = "p3r-v2-d3de29c88fe0ce5fa309"
DETECTOR_VERSION = "D3DE_D0_EXACT_SELECTED_FOUR_STEP_LADDER.v1"
SELECTED_ROUTE = (
    (1, "PLAIN_XFER", 29_999_985_000),
    (2, "WSOL_WRAP_CLOSE", 29_999_990_000),
    (3, "WSOL_WRAP_CLOSE", 14_479_000),
    (4, "WSOL_WRAP_CLOSE", 2_074_000),
)

DDL = """
CREATE TABLE IF NOT EXISTS confirmed_operation_matches (
    match_id TEXT PRIMARY KEY, operator_id TEXT NOT NULL REFERENCES operators(operator_id),
    mint TEXT NOT NULL, detector_version TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('CONFIRMED_MATCH')), evidence_json TEXT NOT NULL,
    detected_at INTEGER NOT NULL, UNIQUE(operator_id, mint, detector_version)
);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(DDL)


def selected_evidence(conn: sqlite3.Connection, mint: str) -> list[dict] | None:
    try:
        rows = conn.execute(
            "SELECT mint,hop_depth,mechanism,amount_lamports,wallet,candidate_parent,signature,"
            "anchor_signature,block_time,anchor_block_time,selection_status "
            "FROM wt_walkback_edge_candidates WHERE mint=? AND selection_status='SELECTED' "
            "ORDER BY hop_depth,signature",
            (mint,),
        ).fetchall()
    except sqlite3.OperationalError:
        return None
    return [dict(row) if hasattr(row, "keys") else dict(zip(
        ("mint", "hop_depth", "mechanism", "amount_lamports", "wallet", "candidate_parent",
         "signature", "anchor_signature", "block_time", "anchor_block_time", "selection_status"), row
    )) for row in rows]


def is_d0_match(evidence: list[dict] | None) -> bool:
    """D0: the complete exact selected four-hop route, with no address input."""
    if evidence is None:
        return False
    observed = tuple((row.get("hop_depth"), row.get("mechanism"), row.get("amount_lamports")) for row in evidence)
    return observed == SELECTED_ROUTE


def project_completed_walkback(conn: sqlite3.Connection, mint: str, *, core_db_path: str | None = None,
                               now: int | None = None) -> str:
    evidence = selected_evidence(conn, mint)
    if not is_d0_match(evidence):
        return "not_d3de_d0"
    if not conn.execute("SELECT 1 FROM operators WHERE operator_id=? AND status='CONFIRMED'", (OPERATOR_ID,)).fetchone():
        return "operator_not_registered"
    existing = conn.execute("SELECT operator_id FROM operator_launch_membership WHERE mint=?", (mint,)).fetchone()
    if existing and existing[0] != OPERATOR_ID:
        return "existing_other_operator"
    now = int(now or time.time())
    payload = {"detector_version": DETECTOR_VERSION, "predicate": "D0 exact selected route", "selected_edges": evidence}
    match_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{DETECTOR_VERSION}:{mint}"))
    conn.execute("INSERT OR IGNORE INTO confirmed_operation_matches(match_id,operator_id,mint,detector_version,state,evidence_json,detected_at) VALUES(?,?,?,?,?,?,?)", (match_id, OPERATOR_ID, mint, DETECTOR_VERSION, "CONFIRMED_MATCH", json.dumps(payload, sort_keys=True), now))
    conn.execute("INSERT OR IGNORE INTO operator_launch_membership(mint,operator_id,source_population_id,assigned_at,event_id) VALUES(?,?,?,?,?)", (mint, OPERATOR_ID, SOURCE_CANDIDATE_ID, now, match_id))
    from src.ops.manual_registry import refresh_operator_activity_snapshot
    refresh_operator_activity_snapshot(conn, OPERATOR_ID, core_db_path=core_db_path, now=now)
    return "admitted" if not existing else "already_present"
