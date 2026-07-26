"""X65.40 — backend half: operational_intelligence.py must expose the
authoritative walkback-confirmation timestamp (wt_attribution_outcomes.
completed_at) per confirmed mint. mint is wt_attribution_outcomes' PRIMARY
KEY, so exactly one completed_at exists per mint; no retry/reprocessing
scenario can produce a second, later value to double-count a confirmation.

X67.37 SUPERSEDES this file's original "unconditional lookup against the
canonical registry, not restricted to the window's Stage-1 population"
premise for FINITE windows. X67.36 found that premise was the exact
mechanism inflating every windowed Operational Intelligence payload with
the entire historical registry (907 rows for a 24h request instead of the
true ~760; 164 is_watchtower=True instead of the Canonical panel's own
correctly-windowed 20). The corrected architecture uses ONE population
definition per request: create_time-windowed launches only (finite
window), or create_time-windowed launches UNION the full registry (all-time
window) -- there is no longer a second, confirmation-time-based path that
adds mints outside that population.

This means: a launch created 30 days ago but confirmed by walkback 1 hour
ago is a genuinely old LAUNCH (by create_time) that happens to have had
recent CONFIRMATION ACTIVITY -- two different signals. It correctly no
longer appears in a 24h Operational Intelligence payload (`records`),
since that payload is now a true 24h launch population, not a mixed
population where some rows qualify by launch time and others by
confirmation time (which would silently reintroduce the exact kind of
population contamination X67.37 fixed). The confirmation timestamp itself
is still stored and reachable -- via the "all" window, the dedicated
Canonical WATCHTOWER panel (which reads wt_watchtower_launches directly,
unaffected by this population change), or a purpose-built confirmation-
activity metric -- just not via a finite-window launch population.
"""
from __future__ import annotations

import json
import sqlite3
import time

from src.ops.operational_intelligence import build_operational_intelligence


def _ops_db(path):
    conn = sqlite3.connect(path)
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
            detection_source TEXT, detection_delay_seconds INTEGER,
            funding_mechanism TEXT, creator_extraction_method TEXT,
            confidence TEXT, state TEXT, recorded_at INTEGER
        )"""
    )
    now = int(time.time())

    # CONFIRMED_OLD_LAUNCH: launched long ago (create_time), but walkback
    # confirmed it RECENTLY (completed_at inside a 24h window). Under the
    # superseded X65.39 create_time basis this would NOT show up in a 24h
    # window; under X65.40 walkback-confirmation-time basis, it MUST.
    conn.execute(
        "INSERT INTO wt_attribution_outcomes VALUES "
        "('CONFIRMED_OLD_LAUNCH','CANONICAL_OPERATOR_REACHED','stop',"
        "NULL,'wallet','CONFIRMED',?,NULL,0,0,?,NULL,?)",
        (json.dumps({}), now - 3600, now - 3600),
    )
    conn.execute(
        "INSERT INTO wt_watchtower_launches (mint,creator_wallet,create_time,treasury_wallet,subprov_wallet) "
        "VALUES ('CONFIRMED_OLD_LAUNCH','creatorA',?,'treasuryA','subprovA')",
        (now - 30 * 86400,),
    )

    # NEW_UNCONFIRMED_LAUNCH: launched recently but has NOT been confirmed
    # by walkback at all (no wt_watchtower_launches row, or an outcome that
    # isn't in the canonical registry) -- must never appear in the
    # confirmation-window count.
    conn.execute(
        "INSERT INTO wt_attribution_outcomes VALUES "
        "('NEW_UNCONFIRMED_LAUNCH','INSUFFICIENT_EVIDENCE','stop',"
        "NULL,'wallet','BASELINE',?,NULL,0,0,?,NULL,?)",
        (json.dumps({}), now - 1800, now - 1800),
    )

    # OLD_CONFIRMATION: walkback confirmed this a long time ago -- must
    # NOT appear in a 24h confirmation-window count.
    conn.execute(
        "INSERT INTO wt_attribution_outcomes VALUES "
        "('OLD_CONFIRMATION','CANONICAL_OPERATOR_REACHED','stop',"
        "NULL,'wallet','CONFIRMED',?,NULL,0,0,?,NULL,?)",
        (json.dumps({}), now - 20 * 86400, now - 20 * 86400),
    )
    conn.execute(
        "INSERT INTO wt_watchtower_launches (mint,creator_wallet,create_time,treasury_wallet,subprov_wallet) "
        "VALUES ('OLD_CONFIRMATION','creatorB',?,'treasuryB','subprovB')",
        (now - 25 * 86400,),
    )

    conn.commit()
    conn.close()


def _core_db(path):
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE token_analysis (mint TEXT PRIMARY KEY, created_at TEXT, migrated_at INTEGER,"
        " pf_ws_creator TEXT, earliest_tx_creator TEXT, create_tx_signature TEXT)"
    )
    conn.commit()
    conn.close()


def test_confirmation_completed_at_exposed_on_confirmed_records_within_window(tmp_path):
    """X67.37 -- confirmation_completed_at remains a real, correctly-exposed
    field on any record that IS in the population (a launch whose create_time
    falls inside the window). It is simply no longer a second, independent
    reason for an out-of-window launch to appear."""
    ops_path, core_path = str(tmp_path / "ops.db"), str(tmp_path / "core.db")
    _ops_db(ops_path)
    _core_db(core_path)

    # all-time window: CONFIRMED_OLD_LAUNCH (create_time 30 days ago) is in
    # the population via the all-time union; its confirmation_completed_at
    # (1 hour ago) is exposed correctly.
    intel = build_operational_intelligence(ops_path, core_path, window_seconds=365 * 86400)

    old_launch = intel["records"].get("CONFIRMED_OLD_LAUNCH")
    assert old_launch is not None
    assert old_launch["is_cascade_confirmed"] is True
    assert old_launch["confirmation_completed_at"] is not None


def test_old_launch_absent_from_finite_window_despite_recent_confirmation(tmp_path):
    """X67.37's corrected contract, replacing X65.40's original
    "unconditional lookup regardless of window" expectation: a launch
    created 30 days ago is correctly ABSENT from a 1-hour-window population,
    even though it was confirmed by walkback within that same hour.
    create_time (launch recency) and completed_at (confirmation recency)
    are different signals; only create_time determines finite-window
    population membership post-X67.37."""
    ops_path, core_path = str(tmp_path / "ops.db"), str(tmp_path / "core.db")
    _ops_db(ops_path)
    _core_db(core_path)

    intel = build_operational_intelligence(ops_path, core_path, window_seconds=3600)
    assert "CONFIRMED_OLD_LAUNCH" not in intel["records"]


def test_confirmation_completed_at_none_when_not_in_attribution_outcomes(tmp_path):
    ops_path, core_path = str(tmp_path / "ops.db"), str(tmp_path / "core.db")
    conn = sqlite3.connect(ops_path)
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
            detection_source TEXT, detection_delay_seconds INTEGER,
            funding_mechanism TEXT, creator_extraction_method TEXT,
            confidence TEXT, state TEXT, recorded_at INTEGER
        )"""
    )
    now = int(time.time())
    # In wt_watchtower_launches (canonical registry) but NEVER reached
    # wt_attribution_outcomes -- confirmation_completed_at must be None,
    # never guessed/defaulted.
    conn.execute(
        "INSERT INTO wt_watchtower_launches (mint,creator_wallet,create_time) VALUES "
        "('NO_OUTCOME_ROW','creatorC',?)",
        (now - 3600,),
    )
    conn.execute(
        "INSERT INTO wt_attribution_outcomes VALUES "
        "('NO_OUTCOME_ROW_PLACEHOLDER','INSUFFICIENT_EVIDENCE','stop',"
        "NULL,'wallet','BASELINE',?,NULL,0,0,?,NULL,?)",
        (json.dumps({}), now - 3600, now - 3600),
    )
    conn.commit()
    conn.close()
    _core_db(core_path)

    intel = build_operational_intelligence(ops_path, core_path, window_seconds=365 * 86400)
    # NO_OUTCOME_ROW is in the canonical registry (is_cascade_confirmed
    # True) but has no attribution outcome row of its own -- confirmation_
    # completed_at must be None, not silently substituted with anything.
    if "NO_OUTCOME_ROW" in intel["records"]:
        assert intel["records"]["NO_OUTCOME_ROW"]["confirmation_completed_at"] is None
