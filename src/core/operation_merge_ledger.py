"""
OperationMergeLedger — immutable, explanatory record of every wt_ops_v2
DISCOVER/MERGE/EXPAND decision (X41.0, implementing X40.0 Phase 6).

THIS MODULE DOES NOT CHANGE MERGE LOGIC. _find_hard_merge_target() in
src/core/operation_store_v2.py is untouched and continues to produce
identical merge decisions. This ledger is called AFTER that decision is
already made, purely to record why it fired. A failure here must never
prevent or alter the underlying wt_ops_v2 write.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Optional

try:
    from src.utils.db_locking import db_connect
except Exception:                                    # pragma: no cover
    import sqlite3
    def db_connect(path, timeout=30):
        c = sqlite3.connect(path, timeout=timeout); c.row_factory = sqlite3.Row; return c

logger = logging.getLogger("operation_merge_ledger")

OPS_DB_PATH = os.path.abspath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "database", "wt_ops_v2.db"))

EVENT_TYPES = (
    "OPERATION_CREATED",
    "TREASURY_ADDED",
    "HARD_MERGE",
    "MERGE_REJECTED",
    "SPLIT",
    "ROOT_REASSIGNED",
    "FAMILY_LINKED",
    "FAMILY_UNLINKED",
    "MANUAL_OVERRIDE",
)

# Names for the exact deterministic rules in _find_hard_merge_target(), so the
# ledger can record precisely which one fired without re-deriving the logic.
MERGE_RULES = (
    "SAME_ROOT",
    "DIRECT_TREASURY_MEMBERSHIP",
    "SHARED_DECISIVE_INFRA",
    "BROAD_INFRA_OVERLAP_GE_3",
)

_SCHEMA_READY = False


def ensure_schema(conn) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS operation_merge_ledger (
            event_id             INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type           TEXT NOT NULL,
            source_operation_uuid TEXT,
            target_operation_uuid TEXT,
            affected_wallet      TEXT,
            merge_rule           TEXT,
            evidence_refs_json   TEXT,
            reviewer_or_rule     TEXT,
            timestamp            INTEGER NOT NULL,
            previous_state_json  TEXT,
            resulting_state_json TEXT,
            reverses_event_id    INTEGER,
            reconstructed        INTEGER NOT NULL DEFAULT 0,
            reconstruction_source TEXT,
            reconstruction_confidence TEXT,
            created_at           INTEGER NOT NULL
        )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_opml_target ON operation_merge_ledger(target_operation_uuid)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_opml_source ON operation_merge_ledger(source_operation_uuid)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_opml_wallet ON operation_merge_ledger(affected_wallet)"
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS operation_merge_ledger_write_failures (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            operation_uuid TEXT,
            event_type    TEXT,
            error         TEXT,
            payload_json  TEXT,
            occurred_at   INTEGER NOT NULL
        )"""
    )
    conn.commit()


def _ensure_schema_once(conn) -> None:
    global _SCHEMA_READY
    if not _SCHEMA_READY:
        ensure_schema(conn)
        _SCHEMA_READY = True


def record_merge_event(
    conn,
    *,
    event_type: str,
    source_operation_uuid: Optional[str] = None,
    target_operation_uuid: Optional[str] = None,
    affected_wallet: Optional[str] = None,
    merge_rule: Optional[str] = None,
    evidence_refs: Optional[dict] = None,
    reviewer_or_rule: Optional[str] = None,
    timestamp: Optional[int] = None,
    previous_state: Optional[dict] = None,
    resulting_state: Optional[dict] = None,
    reverses_event_id: Optional[int] = None,
    reconstructed: bool = False,
    reconstruction_source: Optional[str] = None,
    reconstruction_confidence: Optional[str] = None,
) -> Optional[int]:
    """Append one merge-ledger row. NEVER RAISES — same dual-write contract as
    attribution_evidence.record_evidence(): called AFTER the wt_ops_v2 write
    already committed; a failure here is logged and swallowed, never bubbled."""
    if event_type not in EVENT_TYPES:
        logger.warning("operation_merge_ledger: unknown event_type %r for op %s",
                        event_type, target_operation_uuid)
    now = int(time.time())
    ts = timestamp if timestamp is not None else now
    try:
        _ensure_schema_once(conn)
        cur = conn.execute(
            """INSERT INTO operation_merge_ledger
                 (event_type, source_operation_uuid, target_operation_uuid, affected_wallet,
                  merge_rule, evidence_refs_json, reviewer_or_rule, timestamp,
                  previous_state_json, resulting_state_json, reverses_event_id,
                  reconstructed, reconstruction_source, reconstruction_confidence, created_at)
               VALUES (?,?,?,?, ?,?,?,?, ?,?,?, ?,?,?, ?)""",
            (event_type, source_operation_uuid, target_operation_uuid, affected_wallet,
             merge_rule, json.dumps(evidence_refs) if evidence_refs is not None else None,
             reviewer_or_rule, ts,
             json.dumps(previous_state) if previous_state is not None else None,
             json.dumps(resulting_state) if resulting_state is not None else None,
             reverses_event_id,
             1 if reconstructed else 0, reconstruction_source, reconstruction_confidence, now),
        )
        conn.commit()
        return cur.lastrowid
    except Exception as exc:                          # pragma: no cover - defensive
        logger.error("operation_merge_ledger write failed for %s (%s): %s",
                     target_operation_uuid, event_type, exc)
        try:
            fail_conn = db_connect(OPS_DB_PATH, timeout=5)
            try:
                ensure_schema(fail_conn)
                fail_conn.execute(
                    """INSERT INTO operation_merge_ledger_write_failures
                         (operation_uuid, event_type, error, payload_json, occurred_at)
                       VALUES (?,?,?,?,?)""",
                    (target_operation_uuid, event_type, str(exc),
                     json.dumps({"source_operation_uuid": source_operation_uuid,
                                 "affected_wallet": affected_wallet,
                                 "merge_rule": merge_rule}), now),
                )
                fail_conn.commit()
            finally:
                fail_conn.close()
        except Exception:                              # pragma: no cover - defensive
            logger.error("operation_merge_ledger: failure log itself failed for %s",
                         target_operation_uuid)
        return None


def determine_merge_rule(conn, treasury_root: str, chain_infra: set, target_op_uuid: str) -> str:
    """Re-derive WHICH of the deterministic rules in _find_hard_merge_target()
    fired, for logging purposes only. This DUPLICATES read-only classification
    logic (never the merge decision itself) so the ledger can record a precise
    rule name without _find_hard_merge_target() needing to return one itself —
    keeping that function's signature and behaviour completely untouched, per
    X41.0's explicit instruction not to modify merge logic."""
    row = conn.execute(
        "SELECT operation_uuid FROM wt_ops_v2 WHERE treasury_root=?", (treasury_root,)
    ).fetchone()
    if row and row[0] == target_op_uuid:
        return "SAME_ROOT"

    infra_rows = conn.execute(
        """SELECT wallet FROM wt_ops_v2_wallets
           WHERE operation_uuid=? AND role IN
           ('TREASURY','COLLECTOR','PASS_THROUGH','TERMINAL','DIRECT_FUNDER')""",
        (target_op_uuid,),
    ).fetchall()
    infra = {r[0] for r in infra_rows}
    if treasury_root in infra:
        return "DIRECT_TREASURY_MEMBERSHIP"

    shared = chain_infra & infra
    if shared:
        for w in shared:
            hit = conn.execute(
                "SELECT 1 FROM wt_ops_v2_wallets WHERE operation_uuid=? AND wallet=? "
                "AND role IN ('COLLECTOR','TERMINAL','DIRECT_FUNDER')",
                (target_op_uuid, w),
            ).fetchone()
            if hit:
                return "SHARED_DECISIVE_INFRA"
    if len(shared) >= 3:
        return "BROAD_INFRA_OVERLAP_GE_3"
    return "UNKNOWN"
