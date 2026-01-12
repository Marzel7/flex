#!/usr/bin/env python3
"""
Debug why transactions aren't parsing
"""
import requests

mint = "82P9MvicWYr2R1yeYZLJrbPZB236uMeMBKJ6bLgpBAGS"
RPC_URL = "https://api.mainnet-beta.solana.com"

# Get a signature
print(f"Getting signatures for {mint}...")
payload = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "getSignaturesForAddress",
    "params": [mint, {"limit": 5}]
}
res = requests.post(RPC_URL, json=payload, timeout=10).json()
sigs = res.get("result", [])
print(f"Found {len(sigs)} signatures\n")

if sigs:
    # Try batch fetch
    sig_list = [s["signature"] for s in sigs[:2]]
    print(f"Trying batch fetch of {len(sig_list)} signatures...")
    
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getMultipleTransactions",
        "params": [sig_list, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}]
    }
    
    res = requests.post(RPC_URL, json=payload, timeout=20).json()
    txs = res.get("result", [])
    print(f"Batch fetch returned {len(txs)} transactions\n")
    
    for i, tx in enumerate(txs):
        if tx is None:
            print(f"TX {i}: NULL")
        elif not tx.get("meta"):
            print(f"TX {i}: No meta")
        else:
            meta = tx.get("meta", {})
            print(f"TX {i}: Has meta")
            print(f"  - preTokenBalances: {len(meta.get('preTokenBalances', []))}")
            print(f"  - postTokenBalances: {len(meta.get('postTokenBalances', []))}")
            
            # Check if our mint is in there
            post_balances = meta.get('postTokenBalances', [])
            for bal in post_balances:
                if bal.get('mint') == mint:
                    print(f"  ✅ Found our mint in postTokenBalances")
                    break
