"""PSI0A-G deterministic stop/abort and production-isolation proof."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import re
from typing import Tuple


CONTRACT_VERSION = "psi0a-g.v1"
TRACE_VERSION = "psi0a-g.trace.v1"
AUTHORITY_CLASS = "NON_EXECUTING_PRODUCTION_SHADOW_ABORT_ISOLATION_PROOF"
ENGINEERING_REVISION = "a624ad478a4effbb3c7ad6b951d7dbb85c7947c6"
HEALTH_GATE_CONTRACT_DIGEST = "8c92231a76c9daad4305bd3859760bc6f1d1ef31249b255b471c213f1ce1c3bf"
RESOURCE_CEILING_CONTRACT_DIGEST = "f5eea8b9f8ba6b102f57e4ae59eb35eb8f0e23d3f8ac0f493f35671f8271f736"
PLAN_QUALIFICATION_DIGEST = "38d0605e77e1503e9d5e952d13a3e1501aacf6c84db7a3debc334bca8fc484ce"
CANONICAL_MANIFEST_DIGEST = "d956bc24c1cd160162acaaad5bc466a2dece78ea34fc1f5238bc80728d4283f5"
READ_BOUNDARY_DIGEST = "fdf11dc5e29c176d3724a4ccd1e3ff56584727512853bfb58a71fb3979c246f8"
STOP_TRIGGERS = (
    "HEALTH_GATE_FAILURE",
    "QUERY_DEADLINE_BREACH",
    "RESOURCE_CEILING_BREACH",
    "SCHEMA_DRIFT",
    "PATH_DRIFT",
    "BOUNDARY_DRIFT",
    "CONTRACT_DRIFT",
    "SQLITE_INTERRUPTION",
    "SQLITE_EXCEPTION",
    "REPLAY_FAILURE",
    "OBSERVER_INSUFFICIENCY",
)
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


class ProductionShadowAbortIsolationError(RuntimeError):
    """Named fail-closed PSI0A-G contract or trace violation."""


@dataclass(frozen=True)
class ProductionShadowAbortIsolationContract:
    contract_version: str
    trace_version: str
    engineering_revision: str
    health_gate_contract_digest: str
    resource_ceiling_contract_digest: str
    plan_qualification_digest: str
    canonical_manifest_digest: str
    read_boundary_digest: str
    authority_class: str
    stop_triggers: Tuple[str, ...]
    retry_allowed: bool
    pagination_allowed: bool
    failover_allowed: bool
    degraded_mode_bypass_allowed: bool
    adaptive_limit_widening_allowed: bool
    partial_bundle_publication_allowed: bool
    grants_extraction_authority: bool
    grants_activation_authority: bool
    contract_digest: str


@dataclass(frozen=True)
class AbortIsolationTrace:
    trace_version: str
    phase: str
    stop_trigger: str
    health_gate_decision_digest: str
    connection_opened: bool
    transaction_started: bool
    progress_handler_installed: bool
    temporary_artifact_created: bool
    progress_handler_removed: bool
    rollback_attempted: bool
    transaction_resolved: bool
    connection_closed: bool
    temporary_artifact_removed: bool
    partial_bundle_published: bool
    production_write_count: int
    ddl_statement_count: int
    service_mutation_count: int
    configuration_mutation_count: int
    lock_mutation_count: int
    metric_mutation_count: int
    retry_attempts: int
    pagination_attempts: int
    failover_attempts: int
    degraded_bypass_attempts: int
    adaptive_limit_changes: int
    trace_digest: str


@dataclass(frozen=True)
class AbortIsolationDecision:
    phase: str
    terminal_state: str
    stop_trigger: str
    reason_codes: Tuple[str, ...]
    trace_digest: str
    isolation_proven: bool
    grants_extraction_authority: bool
    grants_activation_authority: bool
    decision_digest: str


def _digest(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def build_production_shadow_abort_isolation_contract() -> ProductionShadowAbortIsolationContract:
    body = {
        "contract_version": CONTRACT_VERSION,
        "trace_version": TRACE_VERSION,
        "engineering_revision": ENGINEERING_REVISION,
        "health_gate_contract_digest": HEALTH_GATE_CONTRACT_DIGEST,
        "resource_ceiling_contract_digest": RESOURCE_CEILING_CONTRACT_DIGEST,
        "plan_qualification_digest": PLAN_QUALIFICATION_DIGEST,
        "canonical_manifest_digest": CANONICAL_MANIFEST_DIGEST,
        "read_boundary_digest": READ_BOUNDARY_DIGEST,
        "authority_class": AUTHORITY_CLASS,
        "stop_triggers": STOP_TRIGGERS,
        "retry_allowed": False,
        "pagination_allowed": False,
        "failover_allowed": False,
        "degraded_mode_bypass_allowed": False,
        "adaptive_limit_widening_allowed": False,
        "partial_bundle_publication_allowed": False,
        "grants_extraction_authority": False,
        "grants_activation_authority": False,
    }
    return ProductionShadowAbortIsolationContract(**body, contract_digest=_digest(body))


def verify_production_shadow_abort_isolation_contract(
    contract: ProductionShadowAbortIsolationContract,
) -> bool:
    if contract != build_production_shadow_abort_isolation_contract():
        raise ProductionShadowAbortIsolationError("PSI0A_G_CONTRACT_REPLAY_MISMATCH")
    identities = (
        contract.health_gate_contract_digest,
        contract.resource_ceiling_contract_digest,
        contract.plan_qualification_digest,
        contract.canonical_manifest_digest,
        contract.read_boundary_digest,
        contract.contract_digest,
    )
    if any(not _DIGEST.fullmatch(value) for value in identities):
        raise ProductionShadowAbortIsolationError("PSI0A_G_INVALID_BOUND_IDENTITY")
    prohibited = (
        contract.retry_allowed,
        contract.pagination_allowed,
        contract.failover_allowed,
        contract.degraded_mode_bypass_allowed,
        contract.adaptive_limit_widening_allowed,
        contract.partial_bundle_publication_allowed,
        contract.grants_extraction_authority,
        contract.grants_activation_authority,
    )
    if any(prohibited):
        raise ProductionShadowAbortIsolationError("PSI0A_G_AUTHORITY_OR_BYPASS_DRIFT")
    if contract.stop_triggers != STOP_TRIGGERS:
        raise ProductionShadowAbortIsolationError("PSI0A_G_STOP_TRIGGER_DRIFT")
    return True


def build_abort_isolation_trace(**values: object) -> AbortIsolationTrace:
    body = {"trace_version": TRACE_VERSION, **values}
    trace = AbortIsolationTrace(**body, trace_digest=_digest(body))
    verify_abort_isolation_trace(trace)
    return trace


def verify_abort_isolation_trace(trace: AbortIsolationTrace) -> bool:
    body = asdict(trace)
    digest = body.pop("trace_digest")
    if trace.trace_version != TRACE_VERSION or digest != _digest(body):
        raise ProductionShadowAbortIsolationError("PSI0A_G_TRACE_REPLAY_MISMATCH")
    if trace.phase not in ("PRESTART", "ACTIVE"):
        raise ProductionShadowAbortIsolationError("PSI0A_G_UNKNOWN_PHASE")
    if trace.stop_trigger not in STOP_TRIGGERS:
        raise ProductionShadowAbortIsolationError("PSI0A_G_UNKNOWN_STOP_TRIGGER")
    if not _DIGEST.fullmatch(trace.health_gate_decision_digest):
        raise ProductionShadowAbortIsolationError("PSI0A_G_INVALID_HEALTH_DECISION_IDENTITY")
    boolean_fields = (
        trace.connection_opened, trace.transaction_started,
        trace.progress_handler_installed, trace.temporary_artifact_created,
        trace.progress_handler_removed, trace.rollback_attempted,
        trace.transaction_resolved, trace.connection_closed,
        trace.temporary_artifact_removed, trace.partial_bundle_published,
    )
    if any(not isinstance(value, bool) for value in boolean_fields):
        raise ProductionShadowAbortIsolationError("PSI0A_G_TRACE_BOOLEAN_INVALID")
    counters = (
        trace.production_write_count, trace.ddl_statement_count,
        trace.service_mutation_count, trace.configuration_mutation_count,
        trace.lock_mutation_count, trace.metric_mutation_count,
        trace.retry_attempts, trace.pagination_attempts, trace.failover_attempts,
        trace.degraded_bypass_attempts, trace.adaptive_limit_changes,
    )
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in counters):
        raise ProductionShadowAbortIsolationError("PSI0A_G_TRACE_COUNTER_INVALID")
    return True


def evaluate_abort_isolation(
    contract: ProductionShadowAbortIsolationContract,
    trace: AbortIsolationTrace,
) -> AbortIsolationDecision:
    verify_production_shadow_abort_isolation_contract(contract)
    verify_abort_isolation_trace(trace)
    reasons = []
    if trace.phase == "PRESTART":
        if any((trace.connection_opened, trace.transaction_started,
                trace.progress_handler_installed, trace.temporary_artifact_created)):
            reasons.append("PSI0A_G_PRESTART_RESOURCE_OPENED")
        terminal_state = "DO_NOT_START"
    else:
        terminal_state = "ABORTED"
        if trace.progress_handler_installed and not trace.progress_handler_removed:
            reasons.append("PSI0A_G_PROGRESS_HANDLER_NOT_REMOVED")
        if trace.transaction_started and not trace.rollback_attempted:
            reasons.append("PSI0A_G_ROLLBACK_NOT_ATTEMPTED")
        if trace.transaction_started and not trace.transaction_resolved:
            reasons.append("PSI0A_G_TRANSACTION_NOT_RESOLVED")
        if trace.connection_opened and not trace.connection_closed:
            reasons.append("PSI0A_G_CONNECTION_NOT_CLOSED")
        if trace.temporary_artifact_created and not trace.temporary_artifact_removed:
            reasons.append("PSI0A_G_TEMPORARY_ARTIFACT_NOT_REMOVED")
    mutations = (
        trace.production_write_count, trace.ddl_statement_count,
        trace.service_mutation_count, trace.configuration_mutation_count,
        trace.lock_mutation_count, trace.metric_mutation_count,
    )
    if any(mutations):
        reasons.append("PSI0A_G_PRODUCTION_ISOLATION_VIOLATION")
    if trace.partial_bundle_published:
        reasons.append("PSI0A_G_PARTIAL_BUNDLE_PUBLISHED")
    attempts = (
        trace.retry_attempts, trace.pagination_attempts, trace.failover_attempts,
        trace.degraded_bypass_attempts, trace.adaptive_limit_changes,
    )
    if any(attempts):
        reasons.append("PSI0A_G_RETRY_OR_SCOPE_WIDENING_ATTEMPTED")
    ordered = tuple(sorted(set(reasons)))
    isolation_proven = not ordered
    if not isolation_proven:
        terminal_state = "ISOLATION_FAILURE"
    body = {
        "phase": trace.phase,
        "terminal_state": terminal_state,
        "stop_trigger": trace.stop_trigger,
        "reason_codes": ordered,
        "trace_digest": trace.trace_digest,
        "isolation_proven": isolation_proven,
        "grants_extraction_authority": False,
        "grants_activation_authority": False,
    }
    decision = AbortIsolationDecision(**body, decision_digest=_digest(body))
    verify_abort_isolation_decision(decision)
    return decision


def verify_abort_isolation_decision(decision: AbortIsolationDecision) -> bool:
    body = asdict(decision)
    digest = body.pop("decision_digest")
    if digest != _digest(body):
        raise ProductionShadowAbortIsolationError("PSI0A_G_DECISION_REPLAY_MISMATCH")
    if decision.grants_extraction_authority or decision.grants_activation_authority:
        raise ProductionShadowAbortIsolationError("PSI0A_G_DECISION_AUTHORITY_DRIFT")
    return True
