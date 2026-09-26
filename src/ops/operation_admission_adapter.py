"""Generic, durable, provider-free operation admission adapter.

This module is deliberately unbound from any live producer.  It persists only
compact immutable identities and makes canonical membership reachable solely
from a durable, explicit ADMIT outcome.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any, Mapping

EVENT_TYPE = "OPERATION_MEMBER_COMMITTED"
EVENT_VERSION = "OPERATION_MEMBER_COMMITTED_V1"
NORMAL_STORAGE_BUDGET_BYTES = 32 * 1024 * 1024
HARD_STORAGE_STOP_BYTES = 48 * 1024 * 1024
MAX_COMPACT_RECORD_BYTES = 2048
MAX_LOGICAL_RECORDS = HARD_STORAGE_STOP_BYTES // MAX_COMPACT_RECORD_BYTES
RETENTION_MAX_ROWS_PER_BATCH = 128


def _identity(*parts: object) -> str:
    return hashlib.sha256(json.dumps(parts, separators=(",", ":"), sort_keys=True).encode()).hexdigest()


def _compact(value: object, limit: int = 256) -> str:
    value = str(value)
    if not value or len(value.encode()) > limit:
        raise ValueError("admission compact-storage limit exceeded")
    return value


def _logical_record_count(conn: sqlite3.Connection) -> int:
    return sum(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in (
        "operation_admission_candidates", "operation_admission_outcomes", "operation_event_outbox"))


def enforce_storage_guard(conn: sqlite3.Connection, incoming_records: int = 1) -> None:
    """Fail closed before this feature's compact budget could be exceeded."""
    if incoming_records < 0 or _logical_record_count(conn) + incoming_records > MAX_LOGICAL_RECORDS:
        raise RuntimeError("OPERATION_ADMISSION_STORAGE_LIMIT_REACHED")


def candidate_identity(operation_id: str, mint: str, nomination_version: str, nomination_evidence_id: str) -> str:
    return _identity("OPERATION_CANDIDATE_V1", operation_id, mint, nomination_version, nomination_evidence_id)


def outcome_identity(candidate_id: str, assessment_id: str, policy_id: str, policy_version: str, decision: str, reason: str) -> str:
    return _identity("OPERATION_ADMISSION_OUTCOME_V1", candidate_id, assessment_id, policy_id, policy_version, decision, reason)


def event_identity(operation_id: str, mint: str, membership_id: str, admission_outcome_id: str) -> str:
    return _identity(EVENT_TYPE, EVENT_VERSION, operation_id, mint, membership_id, admission_outcome_id)


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS operation_admission_candidates (
      candidate_id TEXT PRIMARY KEY, operation_id TEXT NOT NULL, mint TEXT NOT NULL,
      nomination_type TEXT NOT NULL, nomination_semantic_version TEXT NOT NULL,
      nomination_evidence_id TEXT NOT NULL, lifecycle_state TEXT NOT NULL, created_at INTEGER NOT NULL,
      UNIQUE(operation_id,mint,nomination_semantic_version,nomination_evidence_id));
    CREATE TABLE IF NOT EXISTS operation_admission_outcomes (
      outcome_id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL, operation_id TEXT NOT NULL,
      assessment_id TEXT NOT NULL, assessment_semantic_version TEXT NOT NULL, assessment_result TEXT NOT NULL,
      policy_id TEXT NOT NULL, policy_version TEXT NOT NULL, admission_result TEXT NOT NULL, reason TEXT NOT NULL,
      causal_witness_id TEXT, capital_continuity_id TEXT, transaction_order_id TEXT, created_at INTEGER NOT NULL,
      UNIQUE(candidate_id,assessment_id,policy_id,policy_version,admission_result,reason));
    CREATE TABLE IF NOT EXISTS operation_event_outbox (
      event_id TEXT PRIMARY KEY, event_type TEXT NOT NULL, event_version TEXT NOT NULL,
      operation_id TEXT NOT NULL, mint TEXT NOT NULL, membership_id TEXT NOT NULL, candidate_id TEXT NOT NULL,
      admission_outcome_id TEXT NOT NULL, qualification_semantic_version TEXT NOT NULL, committed_at INTEGER NOT NULL,
      delivery_state TEXT NOT NULL DEFAULT 'PENDING', UNIQUE(event_type,operation_id,mint,membership_id,admission_outcome_id,event_version));
    """)


def persist_candidate(conn: sqlite3.Connection, nomination: Mapping[str, Any], now: int) -> str:
    for key in ("operation_id", "mint", "nomination_type", "nomination_semantic_version", "nomination_evidence_id"):
        _compact(nomination.get(key, ""))
    enforce_storage_guard(conn)
    cid = candidate_identity(nomination["operation_id"], nomination["mint"], nomination["nomination_semantic_version"], nomination["nomination_evidence_id"])
    conn.execute("INSERT OR IGNORE INTO operation_admission_candidates VALUES(?,?,?,?,?,?,?,?)", (cid, nomination["operation_id"], nomination["mint"], nomination.get("nomination_type", "RETAINED"), nomination["nomination_semantic_version"], nomination["nomination_evidence_id"], "CANDIDATE_DURABLE", now))
    return cid


def evaluate_policy(assessment: Mapping[str, Any], policy: Mapping[str, Any]) -> tuple[str, str]:
    """Evaluate frozen facts; policy configuration, never an operation name, selects action."""
    if assessment.get("state") == "CONFLICT" or assessment.get("conflict_state"):
        return "REJECT", "contradictory_evidence"
    if assessment.get("state") != policy.get("required_assessment_outcome"):
        return "INSUFFICIENT_EVIDENCE", "required_assessment_missing"
    if not assessment.get("evidence_complete", True):
        return "INSUFFICIENT_EVIDENCE", "evidence_incomplete"
    return policy.get("positive_action", "REVIEW"), "frozen_policy_positive"


def persist_outcome(conn: sqlite3.Connection, candidate_id: str, nomination: Mapping[str, Any], assessment: Mapping[str, Any], policy: Mapping[str, Any], now: int) -> str:
    for value in (candidate_id, assessment["assessment_id"], assessment["semantic_version"], policy["policy_id"], policy["policy_version"]): _compact(value)
    enforce_storage_guard(conn)
    decision, reason = evaluate_policy(assessment, policy)
    oid = outcome_identity(candidate_id, assessment["assessment_id"], policy["policy_id"], policy["policy_version"], decision, reason)
    conn.execute("INSERT OR IGNORE INTO operation_admission_outcomes VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (oid, candidate_id, nomination["operation_id"], assessment["assessment_id"], assessment["semantic_version"], assessment["state"], policy["policy_id"], policy["policy_version"], decision, reason, assessment.get("causal_witness_id"), assessment.get("capital_continuity_id"), assessment.get("transaction_order_id"), now))
    return oid


def prune_terminal_noncanonical(conn: sqlite3.Connection, *, older_than: int, limit: int = RETENTION_MAX_ROWS_PER_BATCH) -> int:
    """Delete one bounded batch only when no canonical or event provenance exists."""
    bounded = max(1, min(int(limit), RETENTION_MAX_ROWS_PER_BATCH))
    rows = conn.execute(
        "SELECT c.candidate_id FROM operation_admission_candidates c "
        "JOIN operation_admission_outcomes o USING(candidate_id) "
        "WHERE o.admission_result IN ('REJECT','INSUFFICIENT_EVIDENCE') AND o.created_at<? "
        "AND NOT EXISTS(SELECT 1 FROM operator_launch_membership m WHERE m.mint=c.mint) "
        "AND NOT EXISTS(SELECT 1 FROM operation_event_outbox e WHERE e.candidate_id=c.candidate_id) "
        "ORDER BY o.created_at,c.candidate_id LIMIT ?", (int(older_than), bounded),
    ).fetchall()
    for (candidate_id,) in rows:
        conn.execute("DELETE FROM operation_admission_outcomes WHERE candidate_id=?", (candidate_id,))
        conn.execute("DELETE FROM operation_admission_candidates WHERE candidate_id=?", (candidate_id,))
    return len(rows)


def resume_admission(conn: sqlite3.Connection, outcome_id: str, now: int) -> dict[str, str | None]:
    row = conn.execute("SELECT o.*,c.mint FROM operation_admission_outcomes o JOIN operation_admission_candidates c USING(candidate_id) WHERE outcome_id=?", (outcome_id,)).fetchone()
    if row is None: raise ValueError("durable admission outcome required")
    keys = [d[0] for d in conn.execute("SELECT o.*,c.mint FROM operation_admission_outcomes o JOIN operation_admission_candidates c USING(candidate_id) WHERE outcome_id=?", (outcome_id,)).description]
    value = dict(zip(keys, row))
    if value["admission_result"] != "ADMIT": return {"membership_id": None, "event_id": None}
    membership_id = _identity("OPERATION_MEMBERSHIP_V1", value["operation_id"], value["mint"], outcome_id)
    conn.execute("INSERT OR IGNORE INTO operator_launch_membership(mint,operator_id,source_population_id,assigned_at,event_id) VALUES(?,?,?,?,?)", (value["mint"], value["operation_id"], value["candidate_id"], now, membership_id))
    existing = conn.execute("SELECT operator_id,source_population_id,event_id FROM operator_launch_membership WHERE mint=?", (value["mint"],)).fetchone()
    if existing != (value["operation_id"], value["candidate_id"], membership_id): raise ValueError("canonical membership conflict")
    eid = event_identity(value["operation_id"], value["mint"], membership_id, outcome_id)
    conn.execute("INSERT OR IGNORE INTO operation_event_outbox(event_id,event_type,event_version,operation_id,mint,membership_id,candidate_id,admission_outcome_id,qualification_semantic_version,committed_at) VALUES(?,?,?,?,?,?,?,?,?,?)", (eid, EVENT_TYPE, EVENT_VERSION, value["operation_id"], value["mint"], membership_id, value["candidate_id"], outcome_id, value["assessment_semantic_version"], now))
    return {"membership_id": membership_id, "event_id": eid}


def record_review_only_candidate(conn: sqlite3.Connection, nomination: Mapping[str, Any], assessment: Mapping[str, Any], policy: Mapping[str, Any], now: int) -> tuple[str, str]:
    """Persist a review-facing nomination and immutable policy result only."""
    candidate_id = persist_candidate(conn, nomination, now)
    return candidate_id, persist_outcome(conn, candidate_id, nomination, assessment, policy, now)
