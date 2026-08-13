"""EB1.3D immutable manifests over verified EB1.3C proposal projections."""

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import re
from typing import Tuple

from .evidence_fulfillment_planning_proposal import (
    AUTHORITY,
    CONTRACT_VERSION,
    EvidenceFulfillmentPlanningProposal,
)
from .evidence_fulfillment_planning_proposal_adapters import (
    ADAPTER_VERSION,
    VerifiedPlanningProposalProjection,
)

SCHEMA_VERSION = "eb1.3d.v1"
DIGEST = re.compile(r"^[0-9a-f]{64}$")


class EvidenceFulfillmentPlanningProposalManifestError(ValueError):
    pass


@dataclass(frozen=True)
class EvidenceFulfillmentPlanningProposalManifest:
    schema_version: str
    contract_version: str
    adapter_version: str
    eb1_1h_bundle_digest: str
    requirement_projection_digest: str
    requirement_manifest_digest: str
    requirement_corpus_digest: str
    review_history_digest: str
    proposal_history_digest: str
    proposal_count: int
    authority_lanes: Tuple[str, ...]
    cohort_or_window_identities: Tuple[str, ...]
    authority_lane_counts: dict
    proposals: Tuple[EvidenceFulfillmentPlanningProposal, ...]
    authority_class: str
    grants_planning_authority: bool
    grants_execution_authority: bool
    manifest_digest: str


def _digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(payload.encode()).hexdigest()


def build_evidence_fulfillment_planning_proposal_manifest(
    projection: VerifiedPlanningProposalProjection,
) -> EvidenceFulfillmentPlanningProposalManifest:
    if projection.adapter_version != ADAPTER_VERSION:
        raise EvidenceFulfillmentPlanningProposalManifestError("EB1_3D_ADAPTER_VERSION_MISMATCH")
    history = projection.proposal_history
    if history.contract_version != CONTRACT_VERSION or not history.proposals:
        raise EvidenceFulfillmentPlanningProposalManifestError("EB1_3D_INVALID_PROPOSAL_HISTORY")
    if history.proposal_count != len(history.proposals):
        raise EvidenceFulfillmentPlanningProposalManifestError("EB1_3D_PROPOSAL_COUNT_MISMATCH")
    bound_digests = (
        projection.eb1_1h_bundle_digest,
        projection.requirement_projection_digest,
        projection.requirement_manifest_digest,
        projection.requirement_corpus_digest,
        projection.review_history_digest,
        history.history_digest,
    )
    if any(not isinstance(value, str) or not DIGEST.fullmatch(value) for value in bound_digests):
        raise EvidenceFulfillmentPlanningProposalManifestError("EB1_3D_INVALID_DIGEST")
    for proposal in history.proposals:
        if (
            proposal.requirement_projection_digest != projection.requirement_projection_digest
            or proposal.requirement_manifest_digest != projection.requirement_manifest_digest
            or proposal.requirement_corpus_digest != projection.requirement_corpus_digest
            or proposal.review_history_digest != projection.review_history_digest
        ):
            raise EvidenceFulfillmentPlanningProposalManifestError("EB1_3D_LINEAGE_MISMATCH")
        if (
            proposal.authority_class != AUTHORITY
            or proposal.grants_planning_authority
            or proposal.grants_execution_authority
        ):
            raise EvidenceFulfillmentPlanningProposalManifestError("EB1_3D_AUTHORITY_MISMATCH")
    history_body = {
        "contract_version": history.contract_version,
        "input_review_contract_version": history.input_review_contract_version,
        "proposal_count": history.proposal_count,
        "proposals": [asdict(item) for item in history.proposals],
    }
    if history.history_digest != _digest(history_body):
        raise EvidenceFulfillmentPlanningProposalManifestError("EB1_3D_INVALID_PROPOSAL_HISTORY_DIGEST")
    proposals = tuple(history.proposals)
    lanes = tuple(sorted({item.authority_lane for item in proposals}))
    scopes = tuple(sorted({item.cohort_or_window_identity for item in proposals}))
    counts = {lane: sum(item.authority_lane == lane for item in proposals) for lane in lanes}
    body = {
        "schema_version": SCHEMA_VERSION,
        "contract_version": CONTRACT_VERSION,
        "adapter_version": ADAPTER_VERSION,
        "eb1_1h_bundle_digest": projection.eb1_1h_bundle_digest,
        "requirement_projection_digest": projection.requirement_projection_digest,
        "requirement_manifest_digest": projection.requirement_manifest_digest,
        "requirement_corpus_digest": projection.requirement_corpus_digest,
        "review_history_digest": projection.review_history_digest,
        "proposal_history_digest": history.history_digest,
        "proposal_count": history.proposal_count,
        "authority_lanes": lanes,
        "cohort_or_window_identities": scopes,
        "authority_lane_counts": counts,
        "proposals": [asdict(item) for item in proposals],
        "authority_class": AUTHORITY,
        "grants_planning_authority": False,
        "grants_execution_authority": False,
    }
    return EvidenceFulfillmentPlanningProposalManifest(
        **{key: value for key, value in body.items() if key != "proposals"},
        proposals=proposals,
        manifest_digest=_digest(body),
    )


def verify_evidence_fulfillment_planning_proposal_manifest(manifest, projection):
    if build_evidence_fulfillment_planning_proposal_manifest(projection) != manifest:
        raise EvidenceFulfillmentPlanningProposalManifestError("EB1_3D_REPLAY_MISMATCH")
    return True
