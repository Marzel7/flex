"""Playbooks index must render artifact views without DB-only metric fields."""

import sqlite3
from pathlib import Path

from flask import Flask


ROOT = Path(__file__).resolve().parents[1]


def _client(monkeypatch, tmp_path, playbook):
    from src.core import db
    from src.ops import operation_playbook_registry, operator_lifecycle_projection
    from src.ops.operator_routes import operator_bp

    ops_path = tmp_path / "ops.db"
    sqlite3.connect(ops_path).close()
    monkeypatch.setattr(db, "OPS_DB_PATH", ops_path)
    monkeypatch.setattr(operator_lifecycle_projection, "list_playbook_projections", lambda conn: [])
    monkeypatch.setattr(operation_playbook_registry, "all_playbook_views", lambda rows: [playbook])
    app = Flask(__name__, template_folder=str(ROOT / "templates"))
    app.register_blueprint(operator_bp)
    app.testing = True
    return app.test_client()


def test_artifact_playbook_without_optional_metrics_renders(monkeypatch, tmp_path):
    artifact = {
        "operation_id": "watchtower",
        "display_name": "Watchtower",
        "playbook_version": "v1",
        "qualification_status": "QUALIFIED",
        "token_data_completion": "COMPLETE",
        "sample_size": 51,
    }
    response = _client(monkeypatch, tmp_path, artifact).get("/operations/playbooks")
    assert response.status_code == 200
    assert b"Watchtower" in response.data
    assert b"Sample: 51" in response.data
    assert b"price 0.00%" not in response.data


def test_db_playbook_with_optional_metrics_preserves_display(monkeypatch, tmp_path):
    db_view = {
        "operation_id": "example",
        "display_name": "Example",
        "playbook_version": "v2",
        "qualification_status": "QUALIFIED",
        "token_data_completion": "COMPLETE",
        "sample_size": 10,
        "price_data_coverage": 87.5,
        "fx_coverage": 99.0,
        "trigger_status": True,
    }
    response = _client(monkeypatch, tmp_path, db_view).get("/operations/playbooks")
    assert response.status_code == 200
    assert b"price 87.50%" in response.data
    assert b"FX 99.00%" in response.data
