"""X67.32 complete operation-intelligence workspace contracts."""
from __future__ import annotations

from pathlib import Path

from src.ops.operation_intelligence import OperationIntelligenceAssembler, _summary


ROOT = Path(__file__).resolve().parents[1]


def _family(identifier="family:a", launches=2):
    return {
        "family_id": identifier, "family_name": identifier, "lifecycle_state": "EMERGING",
        "stage": "EMERGING", "launch_list": [f"M{i}" for i in range(launches)],
        "member_wallets": ["CLIENT"], "client_wallets": ["CLIENT"],
        "unique_creators": ["C1", "C2"], "treasuries": ["T1"],
        "funding_mechanisms": ["PLAIN_XFER"], "observed_topology_variants": [
            "Treasury → Persistent Client → Fresh Creator → Launch",
            "Treasury → Relay → Persistent Client → Fresh Creator → Launch",
        ],
        "dominant_topology": "Treasury → Persistent Client → Fresh Creator → Launch",
        "first_seen_at": 100, "last_material_activity_at": 200, "active_sessions": 0,
        "session_count": 0, "launches": launches, "promotion_status": "REVIEWABLE",
        "supporting_evidence": [], "growth_timeline": [], "contradictions": [],
        "exclusion_evidence": [], "blocking_reasons": [], "material_change_reasons": [],
        "confidence": None,
        "discovery_significance": {"score": 60, "dimensions": [
            {"key": "recent_launch_activity", "score": 20, "maximum": 30, "label": "Recent"}
        ]},
        "evidence_completeness": {"score": 70}, "operational_maturity": {"score": 50},
    }


def test_numeric_summaries_use_only_persisted_values():
    assert _summary([]) == {
        "count": 0, "minimum": None, "median": None, "average": None, "maximum": None
    }
    assert _summary([1, 2, 9]) == {
        "count": 3, "minimum": 1.0, "median": 2.0, "average": 4.0, "maximum": 9.0
    }


def test_intelligence_payload_has_every_operation_workspace_dimension(tmp_path):
    ops, live = tmp_path / "ops.db", tmp_path / "live.db"
    import sqlite3
    sqlite3.connect(ops).close(); sqlite3.connect(live).close()
    family = _family()
    payload = OperationIntelligenceAssembler(str(ops), str(live)).build(
        family, [family], {"total_tokens": 2, "unknown_tokens": 0}
    )
    assert set(payload) >= {
        "overview", "timeline", "behaviour", "infrastructure", "performance",
        "evidence_audit", "comparison_peers", "ecosystem_context", "data_contract",
    }
    assert payload["performance"]["win_rate"] is None
    assert payload["performance"]["failed"] is None
    assert payload["data_contract"]["estimated_metrics"] == []
    assert len(payload["infrastructure"]["topology_variants"]) == 2


def test_comparison_is_generic_for_any_family(tmp_path):
    ops, live = tmp_path / "ops.db", tmp_path / "live.db"
    import sqlite3
    sqlite3.connect(ops).close(); sqlite3.connect(live).close()
    left, right = _family("family:left"), _family("family:right", 4)
    payload = OperationIntelligenceAssembler(str(ops), str(live)).build(
        left, [left, right], {"total_tokens": 6, "unknown_tokens": 0}
    )
    assert payload["comparison_peers"][0]["family_id"] == "family:right"
    assert payload["comparison_peers"][0]["shared_treasuries"] == ["T1"]
    assert payload["ecosystem_context"]["launch_rank"] == 2


def test_profile_exposes_timeline_behaviour_performance_and_comparison_tabs():
    source = (ROOT / "templates/operation_profile.html").read_text()
    for label in ("Timeline", "Behaviour", "Performance", "Infrastructure", "Evidence", "Compare"):
        assert f"'{label}'" in source
    assert "token_href" in source
    assert "Traceable evidence audit" in source
    assert "WATCHTOWER" not in source and "B48k" not in source
