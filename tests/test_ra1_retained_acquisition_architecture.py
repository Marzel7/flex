from __future__ import annotations

from pathlib import Path

from src.evidence.contracts.ra1_retained_acquisition_architecture import (
    RetentionBudget,
    RetentionLedger,
    RetentionResourcePolicy,
    RetainedObservationEvent,
    bytes_gib,
    candidate_budget_projection,
    estimate_growth,
    fresh_ledger,
    recommend_budget_option,
    retention_decision,
    resource_state,
    resource_state_transition,
    projected_ledger_hot_store,
    RESOURCE_STATE_CRITICAL,
    RESOURCE_STATE_DEGRADED,
    RESOURCE_STATE_NORMAL,
    OUTCOME_RECORD_FULL_PAYLOAD,
    OUTCOME_RECORD_METADATA_ONLY,
)


def test_resource_states_transition_monotonic_and_expected():
    policy = RetentionResourcePolicy(normal_min_free_bytes=20 * 1024**3, degraded_min_free_bytes=15 * 1024**3,
                                   critical_min_free_bytes=10 * 1024**3, hard_floor_bytes=1 * 1024**3)
    checks = [
        ("normal-high", 25 * 1024**3, RESOURCE_STATE_NORMAL),
        ("degraded", 15 * 1024**3 + 1, RESOURCE_STATE_DEGRADED),
        ("critical", 9 * 1024**3, RESOURCE_STATE_CRITICAL),
    ]
    for label, free_bytes, expected in checks:
        assert resource_state(free_bytes, policy) == expected, label

    transition = resource_state_transition(
        [
            ("n", 25 * 1024**3),
            ("d", 16 * 1024**3),
            ("c", 9 * 1024**3),
        ],
        policy,
    )
    assert transition == [RESOURCE_STATE_NORMAL, RESOURCE_STATE_DEGRADED, RESOURCE_STATE_CRITICAL]


def test_growth_projection_and_budget_candidates_are_deterministic():
    growth = estimate_growth(observed_db_bytes=61_916_803_072, observed_observations=1_806_935, observed_days=7.0)
    assert growth["observed_growth"]["observed_gb_per_day"] > 8.0
    assert growth["observed_growth"]["observed_observations_per_day"] > 200_000
    projections = candidate_budget_projection(
        growth["observed_growth"]["observed_gb_per_day"],
        {
            "option_a": 1 * 1024**3,
            "option_b": 512 * 1024**2,
            "option_c": 256 * 1024**2,
        },
    )
    assert set(projections.keys()) == {"option_a", "option_b", "option_c"}
    assert projections["option_a"]["reduction_percent"] > 85.0
    assert projections["option_c"]["reduction_percent"] > projections["option_a"]["reduction_percent"]
    assert recommend_budget_option(projections, minimum_ratio=0.80) in {"option_a", "option_b", "option_c"}


def test_hot_store_projection_and_retention_controls():
    budget = RetentionBudget(
        daily_payload_bytes=512 * 1024 * 1024,
        per_hour_payload_bytes=32 * 1024 * 1024,
        max_payload_bytes_per_correlation=2 * 1024**3,
        max_payloads_per_correlation=24,
        max_payload_bytes_per_mint=8 * 1024**3,
        max_payloads_per_mint=64,
        max_payload_bytes_per_observation=64 * 1024 * 1024,
    )
    hot = projected_ledger_hot_store(250_000.0, budget)
    assert hot["total_hot_window_bytes"] > 0
    assert hot["hot_window_days"] == 3
    assert hot["payload_bytes_hot_window"] == budget.daily_payload_bytes * budget.hot_payload_window_days


def test_retention_decision_enforces_failing_paths_before_storing_payload():
    policy = RetentionResourcePolicy(
        normal_min_free_bytes=10 * 1024**3,
        degraded_min_free_bytes=8 * 1024**3,
        critical_min_free_bytes=5 * 1024**3,
        hard_floor_bytes=1 * 1024**3,
    )
    budget = RetentionBudget(
        daily_payload_bytes=10_000,
        per_hour_payload_bytes=10_000,
        max_payload_bytes_per_correlation=10_000,
        max_payloads_per_correlation=1,
        max_payload_bytes_per_mint=10_000,
        max_payloads_per_mint=1,
        max_payload_bytes_per_observation=4_096,
    )

    ledger = fresh_ledger("2026-08-18T00:00:00Z")
    outcome, reason, next_ledger = retention_decision(
        state=ledger,
        event=RetainedObservationEvent("obs-1", "corr", "mint", 2048, "2026-08-18T00:00:01Z", cold_archive_verified=True),
        policy=policy,
        budget=budget,
        free_bytes=4 * 1024**3,
    )
    assert outcome == OUTCOME_RECORD_METADATA_ONLY
    assert reason == "resource_critical_no_full_payload"
    assert next_ledger == ledger

    outcome2, reason2, next_ledger2 = retention_decision(
        state=ledger,
        event=RetainedObservationEvent("obs-2", "corr", "mint", 2048, "2026-08-18T00:00:02Z", cold_archive_verified=True),
        policy=policy,
        budget=budget,
        free_bytes=15 * 1024**3,
    )
    assert outcome2 == OUTCOME_RECORD_FULL_PAYLOAD
    assert reason2 == "within_contract"
    assert next_ledger2.payload_events == 1
    assert next_ledger2.payload_bytes_day == 2048
    assert next_ledger2.payload_count_by_correlation["corr"] == 1

    outcome3, reason3, next_ledger3 = retention_decision(
        state=next_ledger2,
        event=RetainedObservationEvent("obs-3", "corr", "mint", 1024, "2026-08-18T00:00:03Z", cold_archive_verified=True),
        policy=policy,
        budget=budget,
        free_bytes=15 * 1024**3,
    )
    assert outcome3 != OUTCOME_RECORD_FULL_PAYLOAD
    assert reason3 == "correlation_count_ceiling"
    assert next_ledger3 == next_ledger2


def test_run_ra1_script_writes_artifact(tmp_path: Path):
    from scripts.run_ra1_retained_acquisition_architecture_preflight import build_preflight

    ra0_experiment = {
        "bounded_source_identity": {
            "db_path": "database/evidence_platform/production/retained_acquisition.db",
        },
    }
    ra0_preflight = {
        "diagnosis": "UNIQUE_VOLUME_DOMINANT",
    }

    artifact = build_preflight(
        ra0_experiment=ra0_experiment,
        ra0_preflight=ra0_preflight,
        ra0_experiment_path="/tmp/ra0_bounded_experiment.json",
        ra0_preflight_path="/tmp/ra0_bounded_preflight.json",
        observed_window_days=7,
        observed_db_bytes=61_916_803_072,
        observed_observations=1_806_935,
        free_bytes=5_496_811_520,
    )

    assert artifact["schema_version"] == "ra1_retained_acquisition_architecture_preflight.v1"
    assert artifact["verdict"] == "RA1_BOUNDED_RETENTION_ARCHITECTURE_QUALIFIED"
    assert artifact["migration_planning"]["archive_verification_required_before_retire"]
    assert artifact["candidate_daily_payload_budgets"]["recommended_option"]["name"] in {
        "OPTION_A_1GIB",
        "OPTION_B_512MIB",
        "OPTION_C_256MIB",
    }
