"""PSI0G-D8 explicit real-provenance transition for an unchanged D7 surface."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any, Mapping

from .psi0g_real_provenance_retention import replay_real_candidate_provenance


SCHEMA_VERSION = "psi0g-d8.real-surface-provenance.v1"
SOURCE_PROVENANCE = "FROZEN_SYNTHETIC_KNOWN_BEHAVIOUR_OPERATIONAL_SURFACE"
REAL_PROVENANCE = "RETAINED_REAL_KNOWN_BEHAVIOUR_OPERATIONAL_SURFACE"
FILES = ("source-surface.json", "surface.json", "transition.json", "hashes.json")


class Psi0gRealSurfaceProvenanceError(RuntimeError):
    pass


@dataclass(frozen=True)
class RealProvenanceSurface:
    path: Path
    source_surface_digest: str
    surface_digest: str
    semantic_digest: str
    transition_digest: str
    publication_digest: str


def _fail(code: str) -> None:
    raise Psi0gRealSurfaceProvenanceError(f"PSI0G_D8_{code}")


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode() + b"\n"


def _sha(payload: bytes) -> str:
    return sha256(payload).hexdigest()


def _digest(value: object) -> str:
    return sha256(_canonical(value).rstrip(b"\n")).hexdigest()


def _load(path: Path, *, require_canonical: bool = False) -> tuple[dict[str, Any], bytes]:
    payload = path.read_bytes()
    try:
        value = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise Psi0gRealSurfaceProvenanceError("PSI0G_D8_JSON_INVALID") from exc
    if not isinstance(value, dict) or (require_canonical and _canonical(value) != payload):
        _fail("NONCANONICAL_INPUT")
    return value, payload


def _semantic(surface: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(surface)
    result.pop("fixture_only", None)
    result.pop("provenance_class", None)
    return result


def _authority_false(surface: Mapping[str, Any]) -> bool:
    return (
        surface.get("consumer_enabled") is False and surface.get("default_off") is True and
        surface.get("cross_layer_join_performed") is False and
        not any(surface.get("authority", {}).values()) and
        not any(surface.get("interpretation", {}).values())
    )


def publish_real_provenance_surface(
    destination: Path, *, d5_path: Path, d6_audit_path: Path,
    d7_path: Path, authorization_path: Path,
) -> RealProvenanceSurface:
    d5 = replay_real_candidate_provenance(d5_path)
    d6, d6_payload = _load(d6_audit_path)
    authorization, authorization_payload = _load(authorization_path)
    source, source_payload = _load(d7_path / "surface" / "surface.json", require_canonical=True)
    run, run_payload = _load(d7_path / "surface" / "run.json", require_canonical=True)
    d7_audit, d7_audit_payload = _load(d7_path.parents[1] / "psi0g_d7_first_real_input_surface_inspection.json")
    source_digest = _sha(source_payload)
    expected_allowed = {
        "fixture_only": [True, False],
        "provenance_class": [SOURCE_PROVENANCE, REAL_PROVENANCE],
    }
    if (authorization.get("schema_version") != "psi0g-d8.real-provenance-transition-authorization.v1" or
            authorization.get("status") != "AUTHORIZED" or
            authorization.get("source_d5_retention_id") != d5.retention_id or
            authorization.get("source_d6_retention_id") != run.get("retention_id") or
            authorization.get("source_d7_surface_digest") != source_digest or
            authorization.get("allowed_mutations") != expected_allowed or
            any(authorization.get("authority", {}).values())):
        _fail("AUTHORIZATION_INVALID")
    if (d6.get("source", {}).get("d5_retention_id") != d5.retention_id or
            d6.get("output", {}).get("retention_id") != run.get("retention_id") or
            d6.get("output", {}).get("f9_bundle_digest") != run.get("f9_bundle_digest") or
            d6.get("output", {}).get("f5_source_digest") != run.get("f5_source_digest") or
            d7_audit.get("run", {}).get("surface_digest") != source_digest or
            d7_audit.get("run", {}).get("retention_id") != run.get("retention_id")):
        _fail("LINEAGE_DRIFT")
    if (source.get("fixture_only") is not True or source.get("provenance_class") != SOURCE_PROVENANCE or
            not _authority_false(source)):
        _fail("SOURCE_SEMANTICS_INVALID")
    surface = dict(source)
    surface["fixture_only"] = False
    surface["provenance_class"] = REAL_PROVENANCE
    if _semantic(surface) != _semantic(source) or not _authority_false(surface):
        _fail("OPERATIONAL_SEMANTICS_DRIFT")
    surface_payload = _canonical(surface)
    semantic_digest = _digest(_semantic(source))
    lineage = {
        "d5_retention_id": d5.retention_id,
        "d5_manifest_digest": d5.manifest_digest,
        "d6_audit_digest": _sha(d6_payload),
        "d6_retention_id": run["retention_id"],
        "d7_run_digest": _sha(run_payload),
        "d7_audit_digest": _sha(d7_audit_payload),
        "source_surface_digest": source_digest,
        "authorization_digest": _sha(authorization_payload),
    }
    transition = {
        "schema_version": SCHEMA_VERSION, "status": "PASS",
        "lineage": lineage, "allowed_mutations": expected_allowed,
        "changed_paths": ["fixture_only", "provenance_class"],
        "source_semantic_digest": semantic_digest,
        "surface_semantic_digest": _digest(_semantic(surface)),
        "surface_digest": _sha(surface_payload),
        "authority": authorization["authority"],
    }
    transition["transition_digest"] = _digest(transition)
    payloads = {
        "source-surface.json": source_payload,
        "surface.json": surface_payload,
        "transition.json": _canonical(transition),
    }
    file_hashes = {name: _sha(payload) for name, payload in payloads.items()}
    publication_digest = _digest(file_hashes)
    payloads["hashes.json"] = _canonical({
        "schema_version": SCHEMA_VERSION, "files": file_hashes,
        "publication_digest": publication_digest,
    })
    target = Path(destination)
    if target.exists() or target.is_symlink() or not target.parent.is_dir():
        _fail("DESTINATION_NOT_NEW")
    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}.staging-", dir=target.parent))
    renamed = False
    try:
        for name in FILES:
            path = staging / name
            path.write_bytes(payloads[name])
            descriptor = os.open(path, os.O_RDONLY)
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
        renamed = True
        descriptor = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return replay_real_provenance_surface(
            target, d5_path=d5_path, d6_audit_path=d6_audit_path,
            d7_path=d7_path, authorization_path=authorization_path,
        )
    except BaseException:
        if renamed and target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        raise
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def replay_real_provenance_surface(
    path: Path, *, d5_path: Path, d6_audit_path: Path,
    d7_path: Path, authorization_path: Path,
) -> RealProvenanceSurface:
    source_dir = Path(path)
    if (source_dir.is_symlink() or not source_dir.is_dir() or
            tuple(sorted(item.name for item in source_dir.iterdir())) != tuple(sorted(FILES))):
        _fail("FILE_SET_DRIFT")
    documents = {}
    payloads = {}
    for name in FILES:
        documents[name], payloads[name] = _load(source_dir / name, require_canonical=True)
    expected_hashes = {name: _sha(payloads[name]) for name in FILES if name != "hashes.json"}
    if documents["hashes.json"] != {
        "schema_version": SCHEMA_VERSION, "files": expected_hashes,
        "publication_digest": _digest(expected_hashes),
    }:
        _fail("HASH_REPLAY_MISMATCH")
    external_source, external_payload = _load(d7_path / "surface" / "surface.json", require_canonical=True)
    d5 = replay_real_candidate_provenance(d5_path)
    d6, d6_payload = _load(d6_audit_path)
    authorization, authorization_payload = _load(authorization_path)
    run, run_payload = _load(d7_path / "surface" / "run.json", require_canonical=True)
    d7_audit, d7_audit_payload = _load(d7_path.parents[1] / "psi0g_d7_first_real_input_surface_inspection.json")
    source = documents["source-surface.json"]
    surface = documents["surface.json"]
    transition = documents["transition.json"]
    if payloads["source-surface.json"] != external_payload or source != external_source:
        _fail("SOURCE_BYTES_DRIFT")
    changed = {key for key in set(source) | set(surface) if source.get(key) != surface.get(key)}
    if (changed != {"fixture_only", "provenance_class"} or
            source.get("fixture_only") is not True or surface.get("fixture_only") is not False or
            source.get("provenance_class") != SOURCE_PROVENANCE or
            surface.get("provenance_class") != REAL_PROVENANCE or
            _semantic(source) != _semantic(surface) or not _authority_false(surface)):
        _fail("TRANSITION_SEMANTICS_DRIFT")
    lineage = {
        "d5_retention_id": d5.retention_id, "d5_manifest_digest": d5.manifest_digest,
        "d6_audit_digest": _sha(d6_payload), "d6_retention_id": run["retention_id"],
        "d7_run_digest": _sha(run_payload), "d7_audit_digest": _sha(d7_audit_payload),
        "source_surface_digest": _sha(external_payload),
        "authorization_digest": _sha(authorization_payload),
    }
    expected = {
        "schema_version": SCHEMA_VERSION, "status": "PASS", "lineage": lineage,
        "allowed_mutations": authorization["allowed_mutations"],
        "changed_paths": ["fixture_only", "provenance_class"],
        "source_semantic_digest": _digest(_semantic(source)),
        "surface_semantic_digest": _digest(_semantic(surface)),
        "surface_digest": _sha(payloads["surface.json"]),
        "authority": authorization["authority"],
    }
    expected["transition_digest"] = _digest(expected)
    if transition != expected or any(transition["authority"].values()):
        _fail("TRANSITION_REPLAY_MISMATCH")
    return RealProvenanceSurface(
        path=source_dir, source_surface_digest=lineage["source_surface_digest"],
        surface_digest=expected["surface_digest"],
        semantic_digest=expected["surface_semantic_digest"],
        transition_digest=expected["transition_digest"],
        publication_digest=documents["hashes.json"]["publication_digest"],
    )
