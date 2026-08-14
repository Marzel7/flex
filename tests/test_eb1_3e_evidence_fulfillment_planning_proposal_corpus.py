from dataclasses import replace

import pytest

from src.evidence.contracts.evidence_fulfillment_planning_proposal import AUTHORITY
from src.evidence.contracts.evidence_fulfillment_planning_proposal_corpus import (
    EvidenceFulfillmentPlanningProposalCorpusError,
    assemble_evidence_fulfillment_planning_proposal_corpus,
    verify_evidence_fulfillment_planning_proposal_corpus,
)
from src.evidence.contracts.evidence_fulfillment_planning_proposal_manifest import (
    build_evidence_fulfillment_planning_proposal_manifest,
)
from tests.test_eb1_3d_evidence_fulfillment_planning_proposal_manifest import _projection


def _verified(tmp_path):
    projection = _projection(tmp_path)
    return build_evidence_fulfillment_planning_proposal_manifest(projection), projection


def test_corpus_groups_verified_manifest_by_stage_and_authority_with_coverage_counts(tmp_path):
    pair = _verified(tmp_path)
    corpus = assemble_evidence_fulfillment_planning_proposal_corpus([pair])
    proposal = pair[0].proposals[0]
    lane = corpus.lanes[0]
    assert (lane.upstream_stage, lane.authority_lane) == (
        proposal.upstream_stage,
        proposal.authority_lane,
    )
    assert lane.proposal_count == lane.requirement_count == lane.review_count == lane.scope_count == 1
    assert lane.alternative_count == len(proposal.candidate_evidence_classes)
    assert lane.assumption_count == len(proposal.planning_assumptions)
    assert lane.entries[0].source_manifest_digest == pair[0].manifest_digest
    assert corpus.authority_class == AUTHORITY
    assert not corpus.grants_planning_authority and not corpus.grants_execution_authority
    assert verify_evidence_fulfillment_planning_proposal_corpus(corpus, [pair])


def test_corpus_is_deterministic_and_preserves_exact_proposal(tmp_path):
    pair = _verified(tmp_path)
    first = assemble_evidence_fulfillment_planning_proposal_corpus([pair])
    second = assemble_evidence_fulfillment_planning_proposal_corpus([pair])
    assert first == second
    assert first.lanes[0].entries[0].proposal == pair[0].proposals[0]


def test_empty_unverified_and_duplicate_manifest_inputs_fail_closed(tmp_path):
    pair = _verified(tmp_path)
    with pytest.raises(EvidenceFulfillmentPlanningProposalCorpusError, match="EMPTY_INPUT"):
        assemble_evidence_fulfillment_planning_proposal_corpus([])
    with pytest.raises(EvidenceFulfillmentPlanningProposalCorpusError, match="UNVERIFIED_MANIFEST_INPUT"):
        assemble_evidence_fulfillment_planning_proposal_corpus([pair[0]])
    with pytest.raises(EvidenceFulfillmentPlanningProposalCorpusError, match="DUPLICATE_MANIFEST"):
        assemble_evidence_fulfillment_planning_proposal_corpus([pair, pair])


def test_manifest_tamper_and_cross_lane_substitution_fail_closed(tmp_path):
    manifest, projection = _verified(tmp_path)
    with pytest.raises(EvidenceFulfillmentPlanningProposalCorpusError, match="UNVERIFIED_MANIFEST"):
        assemble_evidence_fulfillment_planning_proposal_corpus(
            [(replace(manifest, manifest_digest="bad"), projection)]
        )
    changed_proposal = replace(manifest.proposals[0], authority_lane="OTHER_LANE")
    changed_history = replace(projection.proposal_history, proposals=(changed_proposal,))
    changed_projection = replace(projection, proposal_history=changed_history)
    changed_manifest = replace(manifest, proposals=(changed_proposal,), authority_lanes=("OTHER_LANE",))
    with pytest.raises(EvidenceFulfillmentPlanningProposalCorpusError, match="UNVERIFIED_MANIFEST"):
        assemble_evidence_fulfillment_planning_proposal_corpus(
            [(changed_manifest, changed_projection)]
        )


def test_corpus_tamper_fails_exact_replay(tmp_path):
    pair = _verified(tmp_path)
    corpus = assemble_evidence_fulfillment_planning_proposal_corpus([pair])
    with pytest.raises(EvidenceFulfillmentPlanningProposalCorpusError, match="REPLAY_MISMATCH"):
        verify_evidence_fulfillment_planning_proposal_corpus(
            replace(corpus, corpus_digest="bad"), [pair]
        )
