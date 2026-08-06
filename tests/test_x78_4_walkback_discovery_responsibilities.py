import sqlite3
from pathlib import Path

from src.ops.emerging_operator_service import EmergingOperatorService


ROOT = Path(__file__).resolve().parents[1]


def test_selected_repeated_review_topology_becomes_discovery_profile(tmp_path):
    db = tmp_path / "ops.db"
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
    CREATE TABLE wt_treasury_review (
      treasury TEXT, status TEXT, has_walkback_evidence INTEGER,
      distinct_subprovs INTEGER, distinct_creators INTEGER
    );
    CREATE TABLE wt_walkback_edge_candidates (
      mint TEXT, wallet TEXT, candidate_parent TEXT, signature TEXT,
      mechanism TEXT, block_time INTEGER, selection_status TEXT
    );
    CREATE TABLE wt_provisioning_edges (
      edge_type TEXT, source_mint TEXT, from_wallet TEXT, to_wallet TEXT,
      funding_mechanism TEXT, funding_tx_signature TEXT
    );
    INSERT INTO wt_treasury_review VALUES ('CiyEB','PENDING_REVIEW',1,2,2);
    INSERT INTO wt_walkback_edge_candidates VALUES ('mint-1','sub-1','CiyEB','up-1','WSOL_WRAP_CLOSE',10,'SELECTED');
    INSERT INTO wt_walkback_edge_candidates VALUES ('mint-2','sub-2','CiyEB','up-2','WSOL_WRAP_CLOSE',20,'SELECTED');
    INSERT INTO wt_provisioning_edges VALUES ('SUBPROV_TO_CREATOR','mint-1','sub-1','creator-1','PLAIN_XFER','down-1');
    INSERT INTO wt_provisioning_edges VALUES ('SUBPROV_TO_CREATOR','mint-2','sub-2','creator-2','PLAIN_XFER','down-2');
    """)
    conn.commit()
    service = EmergingOperatorService(str(db), str(tmp_path / "live.db"))
    profiles = service._discovery_profiles(conn, service._tables(conn))
    profile = profiles["CiyEB"]
    assert profile["sources"] == {"wt_treasury_review_operational_like"}
    assert profile["launches"] == {"mint-1", "mint-2"}
    assert profile["provisioning_clients"] == {"sub-1", "sub-2"}
    assert profile["creators"] == {"creator-1", "creator-2"}
    assert profile["treasuries"] == {"CiyEB"}


def test_responsibility_contract_keeps_provisional_population_out_of_registry():
    source = (ROOT / "src/ops/emerging_operator_service.py").read_text()
    convergence = (ROOT / "src/discovery/operation_convergence.py").read_text()
    assert 'family["projection_scope"] = "DISCOVERY_ONLY"' in source
    assert 'family["registry_eligible"] = False' in source
    assert '"potential_operations_reconciled": potential_operation_cards' in source
    assert 'list_payload.get("potential_operations_reconciled")' in convergence
    assert '"CONFIRMED_OPERATION": 0' in convergence
    assert '"OPERATIONAL_LIKE": 0' in convergence
    assert '"RESIDUAL_ECOSYSTEM_INTELLIGENCE": 0' in convergence


def test_projection_uses_no_rpc():
    source = (ROOT / "src/ops/emerging_operator_service.py").read_text()
    block = source.split("# X78.4:", 1)[1].split("# Infrastructure candidates carry", 1)[0]
    assert "HELIUS" not in block
    assert "getTransaction" not in block
    assert "getSignaturesForAddress" not in block
