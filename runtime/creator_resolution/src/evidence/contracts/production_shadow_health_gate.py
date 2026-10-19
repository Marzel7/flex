"""PSI0A-F immutable health gates for a future production-shadow read."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import re
from typing import Tuple


CONTRACT_VERSION = "psi0a-f.v1"
CHECKPOINT_VERSION = "psi0a-f.checkpoint.v1"
AUTHORITY_CLASS = "NON_EXECUTING_PRODUCTION_SHADOW_HEALTH_GATE"
ENGINEERING_REVISION = "86ed505e8314270b3667218b58fbdd84b72b28b0"
RESOURCE_CEILING_CONTRACT_DIGEST = "f5eea8b9f8ba6b102f57e4ae59eb35eb8f0e23d3f8ac0f493f35671f8271f736"
PLAN_QUALIFICATION_DIGEST = "38d0605e77e1503e9d5e952d13a3e1501aacf6c84db7a3debc334bca8fc484ce"
CANONICAL_MANIFEST_DIGEST = "d956bc24c1cd160162acaaad5bc466a2dece78ea34fc1f5238bc80728d4283f5"
READ_BOUNDARY_DIGEST = "fdf11dc5e29c176d3724a4ccd1e3ff56584727512853bfb58a71fb3979c246f8"
HEALTHY = "HEALTHY"
RUNNING = "RUNNING"
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


class ProductionShadowHealthGateError(RuntimeError):
    """Invalid immutable contract or checkpoint rather than an unhealthy gate."""


@dataclass(frozen=True)
class ProductionShadowHealthGateContract:
    contract_version: str
    checkpoint_version: str
    engineering_revision: str
    resource_ceiling_contract_digest: str
    plan_qualification_digest: str
    canonical_manifest_digest: str
    read_boundary_digest: str
    authority_class: str
    maximum_checkpoint_age_seconds: float
    maximum_future_skew_seconds: float
    checkpoint_spacing_seconds: float
    checkpoint_spacing_tolerance_seconds: float
    required_prestart_checkpoints: int
    primary_fd_warning_threshold_exclusive: int
    serializer_p99_threshold_ms_exclusive: float
    sustained_queue_checkpoint_count: int
    retry_allowed: bool
    degraded_mode_bypass_allowed: bool
    grants_extraction_authority: bool
    grants_activation_authority: bool
    contract_digest: str


@dataclass(frozen=True)
class HealthCheckpoint:
    checkpoint_version: str
    observed_at_epoch: float
    listener_pid: int
    listener_service_state: str
    primary_fd_count: int
    critical_listener_db_handle_count: int
    serializer_p99_wait_ms: float
    serializer_lock_errors: int
    serializer_queue_depth: int
    database_wal_state: str
    write_lease_state: str
    pumpportal_state: str
    pumpswap_state: str
    ingestion_state: str
    worker_state: str
    queue_state: str
    service_state: str
    telemetry_complete: bool
    checkpoint_digest: str


@dataclass(frozen=True)
class HealthGateDecision:
    phase: str
    status: str
    reason_codes: Tuple[str, ...]
    checkpoint_digests: Tuple[str, ...]
    grants_extraction_authority: bool
    grants_activation_authority: bool
    decision_digest: str


def _digest(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def build_production_shadow_health_gate_contract() -> ProductionShadowHealthGateContract:
    body = {
        "contract_version": CONTRACT_VERSION,
        "checkpoint_version": CHECKPOINT_VERSION,
        "engineering_revision": ENGINEERING_REVISION,
        "resource_ceiling_contract_digest": RESOURCE_CEILING_CONTRACT_DIGEST,
        "plan_qualification_digest": PLAN_QUALIFICATION_DIGEST,
        "canonical_manifest_digest": CANONICAL_MANIFEST_DIGEST,
        "read_boundary_digest": READ_BOUNDARY_DIGEST,
        "authority_class": AUTHORITY_CLASS,
        "maximum_checkpoint_age_seconds": 45.0,
        "maximum_future_skew_seconds": 2.0,
        "checkpoint_spacing_seconds": 30.0,
        "checkpoint_spacing_tolerance_seconds": 2.0,
        "required_prestart_checkpoints": 3,
        "primary_fd_warning_threshold_exclusive": 8,
        "serializer_p99_threshold_ms_exclusive": 1000.0,
        "sustained_queue_checkpoint_count": 2,
        "retry_allowed": False,
        "degraded_mode_bypass_allowed": False,
        "grants_extraction_authority": False,
        "grants_activation_authority": False,
    }
    return ProductionShadowHealthGateContract(**body, contract_digest=_digest(body))


def verify_production_shadow_health_gate_contract(
    contract: ProductionShadowHealthGateContract,
) -> bool:
    if contract != build_production_shadow_health_gate_contract():
        raise ProductionShadowHealthGateError("PSI0A_F_CONTRACT_REPLAY_MISMATCH")
    for value in (
        contract.resource_ceiling_contract_digest,
        contract.plan_qualification_digest,
        contract.canonical_manifest_digest,
        contract.read_boundary_digest,
        contract.contract_digest,
    ):
        if not _DIGEST.fullmatch(value):
            raise ProductionShadowHealthGateError("PSI0A_F_INVALID_BOUND_IDENTITY")
    if any((contract.retry_allowed, contract.degraded_mode_bypass_allowed,
            contract.grants_extraction_authority, contract.grants_activation_authority)):
        raise ProductionShadowHealthGateError("PSI0A_F_AUTHORITY_OR_BYPASS_DRIFT")
    return True


def build_health_checkpoint(**values: object) -> HealthCheckpoint:
    body = {"checkpoint_version": CHECKPOINT_VERSION, **values}
    checkpoint = HealthCheckpoint(**body, checkpoint_digest=_digest(body))
    verify_health_checkpoint(checkpoint)
    return checkpoint


def verify_health_checkpoint(checkpoint: HealthCheckpoint) -> bool:
    body = asdict(checkpoint)
    digest = body.pop("checkpoint_digest")
    if checkpoint.checkpoint_version != CHECKPOINT_VERSION or digest != _digest(body):
        raise ProductionShadowHealthGateError("PSI0A_F_CHECKPOINT_REPLAY_MISMATCH")
    numeric = (
        checkpoint.observed_at_epoch,
        checkpoint.serializer_p99_wait_ms,
    )
    integers = (
        checkpoint.listener_pid,
        checkpoint.primary_fd_count,
        checkpoint.critical_listener_db_handle_count,
        checkpoint.serializer_lock_errors,
        checkpoint.serializer_queue_depth,
    )
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0 for value in numeric):
        raise ProductionShadowHealthGateError("PSI0A_F_CHECKPOINT_NUMERIC_INVALID")
    if checkpoint.listener_pid <= 0 or any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in integers
    ):
        raise ProductionShadowHealthGateError("PSI0A_F_CHECKPOINT_INTEGER_INVALID")
    states = (
        checkpoint.listener_service_state, checkpoint.database_wal_state,
        checkpoint.write_lease_state, checkpoint.pumpportal_state,
        checkpoint.pumpswap_state, checkpoint.ingestion_state, checkpoint.worker_state,
        checkpoint.queue_state, checkpoint.service_state,
    )
    if any(not isinstance(value, str) or not value for value in states):
        raise ProductionShadowHealthGateError("PSI0A_F_CHECKPOINT_STATE_MISSING")
    if not isinstance(checkpoint.telemetry_complete, bool):
        raise ProductionShadowHealthGateError("PSI0A_F_CHECKPOINT_COMPLETENESS_INVALID")
    return True


def _checkpoint_reasons(
    contract: ProductionShadowHealthGateContract,
    checkpoint: HealthCheckpoint,
    *,
    now_epoch: float,
    expected_listener_pid: int,
    baseline_lock_errors: int,
    enforce_freshness: bool = True,
) -> list[str]:
    verify_health_checkpoint(checkpoint)
    reasons = []
    if enforce_freshness:
        age = now_epoch - checkpoint.observed_at_epoch
        if age > contract.maximum_checkpoint_age_seconds:
            reasons.append("PSI0A_F_TELEMETRY_STALE")
        if age < -contract.maximum_future_skew_seconds:
            reasons.append("PSI0A_F_TELEMETRY_FROM_FUTURE")
    if not checkpoint.telemetry_complete:
        reasons.append("PSI0A_F_TELEMETRY_INCOMPLETE_OR_UNKNOWN")
    if checkpoint.listener_pid != expected_listener_pid:
        reasons.append("PSI0A_F_LISTENER_PID_CHANGED")
    if checkpoint.listener_service_state != RUNNING:
        reasons.append("PSI0A_F_LISTENER_NOT_RUNNING")
    if checkpoint.primary_fd_count >= contract.primary_fd_warning_threshold_exclusive:
        reasons.append("PSI0A_F_PRIMARY_FD_WARNING_THRESHOLD_REACHED")
    if checkpoint.critical_listener_db_handle_count != 0:
        reasons.append("PSI0A_F_CRITICAL_LISTENER_DB_HANDLE")
    if checkpoint.serializer_p99_wait_ms >= contract.serializer_p99_threshold_ms_exclusive:
        reasons.append("PSI0A_F_SERIALIZER_P99_THRESHOLD_REACHED")
    if checkpoint.serializer_lock_errors != baseline_lock_errors:
        reasons.append("PSI0A_F_SERIALIZER_LOCK_ERROR_INCREMENT")
    for field, value in (
        ("DATABASE_WAL", checkpoint.database_wal_state),
        ("WRITE_LEASE", checkpoint.write_lease_state),
        ("PUMPPORTAL", checkpoint.pumpportal_state),
        ("PUMPSWAP", checkpoint.pumpswap_state),
        ("INGESTION", checkpoint.ingestion_state),
        ("WORKER", checkpoint.worker_state),
        ("QUEUE", checkpoint.queue_state),
        ("SERVICE", checkpoint.service_state),
    ):
        if value != HEALTHY:
            reasons.append(f"PSI0A_F_{field}_UNHEALTHY_OR_UNKNOWN")
    return reasons


def _decision(phase: str, status: str, reasons: list[str], checkpoints: Tuple[HealthCheckpoint, ...]) -> HealthGateDecision:
    ordered = tuple(sorted(set(reasons)))
    body = {
        "phase": phase,
        "status": status,
        "reason_codes": ordered,
        "checkpoint_digests": tuple(item.checkpoint_digest for item in checkpoints),
        "grants_extraction_authority": False,
        "grants_activation_authority": False,
    }
    return HealthGateDecision(**body, decision_digest=_digest(body))


def verify_health_gate_decision(decision: HealthGateDecision) -> bool:
    body = asdict(decision)
    digest = body.pop("decision_digest")
    if digest != _digest(body):
        raise ProductionShadowHealthGateError("PSI0A_F_DECISION_REPLAY_MISMATCH")
    if decision.grants_extraction_authority or decision.grants_activation_authority:
        raise ProductionShadowHealthGateError("PSI0A_F_DECISION_AUTHORITY_DRIFT")
    return True


def evaluate_prestart_health_gate(
    contract: ProductionShadowHealthGateContract,
    checkpoints: Tuple[HealthCheckpoint, ...],
    *,
    now_epoch: float,
    baseline_lock_errors: int,
) -> HealthGateDecision:
    verify_production_shadow_health_gate_contract(contract)
    if len(checkpoints) != contract.required_prestart_checkpoints:
        raise ProductionShadowHealthGateError("PSI0A_F_PRESTART_CHECKPOINT_COUNT_MISMATCH")
    expected_pid = checkpoints[0].listener_pid
    reasons = []
    for position, checkpoint in enumerate(checkpoints):
        reasons.extend(_checkpoint_reasons(
            contract, checkpoint, now_epoch=now_epoch,
            expected_listener_pid=expected_pid, baseline_lock_errors=baseline_lock_errors,
            enforce_freshness=position == len(checkpoints) - 1,
        ))
    for previous, current in zip(checkpoints, checkpoints[1:]):
        spacing = current.observed_at_epoch - previous.observed_at_epoch
        if abs(spacing - contract.checkpoint_spacing_seconds) > contract.checkpoint_spacing_tolerance_seconds:
            reasons.append("PSI0A_F_CHECKPOINT_SPACING_INVALID")
    if checkpoints[-2].serializer_queue_depth > 0 and checkpoints[-1].serializer_queue_depth > 0:
        reasons.append("PSI0A_F_SUSTAINED_SERIALIZER_QUEUE")
    status = "DO_NOT_START" if reasons else "PASS"
    decision = _decision("PRESTART", status, reasons, checkpoints)
    verify_health_gate_decision(decision)
    return decision


def evaluate_active_health_gate(
    contract: ProductionShadowHealthGateContract,
    previous: HealthCheckpoint,
    current: HealthCheckpoint,
    *,
    now_epoch: float,
    expected_listener_pid: int,
    baseline_lock_errors: int,
) -> HealthGateDecision:
    verify_production_shadow_health_gate_contract(contract)
    reasons = _checkpoint_reasons(
        contract, current, now_epoch=now_epoch,
        expected_listener_pid=expected_listener_pid, baseline_lock_errors=baseline_lock_errors,
    )
    spacing = current.observed_at_epoch - previous.observed_at_epoch
    if previous.serializer_queue_depth > 0 and current.serializer_queue_depth > 0:
        if abs(spacing - contract.checkpoint_spacing_seconds) <= contract.checkpoint_spacing_tolerance_seconds:
            reasons.append("PSI0A_F_SUSTAINED_SERIALIZER_QUEUE")
        else:
            reasons.append("PSI0A_F_CHECKPOINT_SPACING_INVALID")
    status = "STOP" if reasons else "PASS"
    decision = _decision("ACTIVE", status, reasons, (previous, current))
    verify_health_gate_decision(decision)
    return decision
