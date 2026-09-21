"""Historical Deep route checks never turn amount-only evidence into ownership."""

import sqlite3
import time
from pathlib import Path

from flask import Flask, render_template

from src.ops.watchtower_deep_historical import (
    COORDINATOR, POOL, WINDOW_START, OPERATOR_ID,
    qualify_historical_mint, historical_plan, commit_historical_operation,
)
from src.ops.watchtower_deep_prospective import assess_prospective_route
from src.ops.operator_reader import OperatorReader
from src.ops.watchtower_deep_review import (
    SCHEMA as REVIEW_SCHEMA, persist_review_lead, fetch_review_leads,
    migrate_review_schema, validate_review_schema, assess_review_readonly,
    review_sweep_page, run_review_sweep_page,
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


def test_prospective_shape_is_review_only_not_canonical_membership():
    result = assess_prospective_route(_db(), "mint")
    assert result["state"] == "REVIEW_CANDIDATE"
    assert result["authority"] == "REVIEW_ONLY"
    assert result["automatic_membership_allowed"] is False
    assert result["capital_continuity"] == "UNQUALIFIED"


def test_prospective_route_discovers_rotated_wallets_without_address_allowlist():
    conn = _db()
    for old, new in ((POOL, "rotated-pool"), (COORDINATOR, "rotated-coordinator")):
        conn.execute("UPDATE wt_walkback_edge_candidates SET wallet=? WHERE wallet=?", (new, old))
        conn.execute("UPDATE wt_walkback_edge_candidates SET candidate_parent=? WHERE candidate_parent=?", (new, old))
        conn.execute("UPDATE wt_walkback_transaction_roles SET transfer_source=? WHERE transfer_source=?", (new, old))
        conn.execute("UPDATE wt_walkback_transaction_roles SET transfer_destination=? WHERE transfer_destination=?", (new, old))
    result = assess_prospective_route(conn, "mint")
    assert result["state"] == "REVIEW_CANDIDATE"
    assert result["route"]["pool"] == "rotated-pool"
    assert result["route"]["coordinator"] == "rotated-coordinator"


def test_prospective_conflicting_selected_path_is_not_a_candidate():
    conn = _db()
    conn.execute("INSERT INTO wt_walkback_edge_candidates VALUES (?,?,?,?,?,?,?,?,?,?)", (
        "mint", 3, "distribution", "different-parent", "conflict",
        WINDOW_START + 9_000, 10_000_000_000, "PLAIN_XFER",
        "TRANSACTION_DERIVED", "SELECTED",
    ))
    assert assess_prospective_route(conn, "mint")["state"] == "CONFLICT"


def test_prospective_multiple_upper_routes_abstains():
    conn = _db()
    conn.execute("INSERT INTO wt_walkback_edge_candidates VALUES (?,?,?,?,?,?,?,?,?,?)", (
        "other", 4, COORDINATOR, "second-pool", "second-pool-sig",
        WINDOW_START + 9_999, 2_000_000_000_000, "PLAIN_XFER",
        "TRANSACTION_DERIVED", "ALTERNATIVE",
    ))
    conn.execute("INSERT INTO wt_walkback_transaction_roles VALUES (?,?,?,?)", (
        "second-pool-sig", "second-pool", COORDINATOR, 2_000_000_000_000,
    ))
    assert assess_prospective_route(conn, "mint")["state"] == "AMBIGUOUS"


def test_prospective_existing_owner_is_preserved():
    conn = _db()
    conn.execute("INSERT INTO operator_launch_membership(mint,operator_id) VALUES ('mint','WATCHTOWER')")
    assert assess_prospective_route(conn, "mint")["state"] == "EXISTING_ASSIGNMENT"


def test_prospective_live_lookup_requires_destination_index():
    conn = _db()
    assert assess_prospective_route(conn, "mint", require_index=True) == {
        "state": "INSUFFICIENT", "reason": "missing_required_destination_index",
        "authority": "NONE",
    }
    conn.execute(
        "CREATE INDEX ix_wwtr_destination_amount "
        "ON wt_walkback_transaction_roles(transfer_destination,transfer_lamports)"
    )
    result = assess_prospective_route(conn, "mint", require_index=True)
    assert result["state"] == "REVIEW_CANDIDATE"
    plan = list(conn.execute(
        "EXPLAIN QUERY PLAN SELECT transfer_source FROM wt_walkback_transaction_roles "
        "INDEXED BY ix_wwtr_destination_amount WHERE transfer_destination=? "
        "AND transfer_lamports>=?", ("distribution", 100_000_000_000),
    ))
    assert any("ix_wwtr_destination_amount" in row[3] for row in plan)


def test_prospective_live_lookup_rejects_misnamed_index():
    conn = _db()
    conn.execute(
        "CREATE INDEX ix_wwtr_destination_amount "
        "ON wt_walkback_transaction_roles(transfer_source)"
    )
    assert assess_prospective_route(conn, "mint", require_index=True)["reason"] == (
        "missing_required_destination_index"
    )


def test_deep_review_lead_is_idempotent_and_not_membership():
    conn = _db()
    conn.executescript(REVIEW_SCHEMA)
    result = assess_prospective_route(conn, "mint")
    first = persist_review_lead(conn, "mint", result, now=100)
    second = persist_review_lead(conn, "mint", result, now=200)
    assert first == second
    assert conn.execute("SELECT COUNT(*) FROM wt_deep_route_review_leads").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM operator_launch_membership").fetchone()[0] == 0
    assert fetch_review_leads(conn)[0]["last_observed_at"] == 200
    conn.execute("INSERT INTO operator_launch_membership(mint,operator_id) VALUES ('mint','WATCHTOWER')")
    assert fetch_review_leads(conn) == []
    assert persist_review_lead(conn, "mint", result)["action"] == "existing_assignment"


def test_deep_review_lead_rejects_nonreview_and_watchtower_ledger():
    conn = _db()
    conn.executescript(REVIEW_SCHEMA)
    result = assess_prospective_route(conn, "mint")
    try:
        persist_review_lead(conn, "mint", {**result, "automatic_membership_allowed": True})
    except ValueError:
        pass
    else:
        raise AssertionError("automatic assignment leaked into review lead")
    conn.execute("INSERT INTO wt_watchtower_launches VALUES ('mint')")
    assert persist_review_lead(conn, "mint", result)["action"] == "watchtower_ledger_present"
    assert fetch_review_leads(conn) == []


def test_review_schema_migration_is_transaction_owned_and_rollbackable():
    conn = _db()
    conn.commit()
    assert validate_review_schema(conn) is False
    conn.execute("BEGIN IMMEDIATE")
    migrate_review_schema(conn)
    assert validate_review_schema(conn) is True
    assert conn.in_transaction is True
    conn.rollback()
    assert validate_review_schema(conn) is False
    assert conn.execute("SELECT COUNT(*) FROM wt_walkback_queue").fetchone()[0] == 1


def test_review_sweep_requires_exact_partial_index():
    conn = _db()
    conn.execute(
        "CREATE INDEX ix_wbq_deep_review_page ON wt_walkback_queue(mint)"
    )
    migrate_review_schema(conn)
    assert validate_review_schema(conn) is False

    clean = _db()
    migrate_review_schema(clean)
    plan = list(clean.execute(
        "EXPLAIN QUERY PLAN SELECT mint FROM wt_walkback_queue "
        "WHERE status='complete' AND intelligence_outcome='LINEAGE_GAP' "
        "AND funding_mechanism='WSOL_WRAP_CLOSE' "
        "AND funder_amount_sol>1.1120385 AND funder_amount_sol<1.1120395 "
        "AND funder_block_time>=? ORDER BY funder_block_time DESC,mint DESC LIMIT 8",
        (WINDOW_START,),
    ))
    assert any("ix_wbq_deep_review_page" in row[3] for row in plan)


def test_readonly_review_assessment_and_bounded_recheck_page(tmp_path):
    conn = _db()
    migrate_review_schema(conn)
    conn.commit()
    assert review_sweep_page(conn, since=WINDOW_START, limit=1) == [
        ("mint", WINDOW_START + 10_300),
    ]
    assert review_sweep_page(
        conn, since=WINDOW_START, cursor=(WINDOW_START + 10_300, "mint"),
    ) == []
    path = tmp_path / "ops.db"
    disk = sqlite3.connect(path)
    conn.backup(disk)
    disk.close()
    result = assess_review_readonly(str(path), "mint")
    assert result["state"] == "REVIEW_CANDIDATE"
    assert result["authority"] == "REVIEW_ONLY"
    persist_review_lead(conn, "mint", result, now=123)
    assert review_sweep_page(conn, since=WINDOW_START) == []


def test_readonly_review_assessment_defers_without_schema(tmp_path):
    conn = _db()
    conn.commit()
    path = tmp_path / "ops.db"
    disk = sqlite3.connect(path)
    conn.backup(disk)
    disk.close()
    assert assess_review_readonly(str(path), "mint")["reason"] == (
        "review_schema_not_current"
    )


def test_operator_review_api_reader_shows_deep_leads_not_membership(tmp_path):
    conn = _db()
    migrate_review_schema(conn)
    persist_review_lead(conn, "mint", assess_prospective_route(conn, "mint"), now=123)
    conn.commit()
    path = tmp_path / "ops.db"
    disk = sqlite3.connect(path)
    conn.backup(disk)
    disk.close()
    rows = OperatorReader(str(path)).fetch_operator_review_candidates(OPERATOR_ID)
    assert [row["mint"] for row in rows] == ["mint"]
    assert rows[0]["authority"] == "REVIEW_ONLY"
    assert conn.execute("SELECT COUNT(*) FROM operator_launch_membership").fetchone()[0] == 0


def test_deep_review_ui_is_explicitly_noncanonical():
    root = Path(__file__).resolve().parents[1]
    app = Flask(__name__, template_folder=str(root / "templates"),
                static_folder=str(root / "static"))
    with app.test_request_context():
        page = render_template("operator_deep_review.html", operator_id=OPERATOR_ID,
                               operator_name="WATCHTOWER_DEEP")
    assert "review-only candidate" in page
    assert "not an operation assignment" in page
    assert "/review-queue" in page
    detail = (root / "templates" / "operator_intelligence.html").read_text()
    assert "View Deep route review candidates" in detail


def test_review_sweep_closes_all_reads_before_publishing(tmp_path):
    conn = _db()
    migrate_review_schema(conn)
    conn.commit()
    path = tmp_path / "ops.db"
    disk = sqlite3.connect(path)
    conn.backup(disk)
    disk.close()
    published = []

    def publish(mint, assessment):
        writer = sqlite3.connect(path, timeout=0.1)
        try:
            writer.execute("BEGIN EXCLUSIVE")
            persist_review_lead(writer, mint, assessment, now=123)
            writer.commit()
        finally:
            writer.close()
        published.append(mint)

    result = run_review_sweep_page(
        str(path), publish, now=WINDOW_START + 11_000, limit=1,
    )
    assert result["checked"] == result["published"] == 1
    assert published == ["mint"]
    assert run_review_sweep_page(
        str(path), publish, cursor=result["cursor"],
        now=WINDOW_START + 11_000,
    )["status"] == "COMPLETE_PASS"


def test_walkback_deep_hook_records_only_review_lead(tmp_path, monkeypatch):
    from src.core import walkback_worker

    conn = _db()
    migrate_review_schema(conn)
    conn.execute("UPDATE wt_walkback_queue SET funder_block_time=? WHERE mint='mint'",
                 (int(time.time()) - 10,))
    conn.commit()
    path = tmp_path / "ops.db"
    disk = sqlite3.connect(path)
    conn.backup(disk)
    monkeypatch.setattr(walkback_worker, "OPS_DB_PATH", str(path))
    monkeypatch.setattr(walkback_worker, "_DEEP_REVIEW_CURSOR", None)
    walkback_worker._review_deep_routes_after_commit(disk)
    assert disk.execute("SELECT COUNT(*) FROM wt_deep_route_review_leads").fetchone()[0] == 1
    assert disk.execute("SELECT COUNT(*) FROM operator_launch_membership").fetchone()[0] == 0
    assert disk.execute("SELECT COUNT(*) FROM wt_watchtower_launches").fetchone()[0] == 0
    disk.close()
