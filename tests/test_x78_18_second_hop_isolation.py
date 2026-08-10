"""X78.18 second-hop read/materialization write-lane isolation."""

from __future__ import annotations

import shutil
import sqlite3
import threading
import time

import pytest

from src.core.second_hop_builder import SecondHopExpansionBuilder
from src.utils.infra_mapping import build_excluded_set


def _seed(path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE listener_settings(setting_key TEXT PRIMARY KEY, setting_value TEXT);
        INSERT INTO listener_settings VALUES('second_hop_sql_enabled','true');
        CREATE TABLE infra_wallets(address TEXT PRIMARY KEY);
        CREATE TABLE cex_wallets(cex_address TEXT, is_active INTEGER);
        CREATE TABLE infra_funders_observed(funder_address TEXT);
        CREATE TABLE shl_excluded_upstreams(address TEXT PRIMARY KEY);
        CREATE TABLE token_analysis(
            bonding_curve_pda TEXT, pool_address TEXT, pumpswap_pool_address TEXT
        );
        CREATE TABLE transfer_index(
            signature TEXT, source TEXT, destination TEXT,
            amount_lamports INTEGER,
            amount_sol REAL GENERATED ALWAYS AS (amount_lamports / 1e9) STORED,
            block_time INTEGER, indexed_at REAL, is_valid INTEGER
        );
        CREATE TABLE creator_funders(
            creator_address TEXT, funder_address TEXT, amount_sol REAL, is_cex INTEGER
        );
        CREATE TABLE funder_network_map(funder_address TEXT, network_name TEXT);
        CREATE TABLE wallet_clusters(funder_wallet TEXT);
        CREATE TABLE farm_cluster_members(wallet_address TEXT);
        CREATE TABLE funder_upstream_links(
            funder_address TEXT, upstream_address TEXT, transfer_count INTEGER,
            total_sol REAL, avg_transfer_sol REAL, first_transfer_ts INTEGER,
            last_transfer_ts INTEGER, source TEXT, is_excluded INTEGER,
            funders_touched INTEGER, last_seen_network_count INTEGER DEFAULT 0,
            built_at INTEGER,
            PRIMARY KEY(funder_address, upstream_address)
        );
        CREATE TABLE upstream_network_bridge(
            upstream_address TEXT, network_a TEXT, network_b TEXT,
            shared_funders INTEGER, confidence_score REAL, risk_level TEXT,
            reason_codes TEXT, is_excluded INTEGER, built_at INTEGER,
            time_span_seconds INTEGER, funders_bridged_count INTEGER,
            PRIMARY KEY(upstream_address, network_a, network_b)
        );
        CREATE TABLE creator_second_hop(
            creator_address TEXT, upstream_address TEXT, via_funder TEXT,
            confidence_score REAL, risk_level TEXT, reason_codes TEXT,
            source TEXT, built_at INTEGER,
            PRIMARY KEY(creator_address, upstream_address, via_funder)
        );
        CREATE TABLE monitored_upstream_hubs(
            upstream_address TEXT PRIMARY KEY, confidence_score REAL,
            networks_bridged INTEGER, funders_bridged INTEGER, risk_level TEXT,
            reason_codes TEXT, status TEXT, discovered_at INTEGER,
            last_expanded_at INTEGER
        );
        CREATE TABLE writer_probe(value TEXT);
    """)
    now = int(time.time())
    conn.executemany(
        "INSERT INTO creator_funders VALUES(?,?,1.0,0)",
        [("creator-a", "funder-a"), ("creator-b", "funder-b")],
    )
    conn.executemany(
        "INSERT INTO funder_network_map VALUES(?,?)",
        [("funder-a", "network-a"), ("funder-b", "network-b")],
    )
    conn.executemany(
        "INSERT INTO transfer_index"
        "(signature,source,destination,amount_lamports,block_time,indexed_at,is_valid) "
        "VALUES(?,?,?,?,?,?,1)",
        [
            ("sig-a", "upstream", "funder-a", 1_000_000_000, now, now),
            ("sig-b", "upstream", "funder-b", 2_000_000_000, now + 1, now),
        ],
    )
    conn.commit()
    conn.close()


def _rows(path, table):
    conn = sqlite3.connect(path)
    columns = [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
    keep = [column for column in columns if column not in {"built_at", "discovered_at"}]
    result = conn.execute(
        f"SELECT {','.join(keep)} FROM {table} ORDER BY {','.join(keep)}"
    ).fetchall()
    conn.close()
    return result


def _legacy_build(path) -> None:
    builder = SecondHopExpansionBuilder(str(path))
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    builder._apply_span_migration(conn)
    builder._apply_hub_migration(conn)
    excluded = build_excluded_set(conn)
    builder._exclude_infra_links(conn)
    builder._build_upstream_links(conn, excluded)
    builder._build_network_bridges(conn, excluded)
    builder._build_creator_second_hop(conn)
    conn.commit()
    conn.close()


def test_materialization_does_not_block_an_unrelated_writer(tmp_path, monkeypatch):
    path = tmp_path / "second-hop.db"
    _seed(path)
    builder = SecondHopExpansionBuilder(str(path))
    completed = threading.Event()

    def materializing(_conn, _excluded):
        def writer():
            write = sqlite3.connect(path, timeout=2)
            write.execute("INSERT INTO writer_probe VALUES('completed')")
            write.commit()
            write.close()
            completed.set()

        thread = threading.Thread(target=writer)
        thread.start()
        assert completed.wait(1.5), "read materialization held the global write lane"
        thread.join()
        return 0

    monkeypatch.setattr(builder, "_build_upstream_links", materializing)
    monkeypatch.setattr(builder, "_build_network_bridges", lambda *_: 0)
    monkeypatch.setattr(builder, "_build_creator_second_hop", lambda *_: 0)
    builder._materialize_read_snapshot()


def test_materialized_output_matches_legacy_builder(tmp_path):
    legacy = tmp_path / "legacy.db"
    isolated = tmp_path / "isolated.db"
    _seed(legacy)
    shutil.copy2(legacy, isolated)

    _legacy_build(legacy)
    result = SecondHopExpansionBuilder(str(isolated)).build()
    assert result["status"] == "success"

    for table in (
        "funder_upstream_links",
        "upstream_network_bridge",
        "creator_second_hop",
        "monitored_upstream_hubs",
    ):
        assert _rows(isolated, table) == _rows(legacy, table)


def test_prewrite_failure_preserves_existing_generation(tmp_path, monkeypatch):
    path = tmp_path / "second-hop.db"
    _seed(path)
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO creator_second_hop VALUES"
        "('old-creator','old-upstream','old-funder',50,'MEDIUM','[]','old',1)"
    )
    conn.commit()
    conn.close()
    builder = SecondHopExpansionBuilder(str(path))
    monkeypatch.setattr(
        builder, "_materialize_read_snapshot", lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    assert builder.build()["status"] == "failed"
    assert _rows(path, "creator_second_hop") == [
        ("old-creator", "old-upstream", "old-funder", 50.0, "MEDIUM", "[]", "old")
    ]


def test_write_failure_rolls_back_complete_generation(tmp_path):
    path = tmp_path / "second-hop.db"
    _seed(path)
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO creator_second_hop VALUES"
        "('old-creator','old-upstream','old-funder',50,'MEDIUM','[]','old',1)"
    )
    conn.commit()
    conn.close()
    builder = SecondHopExpansionBuilder(str(path))
    bad = {
        "links": (["missing_column"], [("bad",)]),
        "bridges": ([], []), "hops": ([], []), "hubs": ([], []),
        "counts": (0, 0, 0),
    }
    with pytest.raises(sqlite3.OperationalError):
        builder._replace_materialized(bad)
    assert _rows(path, "creator_second_hop") == [
        ("old-creator", "old-upstream", "old-funder", 50.0, "MEDIUM", "[]", "old")
    ]


def test_repeated_rebuild_is_idempotent(tmp_path):
    path = tmp_path / "second-hop.db"
    _seed(path)
    builder = SecondHopExpansionBuilder(str(path))
    assert builder.build()["status"] == "success"
    first = {
        table: _rows(path, table)
        for table in ("funder_upstream_links", "upstream_network_bridge", "creator_second_hop")
    }
    assert builder.build()["status"] == "success"
    assert first == {
        table: _rows(path, table)
        for table in ("funder_upstream_links", "upstream_network_bridge", "creator_second_hop")
    }
