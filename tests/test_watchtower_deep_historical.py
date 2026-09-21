"""Historical Deep route checks never turn amount-only evidence into ownership."""

import sqlite3

from src.ops.watchtower_deep_historical import (
    COORDINATOR, POOL, WINDOW_START, OPERATOR_ID,
    qualify_historical_mint, historical_plan, commit_historical_operation,
)


def _db():
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
    CREATE TABLE wt_walkback_queue (
      mint TEXT PRIMARY KEY,creator TEXT,subprov TEXT,status TEXT,
      intelligence_outcome TEXT,funding_mechanism TEXT,funder_block_time INTEGER,
      funder_amount_sol REAL
    );
    CREATE TABLE operator_launch_membership (mint TEXT PRIMARY KEY,operator_id TEXT,
      source_population_id TEXT,assigned_at INTEGER,event_id TEXT);
    CREATE TABLE wt_watchtower_launches (mint TEXT);
    CREATE TABLE wt_walkback_edge_candidates (
      mint TEXT,hop_depth INTEGER,wallet TEXT,candidate_parent TEXT,
      signature TEXT,block_time INTEGER,amount_lamports INTEGER,
      mechanism TEXT,evidence_strength TEXT,selection_status TEXT
    );
    CREATE TABLE wt_walkback_transaction_roles (
      signature TEXT,transfer_source TEXT,transfer_destination TEXT,
      transfer_lamports INTEGER
    );
    """)
    start = WINDOW_START + 10_000
    conn.execute("INSERT INTO wt_walkback_queue VALUES (?,?,?,?,?,?,?,?)", (
        "mint", "creator", "subprov", "complete", "LINEAGE_GAP",
        "WSOL_WRAP_CLOSE", start + 300, 1.112039,
    ))
    edges = [
        ("mint", 1, "creator", "subprov", "close", start + 250,
         1_112_039_000, "WSOL_WRAP_CLOSE", "TRANSACTION_DERIVED", "SELECTED"),
        ("mint", 2, "subprov", "distribution", "lower", start + 200,
         717_000_000_000, "PLAIN_XFER", "TRANSACTION_DERIVED", "SELECTED"),
        ("other", 3, "distribution", COORDINATOR, "upper", start + 100,
         1_750_000_000_000, "PLAIN_XFER", "TRANSACTION_DERIVED", "ALTERNATIVE"),
        ("other", 4, COORDINATOR, POOL, "pool", start,
         2_745_000_000_000, "PLAIN_XFER", "TRANSACTION_DERIVED", "ALTERNATIVE"),
    ]
    conn.executemany("INSERT INTO wt_walkback_edge_candidates VALUES (?,?,?,?,?,?,?,?,?,?)", edges)
    conn.executemany("INSERT INTO wt_walkback_transaction_roles VALUES (?,?,?,?)", [
        ("upper", COORDINATOR, "distribution", 1_750_000_000_000),
        ("pool", POOL, COORDINATOR, 2_745_000_000_000),
    ])
    return conn


def test_complete_time_ordered_route_qualifies():
    assert qualify_historical_mint(_db(), "mint")["eligible"] is True


def test_amount_alone_cannot_qualify():
    conn = _db()
    conn.execute("DELETE FROM wt_walkback_edge_candidates WHERE hop_depth=2")
    assert qualify_historical_mint(conn, "mint")["eligible"] is False


def test_conflicting_selected_hop_three_wins_over_cross_mint_route():
    conn = _db()
    conn.execute("INSERT INTO wt_walkback_edge_candidates VALUES (?,?,?,?,?,?,?,?,?,?)", (
        "mint", 3, "distribution", "another-parent", "conflict",
        WINDOW_START + 9_000, 10_000_000_000, "PLAIN_XFER",
        "TRANSACTION_DERIVED", "SELECTED",
    ))
    assert qualify_historical_mint(conn, "mint")["reason"] == "conflicting_selected_upstream"


def test_existing_watchtower_ownership_cannot_be_taken():
    conn = _db()
    conn.execute("INSERT INTO operator_launch_membership(mint,operator_id) VALUES ('mint','WATCHTOWER')")
    assert qualify_historical_mint(conn, "mint")["reason"] == "existing_operator_assignment"


def test_existing_watchtower_ledger_cannot_be_taken():
    conn = _db()
    conn.execute("INSERT INTO wt_watchtower_launches VALUES ('mint')")
    assert qualify_historical_mint(conn, "mint")["reason"] == "existing_watchtower_launch"


def test_time_reversed_upper_witness_is_rejected():
    conn = _db()
    conn.execute("UPDATE wt_walkback_edge_candidates SET block_time=? WHERE signature='upper'", (
        WINDOW_START + 11_000,
    ))
    assert qualify_historical_mint(conn, "mint")["reason"] == "missing_time_ordered_upper_witness"


def test_same_amount_different_coordinator_is_rejected():
    conn = _db()
    conn.execute("UPDATE wt_walkback_transaction_roles SET transfer_source='other' WHERE signature='upper'")
    conn.execute("UPDATE wt_walkback_edge_candidates SET candidate_parent='other' WHERE signature='upper'")
    assert qualify_historical_mint(conn, "mint")["reason"] == "missing_time_ordered_upper_witness"


def _registration_db():
    conn = _db()
    conn.executescript("""
    CREATE TABLE operators(operator_id TEXT PRIMARY KEY,status TEXT,confidence TEXT,
      first_seen INTEGER,last_seen INTEGER,summary TEXT,review_state TEXT,
      display_name TEXT,created_at INTEGER,updated_at INTEGER);
    CREATE TABLE operator_identity_events(event_id TEXT PRIMARY KEY,operator_id TEXT,
      event_type TEXT,timestamp INTEGER,analyst TEXT,evidence_revision TEXT,
      reason TEXT,payload_json TEXT);
    CREATE TABLE operator_identity_state(operator_id TEXT PRIMARY KEY,identity_status TEXT,
      activity_status TEXT,source_population_id TEXT,source_population_revision TEXT,
      confirmation_evidence_package TEXT,disposition_at_confirmation TEXT,
      updated_at INTEGER);
    CREATE TABLE operation_registry_dispositions(operator_id TEXT PRIMARY KEY,
      disposition TEXT,manual_reviewer TEXT,reason TEXT,source_candidate_id TEXT,
      updated_at INTEGER);
    CREATE TABLE operation_qualification_contracts(contract_id TEXT PRIMARY KEY,
      operator_id TEXT,qualification_category TEXT,automation_eligibility TEXT,
      detector_version TEXT,parent_mechanism TEXT,source_candidate_id TEXT,
      benchmark_json TEXT,contract_json TEXT,evidence_lineage_json TEXT,created_at INTEGER);
    CREATE TABLE operator_launch_assignment_history(assignment_id TEXT PRIMARY KEY,
      mint TEXT,from_operator_id TEXT,to_operator_id TEXT,timestamp INTEGER,
      analyst TEXT,evidence_revision TEXT,reason TEXT,event_id TEXT);
    CREATE TABLE operation_activity_snapshots(snapshot_id TEXT PRIMARY KEY,
      operator_id TEXT,observed_at INTEGER,timestamp_semantics TEXT,metrics_json TEXT,
      activity_state TEXT);
    """)
    # The first route is already in _db; add 25 independent lower routes.
    for index in range(1, 26):
        mint = f"mint{index}"
        creator, subprov = f"creator{index}", f"subprov{index}"
        conn.execute("INSERT INTO wt_walkback_queue VALUES (?,?,?,?,?,?,?,?)", (
            mint, creator, subprov, "complete", "LINEAGE_GAP", "WSOL_WRAP_CLOSE",
            WINDOW_START + 10_300, 1.112039,
        ))
        conn.execute("INSERT INTO wt_walkback_edge_candidates VALUES (?,?,?,?,?,?,?,?,?,?)", (
            mint, 1, creator, subprov, f"close{index}", WINDOW_START + 10_250,
            1_112_039_000, "WSOL_WRAP_CLOSE", "TRANSACTION_DERIVED", "SELECTED",
        ))
        conn.execute("INSERT INTO wt_walkback_edge_candidates VALUES (?,?,?,?,?,?,?,?,?,?)", (
            mint, 2, subprov, "distribution", f"lower{index}", WINDOW_START + 10_200,
            717_000_000_000, "PLAIN_XFER", "TRANSACTION_DERIVED", "SELECTED",
        ))
    return conn


def test_registration_is_exact_idempotent_and_review_only():
    conn = _registration_db()
    plan = historical_plan(conn)
    assert plan["accepted_count"] == 26
    result = commit_historical_operation(
        conn, expected_mints=plan["accepted"], expected_digest=plan["accepted_digest"],
        expected_watchtower_count=0, now=WINDOW_START + 20_000,
    )
    assert result["action"] == "registered"
    assert conn.execute("SELECT COUNT(*) FROM operator_launch_membership WHERE operator_id=?", (
        OPERATOR_ID,
    )).fetchone()[0] == 26
    assert conn.execute("SELECT automation_eligibility FROM operation_qualification_contracts").fetchone()[0] == "REVIEW_ONLY"
    assert conn.execute("SELECT COUNT(*) FROM operator_launch_assignment_history").fetchone()[0] == 26
    assert commit_historical_operation(
        conn, expected_mints=plan["accepted"], expected_digest=plan["accepted_digest"],
        expected_watchtower_count=0, now=WINDOW_START + 20_000,
    )["action"] == "already_registered"


def test_registration_rejects_changed_membership_before_writing():
    conn = _registration_db()
    plan = historical_plan(conn)
    conn.execute("INSERT INTO operator_launch_membership(mint,operator_id) VALUES ('mint1','WATCHTOWER')")
    try:
        commit_historical_operation(
            conn, expected_mints=plan["accepted"], expected_digest=plan["accepted_digest"],
            expected_watchtower_count=0, now=WINDOW_START + 20_000,
        )
    except ValueError as exc:
        assert "historical mint changed" in str(exc)
    else:
        raise AssertionError("changed membership did not abort")
    assert conn.execute("SELECT COUNT(*) FROM operators").fetchone()[0] == 0
