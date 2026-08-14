"""PSI0A-B bounded read-only production schema compatibility audit."""

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Mapping, Tuple
from urllib.parse import quote

from .production_shadow_boundary import (
    ProductionShadowBoundary,
    verify_production_shadow_boundary,
)


AUDIT_VERSION = "psi0a-b.v1"


class ProductionShadowSchemaAuditError(RuntimeError):
    pass


@dataclass(frozen=True)
class RequiredRelation:
    database_id: str
    relation_name: str
    relation_type: str
    required_columns: Tuple[Tuple[str, str], ...]
    required_index_prefixes: Tuple[Tuple[str, ...], ...]


@dataclass(frozen=True)
class RelationSchemaFinding:
    database_id: str
    database_file_name: str
    relation_name: str
    relation_type: str
    columns: Tuple[Tuple[str, str, int, int], ...]
    indexes: Tuple[Tuple[str, Tuple[str, ...], int, int], ...]
    missing_columns: Tuple[str, ...]
    type_mismatches: Tuple[str, ...]
    missing_index_prefixes: Tuple[Tuple[str, ...], ...]
    compatible: bool
    schema_digest: str


@dataclass(frozen=True)
class ProductionSchemaAudit:
    audit_version: str
    boundary_digest: str
    findings: Tuple[RelationSchemaFinding, ...]
    compatible_relation_count: int
    incompatible_relation_count: int
    verdict: str
    production_rows_read: int
    evidence_extractions: int
    audit_digest: str


def _digest(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _open(path: Path) -> sqlite3.Connection:
    if not Path(path).is_file():
        raise ProductionShadowSchemaAuditError("PSI0A_B_SOURCE_NOT_FOUND")
    connection = sqlite3.connect(
        f"file:{quote(str(Path(path).resolve()), safe='/')}?mode=ro",
        uri=True,
        timeout=0.25,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    if connection.execute("PRAGMA query_only").fetchone()[0] != 1:
        connection.close()
        raise ProductionShadowSchemaAuditError("PSI0A_B_QUERY_ONLY_NOT_ENFORCED")
    return connection


def audit_production_schema(
    boundary: ProductionShadowBoundary,
    source_paths: Mapping[str, Path],
    requirements: Tuple[RequiredRelation, ...],
) -> ProductionSchemaAudit:
    verify_production_shadow_boundary(boundary)
    surface_keys = {(item.database_id, item.relation_name, item.relation_type) for item in boundary.surfaces}
    requirement_keys = {(item.database_id, item.relation_name, item.relation_type) for item in requirements}
    if not requirements or len(requirement_keys) != len(requirements) or requirement_keys != surface_keys:
        raise ProductionShadowSchemaAuditError("PSI0A_B_BOUNDARY_REQUIREMENT_MISMATCH")
    if set(source_paths) != {item.database_id for item in requirements}:
        raise ProductionShadowSchemaAuditError("PSI0A_B_SOURCE_SET_MISMATCH")
    findings = []
    for requirement in sorted(requirements, key=lambda item: (item.database_id, item.relation_name)):
        connection = _open(Path(source_paths[requirement.database_id]))
        try:
            relation = connection.execute(
                "SELECT type,name FROM sqlite_schema WHERE name=?", (requirement.relation_name,)
            ).fetchone()
            actual_type = str(relation["type"]).upper() if relation else "MISSING"
            columns = tuple(
                (str(row["name"]), str(row["type"]).upper(), int(row["notnull"]), int(row["pk"]))
                for row in connection.execute(f'PRAGMA table_info("{requirement.relation_name}")')
            )
            indexes = []
            for row in connection.execute(f'PRAGMA index_list("{requirement.relation_name}")'):
                name = str(row["name"])
                index_columns = tuple(
                    str(item["name"]) for item in connection.execute(f'PRAGMA index_info("{name}")')
                )
                indexes.append((name, index_columns, int(row["unique"]), int(row["partial"])))
        finally:
            connection.close()
        column_types = {name: affinity for name, affinity, _, _ in columns}
        required_types = dict(requirement.required_columns)
        missing = tuple(sorted(set(required_types) - set(column_types)))
        mismatched = tuple(
            sorted(name for name, affinity in requirement.required_columns if name in column_types and column_types[name] != affinity)
        )
        prefixes = tuple(columns for _, columns, _, _ in indexes)
        missing_indexes = tuple(
            prefix for prefix in requirement.required_index_prefixes
            if not any(actual[: len(prefix)] == prefix for actual in prefixes)
        )
        compatible = (
            actual_type == requirement.relation_type
            and not missing and not mismatched and not missing_indexes
        )
        schema_body = {
            "database_id": requirement.database_id,
            "database_file_name": Path(source_paths[requirement.database_id]).name,
            "relation_name": requirement.relation_name,
            "relation_type": actual_type,
            "columns": columns,
            "indexes": tuple(sorted(indexes)),
        }
        findings.append(RelationSchemaFinding(
            **schema_body,
            missing_columns=missing,
            type_mismatches=mismatched,
            missing_index_prefixes=missing_indexes,
            compatible=compatible,
            schema_digest=_digest(schema_body),
        ))
    ordered = tuple(findings)
    compatible_count = sum(item.compatible for item in ordered)
    body = {
        "audit_version": AUDIT_VERSION,
        "boundary_digest": boundary.boundary_digest,
        "findings": [asdict(item) for item in ordered],
        "compatible_relation_count": compatible_count,
        "incompatible_relation_count": len(ordered) - compatible_count,
        "verdict": "SCHEMA_COMPATIBLE" if compatible_count == len(ordered) else "SCHEMA_INCOMPATIBLE",
        "production_rows_read": 0,
        "evidence_extractions": 0,
    }
    return ProductionSchemaAudit(
        audit_version=AUDIT_VERSION,
        boundary_digest=boundary.boundary_digest,
        findings=ordered,
        compatible_relation_count=compatible_count,
        incompatible_relation_count=len(ordered) - compatible_count,
        verdict="SCHEMA_COMPATIBLE" if compatible_count == len(ordered) else "SCHEMA_INCOMPATIBLE",
        production_rows_read=0,
        evidence_extractions=0,
        audit_digest=_digest(body),
    )


def verify_production_schema_audit(audit: ProductionSchemaAudit) -> bool:
    body = asdict(audit)
    digest = body.pop("audit_digest", None)
    if audit.audit_version != AUDIT_VERSION or digest != _digest(body):
        raise ProductionShadowSchemaAuditError("PSI0A_B_AUDIT_REPLAY_MISMATCH")
    if audit.production_rows_read or audit.evidence_extractions:
        raise ProductionShadowSchemaAuditError("PSI0A_B_EXTRACTION_OR_ROW_ACCESS_RECORDED")
    return True
