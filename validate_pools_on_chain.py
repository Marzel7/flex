#!/usr/bin/env python3
"""
Validate pools on-chain and update their vault data.

For pools registered from DexScreener with status='pending':
1. Query RPC for the pool account
2. Get base and quote vault addresses
3. Validate both vaults exist and contain correct tokens
4. Update database with real on-chain data
5. Mark as validated
"""

import asyncio
import sqlite3
import os
from typing import Optional, Dict, Tuple
import base58

DB_PATH = "database/flex_complete_database.db"
RPC_URL = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")

# Token program IDs
SPL_TOKEN_PROGRAM = "TokenkegQfeZyiNwAJsyFbPVwwQQfubZPrNpYcP2j"
TOKEN2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
WRAPPED_SOL_MINT = "So11111111111111111111111111111111111111112"


async def get_account_info(account: str) -> Optional[Dict]:
    """Get account info from RPC"""
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
        print(f"  ❌ RPC error: {e}")
    return None


async def validate_token_account(account: str, expected_mint: str) -> bool:
    """Check if account is a valid token account for the mint"""
    info = await get_account_info(account)
    if not info:
        return False

    try:
        # Check owner is token program
        owner = info.get("owner")
        if owner not in [SPL_TOKEN_PROGRAM, TOKEN2022_PROGRAM]:
            return False

        # Check parsed data contains expected mint
        parsed = info.get("data", {}).get("parsed", {})
        if parsed.get("type") == "account":
            mint = parsed.get("info", {}).get("mint")
            if mint == expected_mint:
                return True
    except:
        pass

    return False


async def main():
    """Validate pending pools on-chain"""

    print("=" * 80)
    print("VALIDATE POOLS ON-CHAIN")
    print("=" * 80)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Get pending pools (from DexScreener, not validated)
    cursor.execute("""
        SELECT mint, base_account, quote_account
        FROM token_pool_accounts
        WHERE vault_validation_status = 'pending'
        AND discovery_method = 'dexscreener_authoritative'
        AND is_active = 1
        LIMIT 20
    """)

    pending_pools = cursor.fetchall()

    if not pending_pools:
        print("✅ No pending pools to validate")
        conn.close()
        return

    print(f"\n🔍 Found {len(pending_pools)} pools to validate\n")

    validated = 0
    failed = 0

    for mint, base_account, quote_account in pending_pools:
        print(f"{mint[:16]}...", end=" ", flush=True)

        # Validate base account
        base_valid = await validate_token_account(base_account, mint)
        if not base_valid:
            print("❌ Base account invalid")
            failed += 1
            continue

        # Validate quote account
        quote_valid = await validate_token_account(quote_account, WRAPPED_SOL_MINT)
        if not quote_valid:
            print("❌ Quote account invalid")
            failed += 1
            continue

        # Update database
        try:
            cursor.execute("""
                UPDATE token_pool_accounts
                SET vault_validation_status = 'validated',
                    discovery_method = 'dexscreener_validated_on_chain',
                    last_vault_validation_at = ?
                WHERE mint = ? AND base_account = ?
            """, (int(__import__('time').time()), mint, base_account))
            conn.commit()
            print("✅ Validated")
            validated += 1
        except Exception as e:
            print(f"❌ DB update failed: {e}")
            failed += 1

    conn.close()

    print("\n" + "=" * 80)
    print(f"RESULTS: {validated} validated, {failed} failed")
    print("=" * 80)

    if validated > 0:
        print("\n📋 Next steps:")
        print("1. Restart price_worker to reload pools")
        print("2. WebSocket will subscribe to validated pools")
        print("3. Prices should start flowing")


if __name__ == "__main__":
    asyncio.run(main())
