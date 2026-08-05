import sqlite3
from pathlib import Path

from src.ops.operation_intelligence import OperationIntelligenceAssembler
from src.ops.operational_role import derive_operational_role


ROOT = Path(__file__).resolve().parents[1]


def test_role_chain_contains_only_recorded_relationships():
    family = {
        "member_wallets": ["CONTROL"], "client_wallets": ["CONTROL"],
        "treasuries": ["TREASURY"], "launch_list": ["MINT"],
        "reconciliation": {"disposition": "REVIEW"},
    }
    infrastructure = {"funding_paths": [
        {"from": "TREASURY", "to": "CONTROL", "type": "TREASURY_TO_SUBPROV", "signature": "A"},
        {"from": "CONTROL", "to": "CREATOR", "type": "SUBPROV_TO_CREATOR", "signature": "B", "source_mint": "MINT"},
    ]}
    role = derive_operational_role(family, infrastructure)
    assert role["current_role"] == "Provisioning Controller"
    assert [edge["relationship_type"] for edge in role["edges"]] == [
        "TREASURY_TO_SUBPROV", "SUBPROV_TO_CREATOR", "EDGE_SOURCE_MINT"
    ]
    assert role["edges"][-1]["launches"] == ["MINT"]
    assert role["observed_relationships"] == [{
        "controller": "CONTROL", "creator": "CREATOR", "launch": "MINT",
        "launch_label": "Launch", "mechanism": None, "observed_at": None,
        "transaction": "B",
    }]
    assert role["related_launch_count"] == 1


def test_shared_infrastructure_does_not_invent_operator():
    family = {
        "member_wallets": ["A", "B"], "client_wallets": ["A", "B"],
        "treasuries": ["T1", "T2"], "reconciliation": {"disposition": "UNRESOLVED"},
    }
    role = derive_operational_role(family, {"funding_paths": []})
    assert role["current_role"] == "Shared Infrastructure"
    assert role["nodes"] == [{"role": "Shared Infrastructure", "current": True}]
    assert role["edges"] == []


def test_assembler_finds_edges_by_canonical_launch_membership(tmp_path):
    ops, live = tmp_path / "ops.db", tmp_path / "live.db"
    conn = sqlite3.connect(ops)
    conn.execute("CREATE TABLE wt_provisioning_edges (edge_id TEXT,from_wallet TEXT,to_wallet TEXT,edge_type TEXT,funding_mechanism TEXT,funding_tx_signature TEXT,source_mint TEXT,first_observed_by_flex INTEGER)")
    conn.executemany("INSERT INTO wt_provisioning_edges VALUES (?,?,?,?,?,?,?,?)", [
        ("1", "T", "S", "TREASURY_TO_SUBPROV", "TRANSFER", "SIG1", "M", 1),
        ("2", "S", "C", "SUBPROV_TO_CREATOR", "TRANSFER", "SIG2", "M", 2),
    ])
    conn.commit(); conn.close(); sqlite3.connect(live).close()
    family = {
        "family_id": "canonical:test", "family_name": "Test", "launch_list": ["M"],
        "launches": 1,
        "member_wallets": [], "client_wallets": ["S"], "unique_creators": ["C"],
        "treasuries": ["T"], "funding_mechanisms": [], "observed_topology_variants": [],
        "growth_timeline": [], "first_seen_at": 1, "last_material_activity_at": 2,
        "active_sessions": 0, "lifecycle_state": "CONFIRMED", "promotion_status": "CONFIRMED",
        "evidence_completeness": {"score": 1}, "operational_maturity": {"score": 1},
        "discovery_significance": {"score": 1, "dimensions": [{"key": "recent_launch_activity", "score": 1}]},
        "reconciliation": {"disposition": "CONFIRMED_OPERATION"},
    }
    result = OperationIntelligenceAssembler(str(ops), str(live)).build(family, [family], {"total_tokens": 1})
    assert result["operational_role"]["evidence_backed"] is True
    assert result["infrastructure"]["funding_paths"][0]["source_mint"] == "M"


def test_all_three_ui_surfaces_consume_shared_role_model():
    profile = (ROOT / "templates/operation_profile.html").read_text()
    operator = (ROOT / "templates/operator_intelligence.html").read_text()
    discovery = (ROOT / "templates/discovery.html").read_text()
    assert "f.operational_role||intel.operational_role" in profile
    assert "d.family.operational_role" in operator
    assert "f.operational_role" in discovery
    assert "Typical observed funding relationships" in profile
    assert "View all '+esc(role.related_launch_count" in profile
    assert "https://solscan.io/tx/" in profile
    assert 'rel="noopener noreferrer"' in profile
    assert "Funding Tx " in profile
    assert 'Launch mint ' in profile
    assert '/token-intelligence?mint=' in profile
    assert "WATCHTOWER" not in (ROOT / "src/ops/operational_role.py").read_text()
