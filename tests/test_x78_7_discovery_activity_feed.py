import sqlite3
from pathlib import Path

from src.discovery.operation_convergence import _investigation_activity


ROOT = Path(__file__).resolve().parents[1]
DISCOVERY = (ROOT / "templates/discovery.html").read_text()


def test_activity_projection_reports_window_deltas_not_totals():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE wt_provisioning_edges (
      edge_type TEXT, from_wallet TEXT, to_wallet TEXT, source_mint TEXT,
      first_observed_by_flex INTEGER, last_observed_by_flex INTEGER)""")
    conn.execute("INSERT INTO wt_provisioning_edges VALUES ('SUBPROV_TO_CREATOR','CLIENT','CREATOR-1','MINT-1',190,195)")
    conn.execute("INSERT INTO wt_provisioning_edges VALUES ('SUBPROV_TO_CREATOR','CLIENT','CREATOR-OLD','MINT-OLD',50,60)")
    family = {"member_wallets": ["CLIENT"], "launches": 99, "creator_count": 88}
    activity = _investigation_activity(conn, family, 100)
    assert activity["activity_at"] == 195
    assert [item["label"] for item in activity["changes"]] == [
        "+1 launch", "+1 creator", "+1 provisioning wallet"
    ]
    assert all("99" not in item["label"] and "88" not in item["label"] for item in activity["changes"])


def test_no_activity_means_no_feed_event():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE wt_provisioning_edges (
      edge_type TEXT, from_wallet TEXT, to_wallet TEXT, source_mint TEXT,
      first_observed_by_flex INTEGER, last_observed_by_flex INTEGER)""")
    conn.execute("INSERT INTO wt_provisioning_edges VALUES ('SUBPROV_TO_CREATOR','CLIENT','CREATOR','MINT',10,20)")
    assert _investigation_activity(conn, {"member_wallets": ["CLIENT"]}, 100) is None


def test_discovery_uses_event_language_and_hides_object_totals():
    assert "Operational Activity Feed" in DISCOVERY
    assert "Investigation Activity" in DISCOVERY
    assert "Potential New Operations" not in DISCOVERY
    assert "Investigation updates" in DISCOVERY
    renderer = DISCOVERY.split("function convergenceInvestigationCard", 1)[1].split("// X75.2", 1)[0]
    assert "f.changes" in renderer
    assert "Updated " in renderer
    assert "Open Investigation →" in renderer
    assert "f.launches" not in renderer
    assert "f.creator" not in renderer
