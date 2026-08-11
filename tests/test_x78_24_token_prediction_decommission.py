from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.core.creator_funding_worker import _decommission_token_prediction_triggers
ROOT = Path(__file__).resolve().parents[1]


def _prediction_schema(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE token_prediction_scores (mint TEXT PRIMARY KEY);
        CREATE TABLE token_prediction_events (id INTEGER PRIMARY KEY, mint TEXT);
        CREATE TABLE token_prediction_outcomes (mint TEXT PRIMARY KEY);
        CREATE TABLE token_rescore_queue (mint TEXT PRIMARY KEY, reason TEXT, created_at INTEGER);
        CREATE TABLE token_analysis (mint TEXT PRIMARY KEY, earliest_tx_creator TEXT, pf_ws_creator TEXT);
        CREATE TABLE creator_funders (creator_address TEXT);
        CREATE TABLE creator_risk_scores (creator_address TEXT);
        CREATE TABLE network_membership (creator_address TEXT);
        INSERT INTO token_prediction_scores VALUES ('historical');
        INSERT INTO token_prediction_events VALUES (1, 'historical');
        INSERT INTO token_prediction_outcomes VALUES ('historical');
        CREATE TRIGGER trg_token_prediction_creator_resolved AFTER UPDATE ON token_analysis BEGIN
          INSERT OR REPLACE INTO token_rescore_queue VALUES (NEW.mint, 'creator_resolved', 1);
        END;
        CREATE TRIGGER trg_token_prediction_funding_inserted AFTER INSERT ON creator_funders BEGIN
          INSERT OR REPLACE INTO token_rescore_queue VALUES (NEW.creator_address, 'funding_extracted', 1);
        END;
        CREATE TRIGGER trg_token_prediction_creator_risk_inserted AFTER INSERT ON creator_risk_scores BEGIN
          INSERT OR REPLACE INTO token_rescore_queue VALUES (NEW.creator_address, 'creator_risk_updated', 1);
        END;
        CREATE TRIGGER trg_token_prediction_creator_risk_updated AFTER UPDATE ON creator_risk_scores BEGIN
          INSERT OR REPLACE INTO token_rescore_queue VALUES (NEW.creator_address, 'creator_risk_updated', 1);
        END;
        CREATE TRIGGER trg_token_prediction_network_assigned AFTER INSERT ON network_membership BEGIN
          INSERT OR REPLACE INTO token_rescore_queue VALUES (NEW.creator_address, 'network_assigned', 1);
        END;
        """
    )
    conn.commit()
    conn.close()


def test_known_prediction_triggers_removed_historical_rows_retained(tmp_path):
    path = tmp_path / "decommission.db"
    _prediction_schema(path)

    assert _decommission_token_prediction_triggers(str(path)) == "decommissioned:5"

    conn = sqlite3.connect(path)
    assert conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='trigger' AND name LIKE 'trg_token_prediction_%'"
    ).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM token_prediction_scores").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM token_prediction_events").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM token_prediction_outcomes").fetchone()[0] == 1
    conn.close()


def test_unknown_prediction_trigger_fails_closed(tmp_path):
    path = tmp_path / "custom.db"
    _prediction_schema(path)
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TRIGGER trg_token_prediction_custom AFTER INSERT ON creator_funders BEGIN SELECT 1; END"
    )
    conn.commit()
    conn.close()

    with pytest.raises(RuntimeError, match="unknown token-prediction triggers"):
        _decommission_token_prediction_triggers(str(path))

    conn = sqlite3.connect(path)
    assert conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='trigger' AND name LIKE 'trg_token_prediction_%'"
    ).fetchone()[0] == 6
    conn.close()


def test_all_live_prediction_producers_are_disabled_in_source():
    creator_funding = (ROOT / "src/core/creator_funding_worker.py").read_text()
    graph_runner = (ROOT / "scripts/run_graph_analyzers.py").read_text()
    main = (ROOT / "src/core/main.py").read_text()
    listener = (ROOT / "src/core/pumpfun_curve_listener.py").read_text()

    process_job = creator_funding[creator_funding.index("async def _process_job"):creator_funding.index("def _adaptive_batch")]
    assert "TokenPredictionBuilder" not in process_job
    assert "'TokenPredictionBuilder'," not in graph_runner
    assert "PREDICTION_DAEMON_ENABLED" not in main
    assert "Token prediction daemon started" not in main
    assert "TOKEN_PREDICTION_RUNTIME_ENABLED = False" in listener
    assert "if TOKEN_PREDICTION_RUNTIME_ENABLED:" in listener
    assert "token_rescore_queue" not in listener


def test_prediction_http_is_explicitly_retired_and_navigation_removed():
    main = (ROOT / "src/core/main.py").read_text()
    sidebar = (ROOT / "templates/partials/sidebar.html").read_text()
    assert '"status": "decommissioned"' in main
    assert 'path.startswith("/api/predictions")' in main
    assert 'path == "/api/trading-sim/auto-buy-predictions"' in main
    assert 'href="/predictions"' not in sidebar
