"""Generic, future-only immutable anchors and authoritative order evidence.

This module persists compact facts only.  It deliberately has no membership,
admission, UI, provider, or discovery dependency: callers supply already-known
signatures and independently-qualified causal route evidence.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Mapping

ANCHOR_VERSION = "IMMUTABLE_OPERATION_CAUSAL_ANCHOR_V1"
ORDER_VERSION = "PROSPECTIVE_TRANSACTION_ORDER_EVIDENCE_V1"
SLOT_VERSION = "KNOWN_SIGNATURE_SLOT_V1"
ORDINAL_VERSION = "TRANSACTION_ORDER_SLOT_ORDINAL_V1"
MAX_COMPACT_RECORD_BYTES = 2048
NORMAL_STORAGE_BYTES = 32 * 1024 * 1024
HARD_STORAGE_BYTES = 48 * 1024 * 1024
MAX_TRANSIENT_GETTRANSACTION_BYTES = 1 * 1024 * 1024
MAX_TRANSIENT_GETBLOCK_BYTES = 32 * 1024 * 1024

RETENTION_TABLE_COLUMNS = {
    "immutable_operation_causal_anchors": ("anchor_id", "operation_id", "route_type", "route_semantic_version", "role_left", "role_right", "causal_direction", "source_evidence_id", "ordering_evidence_id", "establishment_source", "established_at"),
    "prospective_signature_slots": ("slot_evidence_id", "signature", "slot", "source_identity", "acquisition_identity", "semantic_version", "acquired_at"),
    "prospective_transaction_ordinals": ("ordinal_evidence_id", "signature", "slot", "transaction_ordinal", "block_evidence_id", "semantic_version", "acquired_at"),
    "prospective_order_links": ("order_evidence_id", "parent_signature", "child_signature", "parent_slot_evidence_id", "child_slot_evidence_id", "parent_ordinal_evidence_id", "child_ordinal_evidence_id", "semantic_version", "established_at"),
    "prospective_slot_acquisition_intents": ("intent_id", "operation_id", "mint", "signature", "source_evidence_id", "semantic_version", "status", "created_at"),
}
# Every table needs its primary-key index plus the listed uniqueness identity.
RETENTION_UNIQUE_INDEX_COLUMNS = {
    "immutable_operation_causal_anchors": ("operation_id", "route_type", "route_semantic_version", "role_left", "role_right", "causal_direction", "source_evidence_id", "ordering_evidence_id"),
    "prospective_signature_slots": ("signature",),
    "prospective_transaction_ordinals": ("signature",),
    "prospective_order_links": ("parent_signature", "child_signature", "parent_slot_evidence_id", "child_slot_evidence_id", "parent_ordinal_evidence_id", "child_ordinal_evidence_id", "semantic_version"),
    "prospective_slot_acquisition_intents": ("operation_id", "mint", "signature", "source_evidence_id", "semantic_version"),
}


def _id(*parts: object) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _compact(value: object) -> str:
    value = str(value)
    if not value or len(value.encode()) > MAX_COMPACT_RECORD_BYTES:
        raise ValueError("compact evidence value invalid")
    return value


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Deployment/fixture migration only; never call from runtime processing."""
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS immutable_operation_causal_anchors (
      anchor_id TEXT PRIMARY KEY, operation_id TEXT NOT NULL, route_type TEXT NOT NULL,
      route_semantic_version TEXT NOT NULL, role_left TEXT NOT NULL, role_right TEXT NOT NULL,
      causal_direction TEXT NOT NULL, source_evidence_id TEXT NOT NULL,
      ordering_evidence_id TEXT NOT NULL, establishment_source TEXT NOT NULL,
      established_at INTEGER NOT NULL,
      UNIQUE(operation_id,route_type,route_semantic_version,role_left,role_right,causal_direction,source_evidence_id,ordering_evidence_id)
    );
    CREATE TABLE IF NOT EXISTS prospective_signature_slots (
      slot_evidence_id TEXT PRIMARY KEY, signature TEXT NOT NULL UNIQUE, slot INTEGER NOT NULL,
      source_identity TEXT NOT NULL, acquisition_identity TEXT NOT NULL,
      semantic_version TEXT NOT NULL, acquired_at INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS prospective_transaction_ordinals (
      ordinal_evidence_id TEXT PRIMARY KEY, signature TEXT NOT NULL UNIQUE, slot INTEGER NOT NULL,
      transaction_ordinal INTEGER NOT NULL, block_evidence_id TEXT NOT NULL,
      semantic_version TEXT NOT NULL, acquired_at INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS prospective_order_links (
      order_evidence_id TEXT PRIMARY KEY, parent_signature TEXT NOT NULL, child_signature TEXT NOT NULL,
      parent_slot_evidence_id TEXT NOT NULL, child_slot_evidence_id TEXT NOT NULL,
      parent_ordinal_evidence_id TEXT, child_ordinal_evidence_id TEXT,
      semantic_version TEXT NOT NULL, established_at INTEGER NOT NULL,
      UNIQUE(parent_signature,child_signature,parent_slot_evidence_id,child_slot_evidence_id,parent_ordinal_evidence_id,child_ordinal_evidence_id,semantic_version)
    );
    CREATE TABLE IF NOT EXISTS prospective_slot_acquisition_intents (
      intent_id TEXT PRIMARY KEY, operation_id TEXT NOT NULL, mint TEXT NOT NULL,
      signature TEXT NOT NULL, source_evidence_id TEXT NOT NULL,
      semantic_version TEXT NOT NULL, status TEXT NOT NULL, created_at INTEGER NOT NULL,
      UNIQUE(operation_id,mint,signature,source_evidence_id,semantic_version)
    );
    """)


def validate_retention_schema(conn: sqlite3.Connection) -> dict:
    """Read-only exact schema preflight for the runtime retention seam."""
    for table, required_columns in RETENTION_TABLE_COLUMNS.items():
        row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
        if row is None:
            return {"state": "SCHEMA_NOT_READY", "reason": "missing_table", "table": table}
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        missing = sorted(set(required_columns) - columns)
        if missing:
            return {"state": "SCHEMA_NOT_READY", "reason": "missing_column", "table": table, "columns": missing}
        required_unique = RETENTION_UNIQUE_INDEX_COLUMNS[table]
        found = False
        for index in conn.execute(f"PRAGMA index_list({table})"):
            if not index[2]:
                continue
            index_columns = tuple(row[2] for row in conn.execute(f"PRAGMA index_info({index[1]})"))
            if index_columns == required_unique:
                found = True
                break
        if not found:
            return {"state": "SCHEMA_NOT_READY", "reason": "missing_unique_index", "table": table}
    return {"state": "READY"}


def _guard(conn: sqlite3.Connection, incoming: int = 1) -> None:
    tables = ("immutable_operation_causal_anchors", "prospective_signature_slots", "prospective_transaction_ordinals", "prospective_order_links", "prospective_slot_acquisition_intents")
    rows = sum(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables)
    if rows + incoming > HARD_STORAGE_BYTES // MAX_COMPACT_RECORD_BYTES:
        raise RuntimeError("PROSPECTIVE_EVIDENCE_STORAGE_LIMIT_REACHED")


def anchor_identity(record: Mapping) -> str:
    fields = tuple(record.get(k) for k in ("operation_id", "route_type", "route_semantic_version", "role_left", "role_right", "causal_direction", "source_evidence_id", "ordering_evidence_id"))
    return _id(ANCHOR_VERSION, *fields)


def persist_anchor(conn: sqlite3.Connection, record: Mapping, *, established_at: int) -> str:
    required = ("operation_id", "route_type", "route_semantic_version", "role_left", "role_right", "causal_direction", "source_evidence_id", "ordering_evidence_id", "establishment_source")
    if record.get("establishment_source") == "membership" or any(_compact(record.get(k, "")) == "" for k in required):
        raise ValueError("immutable anchor requires independent qualified evidence")
    _guard(conn); aid = anchor_identity(record)
    conn.execute("INSERT OR IGNORE INTO immutable_operation_causal_anchors VALUES(?,?,?,?,?,?,?,?,?,?,?)", (aid, *(record[k] for k in required[:8]), record["establishment_source"], int(established_at)))
    return aid


def slot_identity(record: Mapping) -> str:
    return _id(SLOT_VERSION, *(record.get(k) for k in ("signature", "slot", "source_identity", "acquisition_identity", "semantic_version")))


def persist_slot(conn: sqlite3.Connection, record: Mapping, *, acquired_at: int) -> str:
    for key in ("signature", "source_identity", "acquisition_identity", "semantic_version"): _compact(record.get(key, ""))
    if not isinstance(record.get("slot"), int) or record["slot"] < 0: raise ValueError("authoritative slot required")
    _guard(conn); eid = slot_identity(record)
    conn.execute("INSERT OR IGNORE INTO prospective_signature_slots VALUES(?,?,?,?,?,?,?)", (eid, record["signature"], record["slot"], record["source_identity"], record["acquisition_identity"], record["semantic_version"], int(acquired_at)))
    return eid


def ordinal_identity(record: Mapping) -> str:
    return _id(ORDINAL_VERSION, *(record.get(k) for k in ("signature", "slot", "transaction_ordinal", "block_evidence_id", "semantic_version")))


def persist_ordinal(conn: sqlite3.Connection, record: Mapping, *, acquired_at: int) -> str:
    for key in ("signature", "block_evidence_id", "semantic_version"): _compact(record.get(key, ""))
    if not all(isinstance(record.get(k), int) and record[k] >= 0 for k in ("slot", "transaction_ordinal")): raise ValueError("qualified ordinal required")
    _guard(conn); eid = ordinal_identity(record)
    conn.execute("INSERT OR IGNORE INTO prospective_transaction_ordinals VALUES(?,?,?,?,?,?,?)", (eid, record["signature"], record["slot"], record["transaction_ordinal"], record["block_evidence_id"], record["semantic_version"], int(acquired_at)))
    return eid


def assess_order(conn: sqlite3.Connection, parent_signature: str, child_signature: str) -> dict:
    slots = {r["signature"]: dict(r) for r in conn.execute("SELECT * FROM prospective_signature_slots WHERE signature IN (?,?)", (parent_signature, child_signature)).fetchall()}
    if parent_signature not in slots or child_signature not in slots: return {"state": "INSUFFICIENT_EVIDENCE", "reason": "missing_authoritative_slot"}
    parent, child = slots[parent_signature], slots[child_signature]
    if parent["slot"] < child["slot"]: return {"state": "PARENT_BEFORE_CHILD", "reason": "earlier_slot"}
    if parent["slot"] > child["slot"]: return {"state": "PARENT_AFTER_CHILD", "reason": "parent_later_slot"}
    ordinals = {r["signature"]: dict(r) for r in conn.execute("SELECT * FROM prospective_transaction_ordinals WHERE signature IN (?,?) AND slot=?", (parent_signature, child_signature, parent["slot"])).fetchall()}
    if parent_signature not in ordinals or child_signature not in ordinals: return {"state": "INSUFFICIENT_EVIDENCE", "reason": "same_slot_missing_transaction_ordinal"}
    return {"state": "PARENT_BEFORE_CHILD" if ordinals[parent_signature]["transaction_ordinal"] < ordinals[child_signature]["transaction_ordinal"] else "PARENT_AFTER_CHILD", "reason": "transaction_ordinal"}


def assess_anchor_continuity(conn: sqlite3.Connection, candidate: Mapping) -> dict:
    if candidate.get("conflict_state"): return {"state": "CONFLICT", "reason": "selected_upstream_disagrees"}
    required = ("operation_id", "route_type", "route_semantic_version", "role_left", "role_right", "causal_direction", "source_evidence_id", "ordering_evidence_id")
    if any(candidate.get(k) in (None, "") for k in required): return {"state": "INSUFFICIENT_EVIDENCE", "reason": "missing_candidate_route"}
    rows = conn.execute("SELECT anchor_id FROM immutable_operation_causal_anchors WHERE operation_id=? AND route_type=? AND route_semantic_version=? AND role_left=? AND role_right=? AND causal_direction=? AND source_evidence_id=? AND ordering_evidence_id=?", tuple(candidate[k] for k in required)).fetchall()
    if not rows: return {"state": "INSUFFICIENT_EVIDENCE", "reason": "no_matching_immutable_anchor"}
    return {"state": "QUALIFIED", "anchor_id": rows[0][0]}


def stage_missing_slot_intents(conn: sqlite3.Connection, *, operation_id: str, mint: str,
                               parent_signature: str, child_signature: str,
                               source_evidence_id: str, now: int) -> list[str]:
    """Durably stage at most one future getTransaction intent per known signature.

    This is provider-free: an authorized runner must consume the compact intent
    later, with one request and zero retries.  Existing local slot records are
    never re-requested.
    """
    readiness = validate_retention_schema(conn)
    if readiness["state"] != "READY":
        return []
    _guard(conn, 2)
    ids=[]
    for signature in (parent_signature, child_signature):
        _compact(signature); _compact(source_evidence_id)
        if conn.execute("SELECT 1 FROM prospective_signature_slots WHERE signature=?", (signature,)).fetchone():
            continue
        iid=_id("KNOWN_SIGNATURE_SLOT_INTENT_V1", operation_id, mint, signature, source_evidence_id)
        conn.execute("INSERT OR IGNORE INTO prospective_slot_acquisition_intents VALUES(?,?,?,?,?,?,?,?)", (iid, operation_id, mint, signature, source_evidence_id, SLOT_VERSION, "PENDING_AUTHORIZED_ACQUISITION", int(now)))
        ids.append(iid)
    return ids
