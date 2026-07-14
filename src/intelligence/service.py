"""
Entity Intelligence — orchestration layer.

EntityIntelligenceService is the single public entry point.
It coordinates:
  1. Aggregator  — gathers operation observations + timeline events
  2. Knowledge Layer  — derives knowledge items
  3. Confidence engine — derives entity confidence
  4. Summary generator — produces human sentences

Every operation is read-only. No writes. No RPC. No new workers.
Target: <75ms warm (DB reads + in-memory rules).
"""

from __future__ import annotations

import time
from typing import Any

from src.intelligence.aggregator import aggregate
from src.intelligence.confidence import derive_confidence
from src.intelligence.models import (
    ENTITY_TYPE_UNKNOWN,
    KNOWN_ENTITY_TYPES,
    EntitySummary,
    OperationObservation,
    TimelineEvent,
)
from src.intelligence.summary import generate_summary


def _resolve_entity_type(
    aggregator_type: str,
    knowledge: list[dict[str, Any]],
) -> str:
    """
    Merge aggregator's best guess with knowledge signals.

    Knowledge can refine but not contradict a confirmed operation identity.
    If aggregator found UNKNOWN, check knowledge for CEX/platform hints.
    """
    if aggregator_type != ENTITY_TYPE_UNKNOWN:
        return aggregator_type

    kn_types = {item.get("type") for item in knowledge}
    if "KNOWN_CEX" in kn_types:
        return "wallet"   # CEX hot wallet — it's a wallet, not an operator
    if "KNOWN_PLATFORM" in kn_types:
        return "wallet"
    if "JITO" in kn_types:
        return "wallet"

    return ENTITY_TYPE_UNKNOWN


def _merge_timestamps(
    observations: list[OperationObservation],
    knowledge:    list[dict[str, Any]],
    timeline:     list[TimelineEvent],
) -> tuple[int | None, int | None]:
    """Return (first_seen, last_seen) across all sources."""
    candidates: list[int] = []

    for obs in observations:
        if obs.first_seen:
            candidates.append(obs.first_seen)
        if obs.last_seen:
            candidates.append(obs.last_seen)

    for item in knowledge:
        if item.get("first_seen"):
            candidates.append(item["first_seen"])
        if item.get("last_seen"):
            candidates.append(item["last_seen"])

    for ev in timeline:
        candidates.append(ev.ts)

    if not candidates:
        return None, None
    return min(candidates), max(candidates)


def resolve(entity_id: str) -> EntitySummary:
    """
    Produce a complete EntitySummary for entity_id.

    Always returns a valid EntitySummary — never raises.
    On complete unknown: returns a minimal summary with UNKNOWN confidence.
    """
    entity_id = entity_id.strip()
    generated_at = int(time.time())

    # ── Step 1: aggregate operation observations ───────────────────────────────
    observations, timeline_unsorted, aggregator_type = aggregate(entity_id)

    # ── Step 2: knowledge enrichment ───────────────────────────────────────────
    knowledge: list[dict[str, Any]] = []
    try:
        from src.knowledge.engine import enrich
        knowledge = [item.to_dict() for item in enrich(entity_id)]

        # Add timeline events for each knowledge item (use generated_at as ts
        # because knowledge rules are derived, not observed at a specific moment;
        # we mark them with generated_at so they sort after historical events).
        for item in knowledge:
            timeline_unsorted.append(TimelineEvent(
                ts          = generated_at,
                event_type  = "KNOWLEDGE_DERIVED",
                description = (
                    f"Knowledge rule derived: {item['type']} "
                    f"({item['category']}) — {item['value']}"
                ),
                source      = "KNOWLEDGE_LAYER",
                provenance  = item.get("provenance", "knowledge/rules"),
                metadata    = {
                    "category":   item["category"],
                    "type":       item["type"],
                    "confidence": item["confidence"],
                },
            ))
    except Exception as exc:
        print(f"[INTELLIGENCE] knowledge enrichment error for {entity_id}: {exc}")

    # ── Step 3: sort timeline ascending ───────────────────────────────────────
    timeline = sorted(timeline_unsorted, key=lambda e: e.ts)

    # ── Step 4: entity type ────────────────────────────────────────────────────
    entity_type = _resolve_entity_type(aggregator_type, knowledge)

    # ── Step 5: confidence ────────────────────────────────────────────────────
    confidence_breakdown = derive_confidence(observations, knowledge)

    # ── Step 6: timestamps ────────────────────────────────────────────────────
    first_seen, last_seen = _merge_timestamps(observations, knowledge, timeline)

    # ── Step 7: summary sentences ─────────────────────────────────────────────
    summary_sentences = generate_summary(
        entity_type = entity_type,
        operations  = observations,
        knowledge   = knowledge,
        timeline    = timeline,
        confidence  = confidence_breakdown.level,
    )

    return EntitySummary(
        entity_id         = entity_id,
        entity_type       = entity_type,
        operations        = observations,
        knowledge         = knowledge,
        timeline          = timeline,
        summary_sentences = summary_sentences,
        confidence        = confidence_breakdown,
        first_seen        = first_seen,
        last_seen         = last_seen,
        generated_at      = generated_at,
    )
