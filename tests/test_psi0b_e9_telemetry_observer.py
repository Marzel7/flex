import json
from pathlib import Path

import pytest

from src.evidence.contracts.production_shadow_observer_provenance import (
    ObserverAttemptRecorder, verify_observer_attempt_bundle,
)
from src.evidence.contracts.production_shadow_telemetry_observer import (
    HEALTHY, ProductionShadowTelemetryObserverError, ProductionTelemetryObserver,
    SupervisorResult, TelemetryDependencies, parse_supervisor_status,
    production_telemetry_dependencies,
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


def _filesystem_metadata(*, wal_exists=True, database_type="REGULAR", wal_type="REGULAR"):
    rows = []
    for database_id, name, inode in (("main", "flex_complete_database.db", 101), ("ops", "wt_ops_v2.db", 201)):
        path = f"/qualified/{name}"
        rows.append({
            "database_id": database_id, "database_path": path,
            "database_exists": True, "database_type": database_type,
            "database_inode": inode, "database_size": 4096, "database_mtime_ns": 123,
            "wal_path": path + "-wal", "wal_exists": wal_exists,
            "wal_type": wal_type if wal_exists else "ABSENT",
            "wal_inode": inode + 1 if wal_exists else None,
            "wal_size": 0 if wal_exists else None,
            "wal_mtime_ns": 124 if wal_exists else None,
        })
    return tuple(rows)


def _deps(clock=None, **overrides):
    clock = clock or Clock()
    values = dict(
        supervisor_status=lambda: SupervisorResult(3, SUPERVISOR_OK),
        serializer_snapshot=lambda: {"_snapshot_at": clock(), "p99_wait_ms": 1.0, "lock_errors_24h": 0, "queue_depth": 0},
        primary_fd_count=lambda _pid: 0,
        critical_listener_count=lambda: 0,
        authoritative_write_lease_present=lambda: False,
        release_pending_metadata=lambda: (("old.tmp", 1, 2),),
        database_wal_metadata=lambda: _filesystem_metadata(),
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
    ({"database_wal_metadata": lambda: _filesystem_metadata(database_type="NONREGULAR")}, "DATABASE_FILE_INVALID"),
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


def test_pid_drift_fails_on_later_checkpoint(tmp_path):
    clock = Clock(); recorder, _ = _recorder(tmp_path)
    outputs = [SUPERVISOR_OK, SUPERVISOR_OK.replace("watchtower_listener RUNNING pid 30", "watchtower_listener RUNNING pid 31")]
    observer = ProductionTelemetryObserver(_deps(clock, supervisor_status=lambda: SupervisorResult(3, outputs.pop(0))))
    observer.checkpoint(recorder); clock.sleep(30)
    with pytest.raises(ProductionShadowTelemetryObserverError, match="PID_DRIFT"):
        observer.checkpoint(recorder)


def test_release_pending_creation_and_cleanup_are_recorded_but_not_gating(tmp_path):
    clock = Clock(); recorder, attempt = _recorder(tmp_path)
    components = [
        (("old.tmp", 1, 2),),
        (("old.tmp", 1, 2), ("new.tmp", 3, 4)),
        (("old.tmp", 1, 2),),
        (("old.tmp", 1, 2),),
    ]
    observer = ProductionTelemetryObserver(_deps(clock, release_pending_metadata=lambda: components.pop(0)))
    decision = observer.prestart(recorder)
    assert decision.status == "PASS"
    active = observer.active("creator_selected_cohort")
    assert active.status == "PASS"
    rows = [json.loads(line) for line in (attempt / "observer_attempt.jsonl").read_text().splitlines()]
    checkpoints = [row["payload"] for row in rows if row["event"] == "CHECKPOINT_ATTEMPT"]
    assert checkpoints[-1]["phase"] == "ACTIVE"
    assert checkpoints[-1]["query_id"] == "creator_selected_cohort"
    assert checkpoints[-1]["release_pending_metadata_components"] == [["old.tmp", 1, 2]]
    decisions = [row["payload"] for row in rows if row["event"] == "OBSERVER_DECISION"]
    assert decisions[-1]["phase"] == "ACTIVE"


def test_retained_orphan_baseline_does_not_block_prestart(tmp_path):
    recorder, _ = _recorder(tmp_path)
    observer = ProductionTelemetryObserver(_deps(release_pending_metadata=lambda: (("orphan.tmp", 1, 544),)))
    assert observer.prestart(recorder).status == "PASS"


def test_authoritative_lease_active_stop_records_named_decision(tmp_path):
    recorder, attempt = _recorder(tmp_path)
    lease = [False, False, False, True]
    observer = ProductionTelemetryObserver(_deps(authoritative_write_lease_present=lambda: lease.pop(0)))
    assert observer.prestart(recorder).status == "PASS"
    with pytest.raises(ProductionShadowTelemetryObserverError, match="AUTHORITATIVE_WRITE_LEASE_PRESENT"):
        observer.active("evidence_launch_facts")
    rows = [json.loads(line) for line in (attempt / "observer_attempt.jsonl").read_text().splitlines()]
    decision = [row["payload"] for row in rows if row["event"] == "OBSERVER_DECISION"][-1]
    assert decision["status"] == "STOP"
    assert decision["query_id"] == "evidence_launch_facts"
    assert decision["reason_codes"] == ["PSI0B_E9_AUTHORITATIVE_WRITE_LEASE_PRESENT"]


@pytest.mark.parametrize("malformed", (None, (("bad", 1, 2),), (("dup.tmp", 1, 2), ("dup.tmp", 3, 4))))
def test_malformed_release_pending_metadata_fails_closed_and_is_recorded(tmp_path, malformed):
    recorder, attempt = _recorder(tmp_path)
    observer = ProductionTelemetryObserver(_deps(release_pending_metadata=lambda: malformed))
    with pytest.raises(ProductionShadowTelemetryObserverError, match="METADATA_MALFORMED"):
        observer.checkpoint(recorder)
    rows = [json.loads(line) for line in (attempt / "observer_attempt.jsonl").read_text().splitlines()]
    checkpoint = next(row for row in rows if row["event"] == "CHECKPOINT_ATTEMPT")
    assert checkpoint["payload"]["gate_reason_code"] == "PSI0B_E11_RELEASE_PENDING_METADATA_MALFORMED"


def test_collection_exception_is_recorded_before_raise(tmp_path):
    recorder, attempt = _recorder(tmp_path)
    observer = ProductionTelemetryObserver(_deps(serializer_snapshot=lambda: (_ for _ in ()).throw(OSError("metrics"))))
    with pytest.raises(ProductionShadowTelemetryObserverError, match="COLLECTION_EXCEPTION"):
        observer.checkpoint(recorder)
    rows = [json.loads(line) for line in (attempt / "observer_attempt.jsonl").read_text().splitlines()]
    checkpoint = next(row for row in rows if row["event"] == "CHECKPOINT_ATTEMPT")
    assert checkpoint["payload"]["gate_reason_code"] == "PSI0B_E9_TELEMETRY_COLLECTION_EXCEPTION"


def test_database_wal_absent_create_remove_recreate_and_inode_rotation_are_healthy(tmp_path):
    database = tmp_path / "database"; database.mkdir()
    main = database / "flex_complete_database.db"
    ops = database / "wt_ops_v2.db"
    main.write_bytes(b"main"); ops.write_bytes(b"ops")
    dependencies = production_telemetry_dependencies(tmp_path)

    absent = dependencies.database_wal_metadata()
    assert all(row["wal_type"] == "ABSENT" for row in absent)
    main_wal = main.with_name(main.name + "-wal")
    main_wal.write_bytes(b"")
    created = dependencies.database_wal_metadata()
    first_inode = next(row for row in created if row["database_id"] == "main")["wal_inode"]
    main_wal.unlink()
    removed = dependencies.database_wal_metadata()
    main_wal.write_bytes(b"new")
    recreated = dependencies.database_wal_metadata()
    recreated_main = next(row for row in recreated if row["database_id"] == "main")

    for snapshot in (absent, created, removed, recreated):
        observer = ProductionTelemetryObserver(_deps(database_wal_metadata=lambda snapshot=snapshot: snapshot))
        assert observer.checkpoint().database_wal_state == HEALTHY
    assert recreated_main["wal_type"] == "REGULAR"
    assert recreated_main["wal_size"] == 3
    assert isinstance(first_inode, int) and isinstance(recreated_main["wal_inode"], int)


@pytest.mark.parametrize(("mutator", "reason"), (
    (lambda rows: ({**rows[0], "database_exists": False, "database_type": "ABSENT", "database_inode": None}, rows[1]), "DATABASE_FILE_INVALID"),
    (lambda rows: ({**rows[0], "database_type": "SYMLINK"}, rows[1]), "DATABASE_FILE_INVALID"),
    (lambda rows: ({**rows[0], "wal_type": "SYMLINK"}, rows[1]), "WAL_FILE_INVALID"),
    (lambda rows: ({**rows[0], "database_path": "/unknown/database.db", "wal_path": "/unknown/database.db-wal"}, rows[1]), "DATABASE_PATH_UNKNOWN"),
    (lambda rows: ({key: value for key, value in rows[0].items() if key != "wal_inode"}, rows[1]), "METADATA_MALFORMED"),
))
def test_database_wal_invalid_components_fail_closed(tmp_path, mutator, reason):
    rows = _filesystem_metadata()
    recorder, attempt = _recorder(tmp_path)
    observer = ProductionTelemetryObserver(_deps(database_wal_metadata=lambda: mutator(rows)))
    with pytest.raises(ProductionShadowTelemetryObserverError, match=reason):
        observer.checkpoint(recorder)
    checkpoint = next(
        json.loads(line)["payload"] for line in (attempt / "observer_attempt.jsonl").read_text().splitlines()
        if json.loads(line)["event"] == "CHECKPOINT_ATTEMPT"
    )
    assert reason in checkpoint["gate_reason_code"]


def test_database_wal_stat_exception_fails_closed(tmp_path, monkeypatch):
    database = tmp_path / "database"; database.mkdir()
    (database / "flex_complete_database.db").write_bytes(b"main")
    (database / "wt_ops_v2.db").write_bytes(b"ops")
    dependencies = production_telemetry_dependencies(tmp_path)
    monkeypatch.setattr(Path, "lstat", lambda _self: (_ for _ in ()).throw(PermissionError("stat denied")))
    with pytest.raises(PermissionError, match="stat denied"):
        dependencies.database_wal_metadata()
