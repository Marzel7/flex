"""PSI0H-E single-use boundary for one bounded real prospective cohort."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from typing import Any, Callable, Mapping

from .psi0h_prospective_derivation import qualify_prospective_derivation


SCHEMA_VERSION = "psi0h-e.real-prospective-cohort.v1"
AUTHORITY_CLASS = "HUMAN_APPROVED_ONE_RUN_OBSERVATION_ONLY"
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


class Psi0hRealCohortExecutionError(RuntimeError):
    pass


@dataclass(frozen=True)
class RealCohortAuthorization:
    schema_version: str
    authorization_id: str
    run_id: str
    authority_class: str
    source_id: str
    source_kind: str
    interval_start: int
    interval_end: int
    cutoff: int
    maximum_envelopes: int
    maximum_primitives: int
    maximum_provider_requests: int
    provider_access_allowed: bool
    service_changes_allowed: bool
    isolated_output_directory: str
    collector_contract_digest: str
    comparison_allowed: bool
    alerts_allowed: bool
    monitoring_allowed: bool
    activation_allowed: bool
    maximum_attempts: int
    authorization_digest: str


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _digest(value: object) -> str:
    return sha256(_canonical(value)).hexdigest()


def build_real_cohort_authorization(
    *, authorization_id: str, run_id: str, source_id: str, source_kind: str,
    interval_start: int, interval_end: int, cutoff: int, maximum_envelopes: int,
    maximum_primitives: int, maximum_provider_requests: int,
    provider_access_allowed: bool, service_changes_allowed: bool,
    isolated_output_directory: Path, collector_contract_digest: str,
    authority_class: str = AUTHORITY_CLASS,
) -> RealCohortAuthorization:
    output = Path(isolated_output_directory)
    if (not all(_IDENTIFIER.fullmatch(value or "") for value in
                (authorization_id, run_id, source_id, source_kind)) or
            authority_class != AUTHORITY_CLASS or not cutoff < interval_start <= interval_end or
            not 1 <= maximum_envelopes <= 100 or not 1 <= maximum_primitives <= 20 or
            not 0 <= maximum_provider_requests <= maximum_envelopes or
            not isinstance(provider_access_allowed, bool) or
            not isinstance(service_changes_allowed, bool) or
            (maximum_provider_requests > 0) != provider_access_allowed or
            not _DIGEST.fullmatch(collector_contract_digest) or output.exists() or
            not output.parent.is_dir()):
        raise Psi0hRealCohortExecutionError("PSI0H_E_AUTHORIZATION_INVALID")
    values = {
        "schema_version": SCHEMA_VERSION, "authorization_id": authorization_id,
        "run_id": run_id, "authority_class": authority_class, "source_id": source_id,
        "source_kind": source_kind, "interval_start": interval_start,
        "interval_end": interval_end, "cutoff": cutoff,
        "maximum_envelopes": maximum_envelopes, "maximum_primitives": maximum_primitives,
        "maximum_provider_requests": maximum_provider_requests,
        "provider_access_allowed": provider_access_allowed,
        "service_changes_allowed": service_changes_allowed,
        "isolated_output_directory": str(output.resolve()),
        "collector_contract_digest": collector_contract_digest,
        "comparison_allowed": False, "alerts_allowed": False,
        "monitoring_allowed": False, "activation_allowed": False,
        "maximum_attempts": 1,
    }
    return RealCohortAuthorization(**values, authorization_digest=_digest(values))


def verify_real_cohort_authorization(record: RealCohortAuthorization) -> bool:
    body = asdict(record)
    supplied = body.pop("authorization_digest")
    if (record.schema_version != SCHEMA_VERSION or record.authority_class != AUTHORITY_CLASS or
            supplied != _digest(body) or not _DIGEST.fullmatch(supplied) or
            any((record.comparison_allowed, record.alerts_allowed,
                 record.monitoring_allowed, record.activation_allowed)) or
            record.maximum_attempts != 1 or
            (record.maximum_provider_requests > 0) != record.provider_access_allowed):
        raise Psi0hRealCohortExecutionError("PSI0H_E_AUTHORIZATION_REPLAY_FAILED")
    return True


def execute_real_cohort_once(
    authorization: RealCohortAuthorization, *, consumption_directory: Path,
    collector: Callable[[RealCohortAuthorization], Mapping[str, Any]],
) -> dict[str, Any]:
    verify_real_cohort_authorization(authorization)
    output = Path(authorization.isolated_output_directory)
    consumption = Path(consumption_directory)
    marker = consumption / f"{authorization.authorization_id}.consumed.json"
    if (not consumption.is_dir() or any(consumption.iterdir()) or marker.exists() or output.exists()):
        raise Psi0hRealCohortExecutionError("PSI0H_E_DESTINATION_NOT_NEW_EMPTY")
    marker_values = {
        "authorization_id": authorization.authorization_id,
        "run_id": authorization.run_id,
        "authorization_digest": authorization.authorization_digest,
    }
    with marker.open("xb") as handle:
        handle.write(_canonical({**marker_values, "consumption_digest": _digest(marker_values)}) + b"\n")
        handle.flush()
        os.fsync(handle.fileno())
    captured = dict(collector(authorization))
    if set(captured) != {"envelopes", "evidence_rows", "primitive_rows", "provider_request_count"}:
        raise Psi0hRealCohortExecutionError("PSI0H_E_COLLECTOR_SHAPE_INVALID")
    requests = captured["provider_request_count"]
    if (not isinstance(requests, int) or requests < 0 or
            requests > authorization.maximum_provider_requests):
        raise Psi0hRealCohortExecutionError("PSI0H_E_PROVIDER_CEILING_EXCEEDED")
    qualification = qualify_prospective_derivation(
        cutoff=authorization.cutoff, interval_start=authorization.interval_start,
        interval_end=authorization.interval_end, envelopes=captured["envelopes"],
        evidence_rows=captured["evidence_rows"], primitive_rows=captured["primitive_rows"],
        maximum_primitives=authorization.maximum_primitives,
    )
    if qualification["envelope_count"] > authorization.maximum_envelopes:
        raise Psi0hRealCohortExecutionError("PSI0H_E_ENVELOPE_CEILING_EXCEEDED")
    result = {
        "schema_version": SCHEMA_VERSION, "status": qualification["status"],
        "authorization_id": authorization.authorization_id, "run_id": authorization.run_id,
        "authorization_digest": authorization.authorization_digest,
        "source_id": authorization.source_id, "source_kind": authorization.source_kind,
        "provider_request_count": requests, "qualification": qualification,
        "comparison_performed": False, "alerts_emitted": 0,
        "monitoring_activated": False, "activation_authority": False,
    }
    result["artifact_digest"] = _digest(result)
    output.mkdir(parents=False)
    path = output / "cohort.json"
    with path.open("xb") as handle:
        handle.write(_canonical(result) + b"\n")
        handle.flush()
        os.fsync(handle.fileno())
    return result
