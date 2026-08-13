from dataclasses import replace
import json
from pathlib import Path

import pytest

from src.evidence.contracts.cross_stage_eligibility import project_cross_stage_eligibility
from src.evidence.contracts.evidence_fulfillment_planning_proposal_adapters import (
    EvidenceFulfillmentPlanningProposalAdapterError,
    adapt_verified_lineage_to_planning_proposals,
)
from src.evidence.contracts.evidence_gap_requirement import project_evidence_gap_requirements
from src.evidence.contracts.evidence_gap_requirement_bundle import write_evidence_gap_requirement_bundle
from src.evidence.contracts.evidence_gap_requirement_corpus import assemble_evidence_gap_requirement_corpus
from src.evidence.contracts.evidence_gap_requirement_extractor import EvidenceGapRequirementExtraction
from src.evidence.contracts.evidence_gap_requirement_manifest import build_evidence_gap_requirement_manifest
from src.evidence.contracts.requirement_review_disposition import project_requirement_review_history

FIXTURE = Path(__file__).parent / "fixtures/eb1_0a_cross_stage_eligibility.json"
DIGEST = "a" * 64


def _bundle(tmp_path):
    eligibility = project_cross_stage_eligibility(json.loads(FIXTURE.read_text()))
    projection = project_evidence_gap_requirements(eligibility)
    manifest = build_evidence_gap_requirement_manifest(projection)
    corpus = assemble_evidence_gap_requirement_corpus([manifest])
    extraction = EvidenceGapRequirementExtraction(
        "eb1.1g.v1", 4, manifest, corpus, DIGEST, DIGEST
    )
    output = tmp_path / "bundle"
    write_evidence_gap_requirement_bundle(
        extraction, output, run_id="fixture-run", engineering_revision="abcdef0"
    )
    return output, projection.requirements, manifest, corpus


def _review(requirements, manifest, corpus, disposition="READY_FOR_SEPARATE_PLANNING"):
    requirement = requirements[0]
    record = {
        "requirement_id": requirement.requirement_id,
        "requirement_projection_digest": manifest.projection.projection_digest,
        "requirement_manifest_digest": manifest.manifest_digest,
        "requirement_corpus_digest": corpus.corpus_digest,
        "disposition": disposition,
        "reviewer_identity_token": "reviewer-token",
        "review_sequence": 0,
        "reason_code": "EVIDENCE_GAP_REVIEWED",
        "rationale_digest": DIGEST,
        "supersedes_disposition_id": None,
    }
    return project_requirement_review_history([record], requirements)


def _proposal(requirement):
    return {
        "requirement_id": requirement.requirement_id,
        "candidate_evidence_classes": ["CANONICAL_EVENT_FACT", "COMPLETENESS_ASSERTION"],
        "planning_assumptions": ["FIXED_WINDOW", "IMMUTABLE_COHORT"],
        "proposal_sequence": 0,
        "reason_code": "DESCRIPTIVE_ALTERNATIVES_RECORDED",
        "rationale_digest": DIGEST,
        "supersedes_proposal_id": None,
    }


def test_adapter_binds_authoritative_bundle_lineage_and_preserves_caller_alternatives(tmp_path):
    bundle, requirements, manifest, corpus = _bundle(tmp_path)
    history = _review(requirements, manifest, corpus)
    supplied = _proposal(requirements[0])
    result = adapt_verified_lineage_to_planning_proposals(bundle, history, [supplied])
    proposal = result.proposals[0]
    assert proposal.requirement_projection_digest == manifest.projection.projection_digest
    assert proposal.requirement_manifest_digest == manifest.manifest_digest
    assert proposal.requirement_corpus_digest == corpus.corpus_digest
    assert proposal.candidate_evidence_classes == tuple(sorted(supplied["candidate_evidence_classes"]))
    assert not proposal.grants_planning_authority and not proposal.grants_execution_authority


def test_tampered_bundle_fails_before_projection(tmp_path):
    bundle, requirements, manifest, corpus = _bundle(tmp_path)
    history = _review(requirements, manifest, corpus)
    (bundle / "manifest.json").write_text("{}\n")
    with pytest.raises(EvidenceFulfillmentPlanningProposalAdapterError, match="UNVERIFIED"):
        adapt_verified_lineage_to_planning_proposals(bundle, history, [_proposal(requirements[0])])


def test_review_lineage_must_match_authoritative_bundle(tmp_path):
    bundle, requirements, manifest, corpus = _bundle(tmp_path)
    history = _review(requirements, manifest, corpus)
    bad_disposition = replace(history.dispositions[0], requirement_manifest_digest="b" * 64)
    bad_history = replace(history, dispositions=(bad_disposition,))
    with pytest.raises(EvidenceFulfillmentPlanningProposalAdapterError, match="LINEAGE_MISMATCH"):
        adapt_verified_lineage_to_planning_proposals(bundle, bad_history, [_proposal(requirements[0])])


def test_review_authority_and_scope_must_match_requirement(tmp_path):
    bundle, requirements, manifest, corpus = _bundle(tmp_path)
    history = _review(requirements, manifest, corpus)
    bad_disposition = replace(history.dispositions[0], authority_lane="OTHER_LANE")
    bad_history = replace(history, dispositions=(bad_disposition,))
    with pytest.raises(EvidenceFulfillmentPlanningProposalAdapterError, match="AUTHORITY_OR_SCOPE"):
        adapt_verified_lineage_to_planning_proposals(bundle, bad_history, [_proposal(requirements[0])])


def test_latest_review_must_be_ready(tmp_path):
    bundle, requirements, manifest, corpus = _bundle(tmp_path)
    history = _review(requirements, manifest, corpus, "DEFERRED")
    with pytest.raises(EvidenceFulfillmentPlanningProposalAdapterError, match="NOT_READY"):
        adapt_verified_lineage_to_planning_proposals(bundle, history, [_proposal(requirements[0])])


def test_explicit_proposal_schema_cannot_supply_or_override_lineage(tmp_path):
    bundle, requirements, manifest, corpus = _bundle(tmp_path)
    history = _review(requirements, manifest, corpus)
    supplied = _proposal(requirements[0])
    supplied["requirement_manifest_digest"] = "b" * 64
    with pytest.raises(EvidenceFulfillmentPlanningProposalAdapterError, match="SCHEMA_DRIFT"):
        adapt_verified_lineage_to_planning_proposals(bundle, history, [supplied])


def test_forbidden_or_executable_caller_content_is_rejected_by_eb1_3a(tmp_path):
    bundle, requirements, manifest, corpus = _bundle(tmp_path)
    history = _review(requirements, manifest, corpus)
    supplied = _proposal(requirements[0])
    supplied["planning_assumptions"] = ["provider request budget"]
    with pytest.raises(EvidenceFulfillmentPlanningProposalAdapterError, match="EB1_3A_REJECTED"):
        adapt_verified_lineage_to_planning_proposals(bundle, history, [supplied])
