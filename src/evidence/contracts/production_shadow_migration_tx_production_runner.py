"""PSI0A-D6D production-specific durable runner for the D4 index statement."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from typing import Callable, Optional, Tuple

from .production_shadow_migration_tx_index_repair import (
    COLUMN_AFFINITY,
    COLUMN_NAME,
    INDEX_COLUMNS,
    INDEX_NAME,
    INDEX_STATEMENT,
    RECONCILER_QUERY,
    RELATION_NAME,
)


RUNNER_VERSION = "psi0a-d6d.v1"
AUTHORIZATION_VERSION = "psi0a-d6-deployment-authorization.v1"
AUTHORIZATION_CLASS = "EXACTLY_ONE_PRODUCTION_MIGRATION_TX_INDEX_ATTEMPT"
AUTHORIZED_MILESTONE = "PSI0A-D6"
EXPECTED_D4_CONTRACT_DIGEST = (
    "a160398c3130dbacf2314bb4046b9bac4865cd9dd421311f2fd999058c0ab9f2"
)
DATABASE_ID = "main"
MAX_DEADLINE_SECONDS = 60.0
LOCK_TIMEOUT_MS = 250
_REVISION = re.compile(r"^[0-9a-f]{7,64}$")
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class ProductionMigrationTxIndexRunnerError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProductionMigrationTxDeploymentAuthorization:
    authorization_version: str
    authorization_class: str
    authorized_milestone: str
    authorization_id: str
    engineering_revision: str
    d4_contract_digest: str
    database_id: str
    relation_name: str
    required_column: Tuple[str, str]
    index_name: str
    index_columns: Tuple[str, ...]
    statement: str
    reconciler_query: str
    run_id: str
    production_database_path_fingerprint: str
    output_directory_fingerprint: str
    lock_timeout_ms: int
    maximum_deadline_seconds: float
    maximum_statements: int
    retry_count: int
    allows_production_access: bool
    allows_exact_production_ddl: bool
    grants_extraction_authority: bool
    grants_activation_authority: bool
    authorization_digest: str


@dataclass(frozen=True)
class ProductionMigrationTxIndexAttemptResult:
    runner_version: str
    authorization_digest: str
    run_id: str
    d4_contract_digest: str
    database_path_fingerprint: str
    output_directory_fingerprint: str
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
class ProductionMigrationTxLedgerInspection:
    status: str
    event_names: Tuple[str, ...]
    terminal_status: Optional[str]
    ledger_digest: str
    replay_verified: bool


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: object) -> str:
    return sha256(_canonical(value).encode()).hexdigest()


def path_fingerprint(path: Path) -> str:
    return sha256(str(Path(path).resolve()).encode()).hexdigest()


def build_production_migration_tx_deployment_authorization(
    *,
    authorization_id: str,
    engineering_revision: str,
    run_id: str,
    production_database_path: Path,
    output_directory: Path,
    maximum_deadline_seconds: float,
) -> ProductionMigrationTxDeploymentAuthorization:
    if not isinstance(authorization_id, str) or not _RUN_ID.fullmatch(authorization_id):
        raise ProductionMigrationTxIndexRunnerError("PSI0A_D6D_INVALID_AUTHORIZATION_ID")
    if "FIXTURE" in authorization_id.upper():
        raise ProductionMigrationTxIndexRunnerError(
            "PSI0A_D6D_FIXTURE_AUTHORIZATION_REJECTED"
        )
    if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
        raise ProductionMigrationTxIndexRunnerError("PSI0A_D6D_INVALID_RUN_ID")
    if not isinstance(engineering_revision, str) or not _REVISION.fullmatch(engineering_revision):
        raise ProductionMigrationTxIndexRunnerError("PSI0A_D6D_INVALID_ENGINEERING_REVISION")
    if (
        isinstance(maximum_deadline_seconds, bool)
        or not isinstance(maximum_deadline_seconds, (int, float))
        or maximum_deadline_seconds <= 0
        or maximum_deadline_seconds > MAX_DEADLINE_SECONDS
    ):
        raise ProductionMigrationTxIndexRunnerError("PSI0A_D6D_INVALID_DEADLINE")
    body = {
        "authorization_version": AUTHORIZATION_VERSION,
        "authorization_class": AUTHORIZATION_CLASS,
        "authorized_milestone": AUTHORIZED_MILESTONE,
        "authorization_id": authorization_id,
        "engineering_revision": engineering_revision,
        "d4_contract_digest": EXPECTED_D4_CONTRACT_DIGEST,
        "database_id": DATABASE_ID,
        "relation_name": RELATION_NAME,
        "required_column": (COLUMN_NAME, COLUMN_AFFINITY),
        "index_name": INDEX_NAME,
        "index_columns": INDEX_COLUMNS,
        "statement": INDEX_STATEMENT,
        "reconciler_query": RECONCILER_QUERY,
        "run_id": run_id,
        "production_database_path_fingerprint": path_fingerprint(production_database_path),
        "output_directory_fingerprint": path_fingerprint(output_directory),
        "lock_timeout_ms": LOCK_TIMEOUT_MS,
        "maximum_deadline_seconds": float(maximum_deadline_seconds),
        "maximum_statements": 1,
        "retry_count": 0,
        "allows_production_access": True,
        "allows_exact_production_ddl": True,
        "grants_extraction_authority": False,
        "grants_activation_authority": False,
    }
    return ProductionMigrationTxDeploymentAuthorization(
        **body, authorization_digest=_digest(body)
    )


def verify_production_migration_tx_deployment_authorization(
    authorization: ProductionMigrationTxDeploymentAuthorization,
    *,
    production_database_path: Path,
    output_directory: Path,
) -> bool:
    expected = build_production_migration_tx_deployment_authorization(
        authorization_id=authorization.authorization_id,
        engineering_revision=authorization.engineering_revision,
        run_id=authorization.run_id,
        production_database_path=production_database_path,
        output_directory=output_directory,
        maximum_deadline_seconds=authorization.maximum_deadline_seconds,
    )
    if authorization != expected:
        raise ProductionMigrationTxIndexRunnerError("PSI0A_D6D_AUTHORIZATION_REPLAY_MISMATCH")
    if (
        authorization.d4_contract_digest != EXPECTED_D4_CONTRACT_DIGEST
        or authorization.statement != INDEX_STATEMENT
        or authorization.maximum_statements != 1
        or authorization.retry_count != 0
        or not authorization.allows_production_access
        or not authorization.allows_exact_production_ddl
        or authorization.grants_extraction_authority
        or authorization.grants_activation_authority
    ):
        raise ProductionMigrationTxIndexRunnerError("PSI0A_D6D_AUTHORITY_DRIFT")
    return True


class _Ledger:
    def __init__(self, output_directory: Path):
        output_directory = Path(output_directory)
        if not output_directory.is_dir() or any(output_directory.iterdir()):
            raise ProductionMigrationTxIndexRunnerError("PSI0A_D6D_OUTPUT_DIRECTORY_NOT_NEW_EMPTY")
        self.path = output_directory / "attempt.jsonl"
        self.events: list[dict] = []

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


def _reconciler_plan(connection: sqlite3.Connection) -> Tuple[str, ...]:
    return tuple(
        str(row[3])
        for row in connection.execute(
            "EXPLAIN QUERY PLAN " + RECONCILER_QUERY, ("a", "b", "c")
        )
    )


def _install_deadline(
    connection: sqlite3.Connection,
    *,
    started: float,
    deadline_seconds: float,
    clock: Callable[[], float],
    on_deadline: Callable[[], None],
) -> None:
    def progress() -> int:
        if clock() - started > deadline_seconds:
            on_deadline()
            return 1
        return 0

    connection.set_progress_handler(progress, 1000)


def run_authorized_production_migration_tx_index_attempt(
    *,
    authorization: ProductionMigrationTxDeploymentAuthorization,
    production_database_path: Path,
    output_directory: Path,
    clock: Callable[[], float] = time.monotonic,
    fault_hook: Optional[Callable[[str, sqlite3.Connection], None]] = None,
) -> ProductionMigrationTxIndexAttemptResult:
    database_path = Path(production_database_path)
    output_directory = Path(output_directory)
    verify_production_migration_tx_deployment_authorization(
        authorization,
        production_database_path=database_path,
        output_directory=output_directory,
    )
    if not database_path.is_file():
        raise ProductionMigrationTxIndexRunnerError("PSI0A_D6D_DATABASE_NOT_FOUND")
    ledger = _Ledger(output_directory)
    ledger.append(
        "STARTED",
        runner_version=RUNNER_VERSION,
        authorization_digest=authorization.authorization_digest,
        authorization_id=authorization.authorization_id,
        run_id=authorization.run_id,
        d4_contract_digest=authorization.d4_contract_digest,
        database_id=authorization.database_id,
        database_path_fingerprint=authorization.production_database_path_fingerprint,
        output_directory_fingerprint=authorization.output_directory_fingerprint,
        relation_name=authorization.relation_name,
        index_name=authorization.index_name,
        statement_digest=_digest(authorization.statement),
        lock_timeout_ms=authorization.lock_timeout_ms,
        deadline_seconds=authorization.maximum_deadline_seconds,
        maximum_statements=1,
        retry_count=0,
        grants_extraction_authority=False,
        grants_activation_authority=False,
    )
    connection: Optional[sqlite3.Connection] = None
    status = "FAILED"
    exception_type: Optional[str] = None
    exception_message: Optional[str] = None
    statements_executed = 0
    row_count_before: Optional[int] = None
    deadline_logged = False
    lock_acquired = False
    started = clock()

    def log_deadline() -> None:
        nonlocal deadline_logged
        if not deadline_logged:
            ledger.append("DEADLINE_CALLBACK", exceeded=True)
            deadline_logged = True

    try:
        connection = sqlite3.connect(
            str(database_path.resolve()), timeout=0.25, isolation_level=None
        )
        connection.execute(f"PRAGMA busy_timeout={LOCK_TIMEOUT_MS}")
        _install_deadline(
            connection,
            started=started,
            deadline_seconds=authorization.maximum_deadline_seconds,
            clock=clock,
            on_deadline=log_deadline,
        )
        relation = connection.execute(
            "SELECT type FROM sqlite_schema WHERE name=?", (RELATION_NAME,)
        ).fetchone()
        columns = {
            str(row[1]): str(row[2]).upper()
            for row in connection.execute(f'PRAGMA table_info("{RELATION_NAME}")')
        }
        if relation is None or str(relation[0]).upper() != "TABLE":
            raise ProductionMigrationTxIndexRunnerError("PSI0A_D6D_UNKNOWN_OR_INVALID_RELATION")
        if columns.get(COLUMN_NAME) != COLUMN_AFFINITY:
            raise ProductionMigrationTxIndexRunnerError("PSI0A_D6D_COLUMN_OR_TYPE_DRIFT")
        indexes = _index_columns(connection)
        if INDEX_NAME in indexes and indexes[INDEX_NAME] != INDEX_COLUMNS:
            raise ProductionMigrationTxIndexRunnerError("PSI0A_D6D_CONFLICTING_INDEX_DEFINITION")
        row_count_before = int(
            connection.execute(f'SELECT COUNT(*) FROM "{RELATION_NAME}"').fetchone()[0]
        )
        ledger.append("SCHEMA_PRECONDITION_VERIFIED", row_count_before=row_count_before)
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
            ledger.append("DDL_STARTED", statement_digest=_digest(authorization.statement))
            if fault_hook:
                fault_hook("DDL_STARTED", connection)
            connection.execute(authorization.statement)
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

    verify: Optional[sqlite3.Connection] = None
    satisfying = None
    row_count_after = None
    plan: Tuple[str, ...] = ()
    indexed = False
    try:
        verify = sqlite3.connect(str(database_path.resolve()), timeout=0.25)
        verify.execute(f"PRAGMA busy_timeout={LOCK_TIMEOUT_MS}")
        _install_deadline(
            verify,
            started=started,
            deadline_seconds=authorization.maximum_deadline_seconds,
            clock=clock,
            on_deadline=log_deadline,
        )
        final_indexes = _index_columns(verify)
        satisfying = next(
            (name for name, cols in sorted(final_indexes.items()) if cols[:1] == INDEX_COLUMNS),
            None,
        )
        row_count_after = int(
            verify.execute(f'SELECT COUNT(*) FROM "{RELATION_NAME}"').fetchone()[0]
        )
        plan = _reconciler_plan(verify) if satisfying else ()
        indexed = bool(
            satisfying
            and any("SEARCH token_analysis" in detail and satisfying in detail for detail in plan)
        )
    except BaseException as exc:
        ledger.append(
            "POSTCONDITION_EXCEPTION",
            exception_type=type(exc).__name__,
            exception_message=str(exc),
        )
        status = "FAILED"
    finally:
        if verify is not None:
            verify.set_progress_handler(None, 0)
            ledger.append("POSTCONDITION_PROGRESS_HANDLER_REMOVED", removed=True)
            verify.close()
            ledger.append("POSTCONDITION_CONNECTION_CLOSED", closed=True)

    expected_present = status in {"SUCCEEDED", "NOOP_COMPATIBLE"}
    rows_preserved = row_count_before is not None and row_count_after == row_count_before
    if (
        bool(satisfying) == expected_present
        and (not expected_present or (rows_preserved and indexed))
    ):
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
    return ProductionMigrationTxIndexAttemptResult(
        runner_version=RUNNER_VERSION,
        authorization_digest=authorization.authorization_digest,
        run_id=authorization.run_id,
        d4_contract_digest=authorization.d4_contract_digest,
        database_path_fingerprint=authorization.production_database_path_fingerprint,
        output_directory_fingerprint=authorization.output_directory_fingerprint,
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


def inspect_production_migration_tx_attempt_ledger(
    ledger_path: Path,
) -> ProductionMigrationTxLedgerInspection:
    path = Path(ledger_path)
    if not path.is_file():
        raise ProductionMigrationTxIndexRunnerError("PSI0A_D6D_LEDGER_NOT_FOUND")
    events = []
    for position, raw in enumerate(path.read_text(encoding="utf-8").splitlines()):
        record = json.loads(raw)
        if raw != _canonical(record) or record.get("sequence") != position:
            raise ProductionMigrationTxIndexRunnerError("PSI0A_D6D_LEDGER_REPLAY_MISMATCH")
        events.append(record)
    if not events or events[0].get("event") != "STARTED":
        raise ProductionMigrationTxIndexRunnerError("PSI0A_D6D_LEDGER_START_MISSING")
    digest = _digest(events)
    terminal = events[-1] if events[-1].get("event") == "TERMINAL" else None
    if terminal is None:
        return ProductionMigrationTxLedgerInspection(
            status="INCOMPLETE_EXTERNAL_TERMINATION",
            event_names=tuple(item["event"] for item in events),
            terminal_status=None,
            ledger_digest=digest,
            replay_verified=True,
        )
    if terminal["data"].get("preterminal_digest") != _digest(events[:-1]):
        raise ProductionMigrationTxIndexRunnerError("PSI0A_D6D_TERMINAL_DIGEST_MISMATCH")
    return ProductionMigrationTxLedgerInspection(
        status="TERMINAL",
        event_names=tuple(item["event"] for item in events),
        terminal_status=terminal["data"].get("status"),
        ledger_digest=digest,
        replay_verified=True,
    )


def verify_production_migration_tx_index_attempt_result(
    result: ProductionMigrationTxIndexAttemptResult,
) -> bool:
    inspection = inspect_production_migration_tx_attempt_ledger(Path(result.ledger_path))
    if (
        inspection.status != "TERMINAL"
        or inspection.terminal_status != result.status
        or inspection.ledger_digest != result.ledger_digest
        or result.grants_extraction_authority
        or result.grants_activation_authority
    ):
        raise ProductionMigrationTxIndexRunnerError("PSI0A_D6D_RESULT_REPLAY_MISMATCH")
    return True
