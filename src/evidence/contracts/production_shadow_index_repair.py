"""PSI0A-C10 fixture-only additive-index repair qualification contract."""

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import re
import sqlite3
from typing import Mapping, Tuple


CONTRACT_VERSION = "psi0a-c10.v1"
SOURCE_AUDIT_DIGEST = "08642b6ca525a01f029e60029798afc16351162869ab80cc8831d7c212a55c31"
AUTHORITY_CLASS = "FIXTURE_ONLY_ADDITIVE_INDEX_REPAIR_QUALIFICATION"
REVISION = re.compile(r"^[0-9a-f]{7,64}$")
IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ProductionShadowIndexRepairError(RuntimeError):
    pass


@dataclass(frozen=True)
class AdditiveIndexRepair:
    database_id: str
    relation_name: str
    required_columns: Tuple[Tuple[str, str], ...]
    index_name: str
    index_columns: Tuple[str, ...]
    statement: str


@dataclass(frozen=True)
class ProductionShadowIndexRepairContract:
    contract_version: str
    engineering_revision: str
    source_audit_digest: str
    repairs: Tuple[AdditiveIndexRepair, ...]
    fixture_only: bool
    allows_production_access: bool
    allows_production_ddl: bool
    grants_extraction_authority: bool
    grants_activation_authority: bool
    authority_class: str
    contract_digest: str


@dataclass(frozen=True)
class IndexRepairResult:
    contract_digest: str
    created_indexes: Tuple[str, ...]
    already_compatible_indexes: Tuple[str, ...]
    resulting_prefixes: Tuple[Tuple[str, str, Tuple[str, ...]], ...]
    statements_executed: int
    fixture_only: bool
    replay_digest: str


def _digest(value: object) -> str:
    return sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _quote_identifier(value: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ProductionShadowIndexRepairError("PSI0A_C10_INVALID_IDENTIFIER")
    return f'"{value}"'


def _repair(
    database_id: str,
    relation_name: str,
    required_columns: Tuple[Tuple[str, str], ...],
    index_name: str,
    index_columns: Tuple[str, ...],
) -> AdditiveIndexRepair:
    quoted_columns = ", ".join(_quote_identifier(item) for item in index_columns)
    statement = (
        f"CREATE INDEX IF NOT EXISTS {_quote_identifier(index_name)} "
        f"ON {_quote_identifier(relation_name)} ({quoted_columns})"
    )
    if ";" in statement or statement.count("CREATE INDEX") != 1:
        raise ProductionShadowIndexRepairError("PSI0A_C10_INVALID_INDEX_STATEMENT")
    return AdditiveIndexRepair(
        database_id=database_id,
        relation_name=relation_name,
        required_columns=required_columns,
        index_name=index_name,
        index_columns=index_columns,
        statement=statement,
    )


def build_production_shadow_index_repair_contract(
    *, engineering_revision: str,
) -> ProductionShadowIndexRepairContract:
    if not isinstance(engineering_revision, str) or not REVISION.fullmatch(engineering_revision):
        raise ProductionShadowIndexRepairError("PSI0A_C10_INVALID_ENGINEERING_REVISION")
    repairs = (
        _repair(
            "evidence",
            "normalized_evidence_records",
            (("fact_family", "TEXT"),),
            "idx_psi0a_normalized_evidence_fact_family",
            ("fact_family",),
        ),
        _repair(
            "main",
            "token_analysis",
            (("migrated_at", "INTEGER"), ("mint", "TEXT")),
            "idx_psi0a_token_analysis_migrated_mint",
            ("migrated_at", "mint"),
        ),
    )
    body = {
        "contract_version": CONTRACT_VERSION,
        "engineering_revision": engineering_revision,
        "source_audit_digest": SOURCE_AUDIT_DIGEST,
        "repairs": [asdict(item) for item in repairs],
        "fixture_only": True,
        "allows_production_access": False,
        "allows_production_ddl": False,
        "grants_extraction_authority": False,
        "grants_activation_authority": False,
        "authority_class": AUTHORITY_CLASS,
    }
    return ProductionShadowIndexRepairContract(
        contract_version=CONTRACT_VERSION,
        engineering_revision=engineering_revision,
        source_audit_digest=SOURCE_AUDIT_DIGEST,
        repairs=repairs,
        fixture_only=True,
        allows_production_access=False,
        allows_production_ddl=False,
        grants_extraction_authority=False,
        grants_activation_authority=False,
        authority_class=AUTHORITY_CLASS,
        contract_digest=_digest(body),
    )


def verify_production_shadow_index_repair_contract(
    contract: ProductionShadowIndexRepairContract,
) -> bool:
    expected = build_production_shadow_index_repair_contract(
        engineering_revision=contract.engineering_revision
    )
    if contract != expected:
        raise ProductionShadowIndexRepairError("PSI0A_C10_CONTRACT_REPLAY_MISMATCH")
    if (
        not contract.fixture_only
        or contract.allows_production_access
        or contract.allows_production_ddl
        or contract.grants_extraction_authority
        or contract.grants_activation_authority
        or contract.authority_class != AUTHORITY_CLASS
    ):
        raise ProductionShadowIndexRepairError("PSI0A_C10_AUTHORITY_EXPANSION")
    return True


def _index_columns(connection: sqlite3.Connection, relation_name: str) -> dict[str, Tuple[str, ...]]:
    indexes = {}
    for row in connection.execute(f"PRAGMA index_list({_quote_identifier(relation_name)})"):
        name = str(row[1])
        indexes[name] = tuple(
            str(item[2])
            for item in connection.execute(f"PRAGMA index_info({_quote_identifier(name)})")
        )
    return indexes


def apply_fixture_index_repairs(
    contract: ProductionShadowIndexRepairContract,
    connections: Mapping[str, sqlite3.Connection],
    *,
    fixture_authorization: str,
) -> IndexRepairResult:
    """Apply the exact repair tuple to caller-owned frozen/ephemeral fixtures only."""
    verify_production_shadow_index_repair_contract(contract)
    if fixture_authorization != "FROZEN_OR_EPHEMERAL_FIXTURE_ONLY":
        raise ProductionShadowIndexRepairError("PSI0A_C10_FIXTURE_AUTHORIZATION_REQUIRED")
    expected_databases = {item.database_id for item in contract.repairs}
    if set(connections) != expected_databases:
        raise ProductionShadowIndexRepairError("PSI0A_C10_DATABASE_SET_MISMATCH")

    created = []
    compatible = []
    resulting = []
    executed = 0
    for repair in contract.repairs:
        connection = connections[repair.database_id]
        relation = connection.execute(
            "SELECT type FROM sqlite_schema WHERE name=?", (repair.relation_name,)
        ).fetchone()
        if relation is None or str(relation[0]).upper() != "TABLE":
            raise ProductionShadowIndexRepairError("PSI0A_C10_UNKNOWN_OR_INVALID_RELATION")
        columns = {
            str(row[1]): str(row[2]).upper()
            for row in connection.execute(
                f"PRAGMA table_info({_quote_identifier(repair.relation_name)})"
            )
        }
        for name, affinity in repair.required_columns:
            if name not in columns or columns[name] != affinity:
                raise ProductionShadowIndexRepairError("PSI0A_C10_COLUMN_OR_TYPE_DRIFT")

        indexes = _index_columns(connection, repair.relation_name)
        named = indexes.get(repair.index_name)
        if named is not None and named != repair.index_columns:
            raise ProductionShadowIndexRepairError("PSI0A_C10_CONFLICTING_INDEX_DEFINITION")
        satisfying = next(
            (name for name, cols in sorted(indexes.items()) if cols[: len(repair.index_columns)] == repair.index_columns),
            None,
        )
        if satisfying is not None:
            compatible.append(satisfying)
        else:
            connection.execute(repair.statement)
            executed += 1
            created.append(repair.index_name)
        final_indexes = _index_columns(connection, repair.relation_name)
        if final_indexes.get(repair.index_name, ())[: len(repair.index_columns)] != repair.index_columns and not any(
            cols[: len(repair.index_columns)] == repair.index_columns
            for cols in final_indexes.values()
        ):
            raise ProductionShadowIndexRepairError("PSI0A_C10_REQUIRED_PREFIX_NOT_CREATED")
        resulting.append((repair.database_id, repair.relation_name, repair.index_columns))

    body = {
        "contract_digest": contract.contract_digest,
        "created_indexes": tuple(created),
        "already_compatible_indexes": tuple(compatible),
        "resulting_prefixes": tuple(resulting),
        "statements_executed": executed,
        "fixture_only": True,
    }
    return IndexRepairResult(**body, replay_digest=_digest(body))


def verify_index_repair_result(result: IndexRepairResult) -> bool:
    body = asdict(result)
    digest = body.pop("replay_digest", None)
    if not result.fixture_only or digest != _digest(body):
        raise ProductionShadowIndexRepairError("PSI0A_C10_RESULT_REPLAY_MISMATCH")
    return True
