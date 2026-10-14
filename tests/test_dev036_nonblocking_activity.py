import time

from flask import Flask

from src.ops import live_potential_activity as activity, operator_routes


def test_cold_expired_and_busy_cache_never_build_history(monkeypatch):
    monkeypatch.setattr(activity, "_build_aggregate", lambda *a, **k: (_ for _ in ()).throw(AssertionError("UI history scan")))
    monkeypatch.setattr(activity, "_CACHE_VALUE", None)
    assert activity.cached_activity("db")[1]["status"] == "UNKNOWN"
    monkeypatch.setattr(activity, "_CACHE_VALUE", ({"candidate": {"live_launches_24h": 7}}, {"now": 1}))
    monkeypatch.setattr(activity, "_CACHE_DB_PATH", "db")
    monkeypatch.setattr(activity, "_CACHE_EXPIRES_AT", time.monotonic() - 1)
    assert activity.cached_activity("db")[0] == {}
    with activity._CACHE_LOCK:
        assert activity.cached_activity("db")[1]["status"] == "UNKNOWN"


def test_fresh_cache_retains_exact_results_without_mutation(monkeypatch):
    monkeypatch.setattr(activity, "_CACHE_VALUE", ({"candidate": {"live_launches_24h": 7}}, {"now": 1}))
    monkeypatch.setattr(activity, "_CACHE_DB_PATH", "db")
    monkeypatch.setattr(activity, "_CACHE_EXPIRES_AT", time.monotonic() + 30)
    first, stats = activity.cached_activity("db")
    assert stats["status"] == "HEALTHY"
    first["candidate"]["live_launches_24h"] = 99
    assert activity.cached_activity("db")[0]["candidate"]["live_launches_24h"] == 7
    assert activity.cached_activity("different-db")[1]["status"] == "UNKNOWN"


def test_coverage_unknown_does_not_open_database(monkeypatch):
    monkeypatch.setattr(activity, "_CACHE_VALUE", None)
    monkeypatch.setattr(operator_routes.sqlite3, "connect", lambda *a, **k: (_ for _ in ()).throw(AssertionError("Unqualified coverage query")))
    app = Flask(__name__)
    app.register_blueprint(operator_routes.operator_bp)
    response = app.test_client().get("/api/ops/operators/coverage-24h")
    assert response.status_code == 503
    assert response.json["status"] == "UNKNOWN"
    assert "eligible" not in response.json
