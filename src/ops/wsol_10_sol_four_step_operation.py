"""Confirmed, address-independent projector for the 063e current child."""
from __future__ import annotations

import json
import sqlite3
import time
import uuid

OPERATOR_ID = "d8ee4d7a-fcd6-5a5b-b897-24f6ab56e334"
DISPLAY_NAME = "Byzantine"
SOURCE_CHILD_ID = "P3R_063E_BYZC_CURRENT"
DETECTOR_VERSION = "WSOL_10_SOL_FOUR_STEP_PROVISION_CLOSE.v1"
AMOUNT_LAMPORTS = 9_999_985_000
ATOMIC_SEQUENCE = ["createAccount", "initializeAccount", "syncNative", "closeAccount"]

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


def _mapping(row: object) -> dict | None:
    if row is None:
        return None
    if hasattr(row, "keys"):
        return {key: row[key] for key in row.keys()}
    if isinstance(row, tuple) and len(row) == 15:
        return dict(zip(("mint", "candidate_parent", "signature", "anchor_signature", "block_time", "anchor_block_time", "hop_depth", "mechanism", "amount_lamports", "selection_status", "instruction_order_json", "has_create", "has_sync_native", "has_close", "atomic_evidence_key"), row))
    return None


def is_strict_match(evidence: dict | None) -> bool:
    """The audited B1 gate: exact selected route and exact atomic sequence."""
    if not evidence or not (evidence.get("selection_status") == "SELECTED" and evidence.get("hop_depth") == 1 and evidence.get("mechanism") == "WSOL_WRAP_CLOSE" and evidence.get("amount_lamports") == AMOUNT_LAMPORTS and evidence.get("has_create") == 1 and evidence.get("has_sync_native") == 1 and evidence.get("has_close") == 1):
        return False
    try:
        return json.loads(evidence.get("instruction_order_json") or "[]") == ATOMIC_SEQUENCE
    except (TypeError, json.JSONDecodeError):
        return False


def selected_evidence(conn: sqlite3.Connection, mint: str) -> dict | None:
    try:
        row = conn.execute(
            "SELECT e.mint,e.candidate_parent,e.signature,e.anchor_signature,e.block_time,e.anchor_block_time,e.hop_depth,e.mechanism,e.amount_lamports,e.selection_status,a.instruction_order_json,a.has_create,a.has_sync_native,a.has_close,a.evidence_key AS atomic_evidence_key FROM wt_walkback_edge_candidates e JOIN wt_walkback_atomic_flows a ON a.mint=e.mint AND a.signature=e.signature WHERE e.mint=? AND e.selection_status='SELECTED' AND e.hop_depth=1 ORDER BY e.last_observed_at DESC LIMIT 1", (mint,)
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    return _mapping(row)


def project_completed_walkback(conn: sqlite3.Connection, mint: str, *, core_db_path: str | None = None, now: int | None = None) -> str:
    evidence = selected_evidence(conn, mint)
    if not is_strict_match(evidence):
        return "not_wsol_10_four_step"
    if not conn.execute("SELECT 1 FROM operators WHERE operator_id=? AND status='CONFIRMED'", (OPERATOR_ID,)).fetchone():
        return "operator_not_registered"
    existing = conn.execute("SELECT operator_id FROM operator_launch_membership WHERE mint=?", (mint,)).fetchone()
    if existing and existing[0] != OPERATOR_ID:
        return "existing_other_operator"
    now = int(now or time.time())
    evidence.update({"detector_version": DETECTOR_VERSION, "source": "completed_walkback_strict_b1"})
    match_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{DETECTOR_VERSION}:{mint}"))
    conn.execute("INSERT OR IGNORE INTO confirmed_operation_matches(match_id,operator_id,mint,detector_version,state,evidence_json,detected_at) VALUES(?,?,?,?,?,?,?)", (match_id, OPERATOR_ID, mint, DETECTOR_VERSION, "CONFIRMED_MATCH", json.dumps(evidence, sort_keys=True), now))
    conn.execute("INSERT OR IGNORE INTO operator_launch_membership(mint,operator_id,source_population_id,assigned_at,event_id) VALUES(?,?,?,?,?)", (mint, OPERATOR_ID, SOURCE_CHILD_ID, now, match_id))
    from src.ops.manual_registry import refresh_operator_activity_snapshot
    refresh_operator_activity_snapshot(conn, OPERATOR_ID, core_db_path=core_db_path, now=now)
    return "admitted" if not existing else "already_present"
