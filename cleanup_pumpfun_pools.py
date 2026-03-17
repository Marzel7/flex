#!/usr/bin/env python3
"""
Cleanup: Remove Pump.Fun bonding curve accounts registered as pools.

These are legacy entries from before proper vault discovery validation.
Pump.Fun accounts are NOT tradeable pools - they're old bonding curve references.

After cleanup, real pools (SPL Token / Token2022) remain.
"""

import asyncio
import sqlite3
import os
from typing import Optional, Dict

RPC_URL = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
DB_PATH = "database/flex_complete_database.db"

PUMPFUN_PROGRAM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
SPL_TOKEN_PROGRAM = "TokenkegQfeZyiNwAJsyFbPVwwQQfubZPrNpYcP2j"
TOKEN2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"


async def get_account_info(account: str) -> Optional[Dict]:
    """Get account info from RPC."""
    import aiohttp

    try:
        async with aiohttp.ClientSession() as session:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getAccountInfo",
                "params": [account, {"encoding": "jsonParsed"}]
            }
            async with session.post(RPC_URL, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("result"):
                        return data["result"]["value"]
    except Exception as e:
        pass

    return None


async def check_is_pumpfun(account: str) -> bool:
    """Check if account is owned by Pump.Fun program."""
    info = await get_account_info(account)
    if not info:
        return False

    owner = info.get("owner", "")
    return owner == PUMPFUN_PROGRAM or owner.startswith("pAMMBay")


async def main():
    """Find and remove Pump.Fun pool registrations."""

    conn = sqlite3.connect(DB_PATH, timeout=10)
    cursor = conn.cursor()

    print("\n" + "=" * 100)
    print("CLEANUP: Remove Pump.Fun Bonding Curve Pools")
    print("=" * 100)

    # Find all pools with Pump.Fun-like base accounts
    cursor.execute("""
        SELECT COUNT(*) FROM token_pool_accounts
        WHERE is_active = 1
    """)
    total_pools, = cursor.fetchone()

    print(f"\nScanning {total_pools} active pools...")

    # Get all pools to check
    cursor.execute("""
        SELECT mint, base_account, quote_account, discovery_method
        FROM token_pool_accounts
        WHERE is_active = 1
    """)

    pumpfun_pools = []
    checked = 0

    for mint, base_account, quote_account, method in cursor.fetchall():
        # Check if base account is Pump.Fun
        is_pumpfun = await check_is_pumpfun(base_account)

        if is_pumpfun:
            pumpfun_pools.append((mint, base_account, method))
            print(f"  ❌ {mint[:14]}: {base_account[:30]}... (method: {method})")

        checked += 1
        if checked % 10 == 0:
            print(f"    Checked {checked}/{total_pools}...", flush=True)

    print(f"\n✅ Scan complete: Found {len(pumpfun_pools)} Pump.Fun pools to remove")

    if pumpfun_pools:
        print(f"\nRemoving {len(pumpfun_pools)} pools...")

        for mint, base_account, method in pumpfun_pools:
            cursor.execute("""
                DELETE FROM token_pool_accounts
                WHERE mint = ? AND base_account = ?
            """, (mint, base_account))

            print(f"  ✅ Deleted: {mint[:14]} ({base_account[:20]}...)")

        conn.commit()
        print(f"\n✅ Cleanup complete: Removed {len(pumpfun_pools)} Pump.Fun pools")

        # Show remaining pools per token
        cursor.execute("""
            SELECT mint, COUNT(*) as pool_count
            FROM token_pool_accounts
            WHERE is_active = 1
            GROUP BY mint
            HAVING pool_count > 0
            ORDER BY pool_count DESC
            LIMIT 20
        """)

        print(f"\nRemaining pools (top 20):")
        for mint, count in cursor.fetchall():
            print(f"  {mint[:14]}: {count} pools")

    conn.close()

    print("\n" + "=" * 100)
    print("NEXT STEPS:")
    print("  1. Restart listener to trigger vault discovery for affected tokens")
    print("  2. Real pools (Token2022/SPL) will be re-discovered and registered")
    print("  3. WebSocket will subscribe to valid pools only")
    print("=" * 100 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
