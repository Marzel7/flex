"""Deterministic, read-only operator identity evaluation primitives.

This module deliberately has no database or network dependencies.  It models the
two stages used by X16B:

1. evidence evaluators emit immutable observations;
2. the decision engine emits immutable promotion proposals.

Neither stage knows how to persist a canonical operator.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Iterable

from src.ops.operator_model import (
    EVIDENCE_CATALOGUE,
    EVIDENCE_CONTEXT,
    EVIDENCE_IDENTITY,
    EVIDENCE_SUPPORTING,
)


PROMOTION_ELIGIBLE = "PROMOTION_ELIGIBLE"
REVIEW_CANDIDATE = "REVIEW_CANDIDATE"
REVIEW_REQUIRED = "REVIEW_REQUIRED"
INSUFFICIENT = "INSUFFICIENT"

MIN_PROMOTION_IDENTITY_CLASSES = 2
MIN_PROMOTION_CONFIDENCE = 0.75


def _stable_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _stable_value(v) for k, v in sorted(value.items())}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_stable_value(v) for v in sorted(value, key=lambda item: str(item))]
    return value


def _stable_id(prefix: str, payload: dict[str, Any]) -> str:
    encoded = json.dumps(_stable_value(payload), sort_keys=True, separators=(",", ":"))
    return f"{prefix}-{hashlib.sha256(encoded.encode('utf-8')).hexdigest()[:20]}"


@dataclass(frozen=True)
class EvidenceObservation:
    """One explainable observation from the existing X8 evidence catalogue."""

    candidate_key: str
    evidence_type: str
    category: str
    confidence: float
    reason: str
    source_tables: tuple[str, ...]
    entities: tuple[str, ...] = ()
    operations: tuple[str, ...] = ()
    legacy_source: str | None = None
    legacy_identifier: str | None = None
    details: tuple[tuple[str, Any], ...] = ()
    observation_id: str = field(init=False)

    def __post_init__(self) -> None:
        catalogue = EVIDENCE_CATALOGUE.get(self.evidence_type)
        if not catalogue:
            raise ValueError(f"Unknown X8 evidence type: {self.evidence_type!r}")
        if catalogue["category"] != self.category:
            raise ValueError(
                f"{self.evidence_type} is {catalogue['category']}, not {self.category}"
            )
        if not self.candidate_key or not self.reason or not self.source_tables:
            raise ValueError("candidate_key, reason and source_tables are required")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")

        object.__setattr__(self, "source_tables", tuple(sorted(set(self.source_tables))))
        object.__setattr__(self, "entities", tuple(sorted(set(self.entities))))
        object.__setattr__(self, "operations", tuple(sorted(set(self.operations))))
        object.__setattr__(self, "details", tuple(sorted(self.details, key=lambda item: item[0])))
        object.__setattr__(self, "observation_id", _stable_id("obs", self._identity_payload()))

    def _identity_payload(self) -> dict[str, Any]:
        return {
            "candidate_key": self.candidate_key,
            "evidence_type": self.evidence_type,
            "category": self.category,
            "confidence": round(self.confidence, 8),
            "reason": self.reason,
            "source_tables": self.source_tables,
            "entities": self.entities,
            "operations": self.operations,
            "legacy_source": self.legacy_source,
            "legacy_identifier": self.legacy_identifier,
            "details": self.details,
        }

    def to_dict(self) -> dict[str, Any]:
        return {"observation_id": self.observation_id, **self._identity_payload()}


@dataclass(frozen=True)
class IdentityObservation(EvidenceObservation):
    """An observation that is permitted to establish actor identity."""

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.category != EVIDENCE_IDENTITY:
            raise ValueError("IdentityObservation must use IDENTITY evidence")
        if not self.entities and not self.operations:
            raise ValueError("IdentityObservation requires an entity or operation chain")


@dataclass(frozen=True)
class ContradictionObservation:
    candidate_key: str
    reason: str
    source_tables: tuple[str, ...]
    related_entities: tuple[str, ...] = ()
    contradiction_id: str = field(init=False)

    def __post_init__(self) -> None:
        if not self.candidate_key or not self.reason or not self.source_tables:
            raise ValueError("contradiction provenance is required")
        object.__setattr__(self, "source_tables", tuple(sorted(set(self.source_tables))))
        object.__setattr__(self, "related_entities", tuple(sorted(set(self.related_entities))))
        object.__setattr__(self, "contradiction_id", _stable_id("contra", self.to_payload()))

    def to_payload(self) -> dict[str, Any]:
        return {
            "candidate_key": self.candidate_key,
            "reason": self.reason,
            "source_tables": self.source_tables,
            "related_entities": self.related_entities,
        }

    def to_dict(self) -> dict[str, Any]:
        return {"contradiction_id": self.contradiction_id, **self.to_payload()}


@dataclass(frozen=True)
class IdentityEvaluation:
    identity: tuple[IdentityObservation, ...] = ()
    supporting: tuple[EvidenceObservation, ...] = ()
    context: tuple[EvidenceObservation, ...] = ()
    contradictions: tuple[ContradictionObservation, ...] = ()

    def __post_init__(self) -> None:
        for observation in self.supporting:
            if observation.category != EVIDENCE_SUPPORTING:
                raise ValueError("supporting collection contains non-supporting evidence")
        for observation in self.context:
            if observation.category != EVIDENCE_CONTEXT:
                raise ValueError("context collection contains non-context evidence")
        object.__setattr__(self, "identity", _dedupe_observations(self.identity))
        object.__setattr__(self, "supporting", _dedupe_observations(self.supporting))
        object.__setattr__(self, "context", _dedupe_observations(self.context))
        object.__setattr__(
            self,
            "contradictions",
            tuple(sorted({c.contradiction_id: c for c in self.contradictions}.values(),
                         key=lambda c: c.contradiction_id)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "identity": [item.to_dict() for item in self.identity],
            "supporting": [item.to_dict() for item in self.supporting],
            "context": [item.to_dict() for item in self.context],
            "contradictions": [item.to_dict() for item in self.contradictions],
        }


def _dedupe_observations(items: Iterable[EvidenceObservation]) -> tuple:
    return tuple(sorted(
        {item.observation_id: item for item in items}.values(),
        key=lambda item: item.observation_id,
    ))


@dataclass(frozen=True)
class PromotionProposal:
    candidate_key: str
    decision: str
    identity_classes: tuple[str, ...]
    identity_confidence: float
    review_confidence: float
    identity_observation_ids: tuple[str, ...]
    supporting_observation_ids: tuple[str, ...]
    context_observation_ids: tuple[str, ...]
    contradiction_ids: tuple[str, ...]
    reason: str
    proposal_id: str = field(init=False)
    proposal_fingerprint: str = field(init=False)
    identity_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "identity_classes", tuple(sorted(set(self.identity_classes))))
        object.__setattr__(self, "identity_observation_ids", tuple(sorted(self.identity_observation_ids)))
        object.__setattr__(self, "supporting_observation_ids", tuple(sorted(self.supporting_observation_ids)))
        object.__setattr__(self, "context_observation_ids", tuple(sorted(self.context_observation_ids)))
        object.__setattr__(self, "contradiction_ids", tuple(sorted(self.contradiction_ids)))
        # The review URL identifies the candidate and remains stable while its
        # evidence evolves.  The two fingerprints bind a decision to the exact
        # proposal and identity package the analyst reviewed.
        object.__setattr__(self, "proposal_id", _stable_id(
            "proposal", {"candidate_key": self.candidate_key}
        ))
        object.__setattr__(self, "proposal_fingerprint", _stable_id(
            "proposal-fingerprint", self._payload()
        ))
        object.__setattr__(self, "identity_fingerprint", _stable_id(
            "identity-fingerprint", {
                "candidate_key": self.candidate_key,
                "identity_classes": self.identity_classes,
                "identity_observation_ids": self.identity_observation_ids,
            }
        ))

    def _payload(self) -> dict[str, Any]:
        return {
            "candidate_key": self.candidate_key,
            "decision": self.decision,
            "identity_classes": self.identity_classes,
            "identity_confidence": round(self.identity_confidence, 8),
            "review_confidence": round(self.review_confidence, 8),
            "identity_observation_ids": self.identity_observation_ids,
            "supporting_observation_ids": self.supporting_observation_ids,
            "context_observation_ids": self.context_observation_ids,
            "contradiction_ids": self.contradiction_ids,
            "reason": self.reason,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "proposal_fingerprint": self.proposal_fingerprint,
            "identity_fingerprint": self.identity_fingerprint,
            **self._payload(),
        }


class PromotionDecisionEngine:
    """Pure stage-two decision engine; identity classes alone control eligibility."""

    def decide(self, evaluation: IdentityEvaluation) -> tuple[PromotionProposal, ...]:
        # Stage two is identity-led.  Supporting/context-only keys are retained in
        # the evaluation report but cannot manufacture even a candidate proposal.
        keys = sorted(
            {item.candidate_key for item in evaluation.identity}
            | {item.candidate_key for item in evaluation.contradictions}
        )
        proposals: list[PromotionProposal] = []
        for key in keys:
            identity = tuple(item for item in evaluation.identity if item.candidate_key == key)
            supporting = tuple(item for item in evaluation.supporting if item.candidate_key == key)
            context = tuple(item for item in evaluation.context if item.candidate_key == key)
            contradictions = tuple(item for item in evaluation.contradictions if item.candidate_key == key)
            classes = tuple(sorted({item.evidence_type for item in identity}))
            identity_confidence = _class_confidence(identity)
            review_confidence = _review_confidence(identity_confidence, supporting)

            if contradictions:
                decision = REVIEW_REQUIRED
                reason = "Contradictory identity evidence requires analyst resolution."
            elif not classes:
                decision = INSUFFICIENT
                reason = "No identity evidence class is present."
            elif len(classes) == 1:
                decision = REVIEW_CANDIDATE
                reason = "One identity evidence class is present; independent corroboration is required."
            elif identity_confidence < MIN_PROMOTION_CONFIDENCE:
                decision = REVIEW_REQUIRED
                reason = "Multiple identity classes are present but identity confidence is below threshold."
            else:
                decision = PROMOTION_ELIGIBLE
                reason = "At least two independent identity classes satisfy the promotion threshold."

            proposals.append(PromotionProposal(
                candidate_key=key,
                decision=decision,
                identity_classes=classes,
                identity_confidence=identity_confidence,
                review_confidence=review_confidence,
                identity_observation_ids=tuple(item.observation_id for item in identity),
                supporting_observation_ids=tuple(item.observation_id for item in supporting),
                context_observation_ids=tuple(item.observation_id for item in context),
                contradiction_ids=tuple(item.contradiction_id for item in contradictions),
                reason=reason,
            ))
        return tuple(proposals)


def _class_confidence(identity: tuple[IdentityObservation, ...]) -> float:
    if not identity:
        return 0.0
    per_class: dict[str, float] = {}
    for item in identity:
        per_class[item.evidence_type] = max(per_class.get(item.evidence_type, 0.0), item.confidence)
    return round(sum(per_class.values()) / len(per_class), 6)


def _review_confidence(identity_confidence: float,
                       supporting: tuple[EvidenceObservation, ...]) -> float:
    if identity_confidence == 0.0:
        return 0.0
    # Supporting observations can aid review, but the bounded uplift is never fed
    # back into the promotion threshold.
    support_classes = {item.evidence_type for item in supporting}
    return round(min(1.0, identity_confidence + min(0.10, 0.02 * len(support_classes))), 6)
