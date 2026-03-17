#!/usr/bin/env python3
"""
Update top 20 UI tokens with correct trading pair addresses from DexScreener.

This script:
1. Gets the top 20 tokens from token_analysis (ordered by creation_at DESC)
2. For each token, queries DexScreener to find the primary trading pair
3. Extracts the pool address (base_account) from the pair
4. Updates token_pool_accounts with the correct pool address
5. Marks wSOL pools as primary for WebSocket subscription

This fixes the issue where pools were registered with wrong account addresses.
"""

import sqlite3
import asyncio
import aiohttp
import sys
import time
from typing import Optional, Dict, List

DB_PATH = "database/flex_complete_database.db"
DEXSCREENER_API = "https://api.dexscreener.com/latest/dex"
WRAPPED_SOL_MINT = "So11111111111111111111111111111111111111112"

async def get_dexscreener_pair(mint: str, session: aiohttp.ClientSession) -> Optional[Dict]:
    """Fetch primary trading pair from DexScreener for a token."""
    try:
        url = f"{DEXSCREENER_API}/tokens/{mint}"
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
            if resp.status != 200:
                print(f"  ❌ DexScreener API error: {resp.status}")
                return None

            data = await resp.json()
            pairs = data.get("pairs", [])

            if not pairs:
                print(f"  ❌ No pairs found on DexScreener")
                return None

            # Find the primary pair (highest liquidity or first PumpSwap pair)
            best_pair = None
            for pair in pairs:
                if pair.get("chainId") != "solana":
                    continue

                # Prefer PumpSwap pools over others
                if "pumpswap" in pair.get("dex", "").lower():
                    if not best_pair or pair.get("liquidity", {}).get("usd", 0) > best_pair.get("liquidity", {}).get("usd", 0):
                        best_pair = pair
                elif not best_pair and pair.get("liquidity", {}).get("usd", 0) > 1000:
                    best_pair = pair

            if not best_pair:
                best_pair = pairs[0] if pairs else None

            return best_pair

    except Exception as e:
        print(f"  ❌ Error fetching DexScreener data: {e}")
        return None


async def update_pool_from_pair(mint: str, pair: Dict, db_path: str) -> bool:
    """Update database with pool address from DexScreener pair."""
    try:
        pool_address = pair.get("pairAddress")
        if not pool_address:
            print(f"  ❌ No pair address in DexScreener response")
            return False

        # Extract quote mint (usually wSOL for PumpSwap)
        quote_mint = pair.get("quoteToken", {}).get("address", WRAPPED_SOL_MINT)

        print(f"  📍 DexScreener pair: {pool_address[:16]}... / {quote_mint[:16]}...")

        # Connect to database
        conn = sqlite3.connect(db_path, timeout=10)
        cursor = conn.cursor()
        now = int(time.time())

        # Check if pool already exists
        cursor.execute("""
            SELECT COUNT(*) FROM token_pool_accounts
            WHERE mint = ? AND base_account = ?
        """, (mint, pool_address))

        exists = cursor.fetchone()[0] > 0

        if exists:
            # Update existing pool
            cursor.execute("""
                UPDATE token_pool_accounts
                SET quote_account = ?,
                    is_primary = 1,
                    pool_score = 100.0,
                    updated_at = ?
                WHERE mint = ? AND base_account = ?
            """, (quote_mint, now, mint, pool_address))
            print(f"  ✅ Updated existing pool registration")
        else:
            # Insert new pool
            cursor.execute("""
                INSERT INTO token_pool_accounts
                (mint, base_account, quote_account, pool_program, base_token, base_decimals,
                 quote_decimals, quote_token, vault_validation_status, discovery_method,
                 is_primary, pool_score, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                mint,
                pool_address,
                quote_mint,
                "pumpswap" if "pumpswap" in pair.get("dex", "").lower() else "unknown",
                mint,
                6,  # Default token decimals
                9 if quote_mint == WRAPPED_SOL_MINT else 6,
                quote_mint,
                "unvalidated",  # DexScreener source is not RPC-validated
                "dexscreener_pair_address",
                1,  # Mark as primary
                100.0,  # Score for DexScreener pair
                now,
                now
            ))
            print(f"  ✅ Registered new pool from DexScreener")

        conn.commit()
        conn.close()
        return True

    except Exception as e:
        print(f"  ❌ Database update failed: {e}")
        return False


async def main():
    """Main function: update all top 20 UI tokens with DexScreener pair addresses."""

    print("=" * 80)
    print("Updating Top 20 Tokens with DexScreener Trading Pair Addresses")
    print("=" * 80)

    # Get top 20 tokens
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT mint FROM token_analysis
        ORDER BY created_at DESC
        LIMIT 20
    """)

    top_20 = [(row[0], row[0][:8]) for row in cursor.fetchall()]
    conn.close()

    if not top_20:
        print("❌ No tokens found in token_analysis")
        return

    print(f"\n🔍 Found {len(top_20)} tokens to update\n")

    # Create session for API calls
    async with aiohttp.ClientSession() as session:
        successful = 0
        failed = 0

        for i, (mint, name) in enumerate(top_20, 1):
            print(f"\n[{i}/{len(top_20)}] {mint[:16]}... ({name})")

            # Get pair from DexScreener
            pair = await get_dexscreener_pair(mint, session)
            if not pair:
                print(f"  ⏭️  Skipping (no pair found)")
                failed += 1
                continue

            # Update database
            success = await update_pool_from_pair(mint, pair, DB_PATH)
            if success:
                successful += 1
            else:
                failed += 1

            # Rate limit: DexScreener allows 300 req/min (~200ms between requests)
            await asyncio.sleep(0.3)

    print("\n" + "=" * 80)
    print(f"SUMMARY: {successful} updated, {failed} failed")
    print("=" * 80)

    if successful > 0:
        print("\n📋 Next steps:")
        print("1. Restart the price_worker to pick up new pools")
        print("2. WebSocket will subscribe to all pools")
        print("3. Prices should start flowing within 30 seconds")
        print("\nTo restart:")
        print("  kill $(pgrep -f 'src.core.main') || true")
        print("  python3 src/core/main.py &")


if __name__ == "__main__":
    asyncio.run(main())
