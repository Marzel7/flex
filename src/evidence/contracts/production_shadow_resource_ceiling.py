"""PSI0A-E immutable resource ceilings for a future production shadow read."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import re
from typing import Mapping, Tuple


CONTRACT_VERSION = "psi0a-e.v1"
AUTHORITY_CLASS = "NON_EXECUTING_PRODUCTION_SHADOW_RESOURCE_CEILING"
ENGINEERING_REVISION = "91fddb0cfb55e30e55164b83f67135ef6bda571a"
QUERY_PLAN_CONTRACT_DIGEST = "8ba0259d356c3fd6300f22dbf08b6ca3ea96fd836f94221d4f2499949de4577c"
PLAN_QUALIFICATION_DIGEST = "38d0605e77e1503e9d5e952d13a3e1501aacf6c84db7a3debc334bca8fc484ce"
CANONICAL_MANIFEST_DIGEST = "d956bc24c1cd160162acaaad5bc466a2dece78ea34fc1f5238bc80728d4283f5"
READ_BOUNDARY_DIGEST = "fdf11dc5e29c176d3724a4ccd1e3ff56584727512853bfb58a71fb3979c246f8"
QUERY_IDS = (
    "creator_selected_cohort",
    "evidence_launch_facts",
    "main_selected_cohort",
    "ops_selected_cohort",
    "snapshot_selected_cohort",
)
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


class ProductionShadowResourceCeilingError(RuntimeError):
    """Named fail-closed PSI0A-E contract violation."""


@dataclass(frozen=True)
class QueryResourceCeiling:
    query_id: str
    maximum_rows: int
    maximum_canonical_bytes: int
    maximum_query_seconds: float
    maximum_transaction_seconds: float
    maximum_temporary_bytes: int
    permits_bounded_temporary_ordering: bool


@dataclass(frozen=True)
class ProductionShadowResourceCeilingContract:
    contract_version: str
    engineering_revision: str
    query_plan_contract_digest: str
    plan_qualification_digest: str
    canonical_manifest_digest: str
    read_boundary_digest: str
    authority_class: str
    query_ceilings: Tuple[QueryResourceCeiling, ...]
    maximum_total_rows: int
    maximum_total_canonical_bytes: int
    maximum_wall_seconds: float
    maximum_connections_opened: int
    maximum_concurrent_connections: int
    maximum_process_rss_delta_bytes: int
    maximum_sqlite_temporary_bytes: int
    pagination_allowed: bool
    retry_allowed: bool
    failover_allowed: bool
    adaptive_widening_allowed: bool
    grants_extraction_authority: bool
    grants_activation_authority: bool
    contract_digest: str


@dataclass(frozen=True)
class ResourceUsageProposal:
    query_rows: Tuple[Tuple[str, int], ...]
    query_canonical_bytes: Tuple[Tuple[str, int], ...]
    query_seconds: Tuple[Tuple[str, float], ...]
    transaction_seconds: Tuple[Tuple[str, float], ...]
    query_temporary_bytes: Tuple[Tuple[str, int], ...]
    total_wall_seconds: float
    connections_opened: int
    maximum_concurrent_connections: int
    process_rss_delta_bytes: int
    sqlite_temporary_bytes: int
    pagination_attempts: int = 0
    retry_attempts: int = 0
    failover_attempts: int = 0
    adaptive_limit_changes: int = 0


def _digest(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _positive_number(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and value > 0


def _ceiling_rows() -> Tuple[QueryResourceCeiling, ...]:
    ordinary = dict(
        maximum_rows=5_000,
        maximum_canonical_bytes=8 * 1024 * 1024,
        maximum_query_seconds=5.0,
        maximum_transaction_seconds=6.0,
        maximum_temporary_bytes=0,
        permits_bounded_temporary_ordering=False,
    )
    rows = [QueryResourceCeiling(query_id=query_id, **ordinary) for query_id in QUERY_IDS[:-1]]
    rows.append(QueryResourceCeiling(
        query_id="snapshot_selected_cohort",
        maximum_rows=5_000,
        maximum_canonical_bytes=16 * 1024 * 1024,
        maximum_query_seconds=5.0,
        maximum_transaction_seconds=6.0,
        maximum_temporary_bytes=32 * 1024 * 1024,
        permits_bounded_temporary_ordering=True,
    ))
    return tuple(rows)


def build_production_shadow_resource_ceiling_contract() -> ProductionShadowResourceCeilingContract:
    query_ceilings = _ceiling_rows()
    values = {
        "contract_version": CONTRACT_VERSION,
        "engineering_revision": ENGINEERING_REVISION,
        "query_plan_contract_digest": QUERY_PLAN_CONTRACT_DIGEST,
        "plan_qualification_digest": PLAN_QUALIFICATION_DIGEST,
        "canonical_manifest_digest": CANONICAL_MANIFEST_DIGEST,
        "read_boundary_digest": READ_BOUNDARY_DIGEST,
        "authority_class": AUTHORITY_CLASS,
        "query_ceilings": query_ceilings,
        "maximum_total_rows": 25_000,
        "maximum_total_canonical_bytes": 48 * 1024 * 1024,
        "maximum_wall_seconds": 30.0,
        "maximum_connections_opened": 5,
        "maximum_concurrent_connections": 1,
        "maximum_process_rss_delta_bytes": 128 * 1024 * 1024,
        "maximum_sqlite_temporary_bytes": 32 * 1024 * 1024,
        "pagination_allowed": False,
        "retry_allowed": False,
        "failover_allowed": False,
        "adaptive_widening_allowed": False,
        "grants_extraction_authority": False,
        "grants_activation_authority": False,
    }
    body = {**values, "query_ceilings": [asdict(item) for item in query_ceilings]}
    return ProductionShadowResourceCeilingContract(
        **values, contract_digest=_digest(body),
    )


def verify_production_shadow_resource_ceiling_contract(
    contract: ProductionShadowResourceCeilingContract,
) -> bool:
    expected = build_production_shadow_resource_ceiling_contract()
    if contract != expected:
        raise ProductionShadowResourceCeilingError("PSI0A_E_CONTRACT_REPLAY_MISMATCH")
    digests = (
        contract.query_plan_contract_digest,
        contract.plan_qualification_digest,
        contract.canonical_manifest_digest,
        contract.read_boundary_digest,
        contract.contract_digest,
    )
    if any(not _DIGEST.fullmatch(item) for item in digests):
        raise ProductionShadowResourceCeilingError("PSI0A_E_INVALID_BOUND_IDENTITY")
    if tuple(item.query_id for item in contract.query_ceilings) != QUERY_IDS:
        raise ProductionShadowResourceCeilingError("PSI0A_E_QUERY_SURFACE_DRIFT")
    numeric = (
        contract.maximum_total_rows,
        contract.maximum_total_canonical_bytes,
        contract.maximum_wall_seconds,
        contract.maximum_connections_opened,
        contract.maximum_concurrent_connections,
        contract.maximum_process_rss_delta_bytes,
        contract.maximum_sqlite_temporary_bytes,
    )
    if not all(_positive_number(value) for value in numeric):
        raise ProductionShadowResourceCeilingError("PSI0A_E_NON_POSITIVE_CEILING")
    if any((contract.pagination_allowed, contract.retry_allowed, contract.failover_allowed,
            contract.adaptive_widening_allowed, contract.grants_extraction_authority,
            contract.grants_activation_authority)):
        raise ProductionShadowResourceCeilingError("PSI0A_E_AUTHORITY_OR_WIDENING_DRIFT")
    snapshot = contract.query_ceilings[-1]
    if not snapshot.permits_bounded_temporary_ordering or snapshot.maximum_temporary_bytes <= 0:
        raise ProductionShadowResourceCeilingError("PSI0A_E_SNAPSHOT_TEMP_WORK_UNBOUNDED")
    if any(item.maximum_temporary_bytes != 0 for item in contract.query_ceilings[:-1]):
        raise ProductionShadowResourceCeilingError("PSI0A_E_UNEXPECTED_TEMP_WORK_AUTHORITY")
    return True


def _exact_usage_map(values: Tuple[Tuple[str, object], ...], reason: str) -> Mapping[str, object]:
    mapped = dict(values)
    if len(mapped) != len(values) or tuple(sorted(mapped)) != tuple(sorted(QUERY_IDS)):
        raise ProductionShadowResourceCeilingError(reason)
    return mapped


def validate_resource_usage_proposal(
    contract: ProductionShadowResourceCeilingContract,
    proposal: ResourceUsageProposal,
) -> bool:
    verify_production_shadow_resource_ceiling_contract(contract)
    rows = _exact_usage_map(proposal.query_rows, "PSI0A_E_QUERY_ROW_ACCOUNTING_MISMATCH")
    sizes = _exact_usage_map(proposal.query_canonical_bytes, "PSI0A_E_QUERY_BYTE_ACCOUNTING_MISMATCH")
    times = _exact_usage_map(proposal.query_seconds, "PSI0A_E_QUERY_DEADLINE_ACCOUNTING_MISMATCH")
    transactions = _exact_usage_map(
        proposal.transaction_seconds, "PSI0A_E_TRANSACTION_ACCOUNTING_MISMATCH"
    )
    temporary = _exact_usage_map(
        proposal.query_temporary_bytes, "PSI0A_E_TEMPORARY_ACCOUNTING_MISMATCH"
    )
    if any((proposal.pagination_attempts, proposal.retry_attempts, proposal.failover_attempts,
            proposal.adaptive_limit_changes)):
        raise ProductionShadowResourceCeilingError("PSI0A_E_WIDENING_OR_RETRY_PROHIBITED")
    for ceiling in contract.query_ceilings:
        query_id = ceiling.query_id
        if not isinstance(rows[query_id], int) or isinstance(rows[query_id], bool) or not 0 <= rows[query_id] <= ceiling.maximum_rows:
            raise ProductionShadowResourceCeilingError("PSI0A_E_QUERY_ROW_CEILING_EXCEEDED")
        if not isinstance(sizes[query_id], int) or isinstance(sizes[query_id], bool) or not 0 <= sizes[query_id] <= ceiling.maximum_canonical_bytes:
            raise ProductionShadowResourceCeilingError("PSI0A_E_QUERY_BYTE_CEILING_EXCEEDED")
        if not _positive_number(times[query_id]) or times[query_id] > ceiling.maximum_query_seconds:
            raise ProductionShadowResourceCeilingError("PSI0A_E_QUERY_DEADLINE_EXCEEDED")
        if not _positive_number(transactions[query_id]) or transactions[query_id] > ceiling.maximum_transaction_seconds:
            raise ProductionShadowResourceCeilingError("PSI0A_E_TRANSACTION_LIFETIME_EXCEEDED")
        if not isinstance(temporary[query_id], int) or isinstance(temporary[query_id], bool) or not 0 <= temporary[query_id] <= ceiling.maximum_temporary_bytes:
            raise ProductionShadowResourceCeilingError("PSI0A_E_QUERY_TEMPORARY_CEILING_EXCEEDED")
    if sum(rows.values()) > contract.maximum_total_rows:
        raise ProductionShadowResourceCeilingError("PSI0A_E_TOTAL_ROW_CEILING_EXCEEDED")
    if sum(sizes.values()) > contract.maximum_total_canonical_bytes:
        raise ProductionShadowResourceCeilingError("PSI0A_E_TOTAL_BYTE_CEILING_EXCEEDED")
    totals = (
        (proposal.total_wall_seconds, contract.maximum_wall_seconds, "PSI0A_E_WALL_DEADLINE_EXCEEDED"),
        (proposal.connections_opened, contract.maximum_connections_opened, "PSI0A_E_CONNECTION_CEILING_EXCEEDED"),
        (proposal.maximum_concurrent_connections, contract.maximum_concurrent_connections, "PSI0A_E_CONCURRENT_CONNECTION_CEILING_EXCEEDED"),
        (proposal.process_rss_delta_bytes, contract.maximum_process_rss_delta_bytes, "PSI0A_E_MEMORY_CEILING_EXCEEDED"),
        (proposal.sqlite_temporary_bytes, contract.maximum_sqlite_temporary_bytes, "PSI0A_E_SQLITE_TEMPORARY_CEILING_EXCEEDED"),
    )
    for actual, maximum, reason in totals:
        if not _positive_number(actual) or actual > maximum:
            raise ProductionShadowResourceCeilingError(reason)
    if proposal.sqlite_temporary_bytes != sum(temporary.values()):
        raise ProductionShadowResourceCeilingError("PSI0A_E_TEMPORARY_ACCOUNTING_RESIDUAL")
    return True
