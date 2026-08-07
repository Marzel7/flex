import sqlite3

from src.ops.lineage_quarantine import (
    eligible_session_relation,
    ensure_lineage_quarantine_schema,
    is_session_quarantined,
)


def _db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE wt_active_subprov_sessions (
            id INTEGER PRIMARY KEY, subprov_wallet TEXT NOT NULL,
            treasury_wallet TEXT, funding_signature TEXT, funding_amount REAL,
            funding_time INTEGER, state TEXT, detected_at INTEGER
        )
    """)
    ensure_lineage_quarantine_schema(conn)
    return conn


def _quarantine(conn, session_id, evidence_class="C_INHERITED_SESSION_ONLY"):
    conn.execute("""
        INSERT INTO wt_lineage_quarantine
          (quarantine_id,source_table,source_row_id,subject_wallet,related_wallet,
           signature,evidence_class,quarantine_reason,evidence_source,evidence_json,
           exclude_from_tier1,quarantined_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,1,?)
    """, (f"q{session_id}", "wt_active_subprov_sessions", session_id, "69SN",
          "5tzF", f"sig{session_id}", evidence_class, "audit", "X78", "{}", 1))


def test_quarantine_preserves_raw_history_but_excludes_tier1_lineage():
    conn = _db()
    conn.execute("INSERT INTO wt_active_subprov_sessions VALUES (1,'5tzF','69SN','sig',1,1,'EXPIRED',2)")
    _quarantine(conn, 1)
    assert conn.execute("SELECT count(*) FROM wt_active_subprov_sessions").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM wt_lineage_eligible_sessions").fetchone()[0] == 0
    assert is_session_quarantined(conn, 1)


def test_valid_indirect_ancestry_is_context_not_a_flattened_direct_edge():
    conn = _db()
    conn.execute("INSERT INTO wt_active_subprov_sessions VALUES (176429,'5tzF','69SN','sig',1,1,'EXPIRED',2)")
    _quarantine(conn, 176429, "B_INDIRECT_TRANSACTION_PROVEN")
    row = conn.execute("SELECT evidence_class,evidence_json FROM wt_lineage_quarantine").fetchone()
    assert row["evidence_class"] == "B_INDIRECT_TRANSACTION_PROVEN"
    assert conn.execute("SELECT count(*) FROM wt_lineage_eligible_sessions").fetchone()[0] == 0


def test_unrelated_sessions_remain_eligible():
    conn = _db()
    conn.executemany("INSERT INTO wt_active_subprov_sessions VALUES (?,?,?,?,?,?,?,?)", [
        (1, "5tzF", "69SN", "bad", 1, 1, "EXPIRED", 2),
        (2, "OTHER_CHILD", "OTHER_ROOT", "good", 1, 1, "ACTIVE", 2),
    ])
    _quarantine(conn, 1)
    relation = eligible_session_relation(conn)
    rows = conn.execute(f"SELECT id FROM {relation} ORDER BY id").fetchall()
    assert [row[0] for row in rows] == [2]


def test_schema_and_quarantine_are_idempotent():
    conn = _db()
    ensure_lineage_quarantine_schema(conn)
    ensure_lineage_quarantine_schema(conn)
    assert eligible_session_relation(conn) == "wt_lineage_eligible_sessions"


def test_root_policy_fails_closed_for_new_sessions_until_direct_edge_is_verified():
    conn = _db()
    conn.execute("INSERT INTO wt_lineage_root_policies VALUES ('69SN',1,'audit','X78',1)")
    conn.execute("INSERT INTO wt_active_subprov_sessions VALUES (9,'NEW','69SN','sig9',1,1,'ACTIVE',2)")
    assert conn.execute("SELECT count(*) FROM wt_lineage_eligible_sessions").fetchone()[0] == 0
    conn.execute("INSERT INTO wt_lineage_verified_session_edges VALUES (9,'69SN','NEW','sig9','DIRECT_SOL_TRANSFER','RPC',3)")
    assert conn.execute("SELECT count(*) FROM wt_lineage_eligible_sessions").fetchone()[0] == 1
