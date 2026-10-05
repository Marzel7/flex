"""Exact-event repair for a missing canonical membership outbox projection."""
from __future__ import annotations

import sqlite3

from src.ops.canonical_membership_outbox import TABLE, append_transition

WRITER_IDENTITY = "canonical-outbox-exact-event-reconciler-v1"


def reconcile_event(conn: sqlite3.Connection, *, event_id: str, created_at: int) -> dict[str, object]:
    """Project one already-committed assignment, never scan or create membership."""
    row = conn.execute(
        "SELECT mint,operator_id,assigned_at,event_id FROM operator_launch_membership WHERE event_id=?",
        (str(event_id),),
    ).fetchone()
    if row is None:
        raise ValueError("CANONICAL_ASSIGNMENT_EVENT_NOT_FOUND")
    mint, operator_id, assigned_at, canonical_event_id = row
    if canonical_event_id != event_id or not mint or not operator_id:
        raise ValueError("CANONICAL_ASSIGNMENT_EVENT_MEMBERSHIP_MISMATCH")
    try:
        existing = conn.execute(
            f"SELECT outbox_id FROM {TABLE} WHERE canonical_event_id=? ORDER BY outbox_id ASC",
            (str(event_id),),
        ).fetchone()
    except sqlite3.OperationalError:
        existing = None
    if existing is not None:
        return {"status": "ALREADY_PRESENT", "outbox_id": int(existing[0]), "event_id": str(event_id)}
    outbox_id = append_transition(
        conn, event_type="MEMBERSHIP_ASSIGNED", mint=str(mint), operator_id=str(operator_id),
        canonical_event_id=str(event_id), assigned_at=int(assigned_at), previous_operator_id=None,
        writer_identity=WRITER_IDENTITY, created_at=int(created_at),
    )
    return {"status": "PROJECTED", "outbox_id": outbox_id, "event_id": str(event_id)}
