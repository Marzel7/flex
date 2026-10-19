"""PSI0F-F5 pure fixture-only immutable EB0.4 logical-source materialization."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Mapping, Sequence, Tuple

from .operational_family_rematerialization import (
    AUTHORITY_KEYS,
    OperationalFamilyRematerializationError,
    build_immutable_operational_family_source,
    build_operational_family_rematerialization_contract,
    verify_immutable_operational_family_source,
)


CONTRACT_VERSION = "psi0f-f5.v1"
ENGINEERING_REVISION = "914de967b2dc821abb25dce7f00e7cfda8ca2ba5"
PSI0F_F4_DIGEST = "ed1b896b25951342e556cbfeacd04428a7bd4a6f159bef02905fb986c7b84596"
PSI0F_F1_DIGEST = "074686504842dd2174002f04be83b6f670870a76f32269db3623c0c4e634155a"
PROVENANCE = "FROZEN_SYNTHETIC_IMMUTABLE_OPERATIONAL_FAMILY_LOGICAL_SOURCE"
LIFECYCLES = frozenset(("OBSERVED", "RECURRING_PATTERN", "INVESTIGATE", "DISMISSED"))
NOMINATION_STATES = frozenset(("PROPOSED", "SUPPORTED"))

COHORT_FIELDS = frozenset(("position", "operation_id"))
EVALUATION_FIELDS = frozenset((
    "operation_id", "contract_id", "contract_version", "snapshot_digest",
    "detector_result_id", "topology_revision_id", "behaviour_observation_ids",
))
RUNTIME_FIELDS = frozenset((
    "schema_version", "identity_basis", "operation_id", "primary_role",
    "contract_id", "contract_version", "module_id", "module_version",
    "topology_revision_id", "behaviour_observation_id", "input_digest",
    "edge_features", "mechanism_features", "temporal_features",
    "quality_state", "completeness_state", "conflict_group_id",
))
CANDIDATE_FIELDS = frozenset((
    "candidate_id", "input_digest", "population", "supporting_evidence_ids",
    "supporting_primitive_ids", "supporting_behaviour_observation_ids",
    "supporting_topology_revision_ids", "quality_state", "missing_evidence",
    "contradictory_evidence", "lifecycle",
))
DISPOSITION_FIELDS = frozenset((
    "review_id", "candidate_id", "group_id", "operation_ids",
    "nomination_state", "supporting_identity_digest", "authority",
))
VOCABULARY_FIELDS = frozenset(("roles", "edge", "mechanism", "temporal", "contract_digest"))


class OperationalFamilySourceMaterializationError(RuntimeError):
    """Named fail-closed PSI0F-F5 contract violation."""


@dataclass(frozen=True)
class OperationalFamilySourceMaterializationContract:
    contract_version: str
    engineering_revision: str
    psi0f_f4_digest: str
    psi0f_f1_digest: str
    output_schema_version: str
    output_provenance: str
    fixture_only: bool
    performs_io: bool
    lifecycle_grants_nomination_authority: bool
    implicit_membership_allowed: bool
    retains_source_values_outside_output: bool
    authority: Mapping[str, bool]
    contract_digest: str


@dataclass(frozen=True)
class MaterializedOperationalFamilySource:
    payload: bytes
    source_digest: str
    contract_digest: str
    operation_count: int
    runtime_count: int
    candidate_group_count: int
    membership_count: int


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode() + b"\n"


def _digest(value: object) -> str:
    return sha256(_canonical(value).rstrip(b"\n")).hexdigest()


def _fail(code: str) -> None:
    raise OperationalFamilySourceMaterializationError(f"PSI0F_F5_{code}")


def _record(value: object, fields: frozenset[str], code: str) -> dict[str, object]:
    if not isinstance(value, Mapping) or frozenset(value) != fields:
        _fail(f"{code}_SCHEMA_DRIFT")
    return dict(value)


def _text(value: object, code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(code)
    return value.strip()


def _texts(value: object, code: str, *, nonempty: bool = True) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        _fail(code)
    result = tuple(_text(item, code) for item in value)
    if (nonempty and not result) or len(result) != len(set(result)):
        _fail(code)
    return result


def build_operational_family_source_materialization_contract() -> OperationalFamilySourceMaterializationContract:
    source_contract = build_operational_family_rematerialization_contract()
    body = {
        "contract_version": CONTRACT_VERSION,
        "engineering_revision": ENGINEERING_REVISION,
        "psi0f_f4_digest": PSI0F_F4_DIGEST,
        "psi0f_f1_digest": PSI0F_F1_DIGEST,
        "output_schema_version": source_contract.source_schema_version,
        "output_provenance": PROVENANCE,
        "fixture_only": True,
        "performs_io": False,
        "lifecycle_grants_nomination_authority": False,
        "implicit_membership_allowed": False,
        "retains_source_values_outside_output": False,
        "authority": {key: False for key in AUTHORITY_KEYS},
    }
    return OperationalFamilySourceMaterializationContract(**body, contract_digest=_digest(body))


def verify_operational_family_source_materialization_contract(
    contract: OperationalFamilySourceMaterializationContract,
) -> bool:
    if contract != build_operational_family_source_materialization_contract():
        _fail("CONTRACT_REPLAY_MISMATCH")
    if (not contract.fixture_only or contract.performs_io or
            contract.lifecycle_grants_nomination_authority or
            contract.implicit_membership_allowed or
            contract.retains_source_values_outside_output or any(contract.authority.values())):
        _fail("AUTHORITY_DRIFT")
    return True


def _vocabulary(value: object) -> dict[str, list[str]]:
    record = _record(value, VOCABULARY_FIELDS, "VOCABULARY")
    body: dict[str, list[str]] = {}
    for key in ("roles", "edge", "mechanism", "temporal"):
        body[key] = sorted(_texts(record[key], "INVALID_VOCABULARY"))
    if record["contract_digest"] != _digest(body):
        _fail("VOCABULARY_DIGEST_DRIFT")
    return body


def _evaluations(values: object, cohort: tuple[str, ...]) -> dict[str, dict[str, object]]:
    if not isinstance(values, (list, tuple)):
        _fail("EVALUATION_TYPE_INVALID")
    result: dict[str, dict[str, object]] = {}
    for value in values:
        row = _record(value, EVALUATION_FIELDS, "EVALUATION")
        operation = _text(row["operation_id"], "INVALID_OPERATION_ID")
        if operation in result:
            _fail("DUPLICATE_EVALUATION")
        row.update(
            operation_id=operation,
            contract_id=_text(row["contract_id"], "INVALID_CONTRACT_ID"),
            contract_version=_text(row["contract_version"], "INVALID_CONTRACT_VERSION"),
            snapshot_digest=_text(row["snapshot_digest"], "INVALID_SNAPSHOT_DIGEST"),
            detector_result_id=_text(row["detector_result_id"], "INVALID_DETECTOR_RESULT_ID"),
            topology_revision_id=_text(row["topology_revision_id"], "INVALID_TOPOLOGY_REVISION_ID"),
            behaviour_observation_ids=sorted(_texts(row["behaviour_observation_ids"], "INVALID_BEHAVIOUR_IDENTITIES")),
        )
        result[operation] = row
    if tuple(sorted(result)) != tuple(sorted(cohort)):
        _fail("EVALUATION_COHORT_MISMATCH")
    return result


def _runtime(values: object, evaluations: Mapping[str, Mapping[str, object]]) -> list[dict[str, object]]:
    if not isinstance(values, (list, tuple)):
        _fail("RUNTIME_TYPE_INVALID")
    result = []
    seen = set()
    for value in values:
        row = _record(value, RUNTIME_FIELDS, "RUNTIME")
        operation = _text(row["operation_id"], "INVALID_OPERATION_ID")
        evaluation = evaluations.get(operation)
        if evaluation is None:
            _fail("RUNTIME_WITHOUT_EVALUATION")
        key = (operation, row["input_digest"])
        if key in seen:
            _fail("DUPLICATE_RUNTIME")
        seen.add(key)
        if (row["contract_id"] != evaluation["contract_id"] or
                row["contract_version"] != evaluation["contract_version"] or
                row["input_digest"] != evaluation["snapshot_digest"] or
                row["topology_revision_id"] != evaluation["topology_revision_id"] or
                row["behaviour_observation_id"] not in evaluation["behaviour_observation_ids"]):
            _fail("RUNTIME_EVALUATION_LINEAGE_DRIFT")
        result.append(row)
    if set(item["operation_id"] for item in result) != set(evaluations):
        _fail("INCOMPLETE_RUNTIME_COVERAGE")
    return result


def _candidate_identity_body(row: Mapping[str, object]) -> dict[str, object]:
    return {
        "candidate_id": row["candidate_id"],
        "input_digest": row["input_digest"],
        "population": list(row["population"]),
        "supporting_evidence_ids": list(row["supporting_evidence_ids"]),
        "supporting_primitive_ids": list(row["supporting_primitive_ids"]),
        "supporting_behaviour_observation_ids": list(row["supporting_behaviour_observation_ids"]),
        "supporting_topology_revision_ids": list(row["supporting_topology_revision_ids"]),
        "quality_state": row["quality_state"],
        "missing_evidence": list(row["missing_evidence"]),
        "contradictory_evidence": list(row["contradictory_evidence"]),
        "lifecycle": row["lifecycle"],
    }


def _candidates(values: object, cohort: tuple[str, ...]) -> dict[str, dict[str, object]]:
    if not isinstance(values, (list, tuple)):
        _fail("CANDIDATE_TYPE_INVALID")
    result = {}
    for value in values:
        row = _record(value, CANDIDATE_FIELDS, "CANDIDATE")
        candidate_id = _text(row["candidate_id"], "INVALID_CANDIDATE_ID")
        if candidate_id in result:
            _fail("DUPLICATE_CANDIDATE")
        row.update(
            candidate_id=candidate_id,
            input_digest=_text(row["input_digest"], "INVALID_CANDIDATE_INPUT_DIGEST"),
            population=sorted(_texts(row["population"], "INVALID_CANDIDATE_POPULATION")),
            supporting_evidence_ids=sorted(_texts(row["supporting_evidence_ids"], "INVALID_EVIDENCE_IDENTITIES", nonempty=False)),
            supporting_primitive_ids=sorted(_texts(row["supporting_primitive_ids"], "INVALID_PRIMITIVE_IDENTITIES")),
            supporting_behaviour_observation_ids=sorted(_texts(row["supporting_behaviour_observation_ids"], "INVALID_BEHAVIOUR_IDENTITIES")),
            supporting_topology_revision_ids=sorted(_texts(row["supporting_topology_revision_ids"], "INVALID_TOPOLOGY_IDENTITIES")),
            quality_state=_text(row["quality_state"], "INVALID_CANDIDATE_QUALITY"),
            missing_evidence=sorted(_texts(row["missing_evidence"], "INVALID_MISSING_EVIDENCE", nonempty=False)),
            contradictory_evidence=sorted(_texts(row["contradictory_evidence"], "INVALID_CONTRADICTORY_EVIDENCE", nonempty=False)),
            lifecycle=_text(row["lifecycle"], "INVALID_CANDIDATE_LIFECYCLE"),
        )
        if row["lifecycle"] not in LIFECYCLES:
            _fail("INVALID_CANDIDATE_LIFECYCLE")
        if not set(row["population"]).issubset(cohort):
            _fail("CANDIDATE_POPULATION_OUTSIDE_COHORT")
        result[candidate_id] = row
    if not result:
        _fail("EMPTY_CANDIDATES")
    return result


def _disposition_review_body(row: Mapping[str, object]) -> dict[str, object]:
    return {
        "candidate_id": row["candidate_id"],
        "group_id": row["group_id"],
        "operation_ids": list(row["operation_ids"]),
        "nomination_state": row["nomination_state"],
        "supporting_identity_digest": row["supporting_identity_digest"],
        "authority": dict(row["authority"]),
    }


def _memberships(values: object, candidates: Mapping[str, Mapping[str, object]],
                 runtime: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    if not isinstance(values, (list, tuple)):
        _fail("DISPOSITION_TYPE_INVALID")
    runtime_by_operation = {row["operation_id"]: row for row in runtime}
    memberships, groups = [], set()
    for value in values:
        row = _record(value, DISPOSITION_FIELDS, "DISPOSITION")
        candidate_id = _text(row["candidate_id"], "INVALID_CANDIDATE_ID")
        candidate = candidates.get(candidate_id)
        if candidate is None:
            _fail("DISPOSITION_WITHOUT_CANDIDATE")
        group_id = _text(row["group_id"], "INVALID_GROUP_ID")
        if group_id in groups:
            _fail("DUPLICATE_GROUP_DISPOSITION")
        groups.add(group_id)
        operation_ids = _texts(row["operation_ids"], "INVALID_DISPOSITION_MEMBERSHIP")
        if len(operation_ids) < 2 or set(operation_ids) != set(candidate["population"]):
            _fail("DISPOSITION_POPULATION_MISMATCH")
        state = row["nomination_state"]
        if state not in NOMINATION_STATES:
            _fail("INVALID_NOMINATION_STATE")
        authority = {key: False for key in AUTHORITY_KEYS}
        if row["authority"] != authority:
            _fail("AUTHORITY_DRIFT")
        if row["supporting_identity_digest"] != _digest(_candidate_identity_body(candidate)):
            _fail("CANDIDATE_IDENTITY_DIGEST_DRIFT")
        normalized = {
            **row, "candidate_id": candidate_id, "group_id": group_id,
            "operation_ids": list(operation_ids), "nomination_state": state,
            "authority": authority,
        }
        if row["review_id"] != _digest(_disposition_review_body(normalized)):
            _fail("REVIEW_IDENTITY_DRIFT")
        if state == "SUPPORTED":
            selected = [runtime_by_operation.get(operation) for operation in operation_ids]
            if (candidate["quality_state"] != "PROVEN" or candidate["missing_evidence"] or
                    candidate["contradictory_evidence"] or any(item is None for item in selected) or
                    any(item["completeness_state"] != "COMPLETE" or item["conflict_group_id"] is not None or
                        not item["mechanism_features"] or not item["temporal_features"] for item in selected)):
                _fail("SUPPORTED_EVIDENCE_INSUFFICIENT")
        memberships.extend({
            "group_id": group_id, "position": position,
            "operation_id": operation, "nomination_state": state,
        } for position, operation in enumerate(operation_ids))
    if not memberships:
        _fail("EMPTY_DISPOSITIONS")
    return memberships


def materialize_fixture_operational_family_source(
    contract: OperationalFamilySourceMaterializationContract,
    *,
    cohort: object,
    evaluations: object,
    runtime: object,
    candidates: object,
    dispositions: object,
    vocabulary: object,
) -> MaterializedOperationalFamilySource:
    verify_operational_family_source_materialization_contract(contract)
    if not isinstance(cohort, (list, tuple)):
        _fail("COHORT_TYPE_INVALID")
    cohort_rows = [_record(item, COHORT_FIELDS, "COHORT") for item in cohort]
    cohort_rows.sort(key=lambda item: item["position"] if isinstance(item["position"], int) else -1)
    positions = [item["position"] for item in cohort_rows]
    operations = tuple(_text(item["operation_id"], "INVALID_OPERATION_ID") for item in cohort_rows)
    if not operations or positions != list(range(len(operations))) or len(operations) != len(set(operations)):
        _fail("INVALID_COHORT")
    evaluation_rows = _evaluations(evaluations, operations)
    runtime_rows = _runtime(runtime, evaluation_rows)
    candidate_rows = _candidates(candidates, operations)
    membership_rows = _memberships(dispositions, candidate_rows, runtime_rows)
    vocabulary_body = _vocabulary(vocabulary)
    source_contract = build_operational_family_rematerialization_contract()
    if (source_contract.contract_digest != contract.psi0f_f1_digest or
            source_contract.source_provenance_class != contract.output_provenance or
            source_contract.source_schema_version != contract.output_schema_version):
        _fail("F1_CONTRACT_DRIFT")
    try:
        payload = build_immutable_operational_family_source(
            source_contract, cohort=cohort_rows, runtime=runtime_rows,
            memberships=membership_rows, vocabulary=vocabulary_body,
        )
        source_digest = verify_immutable_operational_family_source(source_contract, payload)
    except OperationalFamilyRematerializationError as exc:
        raise OperationalFamilySourceMaterializationError(
            f"PSI0F_F5_F1_SOURCE_REJECTED:{exc}"
        ) from exc
    return MaterializedOperationalFamilySource(
        payload=payload, source_digest=source_digest, contract_digest=contract.contract_digest,
        operation_count=len(cohort_rows), runtime_count=len(runtime_rows),
        candidate_group_count=len(dispositions), membership_count=len(membership_rows),
    )
