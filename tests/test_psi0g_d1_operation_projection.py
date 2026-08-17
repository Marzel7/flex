import json

import pytest

from src.evidence.contracts.psi0g_operation_projection import (
    AUTHORITY, MECHANISM_FEATURES, Psi0gOperationProjectionError,
    project_psi0g_operation_candidate,
)


def material(*, incomplete=False, absent=False):
    operations, behaviours, topologies, detectors = [], [], [], []
    for index, operation in enumerate(("watchtower", "three_sw2")):
        behaviour_ids = [f"b-{operation}-{position}" for position in range(len(MECHANISM_FEATURES))]
        operations.append({
            "operation_key": operation, "contract_id": operation, "contract_version": "1.0.0",
            "snapshot_digest": f"s-{operation}", "detector_result_id": f"d-{operation}",
            "topology_revision_id": f"t-{operation}",
            "behaviour_observation_ids": sorted(behaviour_ids),
        })
        for position, feature in enumerate(MECHANISM_FEATURES):
            count = 0 if absent and operation == "three_sw2" and feature == "REPEATED_COUNTERPARTY" else 1
            missing = ["history"] if incomplete and operation == "watchtower" and feature == "WALLET_FRESH_AT_EVENT" else []
            behaviours.append({
                "contract_id": operation, "contract_version": "1.0.0",
                "observation_id": behaviour_ids[position], "module_id": f"module-{position}",
                "module_version": "1.0.0", "quality_state": "INCOMPLETE" if missing else "PROVEN",
                "measured_values": {"by_primitive_type": {feature: count}},
                "missing_inputs": missing, "evidence_refs": [f"e-{operation}-{position}"],
                "primitive_refs": [f"p-{operation}-{position}"],
            })
        topologies.append({"contract_id": operation, "revision_id": f"t-{operation}"})
        detectors.append({"contract_id": operation, "result_id": f"d-{operation}"})
    return dict(operations=operations, behaviours=behaviours, topologies=topologies,
        detector_results=detectors, subject_candidate_count=5,
        subject_candidate_ids_digest="a" * 64)


def project(**changes):
    values = material()
    values.update(changes)
    return project_psi0g_operation_candidate(**values)


def test_approved_projection_keeps_two_operations_and_no_disposition_or_identity_claim():
    result = project()
    document = json.loads(result.payload)
    assert [row["operation_id"] for row in document["cohort"]] == ["watchtower", "three_sw2"]
    assert document["candidate"]["population"] == ["watchtower", "three_sw2"]
    assert document["disposition"] is None
    assert document["authority"] == AUTHORITY and not any(document["authority"].values())
    assert document["semantic_guards"] == {
        "behavioural_similarity_only": True, "operations_merged": False,
        "same_human_or_operator_claim": False, "same_operation_claim": False,
    }
    assert result.complete_operations == 2


def test_missing_or_unproven_selected_feature_is_partial_and_explicit():
    values = material(incomplete=True, absent=True)
    result = project_psi0g_operation_candidate(**values)
    document = json.loads(result.payload)
    runtime = {row["operation_id"]: row for row in document["runtime"]}
    assert runtime["watchtower"]["completeness_state"] == "PARTIAL"
    assert runtime["three_sw2"]["completeness_state"] == "PARTIAL"
    assert document["candidate"]["quality_state"] == "DEGRADED"
    assert "three_sw2:REPEATED_COUNTERPARTY:NO_PROVEN_OBSERVATION" in document["candidate"]["missing_evidence"]
    assert any(item.startswith("watchtower:WALLET_FRESH_AT_EVENT:")
               for item in document["candidate"]["missing_evidence"])


def test_projection_is_order_independent_and_replay_stable():
    values = material()
    first = project_psi0g_operation_candidate(**values)
    values["behaviours"] = list(reversed(values["behaviours"]))
    values["topologies"] = list(reversed(values["topologies"]))
    values["detector_results"] = list(reversed(values["detector_results"]))
    second = project_psi0g_operation_candidate(**values)
    assert second.payload == first.payload
    assert second.candidate_id == first.candidate_id


def test_operation_cohort_and_lineage_drift_fail_closed():
    values = material()
    values["operations"][0]["operation_key"] = "merged-operation"
    with pytest.raises(Psi0gOperationProjectionError, match="OPERATION_COHORT_DRIFT"):
        project_psi0g_operation_candidate(**values)
    values = material()
    values["topologies"][0]["revision_id"] = "drift"
    with pytest.raises(Psi0gOperationProjectionError, match="MANIFEST_LINEAGE_DRIFT"):
        project_psi0g_operation_candidate(**values)
