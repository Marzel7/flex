import json
from pathlib import Path

import pytest

from src.evidence.contracts.production_shadow_observer_provenance import (
    ObserverAttemptRecorder, verify_observer_attempt_bundle,
)
from src.evidence.contracts.production_shadow_telemetry_observer import (
    HEALTHY, ProductionShadowTelemetryObserverError, ProductionTelemetryObserver,
    SupervisorResult, TelemetryDependencies, parse_supervisor_status,
    telemetry_observer_contract_digest,
)


SUPERVISOR_OK = """alert_evaluator RUNNING pid 10, uptime 1:00:00
walkback_worker RUNNING pid 20, uptime 1:00:00
watchtower_listener RUNNING pid 30, uptime 1:00:00
ws_cascade RUNNING pid 40, uptime 1:00:00
evidence_writer STOPPED Not started
"""


class Clock:
    def __init__(self): self.value = 1_000.0
    def __call__(self): return self.value
    def sleep(self, seconds): self.value += seconds


def _deps(clock=None, **overrides):
    clock = clock or Clock()
    values = dict(
        supervisor_status=lambda: SupervisorResult(3, SUPERVISOR_OK),
        serializer_snapshot=lambda: {"_snapshot_at": clock(), "p99_wait_ms": 1.0, "lock_errors_24h": 0, "queue_depth": 0},
        primary_fd_count=lambda _pid: 0,
        critical_listener_count=lambda: 0,
        authoritative_write_lease_present=lambda: False,
        release_pending_metadata=lambda: (("old.tmp", 1, 2),),
        database_wal_healthy=lambda: True,
        feed_states=lambda: {"pumpportal": HEALTHY, "pumpswap": HEALTHY, "ingestion": HEALTHY},
        clock=clock,
        sleep=clock.sleep,
    )
    values.update(overrides)
    return TelemetryDependencies(**values)


def _recorder(tmp_path):
    attempt = tmp_path / "attempt"; attempt.mkdir()
    recorder = ObserverAttemptRecorder(
        attempt, authorization_id="psi0b-e9-test", authorization_digest="1" * 64,
        preflight_digest="2" * 64, launcher_contract_digest="3" * 64,
    )
    return recorder, attempt


@pytest.mark.parametrize("return_code", (0, 3))
def test_supervisor_aggregate_zero_and_three_parse_required_services(return_code):
    parsed = parse_supervisor_status(SupervisorResult(return_code, SUPERVISOR_OK))
    assert parsed.return_code == return_code
    assert parsed.service_identities["watchtower_listener"] == {"state": "RUNNING", "pid": 30}
    assert "evidence_writer" not in parsed.service_identities


@pytest.mark.parametrize(("result", "reason"), (
    (SupervisorResult(4, SUPERVISOR_OK), "TRANSPORT_FAILED"),
    (SupervisorResult(3, ""), "OUTPUT_MISSING"),
    (SupervisorResult(3, "malformed"), "OUTPUT_MALFORMED"),
    (SupervisorResult(3, "watchtower_listener RUNNING pid 30, uptime 1\n"), "REQUIRED_SERVICE_MISSING"),
    (SupervisorResult(3, SUPERVISOR_OK.replace("ws_cascade RUNNING pid 40, uptime 1:00:00", "ws_cascade STOPPED Not started")), "REQUIRED_SERVICE_NOT_RUNNING"),
))
def test_supervisor_invalid_inputs_fail_closed(result, reason):
    with pytest.raises(ProductionShadowTelemetryObserverError, match=reason):
        parse_supervisor_status(result)


def test_three_checkpoint_prestart_pass_records_transport_and_spacing(tmp_path):
    clock = Clock(); recorder, attempt = _recorder(tmp_path)
    observer = ProductionTelemetryObserver(_deps(clock))
    decision = observer.prestart(recorder)
    assert decision.status == "PASS"
    recorder.record_decision(status=decision.status, decision_digest=decision.decision_digest, reason_codes=decision.reason_codes)
    recorder.finalize(terminal_status="OBSERVER_PASS", terminal_reason_code="PASS")
    terminal = verify_observer_attempt_bundle(attempt)
    assert terminal.checkpoint_attempt_count == 3
    rows = [json.loads(line) for line in (attempt / "observer_attempt.jsonl").read_text().splitlines()]
    checkpoint_rows = [row for row in rows if row["event"] == "CHECKPOINT_ATTEMPT"]
    assert [row["payload"]["observed_at_epoch"] for row in checkpoint_rows] == [1000.0, 1030.0, 1060.0]
    assert all(row["payload"]["supervisor_service_identities"]["_transport"]["return_code"] == 3 for row in checkpoint_rows)
    assert len(telemetry_observer_contract_digest()) == 64


@pytest.mark.parametrize(("override", "reason"), (
    ({"primary_fd_count": lambda _pid: 8}, "PRIMARY_FD_WARNING"),
    ({"serializer_snapshot": lambda: {"_snapshot_at": 1000.0, "p99_wait_ms": 1000.0, "lock_errors_24h": 0, "queue_depth": 0}}, "SERIALIZER_P99"),
    ({"authoritative_write_lease_present": lambda: True}, "AUTHORITATIVE_WRITE_LEASE"),
    ({"database_wal_healthy": lambda: False}, "DATABASE_WAL"),
    ({"feed_states": lambda: {"pumpportal": "UNKNOWN", "pumpswap": HEALTHY, "ingestion": HEALTHY}}, "FEED_OR_INGESTION"),
))
def test_gate_failures_are_recorded_before_raise(tmp_path, override, reason):
    recorder, attempt = _recorder(tmp_path)
    observer = ProductionTelemetryObserver(_deps(**override))
    with pytest.raises(ProductionShadowTelemetryObserverError, match=reason) as captured:
        observer.checkpoint(recorder)
    recorder.record_exception(captured.value)
    recorder.finalize(terminal_status="OBSERVER_FAILED", terminal_reason_code="FAILED", exception=captured.value)
    terminal = verify_observer_attempt_bundle(attempt)
    assert terminal.checkpoint_attempt_count == 1
    rows = [json.loads(line) for line in (attempt / "observer_attempt.jsonl").read_text().splitlines()]
    checkpoint = next(row for row in rows if row["event"] == "CHECKPOINT_ATTEMPT")
    assert reason in checkpoint["payload"]["gate_reason_code"]


def test_supervisor_transport_failure_records_checkpoint_attempt(tmp_path):
    recorder, attempt = _recorder(tmp_path)
    observer = ProductionTelemetryObserver(_deps(supervisor_status=lambda: SupervisorResult(7, "partial", "socket")))
    with pytest.raises(ProductionShadowTelemetryObserverError) as captured:
        observer.checkpoint(recorder)
    recorder.record_exception(captured.value)
    recorder.finalize(terminal_status="OBSERVER_FAILED", terminal_reason_code="FAILED", exception=captured.value)
    terminal = verify_observer_attempt_bundle(attempt)
    assert terminal.checkpoint_attempt_count == 1
    rows = [json.loads(line) for line in (attempt / "observer_attempt.jsonl").read_text().splitlines()]
    checkpoint = next(row for row in rows if row["event"] == "CHECKPOINT_ATTEMPT")
    assert checkpoint["payload"]["supervisor_service_identities"]["_transport"]["return_code"] == 7


def test_pid_and_release_pending_drift_fail_on_later_checkpoint(tmp_path):
    clock = Clock(); recorder, _ = _recorder(tmp_path)
    outputs = [SUPERVISOR_OK, SUPERVISOR_OK.replace("watchtower_listener RUNNING pid 30", "watchtower_listener RUNNING pid 31")]
    observer = ProductionTelemetryObserver(_deps(clock, supervisor_status=lambda: SupervisorResult(3, outputs.pop(0))))
    observer.checkpoint(recorder); clock.sleep(30)
    with pytest.raises(ProductionShadowTelemetryObserverError, match="PID_DRIFT"):
        observer.checkpoint(recorder)


def test_collection_exception_is_recorded_before_raise(tmp_path):
    recorder, attempt = _recorder(tmp_path)
    observer = ProductionTelemetryObserver(_deps(serializer_snapshot=lambda: (_ for _ in ()).throw(OSError("metrics"))))
    with pytest.raises(ProductionShadowTelemetryObserverError, match="COLLECTION_EXCEPTION"):
        observer.checkpoint(recorder)
    rows = [json.loads(line) for line in (attempt / "observer_attempt.jsonl").read_text().splitlines()]
    checkpoint = next(row for row in rows if row["event"] == "CHECKPOINT_ATTEMPT")
    assert checkpoint["payload"]["gate_reason_code"] == "PSI0B_E9_TELEMETRY_COLLECTION_EXCEPTION"
