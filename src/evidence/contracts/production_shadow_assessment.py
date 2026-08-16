"""PSI0C-B fixture-only shadow coverage/conflict/missingness contract."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from pathlib import Path
import shutil
from typing import Mapping, Sequence, Tuple

from .production_shadow_query_plan import build_psi0a_d2a_rebound_contract
from .production_shadow_resource_ceiling import build_production_shadow_resource_ceiling_contract


CONTRACT_VERSION = "psi0c-b.v1"
ENGINEERING_REVISION = "9bbc4c077ecb189952c900539c8a8d0031b59269"
PSI0C_A_DIGEST = "430dbeae5e87f2971e4e29927d2c6b86abc9c70be8fff022c5b724e1e35417b3"
PSI0B_G_DIGEST = "efadf9e061529af1fc690a253be4e8e29d7058d06d6a61be6105890b4e5e90cd"
PSI0B_BUNDLE_DIGEST = "370c815a4bacc640874d798c168ff812c2efd205d6de497fdcfb79b4005351b9"
PSI0A_E_DIGEST = "f5eea8b9f8ba6b102f57e4ae59eb35eb8f0e23d3f8ac0f493f35671f8271f736"
PSI0A_G_DIGEST = "c9ff90b6cbfb332bc00691c87528abf5f6b6158b54072bfb48c60d9f920e5bab"
AUTHORITY_CLASS = "FIXTURE_ONLY_NON_AUTHORITATIVE_SHADOW_ASSESSMENT"
QUERY_IDS = (
    "creator_selected_cohort",
    "evidence_launch_facts",
    "main_selected_cohort",
    "ops_selected_cohort",
    "snapshot_selected_cohort",
)
FILES = {"contract.json", "assessment.json", "hashes.json"}

SCHEMAS = {
    "creator_selected_cohort": ("rowid", "creator_address", "mint", "created_at"),
    "evidence_launch_facts": (
        "rowid", "fact_family", "payload_json", "raw_artifact_digest", "acquired_at",
        "source_id", "source_version", "verification_state",
    ),
    "main_selected_cohort": (
        "rowid", "mint", "migrated_at", "first_observed_mc", "first_observed_price",
        "first_observed_at", "first_observed_source", "first_observed_confidence",
        "pf_ws_creator", "creator_mismatch",
    ),
    "ops_selected_cohort": (
        "rowid", "mint", "creator_wallet", "create_signature", "create_time", "create_slot",
        "creator_extraction_method", "confidence", "recorded_at",
    ),
    "snapshot_selected_cohort": (
        "rowid", "snapshot_id", "mint", "price_usd", "market_cap", "source",
        "captured_at", "created_at",
    ),
}


class ProductionShadowAssessmentError(RuntimeError):
    """Named fail-closed PSI0C-B contract violation."""


@dataclass(frozen=True)
class ShadowAssessmentContract:
    contract_version: str
    engineering_revision: str
    psi0c_a_digest: str
    psi0b_g_digest: str
    psi0b_bundle_identity_digest: str
    query_contract_digest: str
    resource_ceiling_digest: str
    abort_isolation_digest: str
    query_ids: Tuple[str, ...]
    result_schemas: Tuple[Tuple[str, Tuple[str, ...]], ...]
    authority_class: str
    accepts_production_rows: bool
    grants_policy_authority: bool
    grants_ranking_authority: bool
    grants_integration_authority: bool
    grants_activation_authority: bool
    negative_inference_from_absence_allowed: bool
    conflict_resolution_allowed: bool
    contract_digest: str


@dataclass(frozen=True)
class ShadowAssessmentBundle:
    output_directory: Path
    assessment_digest: str
    file_digests: Mapping[str, str]


def _json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode() + b"\n"


def _digest(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def build_shadow_assessment_contract() -> ShadowAssessmentContract:
    query_contract = build_psi0a_d2a_rebound_contract()
    ceiling = build_production_shadow_resource_ceiling_contract()
    body = {
        "contract_version": CONTRACT_VERSION,
        "engineering_revision": ENGINEERING_REVISION,
        "psi0c_a_digest": PSI0C_A_DIGEST,
        "psi0b_g_digest": PSI0B_G_DIGEST,
        "psi0b_bundle_identity_digest": PSI0B_BUNDLE_DIGEST,
        "query_contract_digest": query_contract.contract_digest,
        "resource_ceiling_digest": ceiling.contract_digest,
        "abort_isolation_digest": PSI0A_G_DIGEST,
        "query_ids": QUERY_IDS,
        "result_schemas": tuple((query_id, SCHEMAS[query_id]) for query_id in QUERY_IDS),
        "authority_class": AUTHORITY_CLASS,
        "accepts_production_rows": False,
        "grants_policy_authority": False,
        "grants_ranking_authority": False,
        "grants_integration_authority": False,
        "grants_activation_authority": False,
        "negative_inference_from_absence_allowed": False,
        "conflict_resolution_allowed": False,
    }
    serial = {**body, "result_schemas": [[key, list(value)] for key, value in body["result_schemas"]]}
    return ShadowAssessmentContract(**body, contract_digest=_digest(serial))


def verify_shadow_assessment_contract(contract: ShadowAssessmentContract) -> bool:
    if contract != build_shadow_assessment_contract():
        raise ProductionShadowAssessmentError("PSI0C_B_CONTRACT_REPLAY_MISMATCH")
    forbidden = (
        contract.accepts_production_rows, contract.grants_policy_authority,
        contract.grants_ranking_authority, contract.grants_integration_authority,
        contract.grants_activation_authority, contract.negative_inference_from_absence_allowed,
        contract.conflict_resolution_allowed,
    )
    if any(forbidden):
        raise ProductionShadowAssessmentError("PSI0C_B_AUTHORITY_DRIFT")
    return True


def _normalize_row(query_id: str, value: object) -> dict:
    if not isinstance(value, Mapping) or set(value) != set(SCHEMAS[query_id]):
        raise ProductionShadowAssessmentError("PSI0C_B_UNKNOWN_RESULT_SCHEMA")
    row = {key: value[key] for key in SCHEMAS[query_id]}
    if isinstance(row["rowid"], bool) or not isinstance(row["rowid"], int) or row["rowid"] < 1:
        raise ProductionShadowAssessmentError("PSI0C_B_INVALID_ROW_ID")
    try:
        _json(row)
    except (TypeError, ValueError) as exc:
        raise ProductionShadowAssessmentError("PSI0C_B_NON_CANONICAL_VALUE") from exc
    return row


def _mint(query_id: str, row: Mapping[str, object]) -> str:
    if query_id == "evidence_launch_facts":
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, ValueError) as exc:
            raise ProductionShadowAssessmentError("PSI0C_B_MALFORMED_EVIDENCE_PAYLOAD") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("mint"), str) or not payload["mint"]:
            raise ProductionShadowAssessmentError("PSI0C_B_EVIDENCE_MINT_MISSING")
        return payload["mint"]
    mint = row.get("mint")
    if not isinstance(mint, str) or not mint:
        raise ProductionShadowAssessmentError("PSI0C_B_RESULT_MINT_MISSING")
    return mint


def _creator_assertion(query_id: str, row: Mapping[str, object]) -> object:
    field = {
        "creator_selected_cohort": "creator_address",
        "main_selected_cohort": "pf_ws_creator",
        "ops_selected_cohort": "creator_wallet",
    }.get(query_id)
    if field:
        return row[field]
    if query_id == "evidence_launch_facts":
        payload = json.loads(row["payload_json"])
        for candidate in ("creator", "creator_wallet", "creator_address"):
            if candidate in payload:
                return payload[candidate]
    return None


def assess_fixture_shadow(
    contract: ShadowAssessmentContract,
    *,
    cohort_mints: Sequence[str],
    synthetic_results: Mapping[str, Sequence[Mapping[str, object]]],
    output_directory: Path,
    input_lineage: Mapping[str, str],
    fixture_only: bool = True,
) -> ShadowAssessmentBundle:
    if fixture_only is not True:
        raise ProductionShadowAssessmentError("PSI0C_B_PRODUCTION_ROWS_PROHIBITED")
    return _assess_shadow_rows(
        contract,
        cohort_mints=cohort_mints,
        synthetic_results=synthetic_results,
        output_directory=output_directory,
        input_lineage=input_lineage,
        provenance_class="FROZEN_SYNTHETIC_FIXTURE",
        fixture_only=True,
    )


def _assess_shadow_rows(
    contract: ShadowAssessmentContract,
    *,
    cohort_mints: Sequence[str],
    synthetic_results: Mapping[str, Sequence[Mapping[str, object]]],
    output_directory: Path,
    input_lineage: Mapping[str, str],
    provenance_class: str,
    fixture_only: bool,
) -> ShadowAssessmentBundle:
    verify_shadow_assessment_contract(contract)
    expected_lineage = {
        "psi0c_a_digest": PSI0C_A_DIGEST,
        "psi0b_g_digest": PSI0B_G_DIGEST,
        "psi0b_bundle_identity_digest": PSI0B_BUNDLE_DIGEST,
    }
    if dict(input_lineage) != expected_lineage:
        raise ProductionShadowAssessmentError("PSI0C_B_STALE_OR_ALTERED_LINEAGE")
    if tuple(synthetic_results) != QUERY_IDS:
        raise ProductionShadowAssessmentError("PSI0C_B_QUERY_SET_OR_ORDER_DRIFT")
    cohort = tuple(cohort_mints)
    if not cohort or any(not isinstance(mint, str) or not mint for mint in cohort) or len(set(cohort)) != len(cohort):
        raise ProductionShadowAssessmentError("PSI0C_B_INVALID_COHORT")
    output = Path(output_directory)
    if output.exists():
        raise ProductionShadowAssessmentError("PSI0C_B_OUTPUT_NOT_NEW")

    ceilings = {item.query_id: item for item in build_production_shadow_resource_ceiling_contract().query_ceilings}
    normalized: dict[str, list[dict]] = {}
    membership: dict[str, dict] = {}
    assertions: dict[str, list[dict]] = {mint: [] for mint in cohort}
    cohort_set = set(cohort)
    for query_id in QUERY_IDS:
        rows = [_normalize_row(query_id, row) for row in synthetic_results[query_id]]
        rows.sort(key=lambda row: _json(row))
        if len(rows) > ceilings[query_id].maximum_rows or len(_json(rows)) > ceilings[query_id].maximum_canonical_bytes:
            raise ProductionShadowAssessmentError("PSI0C_B_RESOURCE_CEILING_EXCEEDED")
        normalized[query_id] = rows
        mints = [_mint(query_id, row) for row in rows]
        observed = sorted(set(mints) & cohort_set)
        unmatched = sorted(set(mints) - cohort_set)
        membership[query_id] = {
            "row_count": len(rows),
            "unique_mint_count": len(set(mints)),
            "cohort_present_count": len(observed),
            "cohort_denominator": len(cohort),
            "coverage_numerator": len(observed),
            "coverage_denominator": len(cohort),
            "duplicate_row_count": len(rows) - len(set(mints)),
            "unmatched_mints": unmatched,
            "unmatched_row_count": sum(mint not in cohort_set for mint in mints),
        }
        for row, mint in zip(rows, mints):
            creator = _creator_assertion(query_id, row)
            if mint in cohort_set and creator not in (None, ""):
                assertions[mint].append({
                    "query_id": query_id, "rowid": row["rowid"], "field": "creator", "value": creator,
                })

    missingness = []
    for mint in sorted(cohort):
        missingness.append({
            "mint": mint,
            "surfaces": {
                query_id: (
                    "PRESENT" if any(_mint(query_id, row) == mint for row in normalized[query_id])
                    else "ABSENT_NOT_NEGATIVE"
                ) for query_id in QUERY_IDS
            },
            "negative_outcome_inferred": False,
        })
    conflicts = []
    for mint in sorted(cohort):
        kept = sorted(assertions[mint], key=lambda item: (item["query_id"], item["rowid"], str(item["value"])))
        values = {json.dumps(item["value"], sort_keys=True) for item in kept}
        if len(values) > 1:
            conflicts.append({
                "mint": mint,
                "field": "creator",
                "reason_code": "PSI0C_B_CONFLICT_PRESERVED_UNRESOLVED",
                "assertions": kept,
                "resolved_value": None,
            })

    assessment = {
        "schema_version": "psi0c-b.assessment.v1",
        "contract_digest": contract.contract_digest,
        "input_lineage": expected_lineage,
        "fixture_only": fixture_only,
        "provenance_class": provenance_class,
        "cohort_count": len(cohort),
        "membership": membership,
        "missingness": missingness,
        "conflicts": conflicts,
        "orphan_unmatched_accounting": {
            "unmatched_mints": sorted({mint for item in membership.values() for mint in item["unmatched_mints"]}),
            "unmatched_rows": sum(item["unmatched_row_count"] for item in membership.values()),
        },
        "reason_codes": sorted({
            "PSI0C_B_ABSENCE_IS_NOT_NEGATIVE",
            *("PSI0C_B_CONFLICT_PRESERVED_UNRESOLVED" for _ in conflicts),
            *("PSI0C_B_UNMATCHED_KEY_RECORDED" for item in membership.values() if item["unmatched_row_count"]),
        }),
        "authority": {
            "policy": False, "ranking": False, "integration": False, "activation": False,
        },
    }
    assessment["assessment_digest"] = _digest(assessment)
    contract_payload = asdict(contract)
    contract_payload["result_schemas"] = [[key, list(value)] for key, value in contract.result_schemas]
    payloads = {"contract.json": _json(contract_payload), "assessment.json": _json(assessment)}
    hashes = {name: sha256(payload).hexdigest() for name, payload in payloads.items()}
    bundle_digest = _digest(hashes)
    payloads["hashes.json"] = _json({"file_digests": hashes, "bundle_digest": bundle_digest})
    temporary = output.with_name(f".{output.name}.tmp")
    if temporary.exists():
        shutil.rmtree(temporary)
    try:
        temporary.mkdir(parents=True)
        for name, payload in payloads.items():
            (temporary / name).write_bytes(payload)
        temporary.rename(output)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return ShadowAssessmentBundle(output, assessment["assessment_digest"], hashes)


def verify_fixture_shadow_assessment(output_directory: Path) -> ShadowAssessmentBundle:
    output = Path(output_directory)
    if not output.is_dir() or {path.name for path in output.iterdir()} != FILES:
        raise ProductionShadowAssessmentError("PSI0C_B_FILE_SET_MISMATCH")
    hashes_doc = json.loads((output / "hashes.json").read_text())
    expected = hashes_doc.get("file_digests")
    actual = {name: sha256((output / name).read_bytes()).hexdigest() for name in FILES - {"hashes.json"}}
    if expected != actual or hashes_doc.get("bundle_digest") != _digest(actual):
        raise ProductionShadowAssessmentError("PSI0C_B_REPLAY_DIGEST_MISMATCH")
    contract = json.loads((output / "contract.json").read_text())
    if contract.get("contract_digest") != build_shadow_assessment_contract().contract_digest:
        raise ProductionShadowAssessmentError("PSI0C_B_CONTRACT_REPLAY_MISMATCH")
    assessment = json.loads((output / "assessment.json").read_text())
    digest = assessment.pop("assessment_digest", None)
    if digest != _digest(assessment):
        raise ProductionShadowAssessmentError("PSI0C_B_ASSESSMENT_REPLAY_MISMATCH")
    assessment["assessment_digest"] = digest
    provenance = assessment.get("provenance_class")
    valid_provenance = (
        (assessment.get("fixture_only") is True and provenance == "FROZEN_SYNTHETIC_FIXTURE") or
        (assessment.get("fixture_only") is False and provenance == "PRODUCTION_DERIVED_IMMUTABLE_LOCAL_BUNDLE")
    )
    if any(assessment["authority"].values()) or not valid_provenance:
        raise ProductionShadowAssessmentError("PSI0C_B_AUTHORITY_DRIFT")
    return ShadowAssessmentBundle(output, digest, actual)
