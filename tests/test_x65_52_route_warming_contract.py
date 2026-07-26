"""X65.52 — route-level contract test: allow_warming=1 opts a caller into
the non-blocking cold-cache response; the default (no param) behaviour is
completely unchanged, preserving every existing caller of these two
routes exactly as before this task.
"""
from __future__ import annotations

import threading
import time

import pytest
from flask import Flask

import src.core.operation_dashboard_routes as routes


@pytest.fixture(autouse=True)
def _reset_caches(monkeypatch):
    from src.ops.swr_cache import SWRCache
    monkeypatch.setattr(routes, "_OPERATIONAL_INTELLIGENCE_CACHE", SWRCache(ttl_seconds=300))
    monkeypatch.setattr(routes, "_INVESTIGATION_PIPELINE_CACHE", SWRCache(ttl_seconds=300))
    yield


@pytest.fixture
def client():
    app = Flask(__name__)
    app.register_blueprint(routes.ops_dashboard_bp)
    return app.test_client()


def test_default_request_still_blocks_on_cold_cache_unchanged(client, monkeypatch):
    calls = []

    def fake_build(*a, **k):
        calls.append(1)
        return {
            "generated_at": 0, "total_launches": 0, "conserved": True,
            "topology_summary": [], "behaviour_summary": [], "canonical_behaviour_summary": [],
            "canonical_behaviour_conserved": True, "campaign_summary": [], "campaign_conserved": True,
            "mechanism_summary": [], "creator_identity_summary": [], "disposable_creator_score_distribution": {},
            "operation_summary": {}, "quick_birth_migration_summary": {}, "diagnostics": {}, "records": {},
        }

    import src.ops.operational_intelligence as oi_module
    monkeypatch.setattr(oi_module, "build_operational_intelligence", fake_build)

    resp = client.get("/api/ops-v2/operational-intelligence?window=24h")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert "status" not in data  # no warming envelope for the default path
    assert len(calls) == 1  # the request itself performed the (fake, fast) build


def test_allow_warming_returns_202_immediately_on_cold_cache(client, monkeypatch):
    release = threading.Event()

    def slow_build(*a, **k):
        release.wait(timeout=5)
        return {
            "generated_at": 0, "total_launches": 0, "conserved": True,
            "topology_summary": [], "behaviour_summary": [], "canonical_behaviour_summary": [],
            "canonical_behaviour_conserved": True, "campaign_summary": [], "campaign_conserved": True,
            "mechanism_summary": [], "creator_identity_summary": [], "disposable_creator_score_distribution": {},
            "operation_summary": {}, "quick_birth_migration_summary": {}, "diagnostics": {}, "records": {},
        }

    import src.ops.operational_intelligence as oi_module
    monkeypatch.setattr(oi_module, "build_operational_intelligence", slow_build)

    start = time.perf_counter()
    resp = client.get("/api/ops-v2/operational-intelligence?window=24h&allow_warming=1")
    elapsed = time.perf_counter() - start

    assert resp.status_code == 202
    data = resp.get_json()
    assert data["ok"] is True
    assert data["status"] == "warming"
    assert data["window"] == "24h"
    assert data["retry_after_seconds"] > 0
    assert elapsed < 1.0  # never waited for slow_build
    release.set()  # let the background build finish so it doesn't leak


def test_allow_warming_returns_full_data_once_warm(client, monkeypatch):
    def fast_build(*a, **k):
        return {
            "generated_at": 0, "total_launches": 5, "conserved": True,
            "topology_summary": [], "behaviour_summary": [], "canonical_behaviour_summary": [],
            "canonical_behaviour_conserved": True, "campaign_summary": [], "campaign_conserved": True,
            "mechanism_summary": [], "creator_identity_summary": [], "disposable_creator_score_distribution": {},
            "operation_summary": {}, "quick_birth_migration_summary": {}, "diagnostics": {}, "records": {},
        }

    import src.ops.operational_intelligence as oi_module
    monkeypatch.setattr(oi_module, "build_operational_intelligence", fast_build)

    # First call: cold, kicks off background build, returns warming.
    resp1 = client.get("/api/ops-v2/operational-intelligence?window=24h&allow_warming=1")
    assert resp1.status_code == 202

    # Poll until warm (bounded).
    deadline = time.time() + 2
    resp2 = None
    while time.time() < deadline:
        resp2 = client.get("/api/ops-v2/operational-intelligence?window=24h&allow_warming=1")
        if resp2.status_code == 200:
            break
        time.sleep(0.02)
    assert resp2.status_code == 200
    data = resp2.get_json()
    assert data["ok"] is True
    assert data["total_launches"] == 5
    assert "status" not in data


def test_pipeline_health_route_supports_the_same_warming_contract(client, monkeypatch):
    release = threading.Event()

    def slow_build(*a, **k):
        release.wait(timeout=5)
        return {"generated_at": 0, "total_launches": 0, "conserved": True, "buckets": [], "assignments": {}}

    import src.ops.investigation_pipeline as pipeline_module
    monkeypatch.setattr(pipeline_module, "build_pipeline_health", slow_build)

    start = time.perf_counter()
    resp = client.get("/api/ops-v2/investigation-pipeline?window=24h&allow_warming=1")
    elapsed = time.perf_counter() - start

    assert resp.status_code == 202
    data = resp.get_json()
    assert data["status"] == "warming"
    assert elapsed < 1.0
    release.set()


def test_pipeline_health_default_request_unaffected(client, monkeypatch):
    def fast_build(*a, **k):
        return {"generated_at": 0, "total_launches": 0, "conserved": True, "buckets": [], "assignments": {}}

    import src.ops.investigation_pipeline as pipeline_module
    monkeypatch.setattr(pipeline_module, "build_pipeline_health", fast_build)

    resp = client.get("/api/ops-v2/investigation-pipeline?window=24h")
    assert resp.status_code == 200
    assert "status" not in resp.get_json()
