"""X65.57 — Persisted Discovery Intelligence Snapshots (route-level tests).

Covers:
  - hydrate_intelligence_caches_from_snapshots(): startup hydration reads
    on-disk snapshots (if present) into both SWR caches before any request
    is served.
  - cache_status response field: fresh / stale_refreshing /
    warming_no_snapshot, additive alongside the existing cache_state field.
  - The two success-path caches (_OPERATIONAL_INTELLIGENCE_CACHE,
    _INVESTIGATION_PIPELINE_CACHE) persist a snapshot after a successful
    build via their on_success hooks.
"""
from __future__ import annotations

import time

import pytest
from flask import Flask

import src.core.operation_dashboard_routes as routes
from src.ops.swr_cache import SWRCache


@pytest.fixture(autouse=True)
def _reset_caches(monkeypatch):
    monkeypatch.setattr(routes, "_OPERATIONAL_INTELLIGENCE_CACHE",
                         SWRCache(ttl_seconds=300, on_success=routes._persist_operational_intelligence_snapshot))
    monkeypatch.setattr(routes, "_INVESTIGATION_PIPELINE_CACHE",
                         SWRCache(ttl_seconds=300, on_success=routes._persist_pipeline_health_snapshot))
    yield


@pytest.fixture
def client():
    app = Flask(__name__)
    app.register_blueprint(routes.ops_dashboard_bp)
    return app.test_client()


@pytest.fixture(autouse=True)
def _isolated_snapshot_dir(tmp_path, monkeypatch):
    import src.ops.intelligence_snapshots as snap
    monkeypatch.setattr(snap, "SNAPSHOT_DIR", str(tmp_path / "snapshots"))
    yield


def _fake_intel(total=5):
    return {
        "generated_at": 0, "total_launches": total, "conserved": True,
        "topology_summary": [], "behaviour_summary": [], "canonical_behaviour_summary": [],
        "canonical_behaviour_conserved": True, "campaign_summary": [], "campaign_conserved": True,
        "mechanism_summary": [], "creator_identity_summary": [], "disposable_creator_score_distribution": {},
        "operation_summary": {}, "quick_birth_migration_summary": {}, "diagnostics": {}, "records": {},
    }


def _fake_pipeline(total=5):
    return {"generated_at": 0, "total_launches": total, "conserved": True, "buckets": [], "assignments": {}}


def test_hydrate_finds_nothing_when_no_snapshots_exist_and_does_not_raise():
    routes.hydrate_intelligence_caches_from_snapshots()
    assert routes._OPERATIONAL_INTELLIGENCE_CACHE.state_of(86400) is None
    assert routes._INVESTIGATION_PIPELINE_CACHE.state_of(86400) is None


def test_a_successful_build_persists_a_snapshot_and_hydration_picks_it_up_in_a_fresh_cache(monkeypatch):
    import src.ops.operational_intelligence as oi_module
    monkeypatch.setattr(oi_module, "build_operational_intelligence", lambda *a, **k: _fake_intel(total=7))

    # First "process": build once, which persists a snapshot via on_success.
    routes._get_operational_intelligence(86400)

    # Simulate a fresh process: a brand-new, empty cache instance.
    fresh_cache = SWRCache(ttl_seconds=300, on_success=routes._persist_operational_intelligence_snapshot)
    monkeypatch.setattr(routes, "_OPERATIONAL_INTELLIGENCE_CACHE", fresh_cache)
    assert fresh_cache.state_of(86400) is None

    routes.hydrate_intelligence_caches_from_snapshots()
    assert fresh_cache.state_of(86400) is not None
    intel, meta = routes._get_operational_intelligence(86400)
    assert intel["total_launches"] == 7
    assert meta["state"] in ("fresh", "stale")  # hydrated, not a cold compute


def test_hydration_never_overwrites_an_already_populated_key(monkeypatch):
    import src.ops.operational_intelligence as oi_module
    monkeypatch.setattr(oi_module, "build_operational_intelligence", lambda *a, **k: _fake_intel(total=1))
    routes._get_operational_intelligence(86400)  # persists a snapshot with total=1

    monkeypatch.setattr(oi_module, "build_operational_intelligence", lambda *a, **k: _fake_intel(total=2))
    # A DIFFERENT cache instance, but pre-populated with a live value BEFORE
    # hydration runs -- hydration must not clobber it.
    live_cache = SWRCache(ttl_seconds=300, on_success=routes._persist_operational_intelligence_snapshot)
    monkeypatch.setattr(routes, "_OPERATIONAL_INTELLIGENCE_CACHE", live_cache)
    live_cache.get(86400, lambda: _fake_intel(total=99))

    routes.hydrate_intelligence_caches_from_snapshots()
    intel, _ = routes._get_operational_intelligence(86400)
    assert intel["total_launches"] == 99  # untouched by the total=1 snapshot on disk


def test_response_cache_status_maps_fresh():
    assert routes._response_cache_status({"state": "fresh"}) == "fresh"


def test_response_cache_status_maps_stale_and_refreshing_to_stale_refreshing():
    assert routes._response_cache_status({"state": "stale"}) == "stale_refreshing"
    assert routes._response_cache_status({"state": "refreshing"}) == "stale_refreshing"


def test_response_cache_status_maps_warming_to_warming_no_snapshot():
    assert routes._response_cache_status({"state": "warming"}) == "warming_no_snapshot"


def test_operational_intelligence_route_includes_cache_status_field(client, monkeypatch):
    import src.ops.operational_intelligence as oi_module
    monkeypatch.setattr(oi_module, "build_operational_intelligence", lambda *a, **k: _fake_intel())

    resp = client.get("/api/ops-v2/operational-intelligence?window=24h")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["cache_status"] == "fresh"
    assert data["cache_state"] == "fresh"  # existing field untouched


def test_pipeline_health_route_includes_cache_status_field(client, monkeypatch):
    import src.ops.investigation_pipeline as pipeline_module
    monkeypatch.setattr(pipeline_module, "build_pipeline_health", lambda *a, **k: _fake_pipeline())

    resp = client.get("/api/ops-v2/investigation-pipeline?window=24h")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["cache_status"] == "fresh"


def test_warming_202_response_includes_warming_no_snapshot_status(client, monkeypatch):
    import threading
    release = threading.Event()

    def slow_build(*a, **k):
        release.wait(timeout=5)
        return _fake_intel()

    import src.ops.operational_intelligence as oi_module
    monkeypatch.setattr(oi_module, "build_operational_intelligence", slow_build)

    resp = client.get("/api/ops-v2/operational-intelligence?window=24h&allow_warming=1")
    assert resp.status_code == 202
    data = resp.get_json()
    assert data["status"] == "warming"
    assert data["cache_status"] == "warming_no_snapshot"
    release.set()


def test_a_stale_hydrated_snapshot_reports_stale_refreshing_not_warming(monkeypatch):
    # This is the core X65.57 success criterion: after hydration, a request
    # for an old-but-present snapshot must NEVER report warming_no_snapshot
    # -- there IS a snapshot, it's just old, so a background refresh should
    # run while the (stale) value is served immediately.
    import src.ops.operational_intelligence as oi_module
    monkeypatch.setattr(oi_module, "build_operational_intelligence", lambda *a, **k: _fake_intel(total=3))

    stale_cache = SWRCache(ttl_seconds=300, on_success=routes._persist_operational_intelligence_snapshot)
    monkeypatch.setattr(routes, "_OPERATIONAL_INTELLIGENCE_CACHE", stale_cache)
    stale_cache.hydrate(86400, _fake_intel(total=3), time.time() - 3600)  # 1h old, TTL=300s

    intel, meta = routes._get_operational_intelligence(86400)
    assert intel["total_launches"] == 3  # served the hydrated snapshot value
    assert routes._response_cache_status(meta) == "stale_refreshing"
