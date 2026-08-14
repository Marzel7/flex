import json
from pathlib import Path
import sqlite3

import pytest

from src.evidence.contracts.production_shadow_migration_tx_index_repair import (
    INDEX_NAME,
    build_migration_tx_index_repair_contract,
)
from src.evidence.contracts.production_shadow_migration_tx_index_runner import (
    FIXTURE_AUTHORIZATION,
    MigrationTxIndexRunnerError,
    inspect_migration_tx_attempt_ledger,
    run_fixture_migration_tx_index_attempt,
    verify_migration_tx_index_attempt_result,
)


def _contract():
    return build_migration_tx_index_repair_contract(engineering_revision="cfd97133")


def _fixture(tmp_path: Path, rows: int = 3) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "main.fixture.db"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE token_analysis(mint TEXT, migration_tx TEXT)")
    connection.executemany(
        "INSERT INTO token_analysis VALUES (?,?)",
        ((f"m{position}", f"s{position}") for position in range(rows)),
    )
    connection.commit(); connection.close()
    return path


def _run(tmp_path: Path, **overrides):
    fixture = overrides.pop("fixture_path", None)
    if fixture is None:
        fixture = _fixture(tmp_path)
    output = tmp_path / "output"; output.mkdir()
    values = dict(
        contract=_contract(), fixture_path=fixture, output_dir=output,
        run_id="d5-fixture-run", deadline_seconds=5,
        fixture_authorization=FIXTURE_AUTHORIZATION,
    )
    values.update(overrides)
    return run_fixture_migration_tx_index_attempt(**values), output


def test_success_records_fsynced_ordered_postcondition_and_replay(tmp_path):
    result, output = _run(tmp_path)
    assert result.status == "SUCCEEDED" and result.statements_executed == 1
    assert verify_migration_tx_index_attempt_result(result)
    inspection = inspect_migration_tx_attempt_ledger(output / "attempt.jsonl")
    assert inspection.event_names == (
        "STARTED", "SCHEMA_PRECONDITION_VERIFIED", "LOCK_ACQUIRED", "DDL_STARTED",
        "DDL_SUCCEEDED", "COMMIT_SUCCEEDED", "PROGRESS_HANDLER_REMOVED",
        "CONNECTION_CLOSED", "POSTCONDITION_VERIFIED", "TERMINAL",
    )
    post = json.loads((output / "attempt.jsonl").read_text().splitlines()[-2])
    assert post["data"]["rows_preserved"] and post["data"]["indexed_search"]


def test_already_compatible_is_zero_statement_noop(tmp_path):
    fixture = _fixture(tmp_path)
    connection = sqlite3.connect(fixture)
    connection.execute("CREATE INDEX compatible ON token_analysis(migration_tx)")
    connection.commit(); connection.close()
    result, _ = _run(tmp_path, fixture_path=fixture)
    assert result.status == "NOOP_COMPATIBLE" and result.statements_executed == 0
    assert verify_migration_tx_index_attempt_result(result)


def test_lock_failure_records_exception_cleanup_and_terminal(tmp_path):
    fixture = _fixture(tmp_path)
    holder = sqlite3.connect(fixture, isolation_level=None); holder.execute("BEGIN IMMEDIATE")
    try:
        result, output = _run(tmp_path, fixture_path=fixture)
    finally:
        holder.execute("ROLLBACK"); holder.close()
    assert result.status == "FAILED" and result.exception_type == "OperationalError"
    events = inspect_migration_tx_attempt_ledger(output / "attempt.jsonl").event_names
    assert "EXCEPTION" in events and "CONNECTION_CLOSED" in events


def test_injected_ddl_exception_rolls_back_removes_handler_and_closes(tmp_path):
    def fail(phase, _connection):
        if phase == "DDL_STARTED": raise RuntimeError("injected ddl failure")
    result, output = _run(tmp_path, fault_hook=fail)
    assert result.status == "FAILED"
    events = inspect_migration_tx_attempt_ledger(output / "attempt.jsonl").event_names
    assert "ROLLBACK_SUCCEEDED" in events
    assert events.index("PROGRESS_HANDLER_REMOVED") < events.index("CONNECTION_CLOSED")


def test_deadline_callback_interrupts_and_records_cleanup(tmp_path):
    class Clock:
        def __init__(self): self.armed = False
        def __call__(self): return 1.0 if self.armed else 0.0
    clock = Clock()
    def arm(phase, _connection):
        if phase == "DDL_STARTED": clock.armed = True
    result, output = _run(
        tmp_path, fixture_path=_fixture(tmp_path, rows=50000),
        deadline_seconds=0.5, clock=clock, fault_hook=arm,
    )
    assert result.status == "FAILED"
    events = inspect_migration_tx_attempt_ledger(output / "attempt.jsonl").event_names
    assert "DEADLINE_CALLBACK" in events
    assert "ROLLBACK_ALREADY_AUTOMATIC" in events


def test_schema_drift_conflict_and_authority_fail_closed(tmp_path):
    drift = tmp_path / "drift.db"
    connection = sqlite3.connect(drift)
    connection.execute("CREATE TABLE token_analysis(migration_tx INTEGER)")
    connection.commit(); connection.close()
    result, _ = _run(tmp_path, fixture_path=drift)
    assert result.status == "FAILED" and "TYPE_DRIFT" in result.exception_message

    other = tmp_path / "other"; other.mkdir()
    fixture = tmp_path / "conflict.db"
    connection = sqlite3.connect(fixture)
    connection.execute("CREATE TABLE token_analysis(mint TEXT, migration_tx TEXT)")
    connection.execute(f"CREATE INDEX {INDEX_NAME} ON token_analysis(mint)")
    connection.commit(); connection.close()
    result = run_fixture_migration_tx_index_attempt(
        contract=_contract(), fixture_path=fixture, output_dir=other,
        run_id="conflict", deadline_seconds=1,
        fixture_authorization=FIXTURE_AUTHORIZATION,
    )
    assert result.status == "FAILED" and "CONFLICTING" in result.exception_message


def test_incomplete_termination_tampering_and_output_reuse_fail_closed(tmp_path):
    output = tmp_path / "cutoff"; output.mkdir()
    record = {"sequence": 0, "event": "STARTED", "data": {"run_id": "cut-off"}}
    (output / "attempt.jsonl").write_text(
        json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
    )
    assert inspect_migration_tx_attempt_ledger(output / "attempt.jsonl").status == "INCOMPLETE_EXTERNAL_TERMINATION"

    result, complete = _run(tmp_path / "complete")
    path = complete / "attempt.jsonl"
    path.write_text(path.read_text().replace("SUCCEEDED", "ALTERED", 1))
    with pytest.raises(MigrationTxIndexRunnerError, match="DIGEST_MISMATCH"):
        verify_migration_tx_index_attempt_result(result)
    with pytest.raises(MigrationTxIndexRunnerError, match="NOT_NEW_EMPTY"):
        run_fixture_migration_tx_index_attempt(
            contract=_contract(), fixture_path=Path(result.ledger_path).parents[1] / "main.fixture.db",
            output_dir=complete, run_id="again", deadline_seconds=1,
            fixture_authorization=FIXTURE_AUTHORIZATION,
        )


@pytest.mark.parametrize("deadline", [0, -1, True])
def test_invalid_deadline_and_production_authorization_rejected(tmp_path, deadline):
    fixture = _fixture(tmp_path); output = tmp_path / "output"; output.mkdir()
    with pytest.raises(MigrationTxIndexRunnerError, match="INVALID_DEADLINE"):
        run_fixture_migration_tx_index_attempt(
            contract=_contract(), fixture_path=fixture, output_dir=output,
            run_id="bad", deadline_seconds=deadline,
            fixture_authorization=FIXTURE_AUTHORIZATION,
        )
    output2 = tmp_path / "output2"; output2.mkdir()
    with pytest.raises(MigrationTxIndexRunnerError, match="FIXTURE_AUTHORIZATION"):
        run_fixture_migration_tx_index_attempt(
            contract=_contract(), fixture_path=fixture, output_dir=output2,
            run_id="bad", deadline_seconds=1, fixture_authorization="PRODUCTION",
        )
