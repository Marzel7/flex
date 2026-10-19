"""PSI0A-A immutable, non-extracting production-shadow boundary contract."""

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import re
from typing import Iterable, Tuple


CONTRACT_VERSION = "psi0a-a.v1"
AUTHORITY_CLASS = "NON_EXECUTABLE_PRODUCTION_SHADOW_PREFLIGHT"
REVISION = re.compile(r"^[0-9a-f]{7,64}$")
IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
FORBIDDEN_SQL = re.compile(
    r"\b(?:insert|update|delete|replace|create|alter|drop|vacuum|attach|detach|"
    r"reindex|analyze|begin|commit|rollback|savepoint|release|load_extension)\b",
    re.IGNORECASE,
)
REQUIRED_STOP_CONDITIONS = (
    "DATABASE_PRESSURE",
    "DEADLINE_OR_RESOURCE_CEILING_BREACH",
    "HEALTH_DEGRADATION",
    "LOCK_CONTENTION",
    "QUERY_PLAN_REGRESSION",
    "READ_ONLY_OR_QUERY_ONLY_NOT_PROVEN",
    "REPLAY_FAILURE",
    "SCHEMA_DRIFT_OR_INCOMPATIBILITY",
    "SCOPE_EXPANSION_REQUIRED",
)
PERMITTED_STATEMENT_CLASSES = (
    "EXPLAIN_QUERY_PLAN_SELECT",
    "PRAGMA_INDEX_INFO",
    "PRAGMA_INDEX_LIST",
    "PRAGMA_QUERY_ONLY",
    "PRAGMA_TABLE_INFO",
    "SELECT_METADATA_ONLY",
)


class ProductionShadowBoundaryError(ValueError):
    pass


@dataclass(frozen=True)
class ProductionSurface:
    database_id: str
    relation_name: str
    relation_type: str


@dataclass(frozen=True)
class ProductionShadowBoundary:
    contract_version: str
    engineering_revision: str
    surfaces: Tuple[ProductionSurface, ...]
    permitted_statement_classes: Tuple[str, ...]
    stop_conditions: Tuple[str, ...]
    connection_mode: str
    query_only_required: bool
    transaction_mode: str
    allows_temporary_objects: bool
    allows_provider_rpc: bool
    allows_production_writes: bool
    allows_evidence_extraction: bool
    allows_shadow_evidence_output: bool
    evidence_mirror_allowed: bool
    cohort_mode_allowed: bool
    grants_extraction_authority: bool
    grants_activation_authority: bool
    authority_class: str
    boundary_digest: str


def _digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(encoded.encode()).hexdigest()


def _identifier(value: object, field: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ProductionShadowBoundaryError(f"PSI0A_A_INVALID_{field.upper()}")
    return value


def build_production_shadow_boundary(
    *, engineering_revision: str, surfaces: Iterable[dict]
) -> ProductionShadowBoundary:
    if not isinstance(engineering_revision, str) or not REVISION.fullmatch(engineering_revision):
        raise ProductionShadowBoundaryError("PSI0A_A_INVALID_ENGINEERING_REVISION")
    normalized = []
    expected = {"database_id", "relation_name", "relation_type"}
    for item in surfaces:
        if not isinstance(item, dict) or set(item) != expected:
            raise ProductionShadowBoundaryError("PSI0A_A_SURFACE_SCHEMA_DRIFT")
        relation_type = item["relation_type"]
        if relation_type not in {"TABLE", "VIEW"}:
            raise ProductionShadowBoundaryError("PSI0A_A_INVALID_RELATION_TYPE")
        normalized.append(
            ProductionSurface(
                _identifier(item["database_id"], "database_id"),
                _identifier(item["relation_name"], "relation_name"),
                relation_type,
            )
        )
    ordered = tuple(sorted(normalized, key=lambda row: (row.database_id, row.relation_name)))
    if not ordered or len(set(ordered)) != len(ordered):
        raise ProductionShadowBoundaryError("PSI0A_A_EMPTY_OR_DUPLICATE_SURFACE")
    body = {
        "contract_version": CONTRACT_VERSION,
        "engineering_revision": engineering_revision,
        "surfaces": [asdict(item) for item in ordered],
        "permitted_statement_classes": PERMITTED_STATEMENT_CLASSES,
        "stop_conditions": REQUIRED_STOP_CONDITIONS,
        "connection_mode": "SQLITE_URI_MODE_RO",
        "query_only_required": True,
        "transaction_mode": "EXPLICIT_BOUNDED_READ_TRANSACTION",
        "allows_temporary_objects": False,
        "allows_provider_rpc": False,
        "allows_production_writes": False,
        "allows_evidence_extraction": False,
        "allows_shadow_evidence_output": False,
        "evidence_mirror_allowed": False,
        "cohort_mode_allowed": False,
        "grants_extraction_authority": False,
        "grants_activation_authority": False,
        "authority_class": AUTHORITY_CLASS,
    }
    return ProductionShadowBoundary(
        contract_version=CONTRACT_VERSION,
        engineering_revision=engineering_revision,
        surfaces=ordered,
        permitted_statement_classes=PERMITTED_STATEMENT_CLASSES,
        stop_conditions=REQUIRED_STOP_CONDITIONS,
        connection_mode="SQLITE_URI_MODE_RO",
        query_only_required=True,
        transaction_mode="EXPLICIT_BOUNDED_READ_TRANSACTION",
        allows_temporary_objects=False,
        allows_provider_rpc=False,
        allows_production_writes=False,
        allows_evidence_extraction=False,
        allows_shadow_evidence_output=False,
        evidence_mirror_allowed=False,
        cohort_mode_allowed=False,
        grants_extraction_authority=False,
        grants_activation_authority=False,
        authority_class=AUTHORITY_CLASS,
        boundary_digest=_digest(body),
    )


def verify_production_shadow_boundary(boundary: ProductionShadowBoundary) -> bool:
    body = asdict(boundary)
    digest = body.pop("boundary_digest", None)
    if boundary.contract_version != CONTRACT_VERSION or digest != _digest(body):
        raise ProductionShadowBoundaryError("PSI0A_A_BOUNDARY_REPLAY_MISMATCH")
    if (
        boundary.authority_class != AUTHORITY_CLASS
        or boundary.grants_extraction_authority
        or boundary.grants_activation_authority
        or boundary.allows_evidence_extraction
        or boundary.allows_shadow_evidence_output
        or boundary.allows_production_writes
        or boundary.allows_provider_rpc
        or boundary.allows_temporary_objects
        or boundary.evidence_mirror_allowed
        or boundary.cohort_mode_allowed
    ):
        raise ProductionShadowBoundaryError("PSI0A_A_AUTHORITY_EXPANSION")
    return True


def classify_read_only_statement(
    boundary: ProductionShadowBoundary, *, database_id: str, sql: str
) -> str:
    verify_production_shadow_boundary(boundary)
    database_id = _identifier(database_id, "database_id")
    if database_id not in {item.database_id for item in boundary.surfaces}:
        raise ProductionShadowBoundaryError("PSI0A_A_UNKNOWN_DATABASE_SURFACE")
    if not isinstance(sql, str) or not sql.strip():
        raise ProductionShadowBoundaryError("PSI0A_A_INVALID_STATEMENT")
    normalized = " ".join(sql.strip().split())
    if ";" in normalized or "--" in normalized or "/*" in normalized or FORBIDDEN_SQL.search(normalized):
        raise ProductionShadowBoundaryError("PSI0A_A_WRITE_CAPABLE_OR_MULTI_STATEMENT")
    lowered = normalized.lower()
    if lowered == "pragma query_only":
        return "PRAGMA_QUERY_ONLY"
    pragma = re.fullmatch(r"pragma (table_info|index_list|index_info)\(['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?\)", normalized, re.IGNORECASE)
    if pragma:
        relation = pragma.group(2)
        allowed = {item.relation_name for item in boundary.surfaces if item.database_id == database_id}
        if relation not in allowed and pragma.group(1).lower() != "index_info":
            raise ProductionShadowBoundaryError("PSI0A_A_UNKNOWN_RELATION_SURFACE")
        return f"PRAGMA_{pragma.group(1).upper()}"
    if lowered.startswith("explain query plan select "):
        statement_class = "EXPLAIN_QUERY_PLAN_SELECT"
    elif lowered.startswith("select "):
        statement_class = "SELECT_METADATA_ONLY"
    else:
        raise ProductionShadowBoundaryError("PSI0A_A_STATEMENT_CLASS_PROHIBITED")
    relations = set(re.findall(r"\b(?:from|join)\s+([A-Za-z_][A-Za-z0-9_]*)", normalized, re.IGNORECASE))
    allowed = {item.relation_name for item in boundary.surfaces if item.database_id == database_id}
    if not relations or not relations.issubset(allowed):
        raise ProductionShadowBoundaryError("PSI0A_A_UNKNOWN_RELATION_SURFACE")
    return statement_class
