"""PSI0B-D immutable production-specific authorization and path binding."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Mapping, Tuple

from .production_shadow_fixture_runner import (
    BOUND_COHORT_DIGEST,
    BOUND_FACT_FAMILY,
    BOUND_PREFLIGHT_DIGEST,
    EB0_1P_BUNDLE_DIGEST,
    PSI0B_A_CONTRACT_DIGEST,
    RUNNER_VERSION,
    SELECTED_PROJECTION_DIGEST,
    FixtureShadowBundle,
    _execute_bound_shadow,
    build_fixture_runner_contract,
)
from .production_shadow_health_gate import HealthGateDecision
from .production_shadow_query_plan import build_psi0a_d2a_rebound_contract
from .production_shadow_resource_ceiling import build_production_shadow_resource_ceiling_contract
from .production_shadow_run_preflight import (
    PATH_BINDINGS, QUERY_BOUNDARIES, ProductionShadowRunPreflight,
    verify_production_shadow_run_preflight,
)


ADAPTER_VERSION = "psi0b-d.v1"
ENGINEERING_REVISION = "763a07473c7afb6f0f7e4f52dc8d7074632185c7"
PSI0B_C_CONTRACT_DIGEST = "fb320f28ec0f7fcb5ea2e27248a75fbe3dc024ca309dd2b94c6220be352ad8f7"
AUTHORITY_CLASS = "HUMAN_APPROVED_ONE_RUN_QUERY_ONLY_PRODUCTION_SHADOW"
PRODUCTION_PATHS = {
    "creator": "/Users/kevinkeaveney/Dev/claude/flex/pumpswap_tokens.db",
    "evidence": "/Users/kevinkeaveney/Dev/claude/flex/database/evidence_platform/production/evidence.db",
    "main": "/Users/kevinkeaveney/Dev/claude/flex/database/flex_complete_database.db",
    "ops": "/Users/kevinkeaveney/Dev/claude/flex/database/wt_ops_v2.db",
}
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_BUNDLE_FILES = {"run.json", "accounting.json", "results.json", "hashes.json"}


class ProductionShadowProductionBindingError(RuntimeError):
    """Named fail-closed PSI0B-D binding violation."""


@dataclass(frozen=True)
class ProductionSourceBinding:
    logical_source: str
    absolute_path: str
    expected_filename: str
    logical_path_fingerprint: str


@dataclass(frozen=True)
class ProductionExecutionAuthorization:
    adapter_version: str
    authorization_id: str
    run_id: str
    authority_class: str
    engineering_revision: str
    psi0b_c_runner_version: str
    psi0b_c_contract_digest: str
    psi0b_a_contract_digest: str
    bound_preflight_digest: str
    eb0_1p_bundle_digest: str
    selected_projection_digest: str
    cohort_digest: str
    fact_family: str
    source_bindings: Tuple[ProductionSourceBinding, ...]
    output_directory: str
    output_directory_fingerprint: str
    query_identity_digest: str
    resource_ceiling_digest: str
    uri_mode: str
    query_only_required: bool
    lock_timeout_ms: int
    sequential_single_connections: bool
    progress_deadlines_required: bool
    rollback_handler_removal_close_required: bool
    maximum_attempts: int
    retries_allowed: bool
    pagination_allowed: bool
    failover_allowed: bool
    widening_allowed: bool
    grants_one_production_shadow_execution: bool
    grants_integration_authority: bool
    grants_activation_authority: bool
    authorization_digest: str


@dataclass(frozen=True)
class BoundProductionInvocation:
    authorization_digest: str
    run_id: str
    source_bindings: Tuple[ProductionSourceBinding, ...]
    output_directory: str
    query_ids: Tuple[str, ...]
    rowid_upper_inclusive: Tuple[Tuple[str, int], ...]
    health_gate_required: bool
    abort_isolation_required: bool
    consumable_attempts: int
    invocation_digest: str


def _digest(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def canonical_production_source_bindings() -> Tuple[ProductionSourceBinding, ...]:
    return tuple(
        ProductionSourceBinding(
            logical_source=source,
            absolute_path=PRODUCTION_PATHS[source],
            expected_filename=PATH_BINDINGS[source][0],
            logical_path_fingerprint=PATH_BINDINGS[source][1],
        )
        for source in sorted(PRODUCTION_PATHS)
    )


def _query_identity_digest() -> str:
    contract = build_psi0a_d2a_rebound_contract()
    return _digest(tuple((row.query_id, row.database_id, row.sql, row.parameter_names) for row in contract.templates))


def production_binding_contract_digest() -> str:
    """Return the run-independent identity of the PSI0B-D binding schema."""
    ceiling = build_production_shadow_resource_ceiling_contract()
    return _digest({
        "adapter_version": ADAPTER_VERSION,
        "engineering_revision": ENGINEERING_REVISION,
        "psi0b_c_contract_digest": PSI0B_C_CONTRACT_DIGEST,
        "upstream": (
            PSI0B_A_CONTRACT_DIGEST, BOUND_PREFLIGHT_DIGEST,
            EB0_1P_BUNDLE_DIGEST, SELECTED_PROJECTION_DIGEST,
            BOUND_COHORT_DIGEST, BOUND_FACT_FAMILY,
        ),
        "source_bindings": tuple(asdict(row) for row in canonical_production_source_bindings()),
        "query_identity_digest": _query_identity_digest(),
        "query_boundaries": tuple(sorted(QUERY_BOUNDARIES.items())),
        "resource_ceiling_digest": ceiling.contract_digest,
        "connection_policy": ("ro", True, 250, True, True, True),
        "attempt_policy": (1, False, False, False, False),
        "authority": (AUTHORITY_CLASS, True, False, False),
    })


def build_production_execution_authorization(
    *, authorization_id: str, run_id: str, output_directory: Path,
    source_paths: Mapping[str, str] = PRODUCTION_PATHS,
    authority_class: str = AUTHORITY_CLASS,
) -> ProductionExecutionAuthorization:
    if not _IDENTIFIER.fullmatch(authorization_id) or not _IDENTIFIER.fullmatch(run_id):
        raise ProductionShadowProductionBindingError("PSI0B_D_INVALID_AUTHORIZATION_OR_RUN_ID")
    if authority_class != AUTHORITY_CLASS:
        raise ProductionShadowProductionBindingError("PSI0B_D_FIXTURE_OR_UNKNOWN_AUTHORIZATION_REJECTED")
    if dict(source_paths) != PRODUCTION_PATHS:
        raise ProductionShadowProductionBindingError("PSI0B_D_PRODUCTION_PATH_BINDING_DRIFT")
    output = Path(output_directory)
    if output.exists():
        raise ProductionShadowProductionBindingError("PSI0B_D_OUTPUT_NOT_NEW")
    resolved_output = str(output.resolve())
    if resolved_output in PRODUCTION_PATHS.values():
        raise ProductionShadowProductionBindingError("PSI0B_D_OUTPUT_OVERLAPS_SOURCE")
    ceiling = build_production_shadow_resource_ceiling_contract()
    values = {
        "adapter_version": ADAPTER_VERSION,
        "authorization_id": authorization_id,
        "run_id": run_id,
        "authority_class": authority_class,
        "engineering_revision": ENGINEERING_REVISION,
        "psi0b_c_runner_version": RUNNER_VERSION,
        "psi0b_c_contract_digest": PSI0B_C_CONTRACT_DIGEST,
        "psi0b_a_contract_digest": PSI0B_A_CONTRACT_DIGEST,
        "bound_preflight_digest": BOUND_PREFLIGHT_DIGEST,
        "eb0_1p_bundle_digest": EB0_1P_BUNDLE_DIGEST,
        "selected_projection_digest": SELECTED_PROJECTION_DIGEST,
        "cohort_digest": BOUND_COHORT_DIGEST,
        "fact_family": BOUND_FACT_FAMILY,
        "source_bindings": canonical_production_source_bindings(),
        "output_directory": resolved_output,
        "output_directory_fingerprint": _digest({"resolved_output_directory": resolved_output, "run_id": run_id}),
        "query_identity_digest": _query_identity_digest(),
        "resource_ceiling_digest": ceiling.contract_digest,
        "uri_mode": "ro",
        "query_only_required": True,
        "lock_timeout_ms": 250,
        "sequential_single_connections": True,
        "progress_deadlines_required": True,
        "rollback_handler_removal_close_required": True,
        "maximum_attempts": 1,
        "retries_allowed": False,
        "pagination_allowed": False,
        "failover_allowed": False,
        "widening_allowed": False,
        "grants_one_production_shadow_execution": True,
        "grants_integration_authority": False,
        "grants_activation_authority": False,
    }
    body = asdict(ProductionExecutionAuthorization(**values, authorization_digest=""))
    body.pop("authorization_digest")
    return ProductionExecutionAuthorization(**values, authorization_digest=_digest(body))


def verify_production_execution_authorization(record: ProductionExecutionAuthorization) -> bool:
    if record.authority_class != AUTHORITY_CLASS:
        raise ProductionShadowProductionBindingError("PSI0B_D_FIXTURE_OR_UNKNOWN_AUTHORIZATION_REJECTED")
    if not _IDENTIFIER.fullmatch(record.authorization_id) or not _IDENTIFIER.fullmatch(record.run_id):
        raise ProductionShadowProductionBindingError("PSI0B_D_INVALID_AUTHORIZATION_OR_RUN_ID")
    fixed = (
        record.adapter_version, record.engineering_revision, record.psi0b_c_runner_version,
        record.psi0b_c_contract_digest, record.psi0b_a_contract_digest,
        record.bound_preflight_digest, record.eb0_1p_bundle_digest,
        record.selected_projection_digest, record.cohort_digest, record.fact_family,
    )
    expected_fixed = (
        ADAPTER_VERSION, ENGINEERING_REVISION, RUNNER_VERSION, PSI0B_C_CONTRACT_DIGEST,
        PSI0B_A_CONTRACT_DIGEST, BOUND_PREFLIGHT_DIGEST, EB0_1P_BUNDLE_DIGEST,
        SELECTED_PROJECTION_DIGEST, BOUND_COHORT_DIGEST, BOUND_FACT_FAMILY,
    )
    if fixed != expected_fixed or record.source_bindings != canonical_production_source_bindings():
        raise ProductionShadowProductionBindingError("PSI0B_D_AUTHORIZATION_REPLAY_MISMATCH")
    expected_output_fingerprint = _digest({
        "resolved_output_directory": record.output_directory,
        "run_id": record.run_id,
    })
    ceiling = build_production_shadow_resource_ceiling_contract()
    if (record.output_directory_fingerprint != expected_output_fingerprint or
            record.query_identity_digest != _query_identity_digest() or
            record.resource_ceiling_digest != ceiling.contract_digest or
            record.uri_mode != "ro" or record.lock_timeout_ms != 250 or
            not all((record.query_only_required, record.sequential_single_connections,
                     record.progress_deadlines_required,
                     record.rollback_handler_removal_close_required))):
        raise ProductionShadowProductionBindingError("PSI0B_D_AUTHORIZATION_REPLAY_MISMATCH")
    body = asdict(record)
    authorization_digest = body.pop("authorization_digest")
    if authorization_digest != _digest(body):
        raise ProductionShadowProductionBindingError("PSI0B_D_AUTHORIZATION_REPLAY_MISMATCH")
    if not _DIGEST.fullmatch(record.authorization_digest):
        raise ProductionShadowProductionBindingError("PSI0B_D_INVALID_AUTHORIZATION_DIGEST")
    if any((record.retries_allowed, record.pagination_allowed, record.failover_allowed,
            record.widening_allowed, record.grants_integration_authority,
            record.grants_activation_authority)):
        raise ProductionShadowProductionBindingError("PSI0B_D_AUTHORITY_OR_WIDENING_DRIFT")
    if not record.grants_one_production_shadow_execution or record.maximum_attempts != 1:
        raise ProductionShadowProductionBindingError("PSI0B_D_ONE_RUN_AUTHORITY_DRIFT")
    if build_fixture_runner_contract().contract_digest != record.psi0b_c_contract_digest:
        raise ProductionShadowProductionBindingError("PSI0B_D_PSI0B_C_LINEAGE_DRIFT")
    return True


def bind_production_invocation(record: ProductionExecutionAuthorization) -> BoundProductionInvocation:
    verify_production_execution_authorization(record)
    query_ids = tuple(row.query_id for row in build_psi0a_d2a_rebound_contract().templates)
    boundaries = tuple(sorted(QUERY_BOUNDARIES.items()))
    values = {
        "authorization_digest": record.authorization_digest,
        "run_id": record.run_id,
        "source_bindings": record.source_bindings,
        "output_directory": record.output_directory,
        "query_ids": query_ids,
        "rowid_upper_inclusive": boundaries,
        "health_gate_required": True,
        "abort_isolation_required": True,
        "consumable_attempts": 1,
    }
    body = asdict(BoundProductionInvocation(**values, invocation_digest=""))
    body.pop("invocation_digest")
    return BoundProductionInvocation(**values, invocation_digest=_digest(body))


def verify_bound_production_invocation(
    invocation: BoundProductionInvocation,
    record: ProductionExecutionAuthorization,
) -> bool:
    if invocation != bind_production_invocation(record):
        raise ProductionShadowProductionBindingError("PSI0B_D_INVOCATION_REPLAY_MISMATCH")
    return True


def execute_production_shadow(
    record: ProductionExecutionAuthorization,
    preflight: ProductionShadowRunPreflight,
    *,
    prestart_health: HealthGateDecision,
    active_health_check,
    clock,
    resource_probe=lambda: (1, 0),
    lifecycle_event=lambda _query, _event: None,
    progress_steps: int = 1_000,
) -> FixtureShadowBundle:
    """Consume one exact D record; callers require separate live authorization."""
    verify_production_execution_authorization(record)
    verify_production_shadow_run_preflight(preflight)
    if preflight.preflight_digest != BOUND_PREFLIGHT_DIGEST:
        raise ProductionShadowProductionBindingError("PSI0B_D_BOUND_PREFLIGHT_MISMATCH")
    if preflight.cohort.cohort_digest != BOUND_COHORT_DIGEST:
        raise ProductionShadowProductionBindingError("PSI0B_D_COHORT_MISMATCH")
    if preflight.run_id != record.run_id:
        raise ProductionShadowProductionBindingError("PSI0B_D_RUN_ID_MISMATCH")
    if preflight.output_directory_fingerprint != record.output_directory_fingerprint:
        raise ProductionShadowProductionBindingError("PSI0B_D_OUTPUT_FINGERPRINT_MISMATCH")
    source_paths = {row.logical_source: Path(row.absolute_path) for row in record.source_bindings}
    bundle = _execute_bound_shadow(
        preflight, source_paths, Path(record.output_directory),
        prestart_health=prestart_health, active_health_check=active_health_check,
        fixture_root=None, authority_class=AUTHORITY_CLASS, fixture_only=False,
        grants_production_execution_authority=True,
        execution_authorization_digest=record.authorization_digest,
        output_runner_version=ADAPTER_VERSION, clock=clock,
        resource_probe=resource_probe, lifecycle_event=lifecycle_event,
        progress_steps=progress_steps,
    )
    verify_production_shadow_bundle(bundle.output_directory, record)
    return bundle


def verify_production_shadow_bundle(
    output_directory: Path,
    record: ProductionExecutionAuthorization,
) -> bool:
    verify_production_execution_authorization(record)
    output = Path(output_directory)
    if not output.is_dir() or {row.name for row in output.iterdir()} != _BUNDLE_FILES:
        raise ProductionShadowProductionBindingError("PSI0B_D_BUNDLE_FILE_SET_MISMATCH")
    try:
        run = json.loads((output / "run.json").read_text())
        hashes = json.loads((output / "hashes.json").read_text())
    except Exception as exc:
        raise ProductionShadowProductionBindingError("PSI0B_D_INVALID_BUNDLE_JSON") from exc
    if (run.get("runner_version") != ADAPTER_VERSION or
            run.get("authority_class") != AUTHORITY_CLASS or
            run.get("fixture_only") is not False or
            run.get("execution_authorization_digest") != record.authorization_digest or
            run.get("grants_production_execution_authority") is not True or
            run.get("grants_integration_authority") is not False or
            run.get("grants_activation_authority") is not False or
            hashes.get("runner_version") != ADAPTER_VERSION):
        raise ProductionShadowProductionBindingError("PSI0B_D_BUNDLE_AUTHORITY_DRIFT")
    data_files = _BUNDLE_FILES - {"hashes.json"}
    actual = {name: sha256((output / name).read_bytes()).hexdigest() for name in data_files}
    if hashes.get("files") != actual or hashes.get("bundle_digest") != sha256(
            (json.dumps(actual, sort_keys=True, separators=(",", ":")) + "\n").encode()
    ).hexdigest():
        raise ProductionShadowProductionBindingError("PSI0B_D_BUNDLE_DIGEST_MISMATCH")
    return True
