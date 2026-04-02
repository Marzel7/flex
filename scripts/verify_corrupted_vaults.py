#!/usr/bin/env python3
"""
Verify corrupted vault records against DexScreener without writing to DB.

Identifies records with corrupted pool_address (ADyA shared PDA), re-extracts
vaults using the new struct-based extraction, and compares against DexScreener.
"""

import asyncio
import sqlite3
import logging
import os
import sys
import aiohttp

# Add repo root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.pool_discovery import PoolDiscovery

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# The shared PDA that corrupted 25 records
CORRUPTED_POOL_MARKER = "ADyA8hdefvWN2dbGGWFotbzWxrAvLW83WG6QCVXvJKqw"

async def get_dexscreener_pool(token_mint: str, pool_address: str = None) -> dict:
    """Fetch pool data from DexScreener API."""
    try:
        url = f"https://api.dexscreener.com/latest/dex/tokens/{token_mint}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    pairs = data.get("pairs", [])

                    # If pool_address specified, find matching pair
                    if pool_address:
                        for pair in pairs:
                            if pair.get("pairAddress") == pool_address:
                                return pair

                    # Otherwise return first pair (primary)
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

    # Find corrupted records
    corrupted = conn.execute("""
        SELECT mint, base_account, quote_account, pool_address, discovery_method
        FROM token_pool_accounts
        WHERE pool_address = ? OR base_account = ?
        ORDER BY created_at DESC
    """, (CORRUPTED_POOL_MARKER, CORRUPTED_POOL_MARKER)).fetchall()

    logger.info(f"\n{'='*80}")
    logger.info(f"VERIFICATION: {len(corrupted)} potentially corrupted records")
    logger.info(f"{'='*80}\n")

    if not corrupted:
        logger.info("No corrupted records found.")
        conn.close()
        return

    pool_discovery = PoolDiscovery(db_path, rpc_url)

    matches = 0
    mismatches = 0
    dexscreener_no_data = 0
    extraction_failed = 0

    for i, row in enumerate(corrupted, 1):
        mint = row["mint"]
        old_base = row["base_account"]
        old_quote = row["quote_account"]
        old_pool = row["pool_address"]

        logger.info(f"[{i}/{len(corrupted)}] {mint[:16]}...")
        logger.info(f"  Current DB state:")
        logger.info(f"    pool:  {old_pool[:16] if old_pool else 'NULL'}...")
        logger.info(f"    base:  {old_base[:16]}...")
        logger.info(f"    quote: {old_quote[:16]}...")

        # Find correct pool address
        telemetry = conn.execute("""
            SELECT pool_address FROM token_resolution_telemetry WHERE mint = ?
        """, (mint,)).fetchone()

        if not telemetry or not telemetry["pool_address"]:
            logger.warning(f"  ⚠️  No pool_address in telemetry")
            extraction_failed += 1
            continue

        correct_pool = telemetry["pool_address"]

        # Extract using struct method
        try:
            new_reserves = await pool_discovery.extract_pool_reserves(correct_pool, mint)

            if not new_reserves:
                logger.warning(f"  ❌ Struct extraction failed")
                extraction_failed += 1
                continue

            new_base = new_reserves.get("base_account")
            new_quote = new_reserves.get("quote_account")

            logger.info(f"  Extracted vaults:")
            logger.info(f"    pool:  {correct_pool[:16]}...")
            logger.info(f"    base:  {new_base[:16]}...")
            logger.info(f"    quote: {new_quote[:16]}...")

            # Compare with DexScreener
            dex_pair = await get_dexscreener_pool(mint, correct_pool)

            if not dex_pair:
                logger.warning(f"  ⚠️  DexScreener has no data for pool {correct_pool[:16]}")
                dexscreener_no_data += 1
                continue

            dex_base = dex_pair.get("baseToken", {}).get("address")
            dex_quote = dex_pair.get("quoteToken", {}).get("address")

            logger.info(f"  DexScreener data:")
            logger.info(f"    base:  {dex_base[:16] if dex_base else 'N/A'}...")
            logger.info(f"    quote: {dex_quote[:16] if dex_quote else 'N/A'}...")

            # Check matches
            base_match = new_base == dex_base
            quote_match = new_quote == dex_quote

            if base_match and quote_match:
                logger.info(f"  ✅ MATCH - both base and quote correct")
                matches += 1
            elif base_match or quote_match:
                logger.warning(f"  ⚠️  PARTIAL MATCH - base:{base_match} quote:{quote_match}")
                mismatches += 1
            else:
                logger.error(f"  ❌ MISMATCH - neither vault matches")
                mismatches += 1

        except Exception as e:
            logger.error(f"  ❌ Error: {e}")
            extraction_failed += 1

        logger.info()

    conn.close()

    # Summary
    logger.info(f"{'='*80}")
    logger.info(f"VERIFICATION RESULTS")
    logger.info(f"{'='*80}")
    logger.info(f"  ✅ Matches (both vaults correct):     {matches}")
    logger.info(f"  ⚠️  Mismatches:                       {mismatches}")
    logger.info(f"  ⚠️  DexScreener no data:              {dexscreener_no_data}")
    logger.info(f"  ❌ Extraction failed:                 {extraction_failed}")
    logger.info(f"  ────────────────────────────────────")
    logger.info(f"  Total verified:                       {matches + mismatches + dexscreener_no_data}")
    logger.info(f"  Total tested:                         {len(corrupted)}")
    logger.info(f"{'='*80}\n")

    if matches > 0:
        accuracy = (matches / (matches + mismatches)) * 100 if (matches + mismatches) > 0 else 0
        logger.info(f"✅ Accuracy (matches / decided): {accuracy:.1f}%")

    logger.info(f"\n⚠️  NOTE: This is a READ-ONLY verification. No database changes made.")

if __name__ == "__main__":
    asyncio.run(main())
