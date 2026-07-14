"""X21B read-only API route contracts."""
from __future__ import annotations

import sqlite3

import pytest
from flask import Flask

from src.ops.provisioning_edges import capture_provisioning_relationship, ensure_schema
from src.ops.provisioning_edges_routes import register_provisioning_edges_routes


@pytest.fixture
def app(tmp_path, monkeypatch):
    db_path = tmp_path / "wt_ops_v2.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    capture_provisioning_relationship(
        conn, source_mint="mintA", treasury="T1", subprov="S1", creator="C1",
        treasury_to_subprov_block_time=1000, treasury_to_subprov_amount_sol=0.203,
        treasury_to_subprov_mechanism="WSOL_WRAP_CLOSE",
        subprov_to_creator_block_time=1018, creator_launch_time=1026,
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("WT_OPS_DB_PATH", str(db_path))
    flask_app = Flask(__name__)
    register_provisioning_edges_routes(flask_app)
    return flask_app


def test_edges_endpoint_returns_observed_edges(app):
    with app.test_client() as client:
        resp = client.get("/api/ops-v2/provisioning-edges/T1")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert len(data["outgoing"]) == 1
        assert data["outgoing"][0]["to_wallet"] == "S1"


def test_sessions_endpoint_returns_the_session(app):
    with app.test_client() as client:
        resp = client.get("/api/ops-v2/provisioning-sessions/T1")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["sessions"]) == 1
        assert data["sessions"][0]["source_mint"] == "mintA"


def test_timing_endpoint_returns_a_plain_mean(app):
    with app.test_client() as client:
        resp = client.get("/api/ops-v2/provisioning-timing/T1")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["session_count"] == 1
        assert data["mean_treasury_to_subprov_latency_seconds"] == 18


def test_unknown_wallet_returns_empty_not_an_error(app):
    with app.test_client() as client:
        resp = client.get("/api/ops-v2/provisioning-edges/NeverSeenWallet")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["outgoing"] == []
        assert data["incoming"] == []
