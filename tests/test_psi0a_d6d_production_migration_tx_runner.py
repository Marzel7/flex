from dataclasses import replace
import json
from pathlib import Path
import sqlite3

import pytest

from src.evidence.contracts.production_shadow_migration_tx_index_repair import INDEX_NAME
from src.evidence.contracts.production_shadow_migration_tx_production_runner import (
    ProductionMigrationTxIndexRunnerError,
    build_production_migration_tx_deployment_authorization,
    inspect_production_migration_tx_attempt_ledger,
    run_authorized_production_migration_tx_index_attempt,
    verify_production_migration_tx_deployment_authorization,
    verify_production_migration_tx_index_attempt_result,
)


def _database(tmp_path: Path, rows: int = 3) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "main.production-shape.db"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE token_analysis(mint TEXT, migration_tx TEXT)")
    connection.executemany(
        "INSERT INTO token_analysis VALUES (?,?)",
        ((f"m{position}", f"s{position}") for position in range(rows)),
    )
    connection.commit()
    connection.close()
    return path


def _authorization(database: Path, output: Path, **overrides):
    values = dict(
        authorization_id="psi0a-d6-separate-explicit-authorization",
        engineering_revision="ac06c781",
        run_id="psi0a-d6d-fixture-proof-run",
        production_database_path=database,
        output_directory=output,
        maximum_deadline_seconds=5,
    )
    values.update(overrides)
    return build_production_migration_tx_deployment_authorization(**values)


def _run(tmp_path: Path, **overrides):
    database = overrides.pop("database", None) or _database(tmp_path)
    output = overrides.pop("output", None)
    if output is None:
        output = tmp_path / "output"
        output.mkdir()
    authorization = overrides.pop("authorization", None) or _authorization(database, output)
    result = run_authorized_production_migration_tx_index_attempt(
        authorization=authorization,
        production_database_path=database,
        output_directory=output,
        **overrides,
    )
    return result, database, output, authorization


def test_authorization_binds_exact_production_boundary_and_replays(tmp_path):
    database = _database(tmp_path)
    output = tmp_path / "output"
    output.mkdir()
    authorization = _authorization(database, output)
    assert authorization.database_id == "main"
    assert authorization.relation_name == "token_analysis"
    assert authorization.required_column == ("migration_tx", "TEXT")
    assert authorization.index_name == INDEX_NAME
    assert authorization.maximum_statements == 1 and authorization.retry_count == 0
    assert authorization.allows_production_access and authorization.allows_exact_production_ddl
    assert not authorization.grants_extraction_authority
    assert not authorization.grants_activation_authority
    assert verify_production_migration_tx_deployment_authorization(
        authorization,
        production_database_path=database,
        output_directory=output,
    )


def test_success_is_one_statement_durable_row_preserving_and_replayable(tmp_path):
    result, database, output, _ = _run(tmp_path)
    assert result.status == "SUCCEEDED" and result.statements_executed == 1
    assert verify_production_migration_tx_index_attempt_result(result)
    inspection = inspect_production_migration_tx_attempt_ledger(output / "attempt.jsonl")
    assert inspection.event_names == (
        "STARTED", "SCHEMA_PRECONDITION_VERIFIED", "LOCK_ACQUIRED", "DDL_STARTED",
        "DDL_SUCCEEDED", "COMMIT_SUCCEEDED", "PROGRESS_HANDLER_REMOVED",
        "CONNECTION_CLOSED", "POSTCONDITION_PROGRESS_HANDLER_REMOVED",
        "POSTCONDITION_CONNECTION_CLOSED", "POSTCONDITION_VERIFIED", "TERMINAL",
    )
    postcondition = json.loads((output / "attempt.jsonl").read_text().splitlines()[-2])
    assert postcondition["data"]["rows_preserved"]
    assert postcondition["data"]["indexed_search"]
    connection = sqlite3.connect(database)
    assert connection.execute("SELECT COUNT(*) FROM token_analysis").fetchone()[0] == 3
    connection.close()


def test_already_compatible_is_zero_statement_noop(tmp_path):
    database = _database(tmp_path)
    connection = sqlite3.connect(database)
    connection.execute("CREATE INDEX existing_compatible ON token_analysis(migration_tx)")
    connection.commit()
    connection.close()
    result, _, _, _ = _run(tmp_path, database=database)
    assert result.status == "NOOP_COMPATIBLE" and result.statements_executed == 0
    assert verify_production_migration_tx_index_attempt_result(result)


def test_lock_failure_and_injected_exception_are_terminal_and_clean(tmp_path):
    database = _database(tmp_path / "locked")
    holder = sqlite3.connect(database, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    try:
        result, _, output, _ = _run(tmp_path / "locked", database=database)
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    assert result.status == "FAILED" and result.exception_type == "OperationalError"
    events = inspect_production_migration_tx_attempt_ledger(output / "attempt.jsonl").event_names
    assert "EXCEPTION" in events and "CONNECTION_CLOSED" in events

    def fail(phase, _connection):
        if phase == "DDL_STARTED":
            raise RuntimeError("injected ddl failure")

    result, _, output, _ = _run(tmp_path / "exception", fault_hook=fail)
    assert result.status == "FAILED"
    events = inspect_production_migration_tx_attempt_ledger(output / "attempt.jsonl").event_names
    assert "ROLLBACK_SUCCEEDED" in events
    assert events.index("PROGRESS_HANDLER_REMOVED") < events.index("CONNECTION_CLOSED")


def test_deadline_interrupts_and_records_cleanup(tmp_path):
    class Clock:
        def __init__(self):
            self.armed = False

        def __call__(self):
            return 1.0 if self.armed else 0.0

    clock = Clock()

    def arm(phase, _connection):
        if phase == "DDL_STARTED":
            clock.armed = True

    database = _database(tmp_path, rows=50000)
    output = tmp_path / "output"
    output.mkdir()
    authorization = _authorization(database, output, maximum_deadline_seconds=0.5)
    result, _, output, _ = _run(
        tmp_path,
        database=database,
        output=output,
        authorization=authorization,
        clock=clock,
        fault_hook=arm,
    )
    assert result.status == "FAILED"
    events = inspect_production_migration_tx_attempt_ledger(output / "attempt.jsonl").event_names
    assert "DEADLINE_CALLBACK" in events
    assert "PROGRESS_HANDLER_REMOVED" in events and "CONNECTION_CLOSED" in events


def test_schema_conflict_path_and_authority_drift_fail_closed(tmp_path):
    drift = tmp_path / "drift.db"
    connection = sqlite3.connect(drift)
    connection.execute("CREATE TABLE token_analysis(migration_tx INTEGER)")
    connection.commit()
    connection.close()
    output = tmp_path / "drift-output"
    output.mkdir()
    result, _, _, _ = _run(
        tmp_path, database=drift, output=output, authorization=_authorization(drift, output)
    )
    assert result.status == "FAILED" and "TYPE_DRIFT" in result.exception_message

    conflict = tmp_path / "conflict.db"
    connection = sqlite3.connect(conflict)
    connection.execute("CREATE TABLE token_analysis(mint TEXT, migration_tx TEXT)")
    connection.execute(f"CREATE INDEX {INDEX_NAME} ON token_analysis(mint)")
    connection.commit()
    connection.close()
    conflict_output = tmp_path / "conflict-output"
    conflict_output.mkdir()
    result, _, _, _ = _run(
        tmp_path,
        database=conflict,
        output=conflict_output,
        authorization=_authorization(conflict, conflict_output),
    )
    assert result.status == "FAILED" and "CONFLICTING" in result.exception_message

    database = _database(tmp_path / "binding")
    bound_output = tmp_path / "bound-output"
    bound_output.mkdir()
    authorization = _authorization(database, bound_output)
    wrong_output = tmp_path / "wrong-output"
    wrong_output.mkdir()
    with pytest.raises(ProductionMigrationTxIndexRunnerError, match="REPLAY_MISMATCH"):
        run_authorized_production_migration_tx_index_attempt(
            authorization=authorization,
            production_database_path=database,
            output_directory=wrong_output,
        )
    with pytest.raises(ProductionMigrationTxIndexRunnerError, match="REPLAY_MISMATCH"):
        verify_production_migration_tx_deployment_authorization(
            replace(authorization, statement=authorization.statement + "; VACUUM"),
            production_database_path=database,
            output_directory=bound_output,
        )


def test_fixture_tokens_deadline_reuse_incomplete_and_tamper_fail_closed(tmp_path):
    database = _database(tmp_path)
    output = tmp_path / "output"
    output.mkdir()
    with pytest.raises(ProductionMigrationTxIndexRunnerError, match="FIXTURE_AUTHORIZATION"):
        _authorization(database, output, authorization_id="FROZEN_OR_EPHEMERAL_FIXTURE_ONLY")
    for deadline in (0, -1, True, 60.1):
        with pytest.raises(ProductionMigrationTxIndexRunnerError, match="INVALID_DEADLINE"):
            _authorization(database, output, maximum_deadline_seconds=deadline)

    result, _, complete, authorization = _run(tmp_path / "complete")
    with pytest.raises(ProductionMigrationTxIndexRunnerError, match="NOT_NEW_EMPTY"):
        run_authorized_production_migration_tx_index_attempt(
            authorization=authorization,
            production_database_path=Path(result.ledger_path).parents[1] / "main.production-shape.db",
            output_directory=complete,
        )
    path = complete / "attempt.jsonl"
    path.write_text(path.read_text().replace("SUCCEEDED", "ALTERED", 1))
    with pytest.raises(ProductionMigrationTxIndexRunnerError, match="DIGEST_MISMATCH"):
        verify_production_migration_tx_index_attempt_result(result)

    cutoff = tmp_path / "cutoff"
    cutoff.mkdir()
    record = {"sequence": 0, "event": "STARTED", "data": {"run_id": "cut-off"}}
    (cutoff / "attempt.jsonl").write_text(
        json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
    )
    assert (
        inspect_production_migration_tx_attempt_ledger(cutoff / "attempt.jsonl").status
        == "INCOMPLETE_EXTERNAL_TERMINATION"
    )
