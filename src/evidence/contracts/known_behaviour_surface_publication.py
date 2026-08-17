"""PSI0F-F17 fixture end-to-end EB0.4H and known-behaviour publication."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Mapping

from .known_behaviour_operational_surface import (
    build_known_behaviour_operational_surface_contract,
)
from .known_behaviour_operational_surface_adapters import (
    build_known_behaviour_operational_surface_adapter_contract,
    project_immutable_summary_bytes,
)
from .operational_family_bundle import verify_operational_family_bundle
from .operational_family_rematerialization import (
    build_operational_family_rematerialization_contract,
    rematerialize_operational_family_bundle,
)
from .operational_family_retained_input_store import (
    build_operational_family_retained_input_store_contract,
    export_operational_family_retained_inputs,
)
from .operational_family_retention_bundle import (
    build_operational_family_retention_bundle_contract,
    replay_fixture_operational_family_retention_bundle,
)


CONTRACT_VERSION = "psi0f-f17.v1"
PUBLICATION_SCHEMA_VERSION = "psi0f-f17.publication.v1"
ENGINEERING_REVISION = "b4aeaf84d2848f99eaad9f953de8861527bc5c8f"
PSI0F_F16_DIGEST = "fa06d36f14ff5b8ee412657d3eb4b96a5a1d7ffe629f919d54a3f0b4d3e7235b"
ROOT_ENTRIES = ("eb0_4h", "surface")
SURFACE_FILES = ("run.json", "surface.json", "hashes.json")
AUTHORITY_KEYS = (
    "activation", "attribution", "cohort_mode", "deployment", "eb2",
    "evidence_mirror", "integration", "operator_identity", "policy", "ranking",
)
RUN_FIELDS = frozenset((
    "schema_version", "run_id", "engineering_revision", "f13_contract_digest",
    "retention_id", "retention_manifest_digest", "f9_bundle_digest", "f5_source_digest",
    "f1_contract_digest", "eb0_4h_bundle_digest", "psi0e_bundle_digest",
    "psi0f_adapter_contract_digest", "psi0f_surface_contract_digest", "surface_digest",
    "authority",
))


class KnownBehaviourSurfacePublicationError(RuntimeError):
    """Named fail-closed PSI0F-F17 violation."""


@dataclass(frozen=True)
class KnownBehaviourSurfacePublicationContract:
    contract_version: str
    engineering_revision: str
    psi0f_f16_digest: str
    root_entries: tuple[str, ...]
    surface_files: tuple[str, ...]
    fixture_only: bool
    output_requires_new_path: bool
    atomic_root_publication: bool
    cross_layer_join_allowed: bool
    real_capture_authorized: bool
    authority: Mapping[str, bool]
    contract_digest: str


@dataclass(frozen=True)
class KnownBehaviourSurfacePublication:
    output_directory: Path
    retention_id: str
    retention_manifest_digest: str
    f9_bundle_digest: str
    f5_source_digest: str
    eb0_4h_bundle_digest: str
    psi0e_bundle_digest: str
    surface_digest: str
    publication_digest: str
    contract_digest: str


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode() + b"\n"


def _digest(value: object) -> str:
    return sha256(_canonical(value).rstrip(b"\n")).hexdigest()


def _fail(code: str) -> None:
    raise KnownBehaviourSurfacePublicationError(f"PSI0F_F17_{code}")


def build_known_behaviour_surface_publication_contract() -> KnownBehaviourSurfacePublicationContract:
    body = {
        "contract_version": CONTRACT_VERSION, "engineering_revision": ENGINEERING_REVISION,
        "psi0f_f16_digest": PSI0F_F16_DIGEST, "root_entries": ROOT_ENTRIES,
        "surface_files": SURFACE_FILES, "fixture_only": True,
        "output_requires_new_path": True, "atomic_root_publication": True,
        "cross_layer_join_allowed": False, "real_capture_authorized": False,
        "authority": {key: False for key in AUTHORITY_KEYS},
    }
    return KnownBehaviourSurfacePublicationContract(**body, contract_digest=_digest(body))


def verify_known_behaviour_surface_publication_contract(
    contract: KnownBehaviourSurfacePublicationContract,
) -> bool:
    if contract != build_known_behaviour_surface_publication_contract():
        _fail("CONTRACT_REPLAY_MISMATCH")
    if (not contract.fixture_only or not contract.output_requires_new_path or
            not contract.atomic_root_publication or contract.cross_layer_join_allowed or
            contract.real_capture_authorized or any(contract.authority.values())):
        _fail("AUTHORITY_DRIFT")
    return True


def _file_map(path: Path) -> dict[str, bytes]:
    return {item.name: item.read_bytes() for item in path.iterdir() if item.is_file()}


def _write_exclusive(path: Path, payload: bytes) -> None:
    try:
        with path.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise KnownBehaviourSurfacePublicationError("PSI0F_F17_OVERWRITE_REJECTED") from exc


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _surface_payloads(run: Mapping[str, object], surface: bytes) -> tuple[dict[str, bytes], str]:
    payloads = {"run.json": _canonical(run), "surface.json": surface}
    file_digests = {name: sha256(payload).hexdigest() for name, payload in payloads.items()}
    publication_digest = _digest(file_digests)
    payloads["hashes.json"] = _canonical({
        "schema_version": PUBLICATION_SCHEMA_VERSION,
        "file_digests": file_digests,
        "publication_digest": publication_digest,
    })
    return payloads, publication_digest


def _expected_run(*, run_id: str, retained, exported, rematerialized, adapted) -> dict[str, object]:
    f13_contract = build_operational_family_retained_input_store_contract()
    f1_contract = build_operational_family_rematerialization_contract()
    surface_contract = build_known_behaviour_operational_surface_contract()
    return {
        "schema_version": PUBLICATION_SCHEMA_VERSION, "run_id": run_id,
        "engineering_revision": ENGINEERING_REVISION,
        "f13_contract_digest": f13_contract.contract_digest,
        "retention_id": retained, "retention_manifest_digest": exported.manifest_digest,
        "f9_bundle_digest": exported.bundle.bundle_digest,
        "f5_source_digest": rematerialized.source_digest,
        "f1_contract_digest": f1_contract.contract_digest,
        "eb0_4h_bundle_digest": rematerialized.bundle_digest,
        "psi0e_bundle_digest": adapted.psi0e_bundle_digest,
        "psi0f_adapter_contract_digest": adapted.adapter_contract_digest,
        "psi0f_surface_contract_digest": surface_contract.contract_digest,
        "surface_digest": adapted.surface.surface_digest,
        "authority": {key: False for key in AUTHORITY_KEYS},
    }


def publish_fixture_known_behaviour_surface(
    contract: KnownBehaviourSurfacePublicationContract, *, retained_source: Path,
    retention_id: str, psi0e_files: Mapping[str, bytes], output_directory: Path,
    run_id: str,
) -> KnownBehaviourSurfacePublication:
    verify_known_behaviour_surface_publication_contract(contract)
    output = Path(output_directory)
    if (not output.is_absolute() or not output.parent.is_dir() or output.parent.is_symlink() or
            output.exists() or output.is_symlink()):
        _fail("OUTPUT_NOT_NEW")
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.staging-", dir=output.parent))
    renamed = False
    try:
        exported = export_operational_family_retained_inputs(retained_source, retention_id)
        source = replay_fixture_operational_family_retention_bundle(
            build_operational_family_retention_bundle_contract(), exported.bundle.files,
        )
        f1_contract = build_operational_family_rematerialization_contract()
        rematerialized = rematerialize_operational_family_bundle(
            f1_contract, source.payload, staging / "eb0_4h", run_id=run_id,
            engineering_revision=ENGINEERING_REVISION,
        )
        verified_eb0_4h = verify_operational_family_bundle(staging / "eb0_4h")
        if verified_eb0_4h.bundle_digest != rematerialized.bundle_digest:
            _fail("EB0_4H_REPLAY_MISMATCH")
        adapter_contract = build_known_behaviour_operational_surface_adapter_contract()
        surface_contract = build_known_behaviour_operational_surface_contract()
        adapted = project_immutable_summary_bytes(
            adapter_contract, surface_contract, psi0e_files=psi0e_files,
            eb0_4_files=_file_map(staging / "eb0_4h"),
            expected_eb0_4_bundle_digest=rematerialized.bundle_digest,
        )
        surface_dir = staging / "surface"
        surface_dir.mkdir(mode=0o700)
        run = _expected_run(
            run_id=run_id, retained=retention_id, exported=exported,
            rematerialized=rematerialized, adapted=adapted,
        )
        payloads, publication_digest = _surface_payloads(run, adapted.surface.canonical_surface)
        for name in SURFACE_FILES:
            _write_exclusive(surface_dir / name, payloads[name])
        _fsync_directory(surface_dir)
        _fsync_directory(staging)
        if output.exists() or output.is_symlink():
            _fail("OUTPUT_NOT_NEW")
        os.rename(staging, output)
        renamed = True
        _fsync_directory(output.parent)
        verified = verify_fixture_known_behaviour_surface_publication(
            contract, output_directory=output, retained_source=retained_source,
            retention_id=retention_id, psi0e_files=psi0e_files,
        )
        if verified.publication_digest != publication_digest:
            _fail("POST_PUBLICATION_REPLAY_MISMATCH")
        return verified
    except Exception:
        if renamed and output.is_dir() and not output.is_symlink():
            shutil.rmtree(output)
        raise
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def verify_fixture_known_behaviour_surface_publication(
    contract: KnownBehaviourSurfacePublicationContract, *, output_directory: Path,
    retained_source: Path, retention_id: str, psi0e_files: Mapping[str, bytes],
) -> KnownBehaviourSurfacePublication:
    verify_known_behaviour_surface_publication_contract(contract)
    output = Path(output_directory)
    if (not output.is_dir() or output.is_symlink() or
            tuple(sorted(item.name for item in output.iterdir())) != ROOT_ENTRIES):
        _fail("ROOT_FILE_SET_MISMATCH")
    surface_dir = output / "surface"
    if (not surface_dir.is_dir() or surface_dir.is_symlink() or
            tuple(sorted(item.name for item in surface_dir.iterdir())) != tuple(sorted(SURFACE_FILES))):
        _fail("SURFACE_FILE_SET_MISMATCH")
    try:
        payloads = {name: (surface_dir / name).read_bytes() for name in SURFACE_FILES}
        documents = {name: json.loads(payloads[name]) for name in ("run.json", "hashes.json")}
        surface_document = json.loads(payloads["surface.json"])
    except Exception as exc:
        raise KnownBehaviourSurfacePublicationError("PSI0F_F17_INVALID_JSON") from exc
    if (payloads["run.json"] != _canonical(documents["run.json"]) or
            payloads["hashes.json"] != _canonical(documents["hashes.json"]) or
            payloads["surface.json"] != _canonical(surface_document)):
        _fail("NONCANONICAL_BYTES")
    file_digests = {
        name: sha256(payloads[name]).hexdigest() for name in ("run.json", "surface.json")
    }
    publication_digest = _digest(file_digests)
    if documents["hashes.json"] != {
        "schema_version": PUBLICATION_SCHEMA_VERSION, "file_digests": file_digests,
        "publication_digest": publication_digest,
    }:
        _fail("HASH_REPLAY_MISMATCH")
    run = documents["run.json"]
    if not isinstance(run, Mapping) or frozenset(run) != RUN_FIELDS or run["retention_id"] != retention_id:
        _fail("RUN_SCHEMA_OR_IDENTITY_DRIFT")
    exported = export_operational_family_retained_inputs(retained_source, retention_id)
    source = replay_fixture_operational_family_retention_bundle(
        build_operational_family_retention_bundle_contract(), exported.bundle.files,
    )
    eb0_4h = verify_operational_family_bundle(output / "eb0_4h")
    try:
        eb0_4h_run = json.loads((output / "eb0_4h" / "run.json").read_bytes())
    except Exception as exc:
        raise KnownBehaviourSurfacePublicationError("PSI0F_F17_EB0_4H_RUN_INVALID") from exc
    if (eb0_4h_run.get("run_id") != run["run_id"] or
            eb0_4h_run.get("engineering_revision") != ENGINEERING_REVISION):
        _fail("CROSS_PUBLICATION_RUN_IDENTITY_DRIFT")
    adapter_contract = build_known_behaviour_operational_surface_adapter_contract()
    surface_contract = build_known_behaviour_operational_surface_contract()
    adapted = project_immutable_summary_bytes(
        adapter_contract, surface_contract, psi0e_files=psi0e_files,
        eb0_4_files=_file_map(output / "eb0_4h"),
        expected_eb0_4_bundle_digest=eb0_4h.bundle_digest,
    )
    class Rematerialized:
        source_digest = source.source_digest
        bundle_digest = eb0_4h.bundle_digest
    expected_run = _expected_run(
        run_id=run["run_id"], retained=retention_id, exported=exported,
        rematerialized=Rematerialized, adapted=adapted,
    )
    if run != expected_run or payloads["surface.json"] != adapted.surface.canonical_surface:
        _fail("PUBLICATION_CONTENT_REPLAY_MISMATCH")
    return KnownBehaviourSurfacePublication(
        output_directory=output, retention_id=retention_id,
        retention_manifest_digest=exported.manifest_digest,
        f9_bundle_digest=exported.bundle.bundle_digest, f5_source_digest=source.source_digest,
        eb0_4h_bundle_digest=eb0_4h.bundle_digest,
        psi0e_bundle_digest=adapted.psi0e_bundle_digest,
        surface_digest=adapted.surface.surface_digest,
        publication_digest=publication_digest, contract_digest=contract.contract_digest,
    )
