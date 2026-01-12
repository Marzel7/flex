#!/usr/bin/env python3
"""
Check actual transaction to see which account is being written to
"""
import requests
import json

test_mint = "BZ68YAqHkALtecENB5oy6B4qbTmf2Q8onCwzEtScpump"
RPC_URL = "https://api.mainnet-beta.solana.com"

# First get a signature for this token
print(f"Getting signatures for {test_mint}...")
payload = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "getSignaturesForAddress",
    "params": [test_mint, {"limit": 1}]
}
res = requests.post(RPC_URL, json=payload, timeout=10).json()
sigs = res.get("result", [])

if sigs:
    sig = sigs[0]["signature"]
    print(f"Found signature: {sig}\n")
    
    # Get the full transaction
    print(f"Fetching transaction...")
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTransaction",
        "params": [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}]
    }
    res = requests.post(RPC_URL, json=payload, timeout=10).json()
    tx = res.get("result", {})
    
    if tx and tx.get("meta"):
        meta = tx["meta"]
        print(f"\nTransaction Accounts:")
        print(f"{'='*70}")
        
        # Show pre/post token balances
        pre_balances = meta.get("preTokenBalances", [])
        post_balances = meta.get("postTokenBalances", [])
        
        print(f"\nPre-Transaction Token Balances:")
        for balance in pre_balances[:5]:
            print(f"  Mint: {balance.get('mint', 'N/A')[:20]}...")
            print(f"  Owner: {balance.get('owner', 'N/A')[:20]}...")
            print(f"  Amount: {balance.get('uiTokenAmount', {}).get('amount')}")
            print()
        
        print(f"\nPost-Transaction Token Balances:")
        for balance in post_balances[:5]:
            print(f"  Mint: {balance.get('mint', 'N/A')[:20]}...")
            print(f"  Owner: {balance.get('owner', 'N/A')[:20]}...")
            print(f"  Amount: {balance.get('uiTokenAmount', {}).get('amount')}")
            print()
    else:
        print("Could not fetch transaction details")
else:
    print("No signatures found")
