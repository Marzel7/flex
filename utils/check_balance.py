#!/usr/bin/env python3
"""Quick balance checker for your trading wallet"""

import os
import requests
import json
from solders.keypair import Keypair

# Get Helius API key
helius_key = os.environ.get("HELIUS_API_KEY")
if not helius_key:
    print("❌ HELIUS_API_KEY not set")
    exit(1)

# Get keypair from env
trading_keypair_env = os.environ.get("TRADING_KEYPAIR")
if not trading_keypair_env:
    print("❌ TRADING_KEYPAIR not set")
    exit(1)

try:
    # Load keypair
    if trading_keypair_env.startswith("["):
        keypair_array = json.loads(trading_keypair_env)
        keypair_bytes = bytes(keypair_array)
    else:
        keypair_bytes = bytes.fromhex(trading_keypair_env)

    keypair = Keypair.from_bytes(keypair_bytes)
    wallet_address = str(keypair.pubkey())

    print(f"\n{'='*70}")
    print("Balance Checker")
    print(f"{'='*70}")
    print(f"Wallet: {wallet_address}\n")

    # Get balance
    rpc_endpoint = f"https://mainnet.helius-rpc.com/?api-key={helius_key}"
    response = requests.post(
        rpc_endpoint,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getBalance",
            "params": [wallet_address]
        },
        timeout=5
    )

    result = response.json()
    if "result" in result:
        lamports = result["result"]["value"]
        sol = lamports / 10**9

        print(f"💰 Balance: {sol:.6f} SOL")
        print(f"   ({lamports} lamports)")

        if sol >= 0.01:
            print(f"\n✅ Ready to trade! (Need 0.01 SOL, have {sol:.6f} SOL)")
        else:
            print(f"\n⚠️  Need more SOL (Need 0.01 SOL, have {sol:.6f} SOL)")
    else:
        print(f"❌ Error: {result}")

except Exception as e:
    print(f"❌ Error: {e}")
