"""PSI0E-E fixture-qualified atomic integration-envelope publisher."""

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
from .production_shadow_assessment_summary_consumer import REASON_CODES
from .production_shadow_integration_envelope import (
    AUTHORITY_KEYS,
    OUTPUT_PROVENANCE,
    SOURCE_PROVENANCE,
    build_integration_envelope_contract,
    verify_integration_envelope_contract,
)


CONTRACT_VERSION = "psi0e-e.v1"
ENGINEERING_REVISION = "d2f2c72aa0083bc62950fbfcf2940111d9d8e358"
PSI0E_D_DIGEST = "8834859723b64ce18c323cfd0b404f47e888805c05e620afcbb844ec057396a7"
PSI0E_C_INPUT_DIGEST = "89c8dee5c976bd587aa6fc7ec3a6194e38caeba5508013c8ddca976489038f35"
EXPECTED_ENVELOPE_DIGEST = "c8827678b2137f1aec864f86623514a92affb0c6df092d13b24c160f8fb90a9d"
PSI0E_A_CONTRACT_DIGEST = "2c8b2a296cb55ceb7da59ea4b063557749842041ba35251ec605e3f5f231350f"
PSI0E_A_QUALIFICATION_DIGEST = "69b4133f7d43029bcf08a1ee1143d8704e5a503558b8e2f3bcca6626fc97c57f"
PSI0E_B_CLOSURE_DIGEST = "5067a9668afd21fcb4f92442ced9ad8726b1ecde9855bd251d291fd9ce7bf8f8"
FILES = ("contract.json", "envelope.json", "hashes.json")
ENVELOPE_KEYS = {
    "schema_version", "contract_digest", "source_identities", "default_off",
    "consumer_enabled", "provenance_class", "source_provenance_class",
    "cohort_count", "surfaces", "unresolved_conflict_count",
    "orphan_unmatched_count", "reason_codes", "interpretation", "authority",
}
SOURCE_IDENTITY_KEYS = {
    "psi0d_bundle_digest", "psi0d_projection_digest", "psi0d_hashes_file_digest",
    "psi0d_h_contract_digest", "psi0d_b_consumer_digest",
}
SURFACE_KEYS = {
    "coverage_numerator", "coverage_denominator", "row_count",
    "unique_mint_count", "duplicate_row_count", "unmatched_row_count",
    "missingness_semantics",
}
INTERPRETATION_KEYS = {
    "threshold_applied", "negative_outcome_inferred", "duplicates_collapsed",
    "conflicts_resolved", "entities_ranked_or_selected",
}


class IntegrationEnvelopePublicationError(RuntimeError):
    """Named fail-closed PSI0E-E publication violation."""


@dataclass(frozen=True)
class IntegrationEnvelopePublicationContract:
    contract_version: str
    engineering_revision: str
    psi0e_d_digest: str
    psi0e_c_input_digest: str
    expected_envelope_digest: str
    psi0e_a_contract_digest: str
    psi0e_a_qualification_digest: str
    psi0e_b_closure_digest: str
    expected_provenance: str
    expected_source_provenance: str
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
class PublishedIntegrationEnvelopeBundle:
    output_directory: str
    envelope_digest: str
    bundle_digest: str
    file_digests: Tuple[Tuple[str, str], ...]
    contract_digest: str


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode() + b"\n"


def _digest(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def build_integration_envelope_publication_contract() -> IntegrationEnvelopePublicationContract:
    body = {
        "contract_version": CONTRACT_VERSION,
        "engineering_revision": ENGINEERING_REVISION,
        "psi0e_d_digest": PSI0E_D_DIGEST,
        "psi0e_c_input_digest": PSI0E_C_INPUT_DIGEST,
        "expected_envelope_digest": EXPECTED_ENVELOPE_DIGEST,
        "psi0e_a_contract_digest": PSI0E_A_CONTRACT_DIGEST,
        "psi0e_a_qualification_digest": PSI0E_A_QUALIFICATION_DIGEST,
        "psi0e_b_closure_digest": PSI0E_B_CLOSURE_DIGEST,
        "expected_provenance": OUTPUT_PROVENANCE,
        "expected_source_provenance": SOURCE_PROVENANCE,
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
    return IntegrationEnvelopePublicationContract(**body, contract_digest=_digest(serial))


def verify_integration_envelope_publication_contract(
    contract: IntegrationEnvelopePublicationContract,
) -> bool:
    if contract != build_integration_envelope_publication_contract():
        raise IntegrationEnvelopePublicationError("PSI0E_E_CONTRACT_REPLAY_MISMATCH")
    forbidden = (
        contract.retries_allowed, contract.overwrite_allowed, contract.retains_source_values,
        contract.grants_policy_authority, contract.grants_ranking_authority,
        contract.grants_integration_authority, contract.grants_deployment_authority,
        contract.grants_activation_authority,
    )
    if any(forbidden):
        raise IntegrationEnvelopePublicationError("PSI0E_E_AUTHORITY_DRIFT")
    return True


def _count(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise IntegrationEnvelopePublicationError("PSI0E_E_INVALID_ACCOUNTING")
    return value


def _validate_envelope(contract: IntegrationEnvelopePublicationContract, payload: bytes) -> dict:
    if not isinstance(payload, bytes):
        raise IntegrationEnvelopePublicationError("PSI0E_E_BYTES_REQUIRED")
    try:
        envelope = json.loads(payload)
    except (TypeError, ValueError) as exc:
        raise IntegrationEnvelopePublicationError("PSI0E_E_INVALID_JSON") from exc
    if payload != _canonical(envelope):
        raise IntegrationEnvelopePublicationError("PSI0E_E_NONCANONICAL_ENVELOPE")
    if sha256(payload).hexdigest() != contract.expected_envelope_digest:
        raise IntegrationEnvelopePublicationError("PSI0E_E_ENVELOPE_DIGEST_MISMATCH")
    if not isinstance(envelope, Mapping) or set(envelope) != ENVELOPE_KEYS:
        raise IntegrationEnvelopePublicationError("PSI0E_E_ENVELOPE_SCHEMA_DRIFT")

    upstream = build_integration_envelope_contract()
    verify_integration_envelope_contract(upstream)
    expected_sources = {
        "psi0d_bundle_digest": upstream.psi0d_bundle_digest,
        "psi0d_projection_digest": upstream.psi0d_projection_digest,
        "psi0d_hashes_file_digest": upstream.psi0d_hashes_file_digest,
        "psi0d_h_contract_digest": upstream.psi0d_h_contract_digest,
        "psi0d_b_consumer_digest": upstream.psi0d_b_consumer_digest,
    }
    sources = envelope["source_identities"]
    if (envelope["schema_version"] != "psi0e-a.descriptive-integration-envelope.v1" or
            envelope["contract_digest"] != contract.psi0e_a_contract_digest or
            not isinstance(sources, Mapping) or set(sources) != SOURCE_IDENTITY_KEYS or
            sources != expected_sources or envelope["default_off"] is not True or
            envelope["consumer_enabled"] is not False or
            envelope["provenance_class"] != contract.expected_provenance or
            envelope["source_provenance_class"] != contract.expected_source_provenance):
        raise IntegrationEnvelopePublicationError("PSI0E_E_LINEAGE_OR_PROVENANCE_DRIFT")

    authority = envelope["authority"]
    interpretation = envelope["interpretation"]
    if (not isinstance(authority, Mapping) or set(authority) != AUTHORITY_KEYS or any(authority.values()) or
            not isinstance(interpretation, Mapping) or set(interpretation) != INTERPRETATION_KEYS or
            any(interpretation.values())):
        raise IntegrationEnvelopePublicationError("PSI0E_E_AUTHORITY_OR_INTERPRETATION_DRIFT")

    cohort = _count(envelope["cohort_count"])
    if cohort < 1:
        raise IntegrationEnvelopePublicationError("PSI0E_E_INVALID_ACCOUNTING")
    surfaces = envelope["surfaces"]
    if not isinstance(surfaces, Mapping) or set(surfaces) != set(QUERY_IDS):
        raise IntegrationEnvelopePublicationError("PSI0E_E_QUERY_IDENTITY_DRIFT")
    unmatched_total = 0
    for query_id in QUERY_IDS:
        item = surfaces[query_id]
        if not isinstance(item, Mapping) or set(item) != SURFACE_KEYS:
            raise IntegrationEnvelopePublicationError("PSI0E_E_SURFACE_SCHEMA_DRIFT")
        values = {key: _count(item[key]) for key in SURFACE_KEYS if key != "missingness_semantics"}
        if (item["missingness_semantics"] != "ABSENT_NOT_NEGATIVE" or
                values["coverage_denominator"] != cohort or
                values["coverage_numerator"] > min(cohort, values["unique_mint_count"]) or
                values["unique_mint_count"] > values["row_count"] or
                values["duplicate_row_count"] != values["row_count"] - values["unique_mint_count"] or
                values["unmatched_row_count"] > values["row_count"]):
            raise IntegrationEnvelopePublicationError("PSI0E_E_INCONSISTENT_ACCOUNTING")
        unmatched_total += values["unmatched_row_count"]
    conflicts = _count(envelope["unresolved_conflict_count"])
    unmatched = _count(envelope["orphan_unmatched_count"])
    if unmatched != unmatched_total:
        raise IntegrationEnvelopePublicationError("PSI0E_E_INCONSISTENT_ACCOUNTING")
    reasons = envelope["reason_codes"]
    if (not isinstance(reasons, list) or reasons != sorted(set(reasons)) or
            not set(reasons).issubset(REASON_CODES) or
            "PSI0C_B_ABSENCE_IS_NOT_NEGATIVE" not in reasons or
            (conflicts > 0 and "PSI0C_B_CONFLICT_PRESERVED_UNRESOLVED" not in reasons) or
            (unmatched > 0 and "PSI0C_B_UNMATCHED_KEY_RECORDED" not in reasons)):
        raise IntegrationEnvelopePublicationError("PSI0E_E_REASON_CODE_DRIFT")
    return dict(envelope)


def _manifest(contract: IntegrationEnvelopePublicationContract) -> dict:
    value = asdict(contract)
    value["expected_files"] = list(value["expected_files"])
    return {
        "schema_version": "psi0e-e.publication-manifest.v1",
        "publication_contract": value,
        "envelope_digest": contract.expected_envelope_digest,
        "input_digest": contract.psi0e_c_input_digest,
        "provenance_class": contract.expected_provenance,
        "default_off": True,
        "consumer_enabled": False,
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
        raise IntegrationEnvelopePublicationError("PSI0E_E_PUBLISHED_FILE_SET_MISMATCH")
    return {name: (path / name).read_bytes() for name in FILES}


def verify_published_integration_envelope_bundle(
    contract: IntegrationEnvelopePublicationContract,
    output_directory: Path | str,
) -> PublishedIntegrationEnvelopeBundle:
    verify_integration_envelope_publication_contract(contract)
    output = Path(output_directory)
    files = _read_bundle(output)
    _validate_envelope(contract, files["envelope.json"])
    if files["contract.json"] != _canonical(_manifest(contract)):
        raise IntegrationEnvelopePublicationError("PSI0E_E_PUBLISHED_CONTRACT_MISMATCH")
    try:
        hashes = json.loads(files["hashes.json"])
    except (TypeError, ValueError) as exc:
        raise IntegrationEnvelopePublicationError("PSI0E_E_PUBLISHED_HASHES_INVALID") from exc
    if files["hashes.json"] != _canonical(hashes):
        raise IntegrationEnvelopePublicationError("PSI0E_E_PUBLISHED_HASHES_INVALID")
    expected_file_digests = {
        name: sha256(files[name]).hexdigest()
        for name in ("contract.json", "envelope.json")
    }
    expected_hashes = {
        "file_digests": expected_file_digests,
        "bundle_digest": _digest(expected_file_digests),
    }
    if hashes != expected_hashes:
        raise IntegrationEnvelopePublicationError("PSI0E_E_PUBLISHED_HASH_REPLAY_MISMATCH")
    return PublishedIntegrationEnvelopeBundle(
        output_directory=str(output),
        envelope_digest=contract.expected_envelope_digest,
        bundle_digest=expected_hashes["bundle_digest"],
        file_digests=tuple(sorted(expected_file_digests.items())),
        contract_digest=contract.contract_digest,
    )


def publish_integration_envelope_fixture(
    contract: IntegrationEnvelopePublicationContract,
    *,
    envelope_bytes: bytes,
    output_directory: Path | str,
) -> PublishedIntegrationEnvelopeBundle:
    verify_integration_envelope_publication_contract(contract)
    _validate_envelope(contract, envelope_bytes)
    output = Path(output_directory)
    if output.exists() or output.is_symlink():
        raise IntegrationEnvelopePublicationError("PSI0E_E_OUTPUT_REUSE")
    if not output.parent.is_dir():
        raise IntegrationEnvelopePublicationError("PSI0E_E_OUTPUT_PARENT_INVALID")
    stage = Path(tempfile.mkdtemp(prefix=f".{output.name}.tmp-", dir=output.parent))
    renamed = False
    success = False
    try:
        contract_bytes = _canonical(_manifest(contract))
        payloads = {"contract.json": contract_bytes, "envelope.json": envelope_bytes}
        file_digests = {name: sha256(payload).hexdigest() for name, payload in payloads.items()}
        hashes_bytes = _canonical({
            "file_digests": file_digests,
            "bundle_digest": _digest(file_digests),
        })
        for name, payload in (
            ("envelope.json", envelope_bytes),
            ("contract.json", contract_bytes),
            ("hashes.json", hashes_bytes),
        ):
            _write_fsynced(stage / name, payload)
        _fsync_directory(stage)
        if output.exists() or output.is_symlink():
            raise IntegrationEnvelopePublicationError("PSI0E_E_OUTPUT_REUSE")
        os.replace(stage, output)
        renamed = True
        _fsync_directory(output.parent)
        result = verify_published_integration_envelope_bundle(contract, output)
        success = True
        return result
    except IntegrationEnvelopePublicationError:
        raise
    except Exception as exc:
        raise IntegrationEnvelopePublicationError("PSI0E_E_PUBLICATION_IO_FAILURE") from exc
    finally:
        cleanup = output if renamed else stage
        if not success and cleanup.exists():
            shutil.rmtree(cleanup, ignore_errors=True)
