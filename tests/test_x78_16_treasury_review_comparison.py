import sqlite3
from pathlib import Path

from src.ops.treasury_review_workspace import _operation_matches
from src.ops.watchtower_alignment import WATCHTOWER_OPERATOR_ID


ROOT = Path(__file__).resolve().parents[1]
SW2_ID = "64527dc2-8073-50c0-8bd7-7ef49e62d875"


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
    CREATE TABLE operators(operator_id TEXT,display_name TEXT,status TEXT);
    CREATE TABLE operator_entities(operator_id TEXT,entity_address TEXT,entity_type TEXT);
    CREATE TABLE wt_provisioning_edges(
      edge_type TEXT,from_wallet TEXT,to_wallet TEXT,source_mint TEXT,
      funding_mechanism TEXT,funding_tx_signature TEXT
    );
    CREATE TABLE wt_watchtower_launches(
      mint TEXT,subprov_wallet TEXT,funding_mechanism TEXT
    );
    """)
    conn.execute("INSERT INTO operators VALUES (?,'WATCHTOWER','CONFIRMED')", (WATCHTOWER_OPERATOR_ID,))
    conn.execute("INSERT INTO operators VALUES (?,'3SW2','CONFIRMED')", (SW2_ID,))
    conn.execute("INSERT INTO operator_entities VALUES (?,'watch-treasury','TREASURY')", (WATCHTOWER_OPERATOR_ID,))
    conn.execute("INSERT INTO operator_entities VALUES (?,'3sw2-client','CLIENT')", (SW2_ID,))
    for index in range(4):
        conn.execute("INSERT INTO wt_watchtower_launches VALUES (?,?,?)",
                     (f'wm{index}', f'wsub{index}', 'WSOL_WRAP_CLOSE'))
    conn.execute("INSERT INTO wt_provisioning_edges VALUES ('SUBPROV_TO_CREATOR','3sw2-client','c','sm','PLAIN_XFER','ssig')")
    return conn


def test_clean_existing_evidence_produces_evaluated_dimensions():
    conn = _conn()
    for index in range(3):
        conn.execute("INSERT INTO wt_provisioning_edges VALUES ('TREASURY_TO_SUBPROV','candidate',?,?,?,?)",
                     (f'sub{index}', f'm{index}', 'WSOL_WRAP_CLOSE', f'sig{index}'))
    matches = _operation_matches(
        conn, "candidate", ["sub0", "sub1", "sub2"],
        ["c0", "c1", "c2"], ["m0", "m1", "m2"], {"launches": 3},
    )
    watch = next(match for match in matches if match["display_name"] == "WATCHTOWER")
    assert watch["states"]["Behaviour"] == "MATCH"
    assert watch["states"]["Funding"] == "MATCH"
    assert watch["states"]["Provisioning"] == "MATCH"
    assert watch["states"]["Topology"] == "MATCH"
    assert watch["states"]["Settlement"] == "UNKNOWN"
    assert watch["states"]["Treasury"] == "UNKNOWN"
    assert watch["comparison_state"] == "PARTIAL"
    assert watch["matched"] is False  # similarity never becomes identity overlap


def test_all_unknown_is_not_no_match():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
    CREATE TABLE operators(operator_id TEXT,display_name TEXT,status TEXT);
    CREATE TABLE operator_entities(operator_id TEXT,entity_address TEXT,entity_type TEXT);
    INSERT INTO operators VALUES ('generic','Generic','CONFIRMED');
    """)
    result = _operation_matches(conn, "candidate", [], [], [], {"launches": 0})[0]
    assert set(result["states"].values()) == {"UNKNOWN"}
    assert result["comparison_state"] == "NOT_EVALUATED"


def test_ui_uses_actionable_pagination_and_compact_unknown_copy():
    page = (ROOT / "templates/treasury_review.html").read_text()
    assert "Actionable first · newest within group" in page
    assert "No confirmed Operation comparison could be evaluated" in page
    assert "Load 20 more" in page
    assert "m.comparison_state==='NOT_EVALUATED'" in page
    assert "No match')" not in page
