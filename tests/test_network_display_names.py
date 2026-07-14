import sqlite3

from src.core.network_display_names import NetworkDisplayNameBuilder


def make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE networks_release (
            network_name TEXT PRIMARY KEY,
            network_size INTEGER DEFAULT 0
        );
        CREATE TABLE upstream_network_bridge (
            upstream_address TEXT NOT NULL,
            network_a TEXT NOT NULL,
            network_b TEXT NOT NULL,
            confidence_score REAL DEFAULT 0,
            is_excluded INTEGER DEFAULT 0,
            PRIMARY KEY (upstream_address, network_a, network_b)
        );
        CREATE TABLE funder_network_map (
            funder_address TEXT PRIMARY KEY,
            network_name TEXT NOT NULL,
            creator_count INTEGER DEFAULT 0
        );
        CREATE TABLE network_membership (
            network_name TEXT NOT NULL,
            creator_address TEXT NOT NULL
        );
        CREATE TABLE creator_funders (
            creator_address TEXT NOT NULL,
            funder_address TEXT NOT NULL,
            amount_sol REAL DEFAULT 0
        );
        CREATE TABLE wallet_clusters (
            cluster_id INTEGER PRIMARY KEY,
            funder_wallet TEXT NOT NULL,
            creator_count INTEGER DEFAULT 0,
            confidence_score REAL DEFAULT 0
        );
        CREATE TABLE farm_cluster_members (
            cluster_id INTEGER NOT NULL,
            wallet_address TEXT NOT NULL,
            wallet_role TEXT NOT NULL
        );
        """
    )
    return conn


def insert_networks(conn, *names):
    conn.executemany("INSERT INTO networks_release (network_name) VALUES (?)", [(n,) for n in names])


def row(conn, network_name):
    return conn.execute(
        "SELECT network_name, display_name, display_name_reason, display_name_source FROM networks_release WHERE network_name=?",
        (network_name,),
    ).fetchone()


def test_hub_naming_wins_over_direct_funder():
    conn = make_conn()
    insert_networks(conn, "Network_134", "Network_200")
    conn.execute("INSERT INTO funder_network_map VALUES ('Dv34prABCDE', 'Network_134', 8)")
    conn.execute("INSERT INTO upstream_network_bridge VALUES ('CY1oRH8Qabcdef', 'Network_134', 'Network_200', 72, 0)")

    NetworkDisplayNameBuilder().build(conn)

    r = row(conn, "Network_134")
    assert r["display_name"] == "HubCluster-CY1oRH8Q"
    assert r["display_name_reason"] == "2H upstream hub bridges multiple networks"


def test_dominant_funder_naming_uses_creator_count():
    conn = make_conn()
    insert_networks(conn, "Network_134")
    conn.execute("INSERT INTO funder_network_map VALUES ('Small111111', 'Network_134', 2)")
    conn.execute("INSERT INTO funder_network_map VALUES ('Dominant9', 'Network_134', 9)")

    NetworkDisplayNameBuilder().build(conn)

    assert row(conn, "Network_134")["display_name"] == "FunderCluster-Dominant"


def test_wallet_cluster_fallback_works_when_no_dominant_count():
    conn = make_conn()
    insert_networks(conn, "Network_134")
    conn.execute("INSERT INTO funder_network_map VALUES ('WalletAAA', 'Network_134', 0)")
    conn.execute("INSERT INTO wallet_clusters VALUES (42, 'WalletAAA', 12, 88)")

    NetworkDisplayNameBuilder().build(conn)

    assert row(conn, "Network_134")["display_name"] == "WalletCluster-#42"


def test_fallback_network_number_format():
    conn = make_conn()
    insert_networks(conn, "Network_134")

    NetworkDisplayNameBuilder().build(conn)

    assert row(conn, "Network_134")["display_name"] == "Network-134"


def test_canonical_network_name_is_unchanged():
    conn = make_conn()
    insert_networks(conn, "Network_134")

    NetworkDisplayNameBuilder().build(conn)

    assert row(conn, "Network_134")["network_name"] == "Network_134"


def test_duplicate_display_names_get_suffixes():
    conn = make_conn()
    insert_networks(conn, "Network_134", "Network_200")
    conn.execute("INSERT INTO upstream_network_bridge VALUES ('ABCDEFGHxyz', 'Network_134', 'Network_200', 90, 0)")

    NetworkDisplayNameBuilder().build(conn)

    names = {
        r["network_name"]: r["display_name"]
        for r in conn.execute("SELECT network_name, display_name FROM networks_release")
    }
    assert names["Network_134"] == "HubCluster-ABCDEFGH"
    assert names["Network_200"] == "HubCluster-ABCDEFGH-2"


def test_builder_returns_api_ready_network_and_display_names():
    conn = make_conn()
    insert_networks(conn, "Network_134")

    result = NetworkDisplayNameBuilder().build(conn)

    assert result["Network_134"]["network_name"] == "Network_134"
    assert result["Network_134"]["display_name"] == "Network-134"
    mapped = conn.execute("SELECT network_name, display_name FROM network_display_names").fetchone()
    assert dict(mapped) == {"network_name": "Network_134", "display_name": "Network-134"}

