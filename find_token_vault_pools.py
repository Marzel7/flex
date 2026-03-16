#!/usr/bin/env python3
"""
Find pools by querying for token vault accounts.
Uses getTokenLargestAccounts to find where the token is held in largest quantities.
"""

import asyncio
import os
import json
from typing import List, Dict, Optional
from dotenv import load_dotenv
import aiohttp

load_dotenv()

HELIUS_RPC = os.getenv("HELIUS_RPC_URL", "https://mainnet.helius-rpc.com/?api-key=16f1a5fc-2592-466c-a5d4-b5799ae8da96")

# Known AMM programs
RAYDIUM_AMM = "675kPX9MHTjS2zt1qrXrQVxwwp4W8gNzjX9oVhKt7Ck"
PUMPSWAP = "PumpFun6WS79LYJSDhiBfk9YHgELDHSH4EvBiRVnqW"
SPL_TOKEN = "TokenkegQfeZyiNwAJsyFbPVwwQQftas5LLppuCQqn"


async def get_token_largest_accounts(mint: str, session: aiohttp.ClientSession) -> List[Dict]:
    """Get largest token accounts for a mint."""
    try:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTokenLargestAccounts",
            "params": [mint]
        }

        async with session.post(HELIUS_RPC, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            data = await resp.json()
            if data.get('result'):
                return data['result'].get('value', [])
    except Exception as e:
        print(f"  Error: {e}")
    return []


async def get_account_info(address: str, session: aiohttp.ClientSession) -> Optional[Dict]:
    """Get account info including owner."""
    try:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getAccountInfo",
            "params": [address, {"encoding": "json"}]
        }

        async with session.post(HELIUS_RPC, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            data = await resp.json()
            if data.get('result'):
                account = data['result']
                return {
                    'address': address,
                    'owner': account.get('owner'),
                    'lamports': account.get('lamports'),
                    'data_len': len(account.get('data', '')),
                }
    except Exception as e:
        print(f"  Error fetching {address[:20]}...: {e}")
    return None


async def find_pools_via_vault_accounts(mint: str):
    """Find pools by analyzing token vault accounts."""
    print("=" * 80)
    print(f"FINDING POOLS VIA TOKEN VAULT ACCOUNTS")
    print("=" * 80)
    print(f"Mint: {mint}")
    print(f"RPC: {HELIUS_RPC[:60]}...")
    print()

    async with aiohttp.ClientSession() as session:
        # Get largest accounts
        print(f"[1] Getting largest token accounts...")
        accounts = await get_token_largest_accounts(mint, session)
        print(f"  Found {len(accounts)} token vault accounts")
        print()

        if not accounts:
            print(f"❌ No token vaults found")
            return

        # Check owners
        print(f"[2] Checking account owners...")
        amm_owned_vaults = []

        for i, account in enumerate(accounts[:20]):  # Check top 20
            address = account['address']
            print(f"  Checking {i+1}/min({len(accounts)}, 20)...", end='\r')

            info = await get_account_info(address, session)
            if info:
                owner = info['owner']
                # Check if owned by known AMM programs
                if owner == RAYDIUM_AMM:
                    amm_owned_vaults.append({
                        'address': address,
                        'owner': 'Raydium AMM',
                        'balance': account.get('amount', '0'),
                        'decimals': account.get('decimals', 0)
                    })
                elif owner == PUMPSWAP:
                    amm_owned_vaults.append({
                        'address': address,
                        'owner': 'PumpSwap',
                        'balance': account.get('amount', '0'),
                        'decimals': account.get('decimals', 0)
                    })
            await asyncio.sleep(0.05)

        print()
        print()
        print("=" * 80)
        print("RESULTS")
        print("=" * 80)

        if amm_owned_vaults:
            print(f"✅ Found {len(amm_owned_vaults)} vault accounts owned by AMM programs:")
            print()
            for vault in amm_owned_vaults:
                print(f"Address: {vault['address']}")
                print(f"Owner: {vault['owner']}")
                print(f"Balance: {vault['balance']}")
                print(f"Decimals: {vault['decimals']}")
                print()
        else:
            print(f"❌ No token vaults owned by Raydium or PumpSwap")
            print()
            print(f"Top token vault accounts:")
            for i, account in enumerate(accounts[:5], 1):
                print(f"  {i}. {account['address'][:30]}... (amount={account.get('amount', 'unknown')})")


async def main():
    """Main entry point."""
    tokens = [
        '83Gc9q7KP9yVQCAN6j1Y3gE8v8fJjhNFPSc3eLT4pump',
        'EeBWrYayvfCSuGYVgRZk8m4frPpicxXP8t77Nax9pump'
    ]

    for mint in tokens:
        await find_pools_via_vault_accounts(mint)
        print("\n")


if __name__ == "__main__":
    asyncio.run(main())
