"""X78.7 Part B: regression tests proving _build_context_for_creator
(the single-creator SQL-filtered optimization) produces output IDENTICAL
to _build_context(conn, [creator]) for the same creator, and that
score_creator_now's sync_infra_wallets call is correctly debounced.

Context: X78.6 fixed WHERE the write lease was held (transaction
boundary). X78.7 addresses WHY the setup phase was slow in the first
place: _build_context did eight full-table scans (measured against the
real production DB: ~22s for a single creator, up to ~1.57M rows read
for the tokens_by_creator query alone) to filter down to one creator's
data in Python. _build_context_for_creator pushes that filtering into
SQL instead, verified here to produce byte-identical scoring output.

Live production benchmark results (see
docs/audits/x78_7_risk_scoring_query_performance.md for the full
report): a 943-funder self-funding creator (the canonical example from
docs/CLAUDE.md) scored in 3.2s with the new path vs 21.8s with the old
path -- identical SELF_FUNDING_FARM classification, identical score=80,
identical reason codes.
"""
from __future__ import annotations

import sqlite3
import time

import pytest

from src.core.risk_scoring_builder import RiskScoringBuilder, SYNC_INFRA_WALLETS_DEBOUNCE_SEC
import src.core.risk_scoring_builder as rsb_module


def _make_db_with_data(db_path: str):
    """A schema with enough data across every table _build_context/
    _build_context_for_creator touch to meaningfully exercise both
    paths, including multiple creators (to prove filtering actually
    excludes other creators' data, not just happens to match by luck)."""
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE IF NOT EXISTS network_membership (creator_address TEXT)")
    conn.execute("""CREATE TABLE IF NOT EXISTS creator_funders (
        creator_address TEXT, funder_address TEXT, amount_sol REAL, is_cex INTEGER)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS token_analysis (
        earliest_tx_creator TEXT, mint TEXT, market_cap_highest REAL, market_cap_current REAL,
        created_at TEXT, migrated_at TEXT, market_cap_highest_at_ts TEXT, lifecycle_stage TEXT,
        bonding_curve_pda TEXT, pool_address TEXT, pumpswap_pool_address TEXT)""")
    conn.execute("CREATE TABLE IF NOT EXISTS creator_self_funding (creator_address TEXT, is_self_funding INTEGER)")
    conn.execute("""CREATE TABLE IF NOT EXISTS creator_outbound_classifications (
        creator_address TEXT, relationship_type TEXT, recipient_address TEXT, amount_sol REAL)""")
    conn.execute("CREATE TABLE IF NOT EXISTS creator_second_hop (creator_address TEXT, upstream_address TEXT, confidence_score REAL)")
    conn.execute("CREATE TABLE IF NOT EXISTS creator_c2c_edges (source_creator TEXT, dest_creator TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS coordinated_creator_edges (creator_a TEXT, creator_b TEXT, bridge_funder TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS wallet_clusters (funder_wallet TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS farm_cluster_members (wallet_address TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS creator_tags (creator_address TEXT, tag TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS token_pool_accounts (mint TEXT, liquidity_removed INTEGER, liquidity_removed_at TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS infra_funders_observed (funder_address TEXT, note TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS networks_release (network_name TEXT, network_size INTEGER, network_type TEXT)")

    # Two creators: "creatorA" (the one under test) and "creatorB" (a
    # decoy, to prove creatorA's context doesn't leak creatorB's data
    # and isn't accidentally filtered out by _build_context_for_creator).
    conn.executemany(
        "INSERT INTO creator_funders (creator_address, funder_address, amount_sol, is_cex) VALUES (?, ?, ?, 0)",
        [
            ("creatorA", "funder1", 1.5),
            ("creatorA", "funder2", 2.5),
            ("creatorB", "funder1", 3.0),   # funder1 shared -- fanout must count creatorB too
            ("creatorB", "funder3", 4.0),
        ],
    )
    conn.execute("INSERT INTO creator_self_funding (creator_address, is_self_funding) VALUES ('creatorA', 1)")
    conn.execute("INSERT INTO creator_self_funding (creator_address, is_self_funding) VALUES ('creatorB', 0)")
    conn.executemany(
        "INSERT INTO creator_outbound_classifications (creator_address, relationship_type, recipient_address, amount_sol) VALUES (?, ?, ?, ?)",
        [("creatorA", "return_to_funder", "funder1", 1.0), ("creatorB", "shared_payout_wallet", "x", 1.0)],
    )
    conn.execute("INSERT INTO creator_second_hop (creator_address, upstream_address, confidence_score) VALUES ('creatorA', 'up1', 0.9)")
    conn.execute("INSERT INTO creator_c2c_edges (source_creator, dest_creator) VALUES ('creatorA', 'creatorB')")
    conn.execute("INSERT INTO coordinated_creator_edges (creator_a, creator_b, bridge_funder) VALUES ('creatorA', 'creatorB', 'funder1')")
    conn.execute("INSERT INTO wallet_clusters (funder_wallet) VALUES ('funder1')")
    conn.execute("INSERT INTO farm_cluster_members (wallet_address) VALUES ('creatorA')")
    conn.execute("INSERT INTO creator_tags (creator_address, tag) VALUES ('creatorA', 'uses_jitotip')")
    conn.executemany(
        "INSERT INTO token_analysis (earliest_tx_creator, mint, market_cap_highest, migrated_at) VALUES (?, ?, ?, ?)",
        [("creatorA", "mintA1", 6_000_000, "2024-01-01"), ("creatorA", "mintA2", 100_000, None),
         ("creatorB", "mintB1", 3_000_000, "2024-01-01")],
    )
    conn.execute("INSERT INTO token_pool_accounts (mint, liquidity_removed, liquidity_removed_at) VALUES ('mintA1', 1, '2024-01-02')")

    conn.commit()
    conn.close()


@pytest.fixture(autouse=True)
def _reset_debounce():
    rsb_module._sync_infra_wallets_last_run = 0.0
    yield
    rsb_module._sync_infra_wallets_last_run = 0.0


def test_build_context_for_creator_matches_build_context_exactly(tmp_path):
    """Core X78.7 regression: the SQL-filtered single-creator context
    must be structurally identical to the full-scan context, for the
    same creator, including correctly EXCLUDING the other creator's
    data (proving the filter isn't accidentally a no-op)."""
    db_path = str(tmp_path / "x.db")
    _make_db_with_data(db_path)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    # infra_wallets must exist for the NOT IN subqueries to work.
    conn.execute("CREATE TABLE IF NOT EXISTS infra_wallets (address TEXT PRIMARY KEY, type TEXT, label TEXT, updated_at INTEGER)")
    conn.commit()

    builder = RiskScoringBuilder(db_path)

    for creator in ("creatorA", "creatorB"):
        ctx_new = builder._build_context_for_creator(conn, creator)
        ctx_old = builder._build_context(conn, [creator])

        # Compare every field _score_creator_fast actually reads.
        assert ctx_new["funders_by_creator"].get(creator) == ctx_old["funders_by_creator"].get(creator)
        # fanout_by_funder in the new path is scoped to only THIS
        # creator's own funders (by design, since _score_creator_fast
        # never looks up a funder outside that set) -- but for each of
        # those funders, the COUNT must still reflect the full
        # cross-creator fanout, not be limited to this creator alone.
        this_creators_funders = {row["funder_address"] for row in ctx_new["funders_by_creator"].get(creator, [])}
        assert set(ctx_new["fanout_by_funder"].keys()) == this_creators_funders
        for funder in this_creators_funders:
            assert ctx_new["fanout_by_funder"][funder] == ctx_old["fanout_by_funder"][funder], (
                f"fanout count for shared funder {funder} must match the full "
                f"cross-creator aggregate exactly"
            )
        assert ctx_new["self_funding"].get(creator) == ctx_old["self_funding"].get(creator)
        assert dict(ctx_new["outbound_by_creator"].get(creator, {})) == dict(ctx_old["outbound_by_creator"].get(creator, {}))
        assert ctx_new["second_hop_by_creator"].get(creator) == ctx_old["second_hop_by_creator"].get(creator)
        assert ctx_new["c2c_count"].get(creator, 0) == ctx_old["c2c_count"].get(creator, 0)
        assert dict(ctx_new["coord_by_creator"].get(creator, {})) == dict(ctx_old["coord_by_creator"].get(creator, {}))
        # wallet_cluster_funders is scoped to this creator's own funders
        # only in the new path (by design -- see the query comment);
        # only membership among THIS creator's funders needs to match
        # (that's the only thing _score_creator_fast ever checks).
        expected_cluster_overlap = {
            f for f in ctx_old["wallet_cluster_funders"] if f in this_creators_funders
        }
        assert ctx_new["wallet_cluster_funders"] == expected_cluster_overlap
        assert (creator in ctx_new["farm_members"]) == (creator in ctx_old["farm_members"])
        assert (creator in ctx_new["jito_creators"]) == (creator in ctx_old["jito_creators"])
        assert ctx_new["tokens_by_creator"].get(creator) == ctx_old["tokens_by_creator"].get(creator)


def test_score_creator_fast_output_identical_between_paths(tmp_path):
    """The actual acceptance criterion: scoring OUTPUT (not just
    context shape) must be identical, for both creators, using either
    context-building path."""
    db_path = str(tmp_path / "x.db")
    _make_db_with_data(db_path)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE IF NOT EXISTS infra_wallets (address TEXT PRIMARY KEY, type TEXT, label TEXT, updated_at INTEGER)")
    conn.commit()

    builder = RiskScoringBuilder(db_path)
    keys = ["operator_score", "outcome_score", "g_score", "liquidation_score",
            "final_score", "category", "risk_level", "migrated_tokens",
            "total_tokens", "g7_percentage", "liquidation_count", "reason_codes"]

    for creator in ("creatorA", "creatorB"):
        ctx_new = builder._build_context_for_creator(conn, creator)
        ctx_old = builder._build_context(conn, [creator])
        score_new = builder._score_creator_fast(creator, ctx_new)
        score_old = builder._score_creator_fast(creator, ctx_old)
        for k in keys:
            assert score_new.get(k) == score_old.get(k), (
                f"creator={creator} field={k}: new={score_new.get(k)!r} old={score_old.get(k)!r}"
            )


def test_sync_infra_wallets_debounced_across_score_creator_now_calls(tmp_path, monkeypatch):
    """X78.7: sync_infra_wallets must not run on every single
    score_creator_now call -- only once per debounce window."""
    db_path = str(tmp_path / "x.db")
    _make_db_with_data(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE IF NOT EXISTS infra_wallets (address TEXT PRIMARY KEY, type TEXT, label TEXT, updated_at INTEGER)")
    conn.commit()
    conn.close()

    call_count = {"n": 0}
    real_sync = rsb_module.sync_infra_wallets

    def counting_sync(conn, *args, **kwargs):
        call_count["n"] += 1
        return real_sync(conn, *args, **kwargs)

    monkeypatch.setattr(rsb_module, "sync_infra_wallets", counting_sync)

    builder = RiskScoringBuilder(db_path)
    builder.score_creator_now("creatorA")
    builder.score_creator_now("creatorA")
    builder.score_creator_now("creatorA")

    assert call_count["n"] == 1, (
        f"sync_infra_wallets should run once within the debounce window "
        f"across 3 consecutive score_creator_now calls, ran {call_count['n']} times"
    )


def test_sync_infra_wallets_runs_again_after_debounce_window_expires(tmp_path, monkeypatch):
    """Non-regression: the debounce must not permanently suppress sync
    -- it must run again once the window elapses."""
    db_path = str(tmp_path / "x.db")
    _make_db_with_data(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE IF NOT EXISTS infra_wallets (address TEXT PRIMARY KEY, type TEXT, label TEXT, updated_at INTEGER)")
    conn.commit()
    conn.close()

    call_count = {"n": 0}
    real_sync = rsb_module.sync_infra_wallets

    def counting_sync(conn, *args, **kwargs):
        call_count["n"] += 1
        return real_sync(conn, *args, **kwargs)

    monkeypatch.setattr(rsb_module, "sync_infra_wallets", counting_sync)
    monkeypatch.setattr(rsb_module, "SYNC_INFRA_WALLETS_DEBOUNCE_SEC", 0.05)

    builder = RiskScoringBuilder(db_path)
    builder.score_creator_now("creatorA")
    time.sleep(0.1)
    builder.score_creator_now("creatorA")

    assert call_count["n"] == 2


def test_run_still_uses_full_build_context_unaffected_by_optimization(tmp_path):
    """Non-regression: run() (full-batch scoring) must be completely
    unaffected by the single-creator optimization -- it still uses
    _build_context with the full creator list, not
    _build_context_for_creator."""
    db_path = str(tmp_path / "x.db")
    _make_db_with_data(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE IF NOT EXISTS infra_wallets (address TEXT PRIMARY KEY, type TEXT, label TEXT, updated_at INTEGER)")
    conn.commit()
    conn.close()

    builder = RiskScoringBuilder(db_path)
    result = builder.run()
    assert result["status"] == "success"
    assert result["creators_scored"] >= 2  # creatorA and creatorB both scored
