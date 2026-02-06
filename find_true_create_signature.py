#!/usr/bin/env python3
"""
Find the TRUE CREATE transaction for a token.

The problem: Code searches for "earliest bonding curve transaction" but
the actual CREATE is the transaction that INITIALIZED/CREATED the bonding curve account.

This script finds the transaction that actually created the bonding curve account
by looking at the account's creation block and transaction.
"""

import asyncio
import aiohttp
import json
from pathlib import Path
import sys

# Add parent dir to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from pump_fun_post_migration_analyzer import PostMigrationAnalyzer


async def find_true_create_signature():
    """
    Find the true CREATE signature by:
    1. Getting the bonding curve account info
    2. Finding when it was created
    3. Fetching signatures around that block/slot
    4. Identifying the CREATE transaction (should be close to bonding curve creation)
    """

    token_mint = "FP9azyGgjP5St7d8cXjupyY7Kfs8kvnQ69ktU45Ypump"
    creator = "GgpEgoQ9kYhsgP9NGgbxXov9y6KaT7dLQdDAs7rAoJ9P"
    bonding_curve = "HKCxoMfUYEkNKLrE1T8nRNVVDQc79Nbu8yLSZJx1pump"  # From earlier, this is a placeholder

    print("\n" + "="*80)
    print(f"FIND TRUE CREATE SIGNATURE")
    print("="*80)
    print(f"Token Mint: {token_mint}")
    print(f"Creator: {creator}")
    print("="*80 + "\n")

    rpc_url = "https://api.mainnet-beta.solana.com"

    try:
        async with aiohttp.ClientSession() as session:
            # Step 1: Get token metadata to find bonding curve
            print(f"[SEARCH] Step 1: Looking up token account info...")
            async with session.post(
                rpc_url,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getAccountInfo",
                    "params": [token_mint, {"encoding": "jsonParsed"}]
                },
                timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                data = await resp.json()

                if data.get("result") is None:
                    print(f"[SEARCH] ⚠ Token account not found")
                    return

                account_info = data["result"]
                if not account_info:
                    print(f"[SEARCH] ⚠ Account info is empty")
                    return

                # The account contains parsed token data
                parsed = account_info.get("data", {}).get("parsed", {})
                owner = account_info.get('owner')
                print(f"[SEARCH] ✓ Token account found")
                if owner:
                    print(f"  Owner: {owner[:20]}...")
                else:
                    print(f"  Owner: (unknown)")
                print(f"  Lamports: {account_info.get('lamports')}")

            # Step 2: Get all signatures for this mint address
            print(f"\n[SEARCH] Step 2: Getting all signatures for token mint...")
            async with session.post(
                rpc_url,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getSignaturesForAddress",
                    "params": [
                        token_mint,
                        {
                            "limit": 100,
                            "commitment": "confirmed"
                        }
                    ]
                },
                timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                data = await resp.json()

                if data.get("result") is None:
                    print(f"[SEARCH] ⚠ No signatures found for mint")
                    return

                sigs = data["result"]
                if not sigs:
                    print(f"[SEARCH] ⚠ Signature list is empty")
                    return

                print(f"[SEARCH] ✓ Found {len(sigs)} signatures for mint")

                # The EARLIEST signature should be the CREATE (or close to it)
                print(f"\n[SEARCH] Earliest 10 signatures (oldest first):")
                for i, sig_info in enumerate(reversed(sigs[-10:])):
                    sig = sig_info.get("signature")
                    slot = sig_info.get("slot")
                    block_time = sig_info.get("blockTime")
                    err = sig_info.get("err")
                    status = "✓ Success" if err is None else f"✗ Failed: {err}"
                    print(f"  [{i}] {sig[:20]}... (slot {slot}) - {status}")

                # Step 3: Fetch and analyze the earliest signature
                earliest_sig = sigs[-1]["signature"]  # Last in list = earliest
                print(f"\n[SEARCH] Step 3: Analyzing earliest signature...")
                print(f"  Signature: {earliest_sig}")

                async with session.post(
                    rpc_url,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "getTransaction",
                        "params": [
                            earliest_sig,
                            {
                                "encoding": "jsonParsed",
                                "maxSupportedTransactionVersion": 0
                            }
                        ]
                    },
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    data = await resp.json()

                    if data.get("result"):
                        tx = data["result"]
                        print(f"  ✓ Transaction found")

                        message = tx.get("transaction", {}).get("message", {})
                        instructions = message.get("instructions", [])

                        print(f"\n[SEARCH] Instructions in earliest transaction:")
                        for i, instr in enumerate(instructions[:5]):
                            prog = instr.get("programId", "unknown")[:20]
                            print(f"    [{i}] Program: {prog}...")

                        # Check if this looks like a CREATE
                        analyzer = PostMigrationAnalyzer(token_mint=token_mint)
                        result = analyzer._validate_pumpfun_create_tx(tx)

                        print(f"\n[SEARCH] Validation result for earliest sig:")
                        print(f"  Mint in accounts: {result['mint_in_accounts']}")
                        print(f"  Pump.Fun program found: {result['pumpfun_program_found']}")
                        print(f"  Is Pump.Fun CREATE: {result['is_pumpfun_create']}")

                    else:
                        print(f"  ⚠ Could not fetch transaction")

                # Step 4: Check a few more early signatures
                print(f"\n[SEARCH] Step 4: Checking 5 earliest signatures...")
                for sig_info in reversed(sigs[-5:]):
                    sig = sig_info["signature"]
                    async with session.post(
                        rpc_url,
                        json={
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "getTransaction",
                            "params": [
                                sig,
                                {
                                    "encoding": "jsonParsed",
                                    "maxSupportedTransactionVersion": 0
                                }
                            ]
                        },
                        timeout=aiohttp.ClientTimeout(total=30)
                    ) as resp:
                        data = await resp.json()

                        if data.get("result"):
                            tx = data["result"]
                            analyzer = PostMigrationAnalyzer(token_mint=token_mint)
                            result = analyzer._validate_pumpfun_create_tx(tx)

                            status = "✅ CREATE" if result['is_pumpfun_create'] else "❌ Not CREATE"
                            print(f"  {sig[:16]}... - {status}")
                        else:
                            print(f"  {sig[:16]}... - Error fetching")

                print("\n" + "="*80)

    except Exception as e:
        print(f"[SEARCH] ❌ Error: {e}")
        import traceback
        traceback.print_exc()


async def main():
    """Run the search."""
    await find_true_create_signature()


if __name__ == "__main__":
    asyncio.run(main())
