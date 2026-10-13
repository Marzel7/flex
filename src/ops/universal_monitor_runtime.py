"""Dispatch-disabled universal Monitor persistence and scheduler bridge for DEV.

This intentionally owns neither a provider client nor terminal rules.  It
persists operation-neutral work only; an explicitly enabled future dispatcher
may consume that work after the global admission gate accepts it.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from typing import Any

from src.ops.universal_incremental_window import CADENCE_VERSION, next_checkpoint_at
from src.ops.universal_monitor_adoption import global_budget_work, persist_adoption, plan_adoption

RUNTIME_VERSION = "UNIVERSAL_MONITOR_RUNTIME_BRIDGE_V1"
OWNER_LEGACY, OWNER_UNIVERSAL, OWNER_TERMINAL = "LEGACY", "UNIVERSAL", "TERMINAL"
GLOBAL_BIRDEYE_HOURLY_CAP, GLOBAL_BIRDEYE_DAILY_CAP, NORMAL_DISPATCH_RPS = 120, 2400, 1


def _identity(value: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def ensure_runtime_schema(conn: sqlite3.Connection) -> None:
    """Install bounded, idempotent DEV tables; never alter existing monitor data."""
    conn.execute("PRAGMA max_page_count=16384")  # 64 MiB at normal 4 KiB pages; fail closed before 500 MB.
    persist_adoption(conn, plan_adoption(operation_id="_schema", mint="_schema", assignment={"id": "schema"}, entry=None, checkpoints=[], now=0))
    conn.execute("DELETE FROM universal_monitor_adoption_work WHERE adoption_id=(SELECT adoption_id FROM universal_monitor_adoptions WHERE operation_id='_schema' AND mint='_schema')")
    conn.execute("DELETE FROM universal_monitor_adoptions WHERE operation_id='_schema' AND mint='_schema'")
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS universal_monitor_policies (
      policy_version TEXT PRIMARY KEY, policy_json TEXT NOT NULL, registered_at INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS universal_monitor_ownership (
      operation_id TEXT NOT NULL, mint TEXT NOT NULL, owner TEXT NOT NULL,
      adoption_id TEXT, updated_at INTEGER NOT NULL, PRIMARY KEY(operation_id,mint));
    CREATE TABLE IF NOT EXISTS universal_monitor_global_work (
      work_id TEXT PRIMARY KEY, adoption_id TEXT NOT NULL, operation_id TEXT NOT NULL, mint TEXT NOT NULL,
      work_type TEXT NOT NULL, resume_from INTEGER, due_at INTEGER NOT NULL, checkpoint_boundary INTEGER,
      cadence_version TEXT NOT NULL, provider_class TEXT NOT NULL, state TEXT NOT NULL, created_at INTEGER NOT NULL,
      UNIQUE(adoption_id,work_type));
    CREATE TABLE IF NOT EXISTS universal_monitor_checkpoint_state (
      operation_id TEXT NOT NULL, mint TEXT NOT NULL, last_successful_coverage_boundary INTEGER,
      requested_boundary INTEGER, proven_coverage_boundary INTEGER, cadence_version TEXT NOT NULL,
      updated_at INTEGER NOT NULL, PRIMARY KEY(operation_id,mint));
    CREATE TABLE IF NOT EXISTS universal_monitor_budget_state (
      provider TEXT PRIMARY KEY, hour_cap INTEGER NOT NULL, day_cap INTEGER NOT NULL, normal_rps INTEGER NOT NULL,
      dispatch_enabled INTEGER NOT NULL, updated_at INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS universal_monitor_forward_state (
      singleton INTEGER PRIMARY KEY CHECK(singleton=1), enabled_at INTEGER NOT NULL,
      scheduler_enabled INTEGER NOT NULL, updated_at INTEGER NOT NULL);
    CREATE TRIGGER IF NOT EXISTS universal_monitor_adoptions_capacity
    BEFORE INSERT ON universal_monitor_adoptions
    WHEN (SELECT COUNT(*) FROM universal_monitor_adoptions) >= 10000
    BEGIN SELECT RAISE(ABORT, 'UNIVERSAL_MONITOR_ADOPTION_CAPACITY_EXHAUSTED'); END;
    CREATE TRIGGER IF NOT EXISTS universal_monitor_work_capacity
    BEFORE INSERT ON universal_monitor_global_work
    WHEN (SELECT COUNT(*) FROM universal_monitor_global_work) >= 10000
    BEGIN SELECT RAISE(ABORT, 'UNIVERSAL_MONITOR_WORK_CAPACITY_EXHAUSTED'); END;
    """)
    policy = {"entry": "ASAP", "checkpoints": [["0-2h", "15m"], ["2-6h", "30m"], ["6-12h", "1h"], ["12-24h", "2h"]]}
    now = int(time.time())
    conn.execute("INSERT OR IGNORE INTO universal_monitor_policies VALUES(?,?,?)", (CADENCE_VERSION, json.dumps(policy, separators=(",", ":")), now))
    conn.execute("INSERT OR IGNORE INTO universal_monitor_budget_state VALUES(?,?,?,?,?,?)", ("BIRDEYE", GLOBAL_BIRDEYE_HOURLY_CAP, GLOBAL_BIRDEYE_DAILY_CAP, NORMAL_DISPATCH_RPS, 0, now))
    conn.commit()


def enable_forward_monitoring(conn: sqlite3.Connection, *, now: int) -> dict[str, int]:
    """Enable only prospective enrolment; this never scans historical rows."""
    ensure_runtime_schema(conn)
    with conn:
        conn.execute("INSERT OR IGNORE INTO universal_monitor_forward_state VALUES(1,?,?,?)", (int(now), 1, int(now)))
        conn.execute("UPDATE universal_monitor_budget_state SET dispatch_enabled=1,updated_at=? WHERE provider='BIRDEYE'", (int(now),))
    row = conn.execute("SELECT enabled_at,scheduler_enabled FROM universal_monitor_forward_state WHERE singleton=1").fetchone()
    return {"enabled_at": int(row[0]), "scheduler_enabled": int(row[1])}


def enrol_qualified_forward_entry(conn: sqlite3.Connection, *, operation_id: str, mint: str,
                                  assignment: dict[str, Any], entry: dict[str, Any], now: int) -> dict[str, Any]:
    """Post-Entry universal handoff.  The Entry producer remains authoritative."""
    state = conn.execute("SELECT enabled_at,scheduler_enabled FROM universal_monitor_forward_state WHERE singleton=1").fetchone()
    assigned_at = int(assignment.get("assigned_at") or 0)
    if not state or not int(state[1]):
        return {"status": "FORWARD_MONITOR_DISABLED"}
    if assigned_at < int(state[0]):
        return {"status": "HISTORICAL_ASSIGNMENT_EXCLUDED"}
    if not entry.get("qualified") or entry.get("timestamp") is None or entry.get("mc_usd") is None:
        return {"status": "ENTRY_REQUIRED"}
    result = adopt_into_universal(conn, operation_id=operation_id, mint=mint, assignment=assignment,
                                  entry=entry, checkpoints=[], now=now)
    return {"status": "UNIVERSAL_ENROLLED", **result}


def adopt_into_universal(conn: sqlite3.Connection, *, operation_id: str, mint: str, assignment: dict[str, Any],
                         entry: dict[str, Any] | None, checkpoints: list[dict[str, Any]], now: int) -> dict[str, Any]:
    """Explicit bridge entrypoint.  It does not call a provider or dispatch work."""
    ensure_runtime_schema(conn)
    snapshot = plan_adoption(operation_id=operation_id, mint=mint, assignment=assignment, entry=entry, checkpoints=checkpoints, now=now)
    persist_adoption(conn, snapshot)
    handoff = global_budget_work(snapshot, due_at=now)
    checkpoint_boundary = None
    if snapshot.get("resume_from") is not None and entry and entry.get("timestamp") is not None:
        age = max(0, int(now) - int(entry["timestamp"]))
        checkpoint_boundary = next_checkpoint_at(entry_timestamp=int(entry["timestamp"]), age_seconds=age)
    # Policy C ends at 24h.  A retained Entry older than that remains owned
    # and auditable, but cannot be silently turned into a catch-up call.
    if handoff and handoff["work_type"] in {"ACQUIRE_PRICE_WINDOW", "RESUME_PRICE_WINDOW"} and checkpoint_boundary is None:
        handoff = None
    with conn:
        conn.execute("INSERT OR REPLACE INTO universal_monitor_ownership VALUES(?,?,?,?,?)", (operation_id, mint, OWNER_UNIVERSAL, snapshot["adoption_id"], now))
        if handoff:
            conn.execute("INSERT OR IGNORE INTO universal_monitor_global_work VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (
                handoff["work_id"], snapshot["adoption_id"], operation_id, mint, handoff["work_type"], snapshot.get("resume_from"), int(checkpoint_boundary or now),
                checkpoint_boundary, CADENCE_VERSION, handoff["provider_class"], "PENDING", now))
            conn.execute("INSERT OR REPLACE INTO universal_monitor_checkpoint_state VALUES(?,?,?,?,?,?,?)", (
                operation_id, mint, snapshot.get("resume_from"), checkpoint_boundary, snapshot.get("resume_from"), CADENCE_VERSION, now))
    return {"snapshot": snapshot, "work": handoff, "owner": OWNER_UNIVERSAL, "dispatch_enabled": False}


def dispatch_eligibility(conn: sqlite3.Connection, *, work_id: str, now: int | None = None) -> dict[str, Any]:
    """Provider-free price-window selection boundary; non-live work fails closed.

    A universal ownership/adoption record is not live authority.  Price-window
    dispatch additionally requires the retained monitor fact that drives the
    LIVE projection.  This deliberately leaves Entry acquisition outside the
    lifecycle dispatcher rather than suppressing a legitimate Opening path.
    """
    now = int(time.time()) if now is None else int(now)
    row = conn.execute("SELECT w.operation_id,w.mint,w.work_type,w.state,o.owner,b.dispatch_enabled FROM universal_monitor_global_work w JOIN universal_monitor_ownership o USING(operation_id,mint) JOIN universal_monitor_budget_state b ON b.provider='BIRDEYE' WHERE w.work_id=?", (work_id,)).fetchone()
    if not row:
        return {"eligible": False, "reason": "WORK_NOT_FOUND"}
    if int(row[5]) != 1:
        return {"eligible": False, "reason": "DISPATCH_DISABLED"}
    if row[4] != OWNER_UNIVERSAL:
        return {"eligible": False, "reason": "OWNERSHIP_GUARD"}
    if row[3] != "PENDING":
        return {"eligible": False, "reason": "NOT_PENDING"}
    if row[2] not in {"ACQUIRE_PRICE_WINDOW", "RESUME_PRICE_WINDOW"}:
        return {"eligible": True, "reason": "NON_LIFECYCLE_WORK"}
    has_facts = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='operation_monitor_facts'").fetchone()
    if not has_facts:
        return {"eligible": False, "reason": "LIVE_FACT_UNAVAILABLE"}
    fact = conn.execute("SELECT entry_status,entry_timestamp,entry_mc_usd,monitor_state,monitor_completed_at FROM operation_monitor_facts WHERE operation_id=? AND mint=?", (row[0], row[1])).fetchone()
    if not fact:
        return {"eligible": False, "reason": "LIVE_FACT_UNAVAILABLE"}
    entry_status, entry_timestamp, entry_mc_usd, monitor_state, monitor_completed_at = fact
    live = (
        str(entry_status or "") == "QUALIFIED"
        and entry_timestamp is not None
        and entry_mc_usd is not None
        and int(now) < int(entry_timestamp) + 24 * 60 * 60
        and str(monitor_state or "") == "MONITORING_ACTIVE"
        and monitor_completed_at is None
    )
    return {"eligible": live, "reason": "AUTHORITATIVE_LIVE" if live else "NONLIVE_LIFECYCLE_GUARD"}


def ownership_allows_legacy(conn: sqlite3.Connection, *, operation_id: str, mint: str) -> bool:
    row = conn.execute("SELECT owner FROM universal_monitor_ownership WHERE operation_id=? AND mint=?", (operation_id, mint)).fetchone()
    return row is None or row[0] == OWNER_LEGACY
