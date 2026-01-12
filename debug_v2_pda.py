#!/usr/bin/env python3
"""
Debug V2 bonding curve PDA querying
"""
import requests
from pump_fun_pre_migration_analyzer_v2 import derive_bonding_curve_pda

# Test tokens from logs
test_mints = [
    "BZ68YAqHkALtecENB5oy6B4qbTmf2Q8onCwzEtScpump",
    "DivfgD4Wq9B1KJpJY2WoKiLRpNohxMX6gqJL8JMrpump",
]

RPC_URL = "https://api.mainnet-beta.solana.com"

for mint in test_mints:
    print(f"\n{'='*60}")
    print(f"Testing: {mint[:20]}...")
    print(f"{'='*60}")
    
    # Derive PDA
    pda = derive_bonding_curve_pda(mint)
    print(f"Bonding Curve PDA: {pda}")
    
    # Query by PDA
    print(f"\n[Query 1] Signatures for bonding curve PDA:")
    payload_pda = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getSignaturesForAddress",
        "params": [pda, {"limit": 10}]
    }
    try:
        res = requests.post(RPC_URL, json=payload_pda, timeout=10).json()
        sigs_pda = res.get("result", [])
        print(f"  Found {len(sigs_pda)} signatures")
        if sigs_pda:
            print(f"  First sig: {sigs_pda[0]['signature'][:20]}...")
    except Exception as e:
        print(f"  Error: {e}")
    
    # Query by mint (V1 approach)
    print(f"\n[Query 2] Signatures for token mint:")
    payload_mint = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getSignaturesForAddress",
        "params": [mint, {"limit": 10}]
    }
    try:
        res = requests.post(RPC_URL, json=payload_mint, timeout=10).json()
        sigs_mint = res.get("result", [])
        print(f"  Found {len(sigs_mint)} signatures")
        if sigs_mint:
            print(f"  First sig: {sigs_mint[0]['signature'][:20]}...")
    except Exception as e:
        print(f"  Error: {e}")

print(f"\n{'='*60}\n")
