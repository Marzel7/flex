"""
Entity Intelligence — canonical data model.

Everything produced by Entity Intelligence is a derivation of existing data.
No field is invented. Every field carries provenance.

Design rules:
  - entity_type is first-class from day one (wallet / operator / creator /
    treasury / sub_provisioner / unknown). Today almost everything is a wallet;
    the field exists so the API never has to change shape.
  - All timestamps are unix ints or None.
  - Confidence at the entity level is DERIVED from evidence, never assigned.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ── Entity types ───────────────────────────────────────────────────────────────

ENTITY_TYPE_WALLET          = "wallet"
ENTITY_TYPE_OPERATOR        = "operator"
ENTITY_TYPE_CREATOR         = "creator"
ENTITY_TYPE_TREASURY        = "treasury"
ENTITY_TYPE_SUB_PROVISIONER = "sub_provisioner"
ENTITY_TYPE_UNKNOWN         = "unknown"

KNOWN_ENTITY_TYPES = frozenset({
    ENTITY_TYPE_WALLET,
    ENTITY_TYPE_OPERATOR,
    ENTITY_TYPE_CREATOR,
    ENTITY_TYPE_TREASURY,
    ENTITY_TYPE_SUB_PROVISIONER,
    ENTITY_TYPE_UNKNOWN,
})

# Ordered strongest → weakest (mirrors KnowledgeItem confidence vocabulary)
CONFIDENCE_LEVELS = ("CERTAIN", "HIGH", "MEDIUM", "LOW", "UNKNOWN")
CONFIDENCE_RANK: dict[str, int] = {c: i for i, c in enumerate(CONFIDENCE_LEVELS)}


# ── Timeline event ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TimelineEvent:
    """
    One point in the entity's observable history.

    Fields:
      ts          — unix timestamp (required; events without a ts are excluded)
      event_type  — short machine tag  (e.g. LAUNCH_OBSERVED, OPERATION_OBSERVED,
                    KNOWLEDGE_DERIVED, TREASURY_CONFIRMED, SUBPROV_DISCOVERED)
      description — human sentence
      source      — which system produced this event
      provenance  — specific table/rule/field that supplies the timestamp
      metadata    — arbitrary extra fields (mint, launch_count, …)
    """
    ts:          int
    event_type:  str
    description: str
    source:      str
    provenance:  str
    metadata:    dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts":          self.ts,
            "event_type":  self.event_type,
            "description": self.description,
            "source":      self.source,
            "provenance":  self.provenance,
            "metadata":    self.metadata,
        }


# ── Operation observation ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class OperationObservation:
    """
    What one operation knows about this entity.

    Fields:
      operation_id   — registry operation_id (e.g. "watchtower", "launcher-observatory")
      display_name   — human label
      role           — role this entity plays in that operation (e.g. TREASURY, FUNDER)
      facts          — dict of key → value (operation-sourced, read-only)
      first_seen     — earliest timestamp from that operation's data
      last_seen      — latest timestamp
      provenance     — which table/field supplied this
    """
    operation_id:  str
    display_name:  str
    role:          str
    facts:         dict[str, Any]
    first_seen:    int | None
    last_seen:     int | None
    provenance:    str

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation_id":  self.operation_id,
            "display_name":  self.display_name,
            "role":          self.role,
            "facts":         self.facts,
            "first_seen":    self.first_seen,
            "last_seen":     self.last_seen,
            "provenance":    self.provenance,
        }


# ── Confidence breakdown ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class ConfidenceBreakdown:
    """
    Explains the entity-level confidence derivation.

    Fields:
      level              — final derived level (one of CONFIDENCE_LEVELS)
      contributing_facts — list of short sentences that raised or constrained confidence
      ceiling            — the weakest single contributor (confidence can't exceed this)
      floor              — the strongest single contributor (lower bound on evidence)
    """
    level:              str
    contributing_facts: tuple[str, ...]
    ceiling:            str
    floor:              str

    def to_dict(self) -> dict[str, Any]:
        return {
            "level":              self.level,
            "contributing_facts": list(self.contributing_facts),
            "ceiling":            self.ceiling,
            "floor":              self.floor,
        }


# ── Top-level entity summary ───────────────────────────────────────────────────

@dataclass
class EntitySummary:
    """
    The canonical answer to "What do we know about this entity?"

    Fields:
      entity_id         — the identifier (wallet address, or future: operator slug, etc.)
      entity_type       — one of KNOWN_ENTITY_TYPES
      operations        — list[OperationObservation]  — what each operation knows
      knowledge         — list[dict]  — KnowledgeItem.to_dict() from Knowledge Layer
      timeline          — list[TimelineEvent] sorted ascending by ts
      summary_sentences — deterministic summary (pure rules, no LLM)
      confidence        — ConfidenceBreakdown
      first_seen        — earliest ts across all sources (None if no data)
      last_seen         — latest ts across all sources (None if no data)
      generated_at      — unix timestamp of this computation
    """
    entity_id:         str
    entity_type:       str
    operations:        list[OperationObservation]
    knowledge:         list[dict[str, Any]]
    timeline:          list[TimelineEvent]
    summary_sentences: list[str]
    confidence:        ConfidenceBreakdown
    first_seen:        int | None
    last_seen:         int | None
    generated_at:      int

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id":         self.entity_id,
            "entity_type":       self.entity_type,
            "operations":        [o.to_dict() for o in self.operations],
            "knowledge":         self.knowledge,
            "timeline":          [e.to_dict() for e in self.timeline],
            "summary_sentences": self.summary_sentences,
            "confidence":        self.confidence.to_dict(),
            "first_seen":        self.first_seen,
            "last_seen":         self.last_seen,
            "generated_at":      self.generated_at,
        }
