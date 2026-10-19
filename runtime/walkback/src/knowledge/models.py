"""
Knowledge Layer — data model.

KnowledgeItem is the single reusable structure produced by every rule.
It is operation-agnostic: no WATCHTOWER terms, no Launcher Observatory terms.

Rules produce KnowledgeItems. The engine collects them. Consumers read them.
Nothing else touches this structure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ── Confidence vocabulary ─────────────────────────────────────────────────────

CONFIDENCE_LEVELS = ("CERTAIN", "HIGH", "MEDIUM", "LOW", "UNKNOWN")
CONFIDENCE_RANK: dict[str, int] = {c: i for i, c in enumerate(CONFIDENCE_LEVELS)}


# ── Categories (the four required by K1) ─────────────────────────────────────

CATEGORY_EXECUTION  = "EXECUTION"
CATEGORY_FUNDER     = "FUNDER_TYPE"
CATEGORY_TOOLING    = "TOOLING"
CATEGORY_BEHAVIOUR  = "BEHAVIOUR"

KNOWN_CATEGORIES = frozenset({
    CATEGORY_EXECUTION,
    CATEGORY_FUNDER,
    CATEGORY_TOOLING,
    CATEGORY_BEHAVIOUR,
})


@dataclass(frozen=True)
class KnowledgeItem:
    """
    One reusable, operation-agnostic enrichment fact about an entity.

    Fields:
      entity_id          — the wallet address this item describes
      category           — one of KNOWN_CATEGORIES
      type               — specific variant within the category (e.g. WRAP_CLOSE)
      value              — human-readable fact (e.g. "Binance Hot Wallet")
      confidence         — CERTAIN | HIGH | MEDIUM | LOW | UNKNOWN
      source             — how this was derived (ADDRESS_TABLE | PATTERN_MATCH | OPERATION_DERIVED)
      provenance         — specific file/table/rule that produced this item
      first_seen         — earliest timestamp in supporting evidence (unix int, optional)
      last_seen          — latest timestamp in supporting evidence (unix int, optional)
      supporting_evidence — list of short strings describing the evidence (not raw data)
    """
    entity_id:           str
    category:            str
    type:                str
    value:               str
    confidence:          str
    source:              str
    provenance:          str
    first_seen:          int | None          = None
    last_seen:           int | None          = None
    supporting_evidence: tuple[str, ...]    = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.confidence not in CONFIDENCE_LEVELS:
            raise ValueError(
                f"KnowledgeItem: invalid confidence {self.confidence!r}. "
                f"Must be one of {CONFIDENCE_LEVELS}."
            )
        if self.category not in KNOWN_CATEGORIES:
            raise ValueError(
                f"KnowledgeItem: unknown category {self.category!r}. "
                f"Must be one of {sorted(KNOWN_CATEGORIES)}."
            )

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict safe for JSON responses."""
        return {
            "entity_id":           self.entity_id,
            "category":            self.category,
            "type":                self.type,
            "value":               self.value,
            "confidence":          self.confidence,
            "source":              self.source,
            "provenance":          self.provenance,
            "first_seen":          self.first_seen,
            "last_seen":           self.last_seen,
            "supporting_evidence": list(self.supporting_evidence),
        }
