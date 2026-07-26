import json
import sqlite3
import time
from pathlib import Path

import pytest

from src.ops import operational_intelligence as oi


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "templates" / "discovery.html").read_text()


def _function(name: str, next_name: str) -> str:
    start = HTML.index(f"function {name}")
    return HTML[start:HTML.index(f"function {next_name}", start)]


# X65.35 — Repair Canonical Walkback-Confirmed WATCHTOWER Population.
#
# Root cause (traced with real row counts against database/wt_ops_v2.db):
# the backend logic (is_cascade_confirmed, window=all) was already correct
# -- 22 confirmed launches exist and are computed correctly by
# build_operational_intelligence(window_seconds=365*86400). The bug was a
# LATENCY/payload-size problem, not a logic bug: the X65.34 frontend fix
# fetched window=all&include_records=1 (every launch in the 365-day corpus,
# 8000+ records with nested campaign_evidence objects, ~19MB), then filtered
# to ~22 confirmed rows client-side. In practice this was slow enough that
# the section could appear stuck loading or resolve so late it looked like
# a permanent "0 launches." Fix: a new is_cascade_confirmed=1/0 query param
# on the SAME existing route filters server-side (query() in
# operational_intelligence.py), so only the ~22 confirmed rows are ever
# serialized and sent -- no new endpoint, no new classifier, no schema
# change. Also fixes a masking bug: a failed fetch and a genuine empty
# result both rendered as "✓ 0 launches" -- now distinguished via
# X65_34_CONFIRMED_FAILED.


@pytest.fixture()
def ops_db(tmp_path):
    db_path = tmp_path / "ops.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE wt_attribution_outcomes (
            mint TEXT PRIMARY KEY, outcome_type TEXT, stop_reason TEXT,
            terminal_entity TEXT, terminal_entity_type TEXT, confidence TEXT,
            evidence_json TEXT, operator_id TEXT,
            should_seed_emerging_operator INTEGER, should_retry INTEGER,
            completed_at INTEGER, source_queue_updated_at INTEGER,
            materialized_at INTEGER
        )"""
    )
    conn.execute(
        """CREATE TABLE wt_watchtower_launches (
            id INTEGER PRIMARY KEY AUTOINCREMENT, mint TEXT, creator_wallet TEXT,
            create_signature TEXT, create_time INTEGER, create_slot INTEGER,
            treasury_wallet TEXT, subprov_wallet TEXT, subprov_funding_sol REAL,
            wrap_close_sol REAL, wrap_close_signature TEXT,
            birth_to_launch_seconds INTEGER, create_to_migration_secs INTEGER,
            detection_source TEXT,
            detection_delay_seconds INTEGER, funding_mechanism TEXT,
            creator_extraction_method TEXT, confidence TEXT, state TEXT,
            recorded_at INTEGER
        )"""
    )
    now = int(time.time())
    # One confirmed launch (present in wt_watchtower_launches AND within window).
    conn.execute(
        "INSERT INTO wt_attribution_outcomes VALUES "
        "('CONFIRMEDmint1111111111111111111111111111','CANONICAL_OPERATOR_REACHED','stop',"
        "NULL,'wallet','CONFIRMED',?,NULL,0,0,?,NULL,?)",
        (json.dumps({}), now - 3600, now - 3600),
    )
    conn.execute(
        "INSERT INTO wt_watchtower_launches "
        "(mint,creator_wallet,create_time,treasury_wallet,subprov_wallet) VALUES "
        "('CONFIRMEDmint1111111111111111111111111111','creatorA',?,'treasuryA','subprovA')",
        (now - 7200,),
    )
    # One unconfirmed launch: attribution outcome exists but no matching
    # wt_watchtower_launches row.
    conn.execute(
        "INSERT INTO wt_attribution_outcomes VALUES "
        "('UNCONFIRMEDmint22222222222222222222222222','INSUFFICIENT_EVIDENCE','stop',"
        "NULL,'wallet','BASELINE',?,NULL,0,0,?,NULL,?)",
        (json.dumps({}), now - 1800, now - 1800),
    )
    conn.commit()
    conn.close()
    return str(db_path)


def _core_db(path):
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE token_analysis (mint TEXT PRIMARY KEY, created_at TEXT, migrated_at INTEGER,"
        " pf_ws_creator TEXT, earliest_tx_creator TEXT, create_tx_signature TEXT)"
    )
    # X65.35 -- UNCONFIRMEDmint2... has no wt_watchtower_launches row, so it
    # needs a token_analysis row for its launch create_time to resolve;
    # otherwise it has no launch-time evidence and is correctly excluded
    # from the windowed population (same rule as test_x65_26).
    now = int(time.time())
    conn.execute(
        "INSERT INTO token_analysis (mint, created_at) VALUES (?, ?)",
        ("UNCONFIRMEDmint22222222222222222222222222", str(now - 1800)),
    )
    conn.commit()
    conn.close()


def test_confirmed_population_present_at_365_day_window(ops_db, tmp_path):
    core_db = tmp_path / "core.db"
    _core_db(core_db)
    intel = oi.build_operational_intelligence(ops_db, str(core_db), window_seconds=365 * 86400)
    confirmed_mints = [m for m, r in intel["records"].items() if r.get("is_cascade_confirmed")]
    assert confirmed_mints == ["CONFIRMEDmint1111111111111111111111111111"]


def test_query_filters_server_side_by_is_cascade_confirmed(ops_db, tmp_path):
    core_db = tmp_path / "core.db"
    _core_db(core_db)
    intel = oi.build_operational_intelligence(ops_db, str(core_db), window_seconds=365 * 86400)

    confirmed = oi.query(intel, is_cascade_confirmed=True)
    assert confirmed == ["CONFIRMEDmint1111111111111111111111111111"]

    unconfirmed = oi.query(intel, is_cascade_confirmed=False)
    assert "UNCONFIRMEDmint22222222222222222222222222" in unconfirmed
    assert "CONFIRMEDmint1111111111111111111111111111" not in unconfirmed

    # Conservation: confirmed + unconfirmed must equal the full population,
    # same discipline this codebase already applies elsewhere (topology,
    # canonical_behaviour).
    assert len(confirmed) + len(unconfirmed) == intel["total_launches"]


def test_query_is_cascade_confirmed_none_is_unconstrained(ops_db, tmp_path):
    core_db = tmp_path / "core.db"
    _core_db(core_db)
    intel = oi.build_operational_intelligence(ops_db, str(core_db), window_seconds=365 * 86400)
    all_mints = oi.query(intel)
    assert len(all_mints) == intel["total_launches"]


def test_query_does_not_require_campaign_watchtower_in_current_window(ops_db, tmp_path):
    # The confirmed cohort must be reachable purely via is_cascade_confirmed,
    # independent of the campaign classifier's current-window WATCHTOWER
    # fingerprint match -- these are explicitly different criteria per the
    # brief's semantics section.
    core_db = tmp_path / "core.db"
    _core_db(core_db)
    intel = oi.build_operational_intelligence(ops_db, str(core_db), window_seconds=365 * 86400)
    confirmed_only = oi.query(intel, is_cascade_confirmed=True)
    confirmed_and_campaign = oi.query(intel, is_cascade_confirmed=True, campaign="WATCHTOWER")
    # Not asserting equality -- just that the confirmed filter alone is
    # sufficient to retrieve the row without also requiring campaign=WATCHTOWER.
    assert confirmed_only  # confirmed row is reachable at all
    assert set(confirmed_and_campaign) <= set(confirmed_only)


# --- Route wiring -----------------------------------------------------------

def test_route_accepts_is_cascade_confirmed_param():
    src = (ROOT / "src" / "core" / "operation_dashboard_routes.py").read_text()
    assert 'request.args.get("is_cascade_confirmed")' in src
    assert "is_cascade_confirmed=cascade_confirmed_filter" in src


def test_route_response_filter_echoes_is_cascade_confirmed():
    src = (ROOT / "src" / "core" / "operation_dashboard_routes.py").read_text()
    idx = src.index('response["filter"] = {')
    block = src[idx: idx + 400]
    assert '"is_cascade_confirmed": cascade_confirmed_filter' in block


# --- Frontend loader ---------------------------------------------------------

def test_loader_requests_server_side_confirmed_filter():
    loader = _function("loadConfirmedWatchtowerRows", "loadOperationalIntelligence")
    assert "is_cascade_confirmed" in loader
    assert "'1'" in loader or '"1"' in loader


def test_loader_still_defensively_filters_client_side():
    # Defense in depth against boolean/string/int serialization drift --
    # even though the server now pre-filters, the client filter must remain
    # so a stale/misconfigured backend can never leak unconfirmed rows in.
    loader = _function("loadConfirmedWatchtowerRows", "loadOperationalIntelligence")
    assert "r.is_cascade_confirmed" in loader


def test_loader_distinguishes_failure_from_genuine_zero():
    loader = _function("loadConfirmedWatchtowerRows", "loadOperationalIntelligence")
    catch_block = loader[loader.index(".catch("):]
    assert "X65_34_CONFIRMED_FAILED=true" in catch_block
    success_block = loader[: loader.index(".catch(")]
    assert "X65_34_CONFIRMED_FAILED" not in success_block or "X65_34_CONFIRMED_FAILED=true" not in success_block


def test_population_render_shows_explicit_failure_state_not_false_zero():
    # X65.58 -- this guard moved to the assembly point,
    # renderKnownWatchtowerBlock(), since renderCanonicalWatchtowerSection()
    # no longer has its own guard (it's called only after the caller has
    # already confirmed success).
    block = _function("renderKnownWatchtowerBlock", "renderCampaignDistribution")
    assert "X65_34_CONFIRMED_FAILED" in block
    assert "Unable to load confirmed population" in block
    # The failure branch (an early `return`) must appear before the call to
    # renderCanonicalWatchtowerSection() (whose own body renders the
    # "0 launches" badge markup) -- structurally guaranteeing a failed
    # request can never fall through to it.
    failure_pos = block.index("X65_34_CONFIRMED_FAILED")
    canonical_call_pos = block.index("renderCanonicalWatchtowerSection(confirmed)")
    assert failure_pos < canonical_call_pos


def test_candidate_rows_still_window_scoped_unaffected_by_x65_35():
    helper = _function("x65_27CandidateWatchtowerRows", "x65_27CandidateStatus")
    assert "!r.is_cascade_confirmed" in helper
    assert "x65_25WatchtowerRows()" in helper


def test_topology_and_funding_sections_unchanged():
    topology = _function("renderKnownWatchtowerTopology", "renderConfirmedWatchtowerTreasury")
    funding = _function("renderKnownWatchtowerFunding", "renderKnownWatchtowerBlock")
    for fn in (topology, funding):
        assert "x65_25WatchtowerRows()" in fn
        assert "X65_34_CONFIRMED_ROWS" not in fn
