"""PSI0A-C13 durable, fixture-only single-index deployment runner."""

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
import time
from typing import Callable, Optional, Tuple

from .production_shadow_index_repair import (
    AdditiveIndexRepair,
    ProductionShadowIndexRepairContract,
    verify_production_shadow_index_repair_contract,
)


RUNNER_VERSION = "psi0a-c13.v1"
FIXTURE_AUTHORIZATION = "FROZEN_OR_EPHEMERAL_FIXTURE_ONLY"


class DurableIndexRunnerError(RuntimeError):
    pass


@dataclass(frozen=True)
class DurableIndexAttemptResult:
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
class DurableLedgerInspection:
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
            raise DurableIndexRunnerError("PSI0A_C13_OUTPUT_DIRECTORY_NOT_NEW_EMPTY")
        self.path = output_dir / "attempt.jsonl"
        self._events = []

    def append(self, event: str, **data: object) -> None:
        record = {"sequence": len(self._events), "event": event, "data": data}
        encoded = _canonical(record)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(encoded + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self._events.append(record)

    def terminal(self, status: str) -> str:
        self.append("TERMINAL", status=status, preterminal_digest=_digest(self._events))
        return _digest(self._events)


def _select_repair(
    contract: ProductionShadowIndexRepairContract, index_name: str
) -> AdditiveIndexRepair:
    matches = tuple(item for item in contract.repairs if item.index_name == index_name)
    if len(matches) != 1:
        raise DurableIndexRunnerError("PSI0A_C13_UNKNOWN_OR_AMBIGUOUS_INDEX")
    return matches[0]


def _index_columns(
    connection: sqlite3.Connection, relation_name: str
) -> dict[str, Tuple[str, ...]]:
    result = {}
    for row in connection.execute(f'PRAGMA index_list("{relation_name}")'):
        name = str(row[1])
        result[name] = tuple(
            str(item[2])
            for item in connection.execute(f'PRAGMA index_info("{name}")')
        )
    return result


def run_fixture_single_index_attempt(
    *,
    contract: ProductionShadowIndexRepairContract,
    index_name: str,
    fixture_path: Path,
    output_dir: Path,
    run_id: str,
    deadline_seconds: float,
    fixture_authorization: str,
    clock: Callable[[], float] = time.monotonic,
    fault_hook: Optional[Callable[[str, sqlite3.Connection], None]] = None,
) -> DurableIndexAttemptResult:
    verify_production_shadow_index_repair_contract(contract)
    if fixture_authorization != FIXTURE_AUTHORIZATION:
        raise DurableIndexRunnerError("PSI0A_C13_FIXTURE_AUTHORIZATION_REQUIRED")
    if not isinstance(run_id, str) or not run_id or len(run_id) > 128:
        raise DurableIndexRunnerError("PSI0A_C13_INVALID_RUN_ID")
    if not isinstance(deadline_seconds, (int, float)) or deadline_seconds <= 0:
        raise DurableIndexRunnerError("PSI0A_C13_INVALID_DEADLINE")
    fixture_path = Path(fixture_path)
    if not fixture_path.is_file():
        raise DurableIndexRunnerError("PSI0A_C13_FIXTURE_NOT_FOUND")
    repair = _select_repair(contract, index_name)
    ledger = _Ledger(Path(output_dir))
    ledger.append(
        "STARTED",
        runner_version=RUNNER_VERSION,
        run_id=run_id,
        contract_digest=contract.contract_digest,
        database_id=repair.database_id,
        relation_name=repair.relation_name,
        index_name=repair.index_name,
        index_columns=repair.index_columns,
        statement_digest=_digest(repair.statement),
        lock_timeout_ms=250,
        deadline_seconds=float(deadline_seconds),
        fixture_only=True,
        grants_extraction_authority=False,
        grants_activation_authority=False,
    )
    connection = None
    exception_type = None
    exception_message = None
    statements_executed = 0
    status = "FAILED"
    deadline_logged = False
    lock_acquired = False
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
            "SELECT type FROM sqlite_schema WHERE name=?", (repair.relation_name,)
        ).fetchone()
        columns = {
            str(row[1]): str(row[2]).upper()
            for row in connection.execute(f'PRAGMA table_info("{repair.relation_name}")')
        }
        if relation is None or str(relation[0]).upper() != "TABLE":
            raise DurableIndexRunnerError("PSI0A_C13_UNKNOWN_OR_INVALID_RELATION")
        if any(columns.get(name) != affinity for name, affinity in repair.required_columns):
            raise DurableIndexRunnerError("PSI0A_C13_COLUMN_OR_TYPE_DRIFT")
        indexes = _index_columns(connection, repair.relation_name)
        if repair.index_name in indexes and indexes[repair.index_name] != repair.index_columns:
            raise DurableIndexRunnerError("PSI0A_C13_CONFLICTING_INDEX_DEFINITION")
        ledger.append("SCHEMA_PRECONDITION_VERIFIED", schema_verified=True)
        satisfying = next(
            (name for name, cols in sorted(indexes.items()) if cols[: len(repair.index_columns)] == repair.index_columns),
            None,
        )
        if satisfying:
            ledger.append("ALREADY_COMPATIBLE_NOOP", satisfying_index=satisfying)
            status = "NOOP_COMPATIBLE"
        else:
            connection.execute("BEGIN IMMEDIATE")
            lock_acquired = True
            ledger.append("LOCK_ACQUIRED", transaction="BEGIN IMMEDIATE")
            ledger.append("DDL_STARTED", statement_digest=_digest(repair.statement))
            if fault_hook:
                fault_hook("DDL_STARTED", connection)
            connection.execute(repair.statement)
            statements_executed = 1
            ledger.append("DDL_SUCCEEDED", statements_executed=1)
            connection.execute("COMMIT")
            ledger.append("COMMIT_SUCCEEDED", committed=True)
            status = "SUCCEEDED"
    except BaseException as exc:  # ledger exact injected/runtime failure before cleanup
        exception_type = type(exc).__name__
        exception_message = str(exc)
        ledger.append(
            "EXCEPTION",
            exception_type=exception_type,
            exception_message=exception_message,
        )
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
        final_indexes = _index_columns(verify, repair.relation_name)
        present = any(
            cols[: len(repair.index_columns)] == repair.index_columns
            for cols in final_indexes.values()
        )
    finally:
        verify.close()
    expected_present = status in {"SUCCEEDED", "NOOP_COMPATIBLE"}
    if present == expected_present:
        ledger.append("POSTCONDITION_VERIFIED", index_present=present)
    else:
        ledger.append(
            "POSTCONDITION_FAILED",
            index_present=present,
            expected_present=expected_present,
        )
        status = "FAILED"
    ledger_digest = ledger.terminal(status)
    return DurableIndexAttemptResult(
        runner_version=RUNNER_VERSION,
        run_id=run_id,
        contract_digest=contract.contract_digest,
        index_name=repair.index_name,
        status=status,
        exception_type=exception_type,
        exception_message=exception_message,
        statements_executed=statements_executed,
        ledger_path=str(ledger.path),
        ledger_digest=ledger_digest,
        grants_extraction_authority=False,
        grants_activation_authority=False,
    )


def inspect_durable_attempt_ledger(ledger_path: Path) -> DurableLedgerInspection:
    path = Path(ledger_path)
    if not path.is_file():
        raise DurableIndexRunnerError("PSI0A_C13_LEDGER_NOT_FOUND")
    events = []
    for position, raw in enumerate(path.read_text(encoding="utf-8").splitlines()):
        record = json.loads(raw)
        if raw != _canonical(record) or record.get("sequence") != position:
            raise DurableIndexRunnerError("PSI0A_C13_LEDGER_REPLAY_MISMATCH")
        events.append(record)
    if not events or events[0].get("event") != "STARTED":
        raise DurableIndexRunnerError("PSI0A_C13_LEDGER_START_MISSING")
    digest = _digest(events)
    terminal = events[-1] if events[-1].get("event") == "TERMINAL" else None
    if terminal is None:
        return DurableLedgerInspection(
            status="INCOMPLETE_EXTERNAL_TERMINATION",
            event_names=tuple(item["event"] for item in events),
            terminal_status=None,
            ledger_digest=digest,
            replay_verified=True,
        )
    if terminal["data"].get("preterminal_digest") != _digest(events[:-1]):
        raise DurableIndexRunnerError("PSI0A_C13_TERMINAL_DIGEST_MISMATCH")
    return DurableLedgerInspection(
        status="TERMINAL",
        event_names=tuple(item["event"] for item in events),
        terminal_status=terminal["data"].get("status"),
        ledger_digest=digest,
        replay_verified=True,
    )


def verify_durable_index_attempt_result(result: DurableIndexAttemptResult) -> bool:
    inspection = inspect_durable_attempt_ledger(Path(result.ledger_path))
    if (
        inspection.status != "TERMINAL"
        or inspection.terminal_status != result.status
        or inspection.ledger_digest != result.ledger_digest
        or result.grants_extraction_authority
        or result.grants_activation_authority
    ):
        raise DurableIndexRunnerError("PSI0A_C13_RESULT_REPLAY_MISMATCH")
    return True
