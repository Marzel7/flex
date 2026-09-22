"""Durable, post-commit Byzantine opening-cluster evidence projection.

This store intentionally consumes compact *committed* envelopes; it never
opens the canonical operations database and never fetches provider data.  The
upstream writer must establish the historical causal order
``slot, transaction_index, action_index`` before an envelope is admitted.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Mapping

from src.ops.operation_strategy_trigger_producer import qualify

SCHEMA_VERSION = "byzantine-opening-cluster-projection.v1"
OPERATION_ID = "byzantine"
ORDER_INSUFFICIENT = "OPENING_CLUSTER_ORDER_INSUFFICIENT"
STATE_INSUFFICIENT = "SCENARIO_D_TRIGGER_STATE_INSUFFICIENT"


def _canon(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _id(value: Any) -> str:
    return hashlib.sha256(_canon(value).encode()).hexdigest()


def connect(path: str) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def ensure(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """CREATE TABLE IF NOT EXISTS byzantine_opening_cluster_source (
        source_id TEXT PRIMARY KEY, cluster_id TEXT NOT NULL, payload TEXT NOT NULL,
        received_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS byzantine_opening_cluster_projection (
        projection_id TEXT PRIMARY KEY, cluster_id TEXT NOT NULL, mint TEXT NOT NULL,
        position INTEGER, status TEXT NOT NULL, payload TEXT NOT NULL,
        committed INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL,
        UNIQUE(cluster_id,mint));
        CREATE TABLE IF NOT EXISTS byzantine_opening_cluster_conflicts (
        id TEXT PRIMARY KEY, cluster_id TEXT NOT NULL, detail TEXT NOT NULL,
        created_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS byzantine_opening_cluster_cursor (
        name TEXT PRIMARY KEY, source_count INTEGER NOT NULL, updated_at INTEGER NOT NULL);
        """
    )


def _validate(envelope: Mapping[str, Any]) -> str | None:
    required = {"canonical_evidence_id", "cluster_id", "mint", "signature", "slot",
                "transaction_index", "action_index", "causal_timestamp",
                "is_recurrent_cluster_member", "ordering_complete"}
    if not required.issubset(envelope):
        return ORDER_INSUFFICIENT
    if envelope.get("operation_id", OPERATION_ID) != OPERATION_ID:
        return "OPERATION_MISMATCH"
    if any(envelope.get(k) is None for k in ("slot", "transaction_index", "action_index")):
        return ORDER_INSUFFICIENT
    return None


def ingest(path: str, envelope: Mapping[str, Any], *, now: int | None = None) -> dict[str, Any]:
    """Persist one post-commit canonical envelope.  Duplicate IDs are harmless."""
    reason = _validate(envelope)
    source_id = _id({"schema": SCHEMA_VERSION, "canonical": envelope.get("canonical_evidence_id"),
                     "mint": envelope.get("mint"), "signature": envelope.get("signature")})
    payload = dict(envelope, schema_version=SCHEMA_VERSION, source_id=source_id)
    with connect(path) as conn:
        ensure(conn)
        conn.execute("INSERT OR IGNORE INTO byzantine_opening_cluster_source VALUES(?,?,?,?)",
                     (source_id, str(envelope.get("cluster_id", "")), _canon(payload), int(now or time.time())))
        conn.commit()
    return {"source_id": source_id, "result": reason or "INGESTED"}


def _source_rows(conn: sqlite3.Connection, cluster_id: str) -> list[dict[str, Any]]:
    return [json.loads(row[0]) for row in conn.execute(
        "SELECT payload FROM byzantine_opening_cluster_source WHERE cluster_id=? ORDER BY source_id", (cluster_id,)
    )]


def project_cluster(path: str, cluster_id: str, *, qualification_db: str | None = None,
                    operation_version: str = "BYZANTINE_OPENING_CLUSTER_V1",
                    strategy_version: str = "byzantine-prospective-shadow-candidates.v1",
                    prospective_activation: Mapping[str, Any] | None = None,
                    now: int | None = None) -> dict[str, Any]:
    """Rebuild one cluster deterministically after canonical commit.

    `ordering_complete` is an upstream committed watermark/certificate.  It is
    deliberately required for every recurrent member before position 13 can be
    emitted: this prevents late arrival from silently changing a qualification.
    """
    ts = int(now or time.time())
    with connect(path) as conn:
        ensure(conn)
        rows = _source_rows(conn, cluster_id)
        if any(_validate(row) == ORDER_INSUFFICIENT for row in rows):
            return {"result": ORDER_INSUFFICIENT, "cluster_id": cluster_id}
        members = [r for r in rows if r.get("is_recurrent_cluster_member")]
        members.sort(key=lambda r: (int(r["slot"]), int(r["transaction_index"]), int(r["action_index"]), r["signature"]))
        if not members:
            return {"result": "OPENING_CLUSTER_EMPTY", "cluster_id": cluster_id}
        if not all(r.get("ordering_complete") for r in members):
            return {"result": "OPENING_CLUSTER_PROVISIONAL", "cluster_id": cluster_id}
        emitted = []
        for position, row in enumerate(members, 1):
            status = "OPENING_CLUSTER_MEMBER" if position <= 12 else "POST_OPENING_CLUSTER_MEMBER"
            if position == 13:
                status = "FIRST_POST_12_POSITION_RECURRENT_OPENING_CLUSTER_ENTRY"
                if not row.get("entry_state_evidence_ref"):
                    status = STATE_INSUFFICIENT
            payload = {"schema_version": SCHEMA_VERSION, "operation_id": OPERATION_ID,
                       "operation_version": operation_version, "cluster_id": cluster_id,
                       "mint": row["mint"], "creator": row.get("creator"), "position": position,
                       "signature": row["signature"], "slot": int(row["slot"]),
                       "event_index": int(row["action_index"]), "transaction_index": int(row["transaction_index"]),
                       "causal_timestamp": row["causal_timestamp"],
                       "canonical_evidence_ids": [row["canonical_evidence_id"]],
                       "entry_state_evidence_ref": row.get("entry_state_evidence_ref"),
                       "cluster_complete_through": len(members), "prospective_activation_eligible": False,
                       "provenance_digest": _id(row)}
            pid = _id({"cluster": cluster_id, "mint": row["mint"], "source": row["source_id"]})
            conn.execute("INSERT OR REPLACE INTO byzantine_opening_cluster_projection VALUES(?,?,?,?,?,?,?,?)",
                         (pid, cluster_id, row["mint"], position, status, _canon(payload), 0, ts))
            emitted.append((status, payload, pid))
        conn.execute("INSERT OR REPLACE INTO byzantine_opening_cluster_cursor VALUES('source-count',?,?)", (len(rows), ts))
        conn.commit()  # projection is durable before generic evaluation
    trigger = next((x for x in emitted if x[0] == "FIRST_POST_12_POSITION_RECURRENT_OPENING_CLUSTER_ENTRY"), None)
    if not trigger:
        return {"result": STATE_INSUFFICIENT if any(x[0] == STATE_INSUFFICIENT for x in emitted) else "OPENING_CLUSTER_COMPLETE", "rows": len(emitted)}
    _, p, pid = trigger
    boundary_slot = (prospective_activation or {}).get("slot")
    if boundary_slot is None or p["slot"] < int(boundary_slot):
        return {"result": "HISTORICAL_REPLAY_ONLY", "projection_id": pid, "rows": len(emitted)}
    evidence = {"operation_id": OPERATION_ID, "mint": p["mint"], "cluster_id": cluster_id,
                "opening_positions": list(range(1, 13)), "recurrent_cluster_completed": True,
                "signature": p["signature"], "slot": p["slot"], "event_index": p["event_index"],
                "entry_state_reference": p["entry_state_evidence_ref"], "position": 13, "is_cluster_member": True}
    result = qualify(qualification_db or str(Path(path).with_name("operation_strategy_triggers.sqlite")), evidence,
                     operation_version=operation_version, strategy_version=strategy_version,
                     trigger_name="FIRST_POST_12_POSITION_RECURRENT_OPENING_CLUSTER_ENTRY", trigger_version=SCHEMA_VERSION, now=ts)
    return {"result": result["result"], "projection_id": pid, "rows": len(emitted), "qualification": result}


def submission_capability() -> str:
    return "NONE"
