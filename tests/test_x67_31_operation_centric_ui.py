"""X67.31 operation-centric navigation, profiles, and reconciliation contracts."""
from __future__ import annotations

from pathlib import Path

from src.ops.emerging_operator_service import EmergingOperatorService


ROOT = Path(__file__).resolve().parents[1]


def test_registry_links_every_surface_to_a_permanent_operation_profile(monkeypatch):
    service = EmergingOperatorService("missing.db", "missing-live.db")
    family = {
        "family_id": "family:stable", "supporting_evidence": [],
        "discovery_timeline": [], "evidence_timeline": [], "growth_timeline": [],
        "launch_list": [], "unique_creators": [],
    }
    card = service._list_card(family)
    assert card["profile_href"] == "/intelligence/operations/family:stable"
    assert card["operational_intelligence_href"].endswith("?tab=operational")


def test_operation_profile_is_generic_and_topology_is_first_class():
    source = (ROOT / "templates/operation_profile.html").read_text()
    assert "Operation Profile" in source
    assert "Intelligence" in source
    assert "Structure" in source
    assert "Topology" in source
    assert "aliases=" in source
    assert "WATCHTOWER" not in source
    assert "B48k" not in source


def test_registry_is_a_compact_attention_dashboard():
    source = (ROOT / "templates/emerging_operators.html").read_text()
    assert "Evidence incomplete · investigate selectively" in source
    assert "Contradictory evidence · resolution required" in source
    assert "Shared services · attribution caution" in source
    assert "Why surfaced now" not in source
    assert "analyst_explanation" not in source


def test_sidebar_navigation_is_operation_centric_and_not_operation_specific():
    source = (ROOT / "templates/partials/sidebar.html").read_text()
    assert 'href="/intelligence/operations">Operation Registry' in source
    assert "Canonical Operators" in source
    assert "Ecosystem Intelligence" in source
    assert '>WATCHTOWER<' not in source


def test_reconciliation_assigns_each_claimed_token_once(monkeypatch):
    service = EmergingOperatorService("missing.db", "missing-live.db")
    families = [
        {"family_id": "confirmed", "stage": "CONFIRMED", "launch_list": ["A", "B"]},
        {"family_id": "emerging", "stage": "EMERGING", "launch_list": ["B", "C"]},
        {"family_id": "candidate", "stage": "CANDIDATE", "launch_list": ["D"]},
        {"family_id": "retired", "stage": "RETIRED", "launch_list": ["E"]},
    ]
    result = service._reconcile_token_states(families)
    assert result["total_tokens"] == result["assigned_total"] == 5
    assert result["confirmed_tokens"] == 2
    assert result["emerging_tokens"] == 1
    assert result["candidate_tokens"] == 1
    assert result["unknown_tokens"] == 1
    assert result["source_overlap_count"] == 1
    assert result["balanced"] is True
