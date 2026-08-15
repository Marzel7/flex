from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys

import pytest

from src.evidence.contracts.production_shadow_health_gate import (
    HEALTHY,
    RUNNING,
    build_health_checkpoint,
    build_production_shadow_health_gate_contract,
    evaluate_prestart_health_gate,
)
from src.evidence.contracts.production_shadow_launcher import (
    ProductionShadowLauncherError,
    launch_authorized_shadow,
    launcher_contract_digest,
    load_execution_authorization,
    validate_bootstrap_inputs,
)
from src.evidence.contracts.production_shadow_production_binding import (
    build_production_execution_authorization,
    production_binding_contract_digest,
)
from src.evidence.contracts.production_shadow_superseding_preflight import SHADOW_OUTPUT


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "docs/audits/psi0b_e3_superseding_preflight"
SCRIPT = ROOT / "scripts/run_psi0b_production_shadow.py"


def _authorization(tmp_path, *, authorization_id="psi0b-e7-test-authorization"):
    record = build_production_execution_authorization(
        authorization_id=authorization_id,
        run_id="psi0b-shadow-20260814-02",
        output_directory=SHADOW_OUTPUT,
    )
    document = {
        "schema_version": "psi0b-e.authorization.v1",
        "engineering_commit": "a" * 40,
        "production_binding_contract_digest": production_binding_contract_digest(),
        "authorization": asdict(record),
    }
    path = tmp_path / "authorization.json"
    path.write_text(json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n")
    return path, record


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
            queue_state=HEALTHY, service_state=HEALTHY,
            telemetry_complete=True,
        )
        for position in range(3)
    )
    return evaluate_prestart_health_gate(contract, checkpoints, now_epoch=60.0, baseline_lock_errors=0)


def _pass_decision():
    return _decision()


def test_bootstrap_validation_replays_committed_inputs(tmp_path):
    authorization, record = _authorization(tmp_path)
    consumption = tmp_path / "consumption"; consumption.mkdir()
    loaded, preflight, marker = validate_bootstrap_inputs(authorization, ARTIFACT, consumption)
    assert loaded == record
    assert preflight.preflight_digest == record.bound_preflight_digest
    assert marker.name == f"{record.authorization_id}.consumed.json"
    assert not marker.exists()
    assert len(launcher_contract_digest()) == 64


def test_script_imports_from_outside_repository_working_directory(tmp_path):
    authorization, record = _authorization(tmp_path)
    consumption = tmp_path / "consumption"; consumption.mkdir()
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--authorization", str(authorization),
         "--preflight-artifact", str(ARTIFACT), "--consumption-directory", str(consumption),
         "--bootstrap-check"],
        cwd=tmp_path, text=True, capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert "BOOTSTRAP_PASS" in result.stdout
    assert record.authorization_id in result.stdout
    assert not any(consumption.iterdir())


def test_authorization_consumed_once_only_after_passing_observer(tmp_path):
    authorization, record = _authorization(tmp_path)
    consumption = tmp_path / "consumption"; consumption.mkdir()
    calls = []
    result = launch_authorized_shadow(
        authorization, ARTIFACT, consumption,
        observer_bootstrap=_pass_decision,
        executor=lambda loaded, preflight, decision: calls.append((loaded, preflight, decision)) or "EXECUTED",
    )
    assert result == "EXECUTED"
    assert len(calls) == 1
    marker = consumption / f"{record.authorization_id}.consumed.json"
    assert marker.is_file()
    with pytest.raises(ProductionShadowLauncherError, match="ALREADY_CONSUMED"):
        launch_authorized_shadow(
            authorization, ARTIFACT, consumption,
            observer_bootstrap=_pass_decision, executor=lambda *_: None,
        )


def test_observer_failure_consumes_nothing_and_executes_nothing(tmp_path):
    authorization, _ = _authorization(tmp_path)
    consumption = tmp_path / "consumption"; consumption.mkdir()
    calls = []
    with pytest.raises(ProductionShadowLauncherError, match="OBSERVER_BOOTSTRAP_FAILED"):
        launch_authorized_shadow(
            authorization, ARTIFACT, consumption,
            observer_bootstrap=lambda: (_ for _ in ()).throw(RuntimeError("observer failed")),
            executor=lambda *_: calls.append(True),
        )
    assert calls == []
    assert list(consumption.iterdir()) == []
    assert not SHADOW_OUTPUT.exists()


def test_do_not_start_decision_consumes_nothing(tmp_path):
    authorization, _ = _authorization(tmp_path)
    consumption = tmp_path / "consumption"; consumption.mkdir()
    decision = _decision(primary_fd_count=8)
    with pytest.raises(ProductionShadowLauncherError, match="PRESTART_DO_NOT_START"):
        launch_authorized_shadow(
            authorization, ARTIFACT, consumption,
            observer_bootstrap=lambda: decision, executor=lambda *_: None,
        )
    assert list(consumption.iterdir()) == []


@pytest.mark.parametrize("mutation,reason", (
    ("missing", "JSON_INVALID"),
    ("malformed", "JSON_INVALID"),
    ("extra", "DOCUMENT_SHAPE_DRIFT"),
    ("contract", "BINDING_CONTRACT_DRIFT"),
    ("authority", "AUTHORIZATION_REPLAY_FAILED"),
))
def test_authorization_faults_fail_before_consumption(tmp_path, mutation, reason):
    authorization, _ = _authorization(tmp_path)
    if mutation == "missing":
        authorization.unlink()
    elif mutation == "malformed":
        authorization.write_text("{")
    else:
        document = json.loads(authorization.read_text())
        if mutation == "extra":
            document["extra"] = True
        elif mutation == "contract":
            document["production_binding_contract_digest"] = "0" * 64
        else:
            document["authorization"]["grants_activation_authority"] = True
        authorization.write_text(json.dumps(document))
    with pytest.raises(ProductionShadowLauncherError, match=reason):
        load_execution_authorization(authorization)


def test_missing_preflight_nonempty_output_and_missing_ledger_fail_closed(tmp_path):
    authorization, _ = _authorization(tmp_path)
    missing = tmp_path / "missing"
    consumption = tmp_path / "consumption"; consumption.mkdir()
    with pytest.raises(ProductionShadowLauncherError, match="PREFLIGHT_REPLAY_FAILED"):
        validate_bootstrap_inputs(authorization, missing, consumption)
    with pytest.raises(ProductionShadowLauncherError, match="CONSUMPTION_DIRECTORY_MISSING"):
        validate_bootstrap_inputs(authorization, ARTIFACT, missing)
    with pytest.raises(ProductionShadowLauncherError, match="OUTPUT_NOT_NEW"):
        validate_bootstrap_inputs(
            authorization, ARTIFACT, consumption,
            path_exists=lambda path: path == SHADOW_OUTPUT,
        )
