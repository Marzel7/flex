import sqlite3

from src.ops.lineage_quarantine import (
    ensure_lineage_quarantine_schema,
    record_verified_session_edge,
)


def _db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE wt_active_subprov_sessions (
            id INTEGER PRIMARY KEY, subprov_wallet TEXT,
            treasury_wallet TEXT, funding_signature TEXT
        )
    """)
    ensure_lineage_quarantine_schema(conn)
    return conn


def test_exact_transaction_verified_session_is_tier1_eligible():
    conn = _db()
    conn.execute("INSERT INTO wt_active_subprov_sessions VALUES (1,'child','sender','sig')")
    record_verified_session_edge(
        conn,
        session_id=1,
        sender="sender",
        recipient="child",
        signature="sig",
        verified_at=100,
    )
    row = conn.execute("SELECT * FROM wt_lineage_eligible_sessions").fetchone()
    assert (row["treasury_wallet"], row["subprov_wallet"], row["funding_signature"]) == (
        "sender", "child", "sig"
    )


def test_unverified_session_is_retained_but_not_tier1_eligible():
    conn = _db()
    conn.execute("INSERT INTO wt_active_subprov_sessions VALUES (1,'child','inherited-root','sig')")
    assert conn.execute("SELECT COUNT(*) FROM wt_active_subprov_sessions").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM wt_lineage_eligible_sessions").fetchone()[0] == 0
