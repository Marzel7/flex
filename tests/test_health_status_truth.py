from src.ops.health_status_truth import (
    DEFAULT_REQUIRED_WORKERS,
    classify_database_pressure,
    classify_worker_heartbeats,
)


def test_retired_legacy_heartbeats_do_not_poison_health():
    rows = {
        "creator-funding": {"stale": False},
        "creator-resolution": {"stale": False},
        "walkback_worker": {"stale": False},
        "ws_cascade": {"stale": False},
        "flask-app": {"stale": True},
        "watch-pipeline": {"stale": True},
    }
    result = classify_worker_heartbeats(rows, DEFAULT_REQUIRED_WORKERS)
    assert result["stale_workers"] == []
    assert result["missing_workers"] == []
    assert result["inactive_workers"] == ["flask-app", "watch-pipeline"]


def test_active_stale_or_missing_worker_still_fails_closed():
    rows = {
        "creator-funding": {"stale": True},
        "creator-resolution": {"stale": False},
        "walkback_worker": {"stale": False},
    }
    result = classify_worker_heartbeats(rows, DEFAULT_REQUIRED_WORKERS)
    assert result["stale_workers"] == ["creator-funding"]
    assert result["missing_workers"] == ["ws_cascade"]


def test_database_pressure_uses_current_latency_and_queue_only():
    assert classify_database_pressure(3.2, 0) == "HEALTHY"
    assert classify_database_pressure(1500, 0) == "PRESSURE"
    assert classify_database_pressure(6000, 0) == "AT_RISK"
    assert classify_database_pressure(31000, 0) == "CRITICAL"
