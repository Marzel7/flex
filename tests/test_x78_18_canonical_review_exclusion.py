import sqlite3
from pathlib import Path

import pytest

from src.ops.treasury_review_workspace import (
    WorkspaceError,
    compose_review_item,
    ensure_schema,
    list_review_workspace,
    perform_action,
)
from src.ops.watchtower_alignment import WATCHTOWER_OPERATOR_ID


ROOT = Path(__file__).resolve().parents[1]


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
    CREATE TABLE wt_treasury_review(
      treasury TEXT PRIMARY KEY,transfer_pct INTEGER,out_sol REAL,recipients INTEGER,
      micro_pings INTEGER,detected_via TEXT,status TEXT,reviewed_by TEXT,
      detected_at INTEGER,reviewed_at INTEGER,evidence_subprovs TEXT,
      evidence_creators TEXT,evidence_mints TEXT,has_walkback_evidence INTEGER,
      last_walkback_at INTEGER,distinct_creators INTEGER
    );
    CREATE TABLE wt_confirmed_treasuries(
      treasury TEXT PRIMARY KEY,transfer_pct INTEGER,out_sol REAL,recipients INTEGER,
      micro_pings INTEGER,method TEXT,confidence TEXT,confirmed_at INTEGER,
      provenance TEXT,no_subscribe INTEGER
    );
    CREATE TABLE operators(operator_id TEXT,display_name TEXT,status TEXT);
    CREATE TABLE operator_entities(
      operator_id TEXT,entity_address TEXT,entity_type TEXT,confidence TEXT,
      evidence_count INTEGER,first_seen INTEGER,last_seen INTEGER,added_at INTEGER
    );
    CREATE TABLE wt_discovered_subprovs(subprov TEXT,treasury TEXT);
    CREATE TABLE wt_wrap_close_candidates(
      creator TEXT,subprov_wallet TEXT,tx_signature TEXT,funding_mechanism TEXT,
      funded_at INTEGER,detected_at INTEGER,lineage_source_treasury TEXT
    );
    """)
    ensure_schema(conn)
    conn.execute("INSERT INTO operators VALUES (?,'WATCHTOWER','CONFIRMED')", (WATCHTOWER_OPERATOR_ID,))
    return conn


def _review(conn, treasury):
    conn.execute(
        "INSERT INTO wt_treasury_review(treasury,status,detected_at,detected_via,"
        "evidence_subprovs,evidence_creators,evidence_mints,has_walkback_evidence) "
        "VALUES (?,'PENDING_REVIEW',100,'walkback_hop2','[]','[]','[]',1)",
        (treasury,),
    )


def test_pending_queue_excludes_direct_canonical_identity_without_mutating_history():
    conn = _conn()
    _review(conn, "known")
    _review(conn, "unresolved")
    conn.execute("INSERT INTO wt_confirmed_treasuries(treasury,confirmed_at) VALUES ('known',200)")
    conn.execute(
        "INSERT INTO operator_entities VALUES (?,?, 'TREASURY','HIGH',1,200,200,200)",
        (WATCHTOWER_OPERATOR_ID, "known"),
    )
    result = list_review_workspace(conn, limit=20)
    assert [item["treasury"] for item in result["items"]] == ["unresolved"]
    assert result["pending_review_rows"] == 2
    assert result["pending_total"] == 1
    assert result["excluded_canonical_total"] == 1
    assert result["excluded_canonical_by_operation"] == {"WATCHTOWER": 1}
    # Dynamic projection preserves the historical mutable row exactly.
    assert conn.execute("SELECT status FROM wt_treasury_review WHERE treasury='known'").fetchone()[0] == "PENDING_REVIEW"


def test_direct_detail_marks_known_identity_resolved_and_offers_no_action():
    conn = _conn()
    _review(conn, "known")
    conn.execute("INSERT INTO wt_confirmed_treasuries(treasury,confirmed_at) VALUES ('known',200)")
    row = dict(conn.execute("SELECT * FROM wt_treasury_review WHERE treasury='known'").fetchone())
    item = compose_review_item(conn, row)
    assert item["governance_state"] == "ALREADY_CANONICAL"
    assert item["recommended_action"]["action"] is None
    assert item["recommended_action"]["label"] == "Known WATCHTOWER Treasury"


def test_canonical_identity_cannot_receive_a_second_governance_action():
    conn = _conn()
    _review(conn, "known")
    conn.execute("INSERT INTO wt_confirmed_treasuries(treasury,confirmed_at) VALUES ('known',200)")
    with pytest.raises(WorkspaceError) as exc:
        perform_action(conn, "known", "LINK_TO_OPERATOR", {
            "analyst": "analyst", "reason": "duplicate", "operator_id": WATCHTOWER_OPERATOR_ID,
        })
    assert exc.value.code == "ALREADY_CANONICAL"


def test_downstream_overlap_does_not_exclude_unresolved_treasury():
    conn = _conn()
    _review(conn, "candidate")
    conn.execute(
        "INSERT INTO operator_entities VALUES (?,?, 'CLIENT','HIGH',1,200,200,200)",
        (WATCHTOWER_OPERATOR_ID, "downstream"),
    )
    conn.execute(
        "UPDATE wt_treasury_review SET evidence_subprovs='[\"downstream\"]' WHERE treasury='candidate'"
    )
    result = list_review_workspace(conn, limit=20)
    assert [item["treasury"] for item in result["items"]] == ["candidate"]
    assert result["excluded_canonical_total"] == 0


def test_historical_known_identity_detail_has_no_governance_controls():
    page = (ROOT / "templates/treasury_review.html").read_text()
    assert "item.governance_state==='ALREADY_CANONICAL'" in page
    assert "Already canonical" in page
    assert "resolved?[]" in page
