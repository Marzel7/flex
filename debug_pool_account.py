#!/usr/bin/env python3
"""
Debug script to inspect raw account data for detected pools.
Shows what we're actually extracting from the blockchain.
"""

import asyncio
import aiohttp
import json
from base64 import b64decode
from solders.pubkey import Pubkey

RPC_URL = "https://api.helius.so/v0/connections?api-key=7fa1f01d-d47a-4e20-9fe3-abb98cbcc7c6"

# Known pools from database that all have same vaults
POOLS = [
    ("2PRSxvmHCsgvCrkcoJj3Wj9W5cuJyHbkHbokyJabpump", "?"),  # First pool (unknown address)
    ("GSbpqy5i1nud9jstKcdfj5Av6TXanSpBCT7Kmm39pump", "?"),
    ("ceM9X1Wyv3u1J6Jtxvga88GftuKLs2FwvuSGj4bpump", "?"),
]

async def inspect_account(pool_address: str):
    """Fetch and inspect a pool account."""
    print(f"\n{'='*80}")
    print(f"Pool: {pool_address[:20]}...")
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
                    print(f"❌ Account not found or empty")
                    return

                account = result["result"]["value"]
                owner = account.get("owner")
                data = account.get("data")

                print(f"Owner: {owner}")
                print(f"Executable: {account.get('executable')}")
                print(f"Lamports: {account.get('lamports')}")

                if isinstance(data, list) and len(data) > 0:
                    data_b64 = data[0]
                    data_type = data[1] if len(data) > 1 else "unknown"
                elif isinstance(data, str):
                    data_b64 = data
                    data_type = "base64"
                else:
                    print(f"Data format unexpected: {type(data)}")
                    return

                print(f"Data encoding: {data_type}")
                print(f"Data (base64) length: {len(data_b64)}")

                # Decode
                decoded = b64decode(data_b64)
                print(f"Decoded length: {len(decoded)} bytes")

                # Show first 64 bytes (hex dump)
                print(f"\nFirst 64 bytes (hex):")
                hex_str = decoded[:64].hex()
                for i in range(0, len(hex_str), 32):
                    print(f"  {i//2:3d}: {hex_str[i:i+32]}")

                if len(decoded) >= 296:
                    print(f"\nOffsets 232-296 (vault addresses):")
                    print(f"  232-264 (base):  {decoded[232:264].hex()}")
                    print(f"  264-296 (quote): {decoded[264:296].hex()}")

                    try:
                        base_vault = Pubkey(decoded[232:264])
                        quote_vault = Pubkey(decoded[264:296])
                        print(f"  Base vault:  {base_vault}")
                        print(f"  Quote vault: {quote_vault}")
                    except Exception as e:
                        print(f"  ❌ Could not decode as Pubkey: {e}")
                else:
                    print(f"❌ Account too small ({len(decoded)} bytes) to have vault offsets at 232-296")

                # Show ALL unique 32-byte chunks to identify patterns
                print(f"\nLooking for 32-byte Pubkey patterns...")
                known_addresses = {}
                for i in range(0, len(decoded) - 32, 4):  # Every 4 bytes, look for potential Pubkey
                    chunk = decoded[i:i+32]
                    try:
                        pubkey = Pubkey(chunk)
                        addr_str = str(pubkey)
                        if addr_str not in known_addresses:
                            known_addresses[addr_str] = i
                    except:
                        pass

                if known_addresses:
                    print(f"  Found {len(known_addresses)} potential Pubkeys:")
                    for addr, offset in sorted(known_addresses.items(), key=lambda x: x[1])[:5]:
                        print(f"    Offset {offset:3d}: {addr[:20]}...")
                else:
                    print(f"  No valid Pubkeys found in account data")

    except Exception as e:
        print(f"❌ Error: {e}")

async def main():
    print("\n🔍 Pool Account Inspector")
    print("Checking what data we're actually extracting from pool accounts...")

    # We need the actual pool addresses. Let's fetch them from DB
    import sqlite3

    try:
        conn = sqlite3.connect('database/flex_complete_database.db')
        cursor = conn.cursor()
        cursor.execute("""
            SELECT DISTINCT mint, base_account FROM token_pool_accounts
            ORDER BY created_at DESC LIMIT 1
        """)
        rows = cursor.fetchall()
        conn.close()

        if rows:
            print(f"\nFound {len(rows)} tokens in database")
            # For now, we need pool addresses from the transaction detector
            # But we have the vault addresses - let's check if the vault addresses
            # are actually the accounts we're detecting as "pools"

            for mint, vault in rows[:1]:
                print(f"\nToken: {mint}")
                print(f"Detected vault (from DB): {vault}")
                await inspect_account(vault)
        else:
            print("No pools in database")
    except Exception as e:
        print(f"Error reading database: {e}")

if __name__ == '__main__':
    asyncio.run(main())
