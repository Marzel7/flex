"""
Cross-Operation Intelligence — canonical relationship model.

Every relationship is read-only, deterministic, and fully evidenced.
No operation-specific fields. No heuristics.
"""
from __future__ import annotations

from dataclasses import dataclass, field


# ── Relationship types ─────────────────────────────────────────────────────────

OBSERVED_BY_SAME_OPERATION    = "OBSERVED_BY_SAME_OPERATION"
OBSERVED_BY_MULTIPLE_OPS      = "OBSERVED_BY_MULTIPLE_OPERATIONS"
SHARED_TARGET                 = "SHARED_TARGET"
SHARED_FUNDER                 = "SHARED_FUNDER"
SHARED_SWARM                  = "SHARED_SWARM"
SHARED_OPERATOR               = "SHARED_OPERATOR"

ALL_RELATIONSHIP_TYPES = frozenset({
    OBSERVED_BY_SAME_OPERATION,
    OBSERVED_BY_MULTIPLE_OPS,
    SHARED_TARGET,
    SHARED_FUNDER,
    SHARED_SWARM,
    SHARED_OPERATOR,
})

# ── Confidence levels (deterministic) ─────────────────────────────────────────

CONFIDENCE_CERTAIN = "CERTAIN"
CONFIDENCE_HIGH    = "HIGH"
CONFIDENCE_MEDIUM  = "MEDIUM"
CONFIDENCE_LOW     = "LOW"


@dataclass(frozen=True)
class Relationship:
    relationship_id:       str
    entity_a:              str
    entity_b:              str
    relationship_type:     str
    confidence:            str
    supporting_operations: tuple[str, ...]
    supporting_evidence:   tuple[str, ...]   # human-readable sentences
    first_seen:            int | None
    last_seen:             int | None
    provenance:            str               # table(s) that produced this
    metadata:              dict = field(default_factory=dict, compare=False, hash=False)


@dataclass
class EntityOverlap:
    """Which operations have observed an entity, and the resulting relationships."""
    entity_id:             str
    observed_by:           list[str]           # operation_ids
    operation_count:       int
    relationships:         list[Relationship]
    unified_timeline:      list[dict]          # {ts, event_type, description, source, provenance}
    generated_at:          int


@dataclass
class GlobalStats:
    total_entities:           int
    entities_in_1_operation:  int
    entities_in_2_operations: int
    entities_in_3_operations: int
    total_relationships:      int
    avg_relationships_per_entity: float
    relationship_type_distribution: dict[str, int]
    operation_pair_overlaps:  dict[str, int]   # "wt+lo" -> N entities shared
    generated_at:             int
