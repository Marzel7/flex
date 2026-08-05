"""X72.2 task-oriented Investigation Population profile contracts."""
from pathlib import Path

import pytest

from src.core.db import DB_PATH, OPS_DB_PATH
from src.ops.emerging_operator_service import EmergingOperatorService


ROOT = Path(__file__).resolve().parents[1]


def _source() -> str:
    return (ROOT / "templates/operation_profile.html").read_text()


def test_hero_is_reduced_to_object_type_and_name():
    source = _source()
    hero = source.split('<header class="rp-hero">', 1)[1].split("</header>", 1)[0]
    for field in ("rp-kind", "rp-name"):
        assert field in hero
    for removed in ("Evidence %", "Significance", "Maturity", "First observed", "Active sessions", "Treasuries"):
        assert removed not in hero


def test_exactly_five_task_oriented_tabs_and_legacy_tab_aliases():
    source = _source()
    assert source.count('<section class="rp-panel" data-panel=') == 5
    for tab in ("Summary", "Evidence", "Members", "Analysis", "History"):
        assert "'" + tab + "'" in source
    for alias in (
        "participants:'members'", "launches:'members'", "timeline:'history'",
        "walkback:'analysis'", "topology:'analysis'", "reconciliation:'evidence'",
        "operational:'analysis'", "comparison:'analysis'",
    ):
        assert alias in source


def test_first_screen_deduplicates_state_and_uses_compact_summary():
    source = _source()
    assert "disclosure('Why?'" not in source
    assert "Population Metrics" not in source
    assert "Population Identity" in source
    assert "Next Evidence" in source


def test_detail_surfaces_use_progressive_disclosure():
    source = _source()
    for section in (
        "Infrastructure", "Behaviour", "Advanced Metrics", "Timeline",
        "Legacy Context",
    ):
        assert "disclosure('" + section + "'" in source
    assert "<details class=\"rp-disclosure\"" in source


def test_promotion_actions_are_reconciliation_gated_without_unresolved_bypass():
    source = _source()
    for state in ("NOT ELIGIBLE", "READY FOR REVIEW", "OPERATOR CANDIDATE", "CONFIRMED OPERATION"):
        assert state in source
    assert "pres.confirmation_permitted&&disp==='OPERATOR_CANDIDATE'" in source
    assert "Confirm Operation" in source
    assert "Next Evidence" in source


@pytest.fixture(scope="module")
def production_projection():
    service = EmergingOperatorService(str(OPS_DB_PATH), str(DB_PATH))
    families = service._compose()
    return service, families, service._list_uncached(limit=500)


def test_promoted_operation_preserves_its_source_population_without_double_accounting(production_projection):
    _, families, listing = production_projection
    operation = next(x for x in families if x["family_name"] == "3SW2")
    population = next(x for x in families if x["family_name"] == "3SW2 Family")
    assert operation["source_population_id"] == population["family_id"]
    assert population["promoted_to_operation_id"] == operation["family_id"]
    assert population["child_operations"] == [operation["family_id"]]
    surfaced = sum((listing[key] for key in (
        "confirmed_operations_reconciled", "active_investigations_reconciled",
        "operator_candidates_reconciled", "review_cases_reconciled",
        "infrastructure_alerts_reconciled",
    )), [])
    assert sum(x["family_name"] == "3SW2" for x in surfaced) == 1
    assert not any(x["family_name"] == "3SW2 Family" for x in surfaced)
    assert listing["reconciled_counts"]["exclusive"] is True
    assert sum(listing["reconciled_counts"]["launch_counts"].values()) == listing["reconciled_counts"]["total_launches"]


def test_named_controls_keep_expected_dispositions(production_projection):
    _, _, listing = production_projection
    visible = sum((listing[key] for key in (
        "confirmed_operations_reconciled", "active_investigations_reconciled",
        "review_cases_reconciled", "infrastructure_alerts_reconciled",
    )), [])
    by_name = {x["family_name"]: x for x in visible}
    assert by_name["WATCHTOWER"]["reconciliation"]["disposition"] == "CONFIRMED_OPERATION"
    assert by_name["3SW2"]["reconciliation"]["disposition"] == "CONFIRMED_OPERATION"
    assert by_name["B48k / Dv34 Family"]["reconciliation"]["disposition"] == "UNRESOLVED"
    assert by_name["C7Ha Family"]["reconciliation"]["disposition"] == "REVIEW"
    assert any(x["reconciliation"]["disposition"] == "INFRASTRUCTURE" for x in visible)


def test_decision_layers_remain_unchanged():
    for relative in (
        "src/ops/disposition_resolver.py", "src/ops/evidence_reconciliation.py",
        "src/ops/operation_attribution.py",
    ):
        assert "X72.2" not in (ROOT / relative).read_text()
