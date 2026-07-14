"""
Tests for the Entity Intelligence service (Sprint I1).

Covers (Task 13):
  - Aggregation correctness
  - Knowledge reuse (no duplicate computation)
  - Timeline ordering
  - Summary generation
  - Confidence derivation
  - Unknown entity
  - Performance (<75ms warm)
  - No DB writes
  - No Flask at import level
  - Entity-type-agnostic model
  - Operation observations have provenance
  - All timeline events have timestamps
  - Demonstration scenarios (Task 12): Launcher-only, WATCHTOWER-only,
    both, unknown, knowledge-only
"""

from __future__ import annotations

import importlib
import sys
import time
from typing import Any

import pytest

# ── Known entity IDs from the live OPS DB ────────────────────────────────────
# Verified 2026-07-12 against database/wt_ops_v2.db

TREASURY_ADDR   = "3sStXWrDYHSnHhY1cbjRNR23pF24W9jK6T8LnaP85TMm"
LO_FUNDER_ADDR  = "4AV2Qzp3N4c9RfzyEbNZs2wqWfW4EwKnnxFAZCndvfGh"  # 18 launches
SUBPROV_ADDR    = "6xtSbexfUG5zb9Jh7W8VoKxAm24PFh3avWgVSjgKCKEr"
BOTH_ADDR       = "69SNcRC8NqjHBSXEcugCN5oFKRQoKmddmWzZYc3tqtxk"  # treasury + launches
JITO_ADDR       = "96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5"  # Jito tip #0
UNKNOWN_ADDR    = "XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"


# ── 1. No Flask at module import level ────────────────────────────────────────

class TestNoFlaskAtImport:
    def _evict_flask(self) -> None:
        for k in list(sys.modules):
            if k.startswith("flask"):
                del sys.modules[k]

    def test_models_no_flask(self) -> None:
        self._evict_flask()
        import src.intelligence.models  # noqa: F401
        assert "flask" not in sys.modules

    def test_aggregator_no_flask(self) -> None:
        self._evict_flask()
        import src.intelligence.aggregator  # noqa: F401
        assert "flask" not in sys.modules

    def test_confidence_no_flask(self) -> None:
        self._evict_flask()
        import src.intelligence.confidence  # noqa: F401
        assert "flask" not in sys.modules

    def test_summary_no_flask(self) -> None:
        self._evict_flask()
        import src.intelligence.summary  # noqa: F401
        assert "flask" not in sys.modules

    def test_service_no_flask(self) -> None:
        self._evict_flask()
        import src.intelligence.service  # noqa: F401
        assert "flask" not in sys.modules

    def test_routes_no_flask(self) -> None:
        self._evict_flask()
        import src.intelligence.routes  # noqa: F401
        assert "flask" not in sys.modules


# ── 2. No DB writes in source ─────────────────────────────────────────────────

class TestNoDbWrites:
    def _source(self, path: str) -> str:
        import inspect
        mod = importlib.import_module(path)
        return inspect.getsource(mod)

    def test_aggregator_no_writes(self) -> None:
        src = self._source("src.intelligence.aggregator")
        for kw in ("INSERT", "UPDATE", "DELETE"):
            assert kw not in src

    def test_service_no_writes(self) -> None:
        src = self._source("src.intelligence.service")
        for kw in ("INSERT", "UPDATE", "DELETE"):
            assert kw not in src


# ── 3. Entity model ───────────────────────────────────────────────────────────

class TestEntityModel:
    def test_entity_types_defined(self) -> None:
        from src.intelligence.models import KNOWN_ENTITY_TYPES
        assert "wallet" in KNOWN_ENTITY_TYPES
        assert "operator" in KNOWN_ENTITY_TYPES
        assert "treasury" in KNOWN_ENTITY_TYPES
        assert "creator" in KNOWN_ENTITY_TYPES
        assert "sub_provisioner" in KNOWN_ENTITY_TYPES
        assert "unknown" in KNOWN_ENTITY_TYPES

    def test_timeline_event_to_dict(self) -> None:
        from src.intelligence.models import TimelineEvent
        ev = TimelineEvent(
            ts=1000, event_type="TEST", description="test",
            source="TEST", provenance="test.table",
        )
        d = ev.to_dict()
        assert d["ts"] == 1000
        assert d["event_type"] == "TEST"
        assert "provenance" in d

    def test_confidence_breakdown_to_dict(self) -> None:
        from src.intelligence.models import ConfidenceBreakdown
        cb = ConfidenceBreakdown(
            level="HIGH",
            contributing_facts=("fact A", "fact B"),
            ceiling="HIGH",
            floor="CERTAIN",
        )
        d = cb.to_dict()
        assert d["level"] == "HIGH"
        assert isinstance(d["contributing_facts"], list)

    def test_operation_observation_to_dict(self) -> None:
        from src.intelligence.models import OperationObservation
        obs = OperationObservation(
            operation_id="watchtower", display_name="WATCHTOWER",
            role="TREASURY", facts={"confidence": "CERTAIN"},
            first_seen=1000, last_seen=2000,
            provenance="wt_confirmed_treasuries",
        )
        d = obs.to_dict()
        assert d["role"] == "TREASURY"
        assert "provenance" in d

    def test_entity_summary_to_dict_shape(self) -> None:
        from src.intelligence.service import resolve
        summary = resolve(UNKNOWN_ADDR)
        d = summary.to_dict()
        required_keys = {
            "entity_id", "entity_type", "operations", "knowledge",
            "timeline", "summary_sentences", "confidence",
            "first_seen", "last_seen", "generated_at",
        }
        assert required_keys <= set(d.keys())


# ── 4. Unknown entity ─────────────────────────────────────────────────────────

class TestUnknownEntity:
    def test_unknown_returns_summary(self) -> None:
        from src.intelligence.service import resolve
        summary = resolve(UNKNOWN_ADDR)
        assert summary.entity_id == UNKNOWN_ADDR
        assert summary.entity_type == "unknown"
        assert summary.operations == []
        assert summary.first_seen is None
        assert summary.last_seen is None

    def test_unknown_confidence_is_unknown(self) -> None:
        from src.intelligence.service import resolve
        summary = resolve(UNKNOWN_ADDR)
        assert summary.confidence.level == "UNKNOWN"

    def test_unknown_summary_sentence(self) -> None:
        from src.intelligence.service import resolve
        summary = resolve(UNKNOWN_ADDR)
        joined = " ".join(summary.summary_sentences)
        assert "Unknown entity" in joined or "unknown" in joined.lower()

    def test_empty_entity_id_returns_summary(self) -> None:
        from src.intelligence.service import resolve
        summary = resolve("")
        assert summary.entity_type == "unknown"


# ── 5. WATCHTOWER-only entity (treasury) ──────────────────────────────────────

class TestWatchtowerEntity:
    def test_treasury_resolved(self) -> None:
        from src.intelligence.service import resolve
        summary = resolve(TREASURY_ADDR)
        assert summary.entity_type == "treasury"

    def test_treasury_has_watchtower_observation(self) -> None:
        from src.intelligence.service import resolve
        summary = resolve(TREASURY_ADDR)
        op_ids = {o.operation_id for o in summary.operations}
        assert "watchtower" in op_ids

    def test_treasury_role_in_observation(self) -> None:
        from src.intelligence.service import resolve
        summary = resolve(TREASURY_ADDR)
        roles = {o.role for o in summary.operations if o.operation_id == "watchtower"}
        assert "TREASURY" in roles

    def test_treasury_observation_has_provenance(self) -> None:
        from src.intelligence.service import resolve
        summary = resolve(TREASURY_ADDR)
        for obs in summary.operations:
            assert obs.provenance, f"Observation {obs.operation_id}/{obs.role} missing provenance"

    def test_treasury_timeline_has_confirmed_event(self) -> None:
        from src.intelligence.service import resolve
        summary = resolve(TREASURY_ADDR)
        event_types = {e.event_type for e in summary.timeline}
        assert "TREASURY_CONFIRMED" in event_types

    def test_treasury_summary_mentions_watchtower(self) -> None:
        from src.intelligence.service import resolve
        summary = resolve(TREASURY_ADDR)
        text = " ".join(summary.summary_sentences)
        assert "WATCHTOWER" in text or "treasury" in text.lower()

    def test_treasury_confidence_not_unknown(self) -> None:
        from src.intelligence.service import resolve
        summary = resolve(TREASURY_ADDR)
        assert summary.confidence.level != "UNKNOWN"


# ── 6. Launcher Observatory-only entity ───────────────────────────────────────

class TestLauncherObservatoryEntity:
    def test_lo_funder_resolved_as_operator(self) -> None:
        from src.intelligence.service import resolve
        summary = resolve(LO_FUNDER_ADDR)
        assert summary.entity_type == "operator"

    def test_lo_funder_has_lo_observation(self) -> None:
        from src.intelligence.service import resolve
        summary = resolve(LO_FUNDER_ADDR)
        op_ids = {o.operation_id for o in summary.operations}
        assert "launcher-observatory" in op_ids

    def test_lo_funder_role_is_persistent(self) -> None:
        from src.intelligence.service import resolve
        summary = resolve(LO_FUNDER_ADDR)
        roles = {o.role for o in summary.operations if o.operation_id == "launcher-observatory"}
        assert "PERSISTENT_FUNDER" in roles

    def test_lo_funder_launch_count_correct(self) -> None:
        from src.intelligence.service import resolve
        summary = resolve(LO_FUNDER_ADDR)
        lo_obs = [o for o in summary.operations if o.operation_id == "launcher-observatory"]
        assert lo_obs
        assert lo_obs[0].facts["launch_count"] == 18

    def test_lo_funder_no_watchtower(self) -> None:
        from src.intelligence.service import resolve
        summary = resolve(LO_FUNDER_ADDR)
        op_ids = {o.operation_id for o in summary.operations}
        assert "watchtower" not in op_ids

    def test_lo_funder_summary_no_wt_attribution(self) -> None:
        from src.intelligence.service import resolve
        summary = resolve(LO_FUNDER_ADDR)
        text = " ".join(summary.summary_sentences)
        assert "No WATCHTOWER attribution" in text

    def test_lo_funder_timeline_has_launch_events(self) -> None:
        from src.intelligence.service import resolve
        summary = resolve(LO_FUNDER_ADDR)
        event_types = [e.event_type for e in summary.timeline]
        assert "LAUNCH_OBSERVED" in event_types

    def test_lo_funder_timeline_has_persistent_event(self) -> None:
        from src.intelligence.service import resolve
        summary = resolve(LO_FUNDER_ADDR)
        event_types = {e.event_type for e in summary.timeline}
        assert "OPERATOR_BECAME_PERSISTENT" in event_types


# ── 7. Entity with both operations ───────────────────────────────────────────

class TestBothOperationsEntity:
    def test_both_has_two_operations(self) -> None:
        from src.intelligence.service import resolve
        summary = resolve(BOTH_ADDR)
        op_ids = {o.operation_id for o in summary.operations}
        assert "watchtower" in op_ids
        assert "launcher-observatory" in op_ids

    def test_both_summary_mentions_both(self) -> None:
        from src.intelligence.service import resolve
        summary = resolve(BOTH_ADDR)
        text = " ".join(summary.summary_sentences)
        assert "both" in text.lower() or ("WATCHTOWER" in text and "Launcher" in text)

    def test_both_first_seen_is_earliest(self) -> None:
        from src.intelligence.service import resolve
        summary = resolve(BOTH_ADDR)
        if summary.first_seen and summary.timeline:
            # first_seen must equal the earliest timeline event ts
            earliest_event = min(e.ts for e in summary.timeline)
            assert summary.first_seen <= earliest_event


# ── 8. Timeline ordering ──────────────────────────────────────────────────────

class TestTimelineOrdering:
    def test_timeline_ascending(self) -> None:
        from src.intelligence.service import resolve
        for addr in (TREASURY_ADDR, LO_FUNDER_ADDR, BOTH_ADDR):
            summary = resolve(addr)
            ts_list = [e.ts for e in summary.timeline]
            assert ts_list == sorted(ts_list), f"Timeline not sorted for {addr}"

    def test_all_timeline_events_have_ts(self) -> None:
        from src.intelligence.service import resolve
        for addr in (TREASURY_ADDR, LO_FUNDER_ADDR, SUBPROV_ADDR):
            summary = resolve(addr)
            for ev in summary.timeline:
                assert ev.ts is not None, f"Event {ev.event_type} has no ts"
                assert isinstance(ev.ts, int)

    def test_all_timeline_events_have_provenance(self) -> None:
        from src.intelligence.service import resolve
        summary = resolve(LO_FUNDER_ADDR)
        for ev in summary.timeline:
            assert ev.provenance, f"Event {ev.event_type} missing provenance"

    def test_knowledge_derived_events_sort_last(self) -> None:
        """KNOWLEDGE_DERIVED events use generated_at; they should appear after historical events."""
        from src.intelligence.service import resolve
        summary = resolve(LO_FUNDER_ADDR)
        kd_events = [e for e in summary.timeline if e.event_type == "KNOWLEDGE_DERIVED"]
        lo_events = [e for e in summary.timeline if e.event_type == "LAUNCH_OBSERVED"]
        if kd_events and lo_events:
            assert min(e.ts for e in kd_events) >= max(e.ts for e in lo_events)


# ── 9. Summary generation ─────────────────────────────────────────────────────

class TestSummaryGeneration:
    def test_summary_is_list_of_strings(self) -> None:
        from src.intelligence.service import resolve
        summary = resolve(LO_FUNDER_ADDR)
        assert isinstance(summary.summary_sentences, list)
        for s in summary.summary_sentences:
            assert isinstance(s, str) and s

    def test_summary_mentions_launch_count(self) -> None:
        from src.intelligence.service import resolve
        summary = resolve(LO_FUNDER_ADDR)
        text = " ".join(summary.summary_sentences)
        assert "18" in text

    def test_summary_deterministic(self) -> None:
        """Same entity → same summary every time."""
        from src.intelligence.service import resolve
        s1 = resolve(LO_FUNDER_ADDR).summary_sentences
        s2 = resolve(LO_FUNDER_ADDR).summary_sentences
        assert s1 == s2

    def test_jito_address_summary(self) -> None:
        """Jito tip account should get a TOOLING knowledge sentence."""
        from src.intelligence.service import resolve
        summary = resolve(JITO_ADDR)
        text = " ".join(summary.summary_sentences)
        assert "Jito" in text


# ── 10. Confidence derivation ────────────────────────────────────────────────

class TestConfidenceDerivation:
    def test_confidence_has_contributing_facts(self) -> None:
        from src.intelligence.service import resolve
        summary = resolve(TREASURY_ADDR)
        assert len(summary.confidence.contributing_facts) > 0

    def test_confidence_has_ceiling_and_floor(self) -> None:
        from src.intelligence.service import resolve
        from src.intelligence.models import CONFIDENCE_LEVELS
        summary = resolve(TREASURY_ADDR)
        assert summary.confidence.ceiling in CONFIDENCE_LEVELS
        assert summary.confidence.floor in CONFIDENCE_LEVELS

    def test_confidence_level_valid(self) -> None:
        from src.intelligence.service import resolve
        from src.intelligence.models import CONFIDENCE_LEVELS
        for addr in (TREASURY_ADDR, LO_FUNDER_ADDR, UNKNOWN_ADDR):
            summary = resolve(addr)
            assert summary.confidence.level in CONFIDENCE_LEVELS

    def test_certain_signals_produce_non_unknown_confidence(self) -> None:
        """Treasury confirmed CERTAIN should yield at least MEDIUM (never UNKNOWN or LOW)."""
        from src.intelligence.service import resolve
        from src.intelligence.models import CONFIDENCE_RANK
        summary = resolve(TREASURY_ADDR)
        # CERTAIN treasury observation → confidence must be at least MEDIUM
        assert CONFIDENCE_RANK[summary.confidence.level] <= CONFIDENCE_RANK["MEDIUM"]

    def test_no_data_produces_unknown_confidence(self) -> None:
        from src.intelligence.confidence import derive_confidence
        breakdown = derive_confidence([], [])
        assert breakdown.level == "UNKNOWN"

    def test_confidence_only_from_knowledge(self) -> None:
        """Knowledge item alone should drive confidence."""
        from src.intelligence.confidence import derive_confidence
        items = [{"type": "JITO", "category": "TOOLING", "confidence": "CERTAIN"}]
        breakdown = derive_confidence([], items)
        assert breakdown.level in ("CERTAIN", "HIGH", "MEDIUM", "LOW")
        assert breakdown.level != "UNKNOWN"


# ── 11. Knowledge reuse (no duplication) ────────────────────────────────────

class TestKnowledgeReuse:
    def test_entity_knowledge_comes_from_knowledge_layer(self) -> None:
        """Knowledge items in EntitySummary should match direct engine.enrich() output."""
        from src.intelligence.service import resolve
        from src.knowledge.engine import enrich

        summary = resolve(JITO_ADDR)
        direct_items = enrich(JITO_ADDR)

        # Same entity_id in both
        summary_types = {item["type"] for item in summary.knowledge}
        direct_types  = {item.type for item in direct_items}
        assert summary_types == direct_types

    def test_knowledge_items_have_provenance(self) -> None:
        from src.intelligence.service import resolve
        summary = resolve(JITO_ADDR)
        for item in summary.knowledge:
            assert item.get("provenance"), f"Knowledge item {item.get('type')} missing provenance"


# ── 12. Performance ───────────────────────────────────────────────────────────

class TestPerformance:
    def test_warm_request_under_75ms(self) -> None:
        from src.intelligence.service import resolve

        # warm run (loads caches)
        resolve(LO_FUNDER_ADDR)

        start = time.perf_counter()
        resolve(LO_FUNDER_ADDR)
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert elapsed_ms < 75, f"Warm resolve took {elapsed_ms:.1f}ms > 75ms"

    def test_unknown_entity_fast(self) -> None:
        from src.intelligence.service import resolve

        resolve(UNKNOWN_ADDR)  # warm
        start = time.perf_counter()
        resolve(UNKNOWN_ADDR)
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert elapsed_ms < 75, f"Unknown entity resolve took {elapsed_ms:.1f}ms"


# ── 13. Knowledge-only entity (Jito tip account) ─────────────────────────────

class TestKnowledgeOnlyEntity:
    def test_jito_has_knowledge_no_operations(self) -> None:
        from src.intelligence.service import resolve
        summary = resolve(JITO_ADDR)
        assert len(summary.knowledge) > 0
        assert summary.operations == []

    def test_jito_entity_type(self) -> None:
        from src.intelligence.service import resolve
        summary = resolve(JITO_ADDR)
        # Jito is a wallet (known address); type resolved from knowledge
        assert summary.entity_type in ("wallet", "unknown")

    def test_jito_confidence_driven_by_knowledge(self) -> None:
        from src.intelligence.service import resolve
        summary = resolve(JITO_ADDR)
        # Jito addresses are CERTAIN confidence in the YAML
        assert summary.confidence.level in ("CERTAIN", "HIGH", "MEDIUM")
        assert summary.confidence.level != "UNKNOWN"

    def test_jito_timeline_has_knowledge_derived(self) -> None:
        from src.intelligence.service import resolve
        summary = resolve(JITO_ADDR)
        event_types = {e.event_type for e in summary.timeline}
        assert "KNOWLEDGE_DERIVED" in event_types
