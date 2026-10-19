"""EB1.3C verified-lineage adapters into the EB1.3A proposal contract."""

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Iterable

from .evidence_fulfillment_planning_proposal import (
    EvidenceFulfillmentPlanningProposalError,
    EvidenceFulfillmentPlanningProposalHistory,
    project_evidence_fulfillment_planning_proposals,
)
from .evidence_gap_requirement_bundle import verify_evidence_gap_requirement_bundle
from .evidence_gap_requirement_corpus import (
    EvidenceGapRequirementCorpus,
    EvidenceGapRequirementLane,
    verify_evidence_gap_requirement_corpus,
)
from .evidence_gap_requirement_manifest import (
    EvidenceGapRequirementManifest,
    verify_evidence_gap_requirement_manifest,
)
from .evidence_gap_requirement import (
    EvidenceGapRequirement,
    EvidenceGapRequirementProjection,
)
from .requirement_review_disposition import RequirementReviewHistory

ADAPTER_VERSION = "eb1.3c.v1"


class EvidenceFulfillmentPlanningProposalAdapterError(ValueError):
    pass


@dataclass(frozen=True)
class VerifiedPlanningProposalProjection:
    adapter_version: str
    eb1_1h_bundle_digest: str
    requirement_projection_digest: str
    requirement_manifest_digest: str
    requirement_corpus_digest: str
    review_history_digest: str
    proposal_history: EvidenceFulfillmentPlanningProposalHistory


def _load_verified_bundle(bundle_directory: Path):
    try:
        verified = verify_evidence_gap_requirement_bundle(bundle_directory)
        manifest_doc = json.loads((Path(bundle_directory) / "manifest.json").read_text())
        corpus_doc = json.loads((Path(bundle_directory) / "corpus.json").read_text())
        projection_doc = manifest_doc["projection"]
        def requirement_from_doc(item):
            return EvidenceGapRequirement(**{**item, "reason_codes": tuple(item["reason_codes"])})

        requirements = tuple(requirement_from_doc(item) for item in projection_doc["requirements"])
        projection = EvidenceGapRequirementProjection(
            projection_doc["contract_version"],
            projection_doc["input_contract_version"],
            projection_doc["input_projection_digest"],
            projection_doc["requirement_count"],
            requirements,
            projection_doc["projection_digest"],
        )
        manifest = EvidenceGapRequirementManifest(
            manifest_doc["schema_version"],
            manifest_doc["contract_version"],
            manifest_doc["adapter_version"],
            manifest_doc["input_projection_digest"],
            projection,
            manifest_doc["manifest_digest"],
        )
        lanes = tuple(
            EvidenceGapRequirementLane(
                lane["upstream_stage"],
                lane["authority_lane"],
                tuple(requirement_from_doc(item) for item in lane["requirements"]),
                lane["lane_digest"],
            )
            for lane in corpus_doc["lanes"]
        )
        corpus = EvidenceGapRequirementCorpus(
            corpus_doc["schema_version"],
            tuple(corpus_doc["source_manifest_digests"]),
            lanes,
            corpus_doc["corpus_digest"],
        )
        verify_evidence_gap_requirement_manifest(manifest, projection)
        verify_evidence_gap_requirement_corpus(corpus, [manifest])
        normalized_manifest = json.loads(json.dumps(asdict(manifest), default=str))
        normalized_corpus = json.loads(json.dumps(asdict(corpus), default=str))
        if normalized_manifest != manifest_doc or normalized_corpus != corpus_doc:
            raise EvidenceFulfillmentPlanningProposalAdapterError("EB1_3C_BUNDLE_OBJECT_MISMATCH")
    except EvidenceFulfillmentPlanningProposalAdapterError:
        raise
    except Exception as exc:
        raise EvidenceFulfillmentPlanningProposalAdapterError("EB1_3C_UNVERIFIED_EB1_1H_BUNDLE") from exc
    return verified, manifest, corpus, requirements


def adapt_verified_lineage_to_planning_proposal_projection(
    bundle_directory: Path,
    review_history: RequirementReviewHistory,
    proposal_inputs: Iterable[dict],
):
    """Bind verified EB1.1H lineage to EB1.2A and project explicit EB1.3A inputs."""
    verified, manifest, corpus, requirements = _load_verified_bundle(bundle_directory)
    requirement_by_id = {item.requirement_id: item for item in requirements}
    if len(requirement_by_id) != len(requirements):
        raise EvidenceFulfillmentPlanningProposalAdapterError("EB1_3C_DUPLICATE_REQUIREMENT")

    authoritative = (
        manifest.projection.projection_digest,
        manifest.manifest_digest,
        corpus.corpus_digest,
    )
    for disposition in review_history.dispositions:
        requirement = requirement_by_id.get(disposition.requirement_id)
        if requirement is None:
            raise EvidenceFulfillmentPlanningProposalAdapterError("EB1_3C_REVIEW_REQUIREMENT_MISMATCH")
        if (
            disposition.requirement_projection_digest,
            disposition.requirement_manifest_digest,
            disposition.requirement_corpus_digest,
        ) != authoritative:
            raise EvidenceFulfillmentPlanningProposalAdapterError("EB1_3C_REVIEW_LINEAGE_MISMATCH")
        if (
            disposition.upstream_stage != requirement.upstream_stage
            or disposition.authority_lane != requirement.authority_lane
            or disposition.cohort_or_window_identity != requirement.cohort_or_window_identity
        ):
            raise EvidenceFulfillmentPlanningProposalAdapterError("EB1_3C_REVIEW_AUTHORITY_OR_SCOPE_MISMATCH")

    expected = {
        "requirement_id", "candidate_evidence_classes", "planning_assumptions",
        "proposal_sequence", "reason_code", "rationale_digest", "supersedes_proposal_id",
    }
    records = []
    for supplied in proposal_inputs:
        if not isinstance(supplied, dict) or set(supplied) != expected:
            raise EvidenceFulfillmentPlanningProposalAdapterError("EB1_3C_PROPOSAL_INPUT_SCHEMA_DRIFT")
        requirement = requirement_by_id.get(supplied["requirement_id"])
        if requirement is None:
            raise EvidenceFulfillmentPlanningProposalAdapterError("EB1_3C_UNKNOWN_REQUIREMENT")
        latest = max(
            (item for item in review_history.dispositions if item.requirement_id == requirement.requirement_id),
            key=lambda item: item.review_sequence,
            default=None,
        )
        if latest is None or latest.disposition != "READY_FOR_SEPARATE_PLANNING":
            raise EvidenceFulfillmentPlanningProposalAdapterError("EB1_3C_REQUIREMENT_NOT_READY")
        records.append(
            {
                **supplied,
                "requirement_projection_digest": authoritative[0],
                "requirement_manifest_digest": authoritative[1],
                "requirement_corpus_digest": authoritative[2],
                "review_disposition_id": latest.disposition_id,
                "review_history_digest": review_history.history_digest,
                "authority_lane": requirement.authority_lane,
                "cohort_or_window_identity": requirement.cohort_or_window_identity,
            }
        )
    try:
        history = project_evidence_fulfillment_planning_proposals(records, requirements, review_history)
    except EvidenceFulfillmentPlanningProposalError as exc:
        raise EvidenceFulfillmentPlanningProposalAdapterError("EB1_3C_EB1_3A_REJECTED") from exc
    return VerifiedPlanningProposalProjection(
        ADAPTER_VERSION,
        verified.bundle_digest,
        authoritative[0],
        authoritative[1],
        authoritative[2],
        review_history.history_digest,
        history,
    )


def adapt_verified_lineage_to_planning_proposals(
    bundle_directory: Path,
    review_history: RequirementReviewHistory,
    proposal_inputs: Iterable[dict],
):
    """Backward-compatible EB1.3C projection returning the EB1.3A history."""
    return adapt_verified_lineage_to_planning_proposal_projection(
        bundle_directory, review_history, proposal_inputs
    ).proposal_history
