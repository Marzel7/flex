"""PSI0D-H fixture-qualified atomic descriptive-projection publisher."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Mapping, Tuple

from .production_shadow_assessment import QUERY_IDS
from .production_shadow_assessment_summary_consumer import (
    PRODUCTION_DERIVED_PROVENANCE_CLASS,
    REASON_CODES,
    build_assessment_summary_consumer_contract,
)


CONTRACT_VERSION = "psi0d-h.v1"
ENGINEERING_REVISION = "9ef7467327b74dbf435fc00ddc26c99a77f5b33e"
PSI0D_G_DIGEST = "1ba3574d2caff53c538f3990a86604edc48052e7f9fd926e4b8f1242156e468d"
PSI0D_F_DIGEST = "9e755dcc6fceef6ef27664eb8531cff0478b0b6cc252ded22ee84adfefd1efac"
PSI0D_D_DIGEST = "48e4480d741793c78dda8413ce8fa233c849d3781538bc32c0283a436f00bd7d"
PSI0D_B_DIGEST = "a9e368cceede689736ca234891394551bda098df3b51ebfebf2e72f32aeb51f6"
EXPECTED_PROJECTION_DIGEST = "482461d10319e657bbb4df37b4cfa4be526ada21b4dbd3186ce43834ecb2d136"
EXPECTED_PROVENANCE = PRODUCTION_DERIVED_PROVENANCE_CLASS
FILES = ("contract.json", "hashes.json", "projection.json")
PROJECTION_KEYS = {
    "schema_version", "contract_digest", "input_lineage", "fixture_only",
    "default_off", "provenance_class", "cohort_count", "surfaces",
    "unresolved_conflict_count", "orphan_unmatched_count", "reason_codes",
    "interpretation", "authority",
}
SURFACE_KEYS = {
    "row_count", "unique_mint_count", "coverage_numerator",
    "coverage_denominator", "duplicate_row_count", "unmatched_row_count",
    "missingness_semantics",
}
AUTHORITY_KEYS = {"policy", "ranking", "integration", "deployment", "activation"}
INTERPRETATION_KEYS = {
    "threshold_applied", "negative_outcome_inferred", "duplicates_collapsed",
    "conflicts_resolved", "entities_ranked_or_selected",
}


class ProjectionPublicationError(RuntimeError):
    """Named fail-closed PSI0D-H publication violation."""


@dataclass(frozen=True)
class ProjectionPublicationContract:
    contract_version: str
    engineering_revision: str
    psi0d_g_digest: str
    psi0d_f_digest: str
    psi0d_d_digest: str
    psi0d_b_digest: str
    expected_projection_digest: str
    expected_provenance: str
    expected_files: Tuple[str, ...]
    retries_allowed: bool
    overwrite_allowed: bool
    retains_source_values: bool
    grants_policy_authority: bool
    grants_ranking_authority: bool
    grants_integration_authority: bool
    grants_deployment_authority: bool
    grants_activation_authority: bool
    contract_digest: str


@dataclass(frozen=True)
class PublishedProjectionBundle:
    output_directory: str
    projection_digest: str
    bundle_digest: str
    file_digests: Tuple[Tuple[str, str], ...]
    contract_digest: str


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode() + b"\n"


def _digest(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def build_projection_publication_contract() -> ProjectionPublicationContract:
    body = {
        "contract_version": CONTRACT_VERSION,
        "engineering_revision": ENGINEERING_REVISION,
        "psi0d_g_digest": PSI0D_G_DIGEST,
        "psi0d_f_digest": PSI0D_F_DIGEST,
        "psi0d_d_digest": PSI0D_D_DIGEST,
        "psi0d_b_digest": PSI0D_B_DIGEST,
        "expected_projection_digest": EXPECTED_PROJECTION_DIGEST,
        "expected_provenance": EXPECTED_PROVENANCE,
        "expected_files": FILES,
        "retries_allowed": False,
        "overwrite_allowed": False,
        "retains_source_values": False,
        "grants_policy_authority": False,
        "grants_ranking_authority": False,
        "grants_integration_authority": False,
        "grants_deployment_authority": False,
        "grants_activation_authority": False,
    }
    serial = {key: list(value) if isinstance(value, tuple) else value for key, value in body.items()}
    return ProjectionPublicationContract(**body, contract_digest=_digest(serial))


def verify_projection_publication_contract(contract: ProjectionPublicationContract) -> bool:
    if contract != build_projection_publication_contract():
        raise ProjectionPublicationError("PSI0D_H_CONTRACT_REPLAY_MISMATCH")
    forbidden = (
        contract.retries_allowed, contract.overwrite_allowed, contract.retains_source_values,
        contract.grants_policy_authority, contract.grants_ranking_authority,
        contract.grants_integration_authority, contract.grants_deployment_authority,
        contract.grants_activation_authority,
    )
    if any(forbidden):
        raise ProjectionPublicationError("PSI0D_H_AUTHORITY_DRIFT")
    return True


def _count(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ProjectionPublicationError("PSI0D_H_INVALID_ACCOUNTING")
    return value


def _validate_projection(contract: ProjectionPublicationContract, payload: bytes) -> dict:
    if not isinstance(payload, bytes):
        raise ProjectionPublicationError("PSI0D_H_BYTES_REQUIRED")
    try:
        projection = json.loads(payload)
    except (TypeError, ValueError) as exc:
        raise ProjectionPublicationError("PSI0D_H_INVALID_JSON") from exc
    if payload != _canonical(projection):
        raise ProjectionPublicationError("PSI0D_H_NONCANONICAL_PROJECTION")
    if sha256(payload).hexdigest() != contract.expected_projection_digest:
        raise ProjectionPublicationError("PSI0D_H_PROJECTION_DIGEST_MISMATCH")
    if not isinstance(projection, Mapping) or set(projection) != PROJECTION_KEYS:
        raise ProjectionPublicationError("PSI0D_H_PROJECTION_SCHEMA_DRIFT")
    consumer = build_assessment_summary_consumer_contract()
    expected_lineage = {
        "psi0d_a_digest": consumer.psi0d_a_digest,
        "psi0c_d_digest": consumer.psi0c_d_digest,
        "psi0c_c_assessment_identity": consumer.psi0c_c_assessment_identity,
        "psi0c_c_bundle_identity": consumer.psi0c_c_bundle_identity,
        "psi0c_b_digest": consumer.psi0c_b_digest,
    }
    if (projection["schema_version"] != "psi0d-b.descriptive-projection.v1" or
            projection["contract_digest"] != contract.psi0d_b_digest or
            projection["input_lineage"] != expected_lineage or
            projection["fixture_only"] is not False or projection["default_off"] is not True or
            projection["provenance_class"] != contract.expected_provenance):
        raise ProjectionPublicationError("PSI0D_H_LINEAGE_OR_PROVENANCE_DRIFT")
    authority = projection["authority"]
    interpretation = projection["interpretation"]
    if (not isinstance(authority, Mapping) or set(authority) != AUTHORITY_KEYS or any(authority.values()) or
            not isinstance(interpretation, Mapping) or set(interpretation) != INTERPRETATION_KEYS or
            any(interpretation.values())):
        raise ProjectionPublicationError("PSI0D_H_AUTHORITY_OR_INTERPRETATION_DRIFT")
    cohort = _count(projection["cohort_count"])
    if cohort < 1:
        raise ProjectionPublicationError("PSI0D_H_INVALID_ACCOUNTING")
    surfaces = projection["surfaces"]
    if not isinstance(surfaces, Mapping) or set(surfaces) != set(QUERY_IDS):
        raise ProjectionPublicationError("PSI0D_H_QUERY_IDENTITY_DRIFT")
    unmatched_total = 0
    for query_id in QUERY_IDS:
        item = surfaces[query_id]
        if not isinstance(item, Mapping) or set(item) != SURFACE_KEYS:
            raise ProjectionPublicationError("PSI0D_H_SURFACE_SCHEMA_DRIFT")
        values = {key: _count(item[key]) for key in SURFACE_KEYS if key != "missingness_semantics"}
        if (item["missingness_semantics"] != "ABSENT_NOT_NEGATIVE" or
                values["coverage_denominator"] != cohort or
                values["coverage_numerator"] > min(cohort, values["unique_mint_count"]) or
                values["unique_mint_count"] > values["row_count"] or
                values["duplicate_row_count"] != values["row_count"] - values["unique_mint_count"] or
                values["unmatched_row_count"] > values["row_count"]):
            raise ProjectionPublicationError("PSI0D_H_INCONSISTENT_ACCOUNTING")
        unmatched_total += values["unmatched_row_count"]
    conflicts = _count(projection["unresolved_conflict_count"])
    unmatched = _count(projection["orphan_unmatched_count"])
    if unmatched != unmatched_total:
        raise ProjectionPublicationError("PSI0D_H_INCONSISTENT_ACCOUNTING")
    reasons = projection["reason_codes"]
    if (not isinstance(reasons, list) or reasons != sorted(set(reasons)) or
            not set(reasons).issubset(REASON_CODES) or
            "PSI0C_B_ABSENCE_IS_NOT_NEGATIVE" not in reasons or
            (conflicts > 0 and "PSI0C_B_CONFLICT_PRESERVED_UNRESOLVED" not in reasons) or
            (unmatched > 0 and "PSI0C_B_UNMATCHED_KEY_RECORDED" not in reasons)):
        raise ProjectionPublicationError("PSI0D_H_REASON_CODE_DRIFT")
    return dict(projection)


def _manifest(contract: ProjectionPublicationContract) -> dict:
    value = asdict(contract)
    value["expected_files"] = list(value["expected_files"])
    return {
        "schema_version": "psi0d-h.publication-manifest.v1",
        "publication_contract": value,
        "projection_digest": contract.expected_projection_digest,
        "source_provenance": contract.expected_provenance,
        "authority": {
            "policy": False, "ranking": False, "integration": False,
            "deployment": False, "activation": False,
        },
    }


def _write_fsynced(path: Path, payload: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _read_bundle(path: Path) -> dict[str, bytes]:
    if not path.is_dir() or tuple(sorted(item.name for item in path.iterdir())) != FILES:
        raise ProjectionPublicationError("PSI0D_H_PUBLISHED_FILE_SET_MISMATCH")
    return {name: (path / name).read_bytes() for name in FILES}


def verify_published_projection_bundle(
    contract: ProjectionPublicationContract,
    output_directory: Path | str,
) -> PublishedProjectionBundle:
    verify_projection_publication_contract(contract)
    output = Path(output_directory)
    files = _read_bundle(output)
    _validate_projection(contract, files["projection.json"])
    if files["contract.json"] != _canonical(_manifest(contract)):
        raise ProjectionPublicationError("PSI0D_H_PUBLISHED_CONTRACT_MISMATCH")
    try:
        hashes = json.loads(files["hashes.json"])
    except (TypeError, ValueError) as exc:
        raise ProjectionPublicationError("PSI0D_H_PUBLISHED_HASHES_INVALID") from exc
    if files["hashes.json"] != _canonical(hashes):
        raise ProjectionPublicationError("PSI0D_H_PUBLISHED_HASHES_INVALID")
    expected_file_digests = {
        name: sha256(files[name]).hexdigest()
        for name in ("contract.json", "projection.json")
    }
    expected_hashes = {
        "file_digests": expected_file_digests,
        "bundle_digest": _digest(expected_file_digests),
    }
    if hashes != expected_hashes:
        raise ProjectionPublicationError("PSI0D_H_PUBLISHED_HASH_REPLAY_MISMATCH")
    return PublishedProjectionBundle(
        output_directory=str(output),
        projection_digest=contract.expected_projection_digest,
        bundle_digest=expected_hashes["bundle_digest"],
        file_digests=tuple(sorted(expected_file_digests.items())),
        contract_digest=contract.contract_digest,
    )


def publish_projection_fixture(
    contract: ProjectionPublicationContract,
    *,
    projection_bytes: bytes,
    output_directory: Path | str,
) -> PublishedProjectionBundle:
    verify_projection_publication_contract(contract)
    _validate_projection(contract, projection_bytes)
    output = Path(output_directory)
    if output.exists() or output.is_symlink():
        raise ProjectionPublicationError("PSI0D_H_OUTPUT_REUSE")
    if not output.parent.is_dir():
        raise ProjectionPublicationError("PSI0D_H_OUTPUT_PARENT_INVALID")
    stage = Path(tempfile.mkdtemp(prefix=f".{output.name}.tmp-", dir=output.parent))
    renamed = False
    success = False
    try:
        contract_bytes = _canonical(_manifest(contract))
        payloads = {"contract.json": contract_bytes, "projection.json": projection_bytes}
        file_digests = {name: sha256(payload).hexdigest() for name, payload in payloads.items()}
        hashes_bytes = _canonical({
            "file_digests": file_digests,
            "bundle_digest": _digest(file_digests),
        })
        for name, payload in (
            ("projection.json", projection_bytes),
            ("contract.json", contract_bytes),
            ("hashes.json", hashes_bytes),
        ):
            _write_fsynced(stage / name, payload)
        _fsync_directory(stage)
        if output.exists() or output.is_symlink():
            raise ProjectionPublicationError("PSI0D_H_OUTPUT_REUSE")
        os.replace(stage, output)
        renamed = True
        _fsync_directory(output.parent)
        result = verify_published_projection_bundle(contract, output)
        success = True
        return result
    except ProjectionPublicationError:
        raise
    except Exception as exc:
        raise ProjectionPublicationError("PSI0D_H_PUBLICATION_IO_FAILURE") from exc
    finally:
        cleanup = output if renamed else stage
        if not success and cleanup.exists():
            shutil.rmtree(cleanup, ignore_errors=True)
