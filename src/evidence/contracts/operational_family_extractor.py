"""EB0.4G bounded query-only extraction from normalized fixture evidence."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import time
from typing import Callable, Mapping, Tuple
from urllib.parse import quote

from .operational_family_adapters import adapt_normalized_operation_runtime
from .operational_family_corpus import OperationalFamilyCorpus, assemble_operational_family_corpora
from .operational_family_manifest import OperationalFamilyManifest, build_operational_family_manifest
from .operational_family_nomination import OperationBehaviourFact, nominate_operational_family


EXTRACTOR_SCHEMA_VERSION = "eb0.4g.v1"
MAX_OPERATIONS = 5_000
MAX_EVIDENCE_ROWS = 10_000
MAX_GROUPS = 5_000
MAX_MEMBERSHIPS = 50_000
MAX_QUERY_SECONDS = 30.0

_SCHEMA = {
    "operation_cohort": {"position", "operation_id"},
    "normalized_operation_runtime": {
        "schema_version", "identity_basis", "operation_id", "primary_role",
        "contract_id", "contract_version", "module_id", "module_version",
        "topology_revision_id", "behaviour_observation_id", "input_digest",
        "edge_features_json", "mechanism_features_json", "temporal_features_json",
        "quality_state", "completeness_state", "conflict_group_id",
    },
    "nomination_candidates": {"group_id", "position", "operation_id", "nomination_state"},
}


class OperationalFamilyExtractorError(RuntimeError):
    """Named fail-closed EB0.4G error."""


@dataclass(frozen=True)
class OperationalFamilyExtraction:
    schema_version: str
    selected_operation_ids: Tuple[str, ...]
    qualified_operation_ids: Tuple[str, ...]
    excluded_operations: Mapping[str, str]
    candidate_group_count: int
    fact_count: int
    nomination_count: int
    conflict_count: int
    manifests: Tuple[OperationalFamilyManifest, ...]
    corpora: Tuple[OperationalFamilyCorpus, ...]
    input_fingerprint: str
    result_digest: str


def _digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(payload.encode()).hexdigest()


def _open(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise OperationalFamilyExtractorError("EB0_4G_SOURCE_NOT_FOUND")
    connection = sqlite3.connect(f"file:{quote(str(path.resolve()), safe='/')}?mode=ro", uri=True, timeout=0.25)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    if connection.execute("PRAGMA query_only").fetchone()[0] != 1:
        connection.close()
        raise OperationalFamilyExtractorError("EB0_4G_QUERY_ONLY_NOT_ENFORCED")
    return connection


def _validate_schema(connection: sqlite3.Connection) -> None:
    objects = connection.execute(
        "SELECT type,name FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
    ).fetchall()
    if {(row["type"], row["name"]) for row in objects} != {("table", name) for name in _SCHEMA}:
        raise OperationalFamilyExtractorError("EB0_4G_SCHEMA_OBJECT_MISMATCH")
    for table, expected in _SCHEMA.items():
        actual = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        if actual != expected:
            raise OperationalFamilyExtractorError(f"EB0_4G_SCHEMA_COLUMN_MISMATCH_{table.upper()}")


def _timed(connection: sqlite3.Connection, sql: str, params: tuple[object, ...], *, clock: Callable[[], float], limit: float) -> list[sqlite3.Row]:
    deadline = clock() + limit
    reached = False
    def interrupt() -> int:
        nonlocal reached
        reached = clock() >= deadline
        return int(reached)
    connection.set_progress_handler(interrupt, 1_000)
    try:
        rows = connection.execute(sql, params).fetchall()
        if clock() >= deadline:
            raise OperationalFamilyExtractorError("EB0_4G_QUERY_TIMEOUT")
        return rows
    except sqlite3.OperationalError as exc:
        if reached and "interrupted" in str(exc).lower():
            raise OperationalFamilyExtractorError("EB0_4G_QUERY_TIMEOUT") from exc
        raise
    finally:
        connection.set_progress_handler(None, 0)


def _array(row: sqlite3.Row, name: str) -> list[str]:
    try:
        value = json.loads(row[name])
    except (TypeError, json.JSONDecodeError) as exc:
        raise OperationalFamilyExtractorError("EB0_4G_INVALID_FEATURE_JSON") from exc
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise OperationalFamilyExtractorError("EB0_4G_INVALID_FEATURE_JSON")
    return value


def extract_operational_families(
    source_path: Path, *, max_query_seconds: float = MAX_QUERY_SECONDS,
    clock: Callable[[], float] = time.monotonic,
) -> OperationalFamilyExtraction:
    if max_query_seconds <= 0 or max_query_seconds > MAX_QUERY_SECONDS:
        raise OperationalFamilyExtractorError("EB0_4G_INVALID_QUERY_BOUND")
    connection = _open(Path(source_path))
    try:
        _validate_schema(connection)
        cohort = _timed(connection, "SELECT position,operation_id FROM operation_cohort ORDER BY position LIMIT ?", (MAX_OPERATIONS + 1,), clock=clock, limit=max_query_seconds)
        if not cohort or len(cohort) > MAX_OPERATIONS:
            raise OperationalFamilyExtractorError("EB0_4G_INVALID_COHORT_SIZE")
        positions = [row["position"] for row in cohort]
        selected = tuple(str(row["operation_id"] or "").strip() for row in cohort)
        if positions != list(range(len(cohort))) or any(not item for item in selected) or len(set(selected)) != len(selected):
            raise OperationalFamilyExtractorError("EB0_4G_INVALID_COHORT")
        placeholders = ",".join("?" for _ in selected)
        evidence = _timed(connection, f"SELECT * FROM normalized_operation_runtime WHERE operation_id IN ({placeholders}) ORDER BY operation_id,input_digest LIMIT ?", (*selected, MAX_EVIDENCE_ROWS + 1), clock=clock, limit=max_query_seconds)
        memberships = _timed(connection, f"SELECT * FROM nomination_candidates WHERE operation_id IN ({placeholders}) ORDER BY group_id,position,operation_id LIMIT ?", (*selected, MAX_MEMBERSHIPS + 1), clock=clock, limit=max_query_seconds)
        if len(evidence) > MAX_EVIDENCE_ROWS or len(memberships) > MAX_MEMBERSHIPS:
            raise OperationalFamilyExtractorError("EB0_4G_ROW_CEILING_EXCEEDED")
        facts_by_operation: dict[str, list[OperationBehaviourFact]] = {item: [] for item in selected}
        for row in evidence:
            record = {key: row[key] for key in _SCHEMA["normalized_operation_runtime"] if not key.endswith("_json")}
            record.update(
                edge_features=_array(row, "edge_features_json"),
                mechanism_features=_array(row, "mechanism_features_json"),
                temporal_features=_array(row, "temporal_features_json"),
            )
            try:
                facts_by_operation[row["operation_id"]].extend(adapt_normalized_operation_runtime(record))
            except Exception as exc:
                raise OperationalFamilyExtractorError("EB0_4G_ADAPTER_REJECTED") from exc
        excluded = {operation: "NO_NORMALIZED_RUNTIME_EVIDENCE" for operation, facts in facts_by_operation.items() if not facts}
        groups: dict[str, list[sqlite3.Row]] = {}
        for row in memberships:
            groups.setdefault(str(row["group_id"] or ""), []).append(row)
        if not groups or len(groups) > MAX_GROUPS or any(not key for key in groups):
            raise OperationalFamilyExtractorError("EB0_4G_INVALID_GROUP_COUNT")
        all_facts: dict[str, OperationBehaviourFact] = {}
        nominations = []
        for group_id in sorted(groups):
            rows = groups[group_id]
            group_positions = [row["position"] for row in rows]
            operation_ids = [str(row["operation_id"] or "").strip() for row in rows]
            states = {row["nomination_state"] for row in rows}
            if group_positions != list(range(len(rows))) or len(set(operation_ids)) != len(operation_ids) or len(operation_ids) < 2 or len(states) != 1:
                raise OperationalFamilyExtractorError("EB0_4G_INVALID_CANDIDATE_GROUP")
            if any(operation not in facts_by_operation or not facts_by_operation[operation] for operation in operation_ids):
                raise OperationalFamilyExtractorError("EB0_4G_ORPHAN_CANDIDATE_MEMBERSHIP")
            group_facts = tuple(fact for operation in operation_ids for fact in facts_by_operation[operation])
            try:
                nominations.append(nominate_operational_family(group_facts, nomination_state=next(iter(states))))
            except Exception as exc:
                raise OperationalFamilyExtractorError("EB0_4G_NOMINATION_REJECTED") from exc
            all_facts.update({fact.fact_id: fact for fact in group_facts})
        manifest = build_operational_family_manifest(all_facts.values(), nominations)
        corpora = assemble_operational_family_corpora([manifest])
        qualified = tuple(operation for operation in selected if operation not in excluded)
        body = {
            "schema_version": EXTRACTOR_SCHEMA_VERSION,
            "selected_operation_ids": selected,
            "qualified_operation_ids": qualified,
            "excluded_operations": dict(sorted(excluded.items())),
            "candidate_group_count": len(groups),
            "fact_count": manifest.fact_count,
            "nomination_count": manifest.nomination_count,
            "conflict_count": manifest.conflicting_fact_count,
            "manifests": [manifest.manifest_digest],
            "corpora": [item.corpus_digest for item in corpora],
            "input_fingerprint": _digest({"cohort": [dict(row) for row in cohort], "evidence": [dict(row) for row in evidence], "memberships": [dict(row) for row in memberships]}),
        }
        return OperationalFamilyExtraction(
            **{key: body[key] for key in body if key not in {"manifests", "corpora"}},
            manifests=(manifest,), corpora=corpora, result_digest=_digest(body),
        )
    finally:
        connection.close()
