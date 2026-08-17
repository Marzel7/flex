"""PSI0G-D5 immutable local retention of reviewed real candidate provenance.

This is deliberately not the PSI0F-F13 fixture publisher and does not create an
F13 input store. It seals the exact real projection, source manifest, human
disposition, and READY preflight so a later separately qualified adapter can
consume provenance without reconstructing or weakening it.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping

from .psi0g_real_retention_preflight import assess_real_retention_preflight, canonical


SCHEMA_VERSION = "psi0g-d5.real-provenance-retention.v1"
PAYLOAD_FILES = (
    "projection.json", "projection-manifest.json", "disposition.json",
    "authorization.json", "preflight.json",
)


class Psi0gRealProvenanceRetentionError(RuntimeError):
    pass


@dataclass(frozen=True)
class RetainedRealCandidateProvenance:
    path: Path
    retention_id: str
    manifest_digest: str


def _fail(code: str) -> None:
    raise Psi0gRealProvenanceRetentionError(f"PSI0G_D5_{code}")


def _sha(value: bytes) -> str:
    return sha256(value).hexdigest()


def _manifest_payload(manifest: Mapping[str, Any]) -> bytes:
    return canonical(manifest)


def retain_real_candidate_provenance(
    destination: Path, *, projection: Mapping[str, Any],
    projection_manifest: Mapping[str, Any], disposition: Mapping[str, Any],
    authorization: Mapping[str, Any],
) -> RetainedRealCandidateProvenance:
    preflight = assess_real_retention_preflight(projection, projection_manifest, disposition)
    if preflight["status"] != "READY" or preflight["blockers"]:
        _fail("PREFLIGHT_NOT_READY")
    expected_authority = {
        "supported": False, "same_operation": False, "same_human": False,
        "publication": False, "monitoring": False, "activation": False,
    }
    if (authorization.get("schema_version") != "psi0g-d5.local-retention-authorization.v1" or
            authorization.get("status") != "AUTHORIZED" or
            authorization.get("candidate_id") != projection["candidate"]["candidate_id"] or
            authorization.get("preflight_digest") != preflight["preflight_digest"] or
            authorization.get("action") != "LOCAL_IMMUTABLE_PROVENANCE_RETENTION" or
            authorization.get("authority") != expected_authority):
        _fail("AUTHORIZATION_INVALID")
    documents = {
        "projection.json": projection,
        "projection-manifest.json": projection_manifest,
        "disposition.json": disposition,
        "authorization.json": authorization,
        "preflight.json": preflight,
    }
    payloads = {name: canonical(value) for name, value in documents.items()}
    identity = {
        "schema_version": SCHEMA_VERSION,
        "candidate_id": projection["candidate"]["candidate_id"],
        "preflight_digest": preflight["preflight_digest"],
        "files": {
            name: {"sha256": _sha(payloads[name]), "size_bytes": len(payloads[name])}
            for name in PAYLOAD_FILES
        },
    }
    retention_id = _sha(canonical(identity))
    manifest = {
        **identity, "retention_id": retention_id,
        "authority": {
            "supported": False, "same_operation": False, "same_human": False,
            "publication": False, "monitoring": False, "activation": False,
        },
        "fixture_f13_invoked": False,
        "f13_input_store_created": False,
    }
    manifest_payload = _manifest_payload(manifest)
    target = Path(destination)
    if target.exists() or target.is_symlink() or not target.parent.is_dir():
        _fail("DESTINATION_NOT_NEW")
    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}.staging-", dir=target.parent))
    try:
        for name in PAYLOAD_FILES:
            path = staging / name
            path.write_bytes(payloads[name])
            descriptor = os.open(path, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        manifest_path = staging / "manifest.json"
        manifest_path.write_bytes(manifest_payload)
        descriptor = os.open(manifest_path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        descriptor = os.open(staging, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(staging, target)
        descriptor = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except BaseException:
        if staging.exists():
            for child in staging.iterdir():
                child.unlink()
            staging.rmdir()
        raise
    return RetainedRealCandidateProvenance(
        path=target, retention_id=retention_id, manifest_digest=_sha(manifest_payload),
    )


def replay_real_candidate_provenance(path: Path) -> RetainedRealCandidateProvenance:
    source = Path(path)
    if source.is_symlink() or not source.is_dir():
        _fail("SOURCE_INVALID")
    expected_names = sorted((*PAYLOAD_FILES, "manifest.json"))
    if sorted(child.name for child in source.iterdir()) != expected_names:
        _fail("FILE_SET_DRIFT")
    try:
        manifest_payload = (source / "manifest.json").read_bytes()
        manifest = json.loads(manifest_payload)
    except (OSError, json.JSONDecodeError) as exc:
        raise Psi0gRealProvenanceRetentionError("PSI0G_D5_MANIFEST_INVALID") from exc
    if manifest.get("schema_version") != SCHEMA_VERSION:
        _fail("SCHEMA_DRIFT")
    documents: dict[str, Any] = {}
    for name in PAYLOAD_FILES:
        payload = (source / name).read_bytes()
        expected = manifest.get("files", {}).get(name, {})
        if expected != {"sha256": _sha(payload), "size_bytes": len(payload)}:
            _fail("PAYLOAD_IDENTITY_DRIFT")
        try:
            documents[name] = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise Psi0gRealProvenanceRetentionError("PSI0G_D5_PAYLOAD_INVALID") from exc
        if canonical(documents[name]) != payload:
            _fail("PAYLOAD_NOT_CANONICAL")
    preflight = assess_real_retention_preflight(
        documents["projection.json"], documents["projection-manifest.json"],
        documents["disposition.json"],
    )
    if preflight != documents["preflight.json"] or preflight["status"] != "READY":
        _fail("PREFLIGHT_REPLAY_MISMATCH")
    authorization = documents["authorization.json"]
    expected_authority = {
        "supported": False, "same_operation": False, "same_human": False,
        "publication": False, "monitoring": False, "activation": False,
    }
    if (authorization.get("schema_version") != "psi0g-d5.local-retention-authorization.v1" or
            authorization.get("status") != "AUTHORIZED" or
            authorization.get("candidate_id") != documents["projection.json"]["candidate"]["candidate_id"] or
            authorization.get("preflight_digest") != preflight["preflight_digest"] or
            authorization.get("action") != "LOCAL_IMMUTABLE_PROVENANCE_RETENTION" or
            authorization.get("authority") != expected_authority):
        _fail("AUTHORIZATION_REPLAY_MISMATCH")
    identity = {
        "schema_version": SCHEMA_VERSION,
        "candidate_id": documents["projection.json"]["candidate"]["candidate_id"],
        "preflight_digest": preflight["preflight_digest"],
        "files": manifest["files"],
    }
    retention_id = _sha(canonical(identity))
    if (manifest.get("retention_id") != retention_id or manifest.get("candidate_id") != identity["candidate_id"] or
            manifest.get("preflight_digest") != identity["preflight_digest"] or
            manifest.get("fixture_f13_invoked") is not False or
            manifest.get("f13_input_store_created") is not False or
            any(manifest.get("authority", {}).values())):
        _fail("MANIFEST_REPLAY_MISMATCH")
    return RetainedRealCandidateProvenance(
        path=source, retention_id=retention_id, manifest_digest=_sha(manifest_payload),
    )
