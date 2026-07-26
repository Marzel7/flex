"""X65.51 — Diagnose whether X65.50's fix leaves a cold-start latency
window, via configurable prewarm delay + stage/lock/compute timing logs.

Does not change concurrency/locking semantics (X65.50's fix stands
unmodified) -- purely adds:
  1. WT_PREWARM_START_DELAY_SECONDS env var (default 1s, down from a
     hardcoded 7s) controlling prewarm's initial startup delay.
  2. Explicit prewarm start/complete log lines per window/family.
  3. lock_wait_ms/compute_ms split logged on every COLD build (both cache
     families), so a slow /discovery request can be attributed to
     "queued behind another cold build" vs "the build itself is slow."
  4. Route-level stage timing on /api/ops-v2/operational-intelligence
     (cache-fetch elapsed + total route time + post-cache work time).
"""
from __future__ import annotations

import logging
import os

import pytest

import src.core.operation_dashboard_routes as routes


@pytest.fixture(autouse=True)
def _reset_caches(monkeypatch):
    from src.ops.swr_cache import SWRCache
    monkeypatch.setattr(routes, "_OPERATIONAL_INTELLIGENCE_CACHE", SWRCache(ttl_seconds=300))
    monkeypatch.setattr(routes, "_INVESTIGATION_PIPELINE_CACHE", SWRCache(ttl_seconds=300))
    yield


def test_prewarm_delay_defaults_to_one_second_not_seven(monkeypatch):
    monkeypatch.delenv("WT_PREWARM_START_DELAY_SECONDS", raising=False)
    import inspect
    source = inspect.getsource(routes.prewarm_operational_intelligence_cache)
    assert 'os.environ.get("WT_PREWARM_START_DELAY_SECONDS","1")' in source.replace(" ", "")


def test_prewarm_delay_is_configurable_via_env_var(monkeypatch, caplog):
    monkeypatch.setenv("WT_PREWARM_START_DELAY_SECONDS", "3.5")
    monkeypatch.setattr(routes, "_get_operational_intelligence", lambda ws: ({}, {}))
    monkeypatch.setattr(routes, "_get_pipeline_health", lambda ws: ({}, {}))

    spawned = []
    real_thread_cls = routes.threading.Thread

    class _TrackedThread(real_thread_cls):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            spawned.append(self)

    monkeypatch.setattr(routes.threading, "Thread", _TrackedThread)
    monkeypatch.setattr(routes.time, "sleep", lambda s: None)  # keep test fast
    with caplog.at_level(logging.WARNING):
        routes.prewarm_operational_intelligence_cache()
    spawned[0].join(timeout=2)
    assert "delay=3.5s" in caplog.text


def test_prewarm_logs_start_and_complete_per_window_and_family(monkeypatch, caplog):
    monkeypatch.setattr(routes, "_get_operational_intelligence", lambda ws: ({}, {}))
    monkeypatch.setattr(routes, "_get_pipeline_health", lambda ws: ({}, {}))
    monkeypatch.setattr(routes.time, "sleep", lambda s: None)

    spawned = []
    real_thread_cls = routes.threading.Thread

    class _TrackedThread(real_thread_cls):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            spawned.append(self)

    monkeypatch.setattr(routes.threading, "Thread", _TrackedThread)
    with caplog.at_level(logging.WARNING):
        routes.prewarm_operational_intelligence_cache()
    spawned[0].join(timeout=2)

    assert "prewarm start window=24h family=operational" in caplog.text
    assert "prewarm complete window=24h family=operational" in caplog.text
    assert "prewarm start window=24h family=pipeline_health" in caplog.text
    assert "prewarm complete window=24h family=pipeline_health" in caplog.text
    # `all` must never appear -- excluded from startup prewarm (X65.50).
    assert "window=all" not in caplog.text


def test_cold_build_logs_lock_wait_and_compute_time_split(monkeypatch, caplog):
    def fake_build(*a, **k):
        return {"total_launches": 0, "records": {}}

    import src.ops.operational_intelligence as oi_module
    monkeypatch.setattr(oi_module, "build_operational_intelligence", fake_build)
    with caplog.at_level(logging.WARNING):
        routes._get_operational_intelligence(86400)
    assert "cold_build window_seconds=86400" in caplog.text
    assert "lock_wait_ms=" in caplog.text
    assert "compute_ms=" in caplog.text


def test_route_logs_cache_state_and_stage_timing(monkeypatch, caplog):
    monkeypatch.setattr(
        routes, "_get_operational_intelligence",
        lambda ws: ({
            "generated_at": 0, "total_launches": 0, "conserved": True,
            "topology_summary": [], "behaviour_summary": [], "canonical_behaviour_summary": [],
            "canonical_behaviour_conserved": True, "campaign_summary": [], "campaign_conserved": True,
            "mechanism_summary": [], "creator_identity_summary": [], "disposable_creator_score_distribution": {},
            "operation_summary": {}, "quick_birth_migration_summary": {}, "diagnostics": {}, "records": {},
        }, {"state": "fresh", "age_seconds": 1.0}),
    )
    app = routes.ops_dashboard_bp
    from flask import Flask
    test_app = Flask(__name__)
    test_app.register_blueprint(app)
    client = test_app.test_client()
    with caplog.at_level(logging.INFO):
        resp = client.get("/api/ops-v2/operational-intelligence?window=24h")
    assert resp.status_code == 200
    assert "stage=operational_intelligence_loaded" in caplog.text
    assert "cache_state=fresh" in caplog.text
    assert "stage=response_built" in caplog.text
    assert "post_cache_ms=" in caplog.text


def test_pipeline_health_family_documents_duplication_but_doesnt_fix_it_yet():
    # This is an explicit follow-up flag, not a behavior change -- assert
    # the comment exists so the noted optimization isn't silently lost,
    # without asserting any new merge/reuse logic (none was implemented,
    # per the deliberately narrower scope of this task).
    import inspect
    source = inspect.getsource(routes._get_pipeline_health)
    assert "fully independent classifier" in source
    assert "does not reuse" in source
