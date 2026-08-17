"""Approved PSI0G-D pure projection from retained EP3 outputs to one review candidate."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = "psi0g-d1.operation-projection.v1"
OPERATIONS = ("watchtower", "three_sw2")
PRIMARY_ROLE = "FUNDING_AND_LAUNCH_OPERATION"
EDGE_FEATURES = ("CREATOR_SIGNED_LAUNCH", "DIRECTED_VALUE_TRANSFER")
MECHANISM_FEATURES = (
    "BEHAVIOURAL_TIMING", "DIRECT_COUNTERPARTY", "ECONOMIC_FUNDING",
    "LAUNCH_ACTIVATION", "LAUNCH_SIGNER", "REPEATED_COUNTERPARTY",
    "SYSTEM_TRANSFER", "WALLET_FRESH_AT_EVENT",
)
TEMPORAL_FEATURES = ("BEHAVIOURAL_TIMING_OBSERVED",)
AUTHORITY = {
    "operator_identity": False, "same_operation": False, "same_human": False,
    "proposed": False, "supported": False, "policy": False, "ranking": False,
    "integration": False, "monitoring": False, "deployment": False, "activation": False,
}


class Psi0gOperationProjectionError(RuntimeError):
    pass


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode() + b"\n"


def _digest(value: object) -> str:
    return sha256(_canonical(value).rstrip(b"\n")).hexdigest()


def _fail(code: str) -> None:
    raise Psi0gOperationProjectionError(f"PSI0G_D1_{code}")


@dataclass(frozen=True)
class Psi0gOperationProjection:
    payload: bytes
    projection_digest: str
    candidate_id: str
    complete_operations: int


def project_psi0g_operation_candidate(
    *, operations: Sequence[Mapping[str, Any]],
    behaviours: Sequence[Mapping[str, Any]],
    topologies: Sequence[Mapping[str, Any]],
    detector_results: Sequence[Mapping[str, Any]],
    subject_candidate_count: int,
    subject_candidate_ids_digest: str,
) -> Psi0gOperationProjection:
    if tuple(sorted(str(item.get("operation_key")) for item in operations)) != tuple(sorted(OPERATIONS)):
        _fail("OPERATION_COHORT_DRIFT")
    if not isinstance(subject_candidate_count, int) or subject_candidate_count < 1:
        _fail("SUBJECT_CANDIDATE_COUNT_INVALID")
    if not isinstance(subject_candidate_ids_digest, str) or len(subject_candidate_ids_digest) != 64:
        _fail("SUBJECT_CANDIDATE_DIGEST_INVALID")

    operation_by_contract = {str(item["contract_id"]): dict(item) for item in operations}
    behaviour_by_contract: dict[str, list[dict[str, Any]]] = {key: [] for key in OPERATIONS}
    for value in behaviours:
        row = dict(value)
        contract = str(row.get("contract_id"))
        if contract not in behaviour_by_contract or row.get("contract_version") != "1.0.0":
            _fail("BEHAVIOUR_CONTRACT_DRIFT")
        behaviour_by_contract[contract].append(row)
    topology_by_contract = {str(item.get("contract_id")): dict(item) for item in topologies}
    detector_by_contract = {str(item.get("contract_id")): dict(item) for item in detector_results}
    if set(topology_by_contract) != set(OPERATIONS) or set(detector_by_contract) != set(OPERATIONS):
        _fail("RUNTIME_COVERAGE_DRIFT")

    cohort, evaluations, runtime = [], [], []
    all_evidence: set[str] = set()
    all_primitives: set[str] = set()
    all_behaviours: set[str] = set()
    all_topologies: set[str] = set()
    candidate_missing: set[str] = set()
    for position, operation_id in enumerate(OPERATIONS):
        operation = operation_by_contract[operation_id]
        rows = sorted(behaviour_by_contract[operation_id], key=lambda item: item["observation_id"])
        if not rows:
            _fail("BEHAVIOUR_COVERAGE_DRIFT")
        topology = topology_by_contract[operation_id]
        detector = detector_by_contract[operation_id]
        expected_behaviours = sorted(operation["behaviour_observation_ids"])
        if ([row["observation_id"] for row in rows] != expected_behaviours or
                topology["revision_id"] != operation["topology_revision_id"] or
                detector["result_id"] != operation["detector_result_id"]):
            _fail("MANIFEST_LINEAGE_DRIFT")

        counts = {feature: 0 for feature in MECHANISM_FEATURES}
        proven = {feature: 0 for feature in MECHANISM_FEATURES}
        missing_by_feature: dict[str, set[str]] = {feature: set() for feature in MECHANISM_FEATURES}
        evidence_refs: set[str] = set()
        primitive_refs: set[str] = set()
        for row in rows:
            measured = row.get("measured_values")
            if not isinstance(measured, Mapping) or not isinstance(measured.get("by_primitive_type"), Mapping):
                _fail("BEHAVIOUR_MEASUREMENT_INVALID")
            missing = row.get("missing_inputs")
            if not isinstance(missing, list):
                _fail("BEHAVIOUR_MISSINGNESS_INVALID")
            for feature, count in measured["by_primitive_type"].items():
                if feature in counts:
                    if not isinstance(count, int) or count < 0:
                        _fail("BEHAVIOUR_COUNT_INVALID")
                    counts[feature] += count
                    if row.get("quality_state") == "PROVEN":
                        proven[feature] += count
                    missing_by_feature[feature].update(str(item) for item in missing)
            evidence_refs.update(str(item) for item in row.get("evidence_refs", ()))
            primitive_refs.update(str(item) for item in row.get("primitive_refs", ()))

        observed_mechanisms = sorted(feature for feature in MECHANISM_FEATURES if counts[feature] > 0)
        missing_reasons = []
        for feature in MECHANISM_FEATURES:
            if proven[feature] == 0:
                missing_reasons.append(f"{feature}:NO_PROVEN_OBSERVATION")
            for item in sorted(missing_by_feature[feature]):
                missing_reasons.append(f"{feature}:{item}")
        complete = not missing_reasons
        candidate_missing.update(f"{operation_id}:{item}" for item in missing_reasons)
        edges = []
        if counts["SYSTEM_TRANSFER"] > 0:
            edges.append("DIRECTED_VALUE_TRANSFER")
        if counts["LAUNCH_SIGNER"] > 0:
            edges.append("CREATOR_SIGNED_LAUNCH")
        temporal = ["BEHAVIOURAL_TIMING_OBSERVED"] if counts["BEHAVIOURAL_TIMING"] > 0 else []
        cohort.append({"position": position, "operation_id": operation_id})
        evaluations.append({
            "operation_id": operation_id, "contract_id": operation["contract_id"],
            "contract_version": operation["contract_version"],
            "snapshot_digest": operation["snapshot_digest"],
            "detector_result_id": operation["detector_result_id"],
            "topology_revision_id": operation["topology_revision_id"],
            "behaviour_observation_ids": expected_behaviours,
        })
        runtime.append({
            "schema_version": "eb0.4c.normalized-runtime.v1",
            "identity_basis": "PLATFORM_OPERATION_ID", "operation_id": operation_id,
            "primary_role": PRIMARY_ROLE, "contract_id": operation["contract_id"],
            "contract_version": operation["contract_version"],
            "module_id": "psi0g_ep3_to_eb0_4c_projection", "module_version": "1.0.0",
            "topology_revision_id": operation["topology_revision_id"],
            "behaviour_observation_id": expected_behaviours[0],
            "input_digest": operation["snapshot_digest"], "edge_features": sorted(edges),
            "mechanism_features": observed_mechanisms, "temporal_features": temporal,
            "quality_state": "PROVEN" if complete else "INCOMPLETE",
            "completeness_state": "COMPLETE" if complete else "PARTIAL",
            "conflict_group_id": None,
        })
        all_evidence.update(evidence_refs)
        all_primitives.update(primitive_refs)
        all_behaviours.update(expected_behaviours)
        all_topologies.add(operation["topology_revision_id"])

    candidate_body = {
        "input_digest": _digest({
            "snapshot_digests": sorted(item["snapshot_digest"] for item in evaluations),
            "subject_candidate_ids_digest": subject_candidate_ids_digest,
        }),
        "population": list(OPERATIONS), "supporting_evidence_ids": sorted(all_evidence),
        "supporting_primitive_ids": sorted(all_primitives),
        "supporting_behaviour_observation_ids": sorted(all_behaviours),
        "supporting_topology_revision_ids": sorted(all_topologies),
        "quality_state": "PROVEN" if not candidate_missing else "INCOMPLETE",
        "missing_evidence": sorted(candidate_missing), "contradictory_evidence": [],
        "lifecycle": "RECURRING_PATTERN",
    }
    candidate = {"candidate_id": _digest(candidate_body), **candidate_body}
    vocabulary_body = {
        "roles": [PRIMARY_ROLE], "edge": list(EDGE_FEATURES),
        "mechanism": list(MECHANISM_FEATURES), "temporal": list(TEMPORAL_FEATURES),
    }
    document = {
        "schema_version": SCHEMA_VERSION, "cohort": cohort, "evaluations": evaluations,
        "runtime": runtime, "candidate": candidate,
        "subject_candidate_context": {"count": subject_candidate_count,
            "candidate_ids_digest": subject_candidate_ids_digest},
        "vocabulary": {**vocabulary_body, "contract_digest": _digest(vocabulary_body)},
        "disposition": None, "authority": dict(AUTHORITY),
        "semantic_guards": {
            "operations_merged": False, "same_operation_claim": False,
            "same_human_or_operator_claim": False,
            "behavioural_similarity_only": True,
        },
    }
    payload = _canonical(document)
    return Psi0gOperationProjection(
        payload=payload, projection_digest=sha256(payload).hexdigest(),
        candidate_id=candidate["candidate_id"],
        complete_operations=sum(item["completeness_state"] == "COMPLETE" for item in runtime),
    )
