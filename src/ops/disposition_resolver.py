"""Pure shadow disposition resolution over immutable reconciliation packages.

The resolver has no database, RPC, Registry, clock, cache, or identity lookup.
It can only interpret facts already present in an
``EvidenceReconciliationPackage``.  No production module imports this file.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Final

from src.ops.evidence_reconciliation import EvidenceItem, EvidenceReconciliationPackage


UNRESOLVED: Final = "UNRESOLVED"
INFRASTRUCTURE: Final = "INFRASTRUCTURE"
OPERATOR_CANDIDATE: Final = "OPERATOR_CANDIDATE"
CONFIRMED_OPERATION: Final = "CONFIRMED_OPERATION"
REJECTED: Final = "REJECTED"
RETIRED: Final = "RETIRED"
REVIEW: Final = "REVIEW"

SUPPORTED_DISPOSITIONS: Final = frozenset({
    UNRESOLVED, INFRASTRUCTURE, OPERATOR_CANDIDATE,
    CONFIRMED_OPERATION, REJECTED, RETIRED, REVIEW,
})

_CANONICAL_CONFIRMATION_TYPES: Final = frozenset({"MANUAL_PROMOTION"})
_INFRASTRUCTURE_TYPES: Final = frozenset({
    "CEX", "KNOWN_INFRASTRUCTURE", "EXCHANGE", "EXECUTION_SERVICE",
    "RELAY", "RELAY_SERVICE", "OMNIBUS", "OMNIBUS_WALLET", "CUSTODY",
    "SETTLEMENT_SERVICE", "TRADING_INFRASTRUCTURE", "LIQUIDITY_INFRASTRUCTURE",
    "BRIDGE", "ROUTER",
})
_REJECTION_TYPES: Final = frozenset({
    "DUST_PATTERN", "UNSUPPORTED_ZERO_VALUE", "INVALID_EVIDENCE",
    "NOISE_POPULATION",
})
_RETIREMENT_TYPES: Final = frozenset({
    "RETIREMENT_HISTORY", "POPULATION_RETIRED", "POPULATION_INACTIVE",
})
_CONTROL_TYPES: Final = frozenset({
    "RETURN_TO_CONTROLLER", "SETTLEMENT_CONVERGENCE",
    "RPC_CONFIRMED_LINEAGE", "PERSISTENT_CONTROLLER_REUSE",
    "CONFIRMED_TREASURY_CONTROL", "CREATOR_REUSE_CONTROL",
})
_POPULATION_TYPES: Final = frozenset({
    "TREASURY_RELATIONSHIPS", "PROVISIONING_LINEAGE", "PROVISIONING_SESSIONS",
})


@dataclass(frozen=True, slots=True)
class DispositionEvidenceReference:
    evidence_id: str
    evidence_type: str
    statement: str
    dependency_group: str


@dataclass(frozen=True, slots=True)
class DispositionResult:
    """Immutable, deterministic proposed disposition for one package revision."""

    package_id: str
    population_id: str
    population_revision: str
    disposition: str
    supporting_evidence: tuple[DispositionEvidenceReference, ...]
    contradictory_evidence: tuple[DispositionEvidenceReference, ...]
    context_evidence: tuple[DispositionEvidenceReference, ...]
    missing_evidence: tuple[DispositionEvidenceReference, ...]
    dependency_groups_consulted: tuple[str, ...]
    decision_evidence_ids: tuple[str, ...]
    reasoning_chain: tuple[str, ...]
    result_id: str = field(init=False)

    def __post_init__(self) -> None:
        if self.disposition not in SUPPORTED_DISPOSITIONS:
            raise ValueError(f"Unsupported disposition: {self.disposition}")
        if not self.reasoning_chain:
            raise ValueError("Every proposed disposition requires an exact reasoning chain")
        available = {
            item.evidence_id for item in (
                self.supporting_evidence + self.contradictory_evidence
                + self.context_evidence + self.missing_evidence
            )
        }
        if not self.decision_evidence_ids or any(
            evidence_id not in available for evidence_id in self.decision_evidence_ids
        ):
            raise ValueError("Disposition reasoning must reference available package evidence")
        facts = {
            "package_id": self.package_id,
            "population_revision": self.population_revision,
            "disposition": self.disposition,
            "supporting": tuple(item.evidence_id for item in self.supporting_evidence),
            "contradictory": tuple(item.evidence_id for item in self.contradictory_evidence),
            "context": tuple(item.evidence_id for item in self.context_evidence),
            "missing": tuple(item.evidence_id for item in self.missing_evidence),
            "dependency_groups": self.dependency_groups_consulted,
            "decision_evidence_ids": self.decision_evidence_ids,
            "reasoning_chain": self.reasoning_chain,
        }
        encoded = json.dumps(facts, sort_keys=True, separators=(",", ":")).encode()
        object.__setattr__(self, "result_id", "dr:" + hashlib.sha256(encoded).hexdigest())


ProposedDisposition = DispositionResult


@dataclass(frozen=True, slots=True)
class ShadowDispositionComparison:
    population_id: str
    population_revision: str
    legacy_stage: str
    expected_shadow_equivalent: str
    proposed_disposition: str
    agreement: bool
    missing_evidence_ids: tuple[str, ...]
    contradictory_evidence_ids: tuple[str, ...]
    transition: str


class DispositionResolver:
    """Deterministic pure resolver whose sole input is a reconciliation package."""

    @staticmethod
    def resolve(package: EvidenceReconciliationPackage) -> DispositionResult:
        supporting = tuple(sorted(package.supporting_evidence, key=DispositionResolver._item_key))
        contradictory = tuple(sorted(package.contradictory_evidence, key=DispositionResolver._item_key))
        context = tuple(sorted(package.context, key=DispositionResolver._item_key))
        missing = tuple(sorted(package.missing_evidence, key=DispositionResolver._item_key))

        support_types = {item.evidence_type for item in supporting}
        contradiction_types = {item.evidence_type for item in contradictory}
        context_types = {item.evidence_type for item in context}
        canonical = bool(context_types & _CANONICAL_CONFIRMATION_TYPES)
        infrastructure = bool(contradiction_types & _INFRASTRUCTURE_TYPES)
        rejection = bool(contradiction_types & _REJECTION_TYPES)
        retirement = bool((support_types | context_types) & _RETIREMENT_TYPES)
        control = tuple(item for item in supporting if item.evidence_type in _CONTROL_TYPES)
        population = tuple(item for item in supporting if item.evidence_type in _POPULATION_TYPES)
        control_groups = {item.provenance.dependency_group for item in control}
        population_groups = {item.provenance.dependency_group for item in population}
        independent_control = bool(
            control and population and any(
                control_group != population_group
                for control_group in control_groups
                for population_group in population_groups
            )
        )

        reasons: list[str] = [
            f"Resolved immutable package {package.package_id} for population revision {package.population.revision_id}."
        ]
        decisive: tuple[EvidenceItem, ...]
        if canonical and (infrastructure or rejection or retirement):
            disposition = REVIEW
            decisive = tuple(
                item for item in context if item.evidence_type in _CANONICAL_CONFIRMATION_TYPES
            ) + tuple(
                item for item in contradictory
                if item.evidence_type in (_INFRASTRUCTURE_TYPES | _REJECTION_TYPES)
            ) + tuple(
                item for item in supporting + context if item.evidence_type in _RETIREMENT_TYPES
            )
            reasons.append("Canonical promotion evidence conflicts with an explicit terminal contradiction.")
            reasons.append("Conflicting factual terminal evidence requires review; no terminal disposition is forced.")
        elif canonical:
            disposition = CONFIRMED_OPERATION
            decisive = tuple(item for item in context if item.evidence_type in _CANONICAL_CONFIRMATION_TYPES)
            reasons.append("The package contains an explicit canonical MANUAL_PROMOTION fact.")
            reasons.append("Canonical promotion is persisted operation history, so the shadow disposition is CONFIRMED_OPERATION.")
        elif infrastructure and rejection:
            disposition = REVIEW
            decisive = tuple(
                item for item in contradictory
                if item.evidence_type in (_INFRASTRUCTURE_TYPES | _REJECTION_TYPES)
            )
            reasons.append("The package contains both infrastructure exclusion and invalid/noise evidence.")
            reasons.append("The conflicting terminal explanations require review.")
        elif infrastructure:
            disposition = INFRASTRUCTURE
            decisive = tuple(item for item in contradictory if item.evidence_type in _INFRASTRUCTURE_TYPES)
            reasons.append("An existing Infrastructure Registry or equivalent exclusion fact identifies shared infrastructure.")
            reasons.append("The resolver records INFRASTRUCTURE without inferring an operator identity.")
        elif rejection:
            disposition = REJECTED
            decisive = tuple(item for item in contradictory if item.evidence_type in _REJECTION_TYPES)
            reasons.append("Persisted invalid, unsupported, dust, or noise evidence explains the population.")
            reasons.append("The shadow disposition is REJECTED from the explicit contradiction type.")
        elif retirement:
            disposition = RETIRED
            decisive = tuple(item for item in supporting + context if item.evidence_type in _RETIREMENT_TYPES)
            reasons.append("The package contains an explicit persisted retirement or inactivity fact.")
            reasons.append("No clock or inferred inactivity was used.")
        elif contradictory:
            disposition = REVIEW
            decisive = contradictory
            reasons.append("The package contains observed contradictory evidence without a terminal classification fact.")
            reasons.append("Contradiction is preserved as REVIEW rather than converted into rejection or infrastructure.")
        elif independent_control:
            disposition = OPERATOR_CANDIDATE
            decisive = control + population
            reasons.append("The package contains population evidence and control evidence from different dependency groups.")
            reasons.append("No contradictory evidence is present, so the shadow disposition is OPERATOR_CANDIDATE.")
        else:
            disposition = UNRESOLVED
            decisive = population + missing
            if population:
                reasons.append("The package contains population evidence but no independent control-bearing evidence.")
            else:
                reasons.append("The package does not contain a complete population-and-control evidence combination.")
            if "CONFIRMATION_HISTORY" in context_types:
                reasons.append("Legacy confirmation history is contextual and does not independently establish a reconciled confirmation.")
            if missing:
                reasons.append("Missing evidence remains unknown and is not treated as contradiction.")
            reasons.append("The shadow disposition remains UNRESOLVED.")

        consulted = tuple(sorted({
            item.provenance.dependency_group
            for item in supporting + contradictory + context + missing
        }))
        return DispositionResult(
            package_id=package.package_id,
            population_id=package.population.population_id,
            population_revision=package.population.revision_id,
            disposition=disposition,
            supporting_evidence=tuple(DispositionResolver._reference(item) for item in supporting),
            contradictory_evidence=tuple(DispositionResolver._reference(item) for item in contradictory),
            context_evidence=tuple(DispositionResolver._reference(item) for item in context),
            missing_evidence=tuple(DispositionResolver._reference(item) for item in missing),
            dependency_groups_consulted=consulted,
            decision_evidence_ids=tuple(sorted({item.evidence_id for item in decisive})),
            reasoning_chain=tuple(reasons),
        )

    @staticmethod
    def compare_legacy(legacy_stage: str, result: DispositionResult) -> ShadowDispositionComparison:
        expected = {
            "BACKGROUND": UNRESOLVED,
            "CANDIDATE": OPERATOR_CANDIDATE,
            "SIGNIFICANT_ACTIVE": OPERATOR_CANDIDATE,
            "EMERGING": OPERATOR_CANDIDATE,
            "ESTABLISHED": OPERATOR_CANDIDATE,
            "CONFIRMED": CONFIRMED_OPERATION,
            "DORMANT": RETIRED,
            "RETIRED": REJECTED,
        }.get(str(legacy_stage).upper(), UNRESOLVED)
        return ShadowDispositionComparison(
            population_id=result.population_id,
            population_revision=result.population_revision,
            legacy_stage=str(legacy_stage).upper(),
            expected_shadow_equivalent=expected,
            proposed_disposition=result.disposition,
            agreement=expected == result.disposition,
            missing_evidence_ids=tuple(item.evidence_id for item in result.missing_evidence),
            contradictory_evidence_ids=tuple(item.evidence_id for item in result.contradictory_evidence),
            transition=f"{str(legacy_stage).upper()}->{result.disposition}",
        )

    @staticmethod
    def _reference(item: EvidenceItem) -> DispositionEvidenceReference:
        return DispositionEvidenceReference(
            item.evidence_id, item.evidence_type, item.statement,
            item.provenance.dependency_group,
        )

    @staticmethod
    def _item_key(item: EvidenceItem) -> tuple[str, str]:
        return item.evidence_type, item.evidence_id
