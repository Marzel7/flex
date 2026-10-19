"""One bounded canonical membership commit for a validated manual proposal."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from typing import Any

from src.ops.operation_attribution_evidence import CONTRACT_VERSION


COMMIT_INTENT_SCHEMA = "CANONICAL_MANUAL_OPERATION_COMMIT_INTENT_V1"
MEMBERSHIP_SOURCE = "MANUAL_V1_PROMOTION"


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _ensure_intent_store(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS canonical_manual_operation_commit_intents("
        "proposal_id TEXT NOT NULL,decision_digest TEXT NOT NULL,operation_id TEXT NOT NULL,"
        "member_mints_json TEXT NOT NULL,membership_source TEXT NOT NULL,"
        "attribution_contract_version TEXT NOT NULL,promotion_timestamp INTEGER NOT NULL,"
        "PRIMARY KEY(proposal_id,decision_digest))"
    )


def _ensure_operation(conn: sqlite3.Connection, operation_id: str, label: str, now: int) -> None:
    if conn.execute("SELECT 1 FROM operators WHERE operator_id=?", (operation_id,)).fetchone():
        return
    columns = _columns(conn, "operators")
    values = {
        "operator_id": operation_id, "status": "CONFIRMED", "confidence": "HIGH",
        "summary": "Explicit manual V1 promotion", "review_state": "REVIEWED",
        "display_name": label, "created_at": now, "updated_at": now,
    }
    names = [name for name in values if name in columns]
    if "operator_id" not in names:
        raise ValueError("canonical operators table is incompatible")
    conn.execute(
        f"INSERT INTO operators({','.join(names)}) VALUES({','.join('?' for _ in names)})",
        tuple(values[name] for name in names),
    )


def promote_canonical_manual_operation(
    conn: sqlite3.Connection, *, proposal_id: str, decision: dict[str, Any],
    operation_id: str, operation_label: str, member_mints: list[str], now: int | None = None,
) -> dict[str, Any]:
    """Commit only canonical membership rows, atomically and idempotently."""
    if decision.get("attribution_state") != "ATTRIBUTION_PROVEN" or not decision.get("promotion_eligible"):
        raise ValueError("promotion requires ATTRIBUTION_PROVEN and promotion eligibility")
    if not member_mints or len(member_mints) != len(set(member_mints)):
        raise ValueError("validated member set is invalid")
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='operator_launch_membership'").fetchone():
        raise ValueError("canonical membership writer unavailable")
    timestamp = int(time.time()) if now is None else int(now)
    decision_digest = _digest(decision)
    intent = {
        "schema": COMMIT_INTENT_SCHEMA, "proposal_id": proposal_id,
        "validated_decision_ref": decision_digest, "operation_id": operation_id,
        "member_mints": sorted(member_mints), "membership_source": MEMBERSHIP_SOURCE,
        "attribution_contract_version": CONTRACT_VERSION, "decision_digest": decision_digest,
        "promotion_timestamp": timestamp,
    }
    _ensure_intent_store(conn)
    conn.execute("BEGIN IMMEDIATE")
    try:
        existing = conn.execute(
            "SELECT 1 FROM canonical_manual_operation_commit_intents WHERE proposal_id=? AND decision_digest=?",
            (proposal_id, decision_digest),
        ).fetchone()
        if existing:
            conn.rollback()
            return {"intent": intent, "rows_expected": len(member_mints), "rows_written": 0, "duplicate_rows_written": 0, "idempotent": True}
        conflicts = conn.execute(
            f"SELECT mint,operator_id FROM operator_launch_membership WHERE mint IN ({','.join('?' for _ in member_mints)}) AND operator_id!=?",
            (*member_mints, operation_id),
        ).fetchall()
        if conflicts:
            raise ValueError("canonical membership conflict")
        _ensure_operation(conn, operation_id, operation_label, timestamp)
        written = 0
        for mint in sorted(member_mints):
            before = conn.execute("SELECT 1 FROM operator_launch_membership WHERE mint=?", (mint,)).fetchone()
            if not before:
                conn.execute(
                    "INSERT INTO operator_launch_membership(mint,operator_id,source_population_id,assigned_at,event_id) VALUES(?,?,?,?,NULL)",
                    (mint, operation_id, MEMBERSHIP_SOURCE, timestamp),
                )
                written += 1
        if written != len(member_mints):
            raise ValueError("validated member set was already partially committed")
        conn.execute(
            "INSERT INTO canonical_manual_operation_commit_intents VALUES(?,?,?,?,?,?,?)",
            (proposal_id, decision_digest, operation_id, json.dumps(sorted(member_mints)), MEMBERSHIP_SOURCE, CONTRACT_VERSION, timestamp),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {"intent": intent, "rows_expected": len(member_mints), "rows_written": written, "duplicate_rows_written": 0, "idempotent": False}
