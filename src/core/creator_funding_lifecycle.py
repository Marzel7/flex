"""Fail-open, append-only telemetry for Creator Funding queue lifecycle.

The queue remains authoritative.  This ledger is deliberately secondary: an
event-write failure returns ``False`` and must be recorded as a measurement
coverage gap, never allowed to interrupt queue processing.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from typing import Optional


def work_class(source: Optional[str]) -> str:
    value = (source or "").lower()
    if value.startswith(("pf_ws_", "creator_discovery", "migration_already_known", "mark_token_migrated")):
        return "LIVE"
    if value.startswith(("creator_resolution", "crq_worker", "approval_queue")):
        return "RECOVERY"
    if "coverage_sweep" in value or "backfill" in value:
        return "BACKFILL"
    return "OTHER_PROVEN_SOURCE"


def obligation_id(creator: str, mint: str) -> str:
    return hashlib.sha256(f"creator-funding-v1\0{creator}\0{mint}".encode()).hexdigest()


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS creator_funding_lifecycle_events (
        event_id TEXT PRIMARY KEY, occurred_at INTEGER NOT NULL,
        obligation_id TEXT NOT NULL, creator_address TEXT NOT NULL, mint TEXT NOT NULL,
        work_class TEXT NOT NULL, source TEXT, lifecycle_event TEXT NOT NULL,
        attempt INTEGER, previous_status TEXT, new_status TEXT, correlation_id TEXT
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cfq_lifecycle_time ON creator_funding_lifecycle_events(occurred_at, work_class)")
    conn.execute("""CREATE TABLE IF NOT EXISTS creator_funding_lifecycle_gaps (
        gap_id TEXT PRIMARY KEY, occurred_at INTEGER NOT NULL,
        obligation_id TEXT NOT NULL, creator_address TEXT NOT NULL, mint TEXT NOT NULL,
        lifecycle_event TEXT NOT NULL, error_class TEXT NOT NULL, detail TEXT
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cfq_lifecycle_gaps_time ON creator_funding_lifecycle_gaps(occurred_at)")
    conn.execute("""CREATE TABLE IF NOT EXISTS creator_funding_qualification_snapshots (
        snapshot_id TEXT PRIMARY KEY, captured_at INTEGER NOT NULL,
        event_high_water INTEGER NOT NULL, queue_high_water INTEGER NOT NULL,
        label TEXT NOT NULL, configuration_json TEXT NOT NULL, state_json TEXT NOT NULL
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cfq_qualification_snapshots_time ON creator_funding_qualification_snapshots(captured_at)")


def initialize_schema(db_path: str) -> None:
    """Initialize lifecycle telemetry once before concurrent worker activity."""
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        ensure_schema(conn)
        conn.commit()
    finally:
        conn.close()


def capture_qualification_snapshot(
    db_path: str, *, label: str, configuration: dict[str, object] | None = None,
    captured_at: int | None = None,
) -> dict[str, object]:
    """Capture an auditable queue/ledger boundary without taking a write lease.

    Queue state and the event high-water are read in one SQLite read snapshot.
    The resulting immutable record is then appended in a separate, short write;
    the persisted record describes the earlier read boundary, never the later
    append time.  Qualification consumes event rowids strictly after the start
    boundary and through the end boundary.
    """
    timestamp = int(time.time()) if captured_at is None else int(captured_at)
    cfg = configuration or {}
    read = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
    try:
        read.row_factory = sqlite3.Row
        read.execute("BEGIN")
        event_high_water = int(read.execute(
            "SELECT COALESCE(MAX(rowid), 0) FROM creator_funding_lifecycle_events"
        ).fetchone()[0])
        queue_high_water = int(read.execute(
            "SELECT COALESCE(MAX(rowid), 0) FROM creator_funding_queue"
        ).fetchone()[0])
        rows = read.execute(
            """SELECT creator_address, mint, source, status
                 FROM creator_funding_queue
                 WHERE status IN ('pending','retry','running')
                 ORDER BY creator_address, mint"""
        ).fetchall()
        state: dict[str, list[str]] = {}
        for row in rows:
            cls = work_class(row["source"])
            state.setdefault(cls, []).append(obligation_id(row["creator_address"], row["mint"]))
        read.commit()
    finally:
        read.close()
    canonical_state = {key: sorted(value) for key, value in sorted(state.items())}
    payload = {
        "label": label, "captured_at": timestamp,
        "event_high_water": event_high_water, "queue_high_water": queue_high_water,
        "actionable_obligation_ids_by_class": canonical_state,
        "actionable_counts_by_class": {key: len(value) for key, value in canonical_state.items()},
        "configuration": cfg,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    snapshot_id = hashlib.sha256(encoded.encode()).hexdigest()
    write = sqlite3.connect(db_path, timeout=1)
    try:
        ensure_schema(write)
        write.execute(
            """INSERT OR IGNORE INTO creator_funding_qualification_snapshots
               (snapshot_id, captured_at, event_high_water, queue_high_water,
                label, configuration_json, state_json)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (snapshot_id, timestamp, event_high_water, queue_high_water, label,
             json.dumps(cfg, sort_keys=True), json.dumps(payload, sort_keys=True)),
        )
        write.commit()
    finally:
        write.close()
    return {"snapshot_id": snapshot_id, **payload}


def _record_gap_best_effort(
    db_path: str, *, creator: str, mint: str, event: str, occurred_at: int, error: Exception,
    schema_ready: bool = False,
) -> None:
    """Persist a telemetry-write failure when SQLite is available again.

    This is intentionally a second, short best-effort write: queue mutation is
    already committed and must never wait for observability.  If this too is
    unavailable, callers still receive ``False`` and the clean-window gate
    remains ineligible rather than silently treating the interval as covered.
    """
    logical_id = obligation_id(creator, mint)
    error_class = type(error).__name__
    detail = str(error)[:500]
    gap_id = hashlib.sha256(
        f"gap-v1\0{logical_id}\0{event}\0{occurred_at}\0{error_class}\0{detail}".encode()
    ).hexdigest()
    conn = None
    try:
        conn = sqlite3.connect(db_path, timeout=0.2)
        if not schema_ready:
            ensure_schema(conn)
        conn.execute(
            """INSERT OR IGNORE INTO creator_funding_lifecycle_gaps
               (gap_id, occurred_at, obligation_id, creator_address, mint,
                lifecycle_event, error_class, detail)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (gap_id, occurred_at, logical_id, creator, mint, event, error_class, detail),
        )
        conn.commit()
    except Exception:
        pass
    finally:
        if conn is not None:
            conn.close()


def record_event_fail_open(
    db_path: str, *, creator: str, mint: str, source: Optional[str], event: str,
    occurred_at: Optional[int] = None, attempt: Optional[int] = None,
    previous_status: Optional[str] = None, new_status: Optional[str] = None,
    correlation_id: Optional[str] = None,
    schema_ready: bool = False,
) -> bool:
    """Append one idempotent event after the authoritative mutation committed."""
    timestamp = int(time.time()) if occurred_at is None else int(occurred_at)
    logical_id = obligation_id(creator, mint)
    key = "\0".join(str(x or "") for x in (logical_id, event, timestamp, attempt, previous_status, new_status, source, correlation_id))
    event_id = hashlib.sha256(key.encode()).hexdigest()
    conn = None
    try:
        conn = sqlite3.connect(db_path, timeout=1)
        if not schema_ready:
            ensure_schema(conn)
        conn.execute(
            """INSERT OR IGNORE INTO creator_funding_lifecycle_events
               (event_id, occurred_at, obligation_id, creator_address, mint,
                work_class, source, lifecycle_event, attempt, previous_status,
                new_status, correlation_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (event_id, timestamp, logical_id, creator, mint, work_class(source), source,
             event, attempt, previous_status, new_status, correlation_id),
        )
        conn.commit()
        return True
    except Exception as exc:
        _record_gap_best_effort(
            db_path, creator=creator, mint=mint, event=event,
            occurred_at=timestamp, error=exc, schema_ready=schema_ready,
        )
        return False
    finally:
        if conn is not None:
            conn.close()
