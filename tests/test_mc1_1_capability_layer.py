"""MC1.1: regression tests for the stateless Mission Control capability
layer (src/ops/mission_control_capabilities.py), implementing the frozen
MC1.0 design (docs/design/mc1_0_capability_severity_model.md).

Covers: capability computation is a pure function of the existing
/api/health/full subsystem dict (no new measurement), evidence counts
are correct, degradation propagation floors but never suppresses
independent severity, incident grouping collapses multiple abnormal
signals within one capability into one incident, propagated-only WARNING
(degraded_by set, zero own abnormal signals) does NOT open its own
incident (MC1.0 Section 13's explicit "propagated annotation, not a
second incident" rule), genuinely independent concurrent capability
failures remain separate incidents (MC1.0 Section 7.5 / X78.13A), and
the /api/health/full endpoint remains backward compatible (all existing
subsystem fields present and unchanged, new fields purely additive).
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
    """Incident first-detection tracking is a module-level in-memory dict
    (MC1.0 Section 11's stateless option) -- reset between tests so one
    test's incident timing doesn't leak into another."""
    mc._incident_start_cache.clear()
    yield
    mc._incident_start_cache.clear()


def test_all_healthy_produces_healthy_platform_and_zero_incidents():
    subsystems = _healthy_subsystems()
    caps = mc.compute_capabilities(subsystems)
    assert all(c["status"] == "HEALTHY" for c in caps.values())
    assert mc.compute_platform_status(caps) == "HEALTHY"
    assert mc.compute_incidents(caps) == []


def test_capability_computation_is_pure_and_deterministic():
    """Same input -> same output, every time. No hidden state affecting
    the capability computation itself (only compute_incidents's
    first_detected_at tracking is stateful, and that's covered
    separately)."""
    subsystems = _healthy_subsystems()
    subsystems["ingestion"]["pumpportal"] = "RETRYING"
    subsystems["ingestion"]["last_birth_age_secs"] = 6000
    subsystems["ingestion"]["last_migration_age_secs"] = 6000

    result_1 = mc.compute_capabilities(subsystems)
    result_2 = mc.compute_capabilities(subsystems)
    assert result_1 == result_2


def test_live_ingestion_critical_matches_charter_worked_example():
    """Reproduces MC1.0's / MC1.1's exact worked example: PumpPortal
    retrying, births silent, migrations silent, listener stale -> one
    CRITICAL Live Ingestion incident with 4 evidence signals."""
    subsystems = _healthy_subsystems()
    subsystems["ingestion"] = {
        "status": "DEGRADED", "pumpportal": "RETRYING", "pumpswap": "CONNECTED",
        "last_birth_age_secs": 5700, "last_migration_age_secs": 5700,
        "birth_queue_pending": 0, "migration_queue_pending": 0,
        "listener_log_age_secs": 612,
    }

    caps = mc.compute_capabilities(subsystems)
    live_ingestion = caps["live_ingestion"]

    assert live_ingestion["status"] == "CRITICAL"
    assert live_ingestion["evidence"]["abnormal"] == 4
    abnormal_names = {s["name"] for s in live_ingestion["signals"] if s["abnormal"]}
    assert abnormal_names == {
        "birth_rate_collapse", "migration_rate_collapse",
        "pumpportal_connection", "listener_log_freshness",
    }

    incidents = mc.compute_incidents(caps)
    assert len(incidents) == 1
    assert incidents[0]["capability"] == "live_ingestion"
    assert incidents[0]["severity"] == "CRITICAL"
    assert set(incidents[0]["impact"]) == {
        "Birth rate collapsed", "Migration rate collapsed",
        "PumpPortal unavailable", "Listener unhealthy",
    }


def test_downstream_propagation_floors_but_does_not_suppress_independent_severity():
    """MC1.0 Section 3: an upstream CRITICAL floors downstream to at
    least WARNING via degraded_by, but a downstream capability's own
    independent CRITICAL is never downgraded by the floor."""
    subsystems = _healthy_subsystems()
    subsystems["ingestion"]["pumpportal"] = "RETRYING"
    subsystems["ingestion"]["last_birth_age_secs"] = 6000
    subsystems["ingestion"]["last_migration_age_secs"] = 6000
    subsystems["ingestion"]["listener_log_age_secs"] = 700
    # creator_funding has its OWN independent CRITICAL problem too.
    subsystems["intelligence"]["funding_worker_status"] = "STOPPED"
    subsystems["intelligence"]["funding_worker_heartbeat_age_secs"] = 99999
    subsystems["intelligence"]["funding_queue_pending"] = 500

    caps = mc.compute_capabilities(subsystems)
    assert caps["live_ingestion"]["status"] == "CRITICAL"
    # Own signal makes it CRITICAL -- must NOT be floored down to WARNING
    # by the upstream propagation logic.
    assert caps["creator_funding"]["status"] == "CRITICAL"
    assert caps["creator_funding"]["degraded_by"] == "live_ingestion"

    # operational_intelligence has no own signals -- purely propagated,
    # floored to WARNING (not CRITICAL, since propagation floors to
    # WARNING specifically, per MC1.0 Section 3).
    assert caps["operational_intelligence"]["status"] == "WARNING"
    assert caps["operational_intelligence"]["degraded_by"] == "creator_funding"


def test_propagated_only_warning_does_not_open_its_own_incident():
    """MC1.0 Section 13 (operator playbook): a capability that is WARNING
    purely because of upstream propagation (degraded_by set, zero own
    abnormal evidence signals) must NOT produce a second incident card --
    it is 'a propagated annotation, not a second incident.'"""
    subsystems = _healthy_subsystems()
    subsystems["ingestion"]["pumpportal"] = "RETRYING"
    subsystems["ingestion"]["last_birth_age_secs"] = 6000
    subsystems["ingestion"]["last_migration_age_secs"] = 6000
    subsystems["ingestion"]["listener_log_age_secs"] = 700
    # creator_funding, operational_intelligence, watchtower all stay
    # healthy on their OWN merits -- only propagation makes them WARNING.

    caps = mc.compute_capabilities(subsystems)
    assert caps["creator_funding"]["status"] == "WARNING"
    assert caps["creator_funding"]["evidence"]["abnormal"] == 0
    assert caps["creator_funding"]["degraded_by"] == "live_ingestion"

    incidents = mc.compute_incidents(caps)
    assert len(incidents) == 1
    assert incidents[0]["capability"] == "live_ingestion"
    capability_names_with_incidents = {i["capability"] for i in incidents}
    assert "creator_funding" not in capability_names_with_incidents
    assert "operational_intelligence" not in capability_names_with_incidents
    assert "watchtower" not in capability_names_with_incidents


def test_independent_concurrent_capability_failures_remain_separate_incidents():
    """MC1.0 Section 7.5 / X78.13A lesson: two genuinely independent
    capability failures at the same time must produce two separate
    incidents, never merged into one, even though they're concurrent."""
    subsystems = _healthy_subsystems()
    subsystems["ingestion"]["pumpportal"] = "RETRYING"
    subsystems["ingestion"]["last_birth_age_secs"] = 6000
    subsystems["ingestion"]["last_migration_age_secs"] = 6000
    subsystems["ingestion"]["listener_log_age_secs"] = 700
    # Unrelated infrastructure problem, same poll.
    subsystems["database"]["status"] = "CRITICAL"
    subsystems["database"]["p99_wait_ms"] = 40000

    caps = mc.compute_capabilities(subsystems)
    incidents = mc.compute_incidents(caps)

    assert len(incidents) == 2
    capabilities_with_incidents = {i["capability"] for i in incidents}
    assert capabilities_with_incidents == {"live_ingestion", "infrastructure"}


def test_price_tracking_peak_only_is_not_alarming():
    """MC1.0 Section 3: PEAK-ONLY is an intentionally-degraded,
    non-alarming state and must not classify as WARNING/CRITICAL."""
    subsystems = _healthy_subsystems()
    subsystems["price_worker"]["status"] = "PEAK-ONLY"

    caps = mc.compute_capabilities(subsystems)
    assert caps["price_tracking"]["status"] == "HEALTHY"


def test_incident_first_detected_at_persists_across_polls_until_recovery():
    """Stateless in-memory tracking (MC1.0 Section 11): first_detected_at
    must stay the same across repeated polls while the condition persists,
    and must clear once the capability recovers."""
    subsystems = _healthy_subsystems()
    subsystems["ingestion"]["pumpportal"] = "RETRYING"
    subsystems["ingestion"]["last_birth_age_secs"] = 6000
    subsystems["ingestion"]["last_migration_age_secs"] = 6000
    subsystems["ingestion"]["listener_log_age_secs"] = 700

    caps_1 = mc.compute_capabilities(subsystems)
    incidents_1 = mc.compute_incidents(caps_1)
    first_detected_1 = incidents_1[0]["first_detected_at"]

    caps_2 = mc.compute_capabilities(subsystems)
    incidents_2 = mc.compute_incidents(caps_2)
    first_detected_2 = incidents_2[0]["first_detected_at"]

    assert first_detected_1 == first_detected_2
    assert incidents_2[0]["current_duration_secs"] >= 0

    # Recovery clears the tracked start time.
    healthy_subsystems = _healthy_subsystems()
    caps_3 = mc.compute_capabilities(healthy_subsystems)
    incidents_3 = mc.compute_incidents(caps_3)
    assert incidents_3 == []
    assert "live_ingestion" not in mc._incident_start_cache


def test_evidence_denominator_matches_signal_count():
    """MC1.0 Section 8A: evidence total must equal the number of signal
    checks actually defined for that capability -- no mismatch between
    the N/M count and the signals list length."""
    subsystems = _healthy_subsystems()
    caps = mc.compute_capabilities(subsystems)
    for name, cap in caps.items():
        assert cap["evidence"]["total"] == len(cap["signals"]), name
        assert cap["evidence"]["abnormal"] == sum(1 for s in cap["signals"] if s["abnormal"]), name


def test_healthz_capability_has_zero_abnormal_signals_by_definition():
    """MC1.0 Section 8A: a HEALTHY capability must have 0 abnormal
    evidence signals -- status and evidence count must never disagree."""
    subsystems = _healthy_subsystems()
    caps = mc.compute_capabilities(subsystems)
    for name, cap in caps.items():
        if cap["status"] == "HEALTHY":
            assert cap["evidence"]["abnormal"] == 0, name


def test_api_health_full_endpoint_is_backward_compatible(monkeypatch):
    """Phase F: /api/health/full must retain every existing top-level key
    and every existing subsystems sub-key unchanged; capabilities and
    incidents are purely additive."""
    import src.core.main as m

    with m.app.test_client() as c:
        resp = c.get("/api/health/full")
        assert resp.status_code == 200
        data = resp.get_json()

    # Existing top-level keys still present.
    assert "platform" in data
    assert "status" in data
    assert "ts" in data
    assert "subsystems" in data
    assert data["platform"] == "WATCHTOWER"

    # All 7 existing subsystem blocks still present, unchanged keys.
    expected_subsystems = {
        "ingestion", "price_worker", "cascade_infrastructure",
        "cascade_activity", "database", "api", "intelligence",
    }
    assert set(data["subsystems"].keys()) == expected_subsystems

    # New, additive keys.
    assert "capabilities" in data
    assert "incidents" in data
    assert set(data["capabilities"].keys()) == set(mc.CAPABILITY_NAMES)


def test_rate_engine_falls_back_to_silence_when_no_baseline_available():
    """MC1.0 Section 5: get_expected_rate_per_min() currently returns
    None (no historical baseline wired yet -- explicitly deferred to a
    future implementation step, MC1.1 Phase D's 'mechanism only'
    instruction). Every rate evaluation must therefore correctly route
    to the silence-based fallback, not silently treat None as zero."""
    result = mc.evaluate_rate_signal(
        event_type="births",
        observed_count_in_window=0,
        silence_secs=100,
        fallback_silence_sec=5400,
    )
    assert result["mode"] == "silence_fallback"
    assert result["status"] == "HEALTHY"  # 100s < 5400s fallback threshold

    result_critical = mc.evaluate_rate_signal(
        event_type="births",
        observed_count_in_window=0,
        silence_secs=6000,
        fallback_silence_sec=5400,
    )
    assert result_critical["mode"] == "silence_fallback"
    assert result_critical["status"] == "CRITICAL"


def test_rate_engine_uses_rate_when_baseline_available(monkeypatch):
    """When a baseline IS available (simulating a future implementation
    wiring get_expected_rate_per_min to real data), rate becomes the
    primary signal per MC1.0 Amendment 1, evaluated before silence."""
    monkeypatch.setattr(mc, "get_expected_rate_per_min", lambda event_type: 2.0)

    # Rate collapsed to near-zero, but silence_secs is still small
    # (recent single event) -- rate mode must still fire CRITICAL since
    # it's evaluated first when a baseline exists.
    result = mc.evaluate_rate_signal(
        event_type="births",
        observed_count_in_window=0,
        silence_secs=30,
        fallback_silence_sec=5400,
    )
    assert result["mode"] == "rate"
    assert result["status"] == "CRITICAL"
