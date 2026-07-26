"""X65.26/X65.27 — expose treasury_wallet on the per-mint
operational-intelligence record. Pure pass-through, explicit precedence:
wt_watchtower_launches.treasury_wallet (the wrap-close detection path,
already fetched for the has_confirmed_path check but previously discarded)
first, falling back to wt_walkback_queue.treasury (a separate, already-
persisted evidence source this module already reads for
funder_block_time/funding_mechanism) when the former is absent. No new
query beyond adding one already-existing column to an existing SELECT, no
new classification, no schema change. treasury_wallet_source names which
table each value came from.
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
            detection_source TEXT,
            detection_delay_seconds INTEGER, funding_mechanism TEXT,
            creator_extraction_method TEXT, confidence TEXT, state TEXT,
            recorded_at INTEGER
        )"""
    )
    conn.execute(
        """CREATE TABLE wt_walkback_queue (
            mint TEXT PRIMARY KEY, creator TEXT, subprov TEXT, treasury TEXT,
            walkback_class TEXT, attribution_source TEXT, intelligence_outcome TEXT,
            status TEXT, rpc_used INTEGER, attempts INTEGER, last_error TEXT,
            enqueued_at INTEGER, started_at INTEGER, completed_at INTEGER,
            updated_at INTEGER, funder_wallet TEXT, funding_mechanism TEXT,
            funder_amount_sol REAL, funder_sig TEXT, funder_slot INTEGER,
            funder_block_time INTEGER, termination_reason_json TEXT
        )"""
    )
    now = int(time.time())
    conn.execute(
        "INSERT INTO wt_attribution_outcomes VALUES "
        "('MINT_WITH_TREASURY','CANONICAL_OPERATOR_REACHED','stop',NULL,'wallet','CONFIRMED',?,?,0,0,?,NULL,?)",
        (json.dumps({}), "WATCHTOWER", now, now),
    )
    conn.execute(
        "INSERT INTO wt_attribution_outcomes VALUES "
        "('MINT_NO_LAUNCH_ROW','INSUFFICIENT_EVIDENCE','stop',NULL,'wallet','BASELINE',?,NULL,0,0,?,NULL,?)",
        (json.dumps({}), now, now),
    )
    conn.execute(
        "INSERT INTO wt_attribution_outcomes VALUES "
        "('MINT_WALKBACK_ONLY','INSUFFICIENT_EVIDENCE','stop',NULL,'wallet','BASELINE',?,NULL,0,0,?,NULL,?)",
        (json.dumps({}), now, now),
    )
    conn.execute(
        "INSERT INTO wt_watchtower_launches "
        "(mint,creator_wallet,create_time,treasury_wallet,subprov_wallet) VALUES "
        "('MINT_WITH_TREASURY','CreatorWallet111',?,'TreasuryWallet111','SubprovWallet111')",
        (now,),
    )
    conn.execute(
        "INSERT INTO wt_walkback_queue (mint,creator,treasury) VALUES "
        "('MINT_WALKBACK_ONLY','CreatorWallet222','TreasuryWallet222')"
    )
    conn.commit()
    conn.close()


def _core_db(path):
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE token_analysis (mint TEXT PRIMARY KEY, created_at TEXT, migrated_at INTEGER,"
        " pf_ws_creator TEXT, earliest_tx_creator TEXT, create_tx_signature TEXT)"
    )
    # X65.35 -- population membership is now windowed by each mint's
    # resolved launch create_time (token_analysis.created_at first,
    # falling back to wt_watchtower_launches.create_time), not by
    # wt_attribution_outcomes.completed_at. MINT_WALKBACK_ONLY and
    # MINT_NO_LAUNCH_ROW have no wt_watchtower_launches row, so they need a
    # token_analysis row here or they'd have no resolvable launch time and
    # be correctly excluded from the windowed population being tested.
    now = int(time.time())
    conn.execute(
        "INSERT INTO token_analysis (mint, created_at) VALUES (?, ?)",
        ("MINT_WALKBACK_ONLY", str(now)),
    )
    conn.execute(
        "INSERT INTO token_analysis (mint, created_at) VALUES (?, ?)",
        ("MINT_NO_LAUNCH_ROW", str(now)),
    )
    conn.commit()
    conn.close()


def test_treasury_wallet_is_exposed_when_sourced_from_wt_watchtower_launches(tmp_path):
    ops_path, core_path = str(tmp_path / "ops.db"), str(tmp_path / "core.db")
    _ops_db(ops_path)
    _core_db(core_path)

    intel = build_operational_intelligence(ops_path, core_path, window_seconds=86400)

    record = intel["records"]["MINT_WITH_TREASURY"]
    assert record["treasury_wallet"] == "TreasuryWallet111"
    assert record["treasury_wallet_source"] == "wt_watchtower_launches.treasury_wallet"
    assert record["is_watchtower"] is True


def test_treasury_wallet_falls_back_to_walkback_queue(tmp_path):
    # A launch confirmed WATCHTOWER via a path OTHER than wrap-close
    # detection (no wt_watchtower_launches row) but with a resolved
    # treasury already persisted in wt_walkback_queue.treasury must still
    # expose it, with the source explicitly attributed to that table.
    ops_path, core_path = str(tmp_path / "ops.db"), str(tmp_path / "core.db")
    _ops_db(ops_path)
    _core_db(core_path)

    intel = build_operational_intelligence(ops_path, core_path, window_seconds=86400)

    record = intel["records"]["MINT_WALKBACK_ONLY"]
    assert record["treasury_wallet"] == "TreasuryWallet222"
    assert record["treasury_wallet_source"] == "wt_walkback_queue.treasury"


def test_wt_watchtower_launches_takes_precedence_over_walkback_queue(tmp_path):
    # When BOTH sources have a value, wt_watchtower_launches.treasury_wallet
    # (the more specific wrap-close detection path) wins.
    ops_path, core_path = str(tmp_path / "ops.db"), str(tmp_path / "core.db")
    _ops_db(ops_path)
    _core_db(core_path)
    conn = sqlite3.connect(ops_path)
    conn.execute(
        "INSERT INTO wt_walkback_queue (mint,creator,treasury) VALUES "
        "('MINT_WITH_TREASURY','CreatorWallet111','ShouldNotWinTreasury')"
    )
    conn.commit()
    conn.close()

    intel = build_operational_intelligence(ops_path, core_path, window_seconds=86400)

    record = intel["records"]["MINT_WITH_TREASURY"]
    assert record["treasury_wallet"] == "TreasuryWallet111"
    assert record["treasury_wallet_source"] == "wt_watchtower_launches.treasury_wallet"


def test_treasury_wallet_is_none_when_neither_source_has_a_row(tmp_path):
    # A launch confirmed WATCHTOWER via a DIFFERENT evidence path (here,
    # explicit_confirmed_operation) with no wt_watchtower_launches row and
    # no wt_walkback_queue row at all -- treasury_wallet must be None, not
    # fabricated, per the explicit "recorded value, not complete
    # resolution" constraint.
    ops_path, core_path = str(tmp_path / "ops.db"), str(tmp_path / "core.db")
    _ops_db(ops_path)
    _core_db(core_path)

    intel = build_operational_intelligence(ops_path, core_path, window_seconds=86400)

    record = intel["records"]["MINT_NO_LAUNCH_ROW"]
    assert record["treasury_wallet"] is None
    assert record["treasury_wallet_source"] is None
