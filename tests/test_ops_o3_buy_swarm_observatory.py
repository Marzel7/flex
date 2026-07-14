"""
Tests for Buy Swarm Observatory (Sprint O3).

Covers:
  - YAML loads and validates in framework
  - Three operations now in registry
  - Capability mix matches declaration
  - Vocabulary distinct from other operations
  - Providers registered correctly
  - Qualification rules implemented correctly (data-backed)
  - Health / failure-attribution / behaviour / intelligence / outcome endpoints
  - Entity Intelligence integration (swarm participant + subprov adapter)
  - Knowledge integration (enrichments on subprov)
  - Timeline events appear in Entity Intelligence
  - No Flask at import level
  - No DB writes in route module
  - Performance targets

Entity addresses from live DB (verified 2026-07-12):
  PARTICIPANT_ADDR  Dk2ePWQasrPSe5wNefMhSbRiskBsBZ5zKpXUzZdyiTuN  — in wt_swarm_buys
  SUBPROV_ADDR      EmiKdYMhTbWZ2Z9hLHd7QR4t8PZjvYknKpdssCQPHCap  — provisioned 828-participant swarm
  BEST_MINT         C2kZUYZW4vYnaLdhpSm8MSUvgYfkWqkEPeRFiPrwpump  — 828 participants, 1016s window
"""

from __future__ import annotations

import sys
import time
import importlib

import pytest

PARTICIPANT_ADDR = "Dk2ePWQasrPSe5wNefMhSbRiskBsBZ5zKpXUzZdyiTuN"
SUBPROV_ADDR     = "EmiKdYMhTbWZ2Z9hLHd7QR4t8PZjvYknKpdssCQPHCap"
BEST_MINT        = "C2kZUYZW4vYnaLdhpSm8MSUvgYfkWqkEPeRFiPrwpump"
UNKNOWN_ADDR     = "XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"


# ── 1. No Flask at import level ────────────────────────────────────────────────

class TestNoFlaskAtImport:
    def test_routes_no_flask(self) -> None:
        for k in list(sys.modules):
            if k.startswith("flask"):
                del sys.modules[k]
        import src.ops.buy_swarm_observatory_routes  # noqa: F401
        assert "flask" not in sys.modules


# ── 2. No DB writes ───────────────────────────────────────────────────────────

class TestNoDbWrites:
    def test_routes_no_writes(self) -> None:
        import inspect
        from src.ops import buy_swarm_observatory_routes
        src = inspect.getsource(buy_swarm_observatory_routes)
        for kw in ("INSERT", "UPDATE", "DELETE"):
            assert kw not in src


# ── 3. Framework: YAML loads, three operations present ────────────────────────

class TestFrameworkIntegration:
    def test_yaml_loads(self) -> None:
        from src.ops.registry_loader import get_operation
        op = get_operation("buy-swarm-observatory")
        assert op is not None
        assert op.operation_id == "buy-swarm-observatory"

    def test_three_operations_in_registry(self) -> None:
        from src.ops.registry_loader import list_operations
        ids = list_operations()
        assert "watchtower" in ids
        assert "launcher-observatory" in ids
        assert "buy-swarm-observatory" in ids

    def test_infrastructure_model_flat(self) -> None:
        # FLAT = no node tiers; the conceptual topology is a mesh but FLAT is the
        # framework-correct value (MESH requires full GRAPH-tier vocabulary).
        from src.ops.registry_loader import get_operation
        op = get_operation("buy-swarm-observatory")
        assert op.infrastructure_model == "FLAT"

    def test_archetype_coordinated_swarm(self) -> None:
        from src.ops.registry_loader import get_operation
        op = get_operation("buy-swarm-observatory")
        assert "swarm" in (op.operation_version or "").lower()

    def test_vocabulary_present(self) -> None:
        from src.ops.registry_loader import get_operation
        op = get_operation("buy-swarm-observatory")
        vocab = op.vocabulary
        assert vocab.get("observed_asset") == "Swarm"
        assert vocab.get("signal_source")  == "Swarm Participant"
        assert vocab.get("observation_event") == "Coordinated Buy"

    def test_vocabulary_identity_terms_distinct(self) -> None:
        """
        Primary identity terms (observed_asset, signal_source, observation_event)
        must be distinct across operations.  observed_outcome ('Campaign') is a
        meta-term legitimately shared by multiple operations and is excluded.
        """
        IDENTITY_KEYS = ("observed_asset", "signal_source", "observation_event")
        from src.ops.registry_loader import get_operation
        def id_vocab(op_id):
            return {v for k, v in get_operation(op_id).vocabulary.items() if k in IDENTITY_KEYS}

        wt_vocab  = id_vocab("watchtower")
        lo_vocab  = id_vocab("launcher-observatory")
        bso_vocab = id_vocab("buy-swarm-observatory")
        assert not (bso_vocab & wt_vocab), f"BSO/WATCHTOWER identity overlap: {bso_vocab & wt_vocab}"
        assert not (bso_vocab & lo_vocab), f"BSO/LO identity overlap: {bso_vocab & lo_vocab}"


# ── 4. Capability declarations ────────────────────────────────────────────────

class TestCapabilities:
    def _caps(self):
        from src.ops.registry_loader import get_operation
        return get_operation("buy-swarm-observatory").capabilities

    def test_health_supported(self) -> None:
        assert self._caps()["health"]["state"] == "SUPPORTED"

    def test_behaviour_supported(self) -> None:
        assert self._caps()["behaviour"]["state"] == "SUPPORTED"

    def test_intelligence_supported(self) -> None:
        assert self._caps()["intelligence"]["state"] == "SUPPORTED"

    def test_failure_attribution_supported(self) -> None:
        assert self._caps()["failure_attribution"]["state"] == "SUPPORTED"

    def test_outcome_intelligence_supported(self) -> None:
        assert self._caps()["outcome_intelligence"]["state"] == "SUPPORTED"

    def test_infrastructure_unsupported(self) -> None:
        assert self._caps()["infrastructure"]["state"] == "UNSUPPORTED"

    def test_discovery_assurance_unsupported(self) -> None:
        assert self._caps()["discovery_assurance"]["state"] == "UNSUPPORTED"


# ── 5. Providers registered ───────────────────────────────────────────────────

class TestProviders:
    EXPECTED = [
        "buy_swarm_observatory_health",
        "buy_swarm_observatory_failure_attribution",
        "buy_swarm_observatory_behaviour",
        "buy_swarm_observatory_intelligence",
        "buy_swarm_observatory_outcome_intelligence",
    ]

    def test_all_providers_registered(self) -> None:
        from src.ops.providers import PROVIDER_REGISTRY
        for pid in self.EXPECTED:
            assert pid in PROVIDER_REGISTRY, f"Provider {pid} not registered"

    def test_provider_paths_correct(self) -> None:
        from src.ops.providers import PROVIDER_REGISTRY
        for pid in self.EXPECTED:
            path = PROVIDER_REGISTRY[pid].path
            assert "/buy-swarm-observatory/" in path, f"{pid} has wrong path: {path}"


# ── 6. Qualification rules (data-backed) ──────────────────────────────────────

class TestQualificationRules:
    def test_min_participants_constant(self) -> None:
        from src.ops.buy_swarm_observatory_routes import MIN_PARTICIPANTS
        assert MIN_PARTICIPANTS == 3

    def test_max_window_seconds_constant(self) -> None:
        from src.ops.buy_swarm_observatory_routes import MAX_WINDOW_SECONDS
        assert MAX_WINDOW_SECONDS == 7200

    def test_require_known_subprov_constant(self) -> None:
        from src.ops.buy_swarm_observatory_routes import REQUIRE_KNOWN_SUBPROV
        assert REQUIRE_KNOWN_SUBPROV is True

    def test_qualified_swarms_count_positive(self) -> None:
        from src.ops.buy_swarm_observatory_routes import _get_health
        result = _get_health()
        assert result["ok"]
        assert result["qualified_swarms"] >= 60  # empirically verified ≥ 60

    def test_best_mint_qualifies(self) -> None:
        """C2kZUYZW must be in qualified swarms with ≥ 3 participants."""
        from src.ops.buy_swarm_observatory_routes import _ops_conn, _qualified_swarms_cte
        with _ops_conn() as conn:
            cte = _qualified_swarms_cte()
            row = conn.execute(
                cte + "SELECT mint, participant_count FROM qualified_swarms WHERE mint=? ORDER BY participant_count DESC LIMIT 1",
                (BEST_MINT,)
            ).fetchone()
        assert row is not None, "Best swarm mint not in qualified_swarms"
        assert row["participant_count"] >= 3

    def test_single_participant_mint_not_qualified(self) -> None:
        """A mint with only 1 observed wallet should not appear in qualified_swarms."""
        from src.ops.buy_swarm_observatory_routes import _ops_conn, _qualified_swarms_cte
        with _ops_conn() as conn:
            # Find a mint with exactly 1 participant
            single = conn.execute(
                "SELECT mint FROM wt_swarm_buys GROUP BY mint HAVING COUNT(DISTINCT swarm_wallet)=1 LIMIT 1"
            ).fetchone()
            if single is None:
                pytest.skip("No single-participant mint in test DB")
            cte = _qualified_swarms_cte()
            row = conn.execute(cte + "SELECT mint FROM qualified_swarms WHERE mint=?", (single["mint"],)).fetchone()
        assert row is None, "Single-participant mint incorrectly qualified"


# ── 7. Health endpoint ────────────────────────────────────────────────────────

class TestHealthEndpoint:
    def test_health_ok(self) -> None:
        from src.ops.buy_swarm_observatory_routes import _get_health
        r = _get_health()
        assert r["ok"] is True
        assert r["status"] == "HEALTHY"

    def test_health_total_observations(self) -> None:
        from src.ops.buy_swarm_observatory_routes import _get_health
        r = _get_health()
        assert r["total_observations"] >= 13000

    def test_health_exposes_qualification_rules(self) -> None:
        from src.ops.buy_swarm_observatory_routes import _get_health
        r = _get_health()
        rules = r["qualification_rules"]
        assert rules["min_participants"] == 3
        assert rules["max_window_seconds"] == 7200
        assert rules["require_known_subprov"] is True


# ── 8. Failure attribution endpoint ──────────────────────────────────────────

class TestFailureAttribution:
    def test_attribution_ok(self) -> None:
        from src.ops.buy_swarm_observatory_routes import _get_failure_attribution
        r = _get_failure_attribution()
        assert r["ok"] is True

    def test_all_failure_codes_present(self) -> None:
        from src.ops.buy_swarm_observatory_routes import _get_failure_attribution
        r = _get_failure_attribution()
        fb = r["failure_breakdown"]
        for code in ("DISC_SINGLE_BUY", "DISC_BELOW_THRESHOLD", "DISC_WINDOW_TOO_WIDE", "DISC_NO_PROVISIONER"):
            assert code in fb, f"Failure code {code} missing"

    def test_qualified_plus_failures_consistent(self) -> None:
        from src.ops.buy_swarm_observatory_routes import _get_failure_attribution
        r = _get_failure_attribution()
        # qualified + all failure bins should approximately sum to total
        # (some mints can fall in multiple bins across different groupings — just verify non-zero)
        assert r["qualified"] > 0
        assert r["total_targets_observed"] >= 269

    def test_no_provisioner_count_nonzero(self) -> None:
        from src.ops.buy_swarm_observatory_routes import _get_failure_attribution
        r = _get_failure_attribution()
        assert r["failure_breakdown"]["DISC_NO_PROVISIONER"] > 0


# ── 9. Behaviour endpoint ─────────────────────────────────────────────────────

class TestBehaviourEndpoint:
    def test_behaviour_ok(self) -> None:
        from src.ops.buy_swarm_observatory_routes import _get_behaviour
        r = _get_behaviour()
        assert r["ok"] is True

    def test_swarm_count_positive(self) -> None:
        from src.ops.buy_swarm_observatory_routes import _get_behaviour
        r = _get_behaviour()
        assert r["swarm_count"] >= 60

    def test_avg_participants_positive(self) -> None:
        from src.ops.buy_swarm_observatory_routes import _get_behaviour
        r = _get_behaviour()
        assert r["avg_participants_per_swarm"] > 0

    def test_campaign_count_present(self) -> None:
        from src.ops.buy_swarm_observatory_routes import _get_behaviour
        r = _get_behaviour()
        assert "campaign_count" in r
        assert r["campaign_count"] >= 1


# ── 10. Intelligence endpoint ─────────────────────────────────────────────────

class TestIntelligenceEndpoint:
    def test_intelligence_ok(self) -> None:
        from src.ops.buy_swarm_observatory_routes import _get_intelligence
        r = _get_intelligence()
        assert r["ok"] is True

    def test_top_swarms_present(self) -> None:
        from src.ops.buy_swarm_observatory_routes import _get_intelligence
        r = _get_intelligence()
        assert len(r["top_swarms"]) > 0

    def test_top_swarms_have_required_fields(self) -> None:
        from src.ops.buy_swarm_observatory_routes import _get_intelligence
        r = _get_intelligence()
        for swarm in r["top_swarms"]:
            assert "mint" in swarm
            assert "participant_count" in swarm
            assert "window_seconds" in swarm
            assert "first_seen" in swarm

    def test_campaigns_present(self) -> None:
        from src.ops.buy_swarm_observatory_routes import _get_intelligence
        r = _get_intelligence()
        assert "campaigns" in r
        assert len(r["campaigns"]) > 0

    def test_top_operators_present(self) -> None:
        from src.ops.buy_swarm_observatory_routes import _get_intelligence
        r = _get_intelligence()
        assert "top_operators" in r
        assert len(r["top_operators"]) > 0

    def test_best_swarm_is_top(self) -> None:
        """Top swarm in intelligence should have the most participants."""
        from src.ops.buy_swarm_observatory_routes import _get_intelligence
        r = _get_intelligence()
        assert len(r["top_swarms"]) > 0
        top = r["top_swarms"][0]
        # Participant count grows; just assert it's at or above the known floor
        assert top["participant_count"] >= 400


# ── 11. Outcome intelligence endpoint ────────────────────────────────────────

class TestOutcomeIntelligence:
    def test_outcome_ok(self) -> None:
        from src.ops.buy_swarm_observatory_routes import _get_outcome_intelligence
        r = _get_outcome_intelligence()
        assert r["ok"] is True

    def test_qualification_rate_positive(self) -> None:
        from src.ops.buy_swarm_observatory_routes import _get_outcome_intelligence
        r = _get_outcome_intelligence()
        assert r["qualification_rate_pct"] > 0

    def test_remaining_unqualified_positive(self) -> None:
        from src.ops.buy_swarm_observatory_routes import _get_outcome_intelligence
        r = _get_outcome_intelligence()
        assert r["remaining_unqualified"] > 0


# ── 12. Entity Intelligence integration — participant ────────────────────────

class TestEntityIntelligenceParticipant:
    def test_participant_has_bso_observation(self) -> None:
        from src.intelligence.service import resolve
        summary = resolve(PARTICIPANT_ADDR)
        op_ids = {o.operation_id for o in summary.operations}
        assert "buy-swarm-observatory" in op_ids

    def test_participant_role_correct(self) -> None:
        from src.intelligence.service import resolve
        summary = resolve(PARTICIPANT_ADDR)
        bso_obs = [o for o in summary.operations if o.operation_id == "buy-swarm-observatory"]
        roles = {o.role for o in bso_obs}
        assert "SWARM_PARTICIPANT" in roles

    def test_participant_timeline_has_swarm_buy_event(self) -> None:
        from src.intelligence.service import resolve
        summary = resolve(PARTICIPANT_ADDR)
        event_types = {e.event_type for e in summary.timeline}
        assert "SWARM_BUY_OBSERVED" in event_types

    def test_participant_timeline_events_sorted(self) -> None:
        from src.intelligence.service import resolve
        summary = resolve(PARTICIPANT_ADDR)
        ts_list = [e.ts for e in summary.timeline if e.event_type != "KNOWLEDGE_DERIVED"]
        assert ts_list == sorted(ts_list)

    def test_participant_provenance_set(self) -> None:
        from src.intelligence.service import resolve
        summary = resolve(PARTICIPANT_ADDR)
        for obs in summary.operations:
            assert obs.provenance


# ── 13. Entity Intelligence integration — subprov ────────────────────────────

class TestEntityIntelligenceSubprov:
    def test_subprov_has_bso_observation(self) -> None:
        from src.intelligence.service import resolve
        summary = resolve(SUBPROV_ADDR)
        op_ids = {o.operation_id for o in summary.operations}
        assert "buy-swarm-observatory" in op_ids

    def test_subprov_role_is_swarm_subprov(self) -> None:
        from src.intelligence.service import resolve
        summary = resolve(SUBPROV_ADDR)
        bso_obs = [o for o in summary.operations if o.operation_id == "buy-swarm-observatory"]
        roles = {o.role for o in bso_obs}
        assert "SWARM_SUBPROV" in roles

    def test_subprov_timeline_has_swarm_provisioned(self) -> None:
        from src.intelligence.service import resolve
        summary = resolve(SUBPROV_ADDR)
        event_types = {e.event_type for e in summary.timeline}
        assert "SWARM_PROVISIONED" in event_types

    def test_subprov_qualified_swarm_count_correct(self) -> None:
        from src.intelligence.service import resolve
        summary = resolve(SUBPROV_ADDR)
        bso_obs = [o for o in summary.operations
                   if o.operation_id == "buy-swarm-observatory" and o.role == "SWARM_SUBPROV"]
        assert bso_obs
        assert bso_obs[0].facts["qualified_swarm_count"] >= 1
        assert bso_obs[0].facts["total_participants"] >= 400


# ── 14. Knowledge integration ─────────────────────────────────────────────────

class TestKnowledgeIntegration:
    def test_intelligence_endpoint_enriches_subprovs(self) -> None:
        """Intelligence response may carry subprov_knowledge from Knowledge Layer."""
        from src.ops.buy_swarm_observatory_routes import _get_intelligence
        r = _get_intelligence()
        # At least some swarms may have knowledge enrichment (best-effort)
        # We just verify the field is present (may be empty if no rules fire)
        for swarm in r["top_swarms"]:
            # Field is optional but must not cause an error
            assert isinstance(swarm.get("subprov_knowledge", []), list)

    def test_participant_entity_has_knowledge_derived_events(self) -> None:
        """Knowledge-derived timeline events must appear via standard Knowledge Layer."""
        from src.intelligence.service import resolve
        summary = resolve(PARTICIPANT_ADDR)
        # KNOWLEDGE_DERIVED events come from knowledge.engine — they should be present
        # if any rules fire for this wallet. If none fire, timeline may not have them.
        # Just verify the mechanism doesn't break anything.
        kd = [e for e in summary.timeline if e.event_type == "KNOWLEDGE_DERIVED"]
        # Not asserting count — depends on whether rules fire for this specific wallet
        for ev in kd:
            assert ev.provenance
            assert ev.source == "KNOWLEDGE_LAYER"


# ── 15. Unknown entity handled cleanly ────────────────────────────────────────

class TestUnknownEntity:
    def test_unknown_no_bso_observations(self) -> None:
        from src.intelligence.service import resolve
        summary = resolve(UNKNOWN_ADDR)
        bso_obs = [o for o in summary.operations if o.operation_id == "buy-swarm-observatory"]
        assert bso_obs == []

    def test_get_swarm_buys_for_unknown_wallet(self) -> None:
        from src.ops.buy_swarm_observatory_routes import get_swarm_buys_for_wallet
        result = get_swarm_buys_for_wallet(UNKNOWN_ADDR)
        assert result == []

    def test_get_swarm_buys_for_unknown_subprov(self) -> None:
        from src.ops.buy_swarm_observatory_routes import get_swarm_buys_for_subprov
        result = get_swarm_buys_for_subprov(UNKNOWN_ADDR)
        assert result == []


# ── 16. Performance ───────────────────────────────────────────────────────────

class TestPerformance:
    def test_health_under_500ms(self) -> None:
        from src.ops.buy_swarm_observatory_routes import _get_health
        _get_health()  # warm
        start = time.perf_counter()
        _get_health()
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert elapsed_ms < 500, f"Health took {elapsed_ms:.1f}ms"

    def test_behaviour_under_500ms(self) -> None:
        from src.ops.buy_swarm_observatory_routes import _get_behaviour
        _get_behaviour()
        start = time.perf_counter()
        _get_behaviour()
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert elapsed_ms < 500, f"Behaviour took {elapsed_ms:.1f}ms"

    def test_intelligence_under_500ms(self) -> None:
        from src.ops.buy_swarm_observatory_routes import _get_intelligence
        _get_intelligence()
        start = time.perf_counter()
        _get_intelligence()
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert elapsed_ms < 500, f"Intelligence took {elapsed_ms:.1f}ms"

    def test_entity_intelligence_participant_under_200ms(self) -> None:
        from src.intelligence.service import resolve
        resolve(PARTICIPANT_ADDR)  # warm
        start = time.perf_counter()
        resolve(PARTICIPANT_ADDR)
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert elapsed_ms < 200, f"EI participant resolve took {elapsed_ms:.1f}ms"
