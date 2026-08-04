from dataclasses import FrozenInstanceError

import pytest

from src.ops.emerging_operator_service import EmergingOperatorService
from src.ops.investigation_population import InvestigationPopulation


def _profile(wallet: str) -> dict:
    return {
        "wallet": wallet,
        "sources": {"wt_provisioning_edges", "wt_active_subprov_sessions"},
        "candidate": {"distinct_launches": 2},
        "creators": {f"{wallet}-creator-1", f"{wallet}-creator-2"},
        "treasuries": {"treasury-a", "treasury-b"},
        "mechanisms": {"PLAIN_XFER"},
        "signatures": {f"{wallet}-signature"},
        "launches": {f"{wallet}-mint"},
        "first": 1_700_000_000,
        "last": 1_700_086_400,
        "sessions": 2,
        "active_sessions": 1,
        "session_times": {1_700_000_000},
        "evidence": [{"type": "FUNDING_EDGE", "source": "test"}],
        "outcomes": [],
        "templates": {},
        "campaigns": set(),
        "session_amounts": [],
        "edge_times": [1_700_000_000],
        "zero_value_edges": 0,
        "exclusions": [],
        "warnings": [],
    }


def test_population_is_first_class_and_contains_no_presentation_state():
    service = EmergingOperatorService("ops", "live")
    population = service._population_builder().build_group([_profile("A")])

    assert isinstance(population, InvestigationPopulation)
    assert population.members == ("A",)
    assert population.launches == ("A-mint",)
    assert population.population_basis == ()
    forbidden = {
        "stage", "status", "lifecycle", "lifecycle_state", "candidate_state",
        "confirmation", "operator_id", "operation_id", "attention_eligible",
        "attention_rank", "reconciliation", "promotion_status",
    }
    assert forbidden.isdisjoint(population.metadata)
    with pytest.raises(FrozenInstanceError):
        population.anchor = "changed"


def test_legacy_adapter_preserves_the_existing_family_projection():
    service = EmergingOperatorService("ops", "live")
    group = [_profile("A"), _profile("B")]

    population = service._population_builder().build_group(group)
    projected = service._legacy_adapter(None, []).project(population)
    compatibility = service._profile_family(group, None, [])

    assert projected == compatibility
    assert projected["family_id"] == population.population_id
    assert projected["member_wallets"] == list(population.members)
    assert projected["stage"] == "DORMANT"
