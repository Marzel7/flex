"""One small durable arbiter for all provider work families.

The scheduler owns identities and fair dispatch order only.  It does not know
operation names, call a provider, or retain provider payloads.  Callers submit
compact logical work after their own durable state transition, then use the
existing DEV-009/DEV-012 gate immediately before transport.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Mapping

VERSION = "GENERIC_PROVIDER_WORK_SCHEDULER_V1"
FAMILIES = ("ENTRY_REFERENCE_OPENING", "LIVE_PRICE", "DURABLE_15M", "TERMINAL_FINALIZER")
PRIORITY_POLICY = "ROUND_ROBIN_FAMILIES_WITH_ENTRY_REFERENCE_FIRST_ON_EMPTY_CYCLE"
FAIRNESS_BOUND = "AT_MOST_ONE_DISPATCH_PER_READY_FAMILY_BETWEEN_TURNS"


def _canon(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _id(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canon(value).encode()).hexdigest()


def _connect(path: str | Path) -> sqlite3.Connection:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    return conn


def ensure(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS generic_provider_work (
      work_id TEXT PRIMARY KEY, mint TEXT NOT NULL, family TEXT NOT NULL,
      payload_json TEXT NOT NULL, state TEXT NOT NULL, created_at INTEGER NOT NULL,
      updated_at INTEGER NOT NULL, UNIQUE(mint, family, payload_json));
    CREATE TABLE IF NOT EXISTS generic_provider_scheduler_state (
      name TEXT PRIMARY KEY, value TEXT NOT NULL);
    """)


def logical_identity(*, mint: str, family: str, payload: Mapping[str, Any]) -> str:
    if family not in FAMILIES:
        raise ValueError("UNKNOWN_PROVIDER_WORK_FAMILY")
    return _id({"version": VERSION, "mint": mint, "family": family, "payload": dict(payload)})


def enqueue(path: str | Path, *, mint: str, family: str, payload: Mapping[str, Any], now: int | None = None) -> dict[str, Any]:
    stamp = int(time.time() if now is None else now)
    serialized = _canon(dict(payload))
    work_id = logical_identity(mint=mint, family=family, payload=payload)
    with _connect(path) as conn:
        ensure(conn)
        before = conn.total_changes
        conn.execute("INSERT OR IGNORE INTO generic_provider_work VALUES(?,?,?,?,?,?,?)",
                     (work_id, mint, family, serialized, "PENDING", stamp, stamp))
        created = conn.total_changes > before
        conn.commit()
    return {"work_id": work_id, "created": created, "state": "PENDING"}


def _cursor(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT value FROM generic_provider_scheduler_state WHERE name='family_cursor'").fetchone()
    return int(row[0]) if row else 0


def _set_cursor(conn: sqlite3.Connection, cursor: int) -> None:
    conn.execute("INSERT INTO generic_provider_scheduler_state VALUES('family_cursor', ?) ON CONFLICT(name) DO UPDATE SET value=excluded.value", (str(cursor),))


def next_ready(path: str | Path, *, families: tuple[str, ...] | None = None) -> dict[str, Any] | None:
    """Select one durable item round-robin across ready work families."""
    with _connect(path) as conn:
        ensure(conn)
        eligible = tuple(families or FAMILIES)
        if any(family not in FAMILIES for family in eligible): raise ValueError("UNKNOWN_PROVIDER_WORK_FAMILY")
        start = _cursor(conn) % len(eligible)
        for offset in range(len(eligible)):
            index = (start + offset) % len(eligible)
            family = eligible[index]
            row = conn.execute("SELECT * FROM generic_provider_work WHERE state='PENDING' AND family=? ORDER BY created_at,work_id LIMIT 1", (family,)).fetchone()
            if row:
                _set_cursor(conn, index + 1)
                conn.commit()
                result = dict(row)
                result["payload"] = json.loads(result.pop("payload_json"))
                return result
    return None


def admit_next(path: str | Path, provider_queue: Any, *, now: int, global_limit: int = 20, token_limit: int = 4, families: tuple[str, ...] | None = None, pre_admit: Any = None) -> dict[str, Any] | None:
    """Gate one selected physical attempt; denied work remains PENDING.

    ``pre_admit`` is a caller-owned durable scope check.  It runs after
    selection but before DEV-012 debit and transport, keeping this generic
    arbiter independent of operation-specific acquisition contracts.
    """
    work = next_ready(path, families=families)
    if work is None:
        return None
    if pre_admit is not None and not pre_admit(work):
        return {**work, "pre_admission_denied": True}
    provider_queue.admit_provider_dispatch(work["mint"], work["family"], now=now, global_limit=global_limit, token_limit=token_limit, force_dev=True)
    with _connect(path) as conn:
        ensure(conn)
        conn.execute("UPDATE generic_provider_work SET state='DISPATCHED',updated_at=? WHERE work_id=? AND state='PENDING'", (int(now), work["work_id"]))
        conn.commit()
    return work


def complete(path: str | Path, work_id: str, *, now: int) -> None:
    with _connect(path) as conn:
        ensure(conn)
        conn.execute("UPDATE generic_provider_work SET state='COMPLETE',updated_at=? WHERE work_id=? AND state='DISPATCHED'", (int(now), work_id))
        conn.commit()


def discard_before_dispatch(path: str | Path, work_id: str, *, now: int) -> None:
    """Retire stale work rejected by a caller-owned scope check, without debit."""
    with _connect(path) as conn:
        ensure(conn)
        conn.execute("UPDATE generic_provider_work SET state='COMPLETE',updated_at=? WHERE work_id=? AND state IN ('PENDING','DISPATCHED')", (int(now), work_id))
        conn.commit()


def defer(path: str | Path, work_id: str, *, now: int) -> None:
    """Return a gated attempt to its same durable identity for later eligibility."""
    with _connect(path) as conn:
        ensure(conn)
        conn.execute("UPDATE generic_provider_work SET state='PENDING',updated_at=? WHERE work_id=? AND state='DISPATCHED'", (int(now), work_id))
        conn.commit()


def recover_dispatched(path: str | Path, *, now: int) -> int:
    """Restart-safe recovery: physical result is not assumed durable."""
    with _connect(path) as conn:
        ensure(conn)
        cursor = conn.execute("UPDATE generic_provider_work SET state='PENDING',updated_at=? WHERE state='DISPATCHED'", (int(now),))
        conn.commit()
        return cursor.rowcount


def sync_opening_action_job(path: str | Path, opening_jobs_path: str | Path, job_id: str, *, now: int | None = None) -> dict[str, Any] | None:
    """Project exactly one next opening request into the generic work loop.

    The opening job remains the causal state authority.  This scheduler merely
    retains the ready request identity, so a restart cannot invent a second
    Create, block, or FX call.
    """
    from . import live_opening_action_job
    planned = live_opening_action_job.plan_next(opening_jobs_path, job_id)
    if planned.get("state") != "READY":
        return None
    row = live_opening_action_job.get(opening_jobs_path, job_id)
    return enqueue(path, mint=row["mint"], family="ENTRY_REFERENCE_OPENING",
                   payload={"opening_job_id": job_id, "request_id": planned["request_id"],
                            "request_family": planned["request_family"], "parameters": planned["request_parameters"]},
                   now=now)
