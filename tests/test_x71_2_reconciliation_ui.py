"""X71.2 reconciled presentation contracts (production attribution is unchanged)."""
from pathlib import Path

import pytest

from src.core.db import DB_PATH, OPS_DB_PATH
from src.ops.emerging_operator_service import EmergingOperatorService
from src.ops.reconciliation_presentation import reconciliation_presentation


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def workspace():
    service = EmergingOperatorService(str(OPS_DB_PATH), str(DB_PATH))
    return service, service.list()


def test_attention_queues_are_bounded_and_launch_ledger_is_exclusive(workspace):
    _, data = workspace
    assert len(data["operator_candidates_reconciled"]) <= 5
    assert len(data["review_cases_reconciled"]) <= 5
    assert len(data["infrastructure_alerts_reconciled"]) <= 5
    ledger = data["reconciled_counts"]
    assert ledger["exclusive"] is True
    assert ledger["launch_unit"] == "launches"
    assert ledger["population_unit"] == "populations"
    assert ledger["assigned_launches"] == ledger["total_launches"]
    assert sum(ledger["launch_counts"].values()) == ledger["total_launches"]


def test_named_controls_use_reconciled_nouns_without_changing_legacy(workspace):
    service, data = workspace
    confirmed = data["confirmed_operations_reconciled"]
    assert [(x["family_name"], x["reconciliation"]["disposition"]) for x in confirmed] == [
        ("WATCHTOWER", "CONFIRMED_OPERATION")
    ]
    all_families = service._compose()
    b48 = next(x for x in all_families if x["family_name"] == "B48k / Dv34 Family")
    c7ha = next(x for x in all_families if x["family_name"] == "C7Ha Family")
    b48_detail = service.get(b48["family_id"])
    c7ha_detail = service.get(c7ha["family_id"])
    assert b48_detail["lifecycle_state"] == "CONFIRMED"  # legacy authority unchanged
    assert b48_detail["reconciliation"]["disposition"] == "UNRESOLVED"
    assert b48_detail["presentation"]["kind"] == "investigation_population"
    assert b48_detail["presentation"]["confirmation_permitted"] is False
    assert c7ha_detail["reconciliation"]["disposition"] == "REVIEW"
    assert c7ha_detail["presentation"]["label"] == "Review Required"


def test_fallback_is_explicit_and_never_confirmation_eligible():
    value = reconciliation_presentation(
        {"family_id": "family:x", "family_name": "Example", "lifecycle_state": "EMERGING"},
        None,
    )
    assert value["reconciled"] is False
    assert value["label"] == "Legacy attribution · Emerging"
    assert value["confirmation_permitted"] is False


def test_templates_make_reconciliation_authoritative_and_keep_debug_hidden():
    operations = (ROOT / "templates/emerging_operators.html").read_text()
    profile = (ROOT / "templates/operation_profile.html").read_text()
    discovery = (ROOT / "templates/discovery.html").read_text()
    mission_control = (ROOT / "templates/ops_shell_index.html").read_text()
    assert "operator_candidates_reconciled" in operations
    assert "review_cases_reconciled" in operations
    assert "infrastructure_alerts_reconciled" in operations
    assert "Background investigations remain internal" in operations
    assert "pres.confirmation_permitted" in profile
    assert "Reconciled Intelligence Record" in profile
    assert "Reconciled Attribution · Level 1" in discovery
    assert "Walkback Outcome · Level 1" in discovery
    assert "new URLSearchParams(location.search).get('debug')==='1'" in discovery
    assert 'href="/intelligence/operations"' in discovery
    assert "/intelligence/emerging-operators" not in discovery
    assert "/intelligence/emerging-operators" not in mission_control


def test_production_attribution_has_no_reconciliation_presentation_dependency():
    source = (ROOT / "src/ops/operation_attribution.py").read_text()
    assert "reconciliation_presentation" not in source
    assert "DispositionResolver" not in source
