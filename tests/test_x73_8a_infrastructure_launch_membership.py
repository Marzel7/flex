import sqlite3

from src.ops.emerging_operator_service import EmergingOperatorService
from src.ops.operation_intelligence import OperationIntelligenceAssembler


def _databases(tmp_path):
    ops, live = tmp_path / "ops.db", tmp_path / "live.db"
    conn = sqlite3.connect(ops)
    conn.execute(
        "CREATE TABLE wt_infrastructure_candidates ("
        "wallet TEXT PRIMARY KEY,candidate_role TEXT,distinct_launches INTEGER,"
        "first_seen_at INTEGER,last_seen_at INTEGER)"
    )
    conn.execute(
        "CREATE TABLE wt_walkback_edge_candidates ("
        "candidate_parent TEXT,mint TEXT,selection_status TEXT)"
    )
    conn.execute(
        "CREATE TABLE wt_walkback_queue ("
        "mint TEXT PRIMARY KEY,creator TEXT,subprov TEXT,treasury TEXT)"
    )
    conn.execute(
        "INSERT INTO wt_infrastructure_candidates VALUES "
        "('INFRA','OPERATIONAL_TREASURY',99,100,200)"
    )
    conn.executemany(
        "INSERT INTO wt_walkback_edge_candidates VALUES (?,?,?)",
        [
            ("INFRA", "MINT_A", "SELECTED"),
            ("INFRA", "MINT_A", "ALTERNATIVE"),
            ("INFRA", "MINT_B", "SELECTED"),
            ("INFRA", "MINT_C", "ALTERNATIVE"),
            ("OTHER", "MINT_D", "SELECTED"),
        ],
    )
    conn.executemany(
        "INSERT INTO wt_walkback_queue VALUES (?,?,?,?)",
        [
            ("MINT_A", "CREATOR_A", "CLIENT_A", None),
            ("MINT_B", "CREATOR_B", "CLIENT_B", None),
            ("MINT_C", "CREATOR_C", "CLIENT_C", "OTHER_TREASURY"),
        ],
    )
    conn.commit()
    conn.close()
    live_conn = sqlite3.connect(live)
    live_conn.execute(
        "CREATE TABLE token_analysis (mint TEXT,created_at INTEGER,migrated_at INTEGER,"
        "migration_tx TEXT,lifecycle_stage TEXT,market_cap_highest REAL,"
        "market_cap_current REAL,analyzed_at INTEGER)"
    )
    live_conn.executemany(
        "INSERT INTO token_analysis VALUES (?,?,?,?,?,?,?,?)",
        [
            ("MINT_A", 101, 151, "SIG_A", "migrated", 10.0, 8.0, 201),
            ("MINT_B", 102, 152, "SIG_B", "migrated", 20.0, 9.0, 202),
            ("MINT_C", 103, 153, "SIG_C", "migrated", 30.0, 7.0, 203),
        ],
    )
    live_conn.commit()
    live_conn.close()
    return ops, live


def test_selected_descendant_mints_become_canonical_population_members(tmp_path):
    ops, live = _databases(tmp_path)
    service = EmergingOperatorService(str(ops), str(live))
    with service._connect(str(ops)) as conn:
        profiles = service._discovery_profiles(conn, service._tables(conn))
    profile = profiles["INFRA"]
    assert profile["launches"] == {"MINT_A", "MINT_B"}
    assert profile["walkback_descendants"] == {"MINT_A", "MINT_B"}
    assert "MINT_C" not in profile["launches"]


def test_scalar_hint_cannot_replace_identity_membership(tmp_path):
    ops, live = _databases(tmp_path)
    service = EmergingOperatorService(str(ops), str(live))
    with service._connect(str(ops)) as conn:
        profiles = service._discovery_profiles(conn, service._tables(conn))
    population = service._population_builder().build_group([profiles["INFRA"]])
    family = service._legacy_adapter(None, []).project(population)
    assert family["launch_list"] == ["MINT_A", "MINT_B"]
    assert family["launches"] == family["observed_launches"] == 2
    assert family["walkback_descendant_count"] == 2
    assert family["walkback_descendant_list"] == ["MINT_A", "MINT_B"]
    assert "launch_count_hint" not in population.metadata


def test_established_membership_sources_are_not_broadened_by_descendants(tmp_path):
    ops, live = _databases(tmp_path)
    conn = sqlite3.connect(ops)
    conn.execute(
        "CREATE TABLE wt_provisioning_edges (edge_type TEXT,from_wallet TEXT,"
        "to_wallet TEXT,funding_mechanism TEXT,funding_tx_signature TEXT,"
        "source_mint TEXT,funding_amount_sol REAL,first_observed_by_flex INTEGER,"
        "last_observed_by_flex INTEGER)"
    )
    conn.execute(
        "INSERT INTO wt_provisioning_edges VALUES "
        "('SUBPROV_TO_CREATOR','INFRA','CREATOR','PLAIN','SIG','ESTABLISHED',"
        "1.0,100,200)"
    )
    conn.commit()
    conn.close()
    service = EmergingOperatorService(str(ops), str(live))
    with service._connect(str(ops)) as read_conn:
        profiles = service._discovery_profiles(read_conn, service._tables(read_conn))
    assert profiles["INFRA"]["launches"] == {"ESTABLISHED"}
    assert profiles["INFRA"]["walkback_descendants"] == set()


def test_profile_rows_timeline_and_statistics_consume_launch_list(tmp_path):
    ops, live = _databases(tmp_path)
    family = {
        "family_id": "family:test", "family_name": "Infrastructure Test",
        "launches": 2, "launch_list": ["MINT_A", "MINT_B"],
        "member_wallets": ["INFRA"], "client_wallets": ["INFRA"],
        "unique_creators": [], "treasuries": [], "funding_mechanisms": [],
        "observed_topology_variants": [], "growth_timeline": [],
        "first_seen_at": 100, "last_material_activity_at": 202,
        "active_sessions": 0, "lifecycle_state": "BACKGROUND",
        "promotion_status": "BLOCKED", "evidence_completeness": {"score": 0},
        "discovery_significance": {
            "score": 0,
            "dimensions": [{"key": "recent_launch_activity", "score": 0}],
        },
        "operational_maturity": {"score": 0},
    }
    intelligence = OperationIntelligenceAssembler(str(ops), str(live)).build(
        family, [family], {"launches": {}}
    )
    assert {row["mint"] for row in intelligence["performance"]["launches"]} == {
        "MINT_A", "MINT_B"
    }
    assert intelligence["performance"]["total_launches"] == 2
    launch_events = [
        row for row in intelligence["timeline"] if row.get("type") == "LAUNCH_OBSERVED"
    ]
    assert {row["mint"] for row in launch_events} == {"MINT_A", "MINT_B"}
    assert all(row["source"] == "token_analysis" for row in launch_events)


def test_exclusive_accounting_deduplicates_reconstructed_membership(tmp_path):
    ops, live = _databases(tmp_path)
    service = EmergingOperatorService(str(ops), str(live))
    families = [
        {"family_id": "family:a", "launch_list": ["MINT_A", "MINT_B"]},
        {"family_id": "family:b", "launch_list": ["MINT_B"]},
    ]
    reconciled = service._reconcile_disposition_states(
        families,
        {
            "family:a": {"disposition": "UNRESOLVED"},
            "family:b": {"disposition": "INFRASTRUCTURE"},
        },
    )
    assert reconciled["total_launches"] == 2
    assert reconciled["assigned_launches"] == 2
    assert reconciled["source_overlap_count"] == 1
    assert reconciled["launch_counts"]["INFRASTRUCTURE"] == 1
    assert reconciled["launch_counts"]["UNRESOLVED"] == 1
