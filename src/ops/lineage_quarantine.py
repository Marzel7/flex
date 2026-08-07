"""Selective exclusion of forensic session rows from Tier-1 lineage.

Raw session history remains untouched.  Readers which need directional ancestry
must use :func:`eligible_session_relation`; lifecycle/monitoring readers may
continue to inspect the historical table directly.
"""

from __future__ import annotations

import sqlite3


QUARANTINE_TABLE = "wt_lineage_quarantine"
ELIGIBLE_VIEW = "wt_lineage_eligible_sessions"


def ensure_lineage_quarantine_schema(conn: sqlite3.Connection) -> None:
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {QUARANTINE_TABLE} (
            quarantine_id TEXT PRIMARY KEY,
            source_table TEXT NOT NULL,
            source_row_id INTEGER NOT NULL,
            subject_wallet TEXT,
            related_wallet TEXT,
            signature TEXT,
            evidence_class TEXT NOT NULL,
            quarantine_reason TEXT NOT NULL,
            evidence_source TEXT NOT NULL,
            evidence_json TEXT NOT NULL DEFAULT '{{}}',
            exclude_from_tier1 INTEGER NOT NULL DEFAULT 1,
            quarantined_at INTEGER NOT NULL,
            UNIQUE(source_table, source_row_id)
        )
    """)
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS ix_lineage_quarantine_subject "
        f"ON {QUARANTINE_TABLE}(subject_wallet, exclude_from_tier1)"
    )
    conn.execute("""
        CREATE TABLE IF NOT EXISTS wt_lineage_root_policies (
            subject_wallet TEXT PRIMARY KEY,
            require_explicit_tier1 INTEGER NOT NULL DEFAULT 1,
            policy_reason TEXT NOT NULL,
            evidence_source TEXT NOT NULL,
            updated_at INTEGER NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS wt_lineage_verified_session_edges (
            session_id INTEGER PRIMARY KEY,
            sender TEXT NOT NULL,
            recipient TEXT NOT NULL,
            signature TEXT NOT NULL,
            relationship_type TEXT NOT NULL,
            evidence_source TEXT NOT NULL,
            verified_at INTEGER NOT NULL
        )
    """)
    conn.execute(f"DROP VIEW IF EXISTS {ELIGIBLE_VIEW}")
    conn.execute(f"""
        CREATE VIEW {ELIGIBLE_VIEW} AS
        SELECT sessions.*
          FROM wt_active_subprov_sessions sessions
         WHERE NOT EXISTS (
             SELECT 1 FROM {QUARANTINE_TABLE} quarantine
              WHERE quarantine.source_table='wt_active_subprov_sessions'
                AND quarantine.source_row_id=sessions.id
                AND quarantine.exclude_from_tier1=1
         )
           AND EXISTS (
               SELECT 1 FROM wt_lineage_verified_session_edges verified
                WHERE verified.session_id=sessions.id
                  AND verified.sender=sessions.treasury_wallet
                  AND verified.recipient=sessions.subprov_wallet
                  AND verified.signature=sessions.funding_signature
         )
    """)


def record_verified_session_edge(
    conn: sqlite3.Connection,
    *,
    session_id: int,
    sender: str,
    recipient: str,
    signature: str,
    relationship_type: str = "DIRECT_SOL_TRANSFER",
    evidence_source: str = "LIVE_DIRECTIONAL_TRANSACTION",
    verified_at: int,
) -> None:
    """Admit one exact transaction-proven session edge to Tier-1 lineage."""
    conn.execute(
        """
        INSERT INTO wt_lineage_verified_session_edges
          (session_id,sender,recipient,signature,relationship_type,evidence_source,verified_at)
        VALUES (?,?,?,?,?,?,?)
        ON CONFLICT(session_id) DO UPDATE SET
          sender=excluded.sender,
          recipient=excluded.recipient,
          signature=excluded.signature,
          relationship_type=excluded.relationship_type,
          evidence_source=excluded.evidence_source,
          verified_at=excluded.verified_at
        """,
        (session_id, sender, recipient, signature, relationship_type,
         evidence_source, int(verified_at)),
    )


def eligible_session_relation(conn: sqlite3.Connection) -> str:
    """Return the safe relation, falling back for pre-migration test fixtures."""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?",
        (ELIGIBLE_VIEW,),
    ).fetchone()
    return ELIGIBLE_VIEW if row else "wt_active_subprov_sessions"


def is_session_quarantined(conn: sqlite3.Connection, session_id: int) -> bool:
    try:
        return bool(conn.execute(
            f"SELECT 1 FROM {QUARANTINE_TABLE} WHERE source_table=? "
            "AND source_row_id=? AND exclude_from_tier1=1",
            ("wt_active_subprov_sessions", session_id),
        ).fetchone())
    except sqlite3.OperationalError:
        return False
