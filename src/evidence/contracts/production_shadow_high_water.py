"""PSI0A-C stable high-water/read-boundary contract."""

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
import time
from typing import Callable, Mapping, Optional, Tuple
from urllib.parse import quote

from .production_shadow_boundary import ProductionShadowBoundary, verify_production_shadow_boundary
from .production_shadow_schema_audit import ProductionSchemaAudit, verify_production_schema_audit


CONTRACT_VERSION = "psi0a-c.v1"
IDENTIFIER = re.compile(r"^(?:rowid|[A-Za-z_][A-Za-z0-9_]*)$")


class ProductionShadowHighWaterError(RuntimeError):
    pass


@dataclass(frozen=True)
class HighWaterSpec:
    database_id: str
    relation_name: str
    cursor_column: str
    event_column: Optional[str]


def creator_tokens_cursor_only_high_water_spec() -> HighWaterSpec:
    """Return the qualified creator boundary without timestamp coercion.

    ``creator_tokens.created_at`` has historically accepted integer seconds,
    real seconds, and ISO-8601 text through distinct committed write paths.
    SQLite ``rowid`` is therefore the only qualified stable ordering boundary
    for this relation.  The missing event column is deliberate and must not be
    replaced by implicit timestamp normalization.
    """
    return HighWaterSpec(
        database_id="creator",
        relation_name="creator_tokens",
        cursor_column="rowid",
        event_column=None,
    )


@dataclass(frozen=True)
class RelationHighWater:
    database_id: str
    database_file_name: str
    relation_name: str
    cursor_column: str
    cursor_upper_inclusive: int
    event_column: Optional[str]
    event_upper_inclusive: Optional[int]


@dataclass(frozen=True)
class ProductionShadowReadBoundary:
    contract_version: str
    boundary_digest: str
    schema_audit_digest: str
    captured_at_utc_ns: int
    transaction_mode: str
    relations: Tuple[RelationHighWater, ...]
    evidence_rows_materialized: int
    grants_extraction_authority: bool
    grants_activation_authority: bool
    read_boundary_digest: str


def _digest(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _connect(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(
        f"file:{quote(str(Path(path).resolve()), safe='/')}?mode=ro",
        uri=True,
        timeout=0.25,
        isolation_level=None,
    )


def _open(
    path: Path, *, deadline_at: float, clock: Callable[[], float]
) -> tuple[sqlite3.Connection, dict[str, bool]]:
    if not Path(path).is_file():
        raise ProductionShadowHighWaterError("PSI0A_C_SOURCE_NOT_FOUND")
    connection = _connect(path)
    deadline_state = {"exceeded": False}

    def _progress() -> int:
        if clock() >= deadline_at:
            deadline_state["exceeded"] = True
            return 1
        return 0

    try:
        connection.execute("PRAGMA query_only=ON")
        if connection.execute("PRAGMA query_only").fetchone()[0] != 1:
            raise ProductionShadowHighWaterError("PSI0A_C_QUERY_ONLY_NOT_ENFORCED")
        connection.set_progress_handler(_progress, 1000)
        connection.execute("BEGIN")
    except BaseException:
        connection.set_progress_handler(None, 0)
        connection.close()
        raise
    return connection, deadline_state


def _maximum(
    connection: sqlite3.Connection,
    sql: str,
    *,
    deadline_at: float,
    deadline_state: dict[str, bool],
    clock: Callable[[], float],
) -> object:
    if clock() >= deadline_at:
        deadline_state["exceeded"] = True
        raise ProductionShadowHighWaterError("PSI0A_C_QUERY_DEADLINE_EXCEEDED")
    try:
        return connection.execute(sql).fetchone()[0]
    except sqlite3.OperationalError as exc:
        if deadline_state["exceeded"]:
            raise ProductionShadowHighWaterError(
                "PSI0A_C_QUERY_DEADLINE_EXCEEDED"
            ) from exc
        raise


def _nonnegative_integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ProductionShadowHighWaterError("PSI0A_C_NON_INTEGER_HIGH_WATER")
    return value


def capture_production_shadow_read_boundary(
    boundary: ProductionShadowBoundary,
    schema_audit: ProductionSchemaAudit,
    source_paths: Mapping[str, Path],
    specs: Tuple[HighWaterSpec, ...],
    *,
    captured_at_utc_ns: int,
    max_query_seconds: float,
    clock: Callable[[], float] = time.monotonic,
) -> ProductionShadowReadBoundary:
    verify_production_shadow_boundary(boundary)
    verify_production_schema_audit(schema_audit)
    if schema_audit.verdict != "SCHEMA_COMPATIBLE" or schema_audit.boundary_digest != boundary.boundary_digest:
        raise ProductionShadowHighWaterError("PSI0A_C_SCHEMA_GATE_FAILED")
    if isinstance(captured_at_utc_ns, bool) or not isinstance(captured_at_utc_ns, int) or captured_at_utc_ns <= 0:
        raise ProductionShadowHighWaterError("PSI0A_C_INVALID_CAPTURE_TIME")
    if (
        isinstance(max_query_seconds, bool)
        or not isinstance(max_query_seconds, (int, float))
        or max_query_seconds <= 0
    ):
        raise ProductionShadowHighWaterError("PSI0A_C_INVALID_QUERY_DEADLINE")
    deadline_at = clock() + float(max_query_seconds)
    surfaces = {(item.database_id, item.relation_name) for item in boundary.surfaces}
    spec_keys = {(item.database_id, item.relation_name) for item in specs}
    if not specs or len(spec_keys) != len(specs) or spec_keys != surfaces:
        raise ProductionShadowHighWaterError("PSI0A_C_BOUNDARY_SPEC_MISMATCH")
    if set(source_paths) != {item.database_id for item in specs}:
        raise ProductionShadowHighWaterError("PSI0A_C_SOURCE_SET_MISMATCH")
    connections: dict[str, tuple[sqlite3.Connection, dict[str, bool]]] = {}
    try:
        for database_id in sorted(source_paths):
            connections[database_id] = _open(
                Path(source_paths[database_id]), deadline_at=deadline_at, clock=clock
            )
        relations = []
        for spec in sorted(specs, key=lambda item: (item.database_id, item.relation_name)):
            if not IDENTIFIER.fullmatch(spec.cursor_column) or (
                spec.event_column is not None and not IDENTIFIER.fullmatch(spec.event_column)
            ):
                raise ProductionShadowHighWaterError("PSI0A_C_INVALID_BOUNDARY_COLUMN")
            connection, deadline_state = connections[spec.database_id]
            cursor = _nonnegative_integer(_maximum(
                connection,
                f'SELECT COALESCE(MAX("{spec.cursor_column}"),0) FROM "{spec.relation_name}"',
                deadline_at=deadline_at,
                deadline_state=deadline_state,
                clock=clock,
            ))
            event = None
            if spec.event_column is not None:
                event = _nonnegative_integer(_maximum(
                    connection,
                    f'SELECT COALESCE(MAX("{spec.event_column}"),0) FROM "{spec.relation_name}"',
                    deadline_at=deadline_at,
                    deadline_state=deadline_state,
                    clock=clock,
                ))
            relations.append(RelationHighWater(
                spec.database_id,
                Path(source_paths[spec.database_id]).name,
                spec.relation_name,
                spec.cursor_column,
                cursor,
                spec.event_column,
                event,
            ))
    finally:
        for connection, _ in connections.values():
            try:
                try:
                    connection.execute("ROLLBACK")
                finally:
                    connection.set_progress_handler(None, 0)
            finally:
                connection.close()
    body = {
        "contract_version": CONTRACT_VERSION,
        "boundary_digest": boundary.boundary_digest,
        "schema_audit_digest": schema_audit.audit_digest,
        "captured_at_utc_ns": captured_at_utc_ns,
        "transaction_mode": "EXPLICIT_BOUNDED_READ_TRANSACTION",
        "relations": [asdict(item) for item in relations],
        "evidence_rows_materialized": 0,
        "grants_extraction_authority": False,
        "grants_activation_authority": False,
    }
    return ProductionShadowReadBoundary(
        contract_version=CONTRACT_VERSION,
        boundary_digest=boundary.boundary_digest,
        schema_audit_digest=schema_audit.audit_digest,
        captured_at_utc_ns=captured_at_utc_ns,
        transaction_mode="EXPLICIT_BOUNDED_READ_TRANSACTION",
        relations=tuple(relations),
        evidence_rows_materialized=0,
        grants_extraction_authority=False,
        grants_activation_authority=False,
        read_boundary_digest=_digest(body),
    )


def verify_production_shadow_read_boundary(result: ProductionShadowReadBoundary) -> bool:
    body = asdict(result); digest = body.pop("read_boundary_digest", None)
    if result.contract_version != CONTRACT_VERSION or digest != _digest(body):
        raise ProductionShadowHighWaterError("PSI0A_C_READ_BOUNDARY_REPLAY_MISMATCH")
    if result.evidence_rows_materialized or result.grants_extraction_authority or result.grants_activation_authority:
        raise ProductionShadowHighWaterError("PSI0A_C_AUTHORITY_OR_EXTRACTION_MISMATCH")
    return True
