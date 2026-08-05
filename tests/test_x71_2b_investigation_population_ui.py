"""X71.2B Investigation Population presentation contracts."""
from pathlib import Path

import pytest

from src.core.db import DB_PATH, OPS_DB_PATH
from src.ops.emerging_operator_service import EmergingOperatorService
from src.ops.reconciliation_presentation import reconciliation_presentation


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def registry():
    service = EmergingOperatorService(str(OPS_DB_PATH), str(DB_PATH))
    return service, service.list()


def test_b48_is_an_active_investigation_and_never_an_operation_queue(registry):
    _, data = registry
    active = data["active_investigations_reconciled"]
    b48 = next(item for item in active if item["family_name"] == "B48k / Dv34 Family")
    assert b48["launches"] >= 67
    assert b48["reconciliation"]["disposition"] == "UNRESOLVED"
    assert b48["presentation"]["label"] == "Investigation Population"
    assert b48["presentation"]["kind"] == "investigation_population"
    forbidden = (
        data["confirmed_operations_reconciled"]
        + data["operator_candidates_reconciled"]
        + data["review_cases_reconciled"]
    )
    assert all(item["family_name"] != "B48k / Dv34 Family" for item in forbidden)


def test_named_controls_remain_reconciled(registry):
    _, data = registry
    confirmed = {
        x["family_name"]: x for x in data["confirmed_operations_reconciled"]
    }
    assert confirmed["WATCHTOWER"]["presentation"]["label"] == "Confirmed Operation"
    assert confirmed["3SW2"]["presentation"]["label"] == "Confirmed Operation"
    assert confirmed["3SW2"]["launches"] == 13
    assert confirmed["3SW2"]["source_population_id"] == "family:ebab4a2ecbc1c3a6"
    assert not any(
        x["family_name"] == "3SW2 Family"
        for bucket in (
            data["active_investigations_reconciled"],
            data["operator_candidates_reconciled"],
            data["review_cases_reconciled"],
        )
        for x in bucket
    )
    c7ha = next(x for x in data["review_cases_reconciled"] if x["family_name"] == "C7Ha Family")
    assert c7ha["reconciliation"]["disposition"] == "REVIEW"


def test_investigation_summary_and_empty_cluster_contract_are_data_driven(registry):
    service, _ = registry
    family = next(x for x in service._compose() if x["family_name"] == "B48k / Dv34 Family")
    detail = service.get(family["family_id"])
    presentation = detail["presentation"]
    assert presentation["parent_population_id"] == detail["family_id"]
    assert presentation["potential_operator_clusters"] == []
    assert presentation["child_operations"] == []
    assert any(
        f"{detail['launches']} persisted launches" in line
        for line in presentation["investigation_summary"]
    )
    assert "legacy registry as Confirmed" in presentation["historical_context"]


def test_future_child_clusters_are_projected_without_reclassifying_parent():
    value = reconciliation_presentation(
        {
            "family_id": "population:one",
            "family_name": "Population One",
            "lifecycle_state": "CONFIRMED",
            "launches": 8,
            "potential_operator_clusters": [{
                "cluster_id": "cluster:a", "launches": 3, "creators": 2,
                "disposition": "OPERATOR_CANDIDATE",
                "supporting_observations": ["independent fee payer"],
                "missing_evidence": ["settlement corroboration"],
            }],
            "child_operations": [{"operation_id": "operation:future"}],
        },
        {"disposition": "UNRESOLVED", "supporting_evidence_count": 4, "missing_evidence_count": 1},
    )
    assert value["kind"] == "investigation_population"
    assert value["potential_operator_clusters"][0]["cluster_id"] == "cluster:a"
    assert value["potential_operator_clusters"][0]["current_disposition"] == "OPERATOR_CANDIDATE"
    assert value["child_operations"] == [{"operation_id": "operation:future"}]


def test_ui_uses_investigation_language_and_preserves_evidence_surfaces():
    profile = (ROOT / "templates/operation_profile.html").read_text()
    registry = (ROOT / "templates/emerging_operators.html").read_text()
    discovery = (ROOT / "templates/discovery.html").read_text()
    for value in (
        "Investigation Summary", "Potential Operator Clusters", "Walkback",
        "Topology", "Launches", "Behaviour", "Statistics", "Evidence",
        "No independently supported operator clusters identified.",
    ):
        assert value in profile
    assert "active_investigations_reconciled" in registry
    assert "Active Investigations" in registry
    assert '"UNRESOLVED": "Investigation Population"' in (ROOT / "src/discovery/service.py").read_text()
    assert "Reconciled Attribution · Level 1" in discovery


def test_attribution_and_resolver_layers_remain_unchanged():
    attribution = (ROOT / "src/ops/operation_attribution.py").read_text()
    resolver = (ROOT / "src/ops/disposition_resolver.py").read_text()
    assert "investigation_summary" not in attribution
    assert "potential_operator_clusters" not in attribution
    assert "B48k" not in resolver
