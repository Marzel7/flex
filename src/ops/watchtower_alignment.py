"""Canonical WATCHTOWER identity reconciliation.

``wt_confirmed_treasuries`` is the existing authoritative WATCHTOWER registry.
This module joins that reviewed legacy truth to the already-promoted canonical
WATCHTOWER operator.  It does not discover, score, or infer identities.

All mutating helpers accept the caller's SQLite connection so treasury
confirmation and canonical membership can share one transaction.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any

from src.core.database_write_service import execute_script
from src.ops.operator_writer import OperatorWriter


WATCHTOWER_OPERATOR_ID = "04265d9f-6eb2-568c-a49e-9253091a4dbb"
WATCHTOWER_DISPLAY_NAME = "WATCHTOWER"


DDL = """
CREATE TABLE IF NOT EXISTS watchtower_identity_reconciliations (
    treasury             TEXT PRIMARY KEY,
    operator_id          TEXT NOT NULL REFERENCES operators(operator_id),
    source_table         TEXT NOT NULL,
    source_provenance    TEXT,
    source_confirmed_at  INTEGER,
    evidence_fingerprint TEXT NOT NULL,
    evidence_snapshot    TEXT NOT NULL,
    reconciled_at        INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_wir_operator
    ON watchtower_identity_reconciliations(operator_id, reconciled_at DESC);
"""


class CanonicalWatchtowerMissingError(RuntimeError):
    """The reviewed canonical operator required by reconciliation is absent."""


class TreasuryOwnershipConflict(RuntimeError):
    """A confirmed treasury already belongs to another non-rejected operator."""


def _table_exists(conn, table: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone())


def ensure_schema(conn) -> None:
    """Create only the reconciliation ledger on the caller-owned connection."""
    execute_script(conn, DDL)


def _canonical_operator(conn) -> dict[str, Any]:
    row = conn.execute(
        "SELECT operator_id,display_name,status,confidence FROM operators "
        "WHERE operator_id=?",
        (WATCHTOWER_OPERATOR_ID,),
    ).fetchone()
    if not row:
        raise CanonicalWatchtowerMissingError(
            f"canonical WATCHTOWER operator {WATCHTOWER_OPERATOR_ID} is absent"
        )
    data = dict(row)
    if data.get("display_name") != WATCHTOWER_DISPLAY_NAME or data.get("status") != "CONFIRMED":
        raise CanonicalWatchtowerMissingError(
            "canonical WATCHTOWER must exist with display_name=WATCHTOWER and status=CONFIRMED"
        )
    return data


def _snapshot(row: dict[str, Any]) -> tuple[str, str]:
    evidence = {
        "treasury": row.get("treasury"),
        "method": row.get("method"),
        "confidence": row.get("confidence"),
        "provenance": row.get("provenance"),
        "confirmed_at": row.get("confirmed_at"),
    }
    encoded = json.dumps(evidence, sort_keys=True, separators=(",", ":"))
    return encoded, hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _source_row(conn, treasury: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM wt_confirmed_treasuries WHERE treasury=?", (treasury,)
    ).fetchone()
    return dict(row) if row else None


def reconcile_confirmed_treasury(
    conn,
    treasury: str,
    *,
    reconciled_at: int | None = None,
) -> dict[str, Any]:
    """Persist one evidence-bound membership for one confirmed treasury.

    No source row means no identity answer and no write.  Existing ownership by
    another active operator is a hard conflict; the function never duplicates
    ownership to make the invariant appear green.
    """
    ensure_schema(conn)
    operator = _canonical_operator(conn)
    source = _source_row(conn, treasury)
    if not source:
        return {
            "treasury": treasury,
            "operator_id": None,
            "status": "UNRESOLVED",
            "reason": "Treasury is absent from the authoritative confirmed registry.",
        }

    conflicts = conn.execute(
        "SELECT oe.operator_id,o.display_name,o.status FROM operator_entities oe "
        "JOIN operators o ON o.operator_id=oe.operator_id "
        "WHERE oe.entity_address=? AND oe.operator_id<>? AND o.status<>'REJECTED'",
        (treasury, WATCHTOWER_OPERATOR_ID),
    ).fetchall()
    if conflicts:
        owners = ", ".join(f"{r['display_name'] or r['operator_id']} ({r['status']})" for r in conflicts)
        raise TreasuryOwnershipConflict(
            f"confirmed WATCHTOWER treasury {treasury} already belongs to {owners}"
        )

    now = int(reconciled_at or time.time())
    before = conn.execute(
        "SELECT 1 FROM operator_entities WHERE operator_id=? AND entity_address=?",
        (WATCHTOWER_OPERATOR_ID, treasury),
    ).fetchone()
    conn.execute(
        "INSERT INTO operator_entities "
        "(operator_id,entity_address,entity_type,confidence,evidence_count,first_seen,last_seen,added_at) "
        "VALUES (?,?, 'TREASURY','HIGH',1,?,?,?) "
        "ON CONFLICT(operator_id,entity_address) DO NOTHING",
        (
            WATCHTOWER_OPERATOR_ID,
            treasury,
            source.get("confirmed_at"),
            source.get("confirmed_at"),
            now,
        ),
    )
    encoded, fingerprint = _snapshot(source)
    conn.execute(
        "INSERT INTO watchtower_identity_reconciliations "
        "(treasury,operator_id,source_table,source_provenance,source_confirmed_at,"
        " evidence_fingerprint,evidence_snapshot,reconciled_at) "
        "VALUES (?,?,'wt_confirmed_treasuries',?,?,?,?,?) "
        "ON CONFLICT(treasury) DO UPDATE SET "
        " operator_id=excluded.operator_id,"
        " source_provenance=excluded.source_provenance,"
        " source_confirmed_at=excluded.source_confirmed_at,"
        " evidence_fingerprint=excluded.evidence_fingerprint,"
        " evidence_snapshot=excluded.evidence_snapshot",
        (
            treasury,
            WATCHTOWER_OPERATOR_ID,
            source.get("provenance"),
            source.get("confirmed_at"),
            fingerprint,
            encoded,
            now,
        ),
    )
    return {
        "treasury": treasury,
        "operator_id": operator["operator_id"],
        "operator_name": operator["display_name"],
        "status": "ALIGNED" if before else "RECONCILED",
        "evidence_fingerprint": fingerprint,
    }


def reconcile_all_confirmed_treasuries(conn) -> dict[str, Any]:
    """Reconcile the complete authoritative registry in one transaction."""
    ensure_schema(conn)
    _canonical_operator(conn)
    rows = conn.execute(
        "SELECT treasury FROM wt_confirmed_treasuries ORDER BY treasury"
    ).fetchall()
    results = [reconcile_confirmed_treasury(conn, row["treasury"]) for row in rows]
    audit = audit_alignment(conn)
    return {
        "total": len(results),
        "reconciled": sum(r["status"] == "RECONCILED" for r in results),
        "already_aligned": sum(r["status"] == "ALIGNED" for r in results),
        "audit": audit,
    }


def audit_alignment(conn) -> dict[str, Any]:
    """Return exact ownership coverage without mutating the database."""
    confirmed = conn.execute("SELECT COUNT(*) FROM wt_confirmed_treasuries").fetchone()[0]
    canonical = conn.execute(
        "SELECT COUNT(*) FROM wt_confirmed_treasuries t JOIN operator_entities oe "
        "ON oe.entity_address=t.treasury AND oe.operator_id=?",
        (WATCHTOWER_OPERATOR_ID,),
    ).fetchone()[0]
    duplicates = conn.execute(
        "SELECT COUNT(*) FROM ("
        " SELECT t.treasury FROM wt_confirmed_treasuries t "
        " JOIN operator_entities oe ON oe.entity_address=t.treasury "
        " JOIN operators o ON o.operator_id=oe.operator_id AND o.status<>'REJECTED' "
        " GROUP BY t.treasury HAVING COUNT(DISTINCT oe.operator_id)<>1"
        ")"
    ).fetchone()[0]
    ledger = 0
    if _table_exists(conn, "watchtower_identity_reconciliations"):
        ledger = conn.execute(
            "SELECT COUNT(*) FROM watchtower_identity_reconciliations WHERE operator_id=?",
            (WATCHTOWER_OPERATOR_ID,),
        ).fetchone()[0]
    return {
        "confirmed_treasuries": confirmed,
        "canonical_watchtower": canonical,
        "unresolved": confirmed - canonical,
        "duplicate_ownership": duplicates,
        "ledger_rows": ledger,
        "healthy": confirmed == canonical == ledger and duplicates == 0,
    }


def initialize_and_reconcile(db_path: str, *, write_service: Any = None) -> dict[str, Any]:
    """Run startup reconciliation through the shared operations write lane."""
    writer = OperatorWriter(db_path, write_service=write_service)
    return writer.transaction(
        "watchtower-canonical-reconciliation", reconcile_all_confirmed_treasuries
    )
