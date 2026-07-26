"""X65.44 — Promote Walkback-Confirmed WATCHTOWER Launches into the
Canonical Registry.

wt_watchtower_launches is the authoritative WATCHTOWER registry (X65.41).
Walkback attribution (wt_attribution_outcomes) can independently confirm
canonical WATCHTOWER status by reaching outcome_type=CANONICAL_OPERATOR_
REACHED with operator_id=WATCHTOWER_OPERATOR_ID -- but historically this
confirmation was never promoted into the registry, leaving 76 of 98
historical confirmations (including 18 in the prior 24h) invisible to
every Discovery section keyed on registry membership. This module adds
the missing promotion, idempotent and additive, reusing the existing
authoritative writer (ws_cascade_store.record_launch).
"""
from __future__ import annotations

import json
import sqlite3
import time

import pytest

from src.core.watchtower_registry_promotion import (
    is_canonical_watchtower_outcome,
    promote_walkback_confirmed_watchtower,
    reconcile_missing_promotions,
)
from src.ops.watchtower_alignment import WATCHTOWER_OPERATOR_ID


def _ops_db(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE wt_attribution_outcomes (
            mint TEXT PRIMARY KEY, outcome_type TEXT, stop_reason TEXT,
            terminal_entity TEXT, terminal_entity_type TEXT, confidence TEXT,
            evidence_json TEXT, operator_id TEXT,
            should_seed_emerging_operator INTEGER, should_retry INTEGER,
            completed_at INTEGER, source_queue_updated_at INTEGER,
            materialized_at INTEGER
        );
        CREATE TABLE wt_watchtower_launches (
            id INTEGER PRIMARY KEY AUTOINCREMENT, mint TEXT, creator_wallet TEXT NOT NULL,
            create_signature TEXT, create_time INTEGER, create_slot INTEGER,
            treasury_wallet TEXT, subprov_wallet TEXT, subprov_funding_sol REAL,
            wrap_close_sol REAL, wrap_close_signature TEXT,
            birth_to_launch_seconds INTEGER, create_to_migration_secs INTEGER,
            detection_source TEXT, detection_delay_seconds INTEGER,
            funding_mechanism TEXT DEFAULT 'WSOL_WRAP_CLOSE',
            creator_extraction_method TEXT DEFAULT 'CLOSE_ACCOUNT_DESTINATION',
            confidence TEXT DEFAULT 'STRICT', state TEXT DEFAULT 'FIRED_CREATE',
            recorded_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
            UNIQUE(creator_wallet, create_signature)
        );
        CREATE TABLE wt_walkback_queue (
            mint TEXT PRIMARY KEY, creator TEXT, subprov TEXT, treasury TEXT,
            funding_mechanism TEXT, create_anchor_signature TEXT,
            create_anchor_block_time INTEGER, funder_sig TEXT, funder_amount_sol REAL
        );
        CREATE TABLE wt_candidate_websocket_watches (
            candidate_wallet TEXT, subprov_wallet TEXT, state TEXT, close_reason TEXT, closed_at INTEGER
        );
        """
    )
    conn.commit()
    return conn


def _core_db(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE token_analysis (mint TEXT PRIMARY KEY, created_at TEXT)")
    conn.commit()
    return conn


def _insert_outcome(conn, mint, *, outcome_type, operator_id, evidence, completed_at):
    conn.execute(
        "INSERT INTO wt_attribution_outcomes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (mint, outcome_type, "stop", operator_id, "wallet", "HIGH",
         json.dumps(evidence), operator_id, 0, 0, completed_at, None, completed_at),
    )
    conn.commit()


def test_confirmation_predicate_requires_both_outcome_type_and_operator_id():
    assert is_canonical_watchtower_outcome("CANONICAL_OPERATOR_REACHED", WATCHTOWER_OPERATOR_ID) is True
    assert is_canonical_watchtower_outcome("CANONICAL_OPERATOR_REACHED", "some-other-operator") is False
    assert is_canonical_watchtower_outcome("KNOWN_CEX_REACHED", None) is False
    assert is_canonical_watchtower_outcome("INSUFFICIENT_EVIDENCE", None) is False
    assert is_canonical_watchtower_outcome(None, None) is False


def test_strict_canonical_operator_reached_promotes_the_mint(tmp_path):
    ops = _ops_db(str(tmp_path / "ops.db"))
    now = int(time.time())
    ops.execute(
        "INSERT INTO wt_walkback_queue VALUES (?,?,?,?,?,?,?,?,?)",
        ("MINT_A", "CreatorA", "SubprovA", "TreasuryA", "WSOL_WRAP_CLOSE",
         "sig123", now - 3600, "wrapsig456", 2.5),
    )
    ops.commit()

    result = promote_walkback_confirmed_watchtower(
        ops, "MINT_A",
        outcome_type="CANONICAL_OPERATOR_REACHED", operator_id=WATCHTOWER_OPERATOR_ID,
        evidence={"creator": "CreatorA", "treasuries": ["TreasuryA"], "subprovisioners": ["SubprovA"]},
        completed_at=now,
    )
    assert result["action"] == "promoted"

    row = ops.execute("SELECT * FROM wt_watchtower_launches WHERE mint='MINT_A'").fetchone()
    assert row is not None
    assert row["creator_wallet"] == "CreatorA"
    assert row["treasury_wallet"] == "TreasuryA"
    assert row["subprov_wallet"] == "SubprovA"
    assert row["confidence"] == "WALKBACK"
    assert row["creator_extraction_method"] == "WALKBACK_RECOVERED"
    assert row["create_signature"] == "sig123"
    assert row["wrap_close_signature"] == "wrapsig456"
    assert row["wrap_close_sol"] == 2.5


def test_generic_non_null_operator_id_does_not_promote(tmp_path):
    ops = _ops_db(str(tmp_path / "ops.db"))
    result = promote_walkback_confirmed_watchtower(
        ops, "MINT_B",
        outcome_type="UNKNOWN_INFRASTRUCTURE", operator_id="some-other-operator-uuid",
        evidence={"creator": "CreatorB"}, completed_at=int(time.time()),
    )
    assert result["action"] == "not_eligible"
    row = ops.execute("SELECT * FROM wt_watchtower_launches WHERE mint='MINT_B'").fetchone()
    assert row is None


@pytest.mark.parametrize("outcome_type", [
    "KNOWN_CEX_REACHED", "KNOWN_BRIDGE_REACHED", "KNOWN_RELAY_REACHED",
    "KNOWN_MULTI_TOKEN_CREATOR", "UNKNOWN_INFRASTRUCTURE", "LINEAGE_GAP",
    "AMBIGUOUS_BRANCH", "MAX_DEPTH", "INSUFFICIENT_EVIDENCE",
])
def test_non_canonical_outcome_types_never_promote(tmp_path, outcome_type):
    ops = _ops_db(str(tmp_path / "ops.db"))
    result = promote_walkback_confirmed_watchtower(
        ops, "MINT_C",
        outcome_type=outcome_type, operator_id=None,
        evidence={"creator": "CreatorC"}, completed_at=int(time.time()),
    )
    assert result["action"] == "not_eligible"
    assert ops.execute("SELECT * FROM wt_watchtower_launches WHERE mint='MINT_C'").fetchone() is None


def test_reprocessing_the_same_mint_is_idempotent(tmp_path):
    ops = _ops_db(str(tmp_path / "ops.db"))
    now = int(time.time())
    ops.execute(
        "INSERT INTO wt_walkback_queue VALUES (?,?,?,?,?,?,?,?,?)",
        ("MINT_D", "CreatorD", "SubprovD", "TreasuryD", "WSOL_WRAP_CLOSE",
         None, None, "wrapsigD", 1.0),  # NULL create_anchor_signature -- the exact idempotency risk
    )
    ops.commit()

    kwargs = dict(
        outcome_type="CANONICAL_OPERATOR_REACHED", operator_id=WATCHTOWER_OPERATOR_ID,
        evidence={"creator": "CreatorD", "treasuries": ["TreasuryD"], "subprovisioners": ["SubprovD"]},
        completed_at=now,
    )
    r1 = promote_walkback_confirmed_watchtower(ops, "MINT_D", **kwargs)
    r2 = promote_walkback_confirmed_watchtower(ops, "MINT_D", **kwargs)
    r3 = promote_walkback_confirmed_watchtower(ops, "MINT_D", **kwargs)

    assert r1["action"] == "promoted"
    assert r2["action"] == "already_present"
    assert r3["action"] == "already_present"

    count = ops.execute("SELECT COUNT(*) FROM wt_watchtower_launches WHERE mint='MINT_D'").fetchone()[0]
    assert count == 1  # never duplicated, despite NULL create_signature


def test_does_not_overwrite_existing_stronger_registry_data(tmp_path):
    ops = _ops_db(str(tmp_path / "ops.db"))
    now = int(time.time())
    # Pre-existing STRICT live-detection row for this mint.
    ops.execute(
        "INSERT INTO wt_watchtower_launches "
        "(mint,creator_wallet,create_signature,create_time,treasury_wallet,subprov_wallet,confidence,creator_extraction_method) "
        "VALUES ('MINT_E','StrictCreator','strictsig','1000','StrictTreasury','StrictSubprov','STRICT','CLOSE_ACCOUNT_DESTINATION')"
    )
    ops.commit()

    result = promote_walkback_confirmed_watchtower(
        ops, "MINT_E",
        outcome_type="CANONICAL_OPERATOR_REACHED", operator_id=WATCHTOWER_OPERATOR_ID,
        evidence={"creator": "WalkbackCreator", "treasuries": ["WalkbackTreasury"]},
        completed_at=now,
    )
    assert result["action"] == "already_present"

    row = ops.execute("SELECT * FROM wt_watchtower_launches WHERE mint='MINT_E'").fetchone()
    assert row["creator_wallet"] == "StrictCreator"  # untouched
    assert row["confidence"] == "STRICT"  # untouched
    assert row["treasury_wallet"] == "StrictTreasury"  # untouched
    count = ops.execute("SELECT COUNT(*) FROM wt_watchtower_launches WHERE mint='MINT_E'").fetchone()[0]
    assert count == 1


def test_missing_creator_evidence_does_not_fabricate_a_row(tmp_path):
    ops = _ops_db(str(tmp_path / "ops.db"))
    result = promote_walkback_confirmed_watchtower(
        ops, "MINT_F",
        outcome_type="CANONICAL_OPERATOR_REACHED", operator_id=WATCHTOWER_OPERATOR_ID,
        evidence={},  # no creator anywhere
        completed_at=int(time.time()),
    )
    assert result["action"] == "missing_evidence"
    assert ops.execute("SELECT * FROM wt_watchtower_launches WHERE mint='MINT_F'").fetchone() is None


def test_promotion_failure_is_visible_and_recoverable(tmp_path, monkeypatch):
    ops = _ops_db(str(tmp_path / "ops.db"))
    now = int(time.time())
    ops.execute(
        "INSERT INTO wt_walkback_queue VALUES (?,?,?,?,?,?,?,?,?)",
        ("MINT_G", "CreatorG", "SubprovG", "TreasuryG", "WSOL_WRAP_CLOSE", None, None, None, None),
    )
    ops.commit()

    from src.core import ws_cascade_store as store
    def _boom(*args, **kwargs):
        raise RuntimeError("simulated write failure")
    monkeypatch.setattr(store, "record_launch", _boom)

    result = promote_walkback_confirmed_watchtower(
        ops, "MINT_G",
        outcome_type="CANONICAL_OPERATOR_REACHED", operator_id=WATCHTOWER_OPERATOR_ID,
        evidence={"creator": "CreatorG", "treasuries": ["TreasuryG"]},
        completed_at=now,
    )
    assert result["action"] == "failed"
    assert "simulated write failure" in result["error"]

    # Recoverable: retrying after the transient failure clears succeeds normally.
    monkeypatch.undo()
    retry = promote_walkback_confirmed_watchtower(
        ops, "MINT_G",
        outcome_type="CANONICAL_OPERATOR_REACHED", operator_id=WATCHTOWER_OPERATOR_ID,
        evidence={"creator": "CreatorG", "treasuries": ["TreasuryG"]},
        completed_at=now,
    )
    assert retry["action"] == "promoted"


def test_create_time_falls_back_to_token_analysis_when_walkback_queue_lacks_it(tmp_path):
    ops = _ops_db(str(tmp_path / "ops.db"))
    core = _core_db(str(tmp_path / "core.db"))
    now = int(time.time())
    ops.execute(
        "INSERT INTO wt_walkback_queue VALUES (?,?,?,?,?,?,?,?,?)",
        ("MINT_H", "CreatorH", "SubprovH", "TreasuryH", "WSOL_WRAP_CLOSE", None, None, None, None),
    )
    ops.commit()
    core.execute("INSERT INTO token_analysis VALUES ('MINT_H', ?)", (str(now - 7200),))
    core.commit()

    result = promote_walkback_confirmed_watchtower(
        ops, "MINT_H",
        outcome_type="CANONICAL_OPERATOR_REACHED", operator_id=WATCHTOWER_OPERATOR_ID,
        evidence={"creator": "CreatorH", "treasuries": ["TreasuryH"]},
        completed_at=now, core_conn=core,
    )
    assert result["action"] == "promoted"
    row = ops.execute("SELECT create_time FROM wt_watchtower_launches WHERE mint='MINT_H'").fetchone()
    assert row["create_time"] == now - 7200


def test_reconcile_missing_promotions_dry_run_reports_without_writing(tmp_path):
    ops = _ops_db(str(tmp_path / "ops.db"))
    now = int(time.time())
    _insert_outcome(
        ops, "MINT_I", outcome_type="CANONICAL_OPERATOR_REACHED", operator_id=WATCHTOWER_OPERATOR_ID,
        evidence={"creator": "CreatorI", "treasuries": ["TreasuryI"]}, completed_at=now,
    )
    _insert_outcome(
        ops, "MINT_NOT_WT", outcome_type="INSUFFICIENT_EVIDENCE", operator_id=None,
        evidence={}, completed_at=now,
    )

    result = reconcile_missing_promotions(ops, dry_run=True)
    assert result["eligible"] == 1
    assert result["already_present"] == 0
    assert result["inserted"] == 1
    assert result["dry_run"] is True
    assert ops.execute("SELECT * FROM wt_watchtower_launches WHERE mint='MINT_I'").fetchone() is None


def test_reconcile_missing_promotions_is_idempotent(tmp_path):
    ops = _ops_db(str(tmp_path / "ops.db"))
    now = int(time.time())
    _insert_outcome(
        ops, "MINT_J", outcome_type="CANONICAL_OPERATOR_REACHED", operator_id=WATCHTOWER_OPERATOR_ID,
        evidence={"creator": "CreatorJ", "treasuries": ["TreasuryJ"]}, completed_at=now,
    )

    first = reconcile_missing_promotions(ops, dry_run=False)
    second = reconcile_missing_promotions(ops, dry_run=False)

    assert first["inserted"] == 1
    assert first["already_present"] == 0
    assert second["inserted"] == 0
    assert second["already_present"] == 1

    count = ops.execute("SELECT COUNT(*) FROM wt_watchtower_launches WHERE mint='MINT_J'").fetchone()[0]
    assert count == 1


def test_reconcile_never_demotes_or_overwrites_existing_registry_rows(tmp_path):
    ops = _ops_db(str(tmp_path / "ops.db"))
    now = int(time.time())
    ops.execute(
        "INSERT INTO wt_watchtower_launches "
        "(mint,creator_wallet,create_signature,confidence,creator_extraction_method) "
        "VALUES ('MINT_K','ExistingCreator','existingsig','STRICT','CLOSE_ACCOUNT_DESTINATION')"
    )
    ops.commit()
    _insert_outcome(
        ops, "MINT_K", outcome_type="CANONICAL_OPERATOR_REACHED", operator_id=WATCHTOWER_OPERATOR_ID,
        evidence={"creator": "DifferentCreator", "treasuries": ["DifferentTreasury"]}, completed_at=now,
    )

    result = reconcile_missing_promotions(ops, dry_run=False)
    assert result["already_present"] == 1
    assert result["inserted"] == 0

    row = ops.execute("SELECT * FROM wt_watchtower_launches WHERE mint='MINT_K'").fetchone()
    assert row["creator_wallet"] == "ExistingCreator"
    assert row["confidence"] == "STRICT"


def test_reconcile_reports_failed_count_and_details(tmp_path, monkeypatch):
    ops = _ops_db(str(tmp_path / "ops.db"))
    now = int(time.time())
    _insert_outcome(
        ops, "MINT_L", outcome_type="CANONICAL_OPERATOR_REACHED", operator_id=WATCHTOWER_OPERATOR_ID,
        evidence={"creator": "CreatorL", "treasuries": ["TreasuryL"]}, completed_at=now,
    )

    from src.core import ws_cascade_store as store
    def _boom(*args, **kwargs):
        raise RuntimeError("simulated")
    monkeypatch.setattr(store, "record_launch", _boom)

    result = reconcile_missing_promotions(ops, dry_run=False)
    assert result["failed_count"] == 1
    assert result["failed"][0]["mint"] == "MINT_L"
    assert "simulated" in result["failed"][0]["error"]
