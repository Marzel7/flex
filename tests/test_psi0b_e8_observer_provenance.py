from dataclasses import asdict
import json
from pathlib import Path

import pytest

from src.evidence.contracts.production_shadow_health_gate import (
    HEALTHY, RUNNING, build_health_checkpoint,
    build_production_shadow_health_gate_contract, evaluate_prestart_health_gate,
)
from src.evidence.contracts.production_shadow_launcher import (
    ProductionShadowLauncherError, launch_authorized_shadow_with_provenance,
)
from src.evidence.contracts.production_shadow_observer_provenance import (
    ProductionShadowObserverProvenanceError, observer_provenance_contract_digest,
    verify_observer_attempt_bundle,
)
from src.evidence.contracts.production_shadow_production_binding import (
    build_production_execution_authorization, production_binding_contract_digest,
)
from src.evidence.contracts.production_shadow_superseding_preflight import SHADOW_OUTPUT


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "docs/audits/psi0b_e3_superseding_preflight"


def _authorization(tmp_path, authorization_id="psi0b-e8-test-authorization"):
    record = build_production_execution_authorization(
        authorization_id=authorization_id,
        run_id="psi0b-shadow-20260814-02",
        output_directory=SHADOW_OUTPUT,
    )
    document = {
        "schema_version": "psi0b-e.authorization.v1",
        "engineering_commit": "5" * 40,
        "production_binding_contract_digest": production_binding_contract_digest(),
        "authorization": asdict(record),
    }
    path = tmp_path / "authorization.json"
    path.write_text(json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n")
    consumption = tmp_path / "consumption"; consumption.mkdir()
    attempt = tmp_path / "attempt"; attempt.mkdir()
    return path, record, consumption, attempt


def _decision(*, primary_fd_count=0):
    contract = build_production_shadow_health_gate_contract()
    checkpoints = tuple(
        build_health_checkpoint(
            observed_at_epoch=float(position * 30), listener_pid=123,
            listener_service_state=RUNNING, primary_fd_count=primary_fd_count,
            critical_listener_db_handle_count=0, serializer_p99_wait_ms=1.0,
            serializer_lock_errors=0, serializer_queue_depth=0,
            database_wal_state=HEALTHY, write_lease_state=HEALTHY,
            pumpportal_state=HEALTHY, pumpswap_state=HEALTHY,
            ingestion_state=HEALTHY, worker_state=HEALTHY,
            queue_state=HEALTHY, service_state=HEALTHY, telemetry_complete=True,
        )
        for position in range(3)
    )
    return evaluate_prestart_health_gate(contract, checkpoints, now_epoch=60.0, baseline_lock_errors=0)


def _checkpoint_payload(sequence=1, *, reason=None, lease="ABSENT"):
    return {
        "checkpoint_sequence": sequence,
        "phase": "PRESTART",
        "query_id": None,
        "observed_at_epoch": float(sequence * 30),
        "supervisor_service_identities": {"watchtower_listener": {"pid": 123, "state": "RUNNING"}},
        "primary_fd_count": 0,
        "serializer_snapshot_digest": "1" * 64,
        "serializer_lock_error_baseline": 0,
        "serializer_queue_depth": 0,
        "authoritative_write_lease_state": lease,
        "release_pending_metadata_digest": "2" * 64,
        "release_pending_metadata_components": (("old.tmp", 1, 2),),
        "database_wal_state": "HEALTHY",
        "pumpportal_state": "HEALTHY",
        "pumpswap_state": "HEALTHY",
        "ingestion_state": "HEALTHY",
        "gate_reason_code": reason,
    }


def test_pass_records_replay_before_consumption_and_execution(tmp_path):
    authorization, record, consumption, attempt = _authorization(tmp_path)
    calls = []

    def observe(recorder):
        for position in range(1, 4):
            recorder.record_checkpoint_attempt(**_checkpoint_payload(position))
        return _decision()

    result = launch_authorized_shadow_with_provenance(
        authorization, ARTIFACT, consumption, attempt,
        observer_bootstrap=observe,
        executor=lambda *_: calls.append((attempt / "observer_attempt.jsonl").is_file()) or "EXECUTED",
    )
    assert result == "EXECUTED"
    assert calls == [True]
    assert (consumption / f"{record.authorization_id}.consumed.json").is_file()
    terminal = verify_observer_attempt_bundle(attempt)
    assert terminal.checkpoint_attempt_count == 3
    assert terminal.grants_extraction_authority is False
    assert len(observer_provenance_contract_digest()) == 64


@pytest.mark.parametrize(("reason", "message", "lease"), (
    ("PSI0B_E_WRITE_LEASE_PRESENT", "transient write lease", "PRESENT"),
    ("PSI0A_F_TELEMETRY_STALE", "stale telemetry", "ABSENT"),
    ("PSI0A_F_LISTENER_PID_CHANGED", "service pid drift", "ABSENT"),
    ("PSI0A_F_PRIMARY_FD_WARNING_THRESHOLD_REACHED", "descriptor threshold", "ABSENT"),
    ("PSI0A_F_SERIALIZER_P99_THRESHOLD_REACHED", "serializer pressure", "ABSENT"),
    ("PSI0A_F_SUSTAINED_SERIALIZER_QUEUE", "queue pressure", "ABSENT"),
    ("PSI0A_F_PUMPPORTAL_UNHEALTHY_OR_UNKNOWN", "feed failure", "ABSENT"),
    ("PSI0B_E_RELEASE_PENDING_METADATA_CHANGED", "filesystem drift", "ABSENT"),
    ("PSI0B_E_OBSERVER_EXCEPTION", "observer exception", "ABSENT"),
))
def test_named_observer_failures_are_durable_exact_and_unconsumed(tmp_path, reason, message, lease):
    authorization, _, consumption, attempt = _authorization(tmp_path, f"psi0b-e8-{reason.lower().replace('_', '-')}")
    calls = []

    def observe(recorder):
        recorder.record_checkpoint_attempt(**_checkpoint_payload(reason=reason, lease=lease))
        raise RuntimeError(message)

    with pytest.raises(ProductionShadowLauncherError, match="PSI0B_E7_OBSERVER_BOOTSTRAP_FAILED"):
        launch_authorized_shadow_with_provenance(
            authorization, ARTIFACT, consumption, attempt,
            observer_bootstrap=observe, executor=lambda *_: calls.append(True),
        )
    terminal = verify_observer_attempt_bundle(attempt)
    assert terminal.terminal_status == "OBSERVER_FAILED"
    assert terminal.terminal_reason_code == "PSI0B_E7_OBSERVER_BOOTSTRAP_FAILED"
    assert terminal.exact_exception_type == "RuntimeError"
    assert terminal.exact_exception_message == message
    assert terminal.checkpoint_attempt_count == 1
    assert calls == [] and list(consumption.iterdir()) == []
    assert not SHADOW_OUTPUT.exists()


def test_do_not_start_decision_records_replay_and_does_not_consume(tmp_path):
    authorization, _, consumption, attempt = _authorization(tmp_path)

    def observe(recorder):
        recorder.record_checkpoint_attempt(**_checkpoint_payload(reason="PSI0A_F_PRIMARY_FD_WARNING_THRESHOLD_REACHED"))
        return _decision(primary_fd_count=8)

    with pytest.raises(ProductionShadowLauncherError, match="PSI0B_E7_PRESTART_DO_NOT_START"):
        launch_authorized_shadow_with_provenance(
            authorization, ARTIFACT, consumption, attempt,
            observer_bootstrap=observe, executor=lambda *_: None,
        )
    terminal = verify_observer_attempt_bundle(attempt)
    assert terminal.terminal_status == "PRESTART_DO_NOT_START"
    assert list(consumption.iterdir()) == []


def test_executor_failure_seals_active_provenance_after_consumption(tmp_path):
    authorization, record, consumption, attempt = _authorization(tmp_path)

    def observe(recorder):
        for position in range(1, 4):
            recorder.record_checkpoint_attempt(**_checkpoint_payload(position))
        return _decision()

    def fail(*_):
        raise RuntimeError("active stop")

    with pytest.raises(RuntimeError, match="active stop"):
        launch_authorized_shadow_with_provenance(
            authorization, ARTIFACT, consumption, attempt,
            observer_bootstrap=observe, executor=fail,
        )
    terminal = verify_observer_attempt_bundle(attempt)
    assert terminal.terminal_status == "OBSERVER_FAILED"
    assert terminal.terminal_reason_code == "PSI0B_E11_ACTIVE_OR_EXECUTION_FAILED"
    assert (consumption / f"{record.authorization_id}.consumed.json").is_file()


def test_each_transition_and_terminal_are_fsynced(tmp_path, monkeypatch):
    authorization, _, consumption, attempt = _authorization(tmp_path)
    calls = []
    monkeypatch.setattr("src.evidence.contracts.production_shadow_observer_provenance.os.fsync", lambda fd: calls.append(fd))

    def observe(recorder):
        recorder.record_checkpoint_attempt(**_checkpoint_payload())
        raise RuntimeError("stop")

    with pytest.raises(ProductionShadowLauncherError):
        launch_authorized_shadow_with_provenance(
            authorization, ARTIFACT, consumption, attempt,
            observer_bootstrap=observe, executor=lambda *_: None,
        )
    terminal = verify_observer_attempt_bundle(attempt)
    assert len(calls) == terminal.transition_count + 1


def test_attempt_directory_must_be_new_empty(tmp_path):
    authorization, _, consumption, attempt = _authorization(tmp_path)
    (attempt / "existing").write_text("occupied")
    with pytest.raises(ProductionShadowObserverProvenanceError, match="NOT_NEW_EMPTY"):
        launch_authorized_shadow_with_provenance(
            authorization, ARTIFACT, consumption, attempt,
            observer_bootstrap=lambda _: _decision(), executor=lambda *_: None,
        )
    assert list(consumption.iterdir()) == []


def test_replay_rejects_altered_transition(tmp_path):
    authorization, _, consumption, attempt = _authorization(tmp_path)

    def observe(recorder):
        recorder.record_checkpoint_attempt(**_checkpoint_payload())
        raise RuntimeError("stop")

    with pytest.raises(ProductionShadowLauncherError):
        launch_authorized_shadow_with_provenance(
            authorization, ARTIFACT, consumption, attempt,
            observer_bootstrap=observe, executor=lambda *_: None,
        )
    ledger = attempt / "observer_attempt.jsonl"
    rows = ledger.read_text().splitlines()
    rows[1] = rows[1].replace("HEALTHY", "ALTERED", 1)
    ledger.write_text("\n".join(rows) + "\n")
    with pytest.raises(ProductionShadowObserverProvenanceError):
        verify_observer_attempt_bundle(attempt)
