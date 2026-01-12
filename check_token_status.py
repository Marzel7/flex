#!/usr/bin/env python3
"""
Check if token has migrated or is still on bonding curve
"""
import requests

test_mints = [
    "BZ68YAqHkALtecENB5oy6B4qbTmf2Q8onCwzEtScpump",
    "DivfgD4Wq9B1KJpJY2WoKiLRpNohxMX6gqJL8JMrpump",
]

RPC_URL = "https://api.mainnet-beta.solana.com"

for mint in test_mints:
    print(f"\n{'='*70}")
    print(f"Token: {mint}")
    print(f"{'='*70}")
    
    # Try mint query
    print(f"\nTesting direct mint query...")
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getSignaturesForAddress",
        "params": [mint, {"limit": 5}]
    }
    res = requests.post(RPC_URL, json=payload, timeout=10).json()
    sigs = res.get("result", [])
    print(f"Signatures: {len(sigs)}")
    if sigs:
        for sig in sigs[:2]:
            print(f"  - {sig['signature'][:30]}... ({sig.get('blockTime', 'N/A')})")
    
    # Check account info
    print(f"\nChecking account type...")
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getAccountInfo",
        "params": [mint, {"encoding": "jsonParsed"}]
    }
    res = requests.post(RPC_URL, json=payload, timeout=10).json()
    acc = res.get("result", {})
    if acc:
        print(f"  Owner: {acc.get('owner', 'N/A')}")
        print(f"  Lamports: {acc.get('lamports', 'N/A')}")
        if acc.get("data", {}).get("parsed"):
            parsed = acc["data"]["parsed"]
            print(f"  Type: {parsed.get('type', 'N/A')}")
    else:
        print(f"  Account not found or is empty")

print(f"\n{'='*70}\n")
