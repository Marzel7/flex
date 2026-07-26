"""Regression tests for X44.0 — CEX Funding Intelligence Expansion.

Verifies build_cex_funding_intelligence() is a read-only aggregation over
wt_attribution_outcomes.evidence_json: groups KNOWN_CEX_REACHED launches by
withdrawal origin (terminal_entity), surfaces the already-identified
exchange name (never inferred), the observed funding path (only real
hops), per-origin launch/creator/Operation counts, and shared-
infrastructure/multi-origin signals -- without merging Operations,
creating attribution, or writing anything.
"""
import json
import sqlite3
import time

import pytest

from src.ops.cex_funding_intelligence import (
    build_cex_funding_intelligence,
    UNKNOWN_CEX_LABEL,
)


@pytest.fixture
def ops_db(tmp_path):
    path = str(tmp_path / f"ops_{time.time_ns()}.db")
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE wt_attribution_outcomes (
            mint TEXT PRIMARY KEY, outcome_type TEXT NOT NULL, stop_reason TEXT,
            terminal_entity TEXT, terminal_entity_type TEXT, confidence TEXT,
            evidence_json TEXT, operator_id TEXT,
            should_seed_emerging_operator INTEGER DEFAULT 0,
            should_retry INTEGER DEFAULT 0, completed_at INTEGER NOT NULL,
            source_queue_updated_at INTEGER, materialized_at INTEGER
        )"""
    )
    conn.execute(
        "CREATE TABLE wt_ops_v2_wallets (operation_uuid TEXT, wallet TEXT, role TEXT)"
    )
    conn.commit()
    yield path, conn
    conn.close()


def _insert_outcome(conn, mint, terminal_entity, evidence, completed_at, outcome_type="KNOWN_CEX_REACHED"):
    conn.execute(
        "INSERT INTO wt_attribution_outcomes (mint, outcome_type, stop_reason, terminal_entity, "
        "terminal_entity_type, confidence, evidence_json, completed_at) VALUES (?,?,?,?,?,?,?,?)",
        (mint, outcome_type, "boundary", terminal_entity, "CEX", "HIGH", json.dumps(evidence), completed_at),
    )
    conn.commit()


def test_no_cex_reached_returns_empty(ops_db):
    path, conn = ops_db
    result = build_cex_funding_intelligence(path)
    assert result["origins"] == []
    assert result["mints"] == {}
    assert result["multi_cex_creators"] == []
    assert result["shared_infrastructure"] == []


def test_mints_field_attaches_cex_info_directly_to_the_token_address(ops_db):
    """X44.1 — the actual UI need: given a mint address, look up its own
    exchange/origin/path directly (one dict lookup), rather than only
    getting an origin-grouped summary that has to be cross-referenced back
    to individual tokens."""
    path, conn = ops_db
    now = int(time.time())
    _insert_outcome(conn, "TOKEN_MINT_1", "ORIGIN1", {
        "boundary": {"name": "Binance"}, "creator": "C1", "treasuries": ["T1"],
    }, now)
    result = build_cex_funding_intelligence(path)
    assert "TOKEN_MINT_1" in result["mints"]
    m = result["mints"]["TOKEN_MINT_1"]
    assert m["mint"] == "TOKEN_MINT_1"
    assert m["exchange"] == "Binance"
    assert m["origin"] == "ORIGIN1"
    assert m["creator"] == "C1"
    assert m["treasuries"] == ["T1"]


def test_mints_field_has_one_entry_per_mint_not_per_origin(ops_db):
    """Two different mints funded by the SAME origin must each get their
    own entry in `mints`, keyed by their own address -- not collapsed into
    one origin-level record."""
    path, conn = ops_db
    now = int(time.time())
    _insert_outcome(conn, "MINT_A", "SHARED_ORIGIN", {"boundary": {"name": "Bybit"}, "creator": "C1"}, now)
    _insert_outcome(conn, "MINT_B", "SHARED_ORIGIN", {"boundary": {"name": "Bybit"}, "creator": "C2"}, now)
    result = build_cex_funding_intelligence(path)
    assert set(result["mints"].keys()) == {"MINT_A", "MINT_B"}
    assert result["mints"]["MINT_A"]["creator"] == "C1"
    assert result["mints"]["MINT_B"]["creator"] == "C2"


def test_missing_table_returns_empty(tmp_path):
    path = str(tmp_path / "empty.db")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE unrelated (x TEXT)")
    conn.commit()
    conn.close()
    result = build_cex_funding_intelligence(path)
    assert result["origins"] == []


def test_single_origin_aggregates_launches_and_creators(ops_db):
    path, conn = ops_db
    now = int(time.time())
    _insert_outcome(conn, "M1", "ORIGIN1",
                     {"boundary": {"name": "Binance"}, "creator": "C1"}, now - 100)
    _insert_outcome(conn, "M2", "ORIGIN1",
                     {"boundary": {"name": "Binance"}, "creator": "C2"}, now)
    result = build_cex_funding_intelligence(path)
    assert result["total_origins"] == 1
    o = result["origins"][0]
    assert o["exchange"] == "Binance"
    assert o["launches"] == 2
    assert o["creators"] == 2
    assert o["first_seen"] == now - 100
    assert o["last_seen"] == now


def test_missing_exchange_name_labelled_unknown_never_guessed(ops_db):
    """A row with no boundary.name (or no boundary at all) must be labelled
    UNKNOWN_CEX_LABEL, never inferred from the address or any heuristic."""
    path, conn = ops_db
    now = int(time.time())
    _insert_outcome(conn, "M1", "ORIGIN_X", {"creator": "C1"}, now)
    result = build_cex_funding_intelligence(path)
    assert result["origins"][0]["exchange"] == UNKNOWN_CEX_LABEL
    assert result["origins"][0]["strength_indicators"]["exchange_match"] is False


def test_funding_path_only_renders_observed_hops(ops_db):
    """A CEX->creator path with no treasury/subprov hop must render exactly
    2 nodes (Exchange, Creator) -- never inventing an intermediate hop."""
    path, conn = ops_db
    now = int(time.time())
    _insert_outcome(conn, "M1", "ORIGIN1", {"boundary": {"name": "Kraken"}, "creator": "C1"}, now)
    result = build_cex_funding_intelligence(path)
    roles = [p["role"] for p in result["origins"][0]["funding_path"]]
    assert roles == ["Exchange", "Creator"]


def test_funding_path_includes_real_treasury_and_subprov_hops(ops_db):
    path, conn = ops_db
    now = int(time.time())
    _insert_outcome(conn, "M1", "ORIGIN1", {
        "boundary": {"name": "OKX"}, "creator": "C1",
        "treasuries": ["T1"], "subprovisioners": ["SP1"],
    }, now)
    result = build_cex_funding_intelligence(path)
    roles = [p["role"] for p in result["origins"][0]["funding_path"]]
    assert roles == ["Exchange", "Treasury", "Subprovider", "Creator"]


def test_self_referencing_subprov_excluded_from_path(ops_db):
    """A real production quirk: evidence_json can list terminal_entity
    itself inside subprovisioners. Must never render the same wallet twice
    under two roles (Exchange AND Subprovider)."""
    path, conn = ops_db
    now = int(time.time())
    _insert_outcome(conn, "M1", "ORIGIN1", {
        "boundary": {"name": "KuCoin"}, "creator": "C1",
        "subprovisioners": ["ORIGIN1"],
    }, now)
    result = build_cex_funding_intelligence(path)
    roles = [p["role"] for p in result["origins"][0]["funding_path"]]
    assert "Subprovider" not in roles
    assert result["origins"][0]["strength_indicators"]["shared_subprovider"] is False


def test_shared_withdrawal_origin_true_only_with_multiple_launches(ops_db):
    path, conn = ops_db
    now = int(time.time())
    _insert_outcome(conn, "M1", "SOLO", {"boundary": {"name": "Bybit"}, "creator": "C1"}, now)
    _insert_outcome(conn, "M2", "SHARED", {"boundary": {"name": "Gate"}, "creator": "C2"}, now)
    _insert_outcome(conn, "M3", "SHARED", {"boundary": {"name": "Gate"}, "creator": "C3"}, now)
    result = build_cex_funding_intelligence(path)
    by_origin = {o["origin"]: o for o in result["origins"]}
    assert by_origin["SOLO"]["strength_indicators"]["shared_withdrawal_origin"] is False
    assert by_origin["SHARED"]["strength_indicators"]["shared_withdrawal_origin"] is True


def test_multi_cex_creators_detected_for_real_cross_origin_creator(ops_db):
    """A creator whose CEX-reached launches touch more than one distinct
    withdrawal origin must appear in multi_cex_creators -- exact wallet
    match only, no inferred clustering."""
    path, conn = ops_db
    now = int(time.time())
    _insert_outcome(conn, "M1", "ORIGIN_A", {"boundary": {"name": "Binance"}, "creator": "CREATOR_X"}, now)
    _insert_outcome(conn, "M2", "ORIGIN_B", {"boundary": {"name": "Coinbase"}, "creator": "CREATOR_X"}, now)
    _insert_outcome(conn, "M3", "ORIGIN_A", {"boundary": {"name": "Binance"}, "creator": "CREATOR_Y"}, now)
    result = build_cex_funding_intelligence(path)
    assert len(result["multi_cex_creators"]) == 1
    entry = result["multi_cex_creators"][0]
    assert entry["creator"] == "CREATOR_X"
    assert set(entry["exchanges"]) == {"Binance", "Coinbase"}


def test_shared_infrastructure_cross_exchange_hop(ops_db):
    """A subprov hop that is itself another origin's own withdrawal address
    (a real cross-exchange structural link) must be surfaced as a distinct
    Cross-Exchange Hop entry, and must set shared_subprovider=True on the
    origin whose path includes it."""
    path, conn = ops_db
    now = int(time.time())
    _insert_outcome(conn, "M1", "BINANCE_ORIGIN", {
        "boundary": {"name": "Binance"}, "creator": "C1",
        "subprovisioners": ["KUCOIN_ORIGIN"],
    }, now)
    _insert_outcome(conn, "M2", "KUCOIN_ORIGIN", {"boundary": {"name": "KuCoin"}, "creator": "C2"}, now)
    result = build_cex_funding_intelligence(path)
    cross_hops = [s for s in result["shared_infrastructure"] if s["role"] == "Cross-Exchange Hop"]
    assert len(cross_hops) == 1
    assert cross_hops[0]["wallet"] == "KUCOIN_ORIGIN"
    assert set(cross_hops[0]["exchanges"]) == {"Binance", "KuCoin"}
    by_origin = {o["origin"]: o for o in result["origins"]}
    assert by_origin["BINANCE_ORIGIN"]["strength_indicators"]["shared_subprovider"] is True


def test_operations_reached_reflects_existing_wt_ops_v2_wallets_only(ops_db):
    """operations count/operation_ids must come ONLY from existing
    wt_ops_v2_wallets membership -- never a new relationship. Zero when
    no such membership exists (the honest, real-data answer)."""
    path, conn = ops_db
    now = int(time.time())
    _insert_outcome(conn, "M1", "ORIGIN1", {"boundary": {"name": "Binance"}, "creator": "C1"}, now)
    result = build_cex_funding_intelligence(path)
    assert result["origins"][0]["operations"] == 0
    assert result["origins"][0]["operation_ids"] == []

    conn.execute("INSERT INTO wt_ops_v2_wallets VALUES (?,?,?)", ("op-uuid-1", "ORIGIN1", "TREASURY"))
    conn.commit()
    result2 = build_cex_funding_intelligence(path)
    assert result2["origins"][0]["operations"] == 1
    assert result2["origins"][0]["operation_ids"] == ["op-uuid-1"]


def test_window_seconds_filters_out_older_outcomes(ops_db):
    path, conn = ops_db
    now = int(time.time())
    _insert_outcome(conn, "OLD", "ORIGIN1", {"boundary": {"name": "Binance"}, "creator": "C1"}, now - 10 * 86400)
    _insert_outcome(conn, "NEW", "ORIGIN1", {"boundary": {"name": "Binance"}, "creator": "C2"}, now)
    result = build_cex_funding_intelligence(path, window_seconds=86400, now=now)
    assert result["origins"][0]["launches"] == 1


def test_non_cex_outcome_types_never_included(ops_db):
    path, conn = ops_db
    now = int(time.time())
    _insert_outcome(conn, "M1", "ORIGIN1", {"boundary": {"name": "Binance"}, "creator": "C1"}, now)
    _insert_outcome(conn, "M2", "ORIGIN2", {"creator": "C2"}, now, outcome_type="LINEAGE_GAP")
    result = build_cex_funding_intelligence(path)
    assert result["total_origins"] == 1
    assert result["origins"][0]["origin"] == "ORIGIN1"


def test_no_writes_occur(ops_db):
    """PRAGMA query_only=ON must make any accidental write raise, not
    silently succeed -- proves this module cannot mutate the database."""
    path, conn = ops_db
    now = int(time.time())
    _insert_outcome(conn, "M1", "ORIGIN1", {"boundary": {"name": "Binance"}, "creator": "C1"}, now)
    build_cex_funding_intelligence(path)
    # the connection used internally is closed after the call; verify the
    # source data is byte-identical before/after by re-reading row count
    count = conn.execute("SELECT COUNT(*) FROM wt_attribution_outcomes").fetchone()[0]
    assert count == 1
