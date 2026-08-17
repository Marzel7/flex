from dataclasses import replace
from hashlib import sha256
import json

import pytest

from src.evidence.contracts.operational_family_rematerialization import (
    AUTHORITY_KEYS,
    build_operational_family_rematerialization_contract,
    verify_immutable_operational_family_source,
)
from src.evidence.contracts.operational_family_source_materialization import (
    OperationalFamilySourceMaterializationError,
    build_operational_family_source_materialization_contract,
    materialize_fixture_operational_family_source,
    verify_operational_family_source_materialization_contract,
)


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def material():
    operations = ["operation-alpha", "operation-beta", "operation-gamma", "operation-delta"]
    cohort = [{"position": position, "operation_id": operation} for position, operation in enumerate(operations)]
    evaluations, runtime = [], []
    for position, operation in enumerate(operations):
        evaluations.append({
            "operation_id": operation, "contract_id": f"contract-{position}",
            "contract_version": "1", "snapshot_digest": f"snapshot-{position}",
            "detector_result_id": f"detector-{position}", "topology_revision_id": f"topology-{position}",
            "behaviour_observation_ids": [f"behaviour-{position}"],
        })
        proposed = position >= 2
        runtime.append({
            "schema_version": "eb0.4c.normalized-runtime.v1", "identity_basis": "PLATFORM_OPERATION_ID",
            "operation_id": operation, "primary_role": "PROPOSED_ROLE" if proposed else "SUPPORTED_ROLE",
            "contract_id": f"contract-{position}", "contract_version": "1",
            "module_id": f"module-{position}", "module_version": "1",
            "topology_revision_id": f"topology-{position}",
            "behaviour_observation_id": f"behaviour-{position}", "input_digest": f"snapshot-{position}",
            "edge_features": ["FUNDS"], "mechanism_features": ["BATCHED"],
            "temporal_features": ["PERIODIC"], "quality_state": "CONFLICTING" if proposed else "OBSERVED",
            "completeness_state": "PARTIAL" if proposed else "COMPLETE",
            "conflict_group_id": "conflict-1" if proposed else None,
        })
    candidates = [
        {
            "candidate_id": "candidate-supported", "input_digest": "discovery-1",
            "population": operations[:2], "supporting_evidence_ids": ["evidence-a", "evidence-b"],
            "supporting_primitive_ids": ["primitive-a", "primitive-b"],
            "supporting_behaviour_observation_ids": ["behaviour-0", "behaviour-1"],
            "supporting_topology_revision_ids": ["topology-0", "topology-1"],
            "quality_state": "PROVEN", "missing_evidence": [], "contradictory_evidence": [],
            "lifecycle": "RECURRING_PATTERN",
        },
        {
            "candidate_id": "candidate-proposed", "input_digest": "discovery-2",
            "population": operations[2:], "supporting_evidence_ids": ["evidence-c"],
            "supporting_primitive_ids": ["primitive-c", "primitive-d"],
            "supporting_behaviour_observation_ids": ["behaviour-2", "behaviour-3"],
            "supporting_topology_revision_ids": ["topology-2", "topology-3"],
            "quality_state": "CONFLICTING", "missing_evidence": ["market"],
            "contradictory_evidence": ["evidence-c"], "lifecycle": "INVESTIGATE",
        },
    ]
    dispositions = []
    authority = {key: False for key in AUTHORITY_KEYS}
    for candidate, state in zip(candidates, ("SUPPORTED", "PROPOSED")):
        identity_body = {
            "candidate_id": candidate["candidate_id"], "input_digest": candidate["input_digest"],
            "population": sorted(candidate["population"]),
            "supporting_evidence_ids": sorted(candidate["supporting_evidence_ids"]),
            "supporting_primitive_ids": sorted(candidate["supporting_primitive_ids"]),
            "supporting_behaviour_observation_ids": sorted(candidate["supporting_behaviour_observation_ids"]),
            "supporting_topology_revision_ids": sorted(candidate["supporting_topology_revision_ids"]),
            "quality_state": candidate["quality_state"], "missing_evidence": sorted(candidate["missing_evidence"]),
            "contradictory_evidence": sorted(candidate["contradictory_evidence"]),
            "lifecycle": candidate["lifecycle"],
        }
        row = {
            "candidate_id": candidate["candidate_id"], "group_id": f"group-{state.lower()}",
            "operation_ids": list(candidate["population"]), "nomination_state": state,
            "supporting_identity_digest": digest(identity_body), "authority": authority,
        }
        row["review_id"] = digest(row)
        dispositions.append(row)
    vocabulary_body = {
        "roles": ["PROPOSED_ROLE", "SUPPORTED_ROLE"], "edge": ["FUNDS"],
        "mechanism": ["BATCHED"], "temporal": ["PERIODIC"],
    }
    vocabulary = {**vocabulary_body, "contract_digest": digest(vocabulary_body)}
    return {
        "cohort": cohort, "evaluations": evaluations, "runtime": runtime,
        "candidates": candidates, "dispositions": dispositions, "vocabulary": vocabulary,
    }


def build(**changes):
    values = material()
    values.update(changes)
    return materialize_fixture_operational_family_source(
        build_operational_family_source_materialization_contract(), **values,
    )


def test_contract_is_pure_fixture_only_replayable_and_has_no_authority():
    contract = build_operational_family_source_materialization_contract()
    assert verify_operational_family_source_materialization_contract(contract)
    assert contract.fixture_only and not contract.performs_io
    assert not contract.lifecycle_grants_nomination_authority
    assert not contract.implicit_membership_allowed and not any(contract.authority.values())
    with pytest.raises(OperationalFamilySourceMaterializationError, match="CONTRACT_REPLAY_MISMATCH"):
        verify_operational_family_source_materialization_contract(replace(contract, performs_io=True))


def test_materializes_canonical_f1_source_with_explicit_proposed_and_supported():
    result = build()
    source_contract = build_operational_family_rematerialization_contract()
    assert verify_immutable_operational_family_source(source_contract, result.payload) == result.source_digest
    document = json.loads(result.payload)
    assert document["accounting"] == {
        "candidate_group_count": 2, "cohort_count": 4, "membership_count": 4, "runtime_count": 4,
    }
    assert {item["nomination_state"] for item in document["components"]["nomination_candidates"]} == {"PROPOSED", "SUPPORTED"}
    assert (result.operation_count, result.runtime_count, result.candidate_group_count, result.membership_count) == (4, 4, 2, 4)


def test_input_order_is_independent_but_explicit_member_order_is_preserved():
    values = material()
    first = build().payload
    second = build(
        cohort=list(reversed(values["cohort"])), evaluations=list(reversed(values["evaluations"])),
        runtime=list(reversed(values["runtime"])), candidates=list(reversed(values["candidates"])),
        dispositions=list(reversed(values["dispositions"])),
        vocabulary=dict(reversed(list(values["vocabulary"].items()))),
    ).payload
    assert first == second


@pytest.mark.parametrize("target,mutation,code", [
    ("cohort", lambda rows: rows.append(dict(rows[0])), "INVALID_COHORT"),
    ("evaluations", lambda rows: rows.pop(), "EVALUATION_COHORT_MISMATCH"),
    ("evaluations", lambda rows: rows.append(dict(rows[0])), "DUPLICATE_EVALUATION"),
    ("runtime", lambda rows: rows[0].update(topology_revision_id="drift"), "RUNTIME_EVALUATION_LINEAGE_DRIFT"),
    ("runtime", lambda rows: rows.pop(), "INCOMPLETE_RUNTIME_COVERAGE"),
    ("candidates", lambda rows: rows[0].update(lifecycle="SUPPORTED"), "INVALID_CANDIDATE_LIFECYCLE"),
    ("candidates", lambda rows: rows[0].update(population=["operation-alpha", "outside"]), "CANDIDATE_POPULATION_OUTSIDE_COHORT"),
    ("dispositions", lambda rows: rows[0].update(nomination_state="RECURRING_PATTERN"), "INVALID_NOMINATION_STATE"),
    ("dispositions", lambda rows: rows[0].update(operation_ids=["operation-alpha", "operation-gamma"]), "DISPOSITION_POPULATION_MISMATCH"),
    ("dispositions", lambda rows: rows[0].update(supporting_identity_digest="0" * 64), "CANDIDATE_IDENTITY_DIGEST_DRIFT"),
    ("dispositions", lambda rows: rows[0].update(review_id="0" * 64), "REVIEW_IDENTITY_DRIFT"),
    ("dispositions", lambda rows: rows[0]["authority"].update(attribution=True), "AUTHORITY_DRIFT"),
    ("vocabulary", lambda row: row.update(contract_digest="0" * 64), "VOCABULARY_DIGEST_DRIFT"),
])
def test_identity_membership_authority_and_vocabulary_drift_fail_closed(target, mutation, code):
    values = material()
    mutation(values[target])
    with pytest.raises(OperationalFamilySourceMaterializationError, match=code):
        materialize_fixture_operational_family_source(
            build_operational_family_source_materialization_contract(), **values,
        )


def test_supported_requires_proven_complete_nonconflicting_mechanism_and_temporal_evidence():
    values = material()
    values["candidates"][0]["quality_state"] = "CONFLICTING"
    # Refresh identities so the test reaches the supported-evidence gate.
    candidate = values["candidates"][0]
    identity = {
        "candidate_id": candidate["candidate_id"], "input_digest": candidate["input_digest"],
        "population": sorted(candidate["population"]),
        "supporting_evidence_ids": sorted(candidate["supporting_evidence_ids"]),
        "supporting_primitive_ids": sorted(candidate["supporting_primitive_ids"]),
        "supporting_behaviour_observation_ids": sorted(candidate["supporting_behaviour_observation_ids"]),
        "supporting_topology_revision_ids": sorted(candidate["supporting_topology_revision_ids"]),
        "quality_state": candidate["quality_state"], "missing_evidence": sorted(candidate["missing_evidence"]),
        "contradictory_evidence": sorted(candidate["contradictory_evidence"]), "lifecycle": candidate["lifecycle"],
    }
    disposition = values["dispositions"][0]
    disposition["supporting_identity_digest"] = digest(identity)
    disposition["review_id"] = digest({key: disposition[key] for key in disposition if key != "review_id"})
    with pytest.raises(OperationalFamilySourceMaterializationError, match="SUPPORTED_EVIDENCE_INSUFFICIENT"):
        materialize_fixture_operational_family_source(
            build_operational_family_source_materialization_contract(), **values,
        )


def test_unknown_or_forbidden_runtime_semantics_are_rejected_by_f1():
    values = material()
    values["runtime"][0]["primary_role"] = "operator-owner"
    with pytest.raises(OperationalFamilySourceMaterializationError, match="F1_SOURCE_REJECTED"):
        materialize_fixture_operational_family_source(
            build_operational_family_source_materialization_contract(), **values,
        )


def test_exact_schemas_reject_extra_fields_and_lifecycle_never_supplies_authority():
    values = material()
    values["candidates"][0]["nomination_state"] = "SUPPORTED"
    with pytest.raises(OperationalFamilySourceMaterializationError, match="CANDIDATE_SCHEMA_DRIFT"):
        materialize_fixture_operational_family_source(
            build_operational_family_source_materialization_contract(), **values,
        )
