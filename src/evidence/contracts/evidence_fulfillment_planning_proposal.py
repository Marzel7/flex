"""EB1.3A pure non-executable evidence-fulfillment planning proposals."""

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import re
from typing import Iterable, Optional, Tuple

from .evidence_gap_requirement import EvidenceGapRequirement
from .requirement_review_disposition import (
    CONTRACT_VERSION as REVIEW_VERSION,
    RequirementReviewHistory,
)

CONTRACT_VERSION = "eb1.3a.v1"
AUTHORITY = "NON_EXECUTABLE_PLANNING_PROPOSAL"
DIGEST = re.compile(r"^[0-9a-f]{64}$")
FORBIDDEN = (
    "http://", "https://", "rpc", "gmgn", "helius", "provider", "api_key",
    "credential", "endpoint", "curl ", "python ", "restart", "deploy",
    "production", "wallet", "creator", "operator", "rank", "score",
    "profit", "cashflow", "activate", "budget", "request_count", "execute",
    "acquire", "source selection",
)


class EvidenceFulfillmentPlanningProposalError(ValueError):
    pass


@dataclass(frozen=True)
class EvidenceFulfillmentPlanningProposal:
    requirement_id: str
    requirement_projection_digest: str
    requirement_manifest_digest: str
    requirement_corpus_digest: str
    review_disposition_id: str
    review_history_digest: str
    upstream_stage: str
    authority_lane: str
    cohort_or_window_identity: str
    candidate_evidence_classes: Tuple[str, ...]
    planning_assumptions: Tuple[str, ...]
    proposal_sequence: int
    reason_code: str
    rationale_digest: str
    supersedes_proposal_id: Optional[str]
    authority_class: str
    grants_planning_authority: bool
    grants_execution_authority: bool
    proposal_id: str


@dataclass(frozen=True)
class EvidenceFulfillmentPlanningProposalHistory:
    contract_version: str
    input_review_contract_version: str
    proposal_count: int
    proposals: Tuple[EvidenceFulfillmentPlanningProposal, ...]
    history_digest: str


def _digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(payload.encode()).hexdigest()


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EvidenceFulfillmentPlanningProposalError(f"EB1_3A_INVALID_{field.upper()}")
    normalized = value.strip()
    if any(term in normalized.lower() for term in FORBIDDEN):
        raise EvidenceFulfillmentPlanningProposalError("EB1_3A_EXECUTABLE_OR_FORBIDDEN_CONTENT")
    return normalized


def _unordered_texts(value: object, field: str) -> Tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or not value:
        raise EvidenceFulfillmentPlanningProposalError(f"EB1_3A_INVALID_{field.upper()}")
    normalized = tuple(sorted({_text(item, field) for item in value}))
    if len(normalized) != len(value):
        raise EvidenceFulfillmentPlanningProposalError(f"EB1_3A_DUPLICATE_{field.upper()}")
    return normalized


def project_evidence_fulfillment_planning_proposals(
    records: Iterable[dict],
    requirements: Iterable[EvidenceGapRequirement],
    review_history: RequirementReviewHistory,
) -> EvidenceFulfillmentPlanningProposalHistory:
    if review_history.contract_version != REVIEW_VERSION:
        raise EvidenceFulfillmentPlanningProposalError("EB1_3A_REVIEW_VERSION_MISMATCH")
    review_body = {
        "contract_version": review_history.contract_version,
        "disposition_count": review_history.disposition_count,
        "disposition_counts": review_history.disposition_counts,
        "dispositions": [asdict(item) for item in review_history.dispositions],
    }
    if (
        review_history.disposition_count != len(review_history.dispositions)
        or review_history.history_digest != _digest(review_body)
    ):
        raise EvidenceFulfillmentPlanningProposalError("EB1_3A_INVALID_REVIEW_HISTORY")
    requirements_by_id = {item.requirement_id: item for item in requirements}
    latest_reviews = {}
    for disposition in review_history.dispositions:
        current = latest_reviews.get(disposition.requirement_id)
        if current is None or disposition.review_sequence > current.review_sequence:
            latest_reviews[disposition.requirement_id] = disposition

    expected = {
        "requirement_id", "requirement_projection_digest", "requirement_manifest_digest",
        "requirement_corpus_digest", "review_disposition_id", "review_history_digest",
        "authority_lane", "cohort_or_window_identity", "candidate_evidence_classes",
        "planning_assumptions", "proposal_sequence", "reason_code", "rationale_digest",
        "supersedes_proposal_id",
    }
    output = []
    seen = {}
    last_sequence = {}
    for record in sorted(records, key=lambda item: item.get("proposal_sequence", -1)):
        if not isinstance(record, dict) or set(record) != expected:
            raise EvidenceFulfillmentPlanningProposalError("EB1_3A_SCHEMA_DRIFT")
        requirement_id = _text(record["requirement_id"], "requirement_id")
        requirement = requirements_by_id.get(requirement_id)
        if requirement is None:
            raise EvidenceFulfillmentPlanningProposalError("EB1_3A_UNKNOWN_REQUIREMENT")
        review = latest_reviews.get(requirement_id)
        if review is None or review.disposition != "READY_FOR_SEPARATE_PLANNING":
            raise EvidenceFulfillmentPlanningProposalError("EB1_3A_REQUIREMENT_NOT_READY")
        if record["review_disposition_id"] != review.disposition_id:
            raise EvidenceFulfillmentPlanningProposalError("EB1_3A_STALE_OR_MISMATCHED_REVIEW")
        if record["review_history_digest"] != review_history.history_digest:
            raise EvidenceFulfillmentPlanningProposalError("EB1_3A_REVIEW_HISTORY_MISMATCH")
        if record["authority_lane"] != requirement.authority_lane:
            raise EvidenceFulfillmentPlanningProposalError("EB1_3A_CROSS_AUTHORITY_SUBSTITUTION")
        if record["cohort_or_window_identity"] != requirement.cohort_or_window_identity:
            raise EvidenceFulfillmentPlanningProposalError("EB1_3A_SCOPE_MISMATCH")
        lineage = (
            record["requirement_projection_digest"], record["requirement_manifest_digest"],
            record["requirement_corpus_digest"], record["review_disposition_id"],
            record["review_history_digest"], record["rationale_digest"],
        )
        if any(not isinstance(value, str) or not DIGEST.fullmatch(value) for value in lineage):
            raise EvidenceFulfillmentPlanningProposalError("EB1_3A_INVALID_DIGEST")
        if lineage[:3] != (
            review.requirement_projection_digest,
            review.requirement_manifest_digest,
            review.requirement_corpus_digest,
        ):
            raise EvidenceFulfillmentPlanningProposalError("EB1_3A_REQUIREMENT_LINEAGE_MISMATCH")
        sequence = record["proposal_sequence"]
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
            raise EvidenceFulfillmentPlanningProposalError("EB1_3A_INVALID_PROPOSAL_SEQUENCE")
        if sequence <= last_sequence.get(requirement_id, -1):
            raise EvidenceFulfillmentPlanningProposalError("EB1_3A_INVALID_PROPOSAL_SEQUENCE")
        supersedes = record["supersedes_proposal_id"]
        if requirement_id in last_sequence:
            prior = seen.get(supersedes) if isinstance(supersedes, str) else None
            if prior is None or prior.requirement_id != requirement_id:
                raise EvidenceFulfillmentPlanningProposalError("EB1_3A_INVALID_SUPERSESSION")
        elif supersedes is not None:
            raise EvidenceFulfillmentPlanningProposalError("EB1_3A_UNUSED_SUPERSESSION")
        body = {
            "contract_version": CONTRACT_VERSION,
            "requirement_id": requirement_id,
            "requirement_projection_digest": lineage[0],
            "requirement_manifest_digest": lineage[1],
            "requirement_corpus_digest": lineage[2],
            "review_disposition_id": lineage[3],
            "review_history_digest": lineage[4],
            "upstream_stage": requirement.upstream_stage,
            "authority_lane": requirement.authority_lane,
            "cohort_or_window_identity": requirement.cohort_or_window_identity,
            "candidate_evidence_classes": _unordered_texts(record["candidate_evidence_classes"], "candidate_evidence_classes"),
            "planning_assumptions": _unordered_texts(record["planning_assumptions"], "planning_assumptions"),
            "proposal_sequence": sequence,
            "reason_code": _text(record["reason_code"], "reason_code"),
            "rationale_digest": lineage[5],
            "supersedes_proposal_id": supersedes,
            "authority_class": AUTHORITY,
            "grants_planning_authority": False,
            "grants_execution_authority": False,
        }
        proposal = EvidenceFulfillmentPlanningProposal(
            **{key: value for key, value in body.items() if key != "contract_version"},
            proposal_id=_digest(body),
        )
        output.append(proposal)
        seen[proposal.proposal_id] = proposal
        last_sequence[requirement_id] = sequence

    ordered = tuple(sorted(output, key=lambda item: (item.requirement_id, item.proposal_sequence, item.proposal_id)))
    history_body = {
        "contract_version": CONTRACT_VERSION,
        "input_review_contract_version": REVIEW_VERSION,
        "proposal_count": len(ordered),
        "proposals": [asdict(item) for item in ordered],
    }
    return EvidenceFulfillmentPlanningProposalHistory(
        CONTRACT_VERSION, REVIEW_VERSION, len(ordered), ordered, _digest(history_body)
    )


def verify_evidence_fulfillment_planning_proposals(result, records, requirements, review_history):
    replay = project_evidence_fulfillment_planning_proposals(records, requirements, review_history)
    if replay != result:
        raise EvidenceFulfillmentPlanningProposalError("EB1_3A_REPLAY_MISMATCH")
    return True
