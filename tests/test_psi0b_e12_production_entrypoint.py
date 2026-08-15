from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
import subprocess

import pytest

import scripts.run_psi0b_production_shadow as entrypoint
from src.evidence.contracts.production_shadow_fixture_runner import (
    build_fixture_runner_contract, execute_fixture_shadow, verify_fixture_shadow_bundle,
)
from src.evidence.contracts.production_shadow_health_gate import (
    HEALTHY, RUNNING, build_health_checkpoint,
    build_production_shadow_health_gate_contract, evaluate_active_health_gate,
    evaluate_prestart_health_gate,
)
from src.evidence.contracts.production_shadow_run_preflight import (
    build_immutable_cohort_artifact, build_production_shadow_run_preflight,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/run_psi0b_production_shadow.py"


def _checkpoint(at):
    return build_health_checkpoint(
        observed_at_epoch=at, listener_pid=1, listener_service_state=RUNNING,
        primary_fd_count=0, critical_listener_db_handle_count=0,
        serializer_p99_wait_ms=1.0, serializer_lock_errors=0,
        serializer_queue_depth=0, database_wal_state=HEALTHY,
        write_lease_state=HEALTHY, pumpportal_state=HEALTHY,
        pumpswap_state=HEALTHY, ingestion_state=HEALTHY, worker_state=HEALTHY,
        queue_state=HEALTHY, service_state=HEALTHY, telemetry_complete=True,
    )


def _health():
    contract = build_production_shadow_health_gate_contract()
    prestart = evaluate_prestart_health_gate(
        contract, (_checkpoint(40), _checkpoint(70), _checkpoint(100)),
        now_epoch=101, baseline_lock_errors=0,
    )
    active = evaluate_active_health_gate(
        contract, _checkpoint(70), _checkpoint(100), now_epoch=101,
        expected_listener_pid=1, baseline_lock_errors=0,
    )
    return prestart, active


class Observer:
    def __init__(self, prestart, active):
        self.prestart_decision = prestart
        self.active_decision = active
        self.active_queries = []

    def prestart(self, _recorder):
        return self.prestart_decision

    def active(self, query_id):
        self.active_queries.append(query_id)
        return self.active_decision


@dataclass
class Record:
    authorization_id: str
    authorization_digest: str
    output_directory: str


def _databases(root):
    paths = {name: root / f"{name}.sqlite" for name in ("creator", "evidence", "main", "ops")}
    db = sqlite3.connect(paths["creator"])
    db.executescript("CREATE TABLE creator_tokens(creator_address TEXT,mint TEXT,created_at INTEGER); CREATE INDEX ix_creator_mint ON creator_tokens(mint); INSERT INTO creator_tokens VALUES('creator','mint-a',1);")
    db.commit(); db.close()
    db = sqlite3.connect(paths["evidence"])
    db.executescript("CREATE TABLE normalized_evidence_records(fact_family TEXT,payload_json TEXT,raw_artifact_digest TEXT,acquired_at INTEGER,source_id TEXT,source_version TEXT,verification_state TEXT); CREATE INDEX ix_evidence_family ON normalized_evidence_records(fact_family); INSERT INTO normalized_evidence_records VALUES('LaunchFact','{}','d',1,'s','v','VERIFIED');")
    db.commit(); db.close()
    db = sqlite3.connect(paths["main"])
    db.executescript("CREATE TABLE token_analysis(mint TEXT,migrated_at INTEGER,first_observed_mc REAL,first_observed_price REAL,first_observed_at INTEGER,first_observed_source TEXT,first_observed_confidence REAL,pf_ws_creator TEXT,creator_mismatch INTEGER); CREATE INDEX ix_token_mint ON token_analysis(mint); CREATE TABLE token_price_snapshots(snapshot_id INTEGER,mint TEXT,price_usd REAL,market_cap REAL,source TEXT,captured_at INTEGER,created_at INTEGER); CREATE INDEX ix_snap_mint_time ON token_price_snapshots(mint,captured_at); INSERT INTO token_analysis VALUES('mint-a',2,10,1,2,'s',1,'creator',0); INSERT INTO token_price_snapshots VALUES(1,'mint-a',1,10,'s',2,2);")
    db.commit(); db.close()
    db = sqlite3.connect(paths["ops"])
    db.executescript("CREATE TABLE wt_watchtower_launches(mint TEXT,creator_wallet TEXT,create_signature TEXT,create_time INTEGER,create_slot INTEGER,creator_extraction_method TEXT,confidence TEXT,recorded_at INTEGER); CREATE INDEX ix_ops_mint ON wt_watchtower_launches(mint); INSERT INTO wt_watchtower_launches VALUES('mint-a','creator','sig',1,1,'fixture','HIGH',1);")
    db.commit(); db.close()
    return paths


def _fixture(tmp_path):
    root = tmp_path / "fixtures"; root.mkdir()
    paths = _databases(root)
    output = tmp_path / "output"
    cohort = build_immutable_cohort_artifact(
        cohort_id="fixture", mints=("mint-a", "mint-b"), source_artifact_digest="a" * 64,
    )
    preflight = build_production_shadow_run_preflight(
        run_id="fixture-run", cohort=cohort, fact_family="LaunchFact", output_directory=output,
    )
    return root, paths, output, preflight


def _wire(monkeypatch, tmp_path, observer, record, preflight):
    auth = tmp_path / "authorization.json"; auth.write_text("{}\n")
    committed = tmp_path / "preflight"; committed.mkdir()
    consumption = tmp_path / "consumption"; consumption.mkdir()
    attempt = tmp_path / "observer"; attempt.mkdir()
    audit = tmp_path / "attempt.json"
    monkeypatch.setattr(entrypoint, "load_execution_authorization", lambda _path: record)

    def launch(_auth, _preflight, consumption_dir, attempt_dir, *, observer_bootstrap, executor):
        decision = observer_bootstrap(None)
        (Path(consumption_dir) / f"{record.authorization_id}.consumed.json").write_text("{}\n")
        result = executor(record, preflight, decision)
        (Path(attempt_dir) / "observer_attempt.json").write_text("{}\n")
        return result

    monkeypatch.setattr(entrypoint, "launch_authorized_shadow_with_provenance", launch)
    return auth, committed, consumption, attempt, audit


def test_five_query_ephemeral_success_post_health_and_canonical_audit(tmp_path, monkeypatch):
    root, paths, output, fixture_preflight = _fixture(tmp_path)
    prestart, active = _health(); observer = Observer(prestart, active)
    record = Record("auth-e12", "1" * 64, str(output))
    auth, committed, consumption, attempt, audit = _wire(
        monkeypatch, tmp_path, observer, record, fixture_preflight,
    )

    def execute(_record, _preflight, **kwargs):
        return execute_fixture_shadow(
            build_fixture_runner_contract(), fixture_preflight, paths, output,
            prestart_health=kwargs["prestart_health"],
            active_health_check=kwargs["active_health_check"], fixture_root=root,
        )

    bundle = entrypoint.run_authorized_execution(
        authorization_path=auth, preflight_artifact=committed,
        consumption_directory=consumption, observer_attempt_directory=attempt,
        output_directory=output, attempt_audit_path=audit, observer=observer,
        execute_shadow=execute,
        verify_bundle=lambda path, _record: verify_fixture_shadow_bundle(path),
    )
    assert bundle.total_rows == 4
    assert observer.active_queries[-1] == "POST_RUN"
    assert len(observer.active_queries) == 6
    raw = audit.read_bytes(); values = json.loads(raw)
    assert raw == entrypoint._canonical(values)
    assert values["status"] == "PASS" and values["authorization_consumed"]


def test_prestart_failure_opens_no_source_and_audits(tmp_path, monkeypatch):
    _, _, output, preflight = _fixture(tmp_path)
    prestart, active = _health(); observer = Observer(prestart, active)
    record = Record("auth-stop", "2" * 64, str(output))
    auth, committed, consumption, attempt, audit = _wire(monkeypatch, tmp_path, observer, record, preflight)
    calls = []

    def stop(*_args, **_kwargs):
        raise RuntimeError("PRESTART_STOP")

    monkeypatch.setattr(entrypoint, "launch_authorized_shadow_with_provenance", stop)
    with pytest.raises(RuntimeError, match="PRESTART_STOP"):
        entrypoint.run_authorized_execution(
            authorization_path=auth, preflight_artifact=committed,
            consumption_directory=consumption, observer_attempt_directory=attempt,
            output_directory=output, attempt_audit_path=audit, observer=observer,
            execute_shadow=lambda *_args, **_kwargs: calls.append(True),
        )
    assert calls == [] and not output.exists() and list(consumption.iterdir()) == []
    assert json.loads(audit.read_text())["status"] == "FAILED"


def test_validation_exception_is_audited_without_consumption(tmp_path, monkeypatch):
    audit = tmp_path / "attempt.json"; consumption = tmp_path / "consumption"; consumption.mkdir()
    attempt = tmp_path / "observer"; attempt.mkdir(); output = tmp_path / "output"
    monkeypatch.setattr(entrypoint, "load_execution_authorization", lambda _path: (_ for _ in ()).throw(ValueError("malformed")))
    prestart, active = _health()
    with pytest.raises(ValueError, match="malformed"):
        entrypoint.run_authorized_execution(
            authorization_path=tmp_path / "bad.json", preflight_artifact=tmp_path / "preflight",
            consumption_directory=consumption, observer_attempt_directory=attempt,
            output_directory=output, attempt_audit_path=audit,
            observer=Observer(prestart, active),
        )
    values = json.loads(audit.read_text())
    assert values["authorization_id"] is None
    assert not values["authorization_consumed"] and not values["output_published"]


def test_active_stop_prevents_source_open_and_seals_failed_audit(tmp_path, monkeypatch):
    _, _, output, preflight = _fixture(tmp_path)
    prestart, active = _health()
    stopped = type(active)(**{**active.__dict__, "status": "STOP"})
    observer = Observer(prestart, stopped)
    record = Record("auth-active-stop", "3" * 64, str(output))
    auth, committed, consumption, attempt, audit = _wire(monkeypatch, tmp_path, observer, record, preflight)
    source_opens = []

    def execute(_record, _preflight, **kwargs):
        decision = kwargs["active_health_check"]("creator_selected_cohort")
        if decision.status != "PASS":
            raise RuntimeError("ACTIVE_STOP_BEFORE_SOURCE_OPEN")
        source_opens.append(True)

    with pytest.raises(RuntimeError, match="ACTIVE_STOP_BEFORE_SOURCE_OPEN"):
        entrypoint.run_authorized_execution(
            authorization_path=auth, preflight_artifact=committed,
            consumption_directory=consumption, observer_attempt_directory=attempt,
            output_directory=output, attempt_audit_path=audit, observer=observer,
            execute_shadow=execute,
        )
    assert source_opens == [] and not output.exists()
    values = json.loads(audit.read_text())
    assert values["status"] == "FAILED" and values["authorization_consumed"]


def test_executor_exception_audits_no_publication(tmp_path, monkeypatch):
    _, _, output, preflight = _fixture(tmp_path)
    prestart, active = _health(); observer = Observer(prestart, active)
    record = Record("auth-exception", "4" * 64, str(output))
    auth, committed, consumption, attempt, audit = _wire(monkeypatch, tmp_path, observer, record, preflight)
    with pytest.raises(OSError, match="fixture failure"):
        entrypoint.run_authorized_execution(
            authorization_path=auth, preflight_artifact=committed,
            consumption_directory=consumption, observer_attempt_directory=attempt,
            output_directory=output, attempt_audit_path=audit, observer=observer,
            execute_shadow=lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("fixture failure")),
        )
    values = json.loads(audit.read_text())
    assert values["status"] == "FAILED" and not values["output_published"]


def test_output_drift_and_reused_audit_fail_closed(tmp_path, monkeypatch):
    prestart, active = _health(); observer = Observer(prestart, active)
    expected = tmp_path / "expected"
    record = Record("auth-drift", "5" * 64, str(expected))
    auth = tmp_path / "authorization.json"; auth.write_text("{}\n")
    consumption = tmp_path / "consumption"; consumption.mkdir()
    attempt = tmp_path / "observer"; attempt.mkdir()
    audit = tmp_path / "attempt.json"
    monkeypatch.setattr(entrypoint, "load_execution_authorization", lambda _path: record)
    with pytest.raises(RuntimeError, match="OUTPUT_DIRECTORY_DRIFT"):
        entrypoint.run_authorized_execution(
            authorization_path=auth, preflight_artifact=tmp_path / "preflight",
            consumption_directory=consumption, observer_attempt_directory=attempt,
            output_directory=tmp_path / "altered", attempt_audit_path=audit, observer=observer,
        )
    with pytest.raises(FileExistsError):
        entrypoint.run_authorized_execution(
            authorization_path=auth, preflight_artifact=tmp_path / "preflight",
            consumption_directory=consumption, observer_attempt_directory=attempt,
            output_directory=tmp_path / "altered", attempt_audit_path=audit, observer=observer,
        )


def test_script_bootstraps_from_arbitrary_working_directory(tmp_path):
    assert SCRIPT.stat().st_mode & 0o777 == 0o755
    result = subprocess.run([str(SCRIPT), "--help"], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0
    assert "--execute" in result.stdout
