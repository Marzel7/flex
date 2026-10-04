"""Durable, operation-neutral LIVE -> HISTORY projection for universal monitoring.

The projector stores only a compact completion snapshot.  It does not acquire
prices, mutate Opening facts, or depend on an operation-specific lifecycle.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

LIFECYCLE_STATES = frozenset({"OPENING_PENDING", "MONITORING_ACTIVE", "TERMINAL", "24H_COMPLETE", "RIGHT_CENSORED", "HISTORY"})
LIFECYCLE_WORK = ("ACQUIRE_PRICE_WINDOW", "RESUME_PRICE_WINDOW")


def _identity(operation_id: str, mint: str, opening_timestamp: int, adoption_id: str) -> str:
    payload = {"operation_id": operation_id, "mint": mint, "opening_timestamp": int(opening_timestamp), "adoption_id": adoption_id}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def ensure_live_history_schema(conn: sqlite3.Connection) -> None:
    """Install bounded completion/non-live facts without deleting monitor evidence."""
    conn.execute("PRAGMA max_page_count=16384")  # 64 MiB max SQLite file, below the project 500 MB rule.
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS universal_monitor_lifecycle_completion (
      completion_id TEXT PRIMARY KEY, operation_id TEXT NOT NULL, mint TEXT NOT NULL,
      adoption_id TEXT NOT NULL, opening_timestamp INTEGER NOT NULL, completion_state TEXT NOT NULL,
      completed_at INTEGER NOT NULL, opening_mc_usd REAL, peak_mc_usd REAL,
      peak_timestamp INTEGER, max_multiple REAL, time_to_peak_seconds INTEGER,
      peak_scope TEXT NOT NULL, source_work_state TEXT NOT NULL,
      UNIQUE(operation_id,mint,opening_timestamp,adoption_id));
    CREATE TABLE IF NOT EXISTS universal_monitor_nonlive_projection (
      operation_id TEXT NOT NULL, mint TEXT NOT NULL, assignment_identity TEXT NOT NULL,
      state TEXT NOT NULL, recorded_at INTEGER NOT NULL, detail TEXT NOT NULL,
      PRIMARY KEY(operation_id,mint,assignment_identity));
    """)
    conn.commit()


def record_nonlive_opening(conn: sqlite3.Connection, *, operation_id: str, mint: str,
                           assignment_identity: str, state: str, now: int, detail: str) -> None:
    """Keep failed/missing Openings represented without fabricating LIVE state."""
    ensure_live_history_schema(conn)
    if state not in {"OPENING_PENDING", "RIGHT_CENSORED"}:
        raise ValueError("non-live state must be OPENING_PENDING or RIGHT_CENSORED")
    with conn:
        conn.execute("INSERT OR IGNORE INTO universal_monitor_nonlive_projection VALUES(?,?,?,?,?,?)",
                     (operation_id, mint, assignment_identity, state, int(now), detail))


def _latest_financials(conn: sqlite3.Connection, operation_id: str, mint: str, opening_mc: float | None) -> tuple[Any, ...]:
    row = conn.execute("""SELECT running_peak_mc_usd,running_peak_timestamp,max_multiple,time_to_peak_seconds
                          FROM universal_monitor_price_facts WHERE operation_id=? AND mint=?
                          ORDER BY checkpoint_boundary DESC LIMIT 1""", (operation_id, mint)).fetchone()
    if not row:
        return (None, None, None, None, "UNAVAILABLE")
    peak, stamp, multiple, elapsed = row
    return (peak, stamp, multiple, elapsed, "RUNNING_PEAK_QUALIFIED_SCOPE")


def reconcile_live_to_history(conn: sqlite3.Connection, *, now: int) -> list[dict[str, Any]]:
    """Idempotently project terminal or horizon-complete universal tokens into HISTORY.

    Tables populated by the universal runtime are read as the authority.  All
    lifecycle work becomes terminal/ineligible after the completion commit.
    """
    ensure_live_history_schema(conn)
    rows = conn.execute("""SELECT a.operation_id,a.mint,a.adoption_id,a.snapshot_json,
                                 COALESCE(MAX(w.state),'PENDING')
                          FROM universal_monitor_adoptions a
                          LEFT JOIN universal_monitor_global_work w
                            ON w.adoption_id=a.adoption_id AND w.work_type IN ('ACQUIRE_PRICE_WINDOW','RESUME_PRICE_WINDOW')
                          GROUP BY a.operation_id,a.mint,a.adoption_id,a.snapshot_json""").fetchall()
    completed = []
    for operation_id, mint, adoption_id, raw_snapshot, work_state in rows:
        snapshot = json.loads(raw_snapshot)
        entry = snapshot.get("entry") or {}
        if not entry.get("qualified") or entry.get("timestamp") is None:
            continue
        opening_timestamp, opening_mc = int(entry["timestamp"]), entry.get("mc_usd")
        terminal = work_state == "TERMINAL" or bool(snapshot.get("terminal"))
        horizon = int(now) >= opening_timestamp + 24 * 60 * 60
        if not terminal and not horizon:
            continue
        state = "TERMINAL" if terminal else "24H_COMPLETE"
        completion_id = _identity(operation_id, mint, opening_timestamp, adoption_id)
        if conn.execute("SELECT 1 FROM universal_monitor_lifecycle_completion WHERE completion_id=?", (completion_id,)).fetchone():
            continue
        peak, peak_timestamp, multiple, elapsed, peak_scope = _latest_financials(conn, operation_id, mint, opening_mc)
        with conn:
            conn.execute("""INSERT OR IGNORE INTO universal_monitor_lifecycle_completion
                         VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                         (completion_id, operation_id, mint, adoption_id, opening_timestamp, state, int(now), opening_mc,
                          peak, peak_timestamp, multiple, elapsed, peak_scope, work_state))
            conn.execute("""UPDATE universal_monitor_global_work SET state='TERMINAL', due_at=?
                         WHERE adoption_id=? AND work_type IN ('ACQUIRE_PRICE_WINDOW','RESUME_PRICE_WINDOW')
                         AND state IN ('PENDING','CLAIMED','FACT_COMMITTED')""", (int(now), adoption_id))
        completed.append({"completion_id": completion_id, "operation_id": operation_id, "mint": mint, "state": state})
    return completed


def projection_state(conn: sqlite3.Connection, *, operation_id: str, mint: str) -> str | None:
    """Mutually exclusive UI read contract: HISTORY wins, otherwise LIVE/non-live."""
    ensure_live_history_schema(conn)
    if conn.execute("SELECT 1 FROM universal_monitor_lifecycle_completion WHERE operation_id=? AND mint=?", (operation_id, mint)).fetchone():
        return "HISTORY"
    if conn.execute("SELECT 1 FROM universal_monitor_nonlive_projection WHERE operation_id=? AND mint=?", (operation_id, mint)).fetchone():
        return "OPENING_PENDING"
    return "LIVE"
