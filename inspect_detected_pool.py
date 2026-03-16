#!/usr/bin/env python3
"""
Inspect a real detected pool address to understand what we're actually extracting.
"""

import asyncio
import aiohttp
from base64 import b64decode
from solders.pubkey import Pubkey
import sqlite3

RPC_URL = "https://api.helius.so/v0/connections?api-key=7fa1f01d-d47a-4e20-9fe3-abb98cbcc7c6"

async def inspect_pool(pool_address: str):
    """Fetch and inspect a detected pool account."""
    print(f"\n{'='*80}")
    print(f"Pool Address: {pool_address}")
    print(f"{'='*80}")

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getAccountInfo",
        "params": [pool_address, {"encoding": "base64", "commitment": "finalized"}]
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(RPC_URL, json=payload, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                result = await resp.json()

                if "result" not in result or not result["result"]:
                    print(f"❌ Account not found")
                    return

                account = result["result"]["value"]
                owner = account.get("owner")
                executable = account.get("executable")
                lamports = account.get("lamports")

                print(f"Owner:       {owner}")
                print(f"Executable:  {executable}")
                print(f"Lamports:    {lamports}")

                data = account.get("data", [])
                if isinstance(data, list) and len(data) > 0:
                    data_b64 = data[0]
                    data_encoding = data[1] if len(data) > 1 else "unknown"
                elif isinstance(data, str):
                    data_b64 = data
                    data_encoding = "base64"
                else:
                    print(f"Unexpected data format: {type(data)}")
                    return

                decoded = b64decode(data_b64)
                print(f"\nData:")
                print(f"  Encoding:  {data_encoding}")
                print(f"  Size:      {len(decoded)} bytes")

                if len(decoded) >= 296:
                    # Show offsets 232-296
                    print(f"\n  Offsets 232-296 (supposed to be vault addresses):")
                    print(f"    232-264 (base):  {decoded[232:264].hex()}")
                    print(f"    264-296 (quote): {decoded[264:296].hex()}")

                    try:
                        base = Pubkey(decoded[232:264])
                        quote = Pubkey(decoded[264:296])
                        print(f"\n  Decoded as Pubkeys:")
                        print(f"    Base:  {base}")
                        print(f"    Quote: {quote}")
                    except Exception as e:
                        print(f"    Error decoding: {e}")
                else:
                    print(f"\n❌ Account too small ({len(decoded)} bytes) for offsets 232-296")

                # Try to identify what kind of account this is
                print(f"\n🔍 Account type analysis:")

                # PumpSwap/Raydium pool state accounts should be owned by the AMM program
                PUMPSWAP_PROGRAM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
                RAYDIUM_AMM = "675kPX9MHTjS2zt1qrVrrVrZg1ankqqgoerEmJlwQ1K"

                if owner == PUMPSWAP_PROGRAM:
                    print(f"  ✓ Owned by PumpSwap program (correct)")
                elif owner == RAYDIUM_AMM:
                    print(f"  ✓ Owned by Raydium AMM program (correct)")
                else:
                    print(f"  ✗ Owned by different program (unexpected)")

                # Check if account has expected structure
                if len(decoded) >= 296:
                    print(f"  ✓ Large enough for Raydium AMM v4 pool state")
                else:
                    print(f"  ✗ Too small for Raydium AMM v4 pool state")

    except Exception as e:
        print(f"❌ Error: {e}")

async def main():
    # Get a detected pool from database
    try:
        conn = sqlite3.connect('database/flex_complete_database.db')
        cursor = conn.cursor()
        cursor.execute("""
            SELECT DISTINCT pool_address FROM token_analysis
            WHERE pool_address IS NOT NULL
            LIMIT 3
        """)
        pools = cursor.fetchall()
        conn.close()

        if pools:
            print(f"Found {len(pools)} detected pools in database")
            for (pool_address,) in pools:
                await inspect_pool(pool_address)
        else:
            print("No detected pools in database")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == '__main__':
    asyncio.run(main())
