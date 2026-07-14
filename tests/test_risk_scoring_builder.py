import sqlite3

from src.core.risk_scoring_builder import RiskScoringBuilder


AXIOM = "AxiomRXZAq1Jgjj9pHmNqVP7Lhu67wLXZJZbaK87TTSk"


def _db(tmp_path):
    path = tmp_path / "risk.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE network_membership (network_name TEXT, creator_address TEXT);
        CREATE TABLE funder_network_map (network_name TEXT, funder_address TEXT);
        CREATE TABLE networks_release (network_name TEXT PRIMARY KEY, network_size INTEGER, network_type TEXT);
        CREATE TABLE creator_funders (creator_address TEXT, funder_address TEXT, amount_sol REAL, is_cex INTEGER DEFAULT 0);
        CREATE TABLE creator_self_funding (
            creator_address TEXT PRIMARY KEY,
            is_self_funding INTEGER,
            self_funding_percentage REAL,
            self_funding_intermediates INTEGER,
            total_funders INTEGER
        );
        CREATE TABLE coordinated_creator_edges (creator_a TEXT, creator_b TEXT, bridge_funder TEXT, confidence REAL);
        CREATE TABLE creator_second_hop (creator_address TEXT, upstream_address TEXT, confidence_score REAL);
        CREATE TABLE funder_upstream_links (funder_address TEXT, upstream_address TEXT, confidence_score REAL);
        CREATE TABLE upstream_network_bridge (network_name TEXT, upstream_address TEXT, confidence_score REAL);
        CREATE TABLE creator_outgoing_transfers (creator_address TEXT, recipient_address TEXT, amount_sol REAL);
        CREATE TABLE creator_outbound_classifications (
            creator_address TEXT,
            recipient_address TEXT,
            relationship_type TEXT,
            amount_sol REAL
        );
        CREATE TABLE wallet_clusters (cluster_id INTEGER, funder_wallet TEXT);
        CREATE TABLE farm_clusters (farm_cluster_id INTEGER);
        CREATE TABLE farm_cluster_members (farm_cluster_id INTEGER, wallet_address TEXT, wallet_role TEXT);
        CREATE TABLE creator_tags (creator_address TEXT, tag TEXT);
        CREATE TABLE creator_c2c_edges (source_creator TEXT, dest_creator TEXT);
        CREATE TABLE token_analysis (
            mint TEXT PRIMARY KEY,
            earliest_tx_creator TEXT,
            market_cap_highest REAL,
            market_cap_current REAL,
            created_at INTEGER,
            migrated_at INTEGER,
            market_cap_highest_at_ts INTEGER,
            lifecycle_stage TEXT,
            bonding_curve_pda TEXT,
            pool_address TEXT,
            pumpswap_pool_address TEXT
        );
        CREATE TABLE token_pool_accounts (mint TEXT, liquidity_removed INTEGER, liquidity_removed_at INTEGER);
        """
    )
    conn.commit()
    conn.close()
    return path


def _add_token(conn, creator, idx, peak=10_000, migrated=True, liq=False):
    minted_at = 1_700_000_000 + idx * 100
    mint = f"{creator}_{idx}"
    conn.execute(
        """
        INSERT INTO token_analysis (
            mint, earliest_tx_creator, market_cap_highest, market_cap_current,
            created_at, migrated_at, market_cap_highest_at_ts, lifecycle_stage
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            mint,
            creator,
            peak,
            peak / 2,
            minted_at,
            minted_at + 10 if migrated else None,
            minted_at + 30,
            "migrated" if migrated else "bonding_curve",
        ),
    )
    if liq:
        conn.execute(
            "INSERT INTO token_pool_accounts (mint, liquidity_removed, liquidity_removed_at) VALUES (?, 1, ?)",
            (mint, minted_at + 60),
        )


def _run(path):
    result = RiskScoringBuilder(str(path)).run()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return result, conn


def test_self_funding_creator_is_critical(tmp_path):
    path = _db(tmp_path)
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO creator_self_funding VALUES ('C_SELF', 1, 100, 2, 2)")
    for i in range(3):
        _add_token(conn, "C_SELF", i, peak=12_000)
    conn.commit()
    conn.close()

    _, conn = _run(path)
    row = conn.execute("SELECT * FROM creator_risk_scores WHERE creator_address='C_SELF'").fetchone()
    assert row["category"] == "SELF_FUNDING_FARM"
    assert row["risk_level"] == "CRITICAL"
    assert row["final_score"] >= 80


def test_serial_migrator_scores_without_network(tmp_path):
    path = _db(tmp_path)
    conn = sqlite3.connect(path)
    for i in range(10):
        _add_token(conn, "C_SERIAL", i, peak=20_000)
    conn.commit()
    conn.close()

    _, conn = _run(path)
    row = conn.execute("SELECT * FROM creator_risk_scores WHERE creator_address='C_SERIAL'").fetchone()
    assert row["category"] == "SERIAL_DUMPER"
    assert row["migrated_tokens"] == 10
    assert row["g7_percentage"] == 100


def test_infra_only_funder_is_ignored(tmp_path):
    path = _db(tmp_path)
    conn = sqlite3.connect(path)
    conn.executemany(
        "INSERT INTO creator_funders (creator_address, funder_address, amount_sol, is_cex) VALUES (?, ?, 1, 0)",
        [("C1", AXIOM), ("C2", AXIOM)],
    )
    conn.execute("INSERT INTO networks_release VALUES ('Network_INFRA', 2, 'infra_only')")
    conn.executemany("INSERT INTO network_membership VALUES ('Network_INFRA', ?)", [("C1",), ("C2",)])
    conn.commit()
    conn.close()

    _, conn = _run(path)
    assert conn.execute("SELECT final_score FROM creator_risk_scores WHERE creator_address='C1'").fetchone()[0] == 0
    net = conn.execute("SELECT * FROM network_risk_scores WHERE network_name='Network_INFRA'").fetchone()
    assert net["category"] == "NOISE_OR_INFRA"


def test_large_non_infra_network_becomes_confirmed(tmp_path):
    path = _db(tmp_path)
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO networks_release VALUES ('Network_REAL', 6, 'organic')")
    creators = [f"C{i}" for i in range(6)]
    for creator in creators:
        conn.execute("INSERT INTO network_membership VALUES ('Network_REAL', ?)", (creator,))
        conn.execute(
            "INSERT INTO creator_funders (creator_address, funder_address, amount_sol, is_cex) VALUES (?, 'F_REAL', 2, 0)",
            (creator,),
        )
        conn.execute(
            "INSERT INTO creator_second_hop VALUES (?, 'UPSTREAM_REAL', 80)",
            (creator,),
        )
        _add_token(conn, creator, int(creator[1:]), peak=30_000)
    for i, a in enumerate(creators):
        for b in creators[i + 1 :]:
            conn.execute("INSERT INTO coordinated_creator_edges VALUES (?, ?, 'F_REAL', 90)", (a, b))
    conn.commit()
    conn.close()

    _, conn = _run(path)
    net = conn.execute("SELECT * FROM network_risk_scores WHERE network_name='Network_REAL'").fetchone()
    assert net["category"] == "CONFIRMED_OPERATOR_GROUP"
    assert net["operator_score"] >= 90


def test_liquidation_and_history_are_recorded(tmp_path):
    path = _db(tmp_path)
    conn = sqlite3.connect(path)
    for i in range(3):
        _add_token(conn, "C_LIQ", i, peak=25_000, liq=True)
    conn.commit()
    conn.close()

    result, conn = _run(path)
    row = conn.execute("SELECT * FROM creator_risk_scores WHERE creator_address='C_LIQ'").fetchone()
    history = conn.execute(
        "SELECT COUNT(*) FROM risk_score_history WHERE entity_type='creator' AND entity_id='C_LIQ'"
    ).fetchone()[0]
    assert result["status"] == "success"
    assert row["category"] == "LIQUIDITY_EXTRACTOR"
    assert row["liquidation_count"] == 3
    assert row["liquidation_score"] > 0
    assert history == 1
