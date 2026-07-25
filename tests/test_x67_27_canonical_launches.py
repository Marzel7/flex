"""X67.27 -- Tests for the shared canonical WATCHTOWER launch helper
(src.ops.canonical_launches.get_canonical_watchtower_launches), consumed
by BOTH Discovery and Operation Intelligence so the two pages can never
independently diverge on canonical launch membership again.
"""
import sqlite3

import pytest

from src.ops.canonical_launches import (
    get_canonical_watchtower_launches,
    canonical_launch_to_dict,
)


OPS_SCHEMA = """
CREATE TABLE wt_watchtower_launches (
    id INTEGER PRIMARY KEY AUTOINCREMENT, mint TEXT, creator_wallet TEXT,
    create_signature TEXT, create_time INTEGER, treasury_wallet TEXT,
    subprov_wallet TEXT, create_to_migration_secs INTEGER,
    detection_source TEXT, creator_extraction_method TEXT,
    confidence TEXT DEFAULT 'STRICT', funding_mechanism TEXT DEFAULT 'WSOL_WRAP_CLOSE',
    recorded_at INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE wt_attribution_outcomes (
    mint TEXT PRIMARY KEY, outcome_type TEXT
);
"""

CORE_SCHEMA = """
CREATE TABLE token_analysis (mint TEXT PRIMARY KEY, migrated_at INTEGER);
"""


@pytest.fixture
def ops_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(OPS_SCHEMA)
    return conn


@pytest.fixture
def core_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(CORE_SCHEMA)
    return conn


def _insert_launch(conn, *, mint, create_time, creator="creator1", treasury="treasury1",
                    subprov="subprov1", recorded_at=None, confidence="STRICT",
                    detection_source=None, funding_mechanism="WSOL_WRAP_CLOSE",
                    create_to_migration_secs=None):
    conn.execute(
        "INSERT INTO wt_watchtower_launches "
        "(mint, creator_wallet, create_time, treasury_wallet, subprov_wallet, "
        " confidence, detection_source, funding_mechanism, create_to_migration_secs, recorded_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (mint, creator, create_time, treasury, subprov, confidence,
         detection_source, funding_mechanism, create_to_migration_secs,
         recorded_at if recorded_at is not None else create_time),
    )
    conn.commit()


# ── Population equality / count equality ────────────────────────────────────

def test_population_matches_raw_registry_query(ops_conn, core_conn):
    _insert_launch(ops_conn, mint="MintA", create_time=1000)
    _insert_launch(ops_conn, mint="MintB", create_time=2000)
    _insert_launch(ops_conn, mint="MintC", create_time=3000)

    launches = get_canonical_watchtower_launches(ops_conn, core_conn)
    mints = {l.mint for l in launches}
    raw_mints = {r["mint"] for r in ops_conn.execute("SELECT mint FROM wt_watchtower_launches")}
    assert mints == raw_mints
    assert len(launches) == 3


def test_window_filters_by_create_time_inclusive_both_bounds(ops_conn, core_conn):
    _insert_launch(ops_conn, mint="Early", create_time=500)
    _insert_launch(ops_conn, mint="InWindowStart", create_time=1000)
    _insert_launch(ops_conn, mint="InWindowMid", create_time=1500)
    _insert_launch(ops_conn, mint="InWindowEnd", create_time=2000)
    _insert_launch(ops_conn, mint="Late", create_time=2500)

    launches = get_canonical_watchtower_launches(
        ops_conn, core_conn, window_start=1000, window_end=2000,
    )
    mints = {l.mint for l in launches}
    assert mints == {"InWindowStart", "InWindowMid", "InWindowEnd"}


# ── No non-canonical additions ───────────────────────────────────────────────

def test_treasury_activity_for_non_registry_mint_never_enters_population(ops_conn, core_conn):
    """A mint that only exists via treasury/session activity, never in
    wt_watchtower_launches itself, must never appear."""
    _insert_launch(ops_conn, mint="RealLaunch", create_time=1000)
    # Simulate an attribution-outcomes row for a mint that ISN'T canonical --
    # this must not leak into the canonical population.
    ops_conn.execute(
        "INSERT INTO wt_attribution_outcomes (mint, outcome_type) VALUES (?,?)",
        ("NotCanonicalMint", "LINEAGE_GAP"),
    )
    ops_conn.commit()

    launches = get_canonical_watchtower_launches(ops_conn, core_conn)
    mints = {l.mint for l in launches}
    assert mints == {"RealLaunch"}
    assert "NotCanonicalMint" not in mints


# ── Enrichment safety ─────────────────────────────────────────────────────────

def test_missing_migration_data_does_not_remove_launch(ops_conn, core_conn):
    _insert_launch(ops_conn, mint="NoMigrationYet", create_time=1000)
    # core_conn's token_analysis has no row for this mint at all.
    launches = get_canonical_watchtower_launches(ops_conn, core_conn)
    assert len(launches) == 1
    assert launches[0].migration_time is None
    assert launches[0].mint == "NoMigrationYet"


def test_missing_campaign_attribution_defaults_to_unassigned_not_omitted(ops_conn, core_conn):
    _insert_launch(ops_conn, mint="NoCampaign", create_time=1000)
    # No wt_attribution_outcomes row at all for this mint.
    launches = get_canonical_watchtower_launches(ops_conn, core_conn)
    assert len(launches) == 1
    assert launches[0].campaign == "Unassigned campaign"


def test_missing_live_detection_data_does_not_remove_launch(ops_conn, core_conn):
    _insert_launch(ops_conn, mint="NoDetection", create_time=1000, detection_source=None)
    launches = get_canonical_watchtower_launches(ops_conn, core_conn)
    assert len(launches) == 1
    assert launches[0].mint == "NoDetection"


def test_null_create_time_is_excluded_not_defaulted(ops_conn, core_conn):
    ops_conn.execute(
        "INSERT INTO wt_watchtower_launches (mint, creator_wallet, create_time, recorded_at) "
        "VALUES ('NullCreateTime', 'creator1', NULL, 999)"
    )
    ops_conn.commit()
    _insert_launch(ops_conn, mint="HasCreateTime", create_time=1000)

    launches = get_canonical_watchtower_launches(ops_conn, core_conn)
    mints = {l.mint for l in launches}
    assert "NullCreateTime" not in mints
    assert "HasCreateTime" in mints


# ── Deduplication ─────────────────────────────────────────────────────────────

def test_one_mint_appears_once_even_with_multiple_registry_rows(ops_conn, core_conn):
    """Defensive dedup: even if the underlying uniqueness constraint were
    ever relaxed, this helper's own output contract holds one row per mint."""
    _insert_launch(ops_conn, mint="DupMint", create_time=1000, recorded_at=1000, creator="creatorA")
    _insert_launch(ops_conn, mint="DupMint", create_time=1000, recorded_at=2000, creator="creatorB")

    launches = get_canonical_watchtower_launches(ops_conn, core_conn)
    dup_launches = [l for l in launches if l.mint == "DupMint"]
    assert len(dup_launches) == 1
    # ORDER BY create_time DESC, recorded_at DESC -> the later recorded_at wins.
    assert dup_launches[0].creator_wallet == "creatorB"


# ── Boundary consistency ─────────────────────────────────────────────────────

def test_launch_exactly_at_window_start_boundary_included(ops_conn, core_conn):
    _insert_launch(ops_conn, mint="AtStart", create_time=1000)
    launches = get_canonical_watchtower_launches(ops_conn, core_conn, window_start=1000)
    assert {l.mint for l in launches} == {"AtStart"}


def test_launch_exactly_at_window_end_boundary_included(ops_conn, core_conn):
    _insert_launch(ops_conn, mint="AtEnd", create_time=2000)
    launches = get_canonical_watchtower_launches(ops_conn, core_conn, window_end=2000)
    assert {l.mint for l in launches} == {"AtEnd"}


def test_launch_one_second_before_window_start_excluded(ops_conn, core_conn):
    _insert_launch(ops_conn, mint="JustBefore", create_time=999)
    launches = get_canonical_watchtower_launches(ops_conn, core_conn, window_start=1000)
    assert {l.mint for l in launches} == set()


def test_launch_one_second_after_window_end_excluded(ops_conn, core_conn):
    _insert_launch(ops_conn, mint="JustAfter", create_time=2001)
    launches = get_canonical_watchtower_launches(ops_conn, core_conn, window_end=2000)
    assert {l.mint for l in launches} == set()


# ── Ordering ──────────────────────────────────────────────────────────────────

def test_stable_ordering_by_create_time_descending(ops_conn, core_conn):
    _insert_launch(ops_conn, mint="Oldest", create_time=1000)
    _insert_launch(ops_conn, mint="Newest", create_time=3000)
    _insert_launch(ops_conn, mint="Middle", create_time=2000)

    launches = get_canonical_watchtower_launches(ops_conn, core_conn)
    assert [l.mint for l in launches] == ["Newest", "Middle", "Oldest"]


# ── Time-to-migration derivation ─────────────────────────────────────────────

def test_time_to_migration_prefers_stored_value(ops_conn, core_conn):
    _insert_launch(ops_conn, mint="HasStored", create_time=1000, create_to_migration_secs=500)
    core_conn.execute("INSERT INTO token_analysis (mint, migrated_at) VALUES ('HasStored', 9999)")
    core_conn.commit()
    launches = get_canonical_watchtower_launches(ops_conn, core_conn)
    assert launches[0].time_to_migration_seconds == 500


def test_time_to_migration_derived_when_not_stored(ops_conn, core_conn):
    _insert_launch(ops_conn, mint="Derived", create_time=1000, create_to_migration_secs=None)
    core_conn.execute("INSERT INTO token_analysis (mint, migrated_at) VALUES ('Derived', 1300)")
    core_conn.commit()
    launches = get_canonical_watchtower_launches(ops_conn, core_conn)
    assert launches[0].time_to_migration_seconds == 300


# ── canonical_launch_to_dict projection ──────────────────────────────────────

def test_dict_projection_has_required_columns(ops_conn, core_conn):
    _insert_launch(ops_conn, mint="ForDict", create_time=1000)
    launches = get_canonical_watchtower_launches(ops_conn, core_conn)
    d = canonical_launch_to_dict(launches[0])
    for key in ("mint", "creator", "treasury_wallet", "subprov_wallet",
                "create_time", "time_to_migration_seconds",
                "live_detection_status", "campaign"):
        assert key in d


# ── No writes ─────────────────────────────────────────────────────────────────

def test_helper_performs_no_writes(ops_conn, core_conn):
    _insert_launch(ops_conn, mint="M1", create_time=1000)
    before = ops_conn.execute("SELECT COUNT(*) c FROM wt_watchtower_launches").fetchone()["c"]
    get_canonical_watchtower_launches(ops_conn, core_conn)
    after = ops_conn.execute("SELECT COUNT(*) c FROM wt_watchtower_launches").fetchone()["c"]
    assert before == after


# ── Production regression (read-only, real data) ────────────────────────────

@pytest.fixture
def prod_paths():
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ops_db = os.path.join(root, "database", "wt_ops_v2.db")
    core_db = os.path.join(root, "database", "flex_complete_database.db")
    if not os.path.exists(ops_db):
        pytest.skip("production database not present in this environment")
    return ops_db, core_db


def test_production_population_matches_raw_registry_query(prod_paths):
    ops_db, core_db = prod_paths
    ops = sqlite3.connect(f"file:{ops_db}?mode=ro", uri=True)
    ops.row_factory = sqlite3.Row
    core = sqlite3.connect(f"file:{core_db}?mode=ro", uri=True)
    core.row_factory = sqlite3.Row

    launches = get_canonical_watchtower_launches(ops, core)
    mints = {l.mint for l in launches}
    raw_mints = {
        r["mint"] for r in ops.execute(
            "SELECT mint FROM wt_watchtower_launches WHERE mint IS NOT NULL AND create_time IS NOT NULL"
        )
    }
    assert mints == raw_mints


def test_production_no_writes(prod_paths):
    ops_db, core_db = prod_paths
    ops = sqlite3.connect(f"file:{ops_db}?mode=ro", uri=True)
    ops.row_factory = sqlite3.Row
    core = sqlite3.connect(f"file:{core_db}?mode=ro", uri=True)
    core.row_factory = sqlite3.Row

    before = ops.execute("SELECT COUNT(*) c FROM wt_watchtower_launches").fetchone()["c"]
    get_canonical_watchtower_launches(ops, core, window_start=0)
    after = ops.execute("SELECT COUNT(*) c FROM wt_watchtower_launches").fetchone()["c"]
    assert before == after
