"""Generic, post-commit priority requests for existing evidence pipelines.

This module deliberately knows nothing about a particular operation or the
evidence predicate.  A caller decides eligibility after its source launch has
committed; this module records that decision and only upgrades an existing
mint-keyed Walkback job (or creates that same job through the normal enqueue
adapter).  It never classifies a mint and never writes membership.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from typing import Any, Callable, Optional

NORMAL = 0
OPERATION_CANDIDATE_HIGH = 100
FAIR_HIGH_BURST = 3


def enabled() -> bool:
    """Configured durable state is fail-closed; otherwise retain env behavior."""
    state_path = os.environ.get("OPERATION_EVIDENCE_PRIORITY_STATE_PATH")
    if state_path:
        try:
            state = json.loads(open(state_path).read())
            return (state.get("schema") == "durable-runtime-feature-state.v1"
                    and state.get("feature") == "OPERATION_EVIDENCE_PRIORITY_ENABLED"
                    and state.get("state") == "ON")
        except (OSError, ValueError, TypeError):
            return False
    return os.environ.get("OPERATION_EVIDENCE_PRIORITY_ENABLED", "0").lower() in {"1", "true", "yes"}


def request_id(*, operation_id: str, operation_version: str, entity_type: str,
               entity_id: str, evidence_pipeline: str, reason_version: str) -> str:
    """Stable identity: duplicate post-commit dispatches resolve to one row."""
    values = (operation_id, operation_version, entity_type, entity_id,
              evidence_pipeline, reason_version)
    return hashlib.sha256("\x1f".join(values).encode("utf-8")).hexdigest()


SCHEMA_PREREQUISITES = ()


def migrate_schema_step(conn: sqlite3.Connection) -> dict:
    ddl = """
    CREATE TABLE IF NOT EXISTS operation_evidence_priority_requests (
      request_id TEXT PRIMARY KEY,
      operation_id TEXT NOT NULL, operation_version TEXT NOT NULL,
      entity_type TEXT NOT NULL, entity_id TEXT NOT NULL,
      source_launch_id TEXT NOT NULL, creator_context_ref TEXT,
      cohort_version TEXT, priority_class INTEGER NOT NULL,
      reason_version TEXT NOT NULL, evidence_pipeline TEXT NOT NULL,
      state TEXT NOT NULL DEFAULT 'REQUESTED', linked_job_id TEXT,
      source_launch_committed_at INTEGER NOT NULL, requested_at INTEGER NOT NULL,
      applied_at INTEGER, t0_launch_commit_at INTEGER, t1_enqueue_commit_at INTEGER,
      t2_lease_start_at INTEGER, t3_acquisition_start_at INTEGER,
      t4_evidence_materialized_at INTEGER, t5_classifier_evaluated_at INTEGER,
      t6_membership_committed_at INTEGER,
      UNIQUE(operation_id,operation_version,entity_type,entity_id,evidence_pipeline,reason_version)
    );
    CREATE INDEX IF NOT EXISTS ix_operation_evidence_priority_pending
      ON operation_evidence_priority_requests(state, priority_class DESC, requested_at);
    CREATE TABLE IF NOT EXISTS wt_walkback_scheduler_state (
      scheduler_name TEXT PRIMARY KEY, consecutive_high INTEGER NOT NULL DEFAULT 0,
      updated_at INTEGER NOT NULL
    );
    """
    for statement in (part.strip() for part in ddl.split(";")):
        if statement:
            conn.execute(statement)
    return {"changed": True}


def ensure_schema(conn: sqlite3.Connection) -> None:
    migrate_schema_step(conn)
    conn.commit()


def record_post_commit_request(
    conn: sqlite3.Connection, *, operation_id: str, operation_version: str,
    mint: str, source_launch_id: str, source_launch_committed_at: int,
    creator_context_ref: Optional[str], cohort_version: Optional[str],
    reason_version: str, evidence_pipeline: str = "WALKBACK",
    priority_class: int = OPERATION_CANDIDATE_HIGH,
) -> str:
    """Persist a request; callers must invoke only after their launch commit."""
    if not source_launch_committed_at:
        raise ValueError("post-commit priority request requires source_launch_committed_at")
    ensure_schema(conn)
    rid = request_id(operation_id=operation_id, operation_version=operation_version,
                     entity_type="mint", entity_id=mint,
                     evidence_pipeline=evidence_pipeline, reason_version=reason_version)
    now = int(time.time())
    conn.execute("""INSERT INTO operation_evidence_priority_requests
      (request_id,operation_id,operation_version,entity_type,entity_id,source_launch_id,
       creator_context_ref,cohort_version,priority_class,reason_version,evidence_pipeline,
       source_launch_committed_at,requested_at,t0_launch_commit_at)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(request_id) DO NOTHING""",
      (rid,operation_id,operation_version,"mint",mint,source_launch_id,creator_context_ref,
       cohort_version,priority_class,reason_version,evidence_pipeline,
       source_launch_committed_at,now,source_launch_committed_at))
    conn.commit()
    return rid


def apply_walkback_request(conn: sqlite3.Connection, *, request: str,
                           enqueue: Callable[..., Any], creator: Optional[str],
                           create_signature: Optional[str] = None,
                           create_slot: Optional[int] = None,
                           create_block_time: Optional[int] = None) -> str:
    """Idempotently create/upgrade the single existing queue job, never rerun terminal work."""
    ensure_schema(conn)
    row = conn.execute("SELECT * FROM operation_evidence_priority_requests WHERE request_id=?", (request,)).fetchone()
    if row is None:
        raise KeyError(request)
    mint, now = row["entity_id"], int(time.time())
    job = conn.execute("SELECT status,COALESCE(priority,0) priority FROM wt_walkback_queue WHERE mint=?", (mint,)).fetchone()
    if job is None:
        enqueue(conn, mint=mint, creator=creator, create_signature=create_signature,
                create_slot=create_slot, create_block_time=create_block_time,
                create_source="committed_launch_priority")
        job = conn.execute("SELECT status,COALESCE(priority,0) priority FROM wt_walkback_queue WHERE mint=?", (mint,)).fetchone()
    state = "APPLIED"
    if job and job["status"] in ("pending", "waiting"):
        if job["priority"] < row["priority_class"]:
            conn.execute("UPDATE wt_walkback_queue SET priority=?, priority_reason=?, updated_at=? WHERE mint=?",
                         (row["priority_class"], "operation_priority:" + row["reason_version"], now, mint))
        else:
            state = "ALREADY_HIGH"
    elif job and job["status"] == "running": state = "IN_PROGRESS"
    elif job and job["status"] in ("complete", "skipped"): state = "COMPLETED_EXISTING"
    elif job and job["status"] == "failed": state = "TERMINAL_ERROR"
    conn.execute("""UPDATE operation_evidence_priority_requests
      SET state=?,linked_job_id=?,applied_at=?,t1_enqueue_commit_at=COALESCE(t1_enqueue_commit_at,?)
      WHERE request_id=?""", (state, mint if job else None, now, now, request))
    conn.commit()
    return state


def stamp_timing(conn: sqlite3.Connection, *, mint: str, field: str, at: Optional[int] = None) -> None:
    allowed = {"t2_lease_start_at", "t3_acquisition_start_at", "t4_evidence_materialized_at",
               "t5_classifier_evaluated_at", "t6_membership_committed_at"}
    if field not in allowed: raise ValueError(field)
    ensure_schema(conn)
    conn.execute(f"UPDATE operation_evidence_priority_requests SET {field}=COALESCE({field},?) WHERE entity_id=?", (at or int(time.time()), mint))
    conn.commit()


def fair_order(rows: list[sqlite3.Row], consecutive_high: int) -> list[sqlite3.Row]:
    """FIFO inside each class; after three highs, take one eligible normal."""
    high = [r for r in rows if (r["priority"] or 0) > NORMAL]
    normal = [r for r in rows if (r["priority"] or 0) <= NORMAL]
    if high and normal and consecutive_high >= FAIR_HIGH_BURST:
        return normal[:1] + high + normal[1:]
    return high + normal
