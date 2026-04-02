#!/usr/bin/env python3
"""
Recover corrupted vault records using migration transaction signatures.

For each corrupted token, uses the migration_tx signature from token_analysis
to re-discover the correct pool address, then extracts vaults and verifies
against DexScreener.
"""

import asyncio
import sqlite3
import logging
import os
import sys
import aiohttp

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.pool_discovery import PoolDiscovery
from src.core.post_migration_pool_discovery import PostMigrationPoolDiscovery

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

CORRUPTED_POOL_MARKER = "ADyA8hdefvWN2dbGGWFotbzWxrAvLW83WG6QCVXvJKqw"

async def get_dexscreener_pool(token_mint: str) -> dict:
    """Fetch pool data from DexScreener API."""
    try:
        url = f"https://api.dexscreener.com/latest/dex/tokens/{token_mint}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    pairs = data.get("pairs", [])
                    return pairs[0] if pairs else None
        return None
    except Exception as e:
        logger.debug(f"DexScreener fetch failed: {e}")
        return None

async def main():
    db_path = "database/flex_complete_database.db"
    rpc_url = os.environ.get("HELIUS_RPC_URL") or os.environ.get("RPC_URL") or "https://api.mainnet-beta.solana.com"

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Get corrupted records with migration_tx
    corrupted = conn.execute("""
        SELECT
            t.mint,
            t.base_account,
            t.quote_account,
            t.pool_address,
            a.migration_tx,
            a.pool_address as dex_pool
        FROM token_pool_accounts t
        LEFT JOIN token_analysis a ON t.mint = a.mint
        WHERE t.pool_address = ? OR t.base_account = ?
        ORDER BY t.created_at DESC
    """, (CORRUPTED_POOL_MARKER, CORRUPTED_POOL_MARKER)).fetchall()

    logger.info(f"\n{'='*80}")
    logger.info(f"RECOVERY: {len(corrupted)} corrupted records with migration_tx")
    logger.info(f"{'='*80}\n")

    pool_discovery = PoolDiscovery(db_path, rpc_url)
    migration_discovery = PostMigrationPoolDiscovery(rpc_url)

    matches = 0
    mismatches = 0
    recovered = 0
    failed = 0

    for i, row in enumerate(corrupted, 1):
        mint = row["mint"]
        migration_tx = row["migration_tx"]
        old_pool = row["pool_address"]

        logger.info(f"[{i}/{len(corrupted)}] {mint[:16]}...")
        logger.info(f"  migration_tx: {migration_tx[:20] if migration_tx else 'NULL'}...")

        if not migration_tx:
            logger.warning(f"  ⚠️  No migration_tx, skipping")
            failed += 1
            continue

        try:
            # Use PostMigrationPoolDiscovery to find pool from migration_tx
            logger.info(f"  Discovering pool from migration transaction...")
            vault_pair = await migration_discovery.discover_pumpfun_v1_vault_pair(
                mint=mint,
                pool_address=None  # Will discover from migration_tx
            )

            if not vault_pair:
                logger.warning(f"  ❌ Could not discover vault pair from migration_tx")
                failed += 1
                continue

            logger.info(f"  Discovered vault pair: {vault_pair[:16]}...")

            # Extract vaults using the discovered pool
            new_reserves = await pool_discovery.extract_pool_reserves(vault_pair, mint)

            if not new_reserves:
                logger.warning(f"  ❌ Extraction from discovered pool failed")
                failed += 1
                continue

            new_base = new_reserves.get("base_account")
            new_quote = new_reserves.get("quote_account")
            new_pool = vault_pair

            logger.info(f"  Extracted:")
            logger.info(f"    pool:  {new_pool[:16]}...")
            logger.info(f"    base:  {new_base[:16]}...")
            logger.info(f"    quote: {new_quote[:16]}...")

            # Verify against DexScreener
            dex_pair = await get_dexscreener_pool(mint)

            if not dex_pair:
                logger.warning(f"  ⚠️  DexScreener has no data")
                failed += 1
                continue

            dex_base = dex_pair.get("baseToken", {}).get("address")
            dex_quote = dex_pair.get("quoteToken", {}).get("address")

            logger.info(f"  DexScreener:")
            logger.info(f"    base:  {dex_base[:16] if dex_base else 'N/A'}...")
            logger.info(f"    quote: {dex_quote[:16] if dex_quote else 'N/A'}...")

            base_match = new_base == dex_base
            quote_match = new_quote == dex_quote

            if base_match and quote_match:
                logger.info(f"  ✅ MATCH - both vaults correct, pool: {new_pool[:16]}...")
                matches += 1
                recovered += 1

                # Optionally: update DB here
                # UPDATE token_pool_accounts SET pool_address=?, base_account=?, quote_account=? WHERE mint=?
            elif base_match or quote_match:
                logger.warning(f"  ⚠️  PARTIAL - base:{base_match} quote:{quote_match}")
                mismatches += 1
            else:
                logger.error(f"  ❌ MISMATCH - neither matches")
                mismatches += 1

        except Exception as e:
            logger.error(f"  ❌ Error: {e}")
            failed += 1

        logger.info()

    conn.close()

    logger.info(f"{'='*80}")
    logger.info(f"RECOVERY RESULTS")
    logger.info(f"{'='*80}")
    logger.info(f"  ✅ Matches (both correct):    {matches}")
    logger.info(f"  ⚠️  Mismatches:               {mismatches}")
    logger.info(f"  ❌ Failed:                    {failed}")
    logger.info(f"  ────────────────────────────")
    logger.info(f"  Recovered:                    {recovered}/{len(corrupted)}")
    logger.info(f"{'='*80}\n")

    if matches > 0:
        logger.info(f"✅ Can recover {matches} records with correct pools from migration_tx")

if __name__ == "__main__":
    asyncio.run(main())
