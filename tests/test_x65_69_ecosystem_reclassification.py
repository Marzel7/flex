"""X65.69 -- Reclassify Historical CEX Provisioning Candidates into Ecosystem
Intelligence. Verifies: selection query correctness (only session-confirmed,
non-confirmed-launch, is_known_account subprovs), idempotent migration,
evidence preservation, and that every canonical table is left untouched."""
import json
import sqlite3

import pytest

from src.ops.ecosystem_intelligence import (
    ensure_schema,
    find_historical_cex_candidates,
    migrate_historical_cex_candidates,
    list_ecosystem_exchange_interactions,
)


@pytest.fixture
def ops_db(tmp_path, monkeypatch):
    path = tmp_path / "ops.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE wt_watchtower_launches (mint TEXT, subprov_wallet TEXT)")
    conn.execute(
        "CREATE TABLE wt_attribution_outcomes (mint TEXT PRIMARY KEY, outcome_type TEXT, "
        "confidence TEXT, evidence_json TEXT, completed_at INTEGER)"
    )
    conn.execute(
        "CREATE TABLE wt_active_subprov_sessions (subprov_wallet TEXT, treasury_wallet TEXT, "
        "funding_signature TEXT, funding_amount REAL, funding_time INTEGER, "
        "funding_mechanism TEXT, open_reason TEXT)"
    )
    conn.execute("CREATE TABLE wt_confirmed_treasuries (treasury TEXT)")
    conn.execute("CREATE TABLE wt_ops_v2 (operation_uuid TEXT)")
    conn.execute("CREATE TABLE wt_ops_v2_wallets (wallet TEXT)")
    conn.execute("CREATE TABLE attribution_evidence (id INTEGER PRIMARY KEY)")

    # A known CEX wallet from the real registry, used as subprov.
    cex_wallet = "5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9"  # Binance 2
    genuine_wallet = "GENUINE_SUBPROV_WALLET_NOT_IN_REGISTRY_00000001"

    conn.execute(
        "INSERT INTO wt_attribution_outcomes VALUES (?,?,?,?,?)",
        ("CEX_MINT", "KNOWN_CEX_REACHED", "HIGH",
         json.dumps({"subprovisioners": [cex_wallet], "treasuries": [], "creator": "CREATOR1",
                     "boundary": {"name": "Binance", "entity_type": "CEX"}}), 1000),
    )
    conn.execute(
        "INSERT INTO wt_attribution_outcomes VALUES (?,?,?,?,?)",
        ("GENUINE_MINT", "LINEAGE_GAP", "MEDIUM",
         json.dumps({"subprovisioners": [genuine_wallet], "treasuries": [], "creator": "CREATOR2"}), 1000),
    )
    # A CEX-subprov mint that is ALREADY a confirmed launch -- must be excluded.
    conn.execute(
        "INSERT INTO wt_attribution_outcomes VALUES (?,?,?,?,?)",
        ("ALREADY_CONFIRMED_MINT", "CANONICAL_OPERATOR_REACHED", "HIGH",
         json.dumps({"subprovisioners": [cex_wallet], "treasuries": [], "creator": "CREATOR3"}), 1000),
    )
    conn.execute("INSERT INTO wt_watchtower_launches VALUES (?,?)", ("ALREADY_CONFIRMED_MINT", cex_wallet))

    # A CEX-subprov mint with NO session row -- must be excluded (bare mention only, no real evidence).
    conn.execute(
        "INSERT INTO wt_attribution_outcomes VALUES (?,?,?,?,?)",
        ("UNSESSIONED_CEX_MINT", "KNOWN_CEX_REACHED", "HIGH",
         json.dumps({"subprovisioners": ["UNSESSIONED_CEX_WALLET_NOT_REAL"], "treasuries": []}), 1000),
    )

    conn.execute(
        "INSERT INTO wt_active_subprov_sessions VALUES (?,?,?,?,?,?,?)",
        (cex_wallet, "TREASURY1", "SIG123", 5.5, 2000, "WSOL_WRAP_CLOSE", "SUBPROV_REACTIVATED"),
    )
    conn.execute(
        "INSERT INTO wt_active_subprov_sessions VALUES (?,?,?,?,?,?,?)",
        (genuine_wallet, "TREASURY2", "SIG456", 3.3, 3000, "WSOL_WRAP_CLOSE", "PROVISION_CANDIDATE"),
    )
    conn.commit()
    conn.close()
    return str(path), cex_wallet, genuine_wallet


def test_selection_includes_only_session_confirmed_cex_candidate(ops_db):
    path, cex_wallet, genuine_wallet = ops_db
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    results = find_historical_cex_candidates(conn)
    conn.close()
    mints = {r["mint"] for r in results}
    assert mints == {"CEX_MINT"}


def test_genuine_subprov_never_selected(ops_db):
    path, _, genuine_wallet = ops_db
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    results = find_historical_cex_candidates(conn)
    conn.close()
    assert all(r["exchange_wallet"] != genuine_wallet for r in results)


def test_already_confirmed_launch_never_selected(ops_db):
    path, _, _ = ops_db
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    results = find_historical_cex_candidates(conn)
    conn.close()
    assert all(r["mint"] != "ALREADY_CONFIRMED_MINT" for r in results)


def test_unsessioned_bare_mention_never_selected(ops_db):
    path, _, _ = ops_db
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    results = find_historical_cex_candidates(conn)
    conn.close()
    assert all(r["mint"] != "UNSESSIONED_CEX_MINT" for r in results)


def test_evidence_fields_preserved(ops_db):
    path, cex_wallet, _ = ops_db
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    results = find_historical_cex_candidates(conn)
    conn.close()
    row = next(r for r in results if r["mint"] == "CEX_MINT")
    assert row["treasury_wallet"] == "TREASURY1"
    assert row["exchange_wallet"] == cex_wallet
    assert row["exchange_name"] == "Binance"
    assert row["creator_wallet"] == "CREATOR1"
    assert row["funding_mechanism"] == "WSOL_WRAP_CLOSE"
    assert row["funding_signature"] == "SIG123"
    assert row["funding_amount"] == 5.5
    assert row["funding_time"] == 2000
    assert row["walkback_confidence"] == "HIGH"
    assert json.loads(row["walkback_evidence_json"])["boundary"]["name"] == "Binance"


def test_migration_writes_only_to_ecosystem_table(ops_db):
    path, cex_wallet, _ = ops_db

    def _counts():
        conn = sqlite3.connect(path)
        c = {
            "wt_confirmed_treasuries": conn.execute("SELECT COUNT(*) FROM wt_confirmed_treasuries").fetchone()[0],
            "wt_ops_v2": conn.execute("SELECT COUNT(*) FROM wt_ops_v2").fetchone()[0],
            "wt_ops_v2_wallets": conn.execute("SELECT COUNT(*) FROM wt_ops_v2_wallets").fetchone()[0],
            "wt_watchtower_launches": conn.execute("SELECT COUNT(*) FROM wt_watchtower_launches").fetchone()[0],
            "attribution_evidence": conn.execute("SELECT COUNT(*) FROM attribution_evidence").fetchone()[0],
            "wt_attribution_outcomes": conn.execute("SELECT COUNT(*) FROM wt_attribution_outcomes").fetchone()[0],
            "wt_active_subprov_sessions": conn.execute("SELECT COUNT(*) FROM wt_active_subprov_sessions").fetchone()[0],
        }
        conn.close()
        return c

    before = _counts()
    report = migrate_historical_cex_candidates(ops_db_path=path)
    after = _counts()

    assert before == after
    assert report["candidates_found"] == 1
    assert report["newly_added"] == 1

    rows = list_ecosystem_exchange_interactions(ops_db_path=path)
    assert len(rows) == 1
    assert rows[0]["mint"] == "CEX_MINT"
    assert rows[0]["exchange_wallet"] == cex_wallet
    assert rows[0]["reclassification_reason"] == "KNOWN_INFRASTRUCTURE_REGISTRY_MATCH"


def test_migration_is_idempotent(ops_db):
    path, _, _ = ops_db
    r1 = migrate_historical_cex_candidates(ops_db_path=path)
    r2 = migrate_historical_cex_candidates(ops_db_path=path)
    assert r1["after_count"] == r2["after_count"] == 1
    assert r2["newly_added"] == 0


def test_ensure_schema_is_idempotent(ops_db):
    path, _, _ = ops_db
    conn = sqlite3.connect(path)
    ensure_schema(conn)
    ensure_schema(conn)
    conn.execute("SELECT COUNT(*) FROM wt_ecosystem_exchange_interactions")
    conn.close()
