"""Regression contract for the retained vault dashboard projection."""
from __future__ import annotations

from pathlib import Path
import importlib.util
import os
import sqlite3


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "src/core/flex_dashboard_routes.py").read_text()


def test_legacy_price_history_routes_and_reads_are_absent():
    for value in (
        "/api/token-behaviour",
        "/api/token-intelligence",
        "'/token-behaviour'",
        "'/token-intelligence'",
        "'/snapshots'",
        "token_price_snapshots",
        "token_snapshot_counts",
        "token_behavior",
        "token_behavior_history",
    ):
        assert value not in SOURCE


def test_vault_and_classifier_routes_remain_registered():
    for route in (
        "'/vaults'",
        "'/api/vaults'",
        "'/api/vaults/stats/summary'",
        "'/api/vaults/stats/discovery-health'",
        "'/api/vaults/shared-vaults'",
        "'/api/vaults/shared-vaults/<vault_address>/tokens'",
        "'/api/vaults/launch-clusters'",
        "'/api/vaults/<mint>'",
    ):
        assert route in SOURCE
    assert "get_classifier" in SOURCE


def test_vault_list_serializes_current_discovery_fields_without_history(tmp_path):
    db_path = tmp_path / "vaults.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE token_pool_accounts ("
        "mint TEXT, pool_address TEXT, base_account TEXT, quote_account TEXT, "
        "base_token TEXT, quote_token TEXT, base_decimals INTEGER, quote_decimals INTEGER, "
        "pool_program TEXT, vault_validation_status TEXT, vault_resolution_state TEXT, "
        "vault_discovery_strategy TEXT, discovery_method TEXT, vault_discovery_attempts INTEGER, "
        "vault_discovery_time_secs REAL, created_at INTEGER, last_vault_validation_at INTEGER, "
        "vault_resolved_at INTEGER)"
    )
    conn.execute(
        "INSERT INTO token_pool_accounts VALUES "
        "('mint', 'pool', 'base', 'quote', 'base-token', 'quote-token', 6, 9, 'program', "
        "'validated', 'resolved', 'tx_parsing', 'tx_parsing', 1, 2.5, 10, 12, 12)"
    )
    conn.commit()
    conn.close()

    previous = os.environ.get("DB_PATH")
    os.environ["DB_PATH"] = str(db_path)
    try:
        spec = importlib.util.spec_from_file_location("flex_dashboard_routes_test", ROOT / "src/core/flex_dashboard_routes.py")
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        from flask import Flask
        app = Flask(__name__)
        module.register_dashboard_routes(app)
        response = app.test_client().get("/api/vaults")
    finally:
        if previous is None:
            os.environ.pop("DB_PATH", None)
        else:
            os.environ["DB_PATH"] = previous

    assert response.status_code == 200
    vault = response.get_json()["vaults"][0]
    assert vault["vault_discovery_strategy"] == "tx_parsing"
    assert "category" not in vault
    assert "confidence" not in vault
    assert "snapshot_count" not in vault
