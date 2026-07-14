"""Read-only access to the canonical operator model."""
from __future__ import annotations

import contextlib
import json
import sqlite3


class OperatorReader:
    """A pure reader: URI read-only, query-only, no DDL or persistent PRAGMAs."""

    def __init__(self, db_path: str) -> None:
        self._path = db_path

    @contextlib.contextmanager
    def _connect(self):
        conn = sqlite3.connect(f"file:{self._path}?mode=ro", uri=True, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        try:
            yield conn
        finally:
            conn.close()

    def fetch_operator(self, operator_id: str) -> dict | None:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM operators WHERE operator_id = ?", (operator_id,)
                ).fetchone()
                if not row:
                    return None
                op = dict(row)
                op["entities"] = [dict(r) for r in conn.execute(
                    "SELECT * FROM operator_entities WHERE operator_id = ? "
                    "ORDER BY evidence_count DESC", (operator_id,)
                ).fetchall()]
                op["evidence"] = [
                    {**dict(r), "details": json.loads(r["details"] or "{}")}
                    for r in conn.execute(
                        "SELECT * FROM operator_evidence WHERE operator_id = ? "
                        "ORDER BY created_at DESC", (operator_id,)
                    ).fetchall()
                ]
                op["reviews"] = [dict(r) for r in conn.execute(
                    "SELECT * FROM operator_reviews WHERE operator_id = ? "
                    "ORDER BY timestamp DESC", (operator_id,)
                ).fetchall()]
                op["promotion_history"] = [
                    {**dict(r), "evidence_snapshot": json.loads(r["evidence_snapshot"])}
                    for r in conn.execute(
                        "SELECT * FROM operator_promotion_reviews "
                        "WHERE canonical_operator_id = ? ORDER BY timestamp DESC",
                        (operator_id,),
                    ).fetchall()
                ]
                return op
        except (sqlite3.Error, OSError, json.JSONDecodeError):
            return None

    def fetch_all_operators(self, *, exclude_rejected: bool = True, limit: int = 200) -> list[dict]:
        try:
            with self._connect() as conn:
                where = "WHERE status != 'REJECTED'" if exclude_rejected else ""
                rows = conn.execute(
                    f"SELECT * FROM operators {where} ORDER BY updated_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
                return [dict(row) for row in rows]
        except (sqlite3.Error, OSError):
            return []

    def fetch_by_entity(self, entity_address: str) -> list[dict]:
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT o.*,oe.entity_type,oe.confidence AS entity_confidence,"
                    "oe.evidence_count FROM operators o JOIN operator_entities oe "
                    "ON o.operator_id=oe.operator_id WHERE oe.entity_address=? "
                    "AND o.status!='REJECTED' ORDER BY o.updated_at DESC",
                    (entity_address,),
                ).fetchall()
                return [dict(row) for row in rows]
        except (sqlite3.Error, OSError):
            return []

    def fetch_summary(self) -> dict:
        empty = {
            "total": 0, "candidates": 0, "review_candidates": 0,
            "provisional": 0, "confirmed": 0, "rejected": 0,
            "review_pending": 0,
        }
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT COUNT(*) AS total,"
                    "SUM(CASE WHEN status='CANDIDATE' THEN 1 ELSE 0 END) AS candidates,"
                    "SUM(CASE WHEN status='REVIEW_CANDIDATE' THEN 1 ELSE 0 END) AS review_candidates,"
                    "SUM(CASE WHEN status='PROVISIONAL' THEN 1 ELSE 0 END) AS provisional,"
                    "SUM(CASE WHEN status='CONFIRMED' THEN 1 ELSE 0 END) AS confirmed,"
                    "SUM(CASE WHEN status='REJECTED' THEN 1 ELSE 0 END) AS rejected,"
                    "SUM(CASE WHEN status IN ('MERGE_REVIEW','SPLIT_REVIEW') THEN 1 ELSE 0 END) AS review_pending "
                    "FROM operators"
                ).fetchone()
                if not row:
                    return empty
                return {key: (value or 0) for key, value in dict(row).items()}
        except (sqlite3.Error, OSError):
            return empty

