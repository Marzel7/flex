from src.ops.treasury_activity import ACTIVE, DORMANT, HOT, RETIRED_CANDIDATE
from src.ops.treasury_outbound_monitoring import (
    FEATURE_FLAG_DEFAULT, MonitoringPlan, TreasuryMonitorState, classify_outbound_fact,
    daily_request_estimate, next_reconciliation, scaled_activity_counts, storage_estimate, update_coverage,
)


def test_feature_defaults_off_and_rejects_live_enablement():
    assert FEATURE_FLAG_DEFAULT is False
    try:
        MonitoringPlan(enabled=True).validate()
    except ValueError as exc:
        assert str(exc) == "MONITORING_FEATURE_MUST_REMAIN_DISABLED_IN_DEV"
    else:
        raise AssertionError("live monitoring must be rejected in DEV")


def test_all_activity_classes_remain_reconciliation_eligible():
    for activity in (HOT, ACTIVE, DORMANT, RETIRED_CANDIDATE):
        outcome = next_reconciliation(TreasuryMonitorState("treasury", activity, "cursor", "LIVE_COVERED"), now=100, last_checked_at=None)
        assert outcome["due"] is True
        assert outcome["request"] == "getSignaturesForAddress"
        assert outcome["limit"] == 20


def test_cursor_is_not_advanced_until_complete_decode_coverage():
    assert update_coverage(selected_signatures=["a", "b"], decoded_signatures=["a"], provider_stopped=False) == "COVERAGE_INCOMPLETE"
    assert update_coverage(selected_signatures=["a"], decoded_signatures=["a"], provider_stopped=False) == "LIVE_COVERED"
    assert update_coverage(selected_signatures=["a"], decoded_signatures=[], provider_stopped=True) == "PROVIDER_LIMIT_STOP"


def test_only_direct_balance_verified_confirmed_treasury_facts_are_indexable():
    valid = {"sender": "T", "receiver": "R", "route_semantics": "DIRECT", "balance_delta_verified": True}
    assert classify_outbound_fact(valid, confirmed_treasuries=["T"])["status"] == "VERIFIED_OUTBOUND"
    assert classify_outbound_fact({**valid, "balance_delta_verified": False}, confirmed_treasuries=["T"])["status"] == "NOT_INDEXABLE_OUTBOUND"
    assert classify_outbound_fact({**valid, "route_semantics": "ACCOUNT_CLOSE"}, confirmed_treasuries=["T"])["status"] == "NOT_INDEXABLE_OUTBOUND"


def test_cost_plan_uses_measured_mix_and_keeps_decode_assumption_explicit():
    counts = {HOT: 2, ACTIVE: 17, DORMANT: 14, RETIRED_CANDIDATE: 50}
    estimate = daily_request_estimate(counts)
    assert estimate["treasury_count"] == 83
    assert estimate["signature_requests_per_day"] == 664
    assert estimate["transaction_requests_per_day"] == 0
    assert daily_request_estimate(counts, decoded_transactions_per_poll=1)["total_rpc_requests_per_day"] == 1328


def test_population_scaling_is_deterministic_and_storage_is_bounded():
    measured = {HOT: 2, ACTIVE: 17, DORMANT: 14, RETIRED_CANDIDATE: 50}
    assert sum(scaled_activity_counts(population=1_000, measured_counts=measured).values()) == 1_000
    storage = storage_estimate(compact_records_per_day=100)
    assert storage["estimated_logical_bytes_per_day"] == 102_400
    assert storage["max_single_file_bytes"] < 500_000_000
