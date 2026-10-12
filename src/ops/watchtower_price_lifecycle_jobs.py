"""Disabled-by-default durable Watchtower lifecycle job projection.

The projection is deliberately post-commit and provider-free.  The existing
MonitorWorker remains the sole opening/peak transport owner; DEV020 remains the
sole bounded historical pre-peak owner when a separately authorized runner is
enabled.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from typing import Any

SCHEMA = "watchtower-price-lifecycle-job.v1"
TERMINAL = frozenset({"COMPLETE", "INSUFFICIENT_EVIDENCE", "OUTCOME_UNKNOWN"})


def enabled(env: dict[str, str] | None = None) -> bool:
    values = os.environ if env is None else env
    return values.get("WATCHTOWER_PRICE_LIFECYCLE_ENABLED", "false").lower() in {"1", "true", "yes", "on"}


def _identity(assignment: dict[str, Any]) -> str:
    canonical = json.dumps({"event_id": assignment.get("event_id"), "mint": assignment.get("mint"), "assigned_at": assignment.get("assigned_at")}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def ensure_schema(conn: sqlite3.Connection) -> None:
    # 64 MiB cap: durable compact state only; no candles/raw responses.
    conn.execute("PRAGMA max_page_count=16384")
    conn.execute("""CREATE TABLE IF NOT EXISTS watchtower_price_lifecycle_jobs(
        assignment_identity TEXT PRIMARY KEY,mint TEXT NOT NULL,state TEXT NOT NULL,
        opening_json TEXT,peak_json TEXT,prepeak_json TEXT,postpeak_json TEXT,
        created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL)""")


def enqueue_after_assignment_commit(conn: sqlite3.Connection, assignment: dict[str, Any], *, assignment_committed: bool, now: int | None = None) -> dict[str, Any]:
    """Create exactly one compact job only after the authoritative commit."""
    if not assignment_committed or assignment.get("operation_id") != "watchtower":
        return {"status": "NOT_ENQUEUED"}
    mint = str(assignment.get("mint") or "")
    if not mint:
        return {"status": "INVALID_ASSIGNMENT"}
    ensure_schema(conn); moment = int(time.time() if now is None else now); identity = _identity(assignment)
    with conn:
        before = conn.execute("SELECT 1 FROM watchtower_price_lifecycle_jobs WHERE assignment_identity=?", (identity,)).fetchone()
        conn.execute("INSERT OR IGNORE INTO watchtower_price_lifecycle_jobs(assignment_identity,mint,state,created_at,updated_at) VALUES(?,?,?, ?,?)", (identity,mint,"WAITING_FOR_OPENING",moment,moment))
    return {"status": "ALREADY_ENQUEUED" if before else "ENQUEUED", "assignment_identity": identity}


def advance_from_retained_facts(conn: sqlite3.Connection, assignment_identity: str, facts: dict[str, Any], *, now: int | None = None) -> dict[str, Any]:
    """Advance from detached retained facts; intentionally makes no provider call."""
    ensure_schema(conn); row = conn.execute("SELECT state FROM watchtower_price_lifecycle_jobs WHERE assignment_identity=?", (assignment_identity,)).fetchone()
    if not row: return {"status": "JOB_NOT_FOUND"}
    if row[0] in TERMINAL: return {"status": "TERMINAL"}
    opening = facts.get("opening")
    if not isinstance(opening, dict) or not opening.get("qualified"):
        return {"status": "WAITING_FOR_OPENING"}
    peak = facts.get("observed_peak")
    if not isinstance(peak, dict) or peak.get("market_cap") is None or peak.get("timestamp") is None:
        with conn: conn.execute("UPDATE watchtower_price_lifecycle_jobs SET state=?,opening_json=?,updated_at=? WHERE assignment_identity=?", ("WAITING_FOR_OBSERVED_PEAK",json.dumps(opening,sort_keys=True),int(now or time.time()),assignment_identity))
        return {"status": "WAITING_FOR_OBSERVED_PEAK"}
    # Pre-peak stays pending until a separately enabled, bounded owner supplies
    # compact normalized metrics.  This worker never constructs a transport.
    stage = "WAITING_FOR_PREPEAK_OBSERVATION" if facts.get("prepeak") is None else "COMPLETE"
    with conn:
        conn.execute("UPDATE watchtower_price_lifecycle_jobs SET state=?,opening_json=?,peak_json=?,prepeak_json=?,postpeak_json=?,updated_at=? WHERE assignment_identity=?", (stage,json.dumps(opening,sort_keys=True),json.dumps(peak,sort_keys=True),json.dumps(facts.get("prepeak"),sort_keys=True) if facts.get("prepeak") is not None else None,json.dumps(facts.get("postpeak"),sort_keys=True) if facts.get("postpeak") is not None else None,int(now or time.time()),assignment_identity))
    return {"status": stage}


def reconcile_committed_assignments(db_path: str, *, now: int | None = None) -> int:
    """Project committed Watchtower memberships when explicitly enabled.

    This runs after the existing assignment reconciliation and only writes the
    compact job projection.  It neither opens credentials nor calls providers.
    """
    if not enabled(): return 0
    conn = sqlite3.connect(db_path); conn.row_factory = sqlite3.Row
    try:
        ensure_schema(conn)
        rows = conn.execute("SELECT mint,event_id,assigned_at FROM operator_launch_membership WHERE operator_id='watchtower' ORDER BY assigned_at").fetchall()
        created = 0
        for row in rows:
            result = enqueue_after_assignment_commit(conn, {"operation_id":"watchtower", **dict(row)}, assignment_committed=True, now=now)
            created += result["status"] == "ENQUEUED"
        return created
    finally:
        conn.close()
