"""Immutable EB0.1J output bundles for bounded canonical census results."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Mapping

from .birth_valuation_census import CENSUS_SCHEMA_VERSION, CensusResult, DEFAULT_MINT_LIMIT


BUNDLE_SCHEMA_VERSION = "eb0.1j.v1"
_FILES = ("run.json", "aggregate.json", "corpora.json")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class CensusBundleError(RuntimeError):
    """Named fail-closed error for an unsafe or non-replayable bundle."""


@dataclass(frozen=True)
class CensusBundle:
    output_directory: Path
    bundle_digest: str
    file_digests: Mapping[str, str]


def _json_bytes(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8") + b"\n"


def _sha(payload: bytes) -> str:
    return sha256(payload).hexdigest()


def _counts(result: CensusResult) -> dict[str, object]:
    events: Counter[str] = Counter()
    quality: Counter[str] = Counter()
    completeness: Counter[str] = Counter()
    conflicts = missing_values = 0
    for corpus in result.corpora:
        events.update(corpus.manifest.event_counts)
        quality.update(corpus.manifest.quality_counts)
        completeness.update(corpus.manifest.completeness_counts)
        conflicts += corpus.manifest.conflicting_observation_count
        missing_values += corpus.manifest.missing_valuation_count
    return {
        "selected_mint_count": len(result.selected_mints),
        "corpus_count": len(result.corpora),
        "mints_without_canonical_evidence_count": len(result.mints_without_canonical_evidence),
        "observation_count": result.observation_count,
        "excluded_observation_count": result.excluded_observation_count,
        "ignored_explicit_record_count": result.ignored_explicit_record_count,
        "event_counts": dict(sorted(events.items())),
        "quality_counts": dict(sorted(quality.items())),
        "completeness_counts": dict(sorted(completeness.items())),
        "missing_event_kind_counts": dict(sorted(result.missing_event_kind_counts.items())),
        "conflicting_observation_count": conflicts,
        "missing_valuation_count": missing_values,
    }


def _validate(
    result: CensusResult,
    run_id: str,
    source_schema_fingerprints: Mapping[str, str],
) -> None:
    if not _RUN_ID.fullmatch(run_id):
        raise CensusBundleError("EB0_1J_INVALID_RUN_ID")
    if result.schema_version != CENSUS_SCHEMA_VERSION:
        raise CensusBundleError("EB0_1J_CENSUS_SCHEMA_MISMATCH")
    if result.mint_limit != DEFAULT_MINT_LIMIT or len(result.selected_mints) > DEFAULT_MINT_LIMIT:
        raise CensusBundleError("EB0_1J_UNBOUNDED_RESULT")
    if not _DIGEST.fullmatch(result.input_fingerprint) or not _DIGEST.fullmatch(result.result_digest):
        raise CensusBundleError("EB0_1J_INVALID_RESULT_DIGEST")
    if not source_schema_fingerprints:
        raise CensusBundleError("EB0_1J_MISSING_SCHEMA_FINGERPRINT")
    for name, digest in source_schema_fingerprints.items():
        if not isinstance(name, str) or not _RUN_ID.fullmatch(name) or not _DIGEST.fullmatch(digest):
            raise CensusBundleError("EB0_1J_INVALID_SCHEMA_FINGERPRINT")


def _prepare_directory(path: Path) -> None:
    if path.exists():
        if not path.is_dir() or any(path.iterdir()):
            raise CensusBundleError("EB0_1J_OUTPUT_NOT_EMPTY")
        return
    try:
        path.mkdir(mode=0o700)
    except (FileExistsError, FileNotFoundError, OSError) as exc:
        raise CensusBundleError("EB0_1J_OUTPUT_CREATE_FAILED") from exc


def _write_exclusive(path: Path, payload: bytes) -> None:
    try:
        with path.open("xb") as handle:
            handle.write(payload)
    except FileExistsError as exc:
        raise CensusBundleError("EB0_1J_OVERWRITE_REJECTED") from exc


def write_census_bundle(
    result: CensusResult,
    output_directory: Path,
    *,
    run_id: str,
    source_schema_fingerprints: Mapping[str, str],
) -> CensusBundle:
    """Write a credential-free canonical result bundle using exclusive files."""

    fingerprints = dict(sorted(source_schema_fingerprints.items()))
    _validate(result, run_id, fingerprints)
    output = Path(output_directory)
    _prepare_directory(output)

    run = {
        "bundle_schema_version": BUNDLE_SCHEMA_VERSION,
        "census_schema_version": result.schema_version,
        "run_id": run_id,
        "high_water_migrated_at": result.high_water_migrated_at,
        "mint_limit": result.mint_limit,
        "input_fingerprint": result.input_fingerprint,
        "result_digest": result.result_digest,
        "source_schema_fingerprints": fingerprints,
    }
    aggregate = _counts(result)
    corpora = {
        "selected_mints": list(result.selected_mints),
        "mints_without_canonical_evidence": list(result.mints_without_canonical_evidence),
        "corpora": [asdict(item) for item in result.corpora],
    }
    payloads = {
        "run.json": _json_bytes(run),
        "aggregate.json": _json_bytes(aggregate),
        "corpora.json": _json_bytes(corpora),
    }
    digests = {name: _sha(payloads[name]) for name in _FILES}
    bundle_digest = _sha(_json_bytes(digests))
    hashes = _json_bytes({
        "bundle_schema_version": BUNDLE_SCHEMA_VERSION,
        "files": digests,
        "bundle_digest": bundle_digest,
    })
    try:
        for name in _FILES:
            _write_exclusive(output / name, payloads[name])
        _write_exclusive(output / "hashes.json", hashes)
    except BaseException:
        raise
    return CensusBundle(output, bundle_digest, digests)


def verify_census_bundle(output_directory: Path) -> CensusBundle:
    """Verify the exact file set and every recorded digest."""

    output = Path(output_directory)
    expected = set(_FILES) | {"hashes.json"}
    if not output.is_dir() or {item.name for item in output.iterdir()} != expected:
        raise CensusBundleError("EB0_1J_BUNDLE_FILE_SET_MISMATCH")
    try:
        hashes = json.loads((output / "hashes.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CensusBundleError("EB0_1J_INVALID_HASH_MANIFEST") from exc
    if hashes.get("bundle_schema_version") != BUNDLE_SCHEMA_VERSION:
        raise CensusBundleError("EB0_1J_BUNDLE_SCHEMA_MISMATCH")
    recorded = hashes.get("files")
    if not isinstance(recorded, dict) or set(recorded) != set(_FILES):
        raise CensusBundleError("EB0_1J_HASH_FILE_SET_MISMATCH")
    actual = {name: _sha((output / name).read_bytes()) for name in _FILES}
    if recorded != actual:
        raise CensusBundleError("EB0_1J_FILE_DIGEST_MISMATCH")
    bundle_digest = _sha(_json_bytes(actual))
    if hashes.get("bundle_digest") != bundle_digest:
        raise CensusBundleError("EB0_1J_BUNDLE_DIGEST_MISMATCH")
    return CensusBundle(output, bundle_digest, actual)
