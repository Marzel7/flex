"""Persistent bridge: candidate id -> trusted V1 validation -> canonical promotion."""
from __future__ import annotations

import json
import sqlite3
from hashlib import sha256
from typing import Any, Callable

from src.ops.canonical_manual_operation_promotion import promote_canonical_manual_operation
from src.ops.operation_attribution_manual import ensure_schema, propose, validate
from src.ops.potential_operation_validation import resolve_validation_input

STORE_VERSION = "OPERATION_ATTRIBUTION_MANUAL_PROPOSAL_STORE_V1"


def ensure_bridge(conn: sqlite3.Connection) -> None:
    ensure_schema(conn)
    conn.execute("CREATE TABLE IF NOT EXISTS operation_attribution_manual_proposals(proposal_id TEXT PRIMARY KEY,candidate_source_type TEXT,candidate_source_id TEXT,candidate_snapshot_ref TEXT,proposed_operation_label TEXT,created_at INT,validation_state TEXT,latest_decision_ref TEXT,promotion_state TEXT,promoted_operation_id TEXT,candidate_member_refs_json TEXT)")
    columns = {row[1] for row in conn.execute("PRAGMA table_info(operation_attribution_manual_proposals)")}
    if "candidate_member_refs_json" not in columns:
        conn.execute("ALTER TABLE operation_attribution_manual_proposals ADD COLUMN candidate_member_refs_json TEXT")
    conn.execute("CREATE TABLE IF NOT EXISTS manual_attribution_workflow_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
    conn.execute("INSERT INTO manual_attribution_workflow_metadata(key,value) VALUES('schema_version',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (STORE_VERSION,))
    conn.commit()


def proposal_id(source_type: str, source_id: str, snapshot: str) -> str:
    return sha256(f"{source_type}|{source_id}|{snapshot}".encode()).hexdigest()


def _validate_resolved_input(conn: sqlite3.Connection, resolved: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    ensure_bridge(conn)
    source_id, source_type, snapshot = (resolved[key] for key in ("candidate_source_id", "candidate_source_type", "candidate_snapshot_ref"))
    pid = proposal_id(source_type, source_id, snapshot)
    conn.execute("INSERT OR IGNORE INTO operation_attribution_manual_proposals(proposal_id,candidate_source_type,candidate_source_id,candidate_snapshot_ref,proposed_operation_label,created_at,validation_state,latest_decision_ref,promotion_state,promoted_operation_id,candidate_member_refs_json) VALUES(?,?,?,?,?,0,'NOT_VALIDATED',NULL,'NOT_PROMOTED',NULL,?)", (pid, source_type, source_id, snapshot, resolved.get("proposed_operation_label"), json.dumps(resolved["candidate_member_refs"], sort_keys=True)))
    conn.commit()
    propose(conn, pid, source_type, resolved["detector_contract"], resolved["candidate_member_refs"])
    decision = validate(conn, pid, resolved["evidence_families"], candidate_id=source_id, proposed_operation_id=resolved["proposed_operation_id"], detector_evidence_refs=resolved["detector_evidence_refs"], common_infrastructure_exclusions=resolved.get("common_infrastructure_exclusions", ()), completeness_state=resolved.get("completeness_state", "COMPLETE"))
    state = decision.get("validation_state", decision.get("attribution_state"))
    conn.execute("UPDATE operation_attribution_manual_proposals SET validation_state=?,latest_decision_ref=? WHERE proposal_id=?", (state, decision.get("decision_id"), pid))
    conn.commit()
    return pid, decision


def validate_potential_operation(conn: sqlite3.Connection, candidate_source_id: str) -> tuple[str, dict[str, Any]]:
    """Validate a candidate using server-side retained evidence only."""
    return _validate_resolved_input(conn, resolve_validation_input(candidate_source_id))


def promote_validated_operation(workflow_conn: sqlite3.Connection, canonical_conn: sqlite3.Connection, proposal_id_value: str, *, resolver: Callable[[str], dict[str, Any]] = resolve_validation_input) -> dict[str, Any]:
    """Re-check current snapshot then perform the one canonical commit."""
    ensure_bridge(workflow_conn)
    proposal = workflow_conn.execute("SELECT * FROM operation_attribution_manual_proposals WHERE proposal_id=?", (proposal_id_value,)).fetchone()
    if not proposal:
        raise ValueError("proposal not found")
    if proposal[6] != "ATTRIBUTION_PROVEN" or proposal[8] == "PROMOTED":
        if proposal[8] == "PROMOTED":
            return {"rows_written": 0, "duplicate_rows_written": 0, "idempotent": True}
        raise ValueError("promotion requires ATTRIBUTION_PROVEN")
    resolved = resolver(proposal[2])
    if resolved["candidate_snapshot_ref"] != proposal[3]:
        raise ValueError("proposal snapshot is stale")
    members = json.loads(proposal[10] or "[]")
    if sorted(members) != sorted(resolved["candidate_member_refs"]):
        raise ValueError("validated member set is stale")
    row = workflow_conn.execute("SELECT decision_json FROM manual_attribution_decisions WHERE decision_id=?", (proposal[7],)).fetchone()
    if not row:
        raise ValueError("validated decision unavailable")
    decision = json.loads(row[0])
    outcome = promote_canonical_manual_operation(canonical_conn, proposal_id=proposal_id_value, decision=decision, operation_id=resolved["proposed_operation_id"], operation_label=resolved["proposed_operation_label"], member_mints=members)
    if not outcome["idempotent"]:
        workflow_conn.execute("UPDATE operation_attribution_manual_proposals SET promotion_state='PROMOTED',promoted_operation_id=? WHERE proposal_id=?", (resolved["proposed_operation_id"], proposal_id_value))
        workflow_conn.commit()
    return outcome


def read_model(conn: sqlite3.Connection, source_id: str, snapshot: str, source_type: str = "POTENTIAL_OPERATION") -> dict[str, Any]:
    pid = proposal_id(source_type, source_id, snapshot)
    row = conn.execute("SELECT validation_state,promotion_state FROM operation_attribution_manual_proposals WHERE proposal_id=?", (pid,)).fetchone()
    return {"proposal_id": pid if row else None, "validation_state": row[0] if row else "NOT_VALIDATED", "promotion_state": row[1] if row else "NOT_PROMOTED"}
