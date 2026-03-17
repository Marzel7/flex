#!/usr/bin/env python3
"""
Manually trigger vault discovery for top 20 tokens.

After cleanup, these tokens need fresh vault discovery to find their real on-chain pools.
"""

import asyncio
import sqlite3
import os

DB_PATH = "database/flex_complete_database.db"
RPC_URL = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")


async def discover_pools_for_token(mint: str):
    """Run vault discovery for a specific token."""
    try:
        from solana.rpc.async_client import AsyncClient
        from src.core.vault_discovery import discover_and_register_all_pools
        from src.core.price_worker import get_price_worker

        # Create RPC client
        rpc_client = AsyncClient(RPC_URL)

        # Get price worker for WebSocket refresh
        price_worker = get_price_worker(DB_PATH)

        # Run discovery
        success = await discover_and_register_all_pools(
            token_mint=mint,
            rpc_client=rpc_client,
            db=DB_PATH,
            price_worker=price_worker,
            max_retries=2
        )

        if success:
            print("✅")
        else:
            print("❌ no pools")

        await rpc_client.close()
        return success

    except Exception as e:
        print(f"❌ {str(e)[:30]}")
        return False


async def main():
    """Discover pools for top 20 tokens."""

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    print("=" * 80)
    print("REDISCOVER TOP 20 POOLS")
    print("=" * 80)

    # Get top 20 tokens
    cursor.execute("""
        SELECT mint FROM token_analysis
        ORDER BY created_at DESC
        LIMIT 20
    """)

    tokens = [row[0] for row in cursor.fetchall()]
    conn.close()

    print(f"\nDiscovering pools for {len(tokens)} tokens...\n")

    discovered = 0
    for i, mint in enumerate(tokens, 1):
        print(f"[{i}/20] {mint[:14]}...", end=" ", flush=True)
        success = await discover_pools_for_token(mint)
        if success:
            discovered += 1
        # Small delay between requests
        await asyncio.sleep(2)

    print("\n" + "=" * 80)
    print(f"RESULTS: {discovered}/20 tokens have pools")
    print("=" * 80)
    print("\nNext steps:")
    print("1. Wait 10s for WebSocket to subscribe to new pools")
    print("2. Run: python3 test_top20_update_times.py")
    print("3. Verify prices are updating from pool sources")


if __name__ == "__main__":
    asyncio.run(main())
