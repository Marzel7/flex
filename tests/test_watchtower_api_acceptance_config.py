"""Focused regression coverage for the relocated Watchtower API runtime."""

from __future__ import annotations

import ast
import copy
import json
import os
import sqlite3
import time
from pathlib import Path

from flask import Flask, jsonify

from src.apis import price_api


ROOT = Path(__file__).resolve().parents[1]


def test_main_propagates_explicit_db_path_to_flask_configuration():
    """Price API must not fall back to a database relative to its source checkout."""
    tree = ast.parse((ROOT / "src/core/main.py").read_text())
    assert any(
        isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Subscript)
            and isinstance(target.value, ast.Attribute)
            and isinstance(target.value.value, ast.Name)
            and target.value.value.id == "app"
            and isinstance(target.value.attr, str)
            and target.value.attr == "config"
            and isinstance(target.slice, ast.Constant)
            and target.slice.value == "DATABASE"
            for target in node.targets
        )
        and isinstance(node.value, ast.Name)
        and node.value.id == "DB_PATH"
        for node in tree.body
    )


def test_price_api_uses_flask_database_authority_without_relative_fallback(monkeypatch, tmp_path):
    db_path = tmp_path / "canonical.db"
    app = Flask(__name__)
    app.config["DATABASE"] = str(db_path)
    seen: list[str] = []

    def capture(path):
        seen.append(path)

    monkeypatch.setattr(price_api, "_ensure_metadata_cache_table", capture)
    price_api.register_price_api(app)

    assert seen == [str(db_path)]
    assert price_api._db_path == str(db_path)


def test_healthz_ignores_stale_heartbeats_for_disabled_workers(monkeypatch):
    """A retained heartbeat must not make the active API unhealthy by itself."""
    monkeypatch.delenv("HEALTHZ_REQUIRED_WORKERS", raising=False)
    tree = ast.parse((ROOT / "src/core/main.py").read_text())
    healthz = copy.deepcopy(next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "healthz"))
    healthz.decorator_list = []
    ast.fix_missing_locations(healthz)

    calls = 0

    class Cursor:
        def __init__(self, rows):
            self.rows = rows

        def fetchall(self):
            return self.rows

    class Connection:
        row_factory = None

        def execute(self, _sql):
            return Cursor([
                {"worker_name": "creator-funding", "last_seen": 990, "status": "ok"},
                {"worker_name": "creator-resolution", "last_seen": 990, "status": "ok"},
                {"worker_name": "operation-monitor", "last_seen": 1, "status": "stopped"},
            ])

    class ManagedConnection:
        def __enter__(self):
            nonlocal calls
            calls += 1
            return Connection()

        def __exit__(self, *_exc):
            return False

    namespace = {
        "managed_db_connect": lambda *_args, **_kwargs: ManagedConnection(),
        "DB_PATH": "/isolated/canonical.db",
        "sqlite3": type("SQLite", (), {"Row": object})(),
        "os": os,
        "jsonify": jsonify,
    }
    exec(compile(ast.Module(body=[healthz], type_ignores=[]), "<healthz>", "exec"), namespace)
    monkeypatch.setattr("time.time", lambda: 1_000)

    with Flask(__name__).app_context():
        response, status = namespace["healthz"]()

    assert calls == 2
    assert status == 200, response.get_json()
    assert response.get_json()["stale_workers"] == []


def test_pumpfun_live_route_reads_complete_isolated_fixture_without_timeout(tmp_path):
    """Exercise the deployed route body with its complete read-only table contract."""
    db_path = tmp_path / "canonical.db"
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE token_analysis (
            mint TEXT, created_at INTEGER, earliest_tx_creator TEXT, pf_ws_creator TEXT,
            bonding_curve_pda TEXT, lifecycle_stage TEXT, analyzed_at INTEGER,
            source_platform TEXT, migration_signal_updated_at INTEGER,
            pumpswap_pool_address TEXT, pool_address TEXT, migration_slot INTEGER
        );
        CREATE TABLE metadata_cache (mint TEXT, symbol TEXT, name TEXT);
        CREATE TABLE creator_funding_queue (
            mint TEXT, funding_enqueued_at INTEGER, funding_extracted_at INTEGER,
            status TEXT, source TEXT
        );
        CREATE TABLE creator_self_funding (creator_address TEXT, is_self_funding INTEGER, self_funding_percentage REAL);
        CREATE TABLE network_membership (creator_address TEXT, network_name TEXT);
        CREATE TABLE intelligence_refresh_candidates (target_address TEXT, target_type TEXT, priority INTEGER, reason_codes TEXT);
        CREATE TABLE coordinated_creator_edges (creator_a TEXT, creator_b TEXT);
        CREATE TABLE farm_cluster_members (wallet_address TEXT, wallet_role TEXT);
    """)
    now = int(time.time())
    conn.execute(
        "INSERT INTO token_analysis VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        ("fixture-mint", now, "creator", "creator", "curve", "bonding_curve", now,
         "pumpfun", None, None, None, None),
    )
    conn.execute("INSERT INTO metadata_cache VALUES (?,?,?)", ("fixture-mint", "FIX", "Fixture"))
    conn.commit()
    conn.close()

    tree = ast.parse((ROOT / "src/core/main.py").read_text())
    wanted = {"_fetch_pumpfun_live", "api_pumpfun_live"}
    nodes = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in wanted:
            copied = copy.deepcopy(node)
            copied.decorator_list = []
            nodes.append(copied)
    assert {node.name for node in nodes} == wanted
    module = ast.Module(body=nodes, type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {
        "sqlite3": sqlite3,
        "os": os,
        "time": time,
        "jsonify": jsonify,
        "_pumpfun_live_cache": {"data": None, "at": 0.0, "refreshing": False},
        "_PUMPFUN_LIVE_TTL": 10,
        "DB_PATH": str(db_path),
    }
    exec(compile(module, "<pumpfun-live>", "exec"), namespace)

    with Flask(__name__).app_context():
        response = namespace["api_pumpfun_live"]()

    assert response.status_code == 200
    payload = json.loads(response.get_data(as_text=True))
    assert set(payload) == {"births", "near_migration", "migrations", "portal_tracking_count", "ts"}
    assert [row["mint"] for row in payload["births"]] == ["fixture-mint"]
