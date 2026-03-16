#!/usr/bin/env python3
"""
Find pool creation transactions that occur AFTER the migration signature.
Uses authenticated Helius RPC to query signatures for AMM programs.
"""

import asyncio
import os
import sys
import json
from typing import List, Dict, Optional
from dotenv import load_dotenv
import aiohttp
import time

load_dotenv()

HELIUS_RPC = os.getenv("HELIUS_RPC_URL", "https://mainnet.helius-rpc.com/?api-key=16f1a5fc-2592-466c-a5d4-b5799ae8da96")

# Known AMM programs
RAYDIUM_AMM = "675kPX9MHTjS2zt1qrXrQVxwwp4W8gNzjX9oVhKt7Ck"
PUMPSWAP = "PumpFun6WS79LYJSDhiBfk9YHgELDHSH4EvBiRVnqW"
SPL_TOKEN = "TokenkegQfeZyiNwAJsyFbPVwwQQftas5LLppuCQqn"


async def get_signature_info(sig: str, session: aiohttp.ClientSession) -> Optional[Dict]:
    """Get block time for a signature."""
    try:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTransaction",
            "params": [sig, {"encoding": "json", "maxSupportedTransactionVersion": 0}]
        }

        async with session.post(HELIUS_RPC, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            data = await resp.json()
            if data.get('result'):
                tx = data['result']
                block_time = tx.get('blockTime')
                slot = tx.get('slot')
                return {'blockTime': block_time, 'slot': slot}
    except Exception as e:
        print(f"  Error fetching {sig[:20]}...: {e}")
    return None


async def get_program_signatures(program: str, before_sig: Optional[str] = None, limit: int = 20,
                                  session: aiohttp.ClientSession = None) -> List[Dict]:
    """Get recent signatures for a program."""
    try:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getSignaturesForAddress",
            "params": [
                program,
                {
                    "limit": limit,
                    **({"before": before_sig} if before_sig else {})
                }
            ]
        }

        async with session.post(HELIUS_RPC, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            data = await resp.json()
            if data.get('result'):
                return data['result']
    except Exception as e:
        print(f"  Error getting signatures for {program[:20]}...: {e}")
    return []


async def find_pools_after_migration(mint: str, migration_sig: str):
    """Find pool creation transactions after migration."""
    print("=" * 80)
    print("FINDING POOLS AFTER MIGRATION")
    print("=" * 80)
    print(f"Mint: {mint}")
    print(f"Migration sig: {migration_sig[:50]}...")
    print(f"RPC: {HELIUS_RPC[:60]}...")
    print()

    async with aiohttp.ClientSession() as session:
        # Get migration block time
        print(f"[1] Getting migration block time...")
        mig_info = await get_signature_info(migration_sig, session)
        if not mig_info:
            print(f"❌ Could not fetch migration transaction")
            return

        mig_block_time = mig_info.get('blockTime', 0)
        mig_slot = mig_info.get('slot', 0)
        print(f"  Migration: slot={mig_slot}, blockTime={mig_block_time}")
        print()

        # Search Raydium for pools created after migration
        print(f"[2] Searching Raydium program for recent transactions...")
        raydium_sigs = await get_program_signatures(RAYDIUM_AMM, limit=50, session=session)
        print(f"  Found {len(raydium_sigs)} recent Raydium transactions")

        pools_found = []
        for i, sig_info in enumerate(raydium_sigs):
            sig = sig_info['signature']
            if i % 5 == 0:
                print(f"  Checking {i+1}/{len(raydium_sigs)}...", end='\r')

            info = await get_signature_info(sig, session)
            if info:
                tx_block_time = info.get('blockTime', 0)
                # Check if this tx is after migration
                if tx_block_time and mig_block_time and tx_block_time > mig_block_time:
                    pools_found.append({
                        'program': 'Raydium',
                        'signature': sig,
                        'blockTime': tx_block_time,
                        'slot': info.get('slot'),
                        'time_after_migration': tx_block_time - mig_block_time
                    })
            await asyncio.sleep(0.1)  # Rate limit

        print(f"  Found {len(pools_found)} potential Raydium pools after migration")
        print()

        # Search PumpSwap for pools created after migration
        print(f"[3] Searching PumpSwap program for recent transactions...")
        pumpswap_sigs = await get_program_signatures(PUMPSWAP, limit=50, session=session)
        print(f"  Found {len(pumpswap_sigs)} recent PumpSwap transactions")

        for i, sig_info in enumerate(pumpswap_sigs):
            sig = sig_info['signature']
            if i % 5 == 0:
                print(f"  Checking {i+1}/{len(pumpswap_sigs)}...", end='\r')

            info = await get_signature_info(sig, session)
            if info:
                tx_block_time = info.get('blockTime', 0)
                if tx_block_time and mig_block_time and tx_block_time > mig_block_time:
                    pools_found.append({
                        'program': 'PumpSwap',
                        'signature': sig,
                        'blockTime': tx_block_time,
                        'slot': info.get('slot'),
                        'time_after_migration': tx_block_time - mig_block_time
                    })
            await asyncio.sleep(0.1)

        print(f"  Found {len([p for p in pools_found if p['program']=='PumpSwap'])} potential PumpSwap pools after migration")
        print()

        # Display results
        print("=" * 80)
        print("RESULTS")
        print("=" * 80)
        if pools_found:
            print(f"✅ Found {len(pools_found)} potential pool transactions after migration:")
            print()
            # Sort by time after migration
            pools_found.sort(key=lambda x: x['time_after_migration'])
            for i, pool in enumerate(pools_found[:10], 1):  # Show first 10
                print(f"{i}. Program: {pool['program']}")
                print(f"   Signature: {pool['signature'][:60]}...")
                print(f"   Slot: {pool['slot']}")
                print(f"   Time after migration: {pool['time_after_migration']}s")
                print()
        else:
            print(f"❌ No pool transactions found after migration in recent activity")
            print(f"   This could mean:")
            print(f"   - Pool not created yet")
            print(f"   - Pool created in a different program")
            print(f"   - Pool creation signatures too old (>1000 blocks)")


async def main():
    """Main entry point."""
    # Use the tokens from logs
    tokens = [
        {
            'mint': '83Gc9q7KP9yVQCAN6j1Y3gE8v8fJjhNFPSc3eLT4pump',
            'migration_sig': 'onVMZqm4KpSqNZ25zoZYsHsNgs2sWg7vfMcA3rmXG9QnHS3wL23rcjHXCqb7QbziNdN4ByaT7ogrt7Z6RHWLP3t'
        },
        {
            'mint': 'EeBWrYayvfCSuGYVgRZk8m4frPpicxXP8t77Nax9pump',
            'migration_sig': '2ab1NsXwnbRsPyj8wYdH7hYhNz3bZabt6m9oRy1kToshMiNrAPv1C21V2HEd3cREDercAKJryfJuYr3gGmNsvLQT'
        }
    ]

    for token in tokens:
        await find_pools_after_migration(token['mint'], token['migration_sig'])
        print("\n")


if __name__ == "__main__":
    asyncio.run(main())
