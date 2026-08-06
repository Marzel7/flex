import json
import sqlite3
from pathlib import Path

from src.discovery.operation_convergence import _confirmed_recoveries


ROOT = Path(__file__).resolve().parents[1]
DISCOVERY = (ROOT / "templates" / "discovery.html").read_text()
MISSION = (ROOT / "templates" / "ops_shell_index.html").read_text()
CONVERGENCE = (ROOT / "src" / "discovery" / "operation_convergence.py").read_text()


def test_discovery_order_is_recovery_centred():
    start = DISCOVERY.index("dw-recovery-console")
    section = DISCOVERY[start:]
    labels = ["Recovered Operations", "Potential Recoveries", "Investigation Activity", "Operational Signals"]
    positions = [section.index(label) for label in labels]
    assert positions == sorted(positions)


def test_recovery_explanations_do_not_render_percentages():
    renderer = DISCOVERY.split("function convergenceExpansionCard", 1)[1].split(
        "function convergenceInvestigationCard", 1
    )[0]
    assert "Recovered because" in renderer
    assert "Missing" in renderer
    assert "match_score" not in renderer
    assert "Math.round" not in renderer


def test_potential_recoveries_are_grouped_by_existing_operator():
    assert "grouped[e.matched_operator_id]" in DISCOVERY
    assert "e.recovered_because" in DISCOVERY
    assert "e.missing_evidence" in DISCOVERY
    assert "item.related_identity" not in CONVERGENCE  # matching remains in its existing engine
    assert 'href": f"/intelligence/operator/{operator_id}"' in CONVERGENCE


def test_operational_signal_taxonomy_is_retained_behind_disclosure():
    for label in (
        "Fresh Creator", "Fan-Out", "Creator Identity", "Topology Distribution",
        "Shared Infrastructure", "Funding Origin", "Behaviour",
    ):
        assert label in DISCOVERY


def test_mission_control_consumes_recovery_summary():
    for label in ("recovered today", "potential recoveries", "Treasury Reviews pending", "new Operations"):
        assert label in MISSION
    assert "/api/discovery/convergence?behaviour_limit=1" in MISSION


def test_orphan_governance_event_is_not_a_confirmed_recovery():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE operator_identity_events (operator_id TEXT,event_type TEXT,payload_json TEXT,timestamp INTEGER,evidence_revision TEXT,analyst TEXT)")
    conn.execute("CREATE TABLE operator_identity_assets (operator_id TEXT,asset_type TEXT,asset_value TEXT,status TEXT)")
    conn.execute("CREATE TABLE wt_treasury_review_actions (treasury TEXT,action TEXT)")
    payload = json.dumps({"asset_type": "TREASURY", "asset_value": "ORPHAN"})
    conn.execute("INSERT INTO operator_identity_events VALUES ('OP','TREASURY_ADDED',?,100,'manual:1','analyst')", (payload,))
    assert _confirmed_recoveries(conn, "OP") == []
    conn.execute("INSERT INTO operator_identity_assets VALUES ('OP','TREASURY','ORPHAN','ACTIVE')")
    assert _confirmed_recoveries(conn, "OP") == [
        {"timestamp": 100, "asset_type": "TREASURY", "asset_value": "ORPHAN"}
    ]


def test_backfill_event_is_not_a_recovery_even_with_active_membership():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE operator_identity_events (operator_id TEXT,event_type TEXT,payload_json TEXT,timestamp INTEGER,evidence_revision TEXT,analyst TEXT)")
    conn.execute("CREATE TABLE operator_identity_assets (operator_id TEXT,asset_type TEXT,asset_value TEXT,status TEXT)")
    conn.execute("CREATE TABLE wt_treasury_review_actions (treasury TEXT,action TEXT)")
    payload = json.dumps({"asset_type": "TREASURY", "asset_value": "BACKFILL"})
    conn.execute("INSERT INTO operator_identity_events VALUES ('OP','TREASURY_ADDED',?,100,'backfill:x76_1','system:reconciliation')", (payload,))
    conn.execute("INSERT INTO operator_identity_assets VALUES ('OP','TREASURY','BACKFILL','ACTIVE')")
    assert _confirmed_recoveries(conn, "OP") == []
