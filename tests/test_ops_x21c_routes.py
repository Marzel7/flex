"""X21C read-only API contracts: discovery-triage summary/queue endpoints."""
from __future__ import annotations

import json
import sqlite3

import pytest
from flask import Blueprint, Flask

from src.ops.discovery_triage import build_investigation_queue, build_triage_summary


@pytest.fixture
def app(tmp_path, monkeypatch):
    db_path = tmp_path / "wt_ops_v2.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE wt_attribution_outcomes (
         mint TEXT PRIMARY KEY, outcome_type TEXT, stop_reason TEXT, terminal_entity TEXT,
         terminal_entity_type TEXT, confidence TEXT, evidence_json TEXT, operator_id TEXT,
         should_seed_emerging_operator INTEGER, should_retry INTEGER, completed_at INTEGER
        );
        CREATE TABLE wt_treasury_review (treasury TEXT PRIMARY KEY, status TEXT, distinct_subprovs INTEGER, distinct_creators INTEGER);
        CREATE TABLE wt_unknown_infrastructure_registry (terminal_entity TEXT PRIMARY KEY);
        CREATE TABLE wt_provisioning_edges (edge_id TEXT PRIMARY KEY, edge_type TEXT, from_wallet TEXT, to_wallet TEXT, observation_count INTEGER);
        CREATE TABLE wt_provisioning_sessions (session_id TEXT PRIMARY KEY, source_mint TEXT, treasury TEXT, subprov TEXT, creator TEXT, recorded_at INTEGER);
    """)
    evidence = json.dumps({"creator": "CREATOR1", "treasuries": [], "subprovisioners": []})
    conn.execute(
        "INSERT INTO wt_attribution_outcomes VALUES "
        "('mintA','INSUFFICIENT_EVIDENCE','x','CREATOR1','UNKNOWN','LOW',?,NULL,0,1,1000)",
        (evidence,),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(
        "src.core.operation_dashboard_routes._conn",
        lambda: sqlite3.connect(f"file:{db_path}?mode=ro", uri=True),
    )
    from src.core import operation_dashboard_routes as odr
    flask_app = Flask(__name__)
    flask_app.register_blueprint(odr.ops_dashboard_bp)
    return flask_app


def test_summary_endpoint_returns_real_counts(app):
    with app.test_client() as client:
        resp = client.get("/api/ops-v2/discovery-triage/summary")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["total_terminal_outcomes"] == 1


def test_queue_endpoint_returns_grouped_entries(app):
    with app.test_client() as client:
        resp = client.get("/api/ops-v2/discovery-triage/queue?limit=10")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert len(data["entries"]) == 1
        assert data["entries"][0]["entity"] == "CREATOR1"


def test_queue_endpoint_applies_filter(app):
    with app.test_client() as client:
        resp = client.get("/api/ops-v2/discovery-triage/queue?filter=NO_LINEAGE")
        data = resp.get_json()
        assert data["entries"] == []  # CREATOR1 bucket is CREATOR_IDENTIFIED, not NO_LINEAGE
