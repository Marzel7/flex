"""PSI0A-D5 durable fixture-only runner for the PSI0A-D4 index contract."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
import time
from typing import Callable, Optional, Tuple

from .production_shadow_migration_tx_index_repair import (
    COLUMN_AFFINITY,
    COLUMN_NAME,
    INDEX_COLUMNS,
    INDEX_NAME,
    RECONCILER_QUERY,
    RELATION_NAME,
    MigrationTxIndexRepairContract,
    verify_migration_tx_index_repair_contract,
)


RUNNER_VERSION = "psi0a-d5.v1"
FIXTURE_AUTHORIZATION = "FROZEN_OR_EPHEMERAL_FIXTURE_ONLY"


class MigrationTxIndexRunnerError(RuntimeError):
    pass


@dataclass(frozen=True)
class MigrationTxIndexAttemptResult:
    runner_version: str
    run_id: str
    contract_digest: str
    index_name: str
    status: str
    exception_type: Optional[str]
    exception_message: Optional[str]
    statements_executed: int
    ledger_path: str
    ledger_digest: str
    grants_extraction_authority: bool
    grants_activation_authority: bool


@dataclass(frozen=True)
class MigrationTxLedgerInspection:
    status: str
    event_names: Tuple[str, ...]
    terminal_status: Optional[str]
    ledger_digest: str
    replay_verified: bool


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: object) -> str:
    return sha256(_canonical(value).encode()).hexdigest()


class _Ledger:
    def __init__(self, output_dir: Path):
        output_dir = Path(output_dir)
        if not output_dir.is_dir() or any(output_dir.iterdir()):
            raise MigrationTxIndexRunnerError("PSI0A_D5_OUTPUT_DIRECTORY_NOT_NEW_EMPTY")
        self.path = output_dir / "attempt.jsonl"
        self.events = []

    def append(self, event: str, **data: object) -> None:
        record = {"sequence": len(self.events), "event": event, "data": data}
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(_canonical(record) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self.events.append(record)

    def terminal(self, status: str) -> str:
        self.append("TERMINAL", status=status, preterminal_digest=_digest(self.events))
        return _digest(self.events)


def _index_columns(connection: sqlite3.Connection) -> dict[str, Tuple[str, ...]]:
    result = {}
    for row in connection.execute(f'PRAGMA index_list("{RELATION_NAME}")'):
        name = str(row[1])
        result[name] = tuple(
            str(item[2]) for item in connection.execute(f'PRAGMA index_info("{name}")')
        )
    return result


def _plan(connection: sqlite3.Connection) -> Tuple[str, ...]:
    return tuple(
        str(row[3])
        for row in connection.execute(
            "EXPLAIN QUERY PLAN " + RECONCILER_QUERY, ("a", "b", "c")
        )
    )


def run_fixture_migration_tx_index_attempt(
    *,
    contract: MigrationTxIndexRepairContract,
    fixture_path: Path,
    output_dir: Path,
    run_id: str,
    deadline_seconds: float,
    fixture_authorization: str,
    clock: Callable[[], float] = time.monotonic,
    fault_hook: Optional[Callable[[str, sqlite3.Connection], None]] = None,
) -> MigrationTxIndexAttemptResult:
    verify_migration_tx_index_repair_contract(contract)
    if contract.index_name != INDEX_NAME or contract.index_columns != INDEX_COLUMNS:
        raise MigrationTxIndexRunnerError("PSI0A_D5_NON_D4_INDEX_REJECTED")
    if fixture_authorization != FIXTURE_AUTHORIZATION:
        raise MigrationTxIndexRunnerError("PSI0A_D5_FIXTURE_AUTHORIZATION_REQUIRED")
    if not isinstance(run_id, str) or not run_id or len(run_id) > 128:
        raise MigrationTxIndexRunnerError("PSI0A_D5_INVALID_RUN_ID")
    if (
        isinstance(deadline_seconds, bool)
        or not isinstance(deadline_seconds, (int, float))
        or deadline_seconds <= 0
    ):
        raise MigrationTxIndexRunnerError("PSI0A_D5_INVALID_DEADLINE")
    fixture_path = Path(fixture_path)
    if not fixture_path.is_file():
        raise MigrationTxIndexRunnerError("PSI0A_D5_FIXTURE_NOT_FOUND")

    ledger = _Ledger(Path(output_dir))
    ledger.append(
        "STARTED",
        runner_version=RUNNER_VERSION,
        run_id=run_id,
        contract_digest=contract.contract_digest,
        database_id=contract.database_id,
        relation_name=contract.relation_name,
        index_name=contract.index_name,
        index_columns=contract.index_columns,
        statement_digest=_digest(contract.statement),
        lock_timeout_ms=250,
        deadline_seconds=float(deadline_seconds),
        fixture_only=True,
        grants_extraction_authority=False,
        grants_activation_authority=False,
    )
    connection = None
    status = "FAILED"
    exception_type = None
    exception_message = None
    statements_executed = 0
    deadline_logged = False
    lock_acquired = False
    row_count_before = None
    started = clock()
    try:
        connection = sqlite3.connect(
            str(fixture_path.resolve()), timeout=0.25, isolation_level=None
        )
        connection.execute("PRAGMA busy_timeout=250")

        def progress() -> int:
            nonlocal deadline_logged
            if clock() - started > float(deadline_seconds):
                if not deadline_logged:
                    ledger.append("DEADLINE_CALLBACK", exceeded=True)
                    deadline_logged = True
                return 1
            return 0

        connection.set_progress_handler(progress, 1000)
        relation = connection.execute(
            "SELECT type FROM sqlite_schema WHERE name=?", (RELATION_NAME,)
        ).fetchone()
        columns = {
            str(row[1]): str(row[2]).upper()
            for row in connection.execute(f'PRAGMA table_info("{RELATION_NAME}")')
        }
        if relation is None or str(relation[0]).upper() != "TABLE":
            raise MigrationTxIndexRunnerError("PSI0A_D5_UNKNOWN_OR_INVALID_RELATION")
        if columns.get(COLUMN_NAME) != COLUMN_AFFINITY:
            raise MigrationTxIndexRunnerError("PSI0A_D5_COLUMN_OR_TYPE_DRIFT")
        indexes = _index_columns(connection)
        if INDEX_NAME in indexes and indexes[INDEX_NAME] != INDEX_COLUMNS:
            raise MigrationTxIndexRunnerError("PSI0A_D5_CONFLICTING_INDEX_DEFINITION")
        row_count_before = int(
            connection.execute(f'SELECT COUNT(*) FROM "{RELATION_NAME}"').fetchone()[0]
        )
        ledger.append(
            "SCHEMA_PRECONDITION_VERIFIED",
            schema_verified=True,
            row_count_before=row_count_before,
        )
        satisfying = next(
            (name for name, cols in sorted(indexes.items()) if cols[:1] == INDEX_COLUMNS),
            None,
        )
        if satisfying:
            ledger.append("ALREADY_COMPATIBLE_NOOP", satisfying_index=satisfying)
            status = "NOOP_COMPATIBLE"
        else:
            connection.execute("BEGIN IMMEDIATE")
            lock_acquired = True
            ledger.append("LOCK_ACQUIRED", transaction="BEGIN IMMEDIATE")
            ledger.append("DDL_STARTED", statement_digest=_digest(contract.statement))
            if fault_hook:
                fault_hook("DDL_STARTED", connection)
            connection.execute(contract.statement)
            statements_executed = 1
            ledger.append("DDL_SUCCEEDED", statements_executed=1)
            connection.execute("COMMIT")
            ledger.append("COMMIT_SUCCEEDED", committed=True)
            status = "SUCCEEDED"
    except BaseException as exc:
        exception_type = type(exc).__name__
        exception_message = str(exc)
        ledger.append("EXCEPTION", exception_type=exception_type, exception_message=exception_message)
        if connection is not None and connection.in_transaction:
            try:
                connection.execute("ROLLBACK")
                ledger.append("ROLLBACK_SUCCEEDED", rolled_back=True)
            except BaseException as rollback_exc:
                ledger.append(
                    "ROLLBACK_FAILED",
                    exception_type=type(rollback_exc).__name__,
                    exception_message=str(rollback_exc),
                )
        elif lock_acquired:
            ledger.append("ROLLBACK_ALREADY_AUTOMATIC", rolled_back=True)
        else:
            ledger.append("ROLLBACK_NOT_REQUIRED", rolled_back=False)
        status = "FAILED"
    finally:
        if connection is not None:
            connection.set_progress_handler(None, 0)
            ledger.append("PROGRESS_HANDLER_REMOVED", removed=True)
            connection.close()
            ledger.append("CONNECTION_CLOSED", closed=True)

    verify = sqlite3.connect(str(fixture_path.resolve()), timeout=0.25)
    try:
        final_indexes = _index_columns(verify)
        satisfying = next(
            (name for name, cols in sorted(final_indexes.items()) if cols[:1] == INDEX_COLUMNS),
            None,
        )
        row_count_after = int(
            verify.execute(f'SELECT COUNT(*) FROM "{RELATION_NAME}"').fetchone()[0]
        )
        plan = _plan(verify) if satisfying else ()
        indexed = bool(
            satisfying
            and any("SEARCH token_analysis" in detail and satisfying in detail for detail in plan)
        )
    finally:
        verify.close()
    expected_present = status in {"SUCCEEDED", "NOOP_COMPATIBLE"}
    rows_preserved = row_count_before is not None and row_count_after == row_count_before
    if bool(satisfying) == expected_present and (not expected_present or (rows_preserved and indexed)):
        ledger.append(
            "POSTCONDITION_VERIFIED",
            index_present=bool(satisfying),
            row_count_after=row_count_after,
            rows_preserved=rows_preserved,
            reconciler_plan=plan,
            indexed_search=indexed,
        )
    else:
        ledger.append(
            "POSTCONDITION_FAILED",
            index_present=bool(satisfying),
            expected_present=expected_present,
            rows_preserved=rows_preserved,
            indexed_search=indexed,
        )
        status = "FAILED"
    ledger_digest = ledger.terminal(status)
    return MigrationTxIndexAttemptResult(
        runner_version=RUNNER_VERSION,
        run_id=run_id,
        contract_digest=contract.contract_digest,
        index_name=INDEX_NAME,
        status=status,
        exception_type=exception_type,
        exception_message=exception_message,
        statements_executed=statements_executed,
        ledger_path=str(ledger.path),
        ledger_digest=ledger_digest,
        grants_extraction_authority=False,
        grants_activation_authority=False,
    )


def inspect_migration_tx_attempt_ledger(ledger_path: Path) -> MigrationTxLedgerInspection:
    path = Path(ledger_path)
    if not path.is_file():
        raise MigrationTxIndexRunnerError("PSI0A_D5_LEDGER_NOT_FOUND")
    events = []
    for position, raw in enumerate(path.read_text(encoding="utf-8").splitlines()):
        record = json.loads(raw)
        if raw != _canonical(record) or record.get("sequence") != position:
            raise MigrationTxIndexRunnerError("PSI0A_D5_LEDGER_REPLAY_MISMATCH")
        events.append(record)
    if not events or events[0].get("event") != "STARTED":
        raise MigrationTxIndexRunnerError("PSI0A_D5_LEDGER_START_MISSING")
    digest = _digest(events)
    terminal = events[-1] if events[-1].get("event") == "TERMINAL" else None
    if terminal is None:
        return MigrationTxLedgerInspection(
            status="INCOMPLETE_EXTERNAL_TERMINATION",
            event_names=tuple(item["event"] for item in events),
            terminal_status=None,
            ledger_digest=digest,
            replay_verified=True,
        )
    if terminal["data"].get("preterminal_digest") != _digest(events[:-1]):
        raise MigrationTxIndexRunnerError("PSI0A_D5_TERMINAL_DIGEST_MISMATCH")
    return MigrationTxLedgerInspection(
        status="TERMINAL",
        event_names=tuple(item["event"] for item in events),
        terminal_status=terminal["data"].get("status"),
        ledger_digest=digest,
        replay_verified=True,
    )


def verify_migration_tx_index_attempt_result(result: MigrationTxIndexAttemptResult) -> bool:
    inspection = inspect_migration_tx_attempt_ledger(Path(result.ledger_path))
    if (
        inspection.status != "TERMINAL"
        or inspection.terminal_status != result.status
        or inspection.ledger_digest != result.ledger_digest
        or result.grants_extraction_authority
        or result.grants_activation_authority
    ):
        raise MigrationTxIndexRunnerError("PSI0A_D5_RESULT_REPLAY_MISMATCH")
    return True
