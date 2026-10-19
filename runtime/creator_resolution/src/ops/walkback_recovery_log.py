"""X76.5A -- Walkback recovery-event log.

Persists every self-kill / recovery event for walkback_worker so Mission
Control can show auditable history, not just current status. Deliberately
a separate, minimal append-only table -- not a reuse of
wt_treasury_review_actions (X76.2) or operator_identity_events (X76.1),
since this records WORKER lifecycle events, not analyst governance
decisions or identity events; conflating them would make either audit
trail harder to reason about.

Distinguishes two event kinds, per this milestone's explicit incident-
labelling requirement:
  - "stale_lease_self_kill"      -- the worker's own guard fired
    (src.core.walkback_worker._check_stuck_lease).
  - "manual_external_termination" -- the process was killed by something
    OUTSIDE the worker's own guard (operator action, OOM killer, a signal
    sent by tooling). The worker itself can never distinguish these at
    the moment of death (a killed process cannot log its own death), so
    this kind is recorded RETROACTIVELY by whatever observes the process
    restarted without record_self_kill() having been called for that gap
    -- see reconcile_unexplained_restarts().
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from typing import Any

_DDL = """
CREATE TABLE IF NOT EXISTS wt_walkback_recovery_events (
    event_id            TEXT PRIMARY KEY,
    worker               TEXT NOT NULL,
    event_kind           TEXT NOT NULL,
    reason                TEXT NOT NULL,
    lease_age_seconds    REAL,
    lease_command         TEXT,
    lease_transaction_id TEXT,
    detected_at           INTEGER NOT NULL,
    restarted_at          INTEGER,
    healthy_at            INTEGER,
    restart_outcome       TEXT,
    notes                 TEXT,
    created_at            INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_wtwre_worker_time
    ON wt_walkback_recovery_events(worker, created_at);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_DDL)


def record_self_kill(
    conn: sqlite3.Connection,
    *,
    worker: str,
    reason: str,
    lease_age_seconds: float | None,
    lease_command: str | None,
    lease_transaction_id: str | None,
    event_kind: str = "stale_lease_self_kill",
    notes: str | None = None,
) -> str:
    """Called by the worker itself, immediately before os._exit(), from
    inside the SAME process that detected the stuck lease -- so this is
    the organic, self-diagnosed case. Any restart NOT preceded by a
    matching row here (see reconcile_unexplained_restarts) is, by
    construction, something else: a manual kill, an OOM kill, a crash."""
    ensure_schema(conn)
    now = int(time.time())
    event_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO wt_walkback_recovery_events "
        "(event_id, worker, event_kind, reason, lease_age_seconds, lease_command, "
        " lease_transaction_id, detected_at, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (event_id, worker, event_kind, reason, lease_age_seconds, lease_command,
         lease_transaction_id, now, now),
    )
    conn.commit()
    return event_id


def record_manual_termination(
    conn: sqlite3.Connection,
    *,
    worker: str,
    reason: str,
    detected_at: int,
    restarted_at: int | None = None,
    healthy_at: int | None = None,
    notes: str | None = None,
) -> str:
    """Explicit, honest recording of a termination NOT caused by the
    worker's own self-kill guard -- e.g. the X76.5 SIGABRT sent during
    debugging. Never call this to relabel a real self-kill; it exists so
    the incident record required by this milestone can be entered
    truthfully rather than either fabricated as an organic recovery or
    silently omitted."""
    ensure_schema(conn)
    now = int(time.time())
    event_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO wt_walkback_recovery_events "
        "(event_id, worker, event_kind, reason, detected_at, restarted_at, "
        " healthy_at, restart_outcome, notes, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (event_id, worker, "manual_external_termination", reason, detected_at,
         restarted_at,
         healthy_at,
         ("restarted successfully" if restarted_at else None),
         notes, now),
    )
    conn.commit()
    return event_id


def mark_restarted(
    conn: sqlite3.Connection, event_id: str, *, restarted_at: int, outcome: str
) -> None:
    ensure_schema(conn)
    conn.execute(
        "UPDATE wt_walkback_recovery_events SET restarted_at=?, restart_outcome=? WHERE event_id=?",
        (restarted_at, outcome, event_id),
    )
    conn.commit()


def mark_healthy(conn: sqlite3.Connection, event_id: str, *, healthy_at: int) -> None:
    ensure_schema(conn)
    conn.execute(
        "UPDATE wt_walkback_recovery_events SET healthy_at=? WHERE event_id=?",
        (healthy_at, event_id),
    )
    conn.commit()


def recent_events(conn: sqlite3.Connection, *, worker: str = "walkback_worker", limit: int = 5) -> list[dict[str, Any]]:
    ensure_schema(conn)
    rows = conn.execute(
        "SELECT event_id, worker, event_kind, reason, lease_age_seconds, lease_command, "
        "lease_transaction_id, detected_at, restarted_at, healthy_at, restart_outcome, notes "
        "FROM wt_walkback_recovery_events WHERE worker=? ORDER BY detected_at DESC LIMIT ?",
        (worker, limit),
    ).fetchall()
    cols = ["event_id", "worker", "event_kind", "reason", "lease_age_seconds", "lease_command",
            "lease_transaction_id", "detected_at", "restarted_at", "healthy_at", "restart_outcome", "notes"]
    return [dict(zip(cols, r)) for r in rows]


def counts_in_window(conn: sqlite3.Connection, *, worker: str = "walkback_worker", window_seconds: int, now: int | None = None) -> dict[str, int]:
    ensure_schema(conn)
    now = int(now or time.time())
    cutoff = now - window_seconds
    rows = conn.execute(
        "SELECT event_kind, COUNT(*) FROM wt_walkback_recovery_events "
        "WHERE worker=? AND detected_at>=? GROUP BY event_kind",
        (worker, cutoff),
    ).fetchall()
    counts = {kind: n for kind, n in rows}
    return {
        "self_kill": counts.get("stale_lease_self_kill", 0),
        "manual_termination": counts.get("manual_external_termination", 0),
        "total": sum(counts.values()),
    }
