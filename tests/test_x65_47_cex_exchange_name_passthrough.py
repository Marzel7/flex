"""X65.47 — surface the already-identified exchange name (wt_attribution_
outcomes.evidence_json.boundary.name, per cex_funding_intelligence.py's
own documented field: "already-identified name, never inferred") on each
per-mint operational-intelligence record, and use it in the Provisioning
Candidates Status column instead of the generic "Known CEX boundary"
label whenever a specific exchange has been identified. Pure pass-through:
no new query beyond reading one already-fetched evidence_json field, no
new classification, no schema change. A candidate whose CEX outcome has no
identified boundary name (or whose outcome isn't KNOWN_CEX_REACHED at all)
must fall back to the existing generic status label -- never a fabricated
exchange name.
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

    # MINT_KNOWN_EXCHANGE: identified boundary name present.
    conn.execute(
        "INSERT INTO wt_attribution_outcomes VALUES "
        "('MINT_KNOWN_EXCHANGE','KNOWN_CEX_REACHED','stop',"
        "'CexAddr1','wallet','HIGH',?,NULL,0,0,?,NULL,?)",
        (
            json.dumps({"boundary": {"name": "Binance", "address": "CexAddr1", "entity_type": "CEX"}}),
            now - 3600, now - 3600,
        ),
    )

    # MINT_UNIDENTIFIED_EXCHANGE: KNOWN_CEX_REACHED but no name in boundary.
    conn.execute(
        "INSERT INTO wt_attribution_outcomes VALUES "
        "('MINT_UNIDENTIFIED_EXCHANGE','KNOWN_CEX_REACHED','stop',"
        "'CexAddr2','wallet','MEDIUM',?,NULL,0,0,?,NULL,?)",
        (
            json.dumps({"boundary": {"address": "CexAddr2", "entity_type": "CEX"}}),
            now - 3600, now - 3600,
        ),
    )

    # MINT_NOT_CEX: a different outcome type -- must never get an exchange name.
    conn.execute(
        "INSERT INTO wt_attribution_outcomes VALUES "
        "('MINT_NOT_CEX','LINEAGE_GAP','stop',"
        "NULL,'wallet','LOW',?,NULL,0,1,?,NULL,?)",
        (json.dumps({}), now - 3600, now - 3600),
    )

    conn.commit()
    conn.close()


def _core_db(path):
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE token_analysis (mint TEXT PRIMARY KEY, created_at TEXT, migrated_at INTEGER,"
        " pf_ws_creator TEXT, earliest_tx_creator TEXT, create_tx_signature TEXT)"
    )
    now = int(time.time())
    for mint in ("MINT_KNOWN_EXCHANGE", "MINT_UNIDENTIFIED_EXCHANGE", "MINT_NOT_CEX"):
        conn.execute(
            "INSERT INTO token_analysis (mint, created_at) VALUES (?, ?)", (mint, str(now - 3600)),
        )
    conn.commit()
    conn.close()


def test_identified_exchange_name_is_exposed_on_the_record(tmp_path):
    ops_path, core_path = str(tmp_path / "ops.db"), str(tmp_path / "core.db")
    _ops_db(ops_path)
    _core_db(core_path)

    intel = build_operational_intelligence(ops_path, core_path, window_seconds=365 * 86400)
    record = intel["records"]["MINT_KNOWN_EXCHANGE"]
    assert record["cex_exchange_name"] == "Binance"


def test_unidentified_exchange_boundary_yields_none_not_a_fabricated_name(tmp_path):
    ops_path, core_path = str(tmp_path / "ops.db"), str(tmp_path / "core.db")
    _ops_db(ops_path)
    _core_db(core_path)

    intel = build_operational_intelligence(ops_path, core_path, window_seconds=365 * 86400)
    record = intel["records"]["MINT_UNIDENTIFIED_EXCHANGE"]
    assert record["cex_exchange_name"] is None


def test_non_cex_outcome_never_gets_an_exchange_name(tmp_path):
    ops_path, core_path = str(tmp_path / "ops.db"), str(tmp_path / "core.db")
    _ops_db(ops_path)
    _core_db(core_path)

    intel = build_operational_intelligence(ops_path, core_path, window_seconds=365 * 86400)
    record = intel["records"]["MINT_NOT_CEX"]
    assert record["cex_exchange_name"] is None
