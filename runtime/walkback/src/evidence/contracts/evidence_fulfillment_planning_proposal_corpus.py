"""EB1.3E immutable per-authority-lane planning-proposal corpus."""

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from typing import Tuple

from .evidence_fulfillment_planning_proposal import (
    AUTHORITY,
    EvidenceFulfillmentPlanningProposal,
)
from .evidence_fulfillment_planning_proposal_adapters import VerifiedPlanningProposalProjection
from .evidence_fulfillment_planning_proposal_manifest import (
    EvidenceFulfillmentPlanningProposalManifest,
    verify_evidence_fulfillment_planning_proposal_manifest,
)

SCHEMA_VERSION = "eb1.3e.v1"


class EvidenceFulfillmentPlanningProposalCorpusError(ValueError):
    pass


@dataclass(frozen=True)
class EvidenceFulfillmentPlanningProposalCorpusEntry:
    source_manifest_digest: str
    proposal: EvidenceFulfillmentPlanningProposal
    entry_digest: str


@dataclass(frozen=True)
class EvidenceFulfillmentPlanningProposalLane:
    upstream_stage: str
    authority_lane: str
    proposal_count: int
    requirement_count: int
    review_count: int
    scope_count: int
    alternative_count: int
    assumption_count: int
    entries: Tuple[EvidenceFulfillmentPlanningProposalCorpusEntry, ...]
    lane_digest: str


@dataclass(frozen=True)
class EvidenceFulfillmentPlanningProposalCorpus:
    schema_version: str
    source_manifest_digests: Tuple[str, ...]
    lane_count: int
    proposal_count: int
    requirement_count: int
    review_count: int
    scope_count: int
    lanes: Tuple[EvidenceFulfillmentPlanningProposalLane, ...]
    authority_class: str
    grants_planning_authority: bool
    grants_execution_authority: bool
    corpus_digest: str


def _digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(payload.encode()).hexdigest()


def assemble_evidence_fulfillment_planning_proposal_corpus(verified_manifests):
    pairs = tuple(verified_manifests)
    if not pairs:
        raise EvidenceFulfillmentPlanningProposalCorpusError("EB1_3E_EMPTY_INPUT")
    manifests = {}
    proposals_by_id = {}
    grouped = {}
    for pair in pairs:
        if not isinstance(pair, tuple) or len(pair) != 2:
            raise EvidenceFulfillmentPlanningProposalCorpusError("EB1_3E_UNVERIFIED_MANIFEST_INPUT")
        manifest, projection = pair
        if not isinstance(manifest, EvidenceFulfillmentPlanningProposalManifest) or not isinstance(
            projection, VerifiedPlanningProposalProjection
        ):
            raise EvidenceFulfillmentPlanningProposalCorpusError("EB1_3E_UNVERIFIED_MANIFEST_INPUT")
        try:
            verify_evidence_fulfillment_planning_proposal_manifest(manifest, projection)
        except Exception as exc:
            raise EvidenceFulfillmentPlanningProposalCorpusError("EB1_3E_UNVERIFIED_MANIFEST") from exc
        prior_manifest = manifests.get(manifest.manifest_digest)
        if prior_manifest is not None:
            if prior_manifest != manifest:
                raise EvidenceFulfillmentPlanningProposalCorpusError("EB1_3E_MANIFEST_COLLISION")
            raise EvidenceFulfillmentPlanningProposalCorpusError("EB1_3E_DUPLICATE_MANIFEST")
        manifests[manifest.manifest_digest] = manifest
        for proposal in manifest.proposals:
            if proposal.authority_lane not in manifest.authority_lanes:
                raise EvidenceFulfillmentPlanningProposalCorpusError("EB1_3E_CROSS_LANE_SUBSTITUTION")
            prior_proposal = proposals_by_id.get(proposal.proposal_id)
            if prior_proposal is not None and prior_proposal != proposal:
                raise EvidenceFulfillmentPlanningProposalCorpusError("EB1_3E_PROPOSAL_COLLISION")
            proposals_by_id[proposal.proposal_id] = proposal
            entry_body = {
                "source_manifest_digest": manifest.manifest_digest,
                "proposal": asdict(proposal),
            }
            entry = EvidenceFulfillmentPlanningProposalCorpusEntry(
                manifest.manifest_digest, proposal, _digest(entry_body)
            )
            grouped.setdefault((proposal.upstream_stage, proposal.authority_lane), []).append(entry)

    lanes = []
    for (stage, authority_lane), entries in sorted(grouped.items()):
        ordered = tuple(sorted(entries, key=lambda item: (item.proposal.proposal_id, item.source_manifest_digest)))
        proposals = tuple(item.proposal for item in ordered)
        lane_body = {
            "upstream_stage": stage,
            "authority_lane": authority_lane,
            "proposal_count": len(ordered),
            "requirement_count": len({item.requirement_id for item in proposals}),
            "review_count": len({item.review_disposition_id for item in proposals}),
            "scope_count": len({item.cohort_or_window_identity for item in proposals}),
            "alternative_count": sum(len(item.candidate_evidence_classes) for item in proposals),
            "assumption_count": sum(len(item.planning_assumptions) for item in proposals),
            "entries": [asdict(item) for item in ordered],
        }
        lanes.append(
            EvidenceFulfillmentPlanningProposalLane(
                **{key: value for key, value in lane_body.items() if key != "entries"},
                entries=ordered,
                lane_digest=_digest(lane_body),
            )
        )
    lanes = tuple(lanes)
    all_proposals = tuple(entry.proposal for lane in lanes for entry in lane.entries)
    body = {
        "schema_version": SCHEMA_VERSION,
        "source_manifest_digests": tuple(sorted(manifests)),
        "lane_count": len(lanes),
        "proposal_count": len(all_proposals),
        "requirement_count": len({item.requirement_id for item in all_proposals}),
        "review_count": len({item.review_disposition_id for item in all_proposals}),
        "scope_count": len({item.cohort_or_window_identity for item in all_proposals}),
        "lanes": [asdict(lane) for lane in lanes],
        "authority_class": AUTHORITY,
        "grants_planning_authority": False,
        "grants_execution_authority": False,
    }
    return EvidenceFulfillmentPlanningProposalCorpus(
        **{key: value for key, value in body.items() if key != "lanes"},
        lanes=lanes,
        corpus_digest=_digest(body),
    )


def verify_evidence_fulfillment_planning_proposal_corpus(corpus, verified_manifests):
    if assemble_evidence_fulfillment_planning_proposal_corpus(verified_manifests) != corpus:
        raise EvidenceFulfillmentPlanningProposalCorpusError("EB1_3E_REPLAY_MISMATCH")
    return True
