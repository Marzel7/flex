"""PSI0H-E3 immutable real-run preflight and E2-to-E wrapper."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Callable, Mapping, Sequence

from src.acquisition.transaction import AcquisitionResponse
from .psi0h_census_transaction_adapter import collect_census_transactions
from .psi0h_real_cohort_execution import (
    RealCohortAuthorization, execute_real_cohort_once, verify_real_cohort_authorization,
)


SCHEMA_VERSION = "psi0h-e3.real-run-preflight.v1"
E2_ADAPTER_SHA256 = "9ad8c3b6b45152d2d364e5f9dad4787f515f84c5b45853ea284c6da510c00a88"
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class Psi0hRealRunPreflightError(RuntimeError):
    pass


@dataclass(frozen=True)
class RealRunPreflight:
    schema_version: str
    run_id: str
    source_id: str
    census_path: str
    census_device: int
    census_inode: int
    census_start_offset: int
    maximum_census_bytes: int
    interval_start: int
    interval_end: int
    cutoff: int
    endpoint_class: str
    maximum_events: int
    maximum_provider_requests: int
    e2_adapter_sha256: str
    staging_directory: str
    output_directory: str
    consumption_directory: str
    source_read_authorized: bool
    provider_access_authorized: bool
    service_changes_authorized: bool
    comparison_authorized: bool
    activation_authorized: bool
    preflight_digest: str


def _digest(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def build_real_run_preflight(
    *, run_id: str, source_id: str, census_path: Path, census_device: int,
    census_inode: int, census_start_offset: int, maximum_census_bytes: int,
    interval_start: int, interval_end: int, cutoff: int, endpoint_class: str,
    staging_directory: Path, output_directory: Path, consumption_directory: Path,
    maximum_events: int = 20, maximum_provider_requests: int = 20,
) -> RealRunPreflight:
    paths = [Path(staging_directory), Path(output_directory), Path(consumption_directory)]
    if (not all(_IDENTIFIER.fullmatch(value or "") for value in (run_id, source_id, endpoint_class)) or
            not Path(census_path).is_absolute() or census_device < 0 or census_inode < 0 or
            census_start_offset < 0 or not 1 <= maximum_census_bytes <= 4 * 1024 * 1024 or
            not cutoff < interval_start <= interval_end or not 1 <= maximum_events <= 20 or
            maximum_provider_requests != maximum_events or any(path.exists() for path in paths) or
            len({str(path.resolve()) for path in paths}) != 3 or
            any(not path.parent.is_dir() for path in paths)):
        raise Psi0hRealRunPreflightError("PSI0H_E3_PREFLIGHT_INVALID")
    values = {
        "schema_version": SCHEMA_VERSION, "run_id": run_id, "source_id": source_id,
        "census_path": str(Path(census_path).resolve()), "census_device": census_device,
        "census_inode": census_inode, "census_start_offset": census_start_offset,
        "maximum_census_bytes": maximum_census_bytes, "interval_start": interval_start,
        "interval_end": interval_end, "cutoff": cutoff, "endpoint_class": endpoint_class,
        "maximum_events": maximum_events, "maximum_provider_requests": maximum_provider_requests,
        "e2_adapter_sha256": E2_ADAPTER_SHA256,
        "staging_directory": str(paths[0].resolve()), "output_directory": str(paths[1].resolve()),
        "consumption_directory": str(paths[2].resolve()),
        "source_read_authorized": False, "provider_access_authorized": False,
        "service_changes_authorized": False, "comparison_authorized": False,
        "activation_authorized": False,
    }
    return RealRunPreflight(**values, preflight_digest=_digest(values))


def verify_real_run_preflight(record: RealRunPreflight) -> bool:
    values = asdict(record)
    supplied = values.pop("preflight_digest")
    paths = [Path(record.staging_directory), Path(record.output_directory),
             Path(record.consumption_directory)]
    if (record.schema_version != SCHEMA_VERSION or record.e2_adapter_sha256 != E2_ADAPTER_SHA256 or
            supplied != _digest(values) or not _DIGEST.fullmatch(supplied) or
            any((record.source_read_authorized, record.provider_access_authorized,
                 record.service_changes_authorized, record.comparison_authorized,
                 record.activation_authorized)) or record.maximum_provider_requests != record.maximum_events or
            any(path.exists() for path in paths)):
        raise Psi0hRealRunPreflightError("PSI0H_E3_PREFLIGHT_REPLAY_FAILED")
    return True


def execute_preflight_bound_fixture(
    *, preflight: RealRunPreflight, authorization: RealCohortAuthorization,
    events: Sequence[Mapping[str, Any]],
    transport: Callable[[str, str], AcquisitionResponse],
) -> dict[str, Any]:
    """Exercise the wrapper with injected representations; grants no live access."""
    verify_real_run_preflight(preflight)
    verify_real_cohort_authorization(authorization)
    if (authorization.run_id != preflight.run_id or
            authorization.source_id != preflight.source_id or
            authorization.interval_start != preflight.interval_start or
            authorization.interval_end != preflight.interval_end or
            authorization.cutoff != preflight.cutoff or
            authorization.maximum_provider_requests != preflight.maximum_provider_requests or
            authorization.collector_contract_digest != preflight.e2_adapter_sha256 or
            authorization.isolated_output_directory != preflight.output_directory):
        raise Psi0hRealRunPreflightError("PSI0H_E3_AUTHORIZATION_BINDING_DRIFT")
    consumption = Path(preflight.consumption_directory)
    consumption.mkdir()

    def collector(_: RealCohortAuthorization) -> dict[str, Any]:
        result = collect_census_transactions(
            events=events, interval_start=preflight.interval_start,
            interval_end=preflight.interval_end,
            staging_root=Path(preflight.staging_directory), transport=transport,
        )
        return {key: result[key] for key in
                ("envelopes", "evidence_rows", "primitive_rows", "provider_request_count")}

    return execute_real_cohort_once(
        authorization, consumption_directory=consumption, collector=collector,
    )
