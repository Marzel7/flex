"""Generic, fixture-qualified manual REVIEW admission writer; no HTTP route."""
from __future__ import annotations

import sqlite3
import time
from typing import Mapping

from src.ops.manual_review_admission_contract import authorize_request, review_state_token
from src.ops.operation_admission_adapter import MAX_LOGICAL_RECORDS, enforce_storage_guard, persist_outcome

COMMENT_MAX_BYTES = 512
POLICY_ID = "MANUAL_REVIEW_ADMISSION_POLICY"
POLICY_VERSION = "v1"


def ensure_manual_approval_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS operation_manual_approvals (
      approval_id TEXT PRIMARY KEY, approval_version TEXT NOT NULL, operation_id TEXT NOT NULL,
      mint TEXT NOT NULL, candidate_id TEXT NOT NULL, review_outcome_id TEXT NOT NULL,
      assessment_id TEXT NOT NULL, assessment_semantic_version TEXT NOT NULL,
      policy_id TEXT NOT NULL, policy_version TEXT NOT NULL, approved_state_digest TEXT NOT NULL,
      approver_id TEXT NOT NULL, action TEXT NOT NULL CHECK(action IN ('APPROVE','DECLINE')),
      comment TEXT NOT NULL, created_at INTEGER NOT NULL,
      UNIQUE(candidate_id,review_outcome_id,approved_state_digest,approver_id,action,approval_version))""")


def _row(conn: sqlite3.Connection, candidate_id: str, review_outcome_id: str) -> dict:
    cursor = conn.execute("SELECT c.operation_id,c.mint,c.candidate_id,o.outcome_id AS review_outcome_id,"
        "o.assessment_id,o.assessment_semantic_version,o.assessment_result,o.policy_id,o.policy_version,o.admission_result "
        "FROM operation_admission_candidates c JOIN operation_admission_outcomes o USING(candidate_id) "
        "WHERE c.candidate_id=? AND o.outcome_id=?", (candidate_id, review_outcome_id))
    row = cursor.fetchone()
    if row is None: raise ValueError("MANUAL_APPROVAL_REVIEW_NOT_FOUND")
    return dict(zip((item[0] for item in cursor.description), row))


def _approval_storage_guard(conn: sqlite3.Connection, incoming: int) -> None:
    """Share the compact DEV-019 record ceiling; approval rows cannot bypass it."""
    existing = conn.execute("SELECT COUNT(*) FROM operation_manual_approvals").fetchone()[0]
    if existing + incoming > MAX_LOGICAL_RECORDS:
        raise RuntimeError("MANUAL_APPROVAL_STORAGE_LIMIT_REACHED")
    enforce_storage_guard(conn, incoming)


def write_manual_review_decision(conn: sqlite3.Connection, *, principal: Mapping, candidate_id: str,
                                 review_outcome_id: str, state_token: str, action: str,
                                 comment: str = "", now: int | None = None) -> dict:
    """Short transaction: approval provenance plus ADMIT intent only, never membership/event."""
    if len(comment.encode()) > COMMENT_MAX_BYTES: raise ValueError("MANUAL_APPROVAL_COMMENT_TOO_LARGE")
    now = int(time.time()) if now is None else int(now)
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = _row(conn, candidate_id, review_outcome_id)
        membership_exists = bool(conn.execute("SELECT 1 FROM operator_launch_membership WHERE mint=?", (row["mint"],)).fetchone())
        decision = authorize_request(principal=principal, row=row, supplied_token=state_token, action=action, membership_exists=membership_exists)
        if decision["result"] == "ALREADY_CANONICAL": conn.rollback(); return decision
        _approval_storage_guard(conn, 2 if action == "APPROVE" else 1)
        approval_id = decision["approval_id"]
        conn.execute("INSERT OR IGNORE INTO operation_manual_approvals VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            approval_id, "MANUAL_REVIEW_APPROVAL_V1", row["operation_id"], row["mint"], candidate_id, review_outcome_id,
            row["assessment_id"], row["assessment_semantic_version"], row["policy_id"], row["policy_version"],
            review_state_token(row), principal["id"], action, comment, now))
        outcome_id = None
        if action == "APPROVE":
            nomination = {"operation_id":row["operation_id"], "mint":row["mint"]}
            assessment = {"assessment_id":row["assessment_id"], "semantic_version":row["assessment_semantic_version"],
                          "state":row["assessment_result"], "evidence_complete":True}
            policy = {"policy_id":POLICY_ID,"policy_version":POLICY_VERSION,
                      "required_assessment_outcome":row["assessment_result"],"positive_action":"ADMIT"}
            outcome_id = persist_outcome(conn, candidate_id, nomination, assessment, policy, now)
        conn.commit()
        return {**decision, "approval_id":approval_id, "admission_outcome_id":outcome_id}
    except Exception:
        conn.rollback(); raise
