"""Immutable semantic interpretation of factual reconciliation evidence.

This shadow-only layer does not add or change facts.  It describes whether a
persisted observation is true, applies to the population revision being
resolved, and shares an origin with other observations.  Disposition logic can
therefore reason about evidence without mistaking dependency-group names for
independent provenance.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Final

from src.ops.evidence_reconciliation import EvidenceItem, EvidenceReconciliationPackage


TRUE: Final = "TRUE"
UNKNOWN_TRUTH: Final = "UNKNOWN"

APPLICABLE: Final = "APPLICABLE"
PARTIALLY_APPLICABLE: Final = "PARTIALLY_APPLICABLE"
NOT_APPLICABLE: Final = "NOT_APPLICABLE"
UNKNOWN_APPLICABILITY: Final = "UNKNOWN"

INDEPENDENT: Final = "INDEPENDENT"
SHARED_PROVENANCE: Final = "SHARED_PROVENANCE"


@dataclass(frozen=True, slots=True)
class SemanticProvenance:
    """Origin of an observation, deliberately separate from reasoning dependency."""

    chain_id: str
    source: str
    table: str | None
    registry: str | None
    rpc: bool
    dependency_group: str


@dataclass(frozen=True, slots=True)
class SemanticEvidence:
    evidence: EvidenceItem
    truth: str
    applicability: str
    provenance: SemanticProvenance
    provenance_independence: str
    eligible: bool
    applicability_reason: str


@dataclass(frozen=True, slots=True)
class ProvenanceRelationship:
    left_evidence_id: str
    right_evidence_id: str
    independence: str
    shared_chain_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EvidenceSemantics:
    package_id: str
    population_revision: str
    observations: tuple[SemanticEvidence, ...]
    provenance_relationships: tuple[ProvenanceRelationship, ...]
    semantics_id: str = field(init=False)

    def __post_init__(self) -> None:
        encoded = json.dumps({
            "package_id": self.package_id,
            "population_revision": self.population_revision,
            "observations": tuple(
                (item.evidence.evidence_id, item.truth, item.applicability,
                 item.provenance.chain_id, item.provenance_independence, item.eligible)
                for item in self.observations
            ),
            "relationships": tuple(
                (item.left_evidence_id, item.right_evidence_id,
                 item.independence, item.shared_chain_ids)
                for item in self.provenance_relationships
            ),
        }, sort_keys=True, separators=(",", ":")).encode()
        object.__setattr__(self, "semantics_id", "es:" + hashlib.sha256(encoded).hexdigest())

    def observation(self, evidence_id: str) -> SemanticEvidence:
        return next(item for item in self.observations if item.evidence.evidence_id == evidence_id)

    def are_independent(self, left: EvidenceItem, right: EvidenceItem) -> bool:
        if left.evidence_id == right.evidence_id:
            return False
        pair = tuple(sorted((left.evidence_id, right.evidence_id)))
        return not any(
            (item.left_evidence_id, item.right_evidence_id) == pair
            and item.independence == SHARED_PROVENANCE
            for item in self.provenance_relationships
        )


class EvidenceSemanticsService:
    """Pure semantic projection over one immutable reconciliation package."""

    @staticmethod
    def evaluate(package: EvidenceReconciliationPackage) -> EvidenceSemantics:
        population_launches = frozenset(package.population.launches)
        population_entities = frozenset(
            (package.population.anchor,) + package.population.members
        )
        raw_items = (
            package.supporting_evidence + package.contradictory_evidence
            + package.context + package.missing_evidence
        )
        scoped_observations = tuple(sorted((
            EvidenceSemanticsService._evaluate_item(
                item, population_launches, population_entities
            ) for item in raw_items
        ), key=lambda item: item.evidence.evidence_id))
        shared_ids: set[str] = set()
        for index, left in enumerate(scoped_observations):
            for right in scoped_observations[index + 1:]:
                if (
                    left.evidence.evidence_type != right.evidence.evidence_type
                    and left.provenance.dependency_group != right.provenance.dependency_group
                    and EvidenceSemanticsService._shares_persisted_origin(left, right)
                ):
                    shared_ids.update((left.evidence.evidence_id, right.evidence.evidence_id))
        observations = tuple(
            SemanticEvidence(
                evidence=item.evidence,
                truth=item.truth,
                applicability=item.applicability,
                provenance=item.provenance,
                provenance_independence=(
                    SHARED_PROVENANCE
                    if item.evidence.evidence_id in shared_ids
                    else INDEPENDENT
                ),
                eligible=(
                    item.truth == TRUE
                    and item.applicability == APPLICABLE
                    and item.evidence.evidence_id not in shared_ids
                ),
                applicability_reason=item.applicability_reason,
            )
            for item in scoped_observations
        )
        relationships: list[ProvenanceRelationship] = []
        for index, left in enumerate(observations):
            for right in observations[index + 1:]:
                if (
                    left.evidence.evidence_type == right.evidence.evidence_type
                    or left.provenance.dependency_group == right.provenance.dependency_group
                    or not EvidenceSemanticsService._shares_persisted_origin(left, right)
                ):
                    continue
                relationships.append(ProvenanceRelationship(
                    left_evidence_id=left.evidence.evidence_id,
                    right_evidence_id=right.evidence.evidence_id,
                    independence=SHARED_PROVENANCE,
                    shared_chain_ids=(left.provenance.chain_id,),
                ))
        return EvidenceSemantics(
            package_id=package.package_id,
            population_revision=package.population.revision_id,
            observations=observations,
            provenance_relationships=tuple(relationships),
        )

    @staticmethod
    def _shares_persisted_origin(left: SemanticEvidence, right: SemanticEvidence) -> bool:
        if left.provenance.chain_id != right.provenance.chain_id:
            return False
        left_refs = EvidenceSemanticsService._row_references(left.evidence.details)
        right_refs = EvidenceSemanticsService._row_references(right.evidence.details)
        if left_refs and right_refs:
            return bool(left_refs & right_refs)
        # Direct projections over the same persisted relation share provenance
        # when they scope the same entity.  Evidence merely recorded *about*
        # that table (warnings, missing states, RPC provenance) is not assumed
        # to use those rows.
        return bool(
            left.provenance.table
            and left.provenance.source == left.provenance.table
            and right.provenance.source == right.provenance.table
            and frozenset(left.evidence.entities) & frozenset(right.evidence.entities)
        )

    @staticmethod
    def _row_references(value: object, key: str = "") -> frozenset[str]:
        references: set[str] = set()
        if hasattr(value, "items"):
            for child_key, child in value.items():
                references.update(EvidenceSemanticsService._row_references(child, str(child_key)))
        elif isinstance(value, tuple):
            for child in value:
                references.update(EvidenceSemanticsService._row_references(child, key))
        elif value and (
            key.endswith("_id") or key.endswith("_ids")
            or "signature" in key
        ):
            references.add(str(value))
        return frozenset(references)

    @staticmethod
    def _evaluate_item(
        item: EvidenceItem,
        population_launches: frozenset[str],
        population_entities: frozenset[str],
    ) -> SemanticEvidence:
        truth = UNKNOWN_TRUTH if item.role == "MISSING" else TRUE
        scoped_launches = frozenset(item.launches)
        if item.role == "MISSING":
            applicability = UNKNOWN_APPLICABILITY
            reason = "Missing evidence is unknown and cannot be scoped as an observed fact."
        elif scoped_launches:
            overlap = scoped_launches & population_launches
            if scoped_launches <= population_launches:
                applicability = APPLICABLE
                reason = "Every launch referenced by the observation belongs to this population revision."
            elif overlap:
                applicability = PARTIALLY_APPLICABLE
                reason = "Only some launches referenced by the observation belong to this population revision."
            else:
                applicability = NOT_APPLICABLE
                reason = "No launch referenced by the observation belongs to this population revision."
        elif frozenset(item.entities) & population_entities:
            applicability = APPLICABLE
            reason = "The observation directly references an entity in this population revision."
        else:
            applicability = UNKNOWN_APPLICABILITY
            reason = "The observation has no population-scoped launch or entity reference."

        provenance = item.provenance
        # The storage/RPC origin defines the provenance chain.  ``source`` is
        # descriptive and may differ for two projections over the same rows.
        # It must not make those projections appear independent.
        origin = {
            "table": provenance.table,
            "registry": provenance.registry,
            "rpc": provenance.rpc,
        }
        chain_id = "pc:" + hashlib.sha256(json.dumps(
            origin, sort_keys=True, separators=(",", ":")
        ).encode()).hexdigest()
        semantic_provenance = SemanticProvenance(
            chain_id=chain_id,
            source=provenance.source,
            table=provenance.table,
            registry=provenance.registry,
            rpc=provenance.rpc,
            dependency_group=provenance.dependency_group,
        )
        return SemanticEvidence(
            evidence=item,
            truth=truth,
            applicability=applicability,
            provenance=semantic_provenance,
            provenance_independence=INDEPENDENT,
            eligible=truth == TRUE and applicability == APPLICABLE,
            applicability_reason=reason,
        )
