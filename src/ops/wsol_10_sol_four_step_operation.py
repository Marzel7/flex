"""Confirmed, address-independent projector for the 063e current child."""
from __future__ import annotations

import json
import datetime as dt
import os
import sqlite3
import time
import uuid

OPERATOR_ID = "d8ee4d7a-fcd6-5a5b-b897-24f6ab56e334"
DISPLAY_NAME = "Byzantine"
SOURCE_CHILD_ID = "P3R_063E_BYZC_CURRENT"
DETECTOR_VERSION = "WSOL_10_SOL_FOUR_STEP_PROVISION_CLOSE.v1"
CREATOR_CONTINUITY_VERSION = "BYZANTINE_PROVEN_CREATOR_CONTINUITY.v1"
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


def _epoch(value: object) -> int | None:
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
        except ValueError:
            return None
    return None


def _creator_continuity_proof(conn: sqlite3.Connection, creator: str) -> dict | None:
    """Return the earliest strict Byzantine proof for this creator, if any.

    This is deliberately derived from the same selected edge and atomic-flow
    evidence that admitted a canonical member; it is not a ByZc-address hint.
    """
    try:
        row = conn.execute(
            "SELECT m.mint AS proof_mint,e.signature,e.block_time,e.amount_lamports,"
            "a.evidence_key AS atomic_evidence_key FROM operator_launch_membership m "
            "JOIN wt_walkback_queue q ON q.mint=m.mint "
            "JOIN wt_walkback_edge_candidates e ON e.mint=m.mint "
            "AND e.selection_status='SELECTED' AND e.hop_depth=1 "
            "JOIN wt_walkback_atomic_flows a ON a.mint=e.mint AND a.signature=e.signature "
            "WHERE m.operator_id=? AND q.creator=? AND e.candidate_parent=? "
            "AND e.mechanism='WSOL_WRAP_CLOSE' AND e.amount_lamports=? "
            "AND a.has_create=1 AND a.has_sync_native=1 AND a.has_close=1 "
            "AND a.instruction_order_json=? ORDER BY e.block_time LIMIT 1",
            (OPERATOR_ID, creator, "ByZc7RNeYowEg2jKo2giytWb9WmNyZPrQ1hXhnGSzHTY",
             AMOUNT_LAMPORTS, json.dumps(ATOMIC_SEQUENCE)),
        ).fetchone()
    except sqlite3.Error:
        return None
    if not row:
        return None
    if hasattr(row, "keys"):
        return dict(row)
    return dict(zip(("proof_mint", "signature", "block_time", "amount_lamports", "atomic_evidence_key"), row))


def _continuity_evidence(conn: sqlite3.Connection, mint: str, core_db_path: str | None) -> dict | None:
    """Qualify a later mint only through an already canonical creator proof."""
    path = core_db_path or os.environ.get("DB_PATH")
    if not path or not os.path.exists(path):
        return None
    try:
        core = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        core.row_factory = sqlite3.Row
        row = core.execute("SELECT pf_ws_creator,created_at,create_tx_signature FROM token_analysis WHERE mint=?", (mint,)).fetchone()
        core.close()
    except sqlite3.Error:
        return None
    if not row or not row["pf_ws_creator"] or not row["create_tx_signature"]:
        return None
    proof = _creator_continuity_proof(conn, row["pf_ws_creator"])
    launch_time = _epoch(row["created_at"])
    if not proof or launch_time is None or launch_time <= int(proof["block_time"]):
        return None
    # An explicit active creator-family identity elsewhere is a governance
    # override; historical Byzantine proof never supersedes it.
    try:
        conflict = conn.execute(
            "SELECT 1 FROM operator_identity_assets WHERE asset_type='CREATOR_FAMILY' "
            "AND asset_value=? AND status='ACTIVE' AND operator_id<>? LIMIT 1",
            (row["pf_ws_creator"], OPERATOR_ID),
        ).fetchone()
        if conflict:
            return None
    except sqlite3.Error:
        pass
    return {"creator": row["pf_ws_creator"], "launch_time": launch_time,
            "create_tx_signature": row["create_tx_signature"], **proof}


def project_completed_walkback(conn: sqlite3.Connection, mint: str, *, core_db_path: str | None = None, now: int | None = None) -> str:
    evidence = selected_evidence(conn, mint)
    strict = is_strict_match(evidence)
    continuity = None if strict else _continuity_evidence(conn, mint, core_db_path)
    if not strict and not continuity:
        return "not_wsol_10_four_step"
    if not conn.execute("SELECT 1 FROM operators WHERE operator_id=? AND status='CONFIRMED'", (OPERATOR_ID,)).fetchone():
        return "operator_not_registered"
    existing = conn.execute("SELECT operator_id FROM operator_launch_membership WHERE mint=?", (mint,)).fetchone()
    if existing and existing[0] != OPERATOR_ID:
        return "existing_other_operator"
    now = int(now or time.time())
    if strict:
        evidence.update({"detector_version": DETECTOR_VERSION, "source": "completed_walkback_strict_b1"})
        version, source = DETECTOR_VERSION, SOURCE_CHILD_ID
    else:
        evidence = {"detector_version": CREATOR_CONTINUITY_VERSION, "source": "proven_creator_continuity", "qualification_route": "PROVEN_CREATOR_CONTINUITY", "creator_proof": continuity}
        version, source = CREATOR_CONTINUITY_VERSION, "BYZANTINE_PROVEN_CREATOR_CONTINUITY"
    match_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{version}:{mint}"))
    conn.execute("INSERT OR IGNORE INTO confirmed_operation_matches(match_id,operator_id,mint,detector_version,state,evidence_json,detected_at) VALUES(?,?,?,?,?,?,?)", (match_id, OPERATOR_ID, mint, version, "CONFIRMED_MATCH", json.dumps(evidence, sort_keys=True), now))
    conn.execute("INSERT OR IGNORE INTO operator_launch_membership(mint,operator_id,source_population_id,assigned_at,event_id) VALUES(?,?,?,?,?)", (mint, OPERATOR_ID, source, now, match_id))
    from src.ops.manual_registry import refresh_operator_activity_snapshot
    refresh_operator_activity_snapshot(conn, OPERATOR_ID, core_db_path=core_db_path, now=now)
    return "admitted" if not existing else "already_present"
