"""Durable, single-worker entry-stage queue for disabled Byzantine shadow research.

The caller may invoke ``enqueue_committed_scenario_d`` only *after* its own
Scenario-D transaction commits.  This module has no listener hook, RPC client,
wallet, signer, or transaction submitter.  Its SQLite file is a separate
research namespace, never a canonical operation store.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import time
from typing import Any, Mapping

from src.ops.byzantine_prospective_shadow import (
    FEATURE_FLAG, FEATURE_FLAG_DEFAULT, STRATEGY_VERSION, CurveState, EntryState,
    ScenarioDTrigger, quote_entry_sizes, shadow_identity,
)

DEFAULT_DB_PATH = "database/research/byzantine_shadow/observer.sqlite"
MAX_ATTEMPTS = 3
LEASE_SECONDS = 60
WORKER_CONCURRENCY = 1
PROCESSING_VERSION = "entry-stage-v1"


def enabled(environ: Mapping[str, str] | None = None) -> bool:
    value = (environ or os.environ).get(FEATURE_FLAG, str(FEATURE_FLAG_DEFAULT))
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _connect(path: str) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=2)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema(path: str) -> None:
    with _connect(path) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS byzantine_shadow_jobs (
          logical_id TEXT PRIMARY KEY, envelope_json TEXT NOT NULL, status TEXT NOT NULL,
          attempts INTEGER NOT NULL DEFAULT 0, lease_until INTEGER NOT NULL DEFAULT 0,
          next_attempt_at INTEGER NOT NULL, last_error TEXT, created_at INTEGER NOT NULL,
          updated_at INTEGER NOT NULL, acked_at INTEGER, dead_letter_at INTEGER)""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_byz_shadow_jobs_ready ON byzantine_shadow_jobs(status,next_attempt_at,lease_until)")
        conn.execute("""CREATE TABLE IF NOT EXISTS byzantine_shadow_entry_results (
          result_id TEXT PRIMARY KEY, logical_id TEXT NOT NULL, processing_version TEXT NOT NULL,
          result_json TEXT NOT NULL, created_at INTEGER NOT NULL,
          UNIQUE(logical_id, processing_version))""")


def enqueue_committed_scenario_d(path: str, trigger: ScenarioDTrigger, retained_entry_state: Mapping[str, Any], *, committed: bool, environ: Mapping[str, str] | None = None, now: int | None = None) -> str | None:
    """Post-commit-only compact enqueue; disabled and unqualified inputs are no-ops."""
    if not committed:
        raise RuntimeError("POST_COMMIT_REQUIRED")
    if not enabled(environ) or not trigger.qualified():
        return None
    logical_id = shadow_identity(trigger)
    timestamp = int(time.time()) if now is None else int(now)
    envelope = {"schema_version": "byzantine-shadow-job-v1", "logical_id": logical_id,
        "strategy_version": STRATEGY_VERSION, "operation_id": trigger.operation_id, "mint": trigger.mint,
        "trigger": {"signature": trigger.trigger_signature, "slot": trigger.slot,
            "transaction_ordinal": trigger.transaction_ordinal, "instruction_index": trigger.instruction_index},
        "fingerprint_version": trigger.fingerprint_version, "detector_version": trigger.detector_version,
        "retained_entry_state": dict(retained_entry_state), "enqueued_at": timestamp}
    ensure_schema(path)
    with _connect(path) as conn:
        conn.execute("""INSERT OR IGNORE INTO byzantine_shadow_jobs
            (logical_id,envelope_json,status,attempts,lease_until,next_attempt_at,created_at,updated_at)
            VALUES (?,?,'pending',0,0,?,?,?)""", (logical_id, json.dumps(envelope, sort_keys=True), timestamp, timestamp, timestamp))
    return logical_id


def _lease(path: str, now: int) -> sqlite3.Row | None:
    ensure_schema(path)
    with _connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("""SELECT * FROM byzantine_shadow_jobs
          WHERE (status IN ('pending','retry') AND next_attempt_at<=?)
             OR (status='leased' AND lease_until<=?)
          ORDER BY created_at LIMIT 1""", (now, now)).fetchone()
        if row is not None:
            conn.execute("UPDATE byzantine_shadow_jobs SET status='leased', attempts=attempts+1, lease_until=?, updated_at=? WHERE logical_id=?", (now + LEASE_SECONDS, now, row['logical_id']))
        conn.commit()
        return row


def _entry_state(raw: Mapping[str, Any]) -> EntryState:
    required = {"mint", "bonding_curve", "program_id", "slot", "signature", "transaction_ordinal", "instruction_index", "virtual_sol", "virtual_token", "real_token", "complete", "global_config_identity", "fee_config_identity", "raw_state_sha256", "provenance"}
    if not required.issubset(raw):
        raise ValueError("RETAINED_ENTRY_STATE_UNAVAILABLE")
    return EntryState(**{key: raw.get(key) for key in EntryState.__dataclass_fields__})


def _result(envelope: Mapping[str, Any]) -> dict[str, Any]:
    state = _entry_state(envelope["retained_entry_state"])
    quotes = quote_entry_sizes(state)
    primary = quotes[250_000_000]
    return {"logical_shadow_id": envelope["logical_id"], "strategy_version": STRATEGY_VERSION,
      "processing_version": PROCESSING_VERSION, "operation_id": envelope["operation_id"], "mint": envelope["mint"],
      "scenario_d_trigger": envelope["trigger"], "authoritative_pre_entry_state": dict(envelope["retained_entry_state"]),
      "primary_quote": {"gross_sol_input": primary.gross_sol_input, "quote": primary.quote.__dict__, "post_state": primary.post_state.__dict__},
      "comparison_quotes": {str(size): {"quote": q.quote.__dict__, "post_state": q.post_state.__dict__} for size, q in quotes.items() if size != 250_000_000},
      "observed_control_state": state.curve_state().__dict__, "shadow_counterfactual_initial_state": primary.post_state.__dict__,
      "counterfactual_status": "COUNTERFACTUAL_EXACT", "evidence_label": "MODELLED_WITH_QUALIFIED_EXECUTION",
      "source_artifact_identities": {"raw_state_sha256": state.raw_state_sha256, "provenance": state.provenance}}


def _commit_result(path: str, logical_id: str, result: Mapping[str, Any], now: int) -> None:
    encoded = json.dumps(result, sort_keys=True, separators=(",", ":"))
    result_id = __import__("hashlib").sha256(encoded.encode()).hexdigest()
    with _connect(path) as conn:
        conn.execute("INSERT OR IGNORE INTO byzantine_shadow_entry_results(result_id,logical_id,processing_version,result_json,created_at) VALUES (?,?,?,?,?)", (result_id, logical_id, PROCESSING_VERSION, encoded, now))
        conn.commit()


def _ack(path: str, logical_id: str, now: int) -> None:
    with _connect(path) as conn:
        conn.execute("UPDATE byzantine_shadow_jobs SET status='completed', lease_until=0, acked_at=?, updated_at=? WHERE logical_id=?", (now, now, logical_id))
        conn.commit()


def process_one(path: str, *, now: int | None = None, crash_after_commit: bool = False) -> str | None:
    """Process one job; result persistence always precedes acknowledgement."""
    timestamp = int(time.time()) if now is None else int(now)
    row = _lease(path, timestamp)
    if row is None:
        return None
    try:
        envelope = json.loads(row["envelope_json"])
        result = _result(envelope)
        _commit_result(path, row["logical_id"], result, timestamp)
        if crash_after_commit:
            raise RuntimeError("SIMULATED_CRASH_AFTER_RESULT_COMMIT")
        _ack(path, row["logical_id"], timestamp)
        return "completed"
    except ValueError as exc:
        status = "dead_letter" if "RETAINED_ENTRY_STATE_UNAVAILABLE" in str(exc) else "retry"
        _fail(path, row["logical_id"], str(exc), timestamp, permanent=status == "dead_letter")
        return status
    except Exception as exc:
        _fail(path, row["logical_id"], str(exc), timestamp, permanent=False)
        return "retry"


def _fail(path: str, logical_id: str, error: str, now: int, *, permanent: bool) -> None:
    with _connect(path) as conn:
        attempt = int(conn.execute("SELECT attempts FROM byzantine_shadow_jobs WHERE logical_id=?", (logical_id,)).fetchone()[0])
        terminal = permanent or attempt >= MAX_ATTEMPTS
        status = "dead_letter" if terminal else "retry"
        conn.execute("UPDATE byzantine_shadow_jobs SET status=?, lease_until=0, next_attempt_at=?, last_error=?, updated_at=?, dead_letter_at=? WHERE logical_id=?", (status, now + min(60, 2 ** attempt), error[:500], now, now if terminal else None, logical_id))
        conn.commit()


def result_count(path: str, logical_id: str) -> int:
    with _connect(path) as conn:
        return int(conn.execute("SELECT COUNT(*) FROM byzantine_shadow_entry_results WHERE logical_id=?", (logical_id,)).fetchone()[0])


def submission_capability() -> str:
    return "NONE"
