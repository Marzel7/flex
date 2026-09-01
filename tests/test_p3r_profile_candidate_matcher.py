import json
import sqlite3

from src.ops.p3r_profile_candidate_matcher import evaluate_mint


def _db():
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE operators (operator_id TEXT, display_name TEXT, status TEXT DEFAULT 'CONFIRMED');
        CREATE TABLE operation_registry_dispositions (operator_id TEXT, disposition TEXT);
        CREATE TABLE operation_behavioural_profiles (operator_id TEXT, provenance_json TEXT);
        CREATE TABLE wt_walkback_edge_candidates (mint TEXT, hop_depth INTEGER, mechanism TEXT, amount_lamports INTEGER, selection_status TEXT);
        CREATE TABLE wt_walkback_atomic_flows (mint TEXT, has_create INTEGER, has_sync_native INTEGER, has_close INTEGER, transfer_lamports INTEGER);
    """)
    profiles = [
        ("13", "P3R_13A04", {"funding_ladder_lamports": [29999995000, 29999990000, 29999985000, 29999980000, 29999975000]}),
        ("af", "P3R_AF500", {}),
        ("ec", "P3R_EC1", {}),
    ]
    for operator_id, name, provenance in profiles:
        conn.execute("INSERT INTO operators (operator_id, display_name) VALUES (?,?)", (operator_id, name))
        conn.execute("INSERT INTO operation_registry_dispositions VALUES (?, 'ACTIVE_MANUAL')", (operator_id,))
        conn.execute("INSERT INTO operation_behavioural_profiles VALUES (?,?)", (operator_id, json.dumps(provenance)))
    return conn


def test_13a04_ladder_is_a_single_review_candidate():
    conn = _db()
    conn.executemany("INSERT INTO wt_walkback_edge_candidates VALUES ('mint',?,?,?,'SELECTED')", [
        (1, "PLAIN_XFER", 29999975000), (2, "WSOL_WRAP_CLOSE", 29999980000),
        (3, "PLAIN_XFER", 29999985000), (4, "WSOL_WRAP_CLOSE", 29999990000),
    ])
    result = evaluate_mint(conn, "mint")
    assert result.matching_profiles == ("P3R_13A04",)
    assert result.state == "BEHAVIOURAL_CANDIDATE"


def test_shared_af500_ec1_fingerprint_is_ambiguous_not_attributed():
    conn = _db()
    conn.execute("INSERT INTO wt_walkback_edge_candidates VALUES ('mint',1,'WSOL_WRAP_CLOSE',99999985000,'SELECTED')")
    conn.execute("INSERT INTO wt_walkback_atomic_flows VALUES ('mint',1,1,1,99997955720)")
    result = evaluate_mint(conn, "mint")
    assert result is None


def test_amount_without_atomic_lifecycle_does_not_match_shared_profile():
    conn = _db()
    conn.execute("INSERT INTO wt_walkback_edge_candidates VALUES ('mint',1,'WSOL_WRAP_CLOSE',99999985000,'SELECTED')")
    assert evaluate_mint(conn, "mint") is None
