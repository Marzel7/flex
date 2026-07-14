import asyncio
import os
import sqlite3
import tempfile
import time

from src.core import pumpfun_curve_listener as listener_mod
from src.core.pumpfun_curve_listener import PUMPFUN_PROGRAM, PumpFunCurveListener


def _build_listener():
    listener = PumpFunCurveListener.__new__(PumpFunCurveListener)
    listener._bonding_curve_to_mint = {}
    listener._known_bonding_curve_mints = set()
    listener._recent_birth_mints = {}
    listener._recent_birth_cache_ttl_seconds = 20 * 60
    listener._bonding_curve_index_last_rowid = 0
    listener._bonding_curve_refresh_interval_seconds = 15
    listener._last_bonding_curve_refresh_monotonic = 0.0
    listener._last_bonding_curve_refresh_failure_monotonic = 0.0
    listener._bonding_curve_refresh_failure_cooldown_seconds = 2.0
    listener._bonding_curve_refresh_retry_attempts = 3
    listener._bonding_curve_refresh_retry_backoff_seconds = 0.01
    listener._refresh_bonding_curve_index = lambda *args, **kwargs: 0
    listener._lookup_recent_unresolved_mint_in_db = lambda candidates: (None, None)
    listener._premigration_signal_floor_warm = 50000.0
    listener._premigration_signal_floor_hot = 58000.0
    listener._resolver_resolved_count = 0
    listener._resolver_unresolved_count = 0
    listener.tx_cache = {}
    listener.tx_cache_ttl_seconds = 1800
    listener.call_background_rpc = None
    listener.db_lock = asyncio.Lock()
    listener._flow_windows_by_mint = {}
    listener._last_market_cap_by_mint = {}
    return listener


def _init_token_analysis_db(db_path: str):
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE token_analysis (
            mint TEXT PRIMARY KEY,
            analyzed_at REAL,
            created_at NUM,
            source_platform TEXT,
            lifecycle_stage TEXT DEFAULT 'migration_pending',
            dex TEXT,
            pool_address TEXT,
            pumpswap_pool_address TEXT,
            migration_tx TEXT,
            market_cap_current REAL,
            price_updated_at INTEGER,
            create_tx_signature TEXT,
            bonding_curve_pda TEXT,
            is_about_to_migrate INTEGER DEFAULT 0,
            migration_band TEXT,
            migration_progress_pct REAL,
            migration_signal_source TEXT,
            migration_signal_updated_at INTEGER
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE pumpfun_migration_verification (
            mint TEXT PRIMARY KEY,
            migrated_at INTEGER,
            migration_tx TEXT,
            dex TEXT,
            pumpswap_pool_address TEXT,
            pre_is_about_to_migrate INTEGER DEFAULT 0,
            pre_migration_band TEXT,
            pre_migration_progress_pct REAL,
            pre_migration_signal_updated_at INTEGER,
            pre_market_cap_current REAL,
            pre_market_cap_updated_at INTEGER,
            pre_buys_10s INTEGER DEFAULT 0,
            pre_unique_30s INTEGER DEFAULT 0,
            pre_sol_15s REAL DEFAULT 0,
            pre_inflow_accel REAL DEFAULT 0,
            pre_signal_score INTEGER DEFAULT 0,
            pre_migration_signal_source TEXT,
            predicted_by_flow INTEGER DEFAULT 0,
            predicted_by_market_cap INTEGER DEFAULT 0,
            predicted_by_explicit_signal INTEGER DEFAULT 0,
            was_about_to_migrate_at_migration INTEGER DEFAULT 0,
            was_hot_or_warm_before_migration INTEGER DEFAULT 0,
            signal_age_seconds INTEGER,
            signal_was_fresh INTEGER DEFAULT 0,
            final_verdict TEXT,
            created_at INTEGER
        )
        """
    )
    conn.commit()
    conn.close()


def test_looks_like_pumpfun_mint_is_strict():
    listener = _build_listener()

    assert listener._looks_like_pumpfun_mint("7vfCXTUXx5rP1xjFNF4f3x8gKdpA2m5yFZQ6R3mwpump")
    assert not listener._looks_like_pumpfun_mint("")
    assert not listener._looks_like_pumpfun_mint(PUMPFUN_PROGRAM)
    assert not listener._looks_like_pumpfun_mint("not-a-solana-address-pump")
    assert not listener._looks_like_pumpfun_mint("7vfCXTUXx5rP1xjFNF4f3x8gKdpA2m5yFZQ6R3mwabcd")


def test_rejects_known_junk_candidates_before_mint_like_detection():
    listener = _build_listener()

    assert listener._is_definitely_not_mint_candidate(PUMPFUN_PROGRAM)
    assert listener._is_definitely_not_mint_candidate("AAAAAAAAAABfAAAAAAAAAB4AAAAAAAAA")
    assert listener._is_definitely_not_mint_candidate("TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb")
    assert not listener._is_definitely_not_mint_candidate("7vfCXTUXx5rP1xjFNF4f3x8gKdpA2m5yFZQ6R3mwpump")


def test_resolver_prefers_pda_map():
    listener = _build_listener()
    bonding_curve = "BCrvX2d9p7Kn2pc4m7mHkq9qTi4Z3YhVZ8J6iYbZV8n"
    mint = "7vfCXTUXx5rP1xjFNF4f3x8gKdpA2m5yFZQ6R3mwpump"
    listener._bonding_curve_to_mint[bonding_curve] = mint

    resolved, source, candidates, reason = listener._resolve_bonding_curve_mint_from_logs_detailed(
        [f"Program log: accounts={bonding_curve} buyer=abc"]
    )

    assert resolved == mint
    assert source == "pda_map"
    assert bonding_curve in candidates
    assert reason == f"bonding_curve={bonding_curve}"


def test_resolver_uses_explicit_mint_before_other_candidates():
    listener = _build_listener()
    mint = "7vfCXTUXx5rP1xjFNF4f3x8gKdpA2m5yFZQ6R3mwpump"
    listener._recent_birth_mints[mint] = time.time()

    resolved, source, _, reason = listener._resolve_bonding_curve_mint_from_logs_detailed(
        [f"Program data: mint={mint}"]
    )

    assert resolved == mint
    assert source == "explicit_mint"
    assert reason == f"explicit_mint={mint}"


def test_resolver_uses_recent_birth_cache():
    listener = _build_listener()
    mint = "7vfCXTUXx5rP1xjFNF4f3x8gKdpA2m5yFZQ6R3mwpump"
    listener._recent_birth_mints[mint] = time.time()

    resolved, source, candidates, reason = listener._resolve_bonding_curve_mint_from_logs_detailed(
        [f"Program data: accounts=[foo,{mint}]"]
    )

    assert resolved == mint
    assert source == "birth_cache"
    assert mint in candidates
    assert reason == f"recent_birth={mint}"


def test_resolver_uses_mint_candidate_when_only_mint_like_address_exists():
    listener = _build_listener()
    mint = "7vfCXTUXx5rP1xjFNF4f3x8gKdpA2m5yFZQ6R3mwpump"

    resolved, source, candidates, reason = listener._resolve_bonding_curve_mint_from_logs_detailed(
        [f"Program data: accounts=[abc,{mint}] buyer=wallet"]
    )

    assert resolved == mint
    assert source == "mint_candidate"
    assert mint in candidates
    assert reason == f"mint_like={mint}"


def test_resolver_filters_noise_out_of_ranked_candidates():
    listener = _build_listener()
    mint = "7vfCXTUXx5rP1xjFNF4f3x8gKdpA2m5yFZQ6R3mwpump"

    resolved, source, candidates, _ = listener._resolve_bonding_curve_mint_from_logs_detailed(
        [
            "Program data: accounts=[ComputeBudget111111111111111111111111111111,"
            "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb,"
            "AAAAAAAAAABfAAAAAAAAAB4AAAAAAAAA,"
            f"{mint}]",
        ]
    )

    assert resolved == mint
    assert source == "mint_candidate"
    assert candidates == [mint]


def test_extract_raw_candidates_prefers_pubkey_sized_tokens():
    listener = _build_listener()
    mint = "7vfCXTUXx5rP1xjFNF4f3x8gKdpA2m5yFZQ6R3mwpump"
    bonding_curve = "BCrvX2d9p7Kn2pc4m7mHkq9qTi4Z3YhVZ8J6iYbZV8n"

    candidates = listener._extract_raw_mint_resolution_candidates(
        [
            "Program log: Instruction: BuyExactIn",
            "Program data: AAAAAAAAAABfAAAAAAAAAB4AAAAAAAAA",
            f"Program log: accounts=[{PUMPFUN_PROGRAM},{bonding_curve},{mint}]",
        ]
    )

    assert mint in candidates
    assert bonding_curve in candidates
    assert "BuyExactIn" not in candidates
    assert "AAAAAAAAAABfAAAAAAAAAB4AAAAAAAAA" not in candidates


def test_handle_trade_logs_partial_buy_and_records_count_only_flow():
    listener = _build_listener()
    mint = "7vfCXTUXx5rP1xjFNF4f3x8gKdpA2m5yFZQ6R3mwpump"
    listener._flow_windows_by_mint = {}
    listener._last_market_cap_by_mint = {}
    listener._pumpfun_trade_debug_budget = 25
    listener._debug_pumpfun_trade_skip = lambda *args, **kwargs: None

    async def _persist(*args, **kwargs):
        return None

    listener._persist_pre_migration_signal = _persist

    captured = []
    original_log_print = listener_mod.log_print
    try:
        listener_mod.log_print = lambda message, flush=True: captured.append(message)
        asyncio.run(listener.handle_pumpfun_trade("sig1234567890", [f"Program data: mint={mint}"]))
    finally:
        listener_mod.log_print = original_log_print

    assert any("[BUY_PARTIAL]" in line for line in captured)
    assert mint in listener._flow_windows_by_mint
    assert len(listener._flow_windows_by_mint[mint]) == 1


def test_persist_flow_signal_creates_minimal_pumpfun_row_when_missing():
    listener = _build_listener()
    mint = "7vfCXTUXx5rP1xjFNF4f3x8gKdpA2m5yFZQ6R3mwpump"
    listener._flow_windows_by_mint = {
        mint: listener_mod.deque([{"ts": time.time(), "buyer": None, "sol_amount": None, "kind": "buy"}])
    }
    listener._last_market_cap_by_mint = {}

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "pumpfun_test.db")
        _init_token_analysis_db(db_path)
        original_db_path = listener_mod.DB_PATH
        try:
            listener_mod.DB_PATH = db_path
            asyncio.run(listener._persist_pre_migration_signal(mint, 0.0, int(time.time()), source_hint="flow"))
        finally:
            listener_mod.DB_PATH = original_db_path

        conn = sqlite3.connect(db_path)
        row = conn.execute(
            """
            SELECT source_platform, lifecycle_stage, migration_signal_source
            FROM token_analysis
            WHERE mint = ?
            """,
            (mint,),
        ).fetchone()
        conn.close()

    assert row == ("pumpfun", "bonding_curve", "flow")


def test_fallback_write_does_not_override_existing_flow_signal():
    listener = _build_listener()
    mint = "7vfCXTUXx5rP1xjFNF4f3x8gKdpA2m5yFZQ6R3mwpump"
    listener._flow_windows_by_mint = {}
    listener._last_market_cap_by_mint = {}

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "pumpfun_test.db")
        _init_token_analysis_db(db_path)
        conn = sqlite3.connect(db_path)
        conn.execute(
            """
            INSERT INTO token_analysis (
                mint, source_platform, lifecycle_stage,
                is_about_to_migrate, migration_band, migration_progress_pct,
                migration_signal_source, migration_signal_updated_at
            ) VALUES (?, 'pumpfun', 'bonding_curve', 1, 'hot', 87.5, 'flow', 111)
            """,
            (mint,),
        )
        conn.commit()
        conn.close()

        original_db_path = listener_mod.DB_PATH
        try:
            listener_mod.DB_PATH = db_path
            asyncio.run(listener._persist_pre_migration_signal(mint, 0.0, int(time.time())))
        finally:
            listener_mod.DB_PATH = original_db_path

        conn = sqlite3.connect(db_path)
        row = conn.execute(
            """
            SELECT is_about_to_migrate, migration_band, migration_progress_pct, migration_signal_source
            FROM token_analysis
            WHERE mint = ?
            """,
            (mint,),
        ).fetchone()
        conn.close()

    assert row == (1, "hot", 87.5, "flow")


def test_refresh_bonding_curve_index_preserves_cached_index_on_sqlite_error():
    listener = _build_listener()
    listener._refresh_bonding_curve_index = PumpFunCurveListener._refresh_bonding_curve_index.__get__(listener, PumpFunCurveListener)
    existing_bonding_curve = "BCrvX2d9p7Kn2pc4m7mHkq9qTi4Z3YhVZ8J6iYbZV8n"
    existing_mint = "7vfCXTUXx5rP1xjFNF4f3x8gKdpA2m5yFZQ6R3mwpump"
    listener._bonding_curve_to_mint[existing_bonding_curve] = existing_mint
    listener._known_bonding_curve_mints.add(existing_mint)
    listener._bonding_curve_index_last_rowid = 42

    original_connect = listener_mod.sqlite3.connect

    def _boom(*args, **kwargs):
        raise sqlite3.OperationalError("disk I/O error")

    captured = []
    original_log_print = listener_mod.log_print
    try:
        listener_mod.sqlite3.connect = _boom
        listener_mod.log_print = lambda message, flush=True: captured.append(message)
        refreshed = listener._refresh_bonding_curve_index(force=True)
    finally:
        listener_mod.sqlite3.connect = original_connect
        listener_mod.log_print = original_log_print

    assert refreshed == 0
    assert listener._bonding_curve_to_mint[existing_bonding_curve] == existing_mint
    assert listener._bonding_curve_index_last_rowid == 42
    assert any("[INDEX_REFRESH_RETRY]" in line for line in captured)
    assert any("[INDEX_REFRESH_FAIL]" in line for line in captured)


def test_indirect_inference_uses_ata_context_mint():
    listener = _build_listener()
    mint = "7vfCXTUXx5rP1xjFNF4f3x8gKdpA2m5yFZQ6R3mwpump"
    tx_data = {
        "transaction": {
            "message": {
                "accountKeys": [
                    {"pubkey": "Buyer1111111111111111111111111111111111111"},
                    {"pubkey": mint},
                ],
                "instructions": [
                    {
                        "programId": "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL",
                        "parsed": {
                            "type": "createIdempotent",
                            "info": {
                                "mint": mint,
                                "account": "ATA111111111111111111111111111111111111111",
                                "owner": "Buyer1111111111111111111111111111111111111",
                            },
                        },
                    }
                ],
            }
        },
        "meta": {"preTokenBalances": [], "postTokenBalances": []},
    }

    async def _run():
        async def _context(signature):
            return tx_data

        listener._get_trade_transaction_context = _context
        resolved, source, candidates, _, _ = await listener._resolve_bonding_curve_mint_for_trade(
            "sig1234567890",
            ["Program log: Instruction: Buy"],
        )
        return resolved, source, candidates

    resolved, source, candidates = asyncio.run(_run())
    assert resolved == mint
    assert source == "ata_context"
    assert mint in candidates


def test_indirect_inference_uses_token_account_balance_context():
    listener = _build_listener()
    mint = "7vfCXTUXx5rP1xjFNF4f3x8gKdpA2m5yFZQ6R3mwpump"
    token_account = "ToKenAccoUnt1111111111111111111111111111111"
    tx_data = {
        "transaction": {
            "message": {
                "accountKeys": [
                    {"pubkey": token_account},
                    {"pubkey": "Buyer1111111111111111111111111111111111111"},
                ],
                "instructions": [
                    {
                        "programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
                        "parsed": {
                            "type": "transferChecked",
                            "info": {
                                "source": token_account,
                                "destination": "Dest11111111111111111111111111111111111111",
                            },
                        },
                    }
                ],
            }
        },
        "meta": {
            "preTokenBalances": [{"accountIndex": 0, "mint": mint}],
            "postTokenBalances": [{"accountIndex": 0, "mint": mint}],
        },
    }

    async def _run():
        async def _context(signature):
            return tx_data

        listener._get_trade_transaction_context = _context
        resolved, source, candidates, _, _ = await listener._resolve_bonding_curve_mint_for_trade(
            "sig1234567890",
            ["Program log: Instruction: Buy"],
        )
        return resolved, source, candidates

    resolved, source, candidates = asyncio.run(_run())
    assert resolved == mint
    assert source == "token_account_context"
    assert mint in candidates


def test_partial_trade_enrichment_recovers_buyer_and_sol_from_tx_context():
    listener = _build_listener()
    mint = "7vfCXTUXx5rP1xjFNF4f3x8gKdpA2m5yFZQ6R3mwpump"
    buyer = "Buyer1111111111111111111111111111111111111"
    token_account = "ToKenAccoUnt1111111111111111111111111111111"
    tx_data = {
        "transaction": {
            "message": {
                "accountKeys": [
                    {"pubkey": buyer, "signer": True},
                    {"pubkey": token_account, "signer": False},
                    {"pubkey": mint, "signer": False},
                ],
                "instructions": [
                    {
                        "programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
                        "parsed": {
                            "type": "transferChecked",
                            "info": {
                                "owner": buyer,
                                "source": token_account,
                            },
                        },
                    }
                ],
            }
        },
        "meta": {
            "fee": 5000,
            "preBalances": [2_000_000_000, 0, 0],
            "postBalances": [500_000_000, 0, 0],
            "preTokenBalances": [{"accountIndex": 1, "mint": mint, "owner": buyer}],
            "postTokenBalances": [{"accountIndex": 1, "mint": mint, "owner": buyer}],
        },
    }

    enriched_buyer, enriched_sol, mode, reason = listener._recover_partial_trade_details_from_tx(
        tx_data,
        mint=mint,
        buyer=None,
        sol_amount=None,
    )

    assert enriched_buyer == buyer
    assert enriched_sol is not None
    assert enriched_sol > 1.49
    assert mode == "full"
    assert reason is None


def test_partial_trade_enrichment_recovers_buyer_only_when_sol_not_confident():
    listener = _build_listener()
    mint = "7vfCXTUXx5rP1xjFNF4f3x8gKdpA2m5yFZQ6R3mwpump"
    buyer = "Buyer1111111111111111111111111111111111111"
    tx_data = {
        "transaction": {
            "message": {
                "accountKeys": [
                    {"pubkey": buyer, "signer": True},
                    {"pubkey": mint, "signer": False},
                ],
                "instructions": [
                    {
                        "programId": "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL",
                        "parsed": {
                            "type": "createIdempotent",
                            "info": {
                                "mint": mint,
                                "owner": buyer,
                            },
                        },
                    }
                ],
            }
        },
        "meta": {
            "preBalances": [1_000_000, 0],
            "postBalances": [1_000_000, 0],
            "preTokenBalances": [],
            "postTokenBalances": [],
        },
    }

    enriched_buyer, enriched_sol, mode, reason = listener._recover_partial_trade_details_from_tx(
        tx_data,
        mint=mint,
        buyer=None,
        sol_amount=None,
    )

    assert enriched_buyer == buyer
    assert enriched_sol is None
    assert mode == "buyer_only"
    assert reason is None


def test_evaluate_migration_prediction_snapshot_requires_fresh_signal():
    listener = _build_listener()
    migrated_at = int(time.time())

    verdict = listener._evaluate_migration_prediction_snapshot(
        {
            "migration_signal_updated_at": migrated_at - 30,
            "migration_signal_source": "flow",
            "is_about_to_migrate": 1,
            "migration_band": "hot",
            "market_cap_current": 75000,
            "signal_score": 4,
            "buys_10s": 8,
            "unique_30s": 6,
            "sol_15s": 4.2,
        },
        migrated_at=migrated_at,
    )

    assert verdict["predicted_by_flow"] == 1
    assert verdict["predicted_by_market_cap"] == 1
    assert verdict["predicted_by_explicit_signal"] == 1
    assert verdict["final_verdict"] == "predicted"

    stale_verdict = listener._evaluate_migration_prediction_snapshot(
        {
            "migration_signal_updated_at": migrated_at - 2000,
            "migration_signal_source": "flow",
            "is_about_to_migrate": 1,
            "migration_band": "hot",
            "market_cap_current": 75000,
            "signal_score": 4,
            "buys_10s": 8,
            "unique_30s": 6,
            "sol_15s": 4.2,
        },
        migrated_at=migrated_at,
    )

    assert stale_verdict["predicted_by_flow"] == 0
    assert stale_verdict["final_verdict"] == "stale_signal"


def test_record_migration_verification_snapshot_persists_pre_migration_state():
    listener = _build_listener()
    mint = "7vfCXTUXx5rP1xjFNF4f3x8gKdpA2m5yFZQ6R3mwpump"
    now = int(time.time())
    listener._flow_windows_by_mint = {
        mint: listener_mod.deque(
            [
                {"ts": now - 5, "buyer": "Buyer1111111111111111111111111111111111111", "sol_amount": 1.2, "kind": "buy"},
                {"ts": now - 8, "buyer": "Buyer2222222222222222222222222222222222222", "sol_amount": 1.4, "kind": "buy"},
            ]
        )
    }
    listener._last_market_cap_by_mint = {mint: 82000.0}

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "pumpfun_test.db")
        _init_token_analysis_db(db_path)
        conn = sqlite3.connect(db_path)
        conn.execute(
            """
            INSERT INTO token_analysis (
                mint, source_platform, lifecycle_stage, migration_tx,
                market_cap_current, is_about_to_migrate, migration_band,
                migration_progress_pct, migration_signal_source, migration_signal_updated_at
            ) VALUES (?, 'pumpfun', 'bonding_curve', ?, ?, 1, 'hot', 87.5, 'flow', ?)
            """,
            (mint, "sig123", 82000.0, now - 20),
        )
        conn.commit()
        conn.close()

        original_db_path = listener_mod.DB_PATH
        try:
            listener_mod.DB_PATH = db_path
            asyncio.run(
                listener._record_migration_verification_snapshot(
                    mint,
                    migrated_at=now,
                    migration_tx="sig123",
                    dex="pumpswap",
                    pumpswap_pool_address="Pool11111111111111111111111111111111111111",
                )
            )
        finally:
            listener_mod.DB_PATH = original_db_path

        conn = sqlite3.connect(db_path)
        row = conn.execute(
            """
            SELECT predicted_by_flow, predicted_by_market_cap, predicted_by_explicit_signal,
                   final_verdict, pre_buys_10s, pre_unique_30s, pre_sol_15s
            FROM pumpfun_migration_verification
            WHERE mint = ?
            """,
            (mint,),
        ).fetchone()
        conn.close()

    assert row is not None
    assert row[0] == 1
    assert row[1] == 1
    assert row[2] == 1
    assert row[3] == "predicted"
    assert row[4] >= 2
    assert row[5] >= 2
    assert row[6] > 0
