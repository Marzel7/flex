"""PSI0C-C1 provenance-preserving adapter for immutable PSI0B bundles."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Mapping, Sequence

from .production_shadow_assessment import (
    PSI0B_BUNDLE_DIGEST,
    PSI0B_G_DIGEST,
    PSI0C_A_DIGEST,
    QUERY_IDS,
    SCHEMAS,
    ShadowAssessmentBundle,
    ShadowAssessmentContract,
    _assess_shadow_rows,
    build_shadow_assessment_contract,
)
from .production_shadow_production_binding import ADAPTER_VERSION, AUTHORITY_CLASS
from .production_shadow_resource_ceiling import build_production_shadow_resource_ceiling_contract


ADAPTER_CONTRACT_VERSION = "psi0c-c2c.v1"
ENGINEERING_REVISION = "7351c4bb56fa5d86bc634c60c49f464deda52524"
PSI0C_B_CONTRACT_DIGEST = "3f2d112ba18b190e7acdf9c0dd9ddf552258b7ed75295ccb7cc470a981cc70e1"
PSI0C_B_QUALIFICATION_DIGEST = "e2ae86f50ae023195da0e94d2c69d8a2ff36831f27309b9476b699f42399a103"
EXPECTED_PREFLIGHT_DIGEST = "b2cfd09743c4ba21f7a61a816d8eff8b43e43a1b6e482c4ef9a7f2bd982dcca1"
PROVENANCE_CLASS = "PRODUCTION_DERIVED_IMMUTABLE_LOCAL_BUNDLE"
EXPECTED_BUNDLE_DIGEST = PSI0B_BUNDLE_DIGEST
FILES = ("accounting.json", "hashes.json", "results.json", "run.json")


class ImmutableShadowBundleAdapterError(RuntimeError):
    """Named fail-closed PSI0C-C1 adapter violation."""


@dataclass(frozen=True)
class ImmutableBundleAdapterContract:
    contract_version: str
    engineering_revision: str
    psi0c_b_contract_digest: str
    psi0c_b_qualification_digest: str
    expected_psi0b_bundle_digest: str
    expected_preflight_digest: str
    expected_files: tuple[str, ...]
    provenance_class: str
    serialized_schema_variants: tuple[tuple[str, tuple[str, ...]], ...]
    retries_allowed: bool
    grants_extraction_authority: bool
    grants_integration_authority: bool
    grants_activation_authority: bool
    contract_digest: str


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode() + b"\n"


def _digest(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def build_immutable_bundle_adapter_contract() -> ImmutableBundleAdapterContract:
    variants = (
        ("ops_selected_cohort", tuple(sorted((set(SCHEMAS["ops_selected_cohort"]) - {"rowid"}) | {"id"}))),
        ("snapshot_selected_cohort", tuple(sorted(set(SCHEMAS["snapshot_selected_cohort"]) - {"rowid"}))),
    )
    body = {
        "contract_version": ADAPTER_CONTRACT_VERSION,
        "engineering_revision": ENGINEERING_REVISION,
        "psi0c_b_contract_digest": PSI0C_B_CONTRACT_DIGEST,
        "psi0c_b_qualification_digest": PSI0C_B_QUALIFICATION_DIGEST,
        "expected_psi0b_bundle_digest": EXPECTED_BUNDLE_DIGEST,
        "expected_preflight_digest": EXPECTED_PREFLIGHT_DIGEST,
        "expected_files": FILES,
        "provenance_class": PROVENANCE_CLASS,
        "serialized_schema_variants": variants,
        "retries_allowed": False,
        "grants_extraction_authority": False,
        "grants_integration_authority": False,
        "grants_activation_authority": False,
    }
    return ImmutableBundleAdapterContract(**body, contract_digest=_digest(body))


def verify_immutable_bundle_adapter_contract(contract: ImmutableBundleAdapterContract) -> bool:
    if contract != build_immutable_bundle_adapter_contract():
        raise ImmutableShadowBundleAdapterError("PSI0C_C1_ADAPTER_CONTRACT_REPLAY_MISMATCH")
    if any((contract.retries_allowed, contract.grants_extraction_authority,
            contract.grants_integration_authority, contract.grants_activation_authority)):
        raise ImmutableShadowBundleAdapterError("PSI0C_C1_ADAPTER_AUTHORITY_DRIFT")
    return True


def _parse_canonical(files: Mapping[str, bytes]) -> dict[str, object]:
    if tuple(sorted(files)) != FILES:
        raise ImmutableShadowBundleAdapterError("PSI0C_C1_BUNDLE_FILE_SET_MISMATCH")
    documents = {}
    for name in FILES:
        payload = files[name]
        if not isinstance(payload, bytes):
            raise ImmutableShadowBundleAdapterError("PSI0C_C1_BUNDLE_BYTES_REQUIRED")
        try:
            document = json.loads(payload)
        except (TypeError, ValueError) as exc:
            raise ImmutableShadowBundleAdapterError("PSI0C_C1_INVALID_JSON") from exc
        if payload != _canonical(document):
            raise ImmutableShadowBundleAdapterError("PSI0C_C1_NONCANONICAL_JSON")
        documents[name] = document
    return documents


def _positive_identity(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _normalize_serialized_results(
    contract: ImmutableBundleAdapterContract,
    results: Mapping[str, object],
) -> dict[str, list[dict]]:
    variants = dict(contract.serialized_schema_variants)
    normalized: dict[str, list[dict]] = {}
    for query_id in QUERY_IDS:
        rows = results[query_id]
        if not isinstance(rows, list):
            raise ImmutableShadowBundleAdapterError("PSI0C_C2C_RESULT_ROWS_INVALID")
        logical = set(SCHEMAS[query_id])
        physical = set(variants.get(query_id, ()))
        converted = []
        for row in rows:
            if not isinstance(row, dict):
                raise ImmutableShadowBundleAdapterError("PSI0C_C2C_NON_OBJECT_ROW")
            keys = set(row)
            if keys == logical:
                if not _positive_identity(row.get("rowid")):
                    raise ImmutableShadowBundleAdapterError("PSI0C_C2C_INVALID_LOGICAL_ROW_IDENTITY")
                converted.append(dict(row))
                continue
            if query_id == "ops_selected_cohort" and "id" in keys and "rowid" in keys:
                raise ImmutableShadowBundleAdapterError("PSI0C_C2C_CONFLICTING_ROW_IDENTITIES")
            if keys != physical:
                raise ImmutableShadowBundleAdapterError("PSI0C_C2C_UNKNOWN_SERIALIZED_SCHEMA_VARIANT")
            copy = dict(row)
            if query_id == "ops_selected_cohort":
                identity = copy.pop("id")
            elif query_id == "snapshot_selected_cohort":
                identity = copy.get("snapshot_id")
            else:
                raise ImmutableShadowBundleAdapterError("PSI0C_C2C_UNAUTHORIZED_SCHEMA_VARIANT")
            if not _positive_identity(identity):
                raise ImmutableShadowBundleAdapterError("PSI0C_C2C_INVALID_PHYSICAL_ROW_IDENTITY")
            copy["rowid"] = identity
            if set(copy) != logical:
                raise ImmutableShadowBundleAdapterError("PSI0C_C2C_NORMALIZATION_SCHEMA_DRIFT")
            converted.append(copy)
        normalized[query_id] = converted
    return normalized


def assess_immutable_bundle_representation(
    adapter_contract: ImmutableBundleAdapterContract,
    assessment_contract: ShadowAssessmentContract,
    *,
    bundle_files: Mapping[str, bytes],
    cohort_mints: Sequence[str],
    output_directory: Path,
) -> ShadowAssessmentBundle:
    verify_immutable_bundle_adapter_contract(adapter_contract)
    if assessment_contract != build_shadow_assessment_contract():
        raise ImmutableShadowBundleAdapterError("PSI0C_C1_ASSESSMENT_CONTRACT_DRIFT")
    documents = _parse_canonical(bundle_files)
    hashes = documents["hashes.json"]
    data_names = ("accounting.json", "results.json", "run.json")
    actual = {name: sha256(bundle_files[name]).hexdigest() for name in data_names}
    bundle_digest = sha256(_canonical(actual)).hexdigest()
    if hashes != {"runner_version": ADAPTER_VERSION, "files": actual, "bundle_digest": bundle_digest}:
        raise ImmutableShadowBundleAdapterError("PSI0C_C1_BUNDLE_DIGEST_MISMATCH")
    if bundle_digest != adapter_contract.expected_psi0b_bundle_digest:
        raise ImmutableShadowBundleAdapterError("PSI0C_C1_BUNDLE_IDENTITY_DRIFT")

    run = documents["run.json"]
    if (run.get("runner_version") != ADAPTER_VERSION or
            run.get("preflight_digest") != EXPECTED_PREFLIGHT_DIGEST or
            run.get("authority_class") != AUTHORITY_CLASS or
            run.get("fixture_only") is not False or
            run.get("grants_production_execution_authority") is not True or
            run.get("grants_integration_authority") is not False or
            run.get("grants_activation_authority") is not False):
        raise ImmutableShadowBundleAdapterError("PSI0C_C1_RUN_LINEAGE_OR_AUTHORITY_DRIFT")

    results = documents["results.json"]
    if not isinstance(results, dict) or tuple(sorted(results)) != QUERY_IDS:
        raise ImmutableShadowBundleAdapterError("PSI0C_C1_QUERY_IDENTITY_DRIFT")
    accounting = documents["accounting.json"]
    queries = accounting.get("queries") if isinstance(accounting, dict) else None
    if not isinstance(queries, dict) or tuple(sorted(queries)) != QUERY_IDS:
        raise ImmutableShadowBundleAdapterError("PSI0C_C1_ACCOUNTING_QUERY_DRIFT")
    ceilings = {item.query_id: item for item in build_production_shadow_resource_ceiling_contract().query_ceilings}
    counted_rows = counted_bytes = 0
    for query_id in QUERY_IDS:
        rows = results[query_id]
        if not isinstance(rows, list):
            raise ImmutableShadowBundleAdapterError("PSI0C_C1_RESULT_ROWS_INVALID")
        size = len(_canonical(rows))
        entry = queries[query_id]
        if (entry.get("selected_rows") != len(rows) or entry.get("canonical_output_bytes") != size or
                len(rows) > ceilings[query_id].maximum_rows or size > ceilings[query_id].maximum_canonical_bytes):
            raise ImmutableShadowBundleAdapterError("PSI0C_C1_ACCOUNTING_OR_CEILING_DRIFT")
        counted_rows += len(rows)
        counted_bytes += size
    if (accounting.get("total_rows") != counted_rows or
            accounting.get("total_canonical_output_bytes") != counted_bytes or
            accounting.get("accounting_residual") != 0 or
            counted_rows > build_production_shadow_resource_ceiling_contract().maximum_total_rows or
            counted_bytes > build_production_shadow_resource_ceiling_contract().maximum_total_canonical_bytes):
        raise ImmutableShadowBundleAdapterError("PSI0C_C1_TOTAL_ACCOUNTING_OR_CEILING_DRIFT")

    lineage = {
        "psi0c_a_digest": PSI0C_A_DIGEST,
        "psi0b_g_digest": PSI0B_G_DIGEST,
        "psi0b_bundle_identity_digest": PSI0B_BUNDLE_DIGEST,
    }
    normalized_results = _normalize_serialized_results(adapter_contract, results)
    return _assess_shadow_rows(
        assessment_contract,
        cohort_mints=cohort_mints,
        synthetic_results=normalized_results,
        output_directory=output_directory,
        input_lineage=lineage,
        provenance_class=PROVENANCE_CLASS,
        fixture_only=False,
    )
