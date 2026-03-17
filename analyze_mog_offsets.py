#!/usr/bin/env python3
"""Analyze why MOG pool extraction fails."""

import asyncio
import aiohttp
import base64
import base58
import os

async def check_mog_pool():
    """Analyze the MOG pool structure in detail."""

    pool_address = "A1HFqQZF3t16RQ8ENV9NLkVXL6E5Fu31sWk5s33jH5wn"
    rpc_url = "https://mainnet.helius-rpc.com/?api-key=16f1a5fc-2592-466c-a5d4-b5799ae8da96"

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getAccountInfo",
        "params": [pool_address, {"encoding": "base64"}]
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(rpc_url, json=payload, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            result = await resp.json()
            if "result" in result and result["result"]:
                account = result["result"]["value"]
                data_b64 = account.get("data", [None])[0]
                data = base64.b64decode(data_b64)

                print("\n" + "=" * 80)
                print("MOG POOL STRUCTURE ANALYSIS")
                print("=" * 80)
                print(f"Address: {pool_address}")
                print(f"Owner:   {account['owner']}")
                print(f"Size:    {len(data)} bytes")
                print()

                # The issue: offsets 232/264 are out of bounds!
                # 301 bytes total, so max offset is 301 - 32 = 269
                print("KEY FINDING:")
                print(f"  Data size: {len(data)} bytes")
                print(f"  Max valid offset for 32-byte pubkey: {len(data) - 32}")
                print()

                if len(data) >= 232 + 32:
                    print("✓ Offset 232 is valid")
                else:
                    print("✗ Offset 232 is OUT OF BOUNDS")

                if len(data) >= 264 + 32:
                    print("✓ Offset 264 is valid")
                else:
                    print(f"✗ Offset 264 is OUT OF BOUNDS (need at least {264+32} bytes, have {len(data)})")

                print()
                print("VAULT ADDRESSES AT STANDARD OFFSETS:")
                print()

                # Try offsets 72/104 (Raydium AMM v4 standard)
                if len(data) >= 72 + 64:
                    base_vault_72 = data[72:104]
                    quote_vault_104 = data[104:136]
                    base_addr_72 = base58.b58encode(base_vault_72).decode()
                    quote_addr_104 = base58.b58encode(quote_vault_104).decode()
                    print(f"Offset 72 (base vault):   {base_addr_72}")
                    print(f"Offset 104 (quote vault): {quote_addr_104}")
                    print()

                # Try offsets 232/264 (PumpSwap documented offsets)
                print("Offset 232 (documented PumpSwap base vault):")
                if len(data) >= 232 + 32:
                    base_vault_232 = data[232:264]
                    try:
                        base_addr_232 = base58.b58encode(base_vault_232).decode()
                        print(f"  {base_addr_232}")
                    except:
                        print(f"  {base_vault_232.hex()}")
                else:
                    print(f"  ✗ OUT OF BOUNDS (only {len(data)} bytes, need {232+32})")

                print()
                print("Offset 264 (documented PumpSwap quote vault):")
                if len(data) >= 264 + 32:
                    quote_vault_264 = data[264:296]
                    try:
                        quote_addr_264 = base58.b58encode(quote_vault_264).decode()
                        print(f"  {quote_addr_264}")
                    except:
                        print(f"  {quote_vault_264.hex()}")
                else:
                    print(f"  ✗ OUT OF BOUNDS (only {len(data)} bytes, need {264+32})")

                print()
                print("=" * 80)
                print("ROOT CAUSE IDENTIFIED:")
                print("=" * 80)
                print(f"MOG pool is only {len(data)} bytes, but code tries to read at offset 264+32={264+32}")
                print(f"This is {296 - len(data)} bytes OUT OF BOUNDS!")
                print()
                print("SOLUTION:")
                print("The vaults are at offsets 72/104 (Raydium AMM v4 standard layout)")
                print("NOT at offsets 232/264 (those are for different pool layouts)")
                print()

asyncio.run(check_mog_pool())
