"""
Cross-Operation Intelligence — test suite.

Uses live DB entities verified to have specific presence profiles.
Read-only throughout — no writes, no RPC, no schema changes.

Test fixtures (verified against wt_ops_v2.db):
  WT_ONLY     = 2NjUHDwRgGuPuV1ySzxKSWiwshzxHmREgEDm1TpfMUSp  (treasury, not in LO/BSO)
  LO_ONLY     = 11111111111111111111111111111111               (LO funder, not in WT/BSO)
  BSO_ONLY    = 128PawfK2xefvVh3kZRua44E9hruoPkyHtyt9Tpy6R7e  (swarm participant only)
  WT_BSO      = 2q5AhMTgi4TLqXM7ttPJ3Fbjdke1sSc6En83pE2NmfzW  (WT treasury + BSO treasury)
  WT_BSO_SP   = 12vFPPgP1NbkYxKVeSA6c2fY499TLpExnRapFfz5AEEy  (WT subprov + BSO subprov)
  ALL_THREE   = 69SNcRC8NqjHBSXEcugCN5oFKRQoKmddmWzZYc3tqtxk  (WT treasury + LO funder + BSO treasury)
  UNKNOWN     = ZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZnotreal  (not in any DB)
"""
from __future__ import annotations

import time

import pytest

WT_ONLY   = "2NjUHDwRgGuPuV1ySzxKSWiwshzxHmREgEDm1TpfMUSp"
LO_ONLY   = "11111111111111111111111111111111"
BSO_ONLY  = "128PawfK2xefvVh3kZRua44E9hruoPkyHtyt9Tpy6R7e"
WT_BSO    = "2q5AhMTgi4TLqXM7ttPJ3Fbjdke1sSc6En83pE2NmfzW"
WT_BSO_SP = "12vFPPgP1NbkYxKVeSA6c2fY499TLpExnRapFfz5AEEy"
ALL_THREE = "69SNcRC8NqjHBSXEcugCN5oFKRQoKmddmWzZYc3tqtxk"
UNKNOWN   = "ZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZnotreal"


# ── 1. Model ──────────────────────────────────────────────────────────────────

class TestModel:
    def test_all_relationship_types_defined(self) -> None:
        from src.cross_operation.models import ALL_RELATIONSHIP_TYPES
        assert len(ALL_RELATIONSHIP_TYPES) >= 5

    def test_relationship_is_frozen(self) -> None:
        from src.cross_operation.models import Relationship
        r = Relationship(
            relationship_id="abc", entity_a="A", entity_b="B",
            relationship_type="SHARED_FUNDER", confidence="CERTAIN",
            supporting_operations=("watchtower",), supporting_evidence=("test",),
            first_seen=1, last_seen=2, provenance="test",
        )
        with pytest.raises((AttributeError, TypeError)):
            r.relationship_id = "changed"  # type: ignore[misc]

    def test_entity_overlap_fields(self) -> None:
        from src.cross_operation.models import EntityOverlap
        ov = EntityOverlap(
            entity_id="X", observed_by=["watchtower"], operation_count=1,
            relationships=[], unified_timeline=[], generated_at=1,
        )
        assert ov.entity_id == "X"
        assert ov.operation_count == 1

    def test_global_stats_fields(self) -> None:
        from src.cross_operation.models import GlobalStats
        s = GlobalStats(
            total_entities=10, entities_in_1_operation=7,
            entities_in_2_operations=2, entities_in_3_operations=1,
            total_relationships=3, avg_relationships_per_entity=0.3,
            relationship_type_distribution={}, operation_pair_overlaps={},
            generated_at=1,
        )
        assert s.total_entities == 10


# ── 2. Entity in single operation ─────────────────────────────────────────────

class TestSingleOperationEntity:
    def test_wt_only_observed_by_watchtower(self) -> None:
        from src.cross_operation.service import entity_relationships
        ov = entity_relationships(WT_ONLY)
        assert "watchtower" in ov.observed_by

    def test_wt_only_operation_count_is_1(self) -> None:
        from src.cross_operation.service import entity_relationships
        ov = entity_relationships(WT_ONLY)
        assert ov.operation_count == 1

    def test_wt_only_no_multi_op_relationship(self) -> None:
        from src.cross_operation.models import OBSERVED_BY_MULTIPLE_OPS
        from src.cross_operation.service import entity_relationships
        ov = entity_relationships(WT_ONLY)
        types = [r.relationship_type for r in ov.relationships]
        assert OBSERVED_BY_MULTIPLE_OPS not in types

    def test_lo_only_observed_by_launcher_observatory(self) -> None:
        from src.cross_operation.service import entity_relationships
        ov = entity_relationships(LO_ONLY)
        assert "launcher-observatory" in ov.observed_by

    def test_bso_only_observed_by_buy_swarm(self) -> None:
        from src.cross_operation.service import entity_relationships
        ov = entity_relationships(BSO_ONLY)
        assert "buy-swarm-observatory" in ov.observed_by
        assert ov.operation_count == 1


# ── 3. Unknown entity ─────────────────────────────────────────────────────────

class TestUnknownEntity:
    def test_unknown_returns_overlap_object(self) -> None:
        from src.cross_operation.service import entity_relationships
        ov = entity_relationships(UNKNOWN)
        assert ov.entity_id == UNKNOWN

    def test_unknown_has_no_operations(self) -> None:
        from src.cross_operation.service import entity_relationships
        ov = entity_relationships(UNKNOWN)
        assert ov.operation_count == 0
        assert ov.observed_by == []

    def test_unknown_has_no_relationships(self) -> None:
        from src.cross_operation.service import entity_relationships
        ov = entity_relationships(UNKNOWN)
        assert ov.relationships == []

    def test_unknown_has_empty_timeline(self) -> None:
        from src.cross_operation.service import entity_relationships
        ov = entity_relationships(UNKNOWN)
        assert ov.unified_timeline == []

    def test_unknown_has_generated_at(self) -> None:
        from src.cross_operation.service import entity_relationships
        ov = entity_relationships(UNKNOWN)
        assert ov.generated_at > 0


# ── 4. Cross-operation entity (WT + BSO) ─────────────────────────────────────

class TestWTBSOEntity:
    def test_wt_bso_observed_by_both(self) -> None:
        from src.cross_operation.service import entity_relationships
        ov = entity_relationships(WT_BSO)
        assert "watchtower" in ov.observed_by
        assert "buy-swarm-observatory" in ov.observed_by

    def test_wt_bso_operation_count_is_2(self) -> None:
        from src.cross_operation.service import entity_relationships
        ov = entity_relationships(WT_BSO)
        assert ov.operation_count >= 2

    def test_wt_bso_has_multi_op_relationship(self) -> None:
        from src.cross_operation.models import OBSERVED_BY_MULTIPLE_OPS
        from src.cross_operation.service import entity_relationships
        ov = entity_relationships(WT_BSO)
        types = [r.relationship_type for r in ov.relationships]
        assert OBSERVED_BY_MULTIPLE_OPS in types

    def test_wt_bso_multi_op_confidence_certain(self) -> None:
        from src.cross_operation.models import CONFIDENCE_CERTAIN, OBSERVED_BY_MULTIPLE_OPS
        from src.cross_operation.service import entity_relationships
        ov = entity_relationships(WT_BSO)
        rel = next(r for r in ov.relationships if r.relationship_type == OBSERVED_BY_MULTIPLE_OPS)
        assert rel.confidence == CONFIDENCE_CERTAIN

    def test_wt_bso_relationship_has_evidence(self) -> None:
        from src.cross_operation.models import OBSERVED_BY_MULTIPLE_OPS
        from src.cross_operation.service import entity_relationships
        ov = entity_relationships(WT_BSO)
        rel = next(r for r in ov.relationships if r.relationship_type == OBSERVED_BY_MULTIPLE_OPS)
        assert len(rel.supporting_evidence) >= 2

    def test_wt_bso_shared_operator_relationship(self) -> None:
        from src.cross_operation.models import SHARED_OPERATOR
        from src.cross_operation.service import entity_relationships
        ov = entity_relationships(WT_BSO)
        types = [r.relationship_type for r in ov.relationships]
        assert SHARED_OPERATOR in types


# ── 5. WT subprov + BSO subprov (SHARED_SWARM) ───────────────────────────────

class TestWTBSOSubprov:
    def test_shared_swarm_relationship_present(self) -> None:
        from src.cross_operation.models import SHARED_SWARM
        from src.cross_operation.service import entity_relationships
        ov = entity_relationships(WT_BSO_SP)
        types = [r.relationship_type for r in ov.relationships]
        assert SHARED_SWARM in types

    def test_shared_swarm_supporting_operations(self) -> None:
        from src.cross_operation.models import SHARED_SWARM
        from src.cross_operation.service import entity_relationships
        ov = entity_relationships(WT_BSO_SP)
        rel = next(r for r in ov.relationships if r.relationship_type == SHARED_SWARM)
        assert "watchtower" in rel.supporting_operations
        assert "buy-swarm-observatory" in rel.supporting_operations

    def test_shared_swarm_has_provenance(self) -> None:
        from src.cross_operation.models import SHARED_SWARM
        from src.cross_operation.service import entity_relationships
        ov = entity_relationships(WT_BSO_SP)
        rel = next(r for r in ov.relationships if r.relationship_type == SHARED_SWARM)
        assert rel.provenance


# ── 6. Entity in all three operations ─────────────────────────────────────────

class TestAllThreeOperations:
    def test_all_three_observed_by_all(self) -> None:
        from src.cross_operation.service import entity_relationships
        ov = entity_relationships(ALL_THREE)
        assert "watchtower" in ov.observed_by
        assert "launcher-observatory" in ov.observed_by
        assert "buy-swarm-observatory" in ov.observed_by

    def test_all_three_operation_count(self) -> None:
        from src.cross_operation.service import entity_relationships
        ov = entity_relationships(ALL_THREE)
        assert ov.operation_count == 3

    def test_all_three_has_multi_op_relationship(self) -> None:
        from src.cross_operation.models import OBSERVED_BY_MULTIPLE_OPS
        from src.cross_operation.service import entity_relationships
        ov = entity_relationships(ALL_THREE)
        types = [r.relationship_type for r in ov.relationships]
        assert OBSERVED_BY_MULTIPLE_OPS in types

    def test_all_three_has_multiple_relationships(self) -> None:
        from src.cross_operation.service import entity_relationships
        ov = entity_relationships(ALL_THREE)
        assert len(ov.relationships) >= 2

    def test_all_three_supporting_ops_covers_all(self) -> None:
        from src.cross_operation.models import OBSERVED_BY_MULTIPLE_OPS
        from src.cross_operation.service import entity_relationships
        ov = entity_relationships(ALL_THREE)
        rel = next(r for r in ov.relationships if r.relationship_type == OBSERVED_BY_MULTIPLE_OPS)
        assert len(rel.supporting_operations) == 3


# ── 7. Timeline correlation ───────────────────────────────────────────────────

class TestTimeline:
    def test_single_op_timeline_has_events(self) -> None:
        from src.cross_operation.service import entity_relationships
        ov = entity_relationships(WT_ONLY)
        assert len(ov.unified_timeline) >= 1

    def test_multi_op_timeline_has_multiple_events(self) -> None:
        from src.cross_operation.service import entity_relationships
        ov = entity_relationships(ALL_THREE)
        assert len(ov.unified_timeline) >= 2

    def test_timeline_sorted_ascending(self) -> None:
        from src.cross_operation.service import entity_relationships
        ov = entity_relationships(ALL_THREE)
        tss = [e["ts"] for e in ov.unified_timeline if e.get("ts")]
        assert tss == sorted(tss)

    def test_timeline_events_have_required_fields(self) -> None:
        from src.cross_operation.service import entity_relationships
        ov = entity_relationships(ALL_THREE)
        for ev in ov.unified_timeline:
            assert "ts" in ev
            assert "event_type" in ev
            assert "description" in ev
            assert "source" in ev
            assert "provenance" in ev

    def test_timeline_no_inferred_events(self) -> None:
        from src.cross_operation.service import entity_relationships
        ov = entity_relationships(ALL_THREE)
        # All event sources must be a known operation
        valid_sources = {"WATCHTOWER", "LAUNCHER_OBSERVATORY", "BUY_SWARM_OBSERVATORY"}
        for ev in ov.unified_timeline:
            assert ev["source"] in valid_sources, f"Unknown source: {ev['source']}"

    def test_unknown_entity_empty_timeline(self) -> None:
        from src.cross_operation.service import entity_relationships
        ov = entity_relationships(UNKNOWN)
        assert ov.unified_timeline == []


# ── 8. No duplicate relationships ─────────────────────────────────────────────

class TestNoDuplicates:
    def test_no_duplicate_relationship_ids(self) -> None:
        from src.cross_operation.service import entity_relationships
        ov = entity_relationships(ALL_THREE)
        ids = [r.relationship_id for r in ov.relationships]
        assert len(ids) == len(set(ids))

    def test_no_duplicate_relationship_types(self) -> None:
        from src.cross_operation.service import entity_relationships
        ov = entity_relationships(ALL_THREE)
        types = [r.relationship_type for r in ov.relationships]
        assert len(types) == len(set(types))


# ── 9. Confidence ─────────────────────────────────────────────────────────────

class TestConfidence:
    def test_multi_op_always_certain(self) -> None:
        from src.cross_operation.models import CONFIDENCE_CERTAIN, OBSERVED_BY_MULTIPLE_OPS
        from src.cross_operation.service import entity_relationships
        for addr in (WT_BSO, ALL_THREE):
            ov = entity_relationships(addr)
            rel = next(
                (r for r in ov.relationships if r.relationship_type == OBSERVED_BY_MULTIPLE_OPS),
                None,
            )
            if rel:
                assert rel.confidence == CONFIDENCE_CERTAIN

    def test_every_relationship_has_confidence(self) -> None:
        from src.cross_operation.models import CONFIDENCE_CERTAIN, CONFIDENCE_HIGH, CONFIDENCE_LOW, CONFIDENCE_MEDIUM
        from src.cross_operation.service import entity_relationships
        valid = {CONFIDENCE_CERTAIN, CONFIDENCE_HIGH, CONFIDENCE_MEDIUM, CONFIDENCE_LOW}
        ov = entity_relationships(ALL_THREE)
        for r in ov.relationships:
            assert r.confidence in valid

    def test_every_relationship_has_supporting_evidence(self) -> None:
        from src.cross_operation.service import entity_relationships
        ov = entity_relationships(ALL_THREE)
        for r in ov.relationships:
            assert len(r.supporting_evidence) >= 1

    def test_every_relationship_has_provenance(self) -> None:
        from src.cross_operation.service import entity_relationships
        ov = entity_relationships(ALL_THREE)
        for r in ov.relationships:
            assert r.provenance


# ── 10. Global stats ──────────────────────────────────────────────────────────

class TestGlobalStats:
    def test_global_stats_returns(self) -> None:
        from src.cross_operation.service import global_stats
        s = global_stats()
        assert s.total_entities > 0

    def test_entity_counts_sum_to_total(self) -> None:
        from src.cross_operation.service import global_stats
        s = global_stats()
        assert (
            s.entities_in_1_operation + s.entities_in_2_operations + s.entities_in_3_operations
            == s.total_entities
        )

    def test_operation_pair_overlaps_present(self) -> None:
        from src.cross_operation.service import global_stats
        s = global_stats()
        assert "watchtower+launcher-observatory" in s.operation_pair_overlaps
        assert "watchtower+buy-swarm-observatory" in s.operation_pair_overlaps
        assert "launcher-observatory+buy-swarm-observatory" in s.operation_pair_overlaps
        assert "all-three" in s.operation_pair_overlaps

    def test_all_three_overlap_positive(self) -> None:
        from src.cross_operation.service import global_stats
        s = global_stats()
        assert s.operation_pair_overlaps["all-three"] >= 1

    def test_relationship_type_distribution_present(self) -> None:
        from src.cross_operation.service import global_stats
        s = global_stats()
        assert len(s.relationship_type_distribution) >= 3

    def test_entities_in_multiple_ops_positive(self) -> None:
        from src.cross_operation.service import global_stats
        s = global_stats()
        assert s.entities_in_2_operations + s.entities_in_3_operations >= 1


# ── 11. Read-only guarantee ───────────────────────────────────────────────────

class TestReadOnly:
    def test_entity_relationships_does_not_write(self) -> None:
        import os
        from src.cross_operation.service import entity_relationships
        db = "database/wt_ops_v2.db"
        mtime_before = os.path.getmtime(db)
        entity_relationships(ALL_THREE)
        mtime_after = os.path.getmtime(db)
        assert mtime_before == mtime_after, "DB was modified by entity_relationships()"

    def test_global_stats_does_not_write(self) -> None:
        import os
        from src.cross_operation.service import global_stats
        db = "database/wt_ops_v2.db"
        mtime_before = os.path.getmtime(db)
        global_stats()
        mtime_after = os.path.getmtime(db)
        assert mtime_before == mtime_after, "DB was modified by global_stats()"


# ── 12. API routes ────────────────────────────────────────────────────────────

class TestAPIRoutes:
    @pytest.fixture()
    def client(self):
        import sys
        sys.path.insert(0, ".")
        from flask import Flask
        from src.cross_operation.routes import register_cross_operation_routes
        app = Flask(__name__)
        app.config["TESTING"] = True
        register_cross_operation_routes(app)
        return app.test_client()

    def test_relationships_endpoint_returns_200(self, client) -> None:
        r = client.get(f"/api/intelligence/entity/{ALL_THREE}/relationships")
        assert r.status_code == 200

    def test_relationships_response_has_schema_fields(self, client) -> None:
        import json
        r = client.get(f"/api/intelligence/entity/{ALL_THREE}/relationships")
        data = json.loads(r.data)
        assert "entity_id" in data
        assert "observed_by" in data
        assert "operation_count" in data
        assert "relationships" in data
        assert "unified_timeline" in data
        assert "generated_at" in data

    def test_relationships_unknown_entity_returns_200(self, client) -> None:
        r = client.get(f"/api/intelligence/entity/{UNKNOWN}/relationships")
        assert r.status_code == 200

    def test_relationships_unknown_entity_empty(self, client) -> None:
        import json
        r = client.get(f"/api/intelligence/entity/{UNKNOWN}/relationships")
        data = json.loads(r.data)
        assert data["operation_count"] == 0
        assert data["relationships"] == []

    def test_stats_endpoint_returns_200(self, client) -> None:
        r = client.get("/api/intelligence/cross-operation/stats")
        assert r.status_code == 200

    def test_stats_response_has_schema_fields(self, client) -> None:
        import json
        r = client.get("/api/intelligence/cross-operation/stats")
        data = json.loads(r.data)
        assert "total_entities" in data
        assert "entities_in_1_operation" in data
        assert "entities_in_2_operations" in data
        assert "entities_in_3_operations" in data
        assert "operation_pair_overlaps" in data

    def test_relationships_each_entry_has_required_fields(self, client) -> None:
        import json
        r = client.get(f"/api/intelligence/entity/{ALL_THREE}/relationships")
        data = json.loads(r.data)
        for rel in data["relationships"]:
            assert "relationship_id" in rel
            assert "relationship_type" in rel
            assert "confidence" in rel
            assert "supporting_operations" in rel
            assert "supporting_evidence" in rel
            assert "provenance" in rel


# ── 13. Performance ───────────────────────────────────────────────────────────

class TestPerformance:
    def test_warm_lookup_under_250ms(self) -> None:
        from src.cross_operation.service import entity_relationships
        entity_relationships(ALL_THREE)  # warm
        start = time.perf_counter()
        entity_relationships(ALL_THREE)
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert elapsed_ms < 250, f"Warm lookup took {elapsed_ms:.1f}ms (isolated baseline ~16ms)"

    def test_unknown_entity_fast(self) -> None:
        from src.cross_operation.service import entity_relationships
        entity_relationships(UNKNOWN)  # warm
        start = time.perf_counter()
        entity_relationships(UNKNOWN)
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert elapsed_ms < 250, f"Unknown entity took {elapsed_ms:.1f}ms"

    def test_global_stats_under_1000ms(self) -> None:
        from src.cross_operation.service import global_stats
        global_stats()  # warm
        start = time.perf_counter()
        global_stats()
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert elapsed_ms < 1000, f"global_stats took {elapsed_ms:.1f}ms (isolated baseline ~135ms)"
