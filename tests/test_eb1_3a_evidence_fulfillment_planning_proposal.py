from dataclasses import replace
import json
from pathlib import Path

import pytest

from src.evidence.contracts.cross_stage_eligibility import project_cross_stage_eligibility
from src.evidence.contracts.evidence_gap_requirement import project_evidence_gap_requirements
from src.evidence.contracts.evidence_fulfillment_planning_proposal import (
    AUTHORITY,
    EvidenceFulfillmentPlanningProposalError,
    project_evidence_fulfillment_planning_proposals,
    verify_evidence_fulfillment_planning_proposals,
)
from src.evidence.contracts.requirement_review_disposition import project_requirement_review_history

FIXTURE = Path(__file__).parent / "fixtures/eb1_0a_cross_stage_eligibility.json"
DIGEST = "a" * 64


def _requirements():
    eligibility = project_cross_stage_eligibility(json.loads(FIXTURE.read_text()))
    return project_evidence_gap_requirements(eligibility).requirements


def _review_record(requirement, disposition="READY_FOR_SEPARATE_PLANNING", sequence=0, supersedes=None):
    return {
        "requirement_id": requirement.requirement_id,
        "requirement_projection_digest": DIGEST,
        "requirement_manifest_digest": DIGEST,
        "requirement_corpus_digest": DIGEST,
        "disposition": disposition,
        "reviewer_identity_token": "reviewer-token",
        "review_sequence": sequence,
        "reason_code": "EVIDENCE_GAP_REVIEWED",
        "rationale_digest": DIGEST,
        "supersedes_disposition_id": supersedes,
    }


def _ready_history(requirements):
    return project_requirement_review_history([_review_record(requirements[0])], requirements)


def _proposal(requirement, history, sequence=0, supersedes=None):
    review = history.dispositions[-1]
    return {
        "requirement_id": requirement.requirement_id,
        "requirement_projection_digest": review.requirement_projection_digest,
        "requirement_manifest_digest": review.requirement_manifest_digest,
        "requirement_corpus_digest": review.requirement_corpus_digest,
        "review_disposition_id": review.disposition_id,
        "review_history_digest": history.history_digest,
        "authority_lane": requirement.authority_lane,
        "cohort_or_window_identity": requirement.cohort_or_window_identity,
        "candidate_evidence_classes": ["CANONICAL_EVENT_FACT", "COMPLETENESS_ASSERTION"],
        "planning_assumptions": ["FIXED_WINDOW", "IMMUTABLE_COHORT"],
        "proposal_sequence": sequence,
        "reason_code": "DESCRIPTIVE_ALTERNATIVES_RECORDED",
        "rationale_digest": DIGEST,
        "supersedes_proposal_id": supersedes,
    }


def test_ready_requirement_projects_unordered_non_executable_alternatives_and_replays():
    requirements = _requirements()
    history = _ready_history(requirements)
    record = _proposal(requirements[0], history)
    result = project_evidence_fulfillment_planning_proposals([record], requirements, history)
    proposal = result.proposals[0]
    assert proposal.authority_class == AUTHORITY
    assert proposal.candidate_evidence_classes == tuple(sorted(record["candidate_evidence_classes"]))
    assert proposal.grants_planning_authority is False
    assert proposal.grants_execution_authority is False
    assert verify_evidence_fulfillment_planning_proposals(result, [record], requirements, history)


def test_non_ready_and_stale_ready_dispositions_fail_closed():
    requirements = _requirements()
    acknowledged = project_requirement_review_history(
        [_review_record(requirements[0], "ACKNOWLEDGED")], requirements
    )
    with pytest.raises(EvidenceFulfillmentPlanningProposalError, match="NOT_READY"):
        project_evidence_fulfillment_planning_proposals(
            [_proposal(requirements[0], acknowledged)], requirements, acknowledged
        )
    first = _ready_history(requirements).dispositions[0]
    deferred = project_requirement_review_history(
        [
            _review_record(requirements[0]),
            _review_record(requirements[0], "DEFERRED", 1, first.disposition_id),
        ],
        requirements,
    )
    stale = _proposal(requirements[0], deferred)
    stale["review_disposition_id"] = first.disposition_id
    with pytest.raises(EvidenceFulfillmentPlanningProposalError, match="NOT_READY"):
        project_evidence_fulfillment_planning_proposals([stale], requirements, deferred)


def test_authority_scope_and_requirement_lineage_mismatches_fail_closed():
    requirements = _requirements()
    history = _ready_history(requirements)
    for field, value, error in (
        ("authority_lane", "EB0.9_OTHER", "CROSS_AUTHORITY"),
        ("cohort_or_window_identity", "other-window", "SCOPE_MISMATCH"),
        ("requirement_manifest_digest", "b" * 64, "LINEAGE_MISMATCH"),
    ):
        record = _proposal(requirements[0], history)
        record[field] = value
        with pytest.raises(EvidenceFulfillmentPlanningProposalError, match=error):
            project_evidence_fulfillment_planning_proposals([record], requirements, history)


def test_executable_source_budget_provider_and_identity_content_is_rejected():
    requirements = _requirements()
    history = _ready_history(requirements)
    for field, value in (
        ("candidate_evidence_classes", ["call provider endpoint"]),
        ("planning_assumptions", ["budget 20 requests"]),
        ("reason_code", "deploy production"),
        ("candidate_evidence_classes", ["operator wallet linkage"]),
    ):
        record = _proposal(requirements[0], history)
        record[field] = value
        with pytest.raises(EvidenceFulfillmentPlanningProposalError, match="FORBIDDEN"):
            project_evidence_fulfillment_planning_proposals([record], requirements, history)


def test_append_only_supersession_and_deterministic_alternative_ordering():
    requirements = _requirements()
    history = _ready_history(requirements)
    first_record = _proposal(requirements[0], history)
    first = project_evidence_fulfillment_planning_proposals(
        [first_record], requirements, history
    ).proposals[0]
    second_record = _proposal(requirements[0], history, 1, first.proposal_id)
    second_record["candidate_evidence_classes"].reverse()
    result = project_evidence_fulfillment_planning_proposals(
        [second_record, first_record], requirements, history
    )
    assert result.proposals[-1].supersedes_proposal_id == first.proposal_id
    bad = _proposal(requirements[0], history, 1)
    with pytest.raises(EvidenceFulfillmentPlanningProposalError, match="INVALID_SUPERSESSION"):
        project_evidence_fulfillment_planning_proposals([first_record, bad], requirements, history)


def test_mutated_history_fails_exact_replay():
    requirements = _requirements()
    history = _ready_history(requirements)
    records = [_proposal(requirements[0], history)]
    result = project_evidence_fulfillment_planning_proposals(records, requirements, history)
    with pytest.raises(EvidenceFulfillmentPlanningProposalError, match="REPLAY_MISMATCH"):
        verify_evidence_fulfillment_planning_proposals(
            replace(result, history_digest="bad"), records, requirements, history
        )


def test_tampered_input_review_history_fails_closed():
    requirements = _requirements()
    history = _ready_history(requirements)
    record = _proposal(requirements[0], history)
    with pytest.raises(EvidenceFulfillmentPlanningProposalError, match="INVALID_REVIEW_HISTORY"):
        project_evidence_fulfillment_planning_proposals(
            [record], requirements, replace(history, history_digest="bad")
        )
