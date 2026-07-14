#!/usr/bin/env python3
"""
Test batch fetching of transactions to diagnose rate limiting issues.
"""

import asyncio
import aiohttp
import os
from dotenv import load_dotenv

load_dotenv()

RPC_URL = os.getenv("RPC_URL", "https://api.mainnet-beta.solana.com")

# A real token to test with
TEST_MINT = "EPjFWdd5Au17FXPuVr6BXq3aV2HVj1FQzyZuaFPTZB8J"  # USDC - has lots of transactions

async def fetch_signatures(token_mint: str, limit: int = 100) -> list:
    """Fetch transaction signatures for a token"""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getSignaturesForAddress",
        "params": [token_mint, {"limit": limit}]
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(RPC_URL, json=payload, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            data = await resp.json()
            sigs = [s["signature"] for s in data.get("result", [])]
            return sigs

async def fetch_tx(session: aiohttp.ClientSession, sig: str) -> dict:
    """Fetch a single transaction"""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTransaction",
        "params": [sig, {"encoding": "json", "maxSupportedTransactionVersion": 0}]
    }

    try:
        async with session.post(RPC_URL, json=payload, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            if resp.status != 200:
                return {"error": f"HTTP {resp.status}"}

            data = await resp.json()
            if "error" in data:
                return data
            return data.get("result", {})
    except Exception as e:
        return {"error": str(e)}

async def test_batch_fetch(batch_size: int, num_transactions: int = 50):
    """Test fetching transactions in batches"""
    print(f"\n{'='*70}")
    print(f"TEST: Batch size = {batch_size}, Transactions = {num_transactions}")
    print(f"{'='*70}")

    print(f"\n📝 Fetching signatures...")
    sigs = await fetch_signatures(TEST_MINT, limit=num_transactions)
    print(f"✓ Got {len(sigs)} signatures")

    print(f"\n🔗 RPC Endpoint: {RPC_URL[:80]}...")
    print(f"\n🔄 Fetching transactions in batches of {batch_size}...")

    connector = aiohttp.TCPConnector(
        limit=batch_size,
        limit_per_host=batch_size,
        ttl_dns_cache=None
    )

    successful = 0
    failed = 0
    rate_limited = 0

    async with aiohttp.ClientSession(connector=connector) as session:
        for i in range(0, len(sigs), batch_size):
            batch = sigs[i:i+batch_size]
            print(f"\n  Batch {i//batch_size + 1}: Fetching {len(batch)} txs...", end="", flush=True)

            tasks = [fetch_tx(session, sig) for sig in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            batch_success = 0
            batch_failed = 0
            batch_rate_limited = 0

            for result in results:
                if isinstance(result, Exception):
                    batch_failed += 1
                    failed += 1
                elif "error" in result:
                    if "429" in str(result["error"]):
                        batch_rate_limited += 1
                        rate_limited += 1
                    else:
                        batch_failed += 1
                        failed += 1
                else:
                    batch_success += 1
                    successful += 1

            print(f" ✓ {batch_success} success, ✗ {batch_failed} failed, ⚠ {batch_rate_limited} rate limited")

            # Delay between batches
            if i + batch_size < len(sigs):
                await asyncio.sleep(0.2)

    print(f"\n{'='*70}")
    print(f"RESULTS:")
    print(f"  Successful: {successful}")
    print(f"  Failed: {failed}")
    print(f"  Rate Limited (429): {rate_limited}")
    print(f"  Success Rate: {successful/(successful+failed+rate_limited)*100:.1f}%")
    print(f"{'='*70}\n")

async def main():
    print("\n🧪 BATCH FETCH TEST")
    print("Testing different batch sizes to find optimal configuration")

    # Test with batch size 5
    await test_batch_fetch(batch_size=5, num_transactions=50)

    # Test with batch size 10
    await test_batch_fetch(batch_size=10, num_transactions=50)

if __name__ == "__main__":
    asyncio.run(main())
