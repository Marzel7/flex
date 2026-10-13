"""Dormant, provider-free adoption of existing operation monitor facts."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from typing import Any

POLICY = "UNIVERSAL_EXISTING_LIVE_ADOPTION_V1"
ADOPTION_STATES = frozenset({"ENTRY_REQUIRED", "ENTRY_QUALIFIED_LIFECYCLE_REQUIRED", "LIFECYCLE_RESUME_REQUIRED", "RIGHT_CENSORED_RESUME_REQUIRED", "TERMINAL_ALREADY_COMPLETE", "AMBIGUOUS_PROVIDER_CALL", "INVALID_RETAINED_BOUNDARY"})
NEXT_WORK_TYPES = frozenset({"ACQUIRE_ENTRY", "ACQUIRE_PRICE_WINDOW", "RESUME_PRICE_WINDOW", "RECONCILE_AMBIGUOUS_PROVIDER_CALL", "NONE_TERMINAL", "FAIL_CLOSED_INVALID_BOUNDARY"})


def _identity(value: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def ensure_adoption_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS universal_monitor_adoptions (
      adoption_id TEXT PRIMARY KEY, operation_id TEXT NOT NULL, mint TEXT NOT NULL,
      policy_version TEXT NOT NULL, snapshot_json TEXT NOT NULL, adopted_at INTEGER NOT NULL,
      UNIQUE(operation_id,mint,policy_version))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS universal_monitor_adoption_work (
      work_id TEXT PRIMARY KEY, adoption_id TEXT NOT NULL, work_type TEXT NOT NULL,
      resume_from INTEGER, state TEXT NOT NULL DEFAULT 'PENDING',
      UNIQUE(adoption_id,work_type))""")


def plan_adoption(*, operation_id: str, mint: str, assignment: dict[str, Any], entry: dict[str, Any] | None,
                  checkpoints: list[dict[str, Any]], lifecycle_state: str | None = None,
                  terminal: bool = False, now: int | None = None) -> dict[str, Any]:
    """Return a fail-closed adoption plan; never invokes a provider or clock-based cursor."""
    stamp = int(time.time() if now is None else now)
    qualified_entry = bool(entry and entry.get("qualified") and entry.get("timestamp") is not None)
    if terminal:
        state, work, resume = "TERMINAL_ALREADY_COMPLETE", "NONE_TERMINAL", None
    elif lifecycle_state == "AMBIGUOUS_PROVIDER_CALL":
        state, work, resume = "AMBIGUOUS_PROVIDER_CALL", "RECONCILE_AMBIGUOUS_PROVIDER_CALL", None
    elif lifecycle_state == "RIGHT_CENSORED":
        state, work, resume = "RIGHT_CENSORED_RESUME_REQUIRED", "RESUME_PRICE_WINDOW", int(entry["timestamp"]) if qualified_entry else None
    elif not qualified_entry:
        state, work, resume = "ENTRY_REQUIRED", "ACQUIRE_ENTRY", None
    else:
        ordered = sorted(checkpoints, key=lambda x: int(x.get("start", 0)))
        boundary = int(entry["timestamp"])
        gap = False
        for checkpoint in ordered:
            start, end = checkpoint.get("start"), checkpoint.get("end")
            if not checkpoint.get("qualified") or start is None or end is None or int(end) < int(start):
                gap = True; break
            if int(start) != boundary:
                gap = True; break
            boundary = int(end)
        if boundary > stamp + 300 or boundary < int(entry["timestamp"]):
            state, work, resume = "INVALID_RETAINED_BOUNDARY", "FAIL_CLOSED_INVALID_BOUNDARY", None
        else:
            state, work, resume = ("LIFECYCLE_RESUME_REQUIRED" if gap else "ENTRY_QUALIFIED_LIFECYCLE_REQUIRED"), ("RESUME_PRICE_WINDOW" if gap else "ACQUIRE_PRICE_WINDOW"), boundary
    snapshot = {"operation_id": operation_id, "mint": mint, "policy_version": POLICY, "assignment": assignment,
                "entry": entry if qualified_entry else None, "entry_qualification": "QUALIFIED" if qualified_entry else "MISSING",
                "checkpoint_count": len(checkpoints), "lifecycle_state": lifecycle_state, "terminal": terminal,
                "adoption_state": state, "resume_from": resume, "next_work_type": work, "adopted_at": stamp}
    snapshot["adoption_id"] = _identity({k: snapshot[k] for k in ("operation_id", "mint", "policy_version", "assignment")})
    return snapshot


def persist_adoption(conn: sqlite3.Connection, snapshot: dict[str, Any]) -> dict[str, Any]:
    """Atomically freeze an immutable snapshot and one durable, budget-gated work identity."""
    ensure_adoption_schema(conn)
    adoption_id = snapshot["adoption_id"]
    with conn:
        conn.execute("INSERT OR IGNORE INTO universal_monitor_adoptions VALUES(?,?,?,?,?,?)",
                     (adoption_id, snapshot["operation_id"], snapshot["mint"], POLICY,
                      json.dumps(snapshot, sort_keys=True, separators=(",", ":")), snapshot["adopted_at"]))
        if snapshot["next_work_type"]:
            work_id = _identity({"adoption_id": adoption_id, "work_type": snapshot["next_work_type"]})
            conn.execute("INSERT OR IGNORE INTO universal_monitor_adoption_work(work_id,adoption_id,work_type,resume_from) VALUES(?,?,?,?)",
                         (work_id, adoption_id, snapshot["next_work_type"], snapshot["resume_from"]))
    return {"adoption_id": adoption_id, "work_enqueued": bool(snapshot["next_work_type"])}


def global_budget_work(snapshot: dict[str, Any], *, due_at: int | None = None) -> dict[str, Any] | None:
    """Operation-neutral handoff record for the one global budget scheduler."""
    work_type = snapshot["next_work_type"]
    if work_type in {"NONE_TERMINAL", "FAIL_CLOSED_INVALID_BOUNDARY"}:
        return None
    work_id = _identity({"adoption_id": snapshot["adoption_id"], "work_type": work_type})
    return {"work_id": work_id, "operation_id": snapshot["operation_id"], "mint": snapshot["mint"],
            "work_type": work_type, "due_at": int(snapshot["adopted_at"] if due_at is None else due_at),
            "lifecycle_stage": snapshot["adoption_state"], "lifecycle_age_seconds": 0,
            "resume_from": snapshot["resume_from"], "coverage_gap_start": snapshot["resume_from"] if snapshot["adoption_state"] == "LIFECYCLE_RESUME_REQUIRED" else None,
            "provider_class": "PRICE_OR_ENTRY", "estimated_provider_calls": 1,
            "adoption_snapshot_id": snapshot["adoption_id"], "priority_version": "UNIVERSAL_DUE_STAGE_BOUNDARY_V1",
            "attempt_state": "NOT_ADMITTED"}
