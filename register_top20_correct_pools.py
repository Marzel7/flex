#!/usr/bin/env python3
"""
Register top 20 UI tokens with their correct trading pools.

Strategy: For tokens that migrated from PumpFun to PumpSwap, their trading
addresses on DexScreener are authoritative. We register these as the primary
pools for WebSocket subscription.
"""

import sqlite3
import requests
import time
from typing import Optional, Dict

DB_PATH = "database/flex_complete_database.db"
DEXSCREENER_API = "https://api.dexscreener.com/latest/dex"
WRAPPED_SOL_MINT = "So11111111111111111111111111111111111111112"

def get_dexscreener_pair(mint: str) -> Optional[Dict]:
    """Fetch primary trading pair from DexScreener for a token."""
    try:
        url = f"{DEXSCREENER_API}/tokens/{mint}"
        resp = requests.get(url, timeout=5)

        if resp.status_code != 200:
            return None

        data = resp.json()
        pairs = data.get("pairs", [])

        if not pairs:
            return None

        # Find the primary pair (PumpSwap or highest liquidity)
        best_pair = None
        for pair in pairs:
            if pair.get("chainId") != "solana":
                continue

            if "pumpswap" in pair.get("dex", "").lower():
                if not best_pair or pair.get("liquidity", {}).get("usd", 0) > best_pair.get("liquidity", {}).get("usd", 0):
                    best_pair = pair
            elif not best_pair and pair.get("liquidity", {}).get("usd", 0) > 1000:
                best_pair = pair

        return best_pair if best_pair else (pairs[0] if pairs else None)

    except Exception as e:
        print(f"  ❌ Error fetching DexScreener: {e}")
        return None


def main():
    """Main function: register top 20 with correct pool addresses."""

    print("=" * 80)
    print("Registering Top 20 Tokens with Correct Trading Pair Addresses")
    print("=" * 80)

    # Get top 20 tokens
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT mint FROM token_analysis
        ORDER BY created_at DESC
        LIMIT 20
    """)

    top_20 = [row[0] for row in cursor.fetchall()]
    conn.close()

    if not top_20:
        print("❌ No tokens found in token_analysis")
        return

    print(f"\n🔍 Found {len(top_20)} tokens\n")

    successful = 0
    failed = 0

    for i, mint in enumerate(top_20, 1):
        print(f"[{i:2d}/{len(top_20)}] {mint[:16]}...", end=" ", flush=True)

        # Get pair from DexScreener
        pair = get_dexscreener_pair(mint)
        if not pair:
            print("❌ No pair found")
            failed += 1
            continue

        pool_address = pair.get("pairAddress")
        if not pool_address:
            print("❌ No pool address")
            failed += 1
            continue

        quote_mint = pair.get("quoteToken", {}).get("address", WRAPPED_SOL_MINT)
        dex = pair.get("dex", "unknown").lower()

        # Update database
        try:
            conn = sqlite3.connect(DB_PATH, timeout=10)
            cursor = conn.cursor()
            now = int(time.time())

            # Check if this pool address already exists
            cursor.execute("""
                SELECT COUNT(*) FROM token_pool_accounts
                WHERE mint = ? AND base_account = ?
            """, (mint, pool_address))

            exists = cursor.fetchone()[0] > 0

            if exists:
                # Update: set as primary
                cursor.execute("""
                    UPDATE token_pool_accounts
                    SET is_primary = 1,
                        pool_score = 100.0,
                        quote_account = ?,
                        updated_at = ?
                    WHERE mint = ? AND base_account = ?
                """, (quote_mint, now, mint, pool_address))
                print(f"✅ Updated (score: 100)")
            else:
                # Insert new pool
                cursor.execute("""
                    INSERT INTO token_pool_accounts
                    (mint, base_account, quote_account, pool_program, base_token,
                     base_decimals, quote_decimals, quote_token, vault_validation_status,
                     discovery_method, is_primary, pool_score, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    mint,
                    pool_address,
                    quote_mint,
                    "pumpswap" if "pumpswap" in dex else "unknown",
                    mint,
                    6,
                    9 if quote_mint == WRAPPED_SOL_MINT else 6,
                    quote_mint,
                    "pending",  # Will be validated later by RPC
                    "dexscreener_authoritative",
                    1,  # Mark as primary
                    100.0,
                    now,
                    now
                ))
                print(f"✅ Registered")

            conn.commit()
            conn.close()
            successful += 1

        except Exception as e:
            print(f"❌ DB error: {e}")
            failed += 1

        # Rate limit
        time.sleep(0.2)

    print("\n" + "=" * 80)
    print(f"SUMMARY: {successful} registered/updated, {failed} failed")
    print("=" * 80)

    if successful > 0:
        print("\n📋 Next steps:")
        print("1. Restart price_worker to reload pool list")
        print("2. WebSocket will subscribe to new pools")
        print("3. Prices should start flowing within 30 seconds")


if __name__ == "__main__":
    main()
