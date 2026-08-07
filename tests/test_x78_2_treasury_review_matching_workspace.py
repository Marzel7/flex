import sqlite3
from pathlib import Path

from src.ops.treasury_review_workspace import compose_review_item, ensure_schema
from src.ops.watchtower_alignment import WATCHTOWER_OPERATOR_ID


ROOT = Path(__file__).resolve().parents[1]


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
    CREATE TABLE wt_treasury_review (
      treasury TEXT PRIMARY KEY, status TEXT, detected_at INTEGER,
      detected_via TEXT, has_walkback_evidence INTEGER, last_walkback_at INTEGER,
      evidence_subprovs TEXT, evidence_creators TEXT, evidence_mints TEXT,
      transfer_pct INTEGER, out_sol REAL, recipients INTEGER, micro_pings INTEGER,
      distinct_creators INTEGER
    );
    CREATE TABLE wt_wrap_close_candidates (
      creator TEXT, subprov_wallet TEXT, tx_signature TEXT, funding_mechanism TEXT,
      funded_at INTEGER, detected_at INTEGER, lineage_source_treasury TEXT
    );
    CREATE TABLE wt_discovered_subprovs (subprov TEXT, treasury TEXT);
    CREATE TABLE operators (operator_id TEXT, display_name TEXT, status TEXT);
    CREATE TABLE operator_entities (operator_id TEXT, entity_address TEXT);
    """)
    ensure_schema(conn)
    return conn


def test_review_projection_explains_topology_match_and_recommendation():
    conn = _conn()
    sw2 = "64527dc2-8073-50c0-8bd7-7ef49e62d875"
    conn.execute("INSERT INTO operators VALUES (?,'WATCHTOWER','CONFIRMED')", (WATCHTOWER_OPERATOR_ID,))
    conn.execute("INSERT INTO operators VALUES (?,'3SW2','CONFIRMED')", (sw2,))
    conn.execute("INSERT INTO operator_entities VALUES (?,'sub-1')", (WATCHTOWER_OPERATOR_ID,))
    conn.execute("INSERT INTO wt_discovered_subprovs VALUES ('sub-1','treasury-1')")
    conn.execute("INSERT INTO wt_wrap_close_candidates VALUES ('creator-1','sub-1','sig-1','WSOL_WRAP_CLOSE',200,200,'treasury-1')")
    row = {
        "treasury": "treasury-1", "status": "PENDING_REVIEW", "detected_at": 100,
        "detected_via": "walkback_hop2", "has_walkback_evidence": 1, "last_walkback_at": 200,
        "evidence_subprovs": '["sub-1"]', "evidence_creators": '["creator-1"]',
        "evidence_mints": '["mint-1"]', "transfer_pct": 100, "out_sol": 100,
        "recipients": 1, "micro_pings": 0, "distinct_creators": 1,
    }
    item = compose_review_item(conn, row)
    assert item["observed_topology"]["label"] == "Treasury → Subprovider / Provisioning Wallet → Creator → Launch"
    assert item["operation_matches"][0]["display_name"] == "WATCHTOWER"
    assert item["operation_matches"][0]["states"]["Provisioning"] == "MATCH"
    sw2_match = next(match for match in item["operation_matches"] if match["display_name"] == "3SW2")
    assert sw2_match["operator_href"] == f"/intelligence/operator/{sw2}"
    assert sw2_match["matched"] is False
    assert sw2_match["comparison_state"] in {"PARTIAL", "NO_MATCH"}
    assert item["recommended_action"]["label"] == "Expand WATCHTOWER"
    assert item["relationship_examples"][0]["transaction"] == "sig-1"
    assert "why_surfaced" not in item


def test_workspace_keeps_governance_but_collapses_raw_evidence():
    html = (ROOT / "templates/treasury_review.html").read_text()
    for label in ("Best Operation comparison", "Recommended",
                  "Supporting evidence", "Representative Relationships"):
        assert label in html
    assert "Why this surfaced" not in html
    assert "Investigation Trigger" not in html
    for action in ("APPROVE_TREASURY", "LINK_TO_OPERATOR", "CREATE_OPERATOR_CANDIDATE",
                   "CREATE_INVESTIGATION", "NEEDS_MORE_EVIDENCE", "REJECT_TREASURY"):
        assert action in html
    assert '<details class="tr-support">' in html
    assert "solscan.io/account/" in html
    assert "solscan.io/tx/" in html
