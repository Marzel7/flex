"""MC1.2A -- Live Ingestion Operational Calibration.

docs/design/mc1_2a_live_ingestion_operational_calibration.md

MC1.2 evaluated migrations with the same rate-ratio logic as births.
Production observation found migrations are naturally bursty -- a normal
dormant period between migration bursts produced the same CRITICAL
classification as a genuine outage. This calibration milestone replaces
migration evaluation with a configurable elapsed-time-since-last-migration
band policy, while leaving birth evaluation completely untouched (Phase
A's explicit "no change to birth logic").

These tests verify: the new policy's band boundaries, that it is
correctly wired into _compute_live_ingestion (Phase E: migration is
supporting context, only independently escalates past its calibrated
thresholds), that a normal dormant migration lull cannot produce a
CRITICAL incident, and that birth-only CRITICAL classification is
unchanged from MC1.2's behavior (acceptance gate: "Birth logic
unchanged").
"""
from __future__ import annotations

import pytest

import src.ops.mission_control_capabilities as mc


def _healthy_subsystems():
    return {
        "ingestion": {
            "status": "HEALTHY", "pumpportal": "CONNECTED", "pumpswap": "CONNECTED",
            "last_birth_age_secs": 5, "last_migration_age_secs": 100,
            "birth_queue_pending": 0, "migration_queue_pending": 0,
            "listener_log_age_secs": 5,
        },
        "price_worker": {
            "status": "HEALTHY", "last_peak_update_age_secs": 10,
            "last_snapshot_write_age_secs": 10, "snapshot_expected": True,
        },
        "cascade_infrastructure": {"status": "CONNECTED", "heartbeat_age_secs": 5, "subs_total": 12},
        "cascade_activity": {"status": "ACTIVE"},
        "database": {"status": "HEALTHY", "p99_wait_ms": 50, "serializer_queue_depth": 1},
        "api": {"status": "HEALTHY", "gunicorn_alive": True, "errors_5m": 0},
        "intelligence": {
            "funding_worker_status": "RUNNING", "funding_worker_heartbeat_age_secs": 5,
            "funding_queue_oldest_pending_age_secs": 100, "funding_queue_pending": 5,
            "watch_pipeline_age_secs": 10, "watch_pipeline_interval_secs": 300,
            "crq_worker_age_secs": 10, "creator_queue_failed": 0, "missing_creators_1h": 0,
        },
    }


@pytest.fixture(autouse=True)
def _reset_incident_cache():
    mc._incident_start_cache.clear()
    yield
    mc._incident_start_cache.clear()


@pytest.fixture(autouse=True)
def _reset_trend_buffer():
    mc.reset_trend_buffers()
    yield
    mc.reset_trend_buffers()


@pytest.fixture(autouse=True)
def _isolate_from_live_baseline(monkeypatch):
    """Births still use the real baseline engine -- isolate from the live
    DB by default exactly like the MC1.1/MC1.2 suite does. Migrations no
    longer call these functions at all (elapsed-time policy instead), so
    this fixture no longer needs a migration branch."""
    monkeypatch.setattr(mc, "get_expected_rate_per_min", lambda event_type: None)
    monkeypatch.setattr(mc, "count_recent_events", lambda event_type, window_min=mc.RATE_WINDOW_MIN: 0)
    yield


# ── Phase B: elapsed-time band policy, in isolation ─────────────────────────

def test_dormant_band_is_healthy():
    for secs in (0, 60, 10 * 60, mc.MIGRATION_DORMANT_MAX_MIN * 60):
        result = mc.evaluate_migration_elapsed_policy(secs)
        assert result["status"] == "HEALTHY", secs
        assert result["band"] == "dormant", secs


def test_warning_band():
    midpoint_min = (mc.MIGRATION_DORMANT_MAX_MIN + mc.MIGRATION_WARNING_MAX_MIN) / 2
    result = mc.evaluate_migration_elapsed_policy(int(midpoint_min * 60))
    assert result["status"] == "WARNING"
    assert result["band"] == "warning"


def test_concern_band_is_warning_ranked_but_distinct_label():
    midpoint_min = (mc.MIGRATION_WARNING_MAX_MIN + mc.MIGRATION_CONCERN_MAX_MIN) / 2
    result = mc.evaluate_migration_elapsed_policy(int(midpoint_min * 60))
    # MC1.0's severity vocabulary has no 5th level between WARNING and
    # CRITICAL -- "Concern" is a presentation label the dashboard applies
    # to this band, backend status stays WARNING-ranked.
    assert result["status"] == "WARNING"
    assert result["band"] == "concern"


def test_beyond_concern_is_critical():
    past_critical_min = mc.MIGRATION_CONCERN_MAX_MIN + 1
    result = mc.evaluate_migration_elapsed_policy(int(past_critical_min * 60))
    assert result["status"] == "CRITICAL"
    assert result["band"] == "critical"


def test_no_migration_ever_recorded_is_unknown_not_critical():
    result = mc.evaluate_migration_elapsed_policy(None)
    assert result["status"] == "UNKNOWN"
    assert result["band"] == "unknown"


def test_band_thresholds_are_configurable_via_env(monkeypatch):
    """Charter: 'These values must remain configurable. Do not hard-code
    production policy.' Verify overriding the env-derived module
    constants actually changes classification."""
    monkeypatch.setattr(mc, "MIGRATION_DORMANT_MAX_MIN", 5.0)
    monkeypatch.setattr(mc, "MIGRATION_WARNING_MAX_MIN", 8.0)
    monkeypatch.setattr(mc, "MIGRATION_CONCERN_MAX_MIN", 10.0)

    assert mc.evaluate_migration_elapsed_policy(6 * 60)["band"] == "warning"
    assert mc.evaluate_migration_elapsed_policy(4 * 60)["band"] == "dormant"
    assert mc.evaluate_migration_elapsed_policy(11 * 60)["band"] == "critical"


# ── Phase E: wired into Live Ingestion -- migration is supporting context ──

def test_normal_migration_dormancy_does_not_escalate_live_ingestion():
    """Core acceptance gate: 'No false CRITICAL migration incidents' / 'A
    normal migration lull must never create a CRITICAL incident.' A
    20-minute-old migration (within the dormant band) with everything
    else healthy must leave Live Ingestion HEALTHY, matching the
    charter's own example (0.13/min observed vs 0.60/min expected, 12min
    since last migration -> 🟢 Dormant)."""
    subsystems = _healthy_subsystems()
    subsystems["ingestion"]["last_migration_age_secs"] = 12 * 60  # 12 minutes, charter's own example

    result = mc._compute_live_ingestion(subsystems)
    assert result["status"] == "HEALTHY"
    migration_signal = next(s for s in result["signals"] if s["name"] == "migration_health")
    assert migration_signal["abnormal"] is False
    assert result["flow_metrics"]["migrations"]["band"] == "dormant"


def test_migration_dormancy_up_to_full_dormant_band_is_never_critical():
    """Sweep the entire dormant band -- none of it should ever reach
    CRITICAL, unlike MC1.2's rate-ratio logic which could."""
    subsystems = _healthy_subsystems()
    for mins in (0, 5, 10, 15, mc.MIGRATION_DORMANT_MAX_MIN):
        subsystems["ingestion"]["last_migration_age_secs"] = int(mins * 60)
        result = mc._compute_live_ingestion(subsystems)
        assert result["status"] != "CRITICAL", mins


def test_genuine_migration_outage_still_escalates_to_critical():
    """Migration health can still independently raise Live Ingestion to
    CRITICAL -- MC1.2A calibrates the threshold, it does not remove
    migration's ability to signal a real problem (Phase E: 'unless it
    independently exceeds its calibrated thresholds')."""
    subsystems = _healthy_subsystems()
    subsystems["ingestion"]["last_migration_age_secs"] = int((mc.MIGRATION_CONCERN_MAX_MIN + 5) * 60)

    result = mc._compute_live_ingestion(subsystems)
    assert result["status"] == "CRITICAL"
    migration_signal = next(s for s in result["signals"] if s["name"] == "migration_health")
    assert migration_signal["abnormal"] is True


def test_migration_incident_opens_only_when_genuinely_critical():
    """End-to-end through the incident engine: a dormant migration lull
    produces zero incidents; a genuine migration outage produces one."""
    subsystems = _healthy_subsystems()
    subsystems["ingestion"]["last_migration_age_secs"] = 12 * 60
    caps = mc.compute_capabilities(subsystems)
    assert mc.compute_incidents(caps) == []

    mc._incident_start_cache.clear()
    subsystems["ingestion"]["last_migration_age_secs"] = int((mc.MIGRATION_CONCERN_MAX_MIN + 10) * 60)
    caps = mc.compute_capabilities(subsystems)
    incidents = mc.compute_incidents(caps)
    assert len(incidents) == 1
    assert incidents[0]["capability"] == "live_ingestion"
    assert "Migration activity below expected profile" in incidents[0]["impact"]


# ── Acceptance gate: birth logic unchanged ──────────────────────────────────

def test_birth_critical_classification_is_unchanged_by_migration_calibration(monkeypatch):
    """Phase A: 'No change to birth logic.' Reproduces MC1.2's own
    Case 1 worked example (connected socket, birth rate 0 vs 18/min
    expected -> CRITICAL) with migration held safely dormant, proving
    birth-rate evaluation still uses evaluate_rate_signal exactly as
    before -- only migration's evaluation function changed."""
    monkeypatch.setattr(mc, "get_expected_rate_per_min", lambda event_type: 18.0)
    monkeypatch.setattr(mc, "count_recent_events", lambda event_type, window_min=mc.RATE_WINDOW_MIN: 0)

    subsystems = _healthy_subsystems()
    subsystems["ingestion"]["pumpportal"] = "CONNECTED"
    subsystems["ingestion"]["last_birth_age_secs"] = 60
    subsystems["ingestion"]["last_migration_age_secs"] = 60  # dormant

    result = mc._compute_live_ingestion(subsystems)
    assert result["status"] == "CRITICAL"
    birth_signal = next(s for s in result["signals"] if s["name"] == "birth_rate_collapse")
    assert birth_signal["abnormal"] is True
    assert result["flow_metrics"]["births"]["observed_per_min"] == 0.0
    assert result["flow_metrics"]["births"]["expected_per_min"] == 18.0
    assert result["flow_metrics"]["births"]["mode"] == "rate"  # birth mode/shape unchanged


def test_flow_metrics_migrations_shape_is_elapsed_policy():
    """Phase D: the dashboard needs mode='elapsed_policy' plus band/
    elapsed_min to render migration-specific operational messaging
    instead of a rate-ratio comparison."""
    subsystems = _healthy_subsystems()
    subsystems["ingestion"]["last_migration_age_secs"] = 12 * 60
    result = mc._compute_live_ingestion(subsystems)
    migrations = result["flow_metrics"]["migrations"]
    assert migrations["mode"] == "elapsed_policy"
    assert migrations["band"] == "dormant"
    assert migrations["elapsed_min"] == 12.0
    assert migrations["status"] == "HEALTHY"
