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


def test_infrastructure_launches_split_by_recorded_upstream_sessions():
    family = _family(launches=4)
    family["treasuries"] = ["T1", "T2", "T3"]
    edges = [
        {"edge_type": "SUBPROV_TO_CREATOR", "from_wallet": "CLIENT", "to_wallet": f"C{i}",
         "source_mint": f"M{i}", "funding_block_time": timestamp}
        for i, timestamp in enumerate((1_780_000_110, 1_780_000_120, 1_780_000_210, 1_780_000_220))
    ]
    sessions = [
        {"subprov_wallet": "CLIENT", "treasury_wallet": "T1", "funding_time": 1_780_000_100},
        {"subprov_wallet": "CLIENT", "treasury_wallet": "T2", "funding_time": 1_780_000_200},
    ]
    infrastructure = OperationIntelligenceAssembler._infrastructure(family, edges, sessions)
    assert infrastructure["launches_by_treasury"] == [
        {"treasury": "T1", "launch_count": 2},
        {"treasury": "T2", "launch_count": 2},
        {"treasury": "T3", "launch_count": 0},
    ]
    assert infrastructure["launches_by_treasury_total"] == 4
    assert infrastructure["treasury_by_launch"] == {
        "M0": "T1", "M1": "T1", "M2": "T2", "M3": "T2",
    }


def test_session_projection_supports_production_schema_without_source_mint(tmp_path):
    import sqlite3
    ops, live = tmp_path / "ops.db", tmp_path / "live.db"
    with sqlite3.connect(ops) as conn:
        conn.execute("CREATE TABLE wt_active_subprov_sessions (subprov_wallet TEXT, treasury_wallet TEXT, funding_time INTEGER)")
        conn.execute("INSERT INTO wt_active_subprov_sessions VALUES ('CLIENT','T1',1780000100)")
    sqlite3.connect(live).close()
    assembler = OperationIntelligenceAssembler(str(ops), str(live))
    _, sessions, _ = assembler._operation_rows(["M1"], ["CLIENT"])
    assert sessions == [{"subprov_wallet": "CLIENT", "treasury_wallet": "T1", "funding_time": 1780000100}]


def test_profile_exposes_intelligence_surfaces_through_five_task_groups():
    source = (ROOT / "templates/operation_profile.html").read_text()
    for label in ("Timeline", "Behaviour", "Infrastructure", "Evidence", "Intelligence"):
        assert label in source
    assert "token_href" in source
    assert "Evidence Audit" in source
    assert "performance:'intelligence'" in source
    assert "comparison:'intelligence'" in source
    assert "WATCHTOWER" not in source and "B48k" not in source
