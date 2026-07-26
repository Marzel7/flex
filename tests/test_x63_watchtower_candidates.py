import json
import sqlite3
from pathlib import Path

from src.ops.watchtower_candidates import (
    PRIMITIVE,
    detect_ephemeral_wsol_creator_handoff,
    ensure_schema,
    evaluate_and_enqueue_candidate,
    evaluate_transaction_candidate,
    funding_signature_for_quick_launch,
    sync_walkback_result,
)


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "templates" / "discovery.html").read_text()
WORKER = (ROOT / "src" / "core" / "walkback_worker.py").read_text()


def ops_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE wt_walkback_queue (
          mint TEXT PRIMARY KEY, creator TEXT, status TEXT, intelligence_outcome TEXT,
          enqueued_at INTEGER, updated_at INTEGER, started_at INTEGER, completed_at INTEGER
        );
        CREATE TABLE wt_candidate_websocket_watches (
          candidate_wallet TEXT, subprov_wallet TEXT, wrap_close_signature TEXT,
          wrap_close_time INTEGER, temp_wsol_account TEXT, close_destination TEXT,
          funding_mechanism TEXT, detected_at INTEGER
        );
        CREATE TABLE watchtower_token_attribution (mint TEXT PRIMARY KEY);
    """)
    ensure_schema(conn)
    return conn


def live_db(create=105, migration=120):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE token_analysis (mint TEXT PRIMARY KEY,create_tx_signature TEXT,created_at INTEGER,migrated_at INTEGER)")
    conn.execute("INSERT INTO token_analysis VALUES ('M','CREATE_SIG',?,?)", (create, migration))
    return conn


def add_transfer_index(conn):
    conn.execute(
        "CREATE TABLE transfer_index (signature TEXT,source TEXT,destination TEXT,"
        "amount_lamports INTEGER,block_time INTEGER)"
    )


def handoff(variant="WSOL_WRAP_CLOSE", destination="C"):
    return {
        "funding_mechanism": variant,
        "close_destination": destination,
        "wrap_close_signature": "HANDOFF_SIG",
        "temp_wsol_account": "TEMP",
        "wrap_close_time": 100,
    }


def test_discovery_candidate_queue_follows_selected_window():
    # X65.58 follow-up -- the standalone "WATCHTOWER Candidate Queue" PANEL
    # (fetched via /api/ops-v2/watchtower-candidates) was removed from
    # Discovery's landing page: it duplicated Provisioning Candidates
    # already shown inside the Operation Intelligence tab and rendered
    # outside the tab structure entirely, reading as if it belonged to
    # whichever tab happened to be open. renderWatchtowerCandidateQueue()/
    # loadWatchtowerCandidateQueue() (that panel's own functions) remain gone.
    #
    # X65.60 note: the SAME endpoint is fetched again, but only as a
    # walkback-status ENRICHMENT for the existing Provisioning Candidates
    # table (loadX65_60WalkbackStatusEnrichment), still window-scoped via
    # dwWindowSeconds() -- so the endpoint call legitimately reappears in
    # the HTML, just under a different function serving a different purpose.
    assert 'params.set(\'window\',DW_WINDOW)' in HTML
    assert '"7d":604800' in HTML
    assert "function renderWatchtowerCandidateQueue" not in HTML
    assert "function loadWatchtowerCandidateQueue" not in HTML


def test_both_transaction_variants_map_to_one_primitive():
    for variant in ("WSOL_WRAP_CLOSE", "SEEDED_ACCOUNT_CLOSE"):
        result = detect_ephemeral_wsol_creator_handoff(handoff(variant), "C")
        assert result["primitive_detected"] is True
        assert result["primitive"] == PRIMITIVE
        assert result["variant"] == variant


def test_close_destination_and_temporary_account_are_mandatory():
    assert not detect_ephemeral_wsol_creator_handoff(handoff(destination="OTHER"), "C")["primitive_detected"]
    evidence = handoff()
    evidence["temp_wsol_account"] = None
    assert not detect_ephemeral_wsol_creator_handoff(evidence, "C")["primitive_detected"]


def test_missing_temporary_account_is_retained_as_partial_evidence():
    evidence = handoff()
    evidence["temp_wsol_account"] = None
    result = detect_ephemeral_wsol_creator_handoff(evidence, "C")
    assert result["primitive_detected"] is False
    assert result["partial_evidence"] is True
    assert result["missing_evidence"] == ["TEMP_WSOL_ACCOUNT"]


def test_quick_partial_handoff_is_surfaced_without_attribution():
    ops, live = ops_db(), live_db()
    ops.execute("INSERT INTO wt_walkback_queue(mint,creator,status,enqueued_at,updated_at) VALUES ('M','C','pending',1,1)")
    e = handoff()
    e["temp_wsol_account"] = None
    ops.execute("INSERT INTO wt_candidate_websocket_watches VALUES (?,?,?,?,?,?,?,?)", (
        "C", "S", e["wrap_close_signature"], e["wrap_close_time"], e["temp_wsol_account"],
        e["close_destination"], e["funding_mechanism"], 101,
    ))
    result = evaluate_and_enqueue_candidate(ops, mint="M", creator="C", live_conn=live)
    assert result["partial_evidence"] is True
    row = ops.execute(
        "SELECT evidence_status,candidate_confidence,missing_evidence "
        "FROM wt_watchtower_candidates"
    ).fetchone()
    assert row["evidence_status"] == "PARTIAL"
    assert row["candidate_confidence"] == "PROBABLE"
    assert json.loads(row["missing_evidence"]) == ["TEMP_WSOL_ACCOUNT"]
    assert ops.execute("SELECT COUNT(*) FROM watchtower_token_attribution").fetchone()[0] == 0


def test_quick_handoff_creates_neutral_candidate_and_promotes_priority():
    ops, live = ops_db(), live_db()
    ops.execute("INSERT INTO wt_walkback_queue(mint,creator,status,enqueued_at,updated_at) VALUES ('M','C','pending',1,1)")
    e = handoff()
    ops.execute("INSERT INTO wt_candidate_websocket_watches VALUES (?,?,?,?,?,?,?,?)", (
        "C", "S", e["wrap_close_signature"], e["wrap_close_time"], e["temp_wsol_account"],
        e["close_destination"], e["funding_mechanism"], 101,
    ))
    result = evaluate_and_enqueue_candidate(ops, mint="M", creator="C", live_conn=live)
    assert result["candidate_status"] == "PENDING_WALKBACK"
    candidate = ops.execute("SELECT * FROM wt_watchtower_candidates").fetchone()
    assert candidate["primitive"] == PRIMITIVE
    assert json.loads(candidate["candidate_reason"]) == ["QUICK_BIRTH_MIGRATION", PRIMITIVE]
    queue = ops.execute("SELECT priority,priority_reason FROM wt_walkback_queue").fetchone()
    assert queue["priority"] == 100
    assert "EPHEMERAL_WSOL_CREATOR_HANDOFF" in queue["priority_reason"]
    assert ops.execute("SELECT COUNT(*) FROM watchtower_token_attribution").fetchone()[0] == 0


def test_non_quick_launch_is_not_a_candidate():
    ops, live = ops_db(), live_db(create=100_000, migration=100_010)
    ops.execute("INSERT INTO wt_walkback_queue(mint,creator,status,enqueued_at,updated_at) VALUES ('M','C','pending',1,1)")
    e = handoff()
    ops.execute("INSERT INTO wt_candidate_websocket_watches VALUES (?,?,?,?,?,?,?,?)", (
        "C", "S", e["wrap_close_signature"], e["wrap_close_time"], e["temp_wsol_account"],
        e["close_destination"], e["funding_mechanism"], 101,
    ))
    assert evaluate_and_enqueue_candidate(ops, mint="M", creator="C", live_conn=live) is None
    assert ops.execute("SELECT COUNT(*) FROM wt_watchtower_candidates").fetchone()[0] == 0


def test_funding_signature_uses_earliest_pre_create_inbound_transfer():
    live = live_db(create=105, migration=120)
    add_transfer_index(live)
    live.executemany("INSERT INTO transfer_index VALUES (?,?,?,?,?)", (
        ("BIRTH_SIG", "S", "C", 1, 100),
        ("LATER_PRE_CREATE", "S", "C", 1, 103),
        ("POST_CREATE", "S", "C", 1, 130),
    ))
    assert funding_signature_for_quick_launch(live, mint="M", creator="C") == "BIRTH_SIG"


def test_transaction_detector_covers_unknown_infrastructure(monkeypatch):
    ops, live = ops_db(), live_db()
    ops.execute("INSERT INTO wt_walkback_queue(mint,creator,status,enqueued_at,updated_at) VALUES ('M','C','pending',1,1)")
    monkeypatch.setattr(
        "src.core.wrap_close_detector.extract_close_destinations",
        lambda _tx: [{
            "candidate": "C", "temp_wsol_account": "TEMP",
            "funding_mechanism": "SEEDED_ACCOUNT_CLOSE",
        }],
    )
    result = evaluate_transaction_candidate(
        ops, mint="M", creator="C", signature="BIRTH_SIG",
        tx={"blockTime": 100}, live_conn=live,
    )
    assert result["variant"] == "SEEDED_ACCOUNT_CLOSE"
    row = ops.execute("SELECT handoff_signature,handoff_variant FROM wt_watchtower_candidates").fetchone()
    assert tuple(row) == ("BIRTH_SIG", "SEEDED_ACCOUNT_CLOSE")


def test_candidate_write_is_idempotent():
    ops, live = ops_db(), live_db()
    ops.execute("INSERT INTO wt_walkback_queue(mint,creator,status,enqueued_at,updated_at) VALUES ('M','C','pending',1,1)")
    e = handoff()
    ops.execute("INSERT INTO wt_candidate_websocket_watches VALUES (?,?,?,?,?,?,?,?)", (
        "C", "S", e["wrap_close_signature"], e["wrap_close_time"], e["temp_wsol_account"],
        e["close_destination"], e["funding_mechanism"], 101,
    ))
    evaluate_and_enqueue_candidate(ops, mint="M", creator="C", live_conn=live)
    evaluate_and_enqueue_candidate(ops, mint="M", creator="C", live_conn=live)
    assert ops.execute("SELECT COUNT(*) FROM wt_watchtower_candidates").fetchone()[0] == 1


def test_final_result_only_mirrors_walkback_outcome():
    ops, live = ops_db(), live_db()
    ops.execute("INSERT INTO wt_walkback_queue(mint,creator,status,enqueued_at,updated_at) VALUES ('M','C','pending',1,1)")
    e = handoff()
    ops.execute("INSERT INTO wt_candidate_websocket_watches VALUES (?,?,?,?,?,?,?,?)", (
        "C", "S", e["wrap_close_signature"], e["wrap_close_time"], e["temp_wsol_account"],
        e["close_destination"], e["funding_mechanism"], 101,
    ))
    evaluate_and_enqueue_candidate(ops, mint="M", creator="C", live_conn=live)
    ops.execute("UPDATE wt_walkback_queue SET status='complete',intelligence_outcome='WATCHTOWER_CONFIRMED',started_at=130,completed_at=140")
    sync_walkback_result(ops, "M")
    row = ops.execute("SELECT * FROM wt_watchtower_candidates").fetchone()
    assert row["candidate_status"] == "WALKBACK_COMPLETE"
    assert row["walkback_result"] == "CONFIRMED_WATCHTOWER"


def test_late_candidate_mirrors_walkback_that_already_completed():
    ops, live = ops_db(), live_db()
    ops.execute(
        "INSERT INTO wt_walkback_queue(mint,creator,status,intelligence_outcome,"
        "enqueued_at,updated_at,started_at,completed_at) "
        "VALUES ('M','C','complete','NON_WATCHTOWER',1,1,2,3)"
    )
    e = handoff()
    ops.execute("INSERT INTO wt_candidate_websocket_watches VALUES (?,?,?,?,?,?,?,?)", (
        "C", "S", e["wrap_close_signature"], e["wrap_close_time"], e["temp_wsol_account"],
        e["close_destination"], e["funding_mechanism"], 101,
    ))
    evaluate_and_enqueue_candidate(ops, mint="M", creator="C", live_conn=live)
    row = ops.execute(
        "SELECT candidate_status,walkback_result FROM wt_watchtower_candidates"
    ).fetchone()
    assert tuple(row) == ("WALKBACK_COMPLETE", "REJECTED")


def test_walkback_claim_order_prioritises_candidates():
    assert "ORDER BY COALESCE(priority,0) DESC, enqueued_at ASC" in WORKER


def test_discovery_renders_candidate_queue_and_safety_copy():
    # X65.58 follow-up -- the standalone landing-page "WATCHTOWER Candidate
    # Queue" table (renderWatchtowerCandidateQueue/loadWatchtowerCandidateQueue)
    # was removed; see test_discovery_candidate_queue_follows_selected_window's
    # updated docstring for the reasoning. The PER-ENTITY candidate-evidence
    # check (loadWatchtowerCandidateEvidence, shown on a token's own Discovery
    # entity page) is a different, unrelated feature and remains unchanged.
    assert "<div class=\"dw-vi-title\">WATCHTOWER Candidate Queue" not in HTML
    assert "function renderWatchtowerCandidateQueue" not in HTML
    assert "function loadWatchtowerCandidateQueue" not in HTML
    assert "loadWatchtowerCandidateEvidence(subject.id)" in HTML
    assert "This candidate state does not assign WATCHTOWER attribution." in HTML
