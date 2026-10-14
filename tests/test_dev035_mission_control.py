"""Provider-free DEV035 Mission Control regression coverage."""
from pathlib import Path
import sqlite3

import src.ops.mission_control_capabilities as mc
from src.ops import migration_walkback_handoff_health as handoff


ROOT = Path(__file__).resolve().parents[1]


def _funding(**values):
    return mc._compute_creator_funding({"intelligence": values})


def test_refresh_coordinator_has_bounded_nonoverlap_hidden_abort_and_stale_contract():
    source = (ROOT / "templates/system_health_dashboard.html").read_text()
    assert "const SYSTEM_HEALTH_REFRESH_MS = 15000" in source
    assert "systemHealthRefreshInFlight" in source
    assert "document.hidden || systemHealthRefreshInFlight" in source
    assert "AbortController" in source and "systemHealthAbortController.abort()" in source
    assert "visibilitychange" in source and "polling paused while this page is hidden" in source
    assert "Promise.allSettled" in source
    assert "generation === systemHealthRefreshGeneration" in source
    assert "Last successful refresh:" in source


def test_banner_and_platform_use_the_same_full_health_scope():
    source = (ROOT / "templates/system_health_dashboard.html").read_text()
    assert "function mcPlatformIncidents" not in source
    assert "mcUpdateBanner(fullHealth);" in source
    assert "const scopeStatus = (fullHealth && fullHealth.status) || 'UNKNOWN';" in source
    assert "const top = h?.status || 'UNKNOWN';" in source
    assert "System Health Unknown" in source


def test_failed_core_refresh_marks_existing_health_evidence_stale_not_healthy():
    source = (ROOT / "templates/system_health_dashboard.html").read_text()
    assert "let lastSuccessfulHealthAt = null;" in source
    assert "System Health Stale" in source
    assert "Operational evidence stale" in source
    assert "core health request failed" in source
    assert "results[0].status === 'fulfilled'" in source
    assert "Last successful refresh: ${lastSuccessfulHealthAt.toLocaleTimeString()}" in source


def test_watchtower_subscription_telemetry_missing_is_unknown_not_zero():
    result = mc._compute_watchtower({"cascade_infrastructure": {
        "status": "UNKNOWN", "error": "subscription telemetry unavailable",
    }})
    signal = next(s for s in result["signals"] if s["name"] == "subscriptions_active")
    assert result["status"] == "UNKNOWN"
    assert signal["abnormal"] is False
    assert "unknown:" in signal["detail"]


def test_watchtower_qualified_zero_subscriptions_remains_warning():
    result = mc._compute_watchtower({"cascade_infrastructure": {
        "status": "CONNECTED", "subs_total": 0, "heartbeat_age_secs": 10,
    }})
    signal = next(s for s in result["signals"] if s["name"] == "subscriptions_active")
    assert result["status"] == "WARNING"
    assert signal["abnormal"] is True
    assert signal["detail"] == "subs_total=0"


def test_watchtower_fresh_positive_subscription_evidence_is_healthy():
    result = mc._compute_watchtower({"cascade_infrastructure": {
        "status": "CONNECTED", "subs_total": 3, "heartbeat_age_secs": 10,
    }})
    assert result["status"] == "HEALTHY"


def test_historical_ingestion_disconnect_does_not_outlive_fresh_recovery(monkeypatch):
    monkeypatch.setattr(mc, "get_expected_rate_per_min", lambda _: None)
    monkeypatch.setattr(mc, "count_recent_events", lambda *_: 1)
    result = mc._compute_live_ingestion({"ingestion": {
        "pumpportal": "STALE", "pumpswap": "STALE", "last_birth_age_secs": 10,
        "last_migration_age_secs": 10, "listener_log_age_secs": 10,
    }})
    assert result["status"] == "HEALTHY"
    assert not next(s for s in result["signals"] if s["name"] == "pumpportal_connection")["abnormal"]


def test_missing_ingestion_freshness_is_unknown(monkeypatch):
    monkeypatch.setattr(mc, "get_expected_rate_per_min", lambda _: None)
    monkeypatch.setattr(mc, "count_recent_events", lambda *_: 0)
    result = mc._compute_live_ingestion({"ingestion": {"pumpportal": "UNKNOWN", "pumpswap": "UNKNOWN"}})
    assert result["status"] == "UNKNOWN"


def test_funding_backlog_with_progress_recovers_and_without_progress_warns():
    progressing = _funding(funding_worker_status="RUNNING", funding_hot_pending=4,
                           funding_oldest_hot_age_secs=7200, funding_last_completed_age_secs=20)
    stalled = _funding(funding_worker_status="RUNNING", funding_hot_pending=4,
                       funding_oldest_hot_age_secs=7200, funding_last_completed_age_secs=900)
    assert progressing["status"] == "HEALTHY"
    assert stalled["status"] == "WARNING"


def test_funding_running_without_committed_progress_telemetry_is_unknown():
    assert _funding(funding_worker_status="RUNNING", funding_hot_pending=2)["status"] == "UNKNOWN"


def _handoff_dbs(rows, queued=()):
    live, ops = sqlite3.connect(":memory:"), sqlite3.connect(":memory:")
    live.execute("CREATE TABLE token_analysis(mint TEXT, migrated_at INTEGER, migration_tx TEXT, earliest_tx_creator TEXT)")
    ops.execute("CREATE TABLE wt_walkback_queue(mint TEXT, enqueued_at INTEGER, status TEXT)")
    live.executemany("INSERT INTO token_analysis VALUES(?,?,?,?)", rows)
    ops.executemany("INSERT INTO wt_walkback_queue VALUES(?,?,?)", queued)
    return live, ops


def test_historical_walkback_mismatch_is_not_current_failure(monkeypatch):
    monkeypatch.setattr(handoff, "classify_creator", lambda *_: ("FULL_WALKBACK",))
    result = handoff.build_migration_walkback_handoff_health(*_handoff_dbs([("old", 1, "tx", "creator")]), now=10000)
    assert result["state"] == "UNKNOWN"


def test_current_walkback_requires_time_aligned_identity_and_missing_identity_is_unknown(monkeypatch):
    monkeypatch.setattr(handoff, "classify_creator", lambda *_: ("FULL_WALKBACK",))
    bad = handoff.build_migration_walkback_handoff_health(*_handoff_dbs([("now", 9900, None, "creator")]), now=10000)
    failed = handoff.build_migration_walkback_handoff_health(*_handoff_dbs([("now", 9900, "tx", "creator")], [("now", 9800, "pending")]), now=10000)
    assert bad["state"] == "UNKNOWN"
    assert failed["state"] == "FAILED"


def test_incident_resolution_removes_active_alert_without_duplicate():
    mc._incident_start_cache.clear()
    unhealthy = {"live_ingestion": {"status": "CRITICAL", "degraded_by": None, "signals": [{"name": "x", "abnormal": True}]}}
    healthy = {"live_ingestion": {"status": "HEALTHY", "degraded_by": None, "signals": []}}
    first = mc.compute_incidents(unhealthy)
    assert len(first) == 1
    assert mc.compute_incidents(unhealthy)[0]["id"] == first[0]["id"]
    assert mc.compute_incidents(healthy) == []
