from dataclasses import replace

import pytest

from src.evidence.contracts.production_shadow_abort_isolation import (
    STOP_TRIGGERS,
    ProductionShadowAbortIsolationError,
    build_abort_isolation_trace,
    build_production_shadow_abort_isolation_contract,
    evaluate_abort_isolation,
    verify_abort_isolation_decision,
    verify_abort_isolation_trace,
    verify_production_shadow_abort_isolation_contract,
)


HEALTH_DECISION = "1" * 64


def _trace(phase="ACTIVE", stop_trigger="QUERY_DEADLINE_BREACH", **changes):
    values = {
        "phase": phase,
        "stop_trigger": stop_trigger,
        "health_gate_decision_digest": HEALTH_DECISION,
        "connection_opened": phase == "ACTIVE",
        "transaction_started": phase == "ACTIVE",
        "progress_handler_installed": phase == "ACTIVE",
        "temporary_artifact_created": phase == "ACTIVE",
        "progress_handler_removed": phase == "ACTIVE",
        "rollback_attempted": phase == "ACTIVE",
        "transaction_resolved": phase == "ACTIVE",
        "connection_closed": phase == "ACTIVE",
        "temporary_artifact_removed": phase == "ACTIVE",
        "partial_bundle_published": False,
        "production_write_count": 0,
        "ddl_statement_count": 0,
        "service_mutation_count": 0,
        "configuration_mutation_count": 0,
        "lock_mutation_count": 0,
        "metric_mutation_count": 0,
        "retry_attempts": 0,
        "pagination_attempts": 0,
        "failover_attempts": 0,
        "degraded_bypass_attempts": 0,
        "adaptive_limit_changes": 0,
    }
    values.update(changes)
    return build_abort_isolation_trace(**values)


def test_contract_replays_and_binds_all_upstream_identities():
    contract = build_production_shadow_abort_isolation_contract()
    assert verify_production_shadow_abort_isolation_contract(contract)
    assert contract.stop_triggers == STOP_TRIGGERS
    assert not contract.grants_extraction_authority
    assert not contract.grants_activation_authority


@pytest.mark.parametrize("trigger", STOP_TRIGGERS)
def test_every_prestart_trigger_deterministically_does_not_start(trigger):
    trace = _trace(phase="PRESTART", stop_trigger=trigger)
    decision = evaluate_abort_isolation(build_production_shadow_abort_isolation_contract(), trace)
    assert decision.terminal_state == "DO_NOT_START"
    assert decision.isolation_proven
    assert decision.reason_codes == ()
    assert verify_abort_isolation_decision(decision)


@pytest.mark.parametrize("trigger", STOP_TRIGGERS)
def test_every_active_trigger_deterministically_aborts_with_cleanup(trigger):
    trace = _trace(stop_trigger=trigger)
    decision = evaluate_abort_isolation(build_production_shadow_abort_isolation_contract(), trace)
    assert decision.terminal_state == "ABORTED"
    assert decision.isolation_proven
    assert decision.reason_codes == ()


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"progress_handler_removed": False}, "PROGRESS_HANDLER_NOT_REMOVED"),
        ({"rollback_attempted": False}, "ROLLBACK_NOT_ATTEMPTED"),
        ({"transaction_resolved": False}, "TRANSACTION_NOT_RESOLVED"),
        ({"connection_closed": False}, "CONNECTION_NOT_CLOSED"),
        ({"temporary_artifact_removed": False}, "TEMPORARY_ARTIFACT_NOT_REMOVED"),
        ({"partial_bundle_published": True}, "PARTIAL_BUNDLE_PUBLISHED"),
        ({"production_write_count": 1}, "PRODUCTION_ISOLATION_VIOLATION"),
        ({"ddl_statement_count": 1}, "PRODUCTION_ISOLATION_VIOLATION"),
        ({"service_mutation_count": 1}, "PRODUCTION_ISOLATION_VIOLATION"),
        ({"configuration_mutation_count": 1}, "PRODUCTION_ISOLATION_VIOLATION"),
        ({"lock_mutation_count": 1}, "PRODUCTION_ISOLATION_VIOLATION"),
        ({"metric_mutation_count": 1}, "PRODUCTION_ISOLATION_VIOLATION"),
        ({"retry_attempts": 1}, "RETRY_OR_SCOPE_WIDENING_ATTEMPTED"),
        ({"pagination_attempts": 1}, "RETRY_OR_SCOPE_WIDENING_ATTEMPTED"),
        ({"failover_attempts": 1}, "RETRY_OR_SCOPE_WIDENING_ATTEMPTED"),
        ({"degraded_bypass_attempts": 1}, "RETRY_OR_SCOPE_WIDENING_ATTEMPTED"),
        ({"adaptive_limit_changes": 1}, "RETRY_OR_SCOPE_WIDENING_ATTEMPTED"),
    ],
)
def test_fault_injection_fails_isolation_proof(changes, reason):
    decision = evaluate_abort_isolation(
        build_production_shadow_abort_isolation_contract(), _trace(**changes),
    )
    assert decision.terminal_state == "ISOLATION_FAILURE"
    assert not decision.isolation_proven
    assert any(reason in item for item in decision.reason_codes)


def test_prestart_resource_open_is_fail_closed():
    decision = evaluate_abort_isolation(
        build_production_shadow_abort_isolation_contract(),
        _trace(phase="PRESTART", connection_opened=True),
    )
    assert decision.terminal_state == "ISOLATION_FAILURE"
    assert "PSI0A_G_PRESTART_RESOURCE_OPENED" in decision.reason_codes


def test_conditional_cleanup_is_not_fabricated_for_unopened_resources():
    trace = _trace(
        connection_opened=False,
        transaction_started=False,
        progress_handler_installed=False,
        temporary_artifact_created=False,
        progress_handler_removed=False,
        rollback_attempted=False,
        transaction_resolved=False,
        connection_closed=False,
        temporary_artifact_removed=False,
    )
    assert evaluate_abort_isolation(
        build_production_shadow_abort_isolation_contract(), trace,
    ).isolation_proven


def test_contract_trace_and_decision_mutations_fail_exact_replay():
    contract = build_production_shadow_abort_isolation_contract()
    with pytest.raises(ProductionShadowAbortIsolationError, match="CONTRACT_REPLAY"):
        verify_production_shadow_abort_isolation_contract(replace(contract, retry_allowed=True))
    trace = _trace()
    with pytest.raises(ProductionShadowAbortIsolationError, match="TRACE_REPLAY"):
        verify_abort_isolation_trace(replace(trace, connection_closed=False))
    decision = evaluate_abort_isolation(contract, trace)
    with pytest.raises(ProductionShadowAbortIsolationError, match="DECISION_REPLAY"):
        verify_abort_isolation_decision(replace(decision, isolation_proven=False))


@pytest.mark.parametrize("bad", ("UNKNOWN", "", "query_deadline_breach"))
def test_unknown_trigger_is_rejected(bad):
    with pytest.raises(ProductionShadowAbortIsolationError, match="UNKNOWN_STOP_TRIGGER"):
        _trace(stop_trigger=bad)
