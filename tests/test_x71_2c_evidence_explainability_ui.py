"""X71.2C analyst-safe evidence explainability and readiness contracts."""
from pathlib import Path

import pytest

from src.core.db import DB_PATH, OPS_DB_PATH
from src.ops.emerging_operator_service import EmergingOperatorService
from src.ops.reconciliation_metadata import build_reconciliation_metadata


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def projection():
    service = EmergingOperatorService(str(OPS_DB_PATH), str(DB_PATH))
    families = service._compose()
    metadata = build_reconciliation_metadata(service, families)
    by_name = {family["family_name"]: family for family in families}
    return service, families, metadata, by_name


def _metadata(projection, name):
    family = projection[3][name]
    return projection[2][family["family_id"]]


def test_every_projected_evidence_item_is_analyst_safe(projection):
    required = {
        "evidence_type", "label", "description", "truth_status",
        "applicability", "applicability_reason", "provenance",
        "provenance_independence", "dependency_group",
        "observation_count", "eligible",
    }
    for metadata in projection[2].values():
        for key in ("supporting_evidence", "contradictory_evidence", "missing_evidence"):
            for item in metadata[key]:
                assert set(item) == required
                assert item["description"]
                assert "EvidenceItem" not in str(item)
                assert "SemanticEvidence" not in str(item)


def test_unresolved_populations_explain_support_gaps_and_readiness(projection):
    for name in ("B48k / Dv34 Family",):
        value = _metadata(projection, name)
        assert value["disposition"] == "UNRESOLVED"
        assert value["supporting_evidence"]
        assert value["missing_evidence"]
        assert value["why_population_exists"]
        assert value["analyst_explanation"]
        assert value["promotion_readiness"]["state"] == "PARTIALLY READY"
        assert value["promotion_readiness"]["eligible_for_confirmation"] is False
        assert "Additional independent evidence" in value["promotion_readiness"]["message"]
        assert value["promotion_readiness"]["blockers"]


def test_named_terminal_dispositions_explain_their_decisions(projection):
    watchtower = _metadata(projection, "WATCHTOWER")
    three_sw2 = _metadata(projection, "3SW2")
    c7ha = _metadata(projection, "C7Ha Family")
    infrastructure = next(
        value for value in projection[2].values()
        if value["disposition"] == "INFRASTRUCTURE"
    )
    dust = next(
        value for value in projection[2].values()
        if value["disposition"] == "REJECTED"
        and any(item["evidence_type"] == "DUST_PATTERN" for item in value["contradictory_evidence"])
    )
    assert watchtower["disposition"] == "CONFIRMED_OPERATION"
    assert any("MANUAL_PROMOTION" in line for line in watchtower["analyst_explanation"])
    assert watchtower["promotion_readiness"]["blockers"] == []
    assert watchtower["promotion_readiness"]["requirements"] == []
    assert three_sw2["disposition"] == "CONFIRMED_OPERATION"
    assert any("MANUAL_PROMOTION" in line for line in three_sw2["analyst_explanation"])
    assert three_sw2["promotion_readiness"]["blockers"] == []
    assert c7ha["disposition"] == "REVIEW"
    assert c7ha["promotion_readiness"]["state"] == "READY FOR REVIEW"
    assert c7ha["contradictory_evidence"]
    assert any("INFRASTRUCTURE" in line for line in infrastructure["analyst_explanation"])
    assert any(item["evidence_type"] == "DUST_PATTERN" for item in dust["contradictory_evidence"])


def test_missing_evidence_is_only_package_missing_evidence(projection):
    value = _metadata(projection, "B48k / Dv34 Family")
    assert len(value["missing_evidence"]) == value["missing_evidence_count"]
    assert all(item["truth_status"] == "UNKNOWN" for item in value["missing_evidence"])
    assert all(item["observation_count"] is None for item in value["missing_evidence"])
    assert value["promotion_readiness"]["requirements"] == [
        item["description"] for item in value["missing_evidence"]
    ]


def test_profile_and_registry_render_explanations_without_diagnostics():
    profile = (ROOT / "templates/operation_profile.html").read_text()
    registry = (ROOT / "templates/emerging_operators.html").read_text()
    for phrase in (
        "Promotion Readiness", "Promotion blockers", "Evidence required",
        "truth_status", "applicability", "provenance", "dependency_group",
        "observation_count", "Legacy Context",
    ):
        assert phrase in profile
    assert "evidenceDetails" in registry
    assert "Promotion Readiness" in registry
    assert "Legacy Context" in registry
    assert "?debug=1" not in profile


def test_production_decision_layers_are_unchanged():
    attribution = (ROOT / "src/ops/operation_attribution.py").read_text()
    resolver = (ROOT / "src/ops/disposition_resolver.py").read_text()
    evidence = (ROOT / "src/ops/evidence_reconciliation.py").read_text()
    assert "promotion_readiness" not in attribution
    assert "promotion_readiness" not in resolver
    assert "promotion_readiness" not in evidence
