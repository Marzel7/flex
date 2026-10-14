"""DEV036 registry authority and unavailable-state contracts."""
from __future__ import annotations

import sqlite3

from flask import Flask
import pytest

from src.core import db as core_db
from src.ops import operator_routes
from src.ops.operator_reader import OperatorReader, RegistryUnavailableError


def test_missing_registry_database_is_unavailable_not_an_empty_registry(tmp_path):
    reader = OperatorReader(str(tmp_path / "missing-wt-ops.db"))

    with pytest.raises(RegistryUnavailableError, match="storage is unavailable"):
        reader.fetch_active_manual_operators()


def test_registry_route_returns_explicit_unavailable_state(monkeypatch, tmp_path):
    app = Flask(__name__)
    app.register_blueprint(operator_routes.operator_bp)
    monkeypatch.setattr(operator_routes, "_store", OperatorReader(str(tmp_path / "missing-wt-ops.db")))

    with app.test_client() as client:
        response = client.get("/api/ops/operators")

    assert response.status_code == 503
    assert response.get_json() == {
        "ok": False,
        "operators": [],
        "count": None,
        "error": "REGISTRY_STORAGE_UNAVAILABLE",
    }


def test_coverage_route_uses_the_bound_operations_authority(monkeypatch, tmp_path):
    db_path = tmp_path / "wt_ops_v2.db"
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
            """
            CREATE TABLE wt_walkback_queue (
                mint TEXT, status TEXT, completed_at INTEGER, subprov TEXT,
                funder_block_time INTEGER, funder_wallet TEXT
            );
            CREATE TABLE operator_launch_membership (mint TEXT, operator_id TEXT);
            CREATE TABLE operators (operator_id TEXT);
            CREATE TABLE operation_registry_dispositions (operator_id TEXT, disposition TEXT);
            """
        )
    monkeypatch.setattr(core_db, "OPS_DB_PATH", str(db_path))
    monkeypatch.setattr("src.ops.live_potential_activity.cached_activity", lambda _path: ({}, {"status": "HEALTHY"}))
    app = Flask(__name__)
    app.register_blueprint(operator_routes.operator_bp)

    with app.test_client() as client:
        response = client.get("/api/ops/operators/coverage-24h")

    assert response.status_code == 200
    assert response.get_json()["eligible"] == 0


def test_dev_runtime_preserves_explicit_operations_database_binding():
    source = (operator_routes.__file__.replace("ops/operator_routes.py", "dev_runtime.py"))
    with open(source, encoding="utf-8") as runtime:
        assert 'os.environ.get(\n            "WT_OPS_DB_PATH"' in runtime.read()
