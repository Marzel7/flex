"""PSI0B-E7 fail-closed launcher/bootstrap boundary for PSI0B-D."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Callable

from .production_shadow_health_gate import (
    HealthGateDecision,
    verify_health_gate_decision,
)
from .production_shadow_production_binding import (
    ProductionExecutionAuthorization,
    ProductionSourceBinding,
    production_binding_contract_digest,
    verify_production_execution_authorization,
)
from .production_shadow_run_preflight import (
    ProductionShadowRunPreflight,
    build_immutable_cohort_artifact,
    build_production_shadow_run_preflight,
    verify_production_shadow_run_preflight,
)
from .production_shadow_superseding_preflight import (
    verify_superseding_preflight,
)


LAUNCHER_VERSION = "psi0b-e7.v1"
AUTHORIZATION_SCHEMA = "psi0b-e.authorization.v1"
AUTHORITY_CLASS = "SAFE_LOCAL_LAUNCHER_BOOTSTRAP_NO_EXECUTION_AUTHORITY"
_DIGEST_FIELDS = {
    "production_binding_contract_digest": production_binding_contract_digest(),
}


class ProductionShadowLauncherError(RuntimeError):
    """Named fail-closed launcher/bootstrap violation."""


@dataclass(frozen=True)
class AuthorizationConsumption:
    launcher_version: str
    authorization_id: str
    authorization_digest: str
    run_id: str
    preflight_digest: str
    output_directory: str
    observer_decision_digest: str
    grants_integration_authority: bool
    grants_activation_authority: bool
    consumption_digest: str


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode() + b"\n"


def _digest(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def launcher_contract_digest() -> str:
    return _digest({
        "launcher_version": LAUNCHER_VERSION,
        "authorization_schema": AUTHORIZATION_SCHEMA,
        "authority_class": AUTHORITY_CLASS,
        "production_binding_contract_digest": _DIGEST_FIELDS["production_binding_contract_digest"],
        "bootstrap": "REPOSITORY_ROOT_FROM_COMMITTED_SCRIPT_PATH",
        "validation_order": (
            "AUTHORIZATION", "PREFLIGHT", "OUTPUT", "CONSUMPTION_LEDGER",
            "OBSERVER_PRESTART_PASS", "ATOMIC_CONSUMPTION", "EXECUTOR",
        ),
        "consumption_policy": ("OPEN_XB", "FLUSH", "FSYNC", "NO_REUSE"),
        "authority": (False, False),
    })


def _exact_keys(value: dict, expected: set[str], reason: str) -> None:
    if set(value) != expected:
        raise ProductionShadowLauncherError(reason)


def load_execution_authorization(path: Path) -> ProductionExecutionAuthorization:
    try:
        document = json.loads(Path(path).read_text())
    except Exception as exc:
        raise ProductionShadowLauncherError("PSI0B_E7_AUTHORIZATION_JSON_INVALID") from exc
    _exact_keys(
        document,
        {"schema_version", "engineering_commit", "production_binding_contract_digest", "authorization"},
        "PSI0B_E7_AUTHORIZATION_DOCUMENT_SHAPE_DRIFT",
    )
    if document["schema_version"] != AUTHORIZATION_SCHEMA:
        raise ProductionShadowLauncherError("PSI0B_E7_AUTHORIZATION_SCHEMA_DRIFT")
    if document["production_binding_contract_digest"] != _DIGEST_FIELDS["production_binding_contract_digest"]:
        raise ProductionShadowLauncherError("PSI0B_E7_BINDING_CONTRACT_DRIFT")
    values = document.get("authorization")
    if not isinstance(values, dict):
        raise ProductionShadowLauncherError("PSI0B_E7_AUTHORIZATION_DOCUMENT_SHAPE_DRIFT")
    try:
        values = dict(values)
        values["source_bindings"] = tuple(ProductionSourceBinding(**row) for row in values["source_bindings"])
        record = ProductionExecutionAuthorization(**values)
        verify_production_execution_authorization(record)
    except Exception as exc:
        raise ProductionShadowLauncherError("PSI0B_E7_AUTHORIZATION_REPLAY_FAILED") from exc
    return record


def load_superseding_preflight(
    artifact_directory: Path,
    authorization: ProductionExecutionAuthorization,
) -> ProductionShadowRunPreflight:
    try:
        verify_superseding_preflight(artifact_directory)
        cohort_document = json.loads((Path(artifact_directory) / "cohort.json").read_text())["cohort"]
        expected_preflight = json.loads((Path(artifact_directory) / "preflight.json").read_text())["preflight"]
        cohort = build_immutable_cohort_artifact(
            cohort_id=cohort_document["cohort_id"],
            mints=tuple(cohort_document["mints"]),
            source_artifact_digest=cohort_document["source_artifact_digest"],
        )
        preflight = build_production_shadow_run_preflight(
            run_id=authorization.run_id,
            cohort=cohort,
            fact_family=authorization.fact_family,
            output_directory=Path(authorization.output_directory),
        )
        verify_production_shadow_run_preflight(preflight)
    except Exception as exc:
        raise ProductionShadowLauncherError("PSI0B_E7_PREFLIGHT_REPLAY_FAILED") from exc
    if _canonical(asdict(preflight)) != _canonical(expected_preflight):
        raise ProductionShadowLauncherError("PSI0B_E7_PREFLIGHT_CANONICAL_DRIFT")
    if (preflight.preflight_digest != authorization.bound_preflight_digest or
            preflight.cohort.cohort_digest != authorization.cohort_digest):
        raise ProductionShadowLauncherError("PSI0B_E7_AUTHORIZATION_PREFLIGHT_LINEAGE_DRIFT")
    return preflight


def validate_bootstrap_inputs(
    authorization_path: Path,
    preflight_artifact_directory: Path,
    consumption_directory: Path,
    *,
    path_exists: Callable[[Path], bool] = Path.exists,
) -> tuple[ProductionExecutionAuthorization, ProductionShadowRunPreflight, Path]:
    record = load_execution_authorization(authorization_path)
    preflight = load_superseding_preflight(preflight_artifact_directory, record)
    output = Path(record.output_directory)
    if path_exists(output):
        raise ProductionShadowLauncherError("PSI0B_E7_OUTPUT_NOT_NEW")
    consumption_directory = Path(consumption_directory)
    if not consumption_directory.is_dir():
        raise ProductionShadowLauncherError("PSI0B_E7_CONSUMPTION_DIRECTORY_MISSING")
    marker = consumption_directory / f"{record.authorization_id}.consumed.json"
    if marker.exists():
        raise ProductionShadowLauncherError("PSI0B_E7_AUTHORIZATION_ALREADY_CONSUMED")
    return record, preflight, marker


def _consume_authorization(
    marker: Path,
    record: ProductionExecutionAuthorization,
    preflight: ProductionShadowRunPreflight,
    decision: HealthGateDecision,
) -> AuthorizationConsumption:
    values = {
        "launcher_version": LAUNCHER_VERSION,
        "authorization_id": record.authorization_id,
        "authorization_digest": record.authorization_digest,
        "run_id": record.run_id,
        "preflight_digest": preflight.preflight_digest,
        "output_directory": record.output_directory,
        "observer_decision_digest": decision.decision_digest,
        "grants_integration_authority": False,
        "grants_activation_authority": False,
    }
    consumption = AuthorizationConsumption(**values, consumption_digest=_digest(values))
    try:
        with marker.open("xb") as handle:
            handle.write(_canonical(asdict(consumption)))
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise ProductionShadowLauncherError("PSI0B_E7_AUTHORIZATION_ALREADY_CONSUMED") from exc
    except Exception as exc:
        raise ProductionShadowLauncherError("PSI0B_E7_CONSUMPTION_PERSISTENCE_FAILED") from exc
    return consumption


def launch_authorized_shadow(
    authorization_path: Path,
    preflight_artifact_directory: Path,
    consumption_directory: Path,
    *,
    observer_bootstrap: Callable[[], HealthGateDecision],
    executor: Callable[[ProductionExecutionAuthorization, ProductionShadowRunPreflight, HealthGateDecision], object],
) -> object:
    """Validate, observe, consume once, then delegate to the existing executor."""
    record, preflight, marker = validate_bootstrap_inputs(
        authorization_path, preflight_artifact_directory, consumption_directory,
    )
    try:
        decision = observer_bootstrap()
        verify_health_gate_decision(decision)
    except Exception as exc:
        raise ProductionShadowLauncherError("PSI0B_E7_OBSERVER_BOOTSTRAP_FAILED") from exc
    if decision.phase != "PRESTART" or decision.status != "PASS":
        raise ProductionShadowLauncherError("PSI0B_E7_PRESTART_DO_NOT_START")
    _consume_authorization(marker, record, preflight, decision)
    return executor(record, preflight, decision)
