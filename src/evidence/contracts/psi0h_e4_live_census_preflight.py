"""PSI0H-E4 live-census high-water preflight."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from pathlib import Path
import re


SCHEMA_VERSION = "psi0h-e4.live-census-high-water-preflight.v1"
MAX_CENSUS_BYTES = 4 * 1024 * 1024
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


class Psi0hLiveCensusPreflightError(RuntimeError):
    pass


@dataclass(frozen=True)
class LiveCensusPreflight:
    schema_version: str
    run_id: str
    source_id: str
    source_kind: str
    census_path: str
    census_device: int
    census_inode: int
    census_size_bytes: int
    census_mtime_ns: int
    census_start_offset: int
    maximum_census_bytes: int
    interval_start: int
    interval_end: int
    cutoff: int
    staging_directory: str
    output_directory: str
    consumption_directory: str
    source_read_authorized: bool
    provider_access_authorized: bool
    service_changes_authorized: bool
    comparison_authorized: bool
    monitoring_authorized: bool
    activation_authorized: bool
    preflight_digest: str


def _digest(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _identity(path: Path) -> tuple[int, int, int, int]:
    value = path.stat()
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns


def build_live_census_preflight(
    *, run_id: str, source_id: str, source_kind: str, census_path: Path,
    maximum_census_bytes: int, interval_start: int, interval_end: int,
    cutoff: int, staging_directory: Path, output_directory: Path,
    consumption_directory: Path,
) -> LiveCensusPreflight:
    paths = [staging_directory, output_directory, consumption_directory]
    if (not all(_IDENTIFIER.fullmatch(value or "") for value in
                (run_id, source_id, source_kind)) or not cutoff < interval_start <= interval_end or
            not 1 <= maximum_census_bytes <= MAX_CENSUS_BYTES or
            any(not path.parent.is_dir() for path in paths) or any(path.exists() for path in paths) or
            len({str(path.resolve()) for path in paths}) != 3):
        raise Psi0hLiveCensusPreflightError("PSI0H_E4_PREFLIGHT_INVALID")
    candidate = Path(census_path)
    if not candidate.is_absolute() or not candidate.is_file():
        raise Psi0hLiveCensusPreflightError("PSI0H_E4_PREFLIGHT_INVALID")
    device, inode, size_bytes, mtime_ns = _identity(candidate)
    values = {
        "schema_version": SCHEMA_VERSION, "run_id": run_id,
        "source_id": source_id, "source_kind": source_kind,
        "census_path": str(candidate.resolve()),
        "census_device": device, "census_inode": inode,
        "census_size_bytes": size_bytes, "census_mtime_ns": mtime_ns,
        "census_start_offset": size_bytes, "maximum_census_bytes": maximum_census_bytes,
        "interval_start": interval_start, "interval_end": interval_end,
        "cutoff": cutoff,
        "staging_directory": str(staging_directory.resolve()),
        "output_directory": str(output_directory.resolve()),
        "consumption_directory": str(consumption_directory.resolve()),
        "source_read_authorized": False, "provider_access_authorized": False,
        "service_changes_authorized": False, "comparison_authorized": False,
        "monitoring_authorized": False, "activation_authorized": False,
    }
    return LiveCensusPreflight(**values, preflight_digest=_digest(values))


def verify_live_census_preflight(record: LiveCensusPreflight) -> bool:
    values = asdict(record)
    supplied = values.pop("preflight_digest")
    if (record.schema_version != SCHEMA_VERSION or not _DIGEST.fullmatch(supplied) or
            supplied != _digest(values) or any(
            (record.source_read_authorized, record.provider_access_authorized,
             record.service_changes_authorized, record.comparison_authorized,
             record.monitoring_authorized, record.activation_authorized))):
        raise Psi0hLiveCensusPreflightError("PSI0H_E4_PREFLIGHT_REPLAY_FAILED")
    paths = [Path(record.staging_directory), Path(record.output_directory),
             Path(record.consumption_directory)]
    if any(path.exists() for path in paths):
        raise Psi0hLiveCensusPreflightError("PSI0H_E4_PREFLIGHT_REPLAY_FAILED")
    census_path = Path(record.census_path)
    if (not census_path.is_absolute() or not census_path.is_file() or
            not all(_IDENTIFIER.fullmatch(value or "") for value in
                    (record.run_id, record.source_id, record.source_kind))):
        raise Psi0hLiveCensusPreflightError("PSI0H_E4_PREFLIGHT_REPLAY_FAILED")
    return True
