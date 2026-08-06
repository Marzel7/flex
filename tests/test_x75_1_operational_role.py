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
        "TREASURY_TO_SUBPROV", "SUBPROV_TO_CREATOR", "WALKBACK_SOURCE_MINT"
    ]
    assert role["edges"][-1]["launches"] == ["MINT"]
    assert role["observed_relationships"] == [{
        "controller": "CONTROL", "creator": "CREATOR", "launch": "MINT",
        "launch_label": "Launch", "mechanism": None, "observed_at": None,
        "transaction_at": None,
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


def test_operational_treasury_rows_require_a_complete_recorded_chain():
    family = {
        "family_anchor": "TREASURY", "member_wallets": ["TREASURY"],
        "client_wallets": ["SUBPROV"], "treasuries": ["TREASURY"],
        "launch_list": ["MINT"], "reconciliation": {"disposition": "UNRESOLVED"},
    }
    infrastructure = {"funding_paths": [
        {"from": "TREASURY", "to": "SUBPROV", "type": "TREASURY_TO_SUBPROV",
         "signature": "TREASURY_TX", "transaction_at": 100, "source_mint": "MINT"},
        {"from": "SUBPROV", "to": "CREATOR", "type": "SUBPROV_TO_CREATOR",
         "signature": "CREATOR_TX", "transaction_at": 200, "source_mint": "MINT"},
        {"from": "OTHER", "to": "UNRELATED", "type": "SUBPROV_TO_CREATOR",
         "signature": "WRONG_TX", "transaction_at": 300, "source_mint": "OTHER_MINT"},
    ]}

    role = derive_operational_role(family, infrastructure)

    assert role["current_role"] == "Operational Treasury"
    assert role["related_launch_count"] == 1
    assert role["observed_relationships"][0]["controller"] == "TREASURY"
    assert role["observed_relationships"][0]["intermediary"] == "SUBPROV"
    assert role["observed_relationships"][0]["creator"] == "CREATOR"
    assert [hop["transaction"] for hop in role["observed_relationships"][0]["funding_hops"]] == [
        "TREASURY_TX", "CREATOR_TX",
    ]


def test_direct_creator_funding_takes_controller_role_over_scalar_treasury_membership():
    family = {
        "family_anchor": "CONTROL", "member_wallets": ["CONTROL"],
        "client_wallets": ["CONTROL"], "treasuries": ["CONTROL"],
        "launch_list": ["MINT"], "reconciliation": {"disposition": "REVIEW"},
    }
    infrastructure = {"funding_paths": [{
        "from": "CONTROL", "to": "CREATOR", "type": "SUBPROV_TO_CREATOR",
        "signature": "DIRECT_TX", "transaction_at": 200, "source_mint": "MINT",
        "mechanism": "PLAIN_XFER",
    }]}

    role = derive_operational_role(family, infrastructure)

    assert role["current_role"] == "Provisioning Controller"
    assert role["observed_relationships"] == [{
        "controller": "CONTROL", "creator": "CREATOR", "launch": "MINT",
        "launch_label": "Launch", "mechanism": "PLAIN_XFER", "observed_at": None,
        "transaction_at": 200, "transaction": "DIRECT_TX",
    }]


def test_assembler_finds_edges_by_canonical_launch_membership(tmp_path):
    ops, live = tmp_path / "ops.db", tmp_path / "live.db"
    conn = sqlite3.connect(ops)
    conn.execute("CREATE TABLE wt_provisioning_edges (edge_id TEXT,from_wallet TEXT,to_wallet TEXT,edge_type TEXT,funding_mechanism TEXT,funding_tx_signature TEXT,source_mint TEXT,first_observed_by_flex INTEGER,funding_block_time INTEGER)")
    conn.executemany("INSERT INTO wt_provisioning_edges VALUES (?,?,?,?,?,?,?,?,?)", [
        ("1", "T", "S", "TREASURY_TO_SUBPROV", "TRANSFER", "SIG1", "M", 1, 1700000010),
        ("2", "S", "C", "SUBPROV_TO_CREATOR", "TRANSFER", "SIG2", "M", 2, 1700000020),
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
    assert result["infrastructure"]["funding_paths"][0]["transaction_at"] == 1700000010


def test_all_three_ui_surfaces_consume_shared_role_model():
    profile = (ROOT / "templates/operation_profile.html").read_text()
    operator = (ROOT / "templates/operator_intelligence.html").read_text()
    discovery = (ROOT / "templates/discovery.html").read_text()
    assert "f.operational_role||intel.operational_role" in profile
    assert "d.family.operational_role" in operator
    assert "f.operational_role" in discovery
    assert "Typical observed funding relationships" in profile
    assert "View all '+esc(observations.length)" in profile
    assert 'https://solscan.io/account/' in profile
    assert 'title="Token mint ' in profile
    assert "tokenNode=o.launch" in profile
    assert "observations.slice(3).map(observationHtml)" in profile
    assert 'details class="rp-role-all"' in profile
    assert "https://solscan.io/tx/" in profile
    assert 'rel="noopener noreferrer"' in profile
    assert "Funding Tx " in profile
    assert 'Launch mint ' in profile
    assert '/token-intelligence?mint=' in profile
    assert "Observed while tracing " in profile
    assert "h.transaction_at?new Date(h.transaction_at*1000)" in profile
    assert "o.intermediary" in profile
    assert "o.funding_hops" in profile
    assert ".sort((a,b)=>(b.transaction_at||0)-(a.transaction_at||0))" in profile
    assert "WATCHTOWER" not in (ROOT / "src/ops/operational_role.py").read_text()
