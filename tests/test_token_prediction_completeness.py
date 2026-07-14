import sqlite3

from src.core.token_prediction_builder import TokenPredictionBuilder, TokenPredictionRescoreWorker


def _db(tmp_path):
    path = tmp_path / "predictions.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE token_analysis (
            mint TEXT PRIMARY KEY,
            earliest_tx_creator TEXT,
            pf_ws_creator TEXT,
            market_cap_highest REAL,
            market_cap_current REAL,
            migrated_at INTEGER,
            lifecycle_stage TEXT,
            bonding_curve_pda TEXT,
            pool_address TEXT,
            pumpswap_pool_address TEXT
        );
        CREATE TABLE token_pool_accounts (mint TEXT, liquidity_removed INTEGER);
        CREATE TABLE metadata_cache (mint TEXT PRIMARY KEY, name TEXT);
        CREATE TABLE creator_funders (creator_address TEXT, funder_address TEXT, is_cex INTEGER DEFAULT 0);
        CREATE TABLE creator_risk_scores (
            creator_address TEXT PRIMARY KEY,
            final_score INTEGER,
            category TEXT,
            risk_level TEXT,
            operator_score REAL,
            outcome_score REAL,
            g_score REAL,
            liquidation_score REAL,
            g7_percentage REAL,
            migrated_tokens INTEGER,
            liquidation_count INTEGER
        );
        CREATE TABLE network_risk_scores (
            network_name TEXT PRIMARY KEY,
            final_score INTEGER,
            category TEXT,
            risk_level TEXT,
            operator_score REAL,
            g7_percentage REAL
        );
        CREATE TABLE network_membership (network_name TEXT, creator_address TEXT);
        CREATE TABLE creator_self_funding (creator_address TEXT PRIMARY KEY, is_self_funding INTEGER, self_funding_percentage REAL);
        CREATE TABLE creator_second_hop (creator_address TEXT, upstream_address TEXT);
        CREATE TABLE creator_outbound_classifications (creator_address TEXT, recipient_address TEXT, relationship_type TEXT);
        CREATE TABLE infra_wallets (address TEXT PRIMARY KEY, type TEXT, label TEXT);
        """
    )
    conn.commit()
    conn.close()
    return path


def _token(conn, mint, creator=None):
    conn.execute(
        """
        INSERT INTO token_analysis (
            mint, earliest_tx_creator, pf_ws_creator, market_cap_highest,
            market_cap_current, migrated_at, lifecycle_stage
        ) VALUES (?, ?, NULL, 25000, 1000, 1700000000, 'migrated')
        """,
        (mint, creator),
    )


def _score(path, mint):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    builder = TokenPredictionBuilder(str(path))
    result = builder.score_single(conn, mint, "TEST")
    row = conn.execute("SELECT * FROM token_prediction_scores WHERE mint=?", (mint,)).fetchone()
    conn.close()
    return result, row


def test_token_with_no_creator_is_pending_creator(tmp_path):
    path = _db(tmp_path)
    conn = sqlite3.connect(path)
    _token(conn, "M_NO_CREATOR")
    conn.commit()
    conn.close()

    result, row = _score(path, "M_NO_CREATOR")
    assert result["prediction_status"] == "PENDING_CREATOR"
    assert row["prediction_score"] is None
    assert row["risk_level"] is None
    assert row["prediction_label"] == "PENDING_CREATOR"


def test_creator_without_funding_is_pending_funding(tmp_path):
    path = _db(tmp_path)
    conn = sqlite3.connect(path)
    _token(conn, "M_NO_FUNDING", "C1")
    conn.commit()
    conn.close()

    _, row = _score(path, "M_NO_FUNDING")
    assert row["prediction_status"] == "PENDING_FUNDING"
    assert row["prediction_score"] is None


def test_creator_with_funding_without_risk_is_pending_risk_score(tmp_path):
    path = _db(tmp_path)
    conn = sqlite3.connect(path)
    _token(conn, "M_NO_RISK", "C1")
    conn.execute("INSERT INTO creator_funders VALUES ('C1', 'F1', 0)")
    conn.commit()
    conn.close()

    _, row = _score(path, "M_NO_RISK")
    assert row["prediction_status"] == "PENDING_RISK_SCORE"
    assert row["prediction_score"] is None


def test_creator_with_less_than_two_migrations_is_insufficient_history(tmp_path):
    path = _db(tmp_path)
    conn = sqlite3.connect(path)
    _token(conn, "M_SHORT_HISTORY", "C1")
    conn.execute("INSERT INTO creator_funders VALUES ('C1', 'F1', 0)")
    conn.execute("""
        INSERT INTO creator_risk_scores VALUES
        ('C1', 10, 'LOW', 'LOW', 0, 0, 0, 0, 0, 1, 0)
    """)
    conn.commit()
    conn.close()

    _, row = _score(path, "M_SHORT_HISTORY")
    assert row["prediction_status"] == "INSUFFICIENT_HISTORY"
    assert row["prediction_score"] is None


def test_token_becomes_complete_after_data_arrives(tmp_path):
    path = _db(tmp_path)
    conn = sqlite3.connect(path)
    _token(conn, "M_COMPLETE", "C1")
    conn.execute("INSERT INTO creator_funders VALUES ('C1', 'F1', 0)")
    conn.commit()
    conn.close()

    _, row = _score(path, "M_COMPLETE")
    assert row["prediction_status"] == "PENDING_RISK_SCORE"

    conn = sqlite3.connect(path)
    conn.execute("""
        INSERT INTO creator_risk_scores VALUES
        ('C1', 80, 'CRITICAL_OPERATOR', 'CRITICAL', 90, 70, 60, 0, 90, 3, 0)
    """)
    conn.commit()
    conn.close()

    TokenPredictionRescoreWorker(str(path)).run()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM token_prediction_scores WHERE mint='M_COMPLETE'").fetchone()
    queue_count = conn.execute("SELECT COUNT(*) FROM token_rescore_queue").fetchone()[0]
    conn.close()

    assert row["prediction_status"] == "COMPLETE"
    assert row["prediction_score"] is not None
    assert row["risk_level"] is not None
    assert queue_count == 0


def test_low_risk_only_when_data_complete(tmp_path):
    path = _db(tmp_path)
    conn = sqlite3.connect(path)
    _token(conn, "M_LOW", "C1")
    conn.execute("INSERT INTO creator_funders VALUES ('C1', 'F1', 0)")
    conn.execute("""
        INSERT INTO creator_risk_scores VALUES
        ('C1', 1, 'LOW', 'LOW', 0, 0, 0, 0, 0, 2, 0)
    """)
    conn.commit()
    conn.close()

    _, row = _score(path, "M_LOW")
    assert row["prediction_status"] == "COMPLETE"
    assert row["prediction_label"] == "LOW_RISK"
    assert row["risk_level"] == "LOW"
    assert row["prediction_score"] is not None
