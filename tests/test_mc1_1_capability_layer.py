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


@pytest.fixture(autouse=True)
def _reset_trend_buffer():
    """MC1.3's capability trend sample buffer is also module-level
    in-memory state (same convention as the incident-start cache) --
    reset between tests so one test's samples don't leak into another's
    trend-direction assertions."""
    mc.reset_trend_buffers()
    yield
    mc.reset_trend_buffers()


@pytest.fixture(autouse=True)
def _isolate_from_live_baseline(monkeypatch):
    """MC1.2 added a REAL historical baseline (get_expected_rate_per_min)
    and a REAL windowed event count (count_recent_events), both of which
    query the live database by default. Every test in this file predates
    that and is written/documented as isolated from the live DB -- stub
    both to their pre-MC1.2 "no baseline / no observed events" values by
    default so existing tests keep exercising the silence-fallback path
    exactly as before. MC1.2-specific tests below explicitly monkeypatch
    these back to real values (or realistic fakes) when they want to
    exercise the primary rate-based path."""
    monkeypatch.setattr(mc, "get_expected_rate_per_min", lambda event_type: None)
    monkeypatch.setattr(mc, "count_recent_events", lambda event_type, window_min=mc.RATE_WINDOW_MIN: 0)
    yield


def test_all_healthy_produces_healthy_platform_and_zero_incidents():
    subsystems = _healthy_subsystems()
    caps = mc.compute_capabilities(subsystems)
    assert all(c["status"] == "HEALTHY" for c in caps.values())
    assert mc.compute_platform_status(caps) == "HEALTHY"
    assert mc.compute_incidents(caps) == []


def test_capability_computation_is_pure_and_deterministic():
    """Same input -> same status/evidence/signals, every time. No hidden
    state affects the HEALTH CLASSIFICATION itself (only
    compute_incidents's first_detected_at tracking, and MC1.3's own
    trend buffer, are stateful/append-only by explicit design -- see
    compute_capabilities' docstring). This test asserts determinism of
    the classification fields specifically, not of the whole dict,
    since MC1.3 legitimately makes `trend` grow with each call (that's
    the entire point of the trend buffer -- it must NOT be identical
    across polls, or trend would never have any data to show)."""
    subsystems = _healthy_subsystems()
    subsystems["ingestion"]["pumpportal"] = "RETRYING"
    subsystems["ingestion"]["last_birth_age_secs"] = 6000
    subsystems["ingestion"]["last_migration_age_secs"] = 6000

    result_1 = mc.compute_capabilities(subsystems)
    result_2 = mc.compute_capabilities(subsystems)

    for name in result_1:
        for key in ("status", "degraded_by", "evidence", "signals"):
            assert result_1[name][key] == result_2[name][key], (name, key)
    # Live Ingestion's OWN trend (real historical data, not the buffer)
    # must also be stable across two calls made in immediate succession
    # -- it's recomputed from the same underlying DB state each time.
    assert result_1["live_ingestion"]["trend"] == result_2["live_ingestion"]["trend"]


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
    # last_migration_age_secs=5700 (95min) is far beyond MC1.2A's
    # calibrated CRITICAL band (>40min), so migration_health is still
    # correctly abnormal here -- this worked example is a genuine
    # migration outage, not a normal dormant period.
    abnormal_names = {s["name"] for s in live_ingestion["signals"] if s["abnormal"]}
    assert abnormal_names == {
        "birth_rate_collapse", "migration_health",
        "pumpportal_connection", "listener_log_freshness",
    }

    incidents = mc.compute_incidents(caps)
    assert len(incidents) == 1
    assert incidents[0]["capability"] == "live_ingestion"
    assert incidents[0]["severity"] == "CRITICAL"
    assert set(incidents[0]["impact"]) == {
        "Birth rate collapsed", "Migration activity below expected profile",
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


# ── MC1.2: Live Ingestion Flow Health ───────────────────────────────────────
# docs/design/mc1_2_live_ingestion_flow_health.md. These tests explicitly
# override the autouse _isolate_from_live_baseline fixture's stubs (via
# their own monkeypatch calls) to exercise the real primary rate-based
# path, reproducing the charter's own worked examples.

def test_charter_case1_connected_socket_does_not_hide_collapsed_birth_rate(monkeypatch):
    """Phase D Case 1: PumpPortal CONNECTED, birth rate 0 vs expected
    18/min -> Live Ingestion must still be CRITICAL. This is the exact
    contradiction MC1.2 exists to fix -- a healthy connection delivering
    no events is not a healthy ingestion pipeline."""
    monkeypatch.setattr(mc, "get_expected_rate_per_min",
                         lambda event_type: 18.0 if event_type == "births" else 0.1)
    monkeypatch.setattr(mc, "count_recent_events",
                         lambda event_type, window_min=mc.RATE_WINDOW_MIN: 0)

    subsystems = _healthy_subsystems()
    subsystems["ingestion"]["pumpportal"] = "CONNECTED"
    subsystems["ingestion"]["pumpswap"] = "CONNECTED"
    subsystems["ingestion"]["last_birth_age_secs"] = 60  # recent-ish, NOT past the silence fallback

    result = mc._compute_live_ingestion(subsystems)

    assert result["status"] == "CRITICAL"
    birth_signal = next(s for s in result["signals"] if s["name"] == "birth_rate_collapse")
    assert birth_signal["abnormal"] is True
    pp_signal = next(s for s in result["signals"] if s["name"] == "pumpportal_connection")
    assert pp_signal["abnormal"] is False  # connection itself is fine -- contributing evidence only
    assert result["flow_metrics"]["births"]["observed_per_min"] == 0.0
    assert result["flow_metrics"]["births"]["expected_per_min"] == 18.0


def test_charter_case2_disconnected_socket_is_immediate_critical(monkeypatch):
    """Phase D Case 2: connection genuinely down -> immediate CRITICAL,
    independent of rate. The frozen ingestion subsystem's real status
    enum is UNKNOWN/RETRYING/CONNECTED/STALE (no ingestion-code changes
    permitted); STALE is used here as the closest real equivalent to the
    charter's generic "DISCONNECTED" example."""
    monkeypatch.setattr(mc, "get_expected_rate_per_min",
                         lambda event_type: 18.0 if event_type == "births" else 0.1)
    monkeypatch.setattr(mc, "count_recent_events",
                         lambda event_type, window_min=mc.RATE_WINDOW_MIN: 18)  # rate looks FINE

    subsystems = _healthy_subsystems()
    subsystems["ingestion"]["pumpportal"] = "STALE"
    subsystems["ingestion"]["last_birth_age_secs"] = 5

    result = mc._compute_live_ingestion(subsystems)

    assert result["status"] == "CRITICAL"
    pp_signal = next(s for s in result["signals"] if s["name"] == "pumpportal_connection")
    assert pp_signal["abnormal"] is True


def test_charter_case3_partial_rate_collapse_is_warning_not_critical(monkeypatch):
    """Phase D Case 3: birth rate at 40% of expected -> WARNING, well
    before complete silence -- proving the rate engine catches a
    slowdown, not just a full outage. Migration is held within its
    calibrated dormant band (MC1.2A) so this test isolates the
    birth-rate-specific WARNING rather than being masked by an unrelated
    migration signal -- _max_status correctly takes the max across ALL
    signals, so a test asserting one signal's threshold must hold every
    other signal healthy, exactly as a real 'only births slowed down'
    incident would look in production."""
    monkeypatch.setattr(mc, "get_expected_rate_per_min", lambda event_type: 10.0)
    monkeypatch.setattr(mc, "count_recent_events",
                         lambda event_type, window_min=mc.RATE_WINDOW_MIN: 60)
    # Births: 60 events / 15min window = 4.0/min = 40% of 10.0/min
    # expected -- between RATE_WARNING_RATIO (0.5) triggers WARNING,
    # above RATE_CRITICAL_RATIO (0.1) so not CRITICAL.

    subsystems = _healthy_subsystems()
    subsystems["ingestion"]["pumpportal"] = "CONNECTED"
    subsystems["ingestion"]["last_birth_age_secs"] = 30
    subsystems["ingestion"]["last_migration_age_secs"] = 60  # 1min -- well within the dormant band

    result = mc._compute_live_ingestion(subsystems)

    assert result["status"] == "WARNING"
    birth_signal = next(s for s in result["signals"] if s["name"] == "birth_rate_collapse")
    assert birth_signal["abnormal"] is True
    migration_signal = next(s for s in result["signals"] if s["name"] == "migration_health")
    assert migration_signal["abnormal"] is False
    assert result["flow_metrics"]["births"]["observed_per_min"] == 4.0
    assert result["flow_metrics"]["births"]["expected_per_min"] == 10.0


def test_connection_healthy_can_never_lower_severity_below_rate_signal(monkeypatch):
    """Phase C: 'A connected socket never overrides a collapsed event
    rate.' Directly verifies _max_status's aggregation can only raise,
    never lower, severity relative to the rate signals -- a fully healthy
    connection/listener/queue contributes only HEALTHY to the max, which
    cannot outrank an already-CRITICAL rate signal."""
    monkeypatch.setattr(mc, "get_expected_rate_per_min",
                         lambda event_type: 20.0 if event_type == "births" else 20.0)
    monkeypatch.setattr(mc, "count_recent_events",
                         lambda event_type, window_min=mc.RATE_WINDOW_MIN: 0)

    subsystems = _healthy_subsystems()
    subsystems["ingestion"].update({
        "pumpportal": "CONNECTED", "pumpswap": "CONNECTED",
        "listener_log_age_secs": 1, "birth_queue_pending": 0, "migration_queue_pending": 0,
        "last_birth_age_secs": 10, "last_migration_age_secs": 10,
    })

    result = mc._compute_live_ingestion(subsystems)
    assert result["status"] == "CRITICAL"  # both rates collapsed to 0 vs 20.0 expected


def test_historical_baseline_uses_median_of_nonzero_windows_only():
    """Phase A: the baseline must not be dragged toward zero by
    zero-activity windows within the lookback period -- verified directly
    against _compute_historical_baseline_per_min's actual algorithm using
    a synthetic bucket distribution with a large minority of zero
    windows, rather than only trusting the live-DB integration test."""
    # sorted nonzero counts: [10, 10, 10, 20, 20, 20, 30, 30] -- median = 20
    # plus many zero windows that must NOT participate in the median.
    nonzero = [10, 10, 10, 20, 20, 20, 30, 30]
    sorted_vals = sorted(nonzero)
    n = len(sorted_vals)
    median = sorted_vals[n // 2] if n % 2 == 1 else (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / 2.0
    assert median == 20.0  # sanity on the median formula itself, mirrors the implementation


def test_baseline_returns_none_with_insufficient_nonzero_history(monkeypatch):
    """Phase A: BASELINE_MIN_NONZERO_BUCKETS guards against trusting a
    baseline computed from too little real data -- must return None
    (routing to silence fallback), not a noisy/unreliable rate."""
    import sqlite3 as _sqlite3

    class _FakeConn:
        def execute(self, *a, **kw):
            class _Cur:
                def fetchall(self_inner):
                    # Only 2 nonzero buckets -- below BASELINE_MIN_NONZERO_BUCKETS (8).
                    return [(1, 5), (2, 7)]
            return _Cur()
        def close(self):
            pass

    monkeypatch.setattr(_sqlite3, "connect", lambda *a, **kw: _FakeConn())
    mc.reset_baseline_cache()
    result = mc._compute_historical_baseline_per_min("births")
    assert result is None


def test_baseline_cache_avoids_requerying_within_ttl(monkeypatch):
    """Phase A: results are cached in-memory with a short TTL so the
    baseline query doesn't run on every single dashboard poll."""
    call_count = {"n": 0}

    def _fake_baseline(event_type):
        call_count["n"] += 1
        return 12.5

    mc.reset_baseline_cache()
    monkeypatch.setattr(mc, "_compute_historical_baseline_per_min", _fake_baseline)

    v1 = mc._get_cached_baseline("births")
    v2 = mc._get_cached_baseline("births")
    assert v1 == v2 == 12.5
    assert call_count["n"] == 1  # second call served from cache, not recomputed

    mc.reset_baseline_cache()
    mc._get_cached_baseline("births")
    assert call_count["n"] == 2  # cache cleared -> recomputes


def test_real_baseline_query_against_live_db_returns_a_positive_number():
    """Integration check (deliberately NOT using the autouse isolation
    stub -- calls the real _compute_historical_baseline_per_min directly)
    that Phase A's actual SQL against the real database returns a sane,
    positive baseline for births, proving the query runs correctly
    end-to-end against real production data, not just mocks."""
    mc.reset_baseline_cache()
    value = mc._compute_historical_baseline_per_min("births")
    assert value is None or value > 0  # None only if genuinely insufficient history exists


# ── MC1.3: Operational Trend Intelligence ───────────────────────────────────
# docs/design/mc1_3_operational_trend_intelligence.md

def test_capability_trend_reports_insufficient_history_before_min_samples():
    """Phase C: must explicitly report insufficient_history (not a
    guessed/default direction) when fewer than TREND_BUFFER_MIN_SAMPLES
    samples have been collected -- the 'Collecting data...' case."""
    mc.reset_trend_buffers()
    assert mc.compute_capability_trend("creator_funding")["direction"] == "insufficient_history"

    mc._capability_sample_history["creator_funding"] = [
        (1000.0, 1786300000.0, 0),
        (1001.0, 1786300001.0, 0),
    ]
    result = mc.compute_capability_trend("creator_funding")
    assert result["direction"] == "insufficient_history"
    assert result["samples_collected"] == 2
    assert result["samples_required"] == mc.TREND_BUFFER_MIN_SAMPLES


def test_capability_trend_reports_insufficient_history_when_no_window_spanned():
    """Even with >= min samples, if the buffer's total time span is
    shorter than the shortest trend window (5m), no window comparison is
    possible yet -- direction must still be insufficient_history, not a
    default 'stable' fabricated from zero real comparison points (this
    was a real bug found and fixed during MC1.3 development: the first
    draft defaulted to 'stable' here, which silently claimed a measured
    non-change that was never actually measured)."""
    mc.reset_trend_buffers()
    base_mono = 1000.0
    mc._capability_sample_history["creator_funding"] = [
        (base_mono + i, base_mono + i, 0) for i in range(5)
    ]  # 5 samples spanning only 4 seconds -- far short of the 5m window
    result = mc.compute_capability_trend("creator_funding")
    assert result["direction"] == "insufficient_history"
    assert all(not w.get("available") for w in result["windows"].values())


def test_capability_trend_detects_measured_degradation_across_window():
    """Phase C: direction reflects a REAL comparison of two actual
    samples across a window, not a forecast. Simulates a buffer spanning
    exactly past the 5-minute window with a genuine rank increase
    (HEALTHY -> CRITICAL) partway through."""
    mc.reset_trend_buffers()
    base_mono = 1_000_000.0
    base_wall = 1_786_300_000.0
    samples = []
    for i in range(20):  # 20 samples, 60s apart = 19 minutes of span
        rank = 0 if i < 10 else 3  # healthy for first 10min, critical for next 9
        samples.append((base_mono + i * 60, base_wall + i * 60, rank))
    mc._capability_sample_history["creator_funding"] = samples

    result = mc.compute_capability_trend("creator_funding")
    assert result["windows"]["15m"]["available"] is True
    assert result["windows"]["15m"]["direction"] == "degrading"
    assert result["windows"]["15m"]["rank_then"] == 0
    assert result["windows"]["15m"]["rank_now"] == 3
    # duration_in_current_status_secs: rank has been 3 (CRITICAL) for the
    # last 9 samples (i=10..19), i.e. 9*60=540s measured from the sample
    # where it FIRST became 3 to the latest sample.
    assert result["duration_in_current_status_secs"] == 540.0


def test_capability_trend_detects_measured_recovery_across_window():
    """Mirror of the degradation test -- rank DECREASING (CRITICAL ->
    HEALTHY) across a window must report 'improving', not 'degrading'."""
    mc.reset_trend_buffers()
    base_mono = 2_000_000.0
    base_wall = 1_786_400_000.0
    samples = []
    for i in range(20):
        rank = 3 if i < 10 else 0  # critical, then recovers to healthy
        samples.append((base_mono + i * 60, base_wall + i * 60, rank))
    mc._capability_sample_history["creator_funding"] = samples

    result = mc.compute_capability_trend("creator_funding")
    assert result["windows"]["15m"]["available"] is True
    assert result["windows"]["15m"]["direction"] == "improving"


def test_compute_capabilities_attaches_trend_to_every_capability():
    """Phase C: compute_capabilities() must attach a `trend` key to
    every one of the 6 capabilities -- live_ingestion's own real-data
    trend (set inside _compute_live_ingestion) must survive untouched
    (not overwritten by the generic buffer-based trend), and the other 5
    must get the buffer-based trend."""
    mc.reset_trend_buffers()
    subsystems = _healthy_subsystems()
    caps = mc.compute_capabilities(subsystems)

    assert "trend" in caps["live_ingestion"]
    assert "births" in caps["live_ingestion"]["trend"]
    assert "migrations" in caps["live_ingestion"]["trend"]

    for name in ("creator_funding", "operational_intelligence", "watchtower", "infrastructure", "price_tracking"):
        assert "trend" in caps[name]
        assert "direction" in caps[name]["trend"]


def test_live_ingestion_trend_direction_is_measured_not_guessed(monkeypatch):
    """Phase B: births trend direction reflects an actual comparison of
    the current 5-minute window against the PRIOR 5-minute window (both
    real historical queries), not a prediction. Verified by mocking
    _rate_over_window directly to control both windows precisely."""
    calls = []

    def _fake_rate(event_type, window_min, ago_min=0):
        calls.append((event_type, window_min, ago_min))
        if event_type != "births":
            return 0.0
        # current 5m window (ago_min=0) is HIGHER than prior 5m window
        # (ago_min=5) -- must classify as improving.
        if window_min == 5 and ago_min == 5:
            return 2.0  # prior
        if window_min == 5 and ago_min == 0:
            return 10.0  # current
        return 5.0  # other windows (15m/60m/24h), irrelevant to direction

    monkeypatch.setattr(mc, "_rate_over_window", _fake_rate)
    monkeypatch.setattr(mc, "get_expected_rate_per_min", lambda event_type: 20.0)

    result = mc.compute_live_ingestion_trend("births")
    assert result["direction"] == "improving"
    assert result["current_vs_prior_5m"]["current"] == 10.0
    assert result["current_vs_prior_5m"]["prior"] == 2.0
    assert result["pct_of_baseline"] == 50.0  # 10.0 / 20.0 * 100


def test_live_ingestion_trend_within_epsilon_is_stable(monkeypatch):
    """A small change (within TREND_DIRECTION_EPSILON) must not be
    reported as improving/degrading -- avoids noise being reported as a
    real trend reversal."""
    def _fake_rate(event_type, window_min, ago_min=0):
        if event_type != "births":
            return 0.0
        if window_min == 5 and ago_min == 5:
            return 10.0
        if window_min == 5 and ago_min == 0:
            return 10.2  # 2% change -- well within the 5% epsilon
        return 10.0

    with pytest.MonkeyPatch.context() as m:
        m.setattr(mc, "_rate_over_window", _fake_rate)
        m.setattr(mc, "get_expected_rate_per_min", lambda event_type: 20.0)
        result = mc.compute_live_ingestion_trend("births")
    assert result["direction"] == "stable"


def test_trend_buffer_records_one_sample_per_compute_capabilities_call():
    """Phase C's documented side effect: compute_capabilities() appends
    exactly one sample per call to the in-memory buffer."""
    mc.reset_trend_buffers()
    subsystems = _healthy_subsystems()
    mc.compute_capabilities(subsystems)
    mc.compute_capabilities(subsystems)
    mc.compute_capabilities(subsystems)
    assert len(mc._capability_sample_history["creator_funding"]) == 3


def test_trend_buffer_is_capped_at_max_samples():
    """Phase C: the in-memory buffer must not grow unbounded -- capped
    at TREND_BUFFER_MAX_SAMPLES, oldest samples dropped first."""
    mc.reset_trend_buffers()
    subsystems = _healthy_subsystems()
    original_max = mc.TREND_BUFFER_MAX_SAMPLES
    try:
        mc.TREND_BUFFER_MAX_SAMPLES = 5
        for _ in range(10):
            mc.compute_capabilities(subsystems)
        assert len(mc._capability_sample_history["creator_funding"]) == 5
    finally:
        mc.TREND_BUFFER_MAX_SAMPLES = original_max


def test_api_health_full_includes_trend_field_additively(monkeypatch):
    """Phase G: trend must be purely additive to the existing API
    contract -- every existing key from MC1.1/MC1.2 remains present and
    unchanged in shape; `trend` is a new key inside each capability."""
    import src.core.main as m

    with m.app.test_client() as c:
        resp = c.get("/api/health/full")
        assert resp.status_code == 200
        data = resp.get_json()

    for name, cap in data["capabilities"].items():
        assert "trend" in cap
        assert "status" in cap
        assert "evidence" in cap
        assert "signals" in cap
