from __future__ import annotations

import hashlib
import json
import sqlite3

from src.ops.watchtower_shadow_corpus import PopulationLimits, WatchtowerShadowCorpusMaterializer


SIGNATURE = "1" * 88
MINT = "Mint111111111111111111111111111111111111111"
TREASURY = "Treasury11111111111111111111111111111111111"
CREATOR = "Creator111111111111111111111111111111111111"


def _sources(tmp_path):
    operations = tmp_path / "operations.db"
    connection = sqlite3.connect(operations)
    connection.executescript("""
        CREATE TABLE wt_confirmed_treasuries(
          treasury TEXT, transfer_pct INTEGER, out_sol REAL, recipients INTEGER,
          micro_pings INTEGER, method TEXT, confidence TEXT, confirmed_at INTEGER,
          provenance TEXT, no_subscribe INTEGER);
        CREATE TABLE wt_watchtower_launches(
          mint TEXT, creator_wallet TEXT, create_signature TEXT, create_time INTEGER,
          treasury_wallet TEXT, subprov_wallet TEXT, wrap_close_signature TEXT);
        CREATE TABLE wt_provisioning_edges(
          edge_id TEXT, edge_type TEXT, from_wallet TEXT, to_wallet TEXT,
          first_observed_by_flex INTEGER, last_observed_by_flex INTEGER,
          observation_count INTEGER, funding_mechanism TEXT, funding_amount_sol REAL,
          funding_tx_signature TEXT, funding_block_time INTEGER, source_mint TEXT,
          provenance TEXT);
        CREATE TABLE operator_entities(
          operator_id TEXT, entity_address TEXT, entity_type TEXT, confidence TEXT,
          evidence_count INTEGER, first_seen INTEGER, last_seen INTEGER, added_at INTEGER);
    """)
    connection.execute("INSERT INTO wt_confirmed_treasuries VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (TREASURY, 100, 1.0, 1, 0, "fixture", "HIGH", 100, "fixture", 0))
    connection.execute("INSERT INTO wt_watchtower_launches VALUES(?,?,?,?,?,?,?)",
                       (MINT, CREATOR, SIGNATURE, 100, TREASURY, TREASURY, None))
    connection.execute("INSERT INTO wt_provisioning_edges VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       ("edge-1", "TREASURY_TO_SUBPROV", TREASURY, CREATOR, 100, 100,
                        1, "PLAIN_XFER", 0.001, SIGNATURE, 100, MINT, "fixture"))
    connection.execute("INSERT INTO operator_entities VALUES(?,?,?,?,?,?,?,?)",
                       ("04265d9f-6eb2-568c-a49e-9253091a4dbb", TREASURY,
                        "TREASURY", "HIGH", 1, 100, 100, 100))
    connection.commit(); connection.close()

    cache = tmp_path / "cache.db"
    connection = sqlite3.connect(cache)
    connection.execute("CREATE TABLE tf_transaction_cache(signature TEXT,block_time INTEGER,transaction_json TEXT,fetched_at INTEGER,source TEXT,rpc_verified INTEGER,parse_status TEXT)")
    transaction = {
        "slot": 1, "blockTime": 100, "version": "legacy",
        "transaction": {"signatures": [SIGNATURE], "message": {
            "accountKeys": [
                {"pubkey": TREASURY, "signer": True, "writable": True, "source": "transaction"},
                {"pubkey": CREATOR, "signer": True, "writable": True, "source": "transaction"},
                {"pubkey": MINT, "signer": False, "writable": True, "source": "transaction"},
            ],
            "recentBlockhash": "blockhash", "instructions": [
                {"programId": "11111111111111111111111111111111", "accounts": [],
                 "parsed": {"type": "transfer", "info": {
                     "source": TREASURY, "destination": CREATOR, "lamports": 1_000_000,
                 }}},
                {"programId": "Pump111111111111111111111111111111111111", "accounts": [],
                 "parsed": {"type": "create", "info": {"mint": MINT, "creator": CREATOR}}},
            ],
        }},
        "meta": {"err": None, "fee": 5000, "preBalances": [2_000_000, 0, 0],
                 "postBalances": [995_000, 1_000_000, 0], "innerInstructions": [],
                 "logMessages": [], "preTokenBalances": [], "postTokenBalances": []},
    }
    connection.execute("INSERT INTO tf_transaction_cache VALUES(?,?,?,?,?,?,?)",
                       (SIGNATURE, 100, json.dumps(transaction), 101, "fixture_cache", 1, "PARSED"))
    connection.commit(); connection.close()
    return operations, cache


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_shadow_corpus_is_no_rpc_isolated_replayable_and_deterministic(tmp_path):
    operations, cache = _sources(tmp_path)
    before = (_hash(operations), _hash(cache))
    output = tmp_path / "shadow"
    materializer = WatchtowerShadowCorpusMaterializer(
        operations_db=operations, transaction_cache_db=cache, output_root=output,
        limits=PopulationLimits(1, 1, 1, 1), clock=200,
    )
    first = materializer.materialize()
    assert (_hash(operations), _hash(cache)) == before
    assert first["manifest"]["additional_rpc"] == 0
    assert first["manifest"]["detectors_executed"] == 0
    assert first["replay"]["identical"] is True
    assert first["coverage"]["evidence_complete_launches"] == 1
    assert first["coverage"]["primitive_complete_launches"] == 1
    assert first["coverage"]["runtime_ready_launches"] == 1
    assert first["coverage"]["primitive_types"]["SYSTEM_TRANSFER"] == 1

    second = materializer.materialize()
    assert second["manifest"]["semantic_digests"] == first["manifest"]["semantic_digests"]
    assert second["writer"]["inserted"] == 0
    assert second["primitive"]["inserted"] == 0


def test_missing_artifact_has_explicit_launch_reason(tmp_path):
    operations, cache = _sources(tmp_path)
    connection = sqlite3.connect(cache); connection.execute("DELETE FROM tf_transaction_cache")
    connection.commit(); connection.close()
    output = tmp_path / "shadow"
    result = WatchtowerShadowCorpusMaterializer(
        operations_db=operations, transaction_cache_db=cache, output_root=output,
        limits=PopulationLimits(1, 1, 1, 1), clock=200,
    ).materialize()
    coverage = json.loads((output / "coverage.json").read_text())
    launch = coverage["launches"][0]
    assert result["coverage"]["raw_artifacts_missing"] == 1
    assert launch["ready_for_runtime"] is False
    assert "RAW_ARTIFACT_NOT_IN_TRANSACTION_CACHE" in launch["reasons"]
