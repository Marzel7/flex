import json
from pathlib import Path
import sqlite3

import pytest

from src.evidence.contracts.production_shadow_index_repair import (
    build_production_shadow_index_repair_contract,
)
from src.evidence.contracts.production_shadow_index_runner import (
    FIXTURE_AUTHORIZATION,
    DurableIndexRunnerError,
    inspect_durable_attempt_ledger,
    run_fixture_single_index_attempt,
    verify_durable_index_attempt_result,
)


INDEX = "idx_psi0a_token_analysis_migrated_mint"


def _contract():
    return build_production_shadow_index_repair_contract(engineering_revision="c1bae04a")


def _fixture(tmp_path: Path, rows: int = 1) -> Path:
    path = tmp_path / "main.fixture.db"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE token_analysis(mint TEXT PRIMARY KEY, migrated_at INTEGER)")
    connection.executemany(
        "INSERT INTO token_analysis VALUES (?,?)",
        ((f"m{position}", position) for position in range(rows)),
    )
    connection.commit()
    connection.close()
    return path


def _run(tmp_path: Path, **overrides):
    fixture = overrides.pop("fixture_path") if "fixture_path" in overrides else _fixture(tmp_path)
    output = tmp_path / "output"
    output.mkdir()
    values = dict(
        contract=_contract(),
        index_name=INDEX,
        fixture_path=fixture,
        output_dir=output,
        run_id="fixture-run-1",
        deadline_seconds=5,
        fixture_authorization=FIXTURE_AUTHORIZATION,
    )
    values.update(overrides)
    return run_fixture_single_index_attempt(**values), output


def test_success_records_durable_ordered_terminal_replay(tmp_path):
    result, output = _run(tmp_path)
    assert result.status == "SUCCEEDED"
    assert result.statements_executed == 1
    assert verify_durable_index_attempt_result(result)
    inspection = inspect_durable_attempt_ledger(output / "attempt.jsonl")
    assert inspection.event_names == (
        "STARTED", "SCHEMA_PRECONDITION_VERIFIED", "LOCK_ACQUIRED", "DDL_STARTED",
        "DDL_SUCCEEDED", "COMMIT_SUCCEEDED", "PROGRESS_HANDLER_REMOVED",
        "CONNECTION_CLOSED", "POSTCONDITION_VERIFIED", "TERMINAL",
    )


def test_already_compatible_is_zero_statement_noop(tmp_path):
    fixture = _fixture(tmp_path)
    connection = sqlite3.connect(fixture)
    connection.execute("CREATE INDEX compatible ON token_analysis(migrated_at,mint)")
    connection.commit(); connection.close()
    result, _ = _run(tmp_path, fixture_path=fixture)
    assert result.status == "NOOP_COMPATIBLE"
    assert result.statements_executed == 0
    assert verify_durable_index_attempt_result(result)


def test_lock_failure_records_exception_cleanup_and_terminal(tmp_path):
    fixture = _fixture(tmp_path)
    holder = sqlite3.connect(fixture, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    try:
        result, output = _run(tmp_path, fixture_path=fixture)
    finally:
        holder.execute("ROLLBACK"); holder.close()
    assert result.status == "FAILED"
    assert result.exception_type == "OperationalError"
    events = inspect_durable_attempt_ledger(output / "attempt.jsonl").event_names
    assert "EXCEPTION" in events and "CONNECTION_CLOSED" in events


def test_injected_ddl_exception_rolls_back_and_closes(tmp_path):
    def fail(phase, _connection):
        if phase == "DDL_STARTED":
            raise RuntimeError("injected ddl failure")
    result, output = _run(tmp_path, fault_hook=fail)
    assert result.status == "FAILED"
    events = inspect_durable_attempt_ledger(output / "attempt.jsonl").event_names
    assert "ROLLBACK_SUCCEEDED" in events
    assert events.index("PROGRESS_HANDLER_REMOVED") < events.index("CONNECTION_CLOSED")


def test_deadline_callback_interrupts_and_rolls_back(tmp_path):
    class Clock:
        def __init__(self): self.armed = False
        def __call__(self): return 1.0 if self.armed else 0.0
    clock = Clock()
    def arm_deadline(phase, _connection):
        if phase == "DDL_STARTED": clock.armed = True
    fixture = _fixture(tmp_path, rows=50000)
    result, output = _run(
        tmp_path, fixture_path=fixture, deadline_seconds=0.5, clock=clock,
        fault_hook=arm_deadline,
    )
    assert result.status == "FAILED"
    events = inspect_durable_attempt_ledger(output / "attempt.jsonl").event_names
    assert "DEADLINE_CALLBACK" in events
    assert "ROLLBACK_ALREADY_AUTOMATIC" in events


def test_incomplete_external_termination_is_detected(tmp_path):
    output = tmp_path / "output"; output.mkdir()
    record = {"sequence": 0, "event": "STARTED", "data": {"run_id": "cut-off"}}
    (output / "attempt.jsonl").write_text(
        json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
    )
    inspection = inspect_durable_attempt_ledger(output / "attempt.jsonl")
    assert inspection.status == "INCOMPLETE_EXTERNAL_TERMINATION"
    assert inspection.replay_verified


def test_tampering_output_reuse_and_production_authority_fail_closed(tmp_path):
    result, output = _run(tmp_path)
    path = output / "attempt.jsonl"
    path.write_text(path.read_text().replace("SUCCEEDED", "ALTERED", 1))
    with pytest.raises(DurableIndexRunnerError, match="DIGEST_MISMATCH"):
        verify_durable_index_attempt_result(result)
    with pytest.raises(DurableIndexRunnerError, match="NOT_NEW_EMPTY"):
        run_fixture_single_index_attempt(
            contract=_contract(), index_name=INDEX, fixture_path=Path(result.ledger_path).parents[1] / "main.fixture.db",
            output_dir=output, run_id="again", deadline_seconds=1,
            fixture_authorization=FIXTURE_AUTHORIZATION,
        )


def test_fixture_authorization_and_exact_contract_index_required(tmp_path):
    fixture = _fixture(tmp_path); output = tmp_path / "output"; output.mkdir()
    with pytest.raises(DurableIndexRunnerError, match="FIXTURE_AUTHORIZATION"):
        run_fixture_single_index_attempt(
            contract=_contract(), index_name=INDEX, fixture_path=fixture,
            output_dir=output, run_id="x", deadline_seconds=1,
            fixture_authorization="PRODUCTION",
        )
