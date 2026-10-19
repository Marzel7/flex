"""
Integration Test Helpers — Decode, Price, and Worker Status Export

Provides utilities for end-to-end pool validation:
1. Pool decoder: fetch and decode on-chain pool account
2. Expected price calculator: compute price from reserves using production logic
3. Worker status exporter: export live WebSocket subscriptions and pool state
"""

import asyncio
import logging
import sqlite3
import threading
import time
from typing import Dict, Optional, Tuple, Any
from dataclasses import dataclass, asdict

import aiohttp
from solders.pubkey import Pubkey

logger = logging.getLogger(__name__)

# Known program IDs
RAYDIUM_PROGRAM_ID = "675kPX9MHTjS2zt1qrVrrVrZg1ankqqgoerEmJlwQ1K"
PUMPSWAP_PROGRAM_ID = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"


@dataclass
class PoolAccount:
    """Decoded on-chain pool account"""
    pool_address: str
    base_vault: str
    quote_vault: str
    base_decimals: int
    quote_decimals: int
    quote_mint: str
    pool_program: str


def decode_pool_account(
    pool_address: str,
    pool_program: str,
    rpc_url: str,
) -> Optional[PoolAccount]:
    """
    Fetch and decode an on-chain pool account (Raydium AMM v4 / PumpSwap layout).

    Returns PoolAccount with decoded vault addresses and decimals, or None if fetch fails.
    """
    try:
        result = asyncio.run(
            _decode_pool_account_async(pool_address, pool_program, rpc_url)
        )
        return result
    except Exception as e:
        logger.error(f"Failed to decode pool {pool_address}: {e}")
        return None


async def _decode_pool_account_async(
    pool_address: str,
    pool_program: str,
    rpc_url: str,
) -> Optional[PoolAccount]:
    """Async helper for fetching and decoding pool account"""
    try:
        async with aiohttp.ClientSession() as session:
            # Fetch account data via RPC
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getAccountInfo",
                "params": [pool_address, {"encoding": "base64"}],
            }
            async with session.post(rpc_url, json=payload) as resp:
                data = await resp.json()

            if "result" not in data or data["result"]["value"] is None:
                logger.warning(f"Pool account {pool_address} not found")
                return None

            account_data = data["result"]["value"]["data"][0]
            # Decode from base64
            import base64
            decoded = base64.b64decode(account_data)

            # Raydium AMM v4 / PumpSwap pool layout:
            # Offset 232 (8 bytes): base_vault pubkey
            # Offset 264 (8 bytes): quote_vault pubkey
            # Offsets vary by program; for now use generic 32-byte reads

            if len(decoded) < 296:
                logger.warning(f"Pool account too small: {len(decoded)} bytes")
                return None

            # Vault pubkeys are 32 bytes each
            base_vault = str(Pubkey(decoded[232:264]))
            quote_vault = str(Pubkey(decoded[264:296]))

            # For now, assume standard decimals (base=6, quote=6/9)
            # In production, these would be fetched from the token mints
            return PoolAccount(
                pool_address=pool_address,
                base_vault=base_vault,
                quote_vault=quote_vault,
                base_decimals=6,
                quote_decimals=6,
                quote_mint="",  # Would need to extract from pool struct
                pool_program=pool_program,
            )

    except Exception as e:
        logger.error(f"Error decoding pool account {pool_address}: {e}")
        return None


def compute_expected_price_from_reserves(
    base_raw: int,
    quote_raw: int,
    base_decimals: int = 6,
    quote_decimals: int = 6,
    quote_mint: str = "",
) -> Optional[float]:
    """
    Compute expected token price from raw reserves.

    Price = (quote_reserve / 10^quote_decimals) / (base_reserve / 10^base_decimals)

    Args:
        base_raw: Raw base token reserve amount
        quote_raw: Raw quote token reserve amount
        base_decimals: Base token decimals (default 6)
        quote_decimals: Quote token decimals (default 6)
        quote_mint: Quote token mint (for context, not used in calculation)

    Returns:
        Price in quote token per base token, or None if invalid reserves
    """
    if not base_raw or not quote_raw or base_raw <= 0 or quote_raw <= 0:
        return None

    try:
        # Normalize reserves to human-readable scale
        base_normalized = base_raw / (10 ** base_decimals)
        quote_normalized = quote_raw / (10 ** quote_decimals)

        # Price: quote per base
        price = quote_normalized / base_normalized
        return price
    except Exception as e:
        logger.error(f"Error computing price from reserves: {e}")
        return None


@dataclass
class WorkerPoolState:
    """Snapshot of a single pool's state in the worker"""
    mint: str
    base_account: str
    base_reserve: Optional[int]
    quote_reserve: Optional[int]
    last_update: float
    last_slot_base: Optional[int]
    last_slot_quote: Optional[int]
    is_stale: bool


@dataclass
class WorkerStatus:
    """Complete worker status export"""
    ws_started: bool
    subscribed_accounts: list  # [vault addresses]
    pool_states: Dict[Tuple[str, str], WorkerPoolState]  # (mint, base_account) -> state
    all_mints: list
    last_export_time: float


def export_worker_status(price_worker: Any) -> WorkerStatus:
    """
    Export live worker status including WebSocket subscriptions and pool state.

    Args:
        price_worker: BackgroundPriceWorker instance

    Returns:
        WorkerStatus with all current state
    """
    try:
        ws_started = getattr(price_worker, "_ws_started", False)
        ws_client = getattr(price_worker, "_ws_client", None)
        pool_state_store = getattr(price_worker, "_pool_state", None)

        subscribed_accounts = []
        if ws_client and hasattr(ws_client, "_subscribed_accounts"):
            subscribed_accounts = list(ws_client._subscribed_accounts)

        pool_states = {}
        if pool_state_store and hasattr(pool_state_store, "_state"):
            with pool_state_store._lock:
                for (mint, base_account), state_dict in pool_state_store._state.items():
                    pool_states[(mint, base_account)] = WorkerPoolState(
                        mint=mint,
                        base_account=base_account,
                        base_reserve=state_dict.get("base_reserve"),
                        quote_reserve=state_dict.get("quote_reserve"),
                        last_update=state_dict.get("last_update", 0),
                        last_slot_base=state_dict.get("base_last_slot"),
                        last_slot_quote=state_dict.get("quote_last_slot"),
                        is_stale=state_dict.get("is_stale", False),
                    )

        all_mints = []
        if pool_state_store:
            all_mints = pool_state_store.get_all_mints()

        return WorkerStatus(
            ws_started=ws_started,
            subscribed_accounts=subscribed_accounts,
            pool_states=pool_states,
            all_mints=all_mints,
            last_export_time=time.time(),
        )
    except Exception as e:
        logger.error(f"Error exporting worker status: {e}")
        return WorkerStatus(
            ws_started=False,
            subscribed_accounts=[],
            pool_states={},
            all_mints=[],
            last_export_time=time.time(),
        )


def validate_pool_with_worker_state(
    mint: str,
    base_account: str,
    expected_pool_address: str,
    worker_status: WorkerStatus,
    db_path: str,
) -> Dict[str, Any]:
    """
    Validate that a pool is correctly integrated into the running worker.

    Checks:
    1. Pool state exists in worker store with reserves
    2. Both vaults are in WebSocket subscriptions
    3. Reserves are positive and reasonable
    4. Pool is not stale (updated within 5 minutes)

    Returns dict with validation results.
    """
    results = {
        "mint": mint,
        "base_account": base_account,
        "pool_address": expected_pool_address,
        "checks": {
            "pool_in_worker_store": False,
            "reserves_positive": False,
            "not_stale": False,
            "vaults_subscribed": False,
            "snapshot_exists": False,
        },
        "errors": [],
    }

    # Check 1: Pool in worker store
    key = (mint, base_account)
    if key in worker_status.pool_states:
        pool_st = worker_status.pool_states[key]
        results["checks"]["pool_in_worker_store"] = True
        results["pool_state"] = {
            "base_reserve": pool_st.base_reserve,
            "quote_reserve": pool_st.quote_reserve,
            "last_update": pool_st.last_update,
            "is_stale": pool_st.is_stale,
        }

        # Check 2: Reserves are positive
        if (
            pool_st.base_reserve
            and pool_st.quote_reserve
            and pool_st.base_reserve > 0
            and pool_st.quote_reserve > 0
        ):
            results["checks"]["reserves_positive"] = True

        # Check 3: Not stale
        if not pool_st.is_stale:
            results["checks"]["not_stale"] = True
    else:
        results["errors"].append(
            f"Pool ({mint}, {base_account}) not found in worker store"
        )

    # Check 4: Vaults subscribed (need to fetch from DB)
    try:
        conn = sqlite3.connect(db_path, timeout=15)
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT base_account, quote_account FROM token_pool_accounts
            WHERE mint = ? AND base_account = ?
            LIMIT 1
            """,
            (mint, base_account),
        )
        row = cursor.fetchone()
        conn.close()

        if row:
            base_vault, quote_vault = row
            subscribed = set(worker_status.subscribed_accounts)
            both_subscribed = base_vault in subscribed and quote_vault in subscribed
            results["checks"]["vaults_subscribed"] = both_subscribed
            if not both_subscribed:
                results["errors"].append(
                    f"Vaults not fully subscribed: base={base_vault in subscribed}, "
                    f"quote={quote_vault in subscribed}"
                )
        else:
            results["errors"].append(
                f"Pool ({mint}, {base_account}) not found in database"
            )
    except Exception as e:
        results["errors"].append(f"Error checking DB for vaults: {e}")

    # Check 5: Snapshot exists
    try:
        conn = sqlite3.connect(db_path, timeout=15)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT 1 FROM token_analysis WHERE mint = ? AND first_observed_mc IS NOT NULL LIMIT 1",
            (mint,),
        )
        results["checks"]["compact_valuation_exists"] = cursor.fetchone() is not None
        conn.close()
    except Exception as e:
        results["errors"].append(f"Error checking snapshots: {e}")

    return results
