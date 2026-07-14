"""
Operations OS — Operator Store.

Persistent read/write access to the operators/* tables in the ops DB.

Rules:
  - Human-decided states (CONFIRMED, REJECTED) are NEVER overwritten by resolution.
  - Evidence is append-only.  Updating an operator re-derives confidence but does
    not delete previous evidence.
  - All writes are transactional.
  - fetch_* methods are fault-tolerant (return [] / None on error).
"""
from __future__ import annotations

import json
import sqlite3
import time
from typing import Any
from src.utils.db_locking import db_connect

from src.ops.operator_model import (
    Operator, OperatorEvidence,
    EVIDENCE_CATALOGUE, EVIDENCE_CATEGORIES,
    OPERATOR_STATES, HUMAN_DECIDED,
    CONFIDENCE_UNKNOWN, CONFIDENCE_LOW, CONFIDENCE_MEDIUM,
    CONFIDENCE_HIGH, CONFIDENCE_CERTAIN,
    CANDIDATE, REVIEW_CANDIDATE, PROVISIONAL,
    new_operator_id, new_evidence_id, new_review_id,
)


def _derive_confidence(identity_count: int, supporting_count: int) -> str:
    """
    Derive operator confidence from evidence counts.

    Rules (conservative):
      ≥2 IDENTITY signals              → HIGH
      1  IDENTITY + ≥2 SUPPORTING      → MEDIUM
      1  IDENTITY + 0-1 SUPPORTING     → LOW
      0  IDENTITY (supporting only)    → UNKNOWN  (should not reach PROVISIONAL)

    CERTAIN is reserved for human-confirmed operators (set externally on CONFIRMED).
    """
    if identity_count >= 3:
        return CONFIDENCE_HIGH
    if identity_count == 2:
        return CONFIDENCE_MEDIUM if supporting_count >= 1 else CONFIDENCE_LOW
    if identity_count == 1:
        return CONFIDENCE_LOW
    return CONFIDENCE_UNKNOWN


def _derive_status(identity_class_count: int, current_status: str) -> str:
    """
    Advance status based on evidence — never touch human-decided states.
    """
    if current_status in HUMAN_DECIDED:
        return current_status
    if identity_class_count >= 2:
        return PROVISIONAL
    if identity_class_count == 1:
        return REVIEW_CANDIDATE
    return CANDIDATE


class OperatorStore:

    def __init__(self, db_path: str) -> None:
        self._path = db_path

    def initialize_schema(self) -> None:
        """Explicit legacy bootstrap; construction is deliberately read/write inert."""
        from src.ops.operator_writer import OperatorWriter

        OperatorWriter(self._path).initialize_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = db_connect(self._path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    # ── Create / upsert operator ──────────────────────────────────────────────

    def create_operator(
        self,
        *,
        summary: str | None = None,
        display_name: str | None = None,
    ) -> str:
        """Create a new CANDIDATE operator and return its operator_id."""
        now = int(time.time())
        op_id = new_operator_id()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO operators
                    (operator_id, status, confidence, summary, review_state,
                     display_name, created_at, updated_at)
                VALUES (?, 'CANDIDATE', 'UNKNOWN', ?, 'PENDING', ?, ?, ?)
                """,
                (op_id, summary, display_name, now, now),
            )
        return op_id

    def add_entity(
        self,
        operator_id: str,
        entity_address: str,
        *,
        entity_type: str = "UNKNOWN",
        confidence: str = "UNKNOWN",
        first_seen: int | None = None,
        last_seen:  int | None = None,
    ) -> None:
        """Add or update an entity within an operator."""
        now = int(time.time())
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO operator_entities
                    (operator_id, entity_address, entity_type, confidence,
                     evidence_count, first_seen, last_seen, added_at)
                VALUES (?, ?, ?, ?, 0, ?, ?, ?)
                ON CONFLICT(operator_id, entity_address) DO UPDATE SET
                    entity_type    = excluded.entity_type,
                    confidence     = excluded.confidence,
                    first_seen     = CASE WHEN excluded.first_seen IS NOT NULL
                                          AND (first_seen IS NULL
                                               OR excluded.first_seen < first_seen)
                                     THEN excluded.first_seen ELSE first_seen END,
                    last_seen      = CASE WHEN excluded.last_seen IS NOT NULL
                                          AND (last_seen IS NULL
                                               OR excluded.last_seen > last_seen)
                                     THEN excluded.last_seen ELSE last_seen END,
                    evidence_count = evidence_count + 1
                """,
                (operator_id, entity_address, entity_type, confidence,
                 first_seen, last_seen, now),
            )

    def add_evidence(
        self,
        operator_id: str,
        *,
        evidence_type: str,
        source_operation: str | None = None,
        entity_a: str | None = None,
        entity_b: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> str:
        """
        Record one piece of evidence for an operator.
        Returns the evidence_id.
        Re-derives operator status and confidence after each addition.
        """
        if evidence_type not in EVIDENCE_CATALOGUE:
            raise ValueError(f"Unknown evidence type: {evidence_type!r}")

        cat    = EVIDENCE_CATALOGUE[evidence_type]["category"]
        weight = EVIDENCE_CATALOGUE[evidence_type]["weight"]
        now    = int(time.time())
        ev_id  = new_evidence_id()

        with self._connect() as conn:
            # Append evidence
            conn.execute(
                """
                INSERT INTO operator_evidence
                    (evidence_id, operator_id, evidence_type, evidence_category,
                     source_operation, entity_a, entity_b, weight, details, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (ev_id, operator_id, evidence_type, cat, source_operation,
                 entity_a, entity_b, weight, json.dumps(details or {}), now),
            )

            # Re-derive status + confidence
            row = conn.execute(
                """
                SELECT
                    COUNT(DISTINCT CASE WHEN evidence_category='IDENTITY'
                                        THEN evidence_type END) AS id_cnt,
                    SUM(CASE WHEN evidence_category='SUPPORTING' THEN 1 ELSE 0 END) AS sup_cnt
                FROM operator_evidence
                WHERE operator_id = ?
                """,
                (operator_id,),
            ).fetchone()
            id_cnt  = row["id_cnt"]  or 0
            sup_cnt = row["sup_cnt"] or 0

            cur = conn.execute(
                "SELECT status FROM operators WHERE operator_id = ?",
                (operator_id,),
            ).fetchone()
            if not cur:
                return ev_id

            new_status = _derive_status(id_cnt, cur["status"])
            new_conf   = _derive_confidence(id_cnt, sup_cnt)

            conn.execute(
                """
                UPDATE operators SET
                    status     = ?,
                    confidence = ?,
                    updated_at = ?
                WHERE operator_id = ?
                """,
                (new_status, new_conf, now, operator_id),
            )

        return ev_id

    def update_timestamps(
        self,
        operator_id: str,
        first_seen: int | None,
        last_seen:  int | None,
    ) -> None:
        now = int(time.time())
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE operators SET
                    first_seen = CASE WHEN ? IS NOT NULL AND (first_seen IS NULL OR ? < first_seen)
                                      THEN ? ELSE first_seen END,
                    last_seen  = CASE WHEN ? IS NOT NULL AND (last_seen IS NULL  OR ? > last_seen)
                                      THEN ? ELSE last_seen END,
                    updated_at = ?
                WHERE operator_id = ?
                """,
                (first_seen, first_seen, first_seen,
                 last_seen,  last_seen,  last_seen,
                 now, operator_id),
            )

    def record_review(
        self,
        operator_id: str,
        *,
        decision: str,
        reviewer: str | None = None,
        reason: str | None = None,
        related_operator_id: str | None = None,
    ) -> str:
        """
        Record a human review decision.
        Updates operator status to match the decision.
        Returns the review_id.
        """
        from src.ops.operator_model import CONFIRMED, REJECTED, MERGE_REVIEW, SPLIT_REVIEW
        DECISION_TO_STATUS = {
            "CONFIRMED": CONFIRMED,
            "REJECTED":  REJECTED,
            "MERGE":     MERGE_REVIEW,
            "SPLIT":     SPLIT_REVIEW,
        }
        now       = int(time.time())
        review_id = new_review_id()
        new_status = DECISION_TO_STATUS.get(decision)

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO operator_reviews
                    (review_id, operator_id, decision, reviewer,
                     timestamp, reason, related_operator_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (review_id, operator_id, decision, reviewer, now,
                 reason, related_operator_id),
            )
            if new_status:
                new_conf = CONFIDENCE_CERTAIN if new_status == CONFIRMED else CONFIDENCE_UNKNOWN
                conn.execute(
                    """
                    UPDATE operators SET
                        status       = ?,
                        confidence   = ?,
                        review_state = 'REVIEWED',
                        updated_at   = ?
                    WHERE operator_id = ?
                    """,
                    (new_status, new_conf, now, operator_id),
                )
        return review_id

    # ── Reads ──────────────────────────────────────────────────────────────────

    def fetch_operator(self, operator_id: str) -> dict | None:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM operators WHERE operator_id = ?", (operator_id,)
                ).fetchone()
                if not row:
                    return None
                op = dict(row)
                op["entities"] = [
                    dict(r) for r in conn.execute(
                        "SELECT * FROM operator_entities WHERE operator_id = ? ORDER BY evidence_count DESC",
                        (operator_id,),
                    ).fetchall()
                ]
                op["evidence"] = [
                    {**dict(r), "details": json.loads(r["details"] or "{}")}
                    for r in conn.execute(
                        "SELECT * FROM operator_evidence WHERE operator_id = ? ORDER BY created_at DESC",
                        (operator_id,),
                    ).fetchall()
                ]
                op["reviews"] = [
                    dict(r) for r in conn.execute(
                        "SELECT * FROM operator_reviews WHERE operator_id = ? ORDER BY timestamp DESC",
                        (operator_id,),
                    ).fetchall()
                ]
                op["promotion_history"] = [
                    {**dict(r), "evidence_snapshot": json.loads(r["evidence_snapshot"])}
                    for r in conn.execute(
                        "SELECT * FROM operator_promotion_reviews "
                        "WHERE canonical_operator_id = ? ORDER BY timestamp DESC",
                        (operator_id,),
                    ).fetchall()
                ]
                return op
        except Exception:
            return None

    def fetch_all_operators(
        self,
        *,
        exclude_rejected: bool = True,
        limit: int = 200,
    ) -> list[dict]:
        try:
            with self._connect() as conn:
                where = "WHERE status != 'REJECTED'" if exclude_rejected else ""
                rows = conn.execute(
                    f"SELECT * FROM operators {where} ORDER BY updated_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
                return [dict(r) for r in rows]
        except Exception:
            return []

    def fetch_by_entity(self, entity_address: str) -> list[dict]:
        """Return all operators that include a given wallet address."""
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT o.*, oe.entity_type, oe.confidence AS entity_confidence,
                           oe.evidence_count
                    FROM operators o
                    JOIN operator_entities oe ON o.operator_id = oe.operator_id
                    WHERE oe.entity_address = ?
                      AND o.status != 'REJECTED'
                    ORDER BY o.updated_at DESC
                    """,
                    (entity_address,),
                ).fetchall()
                return [dict(r) for r in rows]
        except Exception:
            return []

    def fetch_summary(self) -> dict:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT
                        COUNT(*) AS total,
                        SUM(CASE WHEN status='CANDIDATE'   THEN 1 ELSE 0 END) AS candidates,
                        SUM(CASE WHEN status='REVIEW_CANDIDATE' THEN 1 ELSE 0 END) AS review_candidates,
                        SUM(CASE WHEN status='PROVISIONAL' THEN 1 ELSE 0 END) AS provisional,
                        SUM(CASE WHEN status='CONFIRMED'   THEN 1 ELSE 0 END) AS confirmed,
                        SUM(CASE WHEN status='REJECTED'    THEN 1 ELSE 0 END) AS rejected,
                        SUM(CASE WHEN status IN ('MERGE_REVIEW','SPLIT_REVIEW') THEN 1 ELSE 0 END) AS review_pending
                    FROM operators
                    """
                ).fetchone()
                return dict(row) if row else {
                    "total": 0, "candidates": 0, "review_candidates": 0, "provisional": 0,
                    "confirmed": 0, "rejected": 0, "review_pending": 0,
                }
        except Exception:
            return {
                "total": 0, "candidates": 0, "review_candidates": 0,
                "provisional": 0,
                "confirmed": 0, "rejected": 0, "review_pending": 0,
            }

    def entity_count(self, operator_id: str) -> int:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT COUNT(*) FROM operator_entities WHERE operator_id = ?",
                    (operator_id,),
                ).fetchone()
                return row[0] if row else 0
        except Exception:
            return 0
