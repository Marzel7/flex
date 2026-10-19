"""EB0.3G immutable output bundles for frozen supplemental market evidence."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Mapping

from .gmgn_market_kline_normalizer import RequestMetadata, normalize_gmgn_market_kline
from .historical_market_observation import HistoricalMarketObservation
from .historical_market_observation_manifest import (
    HistoricalMarketObservationManifest,
    build_historical_market_observation_manifest,
    verify_historical_market_observation_manifest,
)


BUNDLE_SCHEMA_VERSION = "eb0.3g.v1"
_FILES = ("run.json", "projection.json", "manifest.json", "observations.json")
_REVISION = re.compile(r"^[0-9a-f]{7,64}$")
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class HistoricalMarketObservationBundleError(RuntimeError):
    """Named fail-closed EB0.3G bundle error."""


@dataclass(frozen=True)
class HistoricalMarketObservationBundle:
    output_directory: Path
    bundle_digest: str
    file_digests: Mapping[str, str]


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode() + b"\n"


def _sha(payload: bytes) -> str:
    return sha256(payload).hexdigest()


def _prepare_directory(path: Path) -> None:
    if path.exists():
        if not path.is_dir() or any(path.iterdir()):
            raise HistoricalMarketObservationBundleError("EB0_3G_OUTPUT_NOT_EMPTY")
        return
    try:
        path.mkdir(mode=0o700)
    except OSError as exc:
        raise HistoricalMarketObservationBundleError("EB0_3G_OUTPUT_CREATE_FAILED") from exc


def _write_exclusive(path: Path, payload: bytes) -> None:
    try:
        with path.open("xb") as handle:
            handle.write(payload)
    except FileExistsError as exc:
        raise HistoricalMarketObservationBundleError("EB0_3G_OVERWRITE_REJECTED") from exc


def write_historical_market_observation_bundle(
    output_directory: Path,
    *,
    envelope: Mapping[str, object],
    metadata: RequestMetadata,
    source_file_hashes: Mapping[str, str],
    engineering_revision: str,
) -> HistoricalMarketObservationBundle:
    if not _REVISION.fullmatch(engineering_revision):
        raise HistoricalMarketObservationBundleError("EB0_3G_INVALID_ENGINEERING_REVISION")
    if not _RUN_ID.fullmatch(metadata.request_run_id):
        raise HistoricalMarketObservationBundleError("EB0_3G_INVALID_RUN_ID")
    normalized = normalize_gmgn_market_kline(envelope, metadata)
    manifest = build_historical_market_observation_manifest(
        envelope=envelope, metadata=metadata, source_file_hashes=source_file_hashes
    )
    output = Path(output_directory)
    _prepare_directory(output)
    run = {
        "bundle_schema_version": BUNDLE_SCHEMA_VERSION,
        "run_id": metadata.request_run_id,
        "engineering_revision": engineering_revision,
        "request_metadata": asdict(metadata),
        "raw_envelope_digest": normalized.raw_envelope_digest,
        "manifest_digest": manifest.manifest_digest,
    }
    payloads = {
        "run.json": _json_bytes(run),
        "projection.json": _json_bytes(normalized.projection),
        "manifest.json": _json_bytes(manifest.to_dict()),
        "observations.json": _json_bytes(
            {"observations": [asdict(item) for item in manifest.observations]}
        ),
    }
    digests = {name: _sha(payloads[name]) for name in _FILES}
    bundle_digest = _sha(_json_bytes(digests))
    hashes = _json_bytes({
        "bundle_schema_version": BUNDLE_SCHEMA_VERSION,
        "files": digests,
        "bundle_digest": bundle_digest,
    })
    for name in _FILES:
        _write_exclusive(output / name, payloads[name])
    _write_exclusive(output / "hashes.json", hashes)
    return HistoricalMarketObservationBundle(output, bundle_digest, digests)


def _manifest(value: Mapping[str, object]) -> HistoricalMarketObservationManifest:
    data = dict(value)
    data["observations"] = tuple(HistoricalMarketObservation(**item) for item in data["observations"])
    return HistoricalMarketObservationManifest(**data)


def verify_historical_market_observation_bundle(
    output_directory: Path,
    *,
    envelope: Mapping[str, object],
    source_file_hashes: Mapping[str, str],
) -> HistoricalMarketObservationBundle:
    output = Path(output_directory)
    expected = set(_FILES) | {"hashes.json"}
    if not output.is_dir() or {item.name for item in output.iterdir()} != expected:
        raise HistoricalMarketObservationBundleError("EB0_3G_BUNDLE_FILE_SET_MISMATCH")
    try:
        documents = {name: json.loads((output / name).read_text()) for name in _FILES}
        hashes = json.loads((output / "hashes.json").read_text())
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        raise HistoricalMarketObservationBundleError("EB0_3G_INVALID_JSON") from exc
    for name, document in documents.items():
        if (output / name).read_bytes() != _json_bytes(document):
            raise HistoricalMarketObservationBundleError("EB0_3G_NONCANONICAL_JSON")
    if (output / "hashes.json").read_bytes() != _json_bytes(hashes):
        raise HistoricalMarketObservationBundleError("EB0_3G_NONCANONICAL_JSON")
    if hashes.get("bundle_schema_version") != BUNDLE_SCHEMA_VERSION:
        raise HistoricalMarketObservationBundleError("EB0_3G_BUNDLE_SCHEMA_MISMATCH")
    recorded = hashes.get("files")
    if not isinstance(recorded, dict) or set(recorded) != set(_FILES):
        raise HistoricalMarketObservationBundleError("EB0_3G_HASH_FILE_SET_MISMATCH")
    actual = {name: _sha((output / name).read_bytes()) for name in _FILES}
    if recorded != actual:
        raise HistoricalMarketObservationBundleError("EB0_3G_FILE_DIGEST_MISMATCH")
    bundle_digest = _sha(_json_bytes(actual))
    if hashes.get("bundle_digest") != bundle_digest:
        raise HistoricalMarketObservationBundleError("EB0_3G_BUNDLE_DIGEST_MISMATCH")
    try:
        run = documents["run.json"]
        if run.get("bundle_schema_version") != BUNDLE_SCHEMA_VERSION:
            raise HistoricalMarketObservationBundleError("EB0_3G_BUNDLE_SCHEMA_MISMATCH")
        if not _RUN_ID.fullmatch(run.get("run_id", "")):
            raise HistoricalMarketObservationBundleError("EB0_3G_INVALID_RUN_ID")
        if not _REVISION.fullmatch(run.get("engineering_revision", "")):
            raise HistoricalMarketObservationBundleError("EB0_3G_INVALID_ENGINEERING_REVISION")
        metadata = RequestMetadata(**run["request_metadata"])
        if metadata.request_run_id != run["run_id"]:
            raise HistoricalMarketObservationBundleError("EB0_3G_RUN_ID_MISMATCH")
        normalized = normalize_gmgn_market_kline(envelope, metadata)
        if normalized.projection != documents["projection.json"]:
            raise HistoricalMarketObservationBundleError("EB0_3G_PROJECTION_REPLAY_MISMATCH")
        manifest = _manifest(documents["manifest.json"])
        verify_historical_market_observation_manifest(
            manifest, envelope=envelope, metadata=metadata, source_file_hashes=source_file_hashes
        )
        if run["raw_envelope_digest"] != normalized.raw_envelope_digest or run["manifest_digest"] != manifest.manifest_digest:
            raise HistoricalMarketObservationBundleError("EB0_3G_RUN_DIGEST_MISMATCH")
        observations = documents["observations.json"]
        if observations != {"observations": [asdict(item) for item in manifest.observations]}:
            raise HistoricalMarketObservationBundleError("EB0_3G_OBSERVATION_REPLAY_MISMATCH")
    except HistoricalMarketObservationBundleError:
        raise
    except Exception as exc:
        raise HistoricalMarketObservationBundleError("EB0_3G_CONTENT_REPLAY_FAILED") from exc
    return HistoricalMarketObservationBundle(output, bundle_digest, actual)
