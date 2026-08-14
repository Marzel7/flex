from dataclasses import replace

import pytest

from src.evidence.contracts.production_shadow_health_gate import (
    ProductionShadowHealthGateError,
    build_health_checkpoint,
    build_production_shadow_health_gate_contract,
    evaluate_active_health_gate,
    evaluate_prestart_health_gate,
    verify_health_checkpoint,
    verify_health_gate_decision,
    verify_production_shadow_health_gate_contract,
)


def _checkpoint(observed_at_epoch, **changes):
    values = {
        "observed_at_epoch": observed_at_epoch,
        "listener_pid": 1234,
        "listener_service_state": "RUNNING",
        "primary_fd_count": 0,
        "critical_listener_db_handle_count": 0,
        "serializer_p99_wait_ms": 100.0,
        "serializer_lock_errors": 4,
        "serializer_queue_depth": 0,
        "database_wal_state": "HEALTHY",
        "write_lease_state": "HEALTHY",
        "pumpportal_state": "HEALTHY",
        "pumpswap_state": "HEALTHY",
        "ingestion_state": "HEALTHY",
        "worker_state": "HEALTHY",
        "queue_state": "HEALTHY",
        "service_state": "HEALTHY",
        "telemetry_complete": True,
    }
    values.update(changes)
    return build_health_checkpoint(**values)


def _three():
    return (_checkpoint(40.0), _checkpoint(70.0), _checkpoint(100.0))


def test_contract_checkpoint_and_pass_decision_replay_exactly():
    contract = build_production_shadow_health_gate_contract()
    assert verify_production_shadow_health_gate_contract(contract)
    checkpoints = _three()
    assert all(verify_health_checkpoint(item) for item in checkpoints)
    decision = evaluate_prestart_health_gate(
        contract, checkpoints, now_epoch=110.0, baseline_lock_errors=4,
    )
    assert decision.status == "PASS"
    assert decision.reason_codes == ()
    assert not decision.grants_extraction_authority
    assert not decision.grants_activation_authority
    assert verify_health_gate_decision(decision)


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"listener_pid": 999}, "LISTENER_PID_CHANGED"),
        ({"listener_service_state": "STOPPED"}, "LISTENER_NOT_RUNNING"),
        ({"primary_fd_count": 8}, "PRIMARY_FD_WARNING"),
        ({"critical_listener_db_handle_count": 1}, "CRITICAL_LISTENER"),
        ({"serializer_p99_wait_ms": 1000.0}, "SERIALIZER_P99"),
        ({"serializer_lock_errors": 5}, "LOCK_ERROR_INCREMENT"),
        ({"database_wal_state": "UNKNOWN"}, "DATABASE_WAL_UNHEALTHY"),
        ({"write_lease_state": "DEGRADED"}, "WRITE_LEASE_UNHEALTHY"),
        ({"pumpportal_state": "UNKNOWN"}, "PUMPPORTAL_UNHEALTHY"),
        ({"pumpswap_state": "DOWN"}, "PUMPSWAP_UNHEALTHY"),
        ({"ingestion_state": "DEGRADED"}, "INGESTION_UNHEALTHY"),
        ({"worker_state": "UNKNOWN"}, "WORKER_UNHEALTHY"),
        ({"queue_state": "DEGRADED"}, "QUEUE_UNHEALTHY"),
        ({"service_state": "UNKNOWN"}, "SERVICE_UNHEALTHY"),
        ({"telemetry_complete": False}, "TELEMETRY_INCOMPLETE"),
    ],
)
def test_prestart_gates_fail_closed_with_named_reasons(changes, reason):
    checkpoints = list(_three())
    checkpoints[-1] = _checkpoint(100.0, **changes)
    decision = evaluate_prestart_health_gate(
        build_production_shadow_health_gate_contract(), tuple(checkpoints),
        now_epoch=110.0, baseline_lock_errors=4,
    )
    assert decision.status == "DO_NOT_START"
    assert any(reason in item for item in decision.reason_codes)


def test_stale_future_missing_and_bad_spacing_fail_closed():
    contract = build_production_shadow_health_gate_contract()
    stale = evaluate_prestart_health_gate(contract, _three(), now_epoch=200.0, baseline_lock_errors=4)
    assert "PSI0A_F_TELEMETRY_STALE" in stale.reason_codes
    future = (_checkpoint(100.0), _checkpoint(130.0), _checkpoint(170.0))
    result = evaluate_prestart_health_gate(contract, future, now_epoch=160.0, baseline_lock_errors=4)
    assert "PSI0A_F_TELEMETRY_FROM_FUTURE" in result.reason_codes
    assert "PSI0A_F_CHECKPOINT_SPACING_INVALID" in result.reason_codes
    with pytest.raises(ProductionShadowHealthGateError, match="CHECKPOINT_COUNT"):
        evaluate_prestart_health_gate(contract, _three()[:2], now_epoch=110.0, baseline_lock_errors=4)


def test_single_queue_sample_is_transient_but_two_spaced_samples_stop():
    contract = build_production_shadow_health_gate_contract()
    previous = _checkpoint(70.0, serializer_queue_depth=0)
    current = _checkpoint(100.0, serializer_queue_depth=1)
    transient = evaluate_active_health_gate(
        contract, previous, current, now_epoch=101.0,
        expected_listener_pid=1234, baseline_lock_errors=4,
    )
    assert transient.status == "PASS"
    previous = _checkpoint(70.0, serializer_queue_depth=1)
    sustained = evaluate_active_health_gate(
        contract, previous, current, now_epoch=101.0,
        expected_listener_pid=1234, baseline_lock_errors=4,
    )
    assert sustained.status == "STOP"
    assert "PSI0A_F_SUSTAINED_SERIALIZER_QUEUE" in sustained.reason_codes


def test_two_nonzero_queue_samples_without_exact_spacing_fail_closed():
    decision = evaluate_active_health_gate(
        build_production_shadow_health_gate_contract(),
        _checkpoint(60.0, serializer_queue_depth=1),
        _checkpoint(100.0, serializer_queue_depth=1),
        now_epoch=101.0, expected_listener_pid=1234, baseline_lock_errors=4,
    )
    assert decision.status == "STOP"
    assert "PSI0A_F_CHECKPOINT_SPACING_INVALID" in decision.reason_codes


def test_active_gate_stops_immediately_on_current_failure():
    decision = evaluate_active_health_gate(
        build_production_shadow_health_gate_contract(), _checkpoint(70.0),
        _checkpoint(100.0, primary_fd_count=8), now_epoch=101.0,
        expected_listener_pid=1234, baseline_lock_errors=4,
    )
    assert decision.status == "STOP"
    assert "PSI0A_F_PRIMARY_FD_WARNING_THRESHOLD_REACHED" in decision.reason_codes


def test_contract_checkpoint_and_decision_mutations_fail_replay():
    contract = build_production_shadow_health_gate_contract()
    with pytest.raises(ProductionShadowHealthGateError, match="CONTRACT_REPLAY"):
        verify_production_shadow_health_gate_contract(replace(contract, retry_allowed=True))
    checkpoint = _checkpoint(100.0)
    with pytest.raises(ProductionShadowHealthGateError, match="CHECKPOINT_REPLAY"):
        verify_health_checkpoint(replace(checkpoint, primary_fd_count=1))
    decision = evaluate_prestart_health_gate(contract, _three(), now_epoch=110.0, baseline_lock_errors=4)
    with pytest.raises(ProductionShadowHealthGateError, match="DECISION_REPLAY"):
        verify_health_gate_decision(replace(decision, status="DO_NOT_START"))
