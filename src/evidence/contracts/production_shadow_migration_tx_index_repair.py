"""PSI0A-D4 fixture-only migration_tx additive-index repair contract."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import re
import sqlite3
from typing import Tuple


CONTRACT_VERSION = "psi0a-d4.v1"
SOURCE_DIAGNOSIS = "PSI0A-D3"
AUTHORITY_CLASS = "FIXTURE_ONLY_MIGRATION_TX_ADDITIVE_INDEX_REPAIR"
DATABASE_ID = "main"
RELATION_NAME = "token_analysis"
COLUMN_NAME = "migration_tx"
COLUMN_AFFINITY = "TEXT"
INDEX_NAME = "idx_psi0a_token_analysis_migration_tx"
INDEX_COLUMNS = (COLUMN_NAME,)
INDEX_STATEMENT = (
    'CREATE INDEX IF NOT EXISTS "idx_psi0a_token_analysis_migration_tx" '
    'ON "token_analysis" ("migration_tx")'
)
RECONCILER_QUERY = (
    "SELECT migration_tx FROM token_analysis "
    "WHERE migration_tx IN (?,?,?)"
)
_REVISION = re.compile(r"^[0-9a-f]{7,64}$")


class MigrationTxIndexRepairError(RuntimeError):
    pass


@dataclass(frozen=True)
class MigrationTxIndexRepairContract:
    contract_version: str
    engineering_revision: str
    source_diagnosis: str
    database_id: str
    relation_name: str
    required_column: Tuple[str, str]
    index_name: str
    index_columns: Tuple[str, ...]
    statement: str
    reconciler_query: str
    fixture_only: bool
    allows_production_access: bool
    allows_production_ddl: bool
    grants_extraction_authority: bool
    grants_activation_authority: bool
    authority_class: str
    contract_digest: str


@dataclass(frozen=True)
class MigrationTxIndexRepairResult:
    contract_digest: str
    created: bool
    already_compatible: bool
    statements_executed: int
    row_count_before: int
    row_count_after: int
    resulting_index_columns: Tuple[str, ...]
    plan_before: Tuple[str, ...]
    plan_after: Tuple[str, ...]
    plan_before_full_scan: bool
    plan_after_uses_required_index: bool
    fixture_only: bool
    replay_digest: str


def _digest(value: object) -> str:
    return sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def build_migration_tx_index_repair_contract(
    *, engineering_revision: str,
) -> MigrationTxIndexRepairContract:
    if not isinstance(engineering_revision, str) or not _REVISION.fullmatch(engineering_revision):
        raise MigrationTxIndexRepairError("PSI0A_D4_INVALID_ENGINEERING_REVISION")
    if ";" in INDEX_STATEMENT or INDEX_STATEMENT.count("CREATE INDEX") != 1:
        raise MigrationTxIndexRepairError("PSI0A_D4_INVALID_INDEX_STATEMENT")
    body = {
        "contract_version": CONTRACT_VERSION,
        "engineering_revision": engineering_revision,
        "source_diagnosis": SOURCE_DIAGNOSIS,
        "database_id": DATABASE_ID,
        "relation_name": RELATION_NAME,
        "required_column": (COLUMN_NAME, COLUMN_AFFINITY),
        "index_name": INDEX_NAME,
        "index_columns": INDEX_COLUMNS,
        "statement": INDEX_STATEMENT,
        "reconciler_query": RECONCILER_QUERY,
        "fixture_only": True,
        "allows_production_access": False,
        "allows_production_ddl": False,
        "grants_extraction_authority": False,
        "grants_activation_authority": False,
        "authority_class": AUTHORITY_CLASS,
    }
    return MigrationTxIndexRepairContract(**body, contract_digest=_digest(body))


def verify_migration_tx_index_repair_contract(
    contract: MigrationTxIndexRepairContract,
) -> bool:
    expected = build_migration_tx_index_repair_contract(
        engineering_revision=contract.engineering_revision
    )
    if contract != expected:
        raise MigrationTxIndexRepairError("PSI0A_D4_CONTRACT_REPLAY_MISMATCH")
    if (
        not contract.fixture_only
        or contract.allows_production_access
        or contract.allows_production_ddl
        or contract.grants_extraction_authority
        or contract.grants_activation_authority
        or contract.authority_class != AUTHORITY_CLASS
    ):
        raise MigrationTxIndexRepairError("PSI0A_D4_AUTHORITY_EXPANSION")
    return True


def _indexes(connection: sqlite3.Connection) -> dict[str, Tuple[str, ...]]:
    result = {}
    for row in connection.execute('PRAGMA index_list("token_analysis")'):
        name = str(row[1])
        result[name] = tuple(
            str(item[2])
            for item in connection.execute(f'PRAGMA index_info("{name}")')
        )
    return result


def _plan(connection: sqlite3.Connection) -> Tuple[str, ...]:
    return tuple(
        str(row[3])
        for row in connection.execute(
            "EXPLAIN QUERY PLAN " + RECONCILER_QUERY, ("a", "b", "c")
        )
    )


def apply_fixture_migration_tx_index_repair(
    contract: MigrationTxIndexRepairContract,
    connection: sqlite3.Connection,
    *,
    fixture_authorization: str,
) -> MigrationTxIndexRepairResult:
    verify_migration_tx_index_repair_contract(contract)
    if fixture_authorization != "FROZEN_OR_EPHEMERAL_FIXTURE_ONLY":
        raise MigrationTxIndexRepairError("PSI0A_D4_FIXTURE_AUTHORIZATION_REQUIRED")
    relation = connection.execute(
        "SELECT type FROM sqlite_schema WHERE name=?", (RELATION_NAME,)
    ).fetchone()
    if relation is None or str(relation[0]).upper() != "TABLE":
        raise MigrationTxIndexRepairError("PSI0A_D4_UNKNOWN_OR_INVALID_RELATION")
    columns = {
        str(row[1]): str(row[2]).upper()
        for row in connection.execute('PRAGMA table_info("token_analysis")')
    }
    if columns.get(COLUMN_NAME) != COLUMN_AFFINITY:
        raise MigrationTxIndexRepairError("PSI0A_D4_COLUMN_OR_TYPE_DRIFT")

    indexes_before = _indexes(connection)
    named = indexes_before.get(INDEX_NAME)
    if named is not None and named != INDEX_COLUMNS:
        raise MigrationTxIndexRepairError("PSI0A_D4_CONFLICTING_INDEX_DEFINITION")
    satisfying = next(
        (name for name, cols in sorted(indexes_before.items()) if cols[:1] == INDEX_COLUMNS),
        None,
    )
    row_count_before = int(connection.execute("SELECT COUNT(*) FROM token_analysis").fetchone()[0])
    plan_before = _plan(connection)
    created = satisfying is None
    if created:
        connection.execute(contract.statement)
    indexes_after = _indexes(connection)
    satisfying_after = next(
        (name for name, cols in sorted(indexes_after.items()) if cols[:1] == INDEX_COLUMNS),
        None,
    )
    if satisfying_after is None:
        raise MigrationTxIndexRepairError("PSI0A_D4_REQUIRED_PREFIX_NOT_CREATED")
    row_count_after = int(connection.execute("SELECT COUNT(*) FROM token_analysis").fetchone()[0])
    if row_count_after != row_count_before:
        raise MigrationTxIndexRepairError("PSI0A_D4_ROW_PRESERVATION_FAILED")
    plan_after = _plan(connection)
    plan_before_scan = any("SCAN token_analysis" in item for item in plan_before)
    plan_after_index = any(
        "SEARCH token_analysis" in item and satisfying_after in item for item in plan_after
    )
    if not plan_after_index:
        raise MigrationTxIndexRepairError("PSI0A_D4_INDEX_NOT_SELECTED")
    body = {
        "contract_digest": contract.contract_digest,
        "created": created,
        "already_compatible": not created,
        "statements_executed": int(created),
        "row_count_before": row_count_before,
        "row_count_after": row_count_after,
        "resulting_index_columns": indexes_after[satisfying_after],
        "plan_before": plan_before,
        "plan_after": plan_after,
        "plan_before_full_scan": plan_before_scan,
        "plan_after_uses_required_index": plan_after_index,
        "fixture_only": True,
    }
    return MigrationTxIndexRepairResult(**body, replay_digest=_digest(body))


def verify_migration_tx_index_repair_result(
    result: MigrationTxIndexRepairResult,
) -> bool:
    body = asdict(result)
    digest = body.pop("replay_digest", None)
    if not result.fixture_only or digest != _digest(body):
        raise MigrationTxIndexRepairError("PSI0A_D4_RESULT_REPLAY_MISMATCH")
    return True
