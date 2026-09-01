"""PSI0H-E5 execution-boundary preflight for one bounded real prospective cohort."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re
from pathlib import Path

from .psi0h_e4_live_census_preflight import SCHEMA_VERSION as E4_PREFLIGHT_SCHEMA
from .psi0h_real_run_preflight import SCHEMA_VERSION as E3_PREFLIGHT_SCHEMA, E2_ADAPTER_SHA256


SCHEMA_VERSION = "psi0h-e5.real-prospective-cohort-preflight.v1"
MAX_MIGRATIONS = 20
MAX_REQUESTS = 20
REQUESTS_PER_MIGRATION = 1
ENDPOINT_CLASS = "solana-json-rpc-gettransaction"
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


class Psi0hE5PreflightError(RuntimeError):
    pass


@dataclass(frozen=True)
class E5ProspectiveCohortPreflight:
    schema_version: str
    run_id: str
    source_id: str
    source_kind: str
    e4_artifact_path: str
    e4_preflight_digest: str
    census_path: str
    census_device: int
    census_inode: int
    census_size_bytes: int
    census_mtime_ns: int
    census_start_offset: int
    census_high_water_digest: str
    e3_artifact_path: str
    e3_artifact_digest: str
    e3_run_preflight_schema_version: str
    e2_adapter_digest: str
    endpoint_class: str
    interval_start: int
    interval_end: int
    cutoff: int
    maximum_migrations: int
    maximum_provider_requests: int
    requests_per_migration: int
    retries_allowed: int
    pagination_enabled: bool
    failover_enabled: bool
    source_read_authorized: bool
    provider_access_authorized: bool
    service_changes_authorized: bool
    comparison_authorized: bool
    monitoring_authorized: bool
    activation_authorized: bool
    staging_directory: str
    output_directory: str
    consumption_directory: str
    preflight_digest: str


def _digest(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def build_e5_real_prospective_preflight(
    *, run_id: str, e4_artifact: Path, e4_preflight: dict, e3_artifact: Path,
    e3_artifact_digest: str, staging_directory: Path, output_directory: Path,
    consumption_directory: Path,
) -> E5ProspectiveCohortPreflight:
    if (
        not all(_IDENTIFIER.fullmatch(value or "") for value in
                (run_id, "pumpportal-migration-census", "migration-census-live-observation"))
        or not isinstance(e4_preflight, dict)
        or not isinstance(e4_artifact, Path)
        or not _DIGEST.fullmatch(e3_artifact_digest or "")
        or not isinstance(e3_artifact, Path)
        or not all(path.parent.is_dir() for path in
                   (staging_directory, output_directory, consumption_directory))
    ):
        raise Psi0hE5PreflightError("PSI0H_E5_PREFLIGHT_INVALID")
    if not e4_artifact.is_file():
        raise Psi0hE5PreflightError("PSI0H_E5_E4_ARTIFACT_MISSING")

    e4 = e4_preflight.get("preflight") or {}
    if (
        not e4_preflight
        or e4_preflight.get("status") != "PASS"
        or e4.get("schema_version") != E4_PREFLIGHT_SCHEMA
        or e4.get("source_id") != "pumpportal-migration-census"
        or e4.get("source_kind") != "migration-census-live-file"
        or not _DIGEST.fullmatch(e4_preflight.get("preflight_digest", ""))
    ):
        raise Psi0hE5PreflightError("PSI0H_E5_E4_PRELIGHT_BINDING_DRIFT")

    source_path = Path(e4.get("census_path") or "")
    if not source_path.is_absolute() or not source_path.is_file():
        raise Psi0hE5PreflightError("PSI0H_E5_E4_CENSUS_PATH_INVALID")

    census_identity = source_path.stat()
    if (
        census_identity.st_dev != e4.get("census_device")
        or census_identity.st_ino != e4.get("census_inode")
        or census_identity.st_size < e4.get("census_start_offset")
        or e4.get("census_start_offset") != e4.get("census_size_bytes")
    ):
        raise Psi0hE5PreflightError("PSI0H_E5_E4_HIGHWATER_DRIFT")

    interval_start = e4.get("interval_start")
    interval_end = e4.get("interval_end")
    cutoff = e4.get("cutoff")
    if (
        not isinstance(interval_start, int)
        or not isinstance(interval_end, int)
        or not isinstance(cutoff, int)
        or not cutoff < interval_start <= interval_end
    ):
        raise Psi0hE5PreflightError("PSI0H_E5_INTERVAL_INVALID")

    paths = [staging_directory, output_directory, consumption_directory]
    if any(path.exists() for path in paths) or len({str(path.resolve()) for path in paths}) != 3:
        raise Psi0hE5PreflightError("PSI0H_E5_DESTINATION_REUSED")

    values = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "source_id": "pumpportal-migration-census",
        "source_kind": "migration-census-live-observation",
        "e4_artifact_path": str(e4_artifact.resolve()),
        "e4_preflight_digest": e4_preflight.get("preflight_digest", ""),
        "census_path": str(source_path.resolve()),
        "census_device": census_identity.st_dev,
        "census_inode": census_identity.st_ino,
        "census_size_bytes": census_identity.st_size,
        "census_mtime_ns": census_identity.st_mtime_ns,
        "census_start_offset": e4.get("census_start_offset"),
        "census_high_water_digest": e4_preflight.get("preflight_digest", ""),
        "e3_artifact_path": str(e3_artifact.resolve()),
        "e3_artifact_digest": e3_artifact_digest,
        "e3_run_preflight_schema_version": E3_PREFLIGHT_SCHEMA,
        "e2_adapter_digest": E2_ADAPTER_SHA256,
        "endpoint_class": ENDPOINT_CLASS,
        "interval_start": interval_start,
        "interval_end": interval_end,
        "cutoff": cutoff,
        "maximum_migrations": MAX_MIGRATIONS,
        "maximum_provider_requests": MAX_REQUESTS,
        "requests_per_migration": REQUESTS_PER_MIGRATION,
        "retries_allowed": 0,
        "pagination_enabled": False,
        "failover_enabled": False,
        "source_read_authorized": False,
        "provider_access_authorized": False,
        "service_changes_authorized": False,
        "comparison_authorized": False,
        "monitoring_authorized": False,
        "activation_authorized": False,
        "staging_directory": str(staging_directory.resolve()),
        "output_directory": str(output_directory.resolve()),
        "consumption_directory": str(consumption_directory.resolve()),
    }

    if not _DIGEST.fullmatch(values["e4_preflight_digest"]) or not _DIGEST.fullmatch(values["census_high_water_digest"]):
        raise Psi0hE5PreflightError("PSI0H_E5_E4_DIGEST_INVALID")
    if not _DIGEST.fullmatch(values["e3_artifact_digest"]):
        raise Psi0hE5PreflightError("PSI0H_E5_E3_DIGEST_INVALID")

    return E5ProspectiveCohortPreflight(**values, preflight_digest=_digest(values))


def verify_e5_preflight(record: E5ProspectiveCohortPreflight) -> bool:
    values = record.__dict__.copy()
    supplied = values.pop("preflight_digest")
    if (
        record.schema_version != SCHEMA_VERSION
        or not supplied
        or not _DIGEST.fullmatch(supplied)
        or supplied != _digest(values)
        or record.maximum_migrations != MAX_MIGRATIONS
        or record.maximum_provider_requests != MAX_REQUESTS
        or record.requests_per_migration != REQUESTS_PER_MIGRATION
        or record.retries_allowed != 0
        or record.pagination_enabled
        or record.failover_enabled
        or record.endpoint_class != ENDPOINT_CLASS
        or any((record.source_read_authorized, record.service_changes_authorized,
                record.monitoring_authorized, record.activation_authorized, record.comparison_authorized))
        or not (record.provider_access_authorized is False)
    ):
        raise Psi0hE5PreflightError("PSI0H_E5_PREFLIGHT_REPLAY_INVALID")

    if record.cutoff >= record.interval_start or record.interval_start > record.interval_end:
        raise Psi0hE5PreflightError("PSI0H_E5_PREFLIGHT_REPLAY_INVALID")
    if Path(record.staging_directory).exists() or Path(record.output_directory).exists() or Path(record.consumption_directory).exists():
        raise Psi0hE5PreflightError("PSI0H_E5_PREFLIGHT_REPLAY_INVALID")
    if (not _DIGEST.fullmatch(record.e4_preflight_digest) or
            not _DIGEST.fullmatch(record.e3_artifact_digest) or
            not _DIGEST.fullmatch(record.census_high_water_digest) or
            not _DIGEST.fullmatch(record.e2_adapter_digest) or
            not record.e3_run_preflight_schema_version == E3_PREFLIGHT_SCHEMA):
        raise Psi0hE5PreflightError("PSI0H_E5_PREFLIGHT_REPLAY_INVALID")

    source_path = Path(record.census_path)
    if (
        not source_path.is_absolute()
        or not source_path.is_file()
        or record.census_device <= 0
        or record.census_inode <= 0
    ):
        raise Psi0hE5PreflightError("PSI0H_E5_PREFLIGHT_REPLAY_INVALID")

    return True
