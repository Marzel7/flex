from dataclasses import replace

import pytest

from src.evidence.contracts.evidence_fulfillment_planning_proposal import AUTHORITY
from src.evidence.contracts.evidence_fulfillment_planning_proposal_adapters import (
    ADAPTER_VERSION,
    VerifiedPlanningProposalProjection,
)
from src.evidence.contracts.evidence_fulfillment_planning_proposal_manifest import (
    EvidenceFulfillmentPlanningProposalManifestError,
    build_evidence_fulfillment_planning_proposal_manifest,
    verify_evidence_fulfillment_planning_proposal_manifest,
)
from tests.test_eb1_3c_evidence_fulfillment_planning_proposal_adapters import (
    _bundle,
    _proposal,
    _review,
)
from src.evidence.contracts.evidence_fulfillment_planning_proposal_adapters import (
    adapt_verified_lineage_to_planning_proposal_projection,
)


def _projection(tmp_path):
    bundle, requirements, requirement_manifest, corpus = _bundle(tmp_path)
    history = _review(requirements, requirement_manifest, corpus)
    return adapt_verified_lineage_to_planning_proposal_projection(
        bundle, history, [_proposal(requirements[0])]
    )


def test_manifest_binds_all_verified_lineage_proposals_counts_and_authority(tmp_path):
    projection = _projection(tmp_path)
    manifest = build_evidence_fulfillment_planning_proposal_manifest(projection)
    proposal = projection.proposal_history.proposals[0]
    assert manifest.adapter_version == ADAPTER_VERSION
    assert manifest.eb1_1h_bundle_digest == projection.eb1_1h_bundle_digest
    assert manifest.proposal_history_digest == projection.proposal_history.history_digest
    assert manifest.proposals == projection.proposal_history.proposals
    assert manifest.authority_lane_counts == {proposal.authority_lane: 1}
    assert manifest.authority_class == AUTHORITY
    assert not manifest.grants_planning_authority and not manifest.grants_execution_authority
    assert verify_evidence_fulfillment_planning_proposal_manifest(manifest, projection)


def test_manifest_is_deterministic_and_rejects_lineage_mutation(tmp_path):
    projection = _projection(tmp_path)
    first = build_evidence_fulfillment_planning_proposal_manifest(projection)
    assert first == build_evidence_fulfillment_planning_proposal_manifest(projection)
    changed = replace(projection, requirement_manifest_digest="b" * 64)
    with pytest.raises(EvidenceFulfillmentPlanningProposalManifestError, match="LINEAGE_MISMATCH"):
        build_evidence_fulfillment_planning_proposal_manifest(changed)


def test_manifest_rejects_authority_grant_or_authority_class_mutation(tmp_path):
    projection = _projection(tmp_path)
    proposal = projection.proposal_history.proposals[0]
    for changed_proposal in (
        replace(proposal, grants_planning_authority=True),
        replace(proposal, grants_execution_authority=True),
        replace(proposal, authority_class="EXECUTABLE"),
    ):
        history = replace(projection.proposal_history, proposals=(changed_proposal,))
        with pytest.raises(EvidenceFulfillmentPlanningProposalManifestError, match="AUTHORITY_MISMATCH"):
            build_evidence_fulfillment_planning_proposal_manifest(
                replace(projection, proposal_history=history)
            )


def test_manifest_rejects_wrong_adapter_empty_history_and_count_drift(tmp_path):
    projection = _projection(tmp_path)
    with pytest.raises(EvidenceFulfillmentPlanningProposalManifestError, match="ADAPTER_VERSION"):
        build_evidence_fulfillment_planning_proposal_manifest(
            replace(projection, adapter_version="other")
        )
    empty = replace(projection.proposal_history, proposals=(), proposal_count=0)
    with pytest.raises(EvidenceFulfillmentPlanningProposalManifestError, match="INVALID_PROPOSAL_HISTORY"):
        build_evidence_fulfillment_planning_proposal_manifest(
            replace(projection, proposal_history=empty)
        )
    drift = replace(projection.proposal_history, proposal_count=2)
    with pytest.raises(EvidenceFulfillmentPlanningProposalManifestError, match="PROPOSAL_COUNT"):
        build_evidence_fulfillment_planning_proposal_manifest(
            replace(projection, proposal_history=drift)
        )


def test_manifest_tamper_fails_exact_replay(tmp_path):
    projection = _projection(tmp_path)
    manifest = build_evidence_fulfillment_planning_proposal_manifest(projection)
    with pytest.raises(EvidenceFulfillmentPlanningProposalManifestError, match="REPLAY_MISMATCH"):
        verify_evidence_fulfillment_planning_proposal_manifest(
            replace(manifest, manifest_digest="bad"), projection
        )


def test_manifest_rejects_mutated_verified_envelope_digests(tmp_path):
    projection = _projection(tmp_path)
    bad_history = replace(projection.proposal_history, history_digest="b" * 64)
    with pytest.raises(EvidenceFulfillmentPlanningProposalManifestError, match="HISTORY_DIGEST"):
        build_evidence_fulfillment_planning_proposal_manifest(
            replace(projection, proposal_history=bad_history)
        )
    with pytest.raises(EvidenceFulfillmentPlanningProposalManifestError, match="INVALID_DIGEST"):
        build_evidence_fulfillment_planning_proposal_manifest(
            replace(projection, eb1_1h_bundle_digest="bad")
        )
