"""
AttributionEvidence — the canonical append-only ledger for treasury-attribution
decisions (X41.0, implementing the frozen model from X39.0/X40.0).

THIS MODULE IS ADDITIVE AND SHADOW-ONLY. It never becomes authoritative in this
phase: wt_confirmed_treasuries remains the source of truth for every existing
reader. Every write to this ledger happens via record_evidence(), which is a
strict dual-write helper — it is called *after* an existing confirmation write
already succeeded, and its own failure is caught, logged, and swallowed so it
can never interrupt or roll back the existing (authoritative) write.

Frozen architectural rules this module must not violate (X40.0/X41.0):
  - Evidence is append-only. No UPDATE/DELETE path exists here except the
    narrow superseded_event_id backlink, which does not mutate prior rows.
  - Confidence axes remain independent — confidence_axis + confidence_value
    are stored together, never coerced into one shared scale.
  - This ledger does not replace wt_treasury_fingerprint_decisions or
    wt_treasury_approval_audit — those remain untouched and authoritative
    for their existing readers. This ledger is a superset going forward.
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

logger = logging.getLogger("attribution_evidence")

OPS_DB_PATH = os.path.abspath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "database", "wt_ops_v2.db"))

# Event types — see X40.0 Phase 2 for the full contract this schema implements.
EVENT_TYPES = (
    "FINGERPRINT_EVALUATION",
    "LAUNCH_CHAIN_CONFIRMATION",
    "MANUAL_APPROVAL",
    "MANUAL_REJECTION",
    "RPC_VERIFIED_TRACE",
    "SUBPROV_FUNDER_LINK",
    "REVERSION",
    "ROLE_CHANGE",
)

_SCHEMA_READY = False


def ensure_schema(conn) -> None:
    """Idempotent, IF NOT EXISTS only. Never touches any existing table."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS attribution_evidence (
            event_id             INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type           TEXT NOT NULL,
            subject_wallet       TEXT NOT NULL,
            claimed_role         TEXT,
            decision             TEXT,
            evidence_refs_json   TEXT,
            method               TEXT,
            actor_or_process     TEXT,
            timestamp            INTEGER NOT NULL,
            source_pipeline      TEXT,
            confidence_axis      TEXT,
            confidence_value     TEXT,
            superseded_event_id  INTEGER,
            reconstructed        INTEGER NOT NULL DEFAULT 0,
            reconstruction_source TEXT,
            reconstruction_confidence TEXT,
            timestamp_quality    TEXT NOT NULL DEFAULT 'EXACT',
            created_at           INTEGER NOT NULL
        )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_attrev_wallet ON attribution_evidence(subject_wallet)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_attrev_type ON attribution_evidence(event_type)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_attrev_reconstructed ON attribution_evidence(reconstructed)"
    )
    # write-failure log — so a swallowed dual-write failure is still observable,
    # per X41.0's "log the failure, queue reconciliation if required" requirement.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS attribution_evidence_write_failures (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            subject_wallet TEXT,
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


def record_evidence(
    conn,
    *,
    event_type: str,
    subject_wallet: str,
    claimed_role: Optional[str] = None,
    decision: Optional[str] = None,
    evidence_refs: Optional[dict] = None,
    method: Optional[str] = None,
    actor_or_process: Optional[str] = None,
    timestamp: Optional[int] = None,
    source_pipeline: Optional[str] = None,
    confidence_axis: Optional[str] = None,
    confidence_value: Optional[str] = None,
    superseded_event_id: Optional[int] = None,
    reconstructed: bool = False,
    reconstruction_source: Optional[str] = None,
    reconstruction_confidence: Optional[str] = None,
    timestamp_quality: str = "EXACT",
) -> Optional[int]:
    """Append one AttributionEvidence row. NEVER RAISES.

    This is the strict dual-write contract from X41.0: this function must be
    called only AFTER the existing (authoritative) write already committed
    successfully. If this write itself fails for any reason, the failure is
    logged to attribution_evidence_write_failures (best-effort) and the
    exception is swallowed — the caller's existing treasury-confirmation flow
    must never be interrupted by this ledger.

    Returns the new event_id on success, or None if the write failed.
    """
    if event_type not in EVENT_TYPES:
        # Do not raise — an unrecognised event_type is a caller bug we still
        # must not let interrupt production. Log and record under 'UNKNOWN'.
        logger.warning("attribution_evidence: unknown event_type %r for wallet %s",
                        event_type, subject_wallet)
    now = int(time.time())
    ts = timestamp if timestamp is not None else now
    try:
        _ensure_schema_once(conn)
        cur = conn.execute(
            """INSERT INTO attribution_evidence
                 (event_type, subject_wallet, claimed_role, decision, evidence_refs_json,
                  method, actor_or_process, timestamp, source_pipeline,
                  confidence_axis, confidence_value, superseded_event_id,
                  reconstructed, reconstruction_source, reconstruction_confidence,
                  timestamp_quality, created_at)
               VALUES (?,?,?,?,?, ?,?,?,?, ?,?,?, ?,?,?, ?,?)""",
            (event_type, subject_wallet, claimed_role, decision,
             json.dumps(evidence_refs) if evidence_refs is not None else None,
             method, actor_or_process, ts, source_pipeline,
             confidence_axis, confidence_value, superseded_event_id,
             1 if reconstructed else 0, reconstruction_source, reconstruction_confidence,
             timestamp_quality, now),
        )
        conn.commit()
        return cur.lastrowid
    except Exception as exc:                          # pragma: no cover - defensive
        logger.error("attribution_evidence write failed for %s (%s): %s",
                     subject_wallet, event_type, exc)
        try:
            # best-effort failure log on a FRESH connection — the caller's conn may
            # be in a bad state after the exception above, and we must not risk
            # raising again or touching the caller's transaction.
            fail_conn = db_connect(OPS_DB_PATH, timeout=5)
            try:
                ensure_schema(fail_conn)
                fail_conn.execute(
                    """INSERT INTO attribution_evidence_write_failures
                         (subject_wallet, event_type, error, payload_json, occurred_at)
                       VALUES (?,?,?,?,?)""",
                    (subject_wallet, event_type, str(exc),
                     json.dumps({"claimed_role": claimed_role, "decision": decision,
                                 "method": method}), now),
                )
                fail_conn.commit()
            finally:
                fail_conn.close()
        except Exception:                              # pragma: no cover - defensive
            logger.error("attribution_evidence: failure log itself failed for %s", subject_wallet)
        return None
