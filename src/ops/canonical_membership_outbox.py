"""Forward-only, transaction-local canonical membership transition outbox.

This module is deliberately small: writers call ``append_transition`` on the
same SQLite connection immediately after a semantic membership mutation.  It
never commits; the caller owns the transaction.
"""
from __future__ import annotations
import sqlite3

VERSION = "CANONICAL_MEMBERSHIP_OUTBOX_V1"
TABLE = "canonical_membership_outbox"
MAX_ROWS = 12000

DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE}(
 outbox_id INTEGER PRIMARY KEY AUTOINCREMENT,
 event_type TEXT NOT NULL CHECK(event_type IN ('MEMBERSHIP_ASSIGNED','MEMBERSHIP_REASSIGNED','MEMBERSHIP_REMOVED')),
 mint TEXT NOT NULL, operator_id TEXT, canonical_event_id TEXT,
 assigned_at INTEGER, previous_operator_id TEXT,
 writer_identity TEXT NOT NULL, created_at INTEGER NOT NULL,
 schema_version TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_canonical_membership_outbox_forward ON {TABLE}(outbox_id);
CREATE TRIGGER IF NOT EXISTS canonical_membership_outbox_immutable_update
BEFORE UPDATE ON {TABLE} BEGIN SELECT RAISE(ABORT,'canonical membership outbox is immutable'); END;
CREATE TRIGGER IF NOT EXISTS canonical_membership_outbox_immutable_delete
BEFORE DELETE ON {TABLE} BEGIN SELECT RAISE(ABORT,'canonical membership outbox is immutable'); END;
"""

def ensure_schema(conn: sqlite3.Connection) -> None:
    # ``executescript`` commits any caller-owned transaction first.  These
    # individual DDL statements therefore preserve same-transaction atomicity.
    conn.execute(f'''CREATE TABLE IF NOT EXISTS {TABLE}(outbox_id INTEGER PRIMARY KEY AUTOINCREMENT,event_type TEXT NOT NULL CHECK(event_type IN ('MEMBERSHIP_ASSIGNED','MEMBERSHIP_REASSIGNED','MEMBERSHIP_REMOVED')),mint TEXT NOT NULL,operator_id TEXT,canonical_event_id TEXT,assigned_at INTEGER,previous_operator_id TEXT,writer_identity TEXT NOT NULL,created_at INTEGER NOT NULL,schema_version TEXT NOT NULL)''')
    conn.execute(f'CREATE INDEX IF NOT EXISTS ix_canonical_membership_outbox_forward ON {TABLE}(outbox_id)')
    conn.execute(f'''CREATE TRIGGER IF NOT EXISTS canonical_membership_outbox_immutable_update BEFORE UPDATE ON {TABLE} BEGIN SELECT RAISE(ABORT,'canonical membership outbox is immutable'); END''')
    conn.execute(f'''CREATE TRIGGER IF NOT EXISTS canonical_membership_outbox_immutable_delete BEFORE DELETE ON {TABLE} BEGIN SELECT RAISE(ABORT,'canonical membership outbox is immutable'); END''')

def append_transition(conn: sqlite3.Connection, *, event_type: str, mint: str,
                      operator_id: str | None, canonical_event_id: str | None,
                      assigned_at: int | None, previous_operator_id: str | None,
                      writer_identity: str, created_at: int) -> int:
    """Append one compact event without committing; returns its total-order id."""
    ensure_schema(conn)
    if conn.execute(f"SELECT count(*) FROM {TABLE}").fetchone()[0] >= MAX_ROWS:
        raise RuntimeError("CANONICAL_MEMBERSHIP_OUTBOX_STORAGE_LIMIT")
    cur = conn.execute(
        f"INSERT INTO {TABLE}(event_type,mint,operator_id,canonical_event_id,assigned_at,previous_operator_id,writer_identity,created_at,schema_version) VALUES(?,?,?,?,?,?,?,?,?)",
        (event_type,mint,operator_id,canonical_event_id,assigned_at,previous_operator_id,writer_identity,int(created_at),VERSION),
    )
    return int(cur.lastrowid)

def read_after(conn: sqlite3.Connection, last_consumed_outbox_id: int, limit: int = 64):
    if limit < 1 or limit > 256: raise ValueError("OUTBOX_BATCH_LIMIT")
    return conn.execute(f"SELECT outbox_id,event_type,mint,operator_id,canonical_event_id,assigned_at,previous_operator_id,writer_identity,created_at,schema_version FROM {TABLE} WHERE outbox_id>? ORDER BY outbox_id ASC LIMIT ?",(int(last_consumed_outbox_id),int(limit))).fetchall()
